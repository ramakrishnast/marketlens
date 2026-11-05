"""Prices from yfinance (unofficial Yahoo Finance scraper).

It is rate-limited and can break without notice, so: batch tickers into one request,
retry with exponential backoff, and let the caller cache everything.
"""
from __future__ import annotations

import logging
import time
from typing import Any

import pandas as pd

from .base import ProviderError

log = logging.getLogger(__name__)

BATCH_SIZE = 40


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    df = df.rename(columns=str.lower)
    keep = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
    df = df[keep].dropna(subset=["close"])
    idx = pd.to_datetime(df.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    df.index = idx
    return df.astype(float)


def _retry(fn, attempts: int = 4, base_delay: float = 2.0):
    last: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:  # yfinance raises many exception types
            last = e
            delay = base_delay * (2**i)
            log.warning("yfinance call failed (%s), retrying in %.0fs", e, delay)
            time.sleep(delay)
    raise ProviderError(f"yfinance failed after {attempts} attempts: {last}")


class YahooPriceProvider:
    name = "yfinance"

    def daily(self, tickers: list[str], start: str | None = None) -> dict[str, pd.DataFrame]:
        import yfinance as yf

        out: dict[str, pd.DataFrame] = {}
        tickers = sorted({t.upper() for t in tickers})
        for i in range(0, len(tickers), BATCH_SIZE):
            batch = tickers[i : i + BATCH_SIZE]
            kwargs: dict[str, Any] = dict(
                tickers=batch, interval="1d", auto_adjust=True, progress=False,
                group_by="ticker", threads=True,
            )
            if start:
                kwargs["start"] = start
            else:
                kwargs["period"] = "10y"

            def call():
                df = yf.download(**kwargs)
                if df is None or df.empty:
                    raise ProviderError("empty response (rate-limited?)")
                return df

            df = _retry(call)
            for t in batch:
                try:
                    sub = df[t] if isinstance(df.columns, pd.MultiIndex) else df
                except KeyError:
                    continue
                sub = _normalize(sub)
                if not sub.empty:
                    out[t] = sub
            if i + BATCH_SIZE < len(tickers):
                time.sleep(1.0)  # be polite between batches
        return out

    def intraday(self, ticker: str, interval: str = "5m", days: int = 5) -> pd.DataFrame:
        import yfinance as yf

        def call():
            df = yf.Ticker(ticker).history(period=f"{days}d", interval=interval, auto_adjust=False)
            if df is None or df.empty:
                raise ProviderError("empty intraday response")
            return df

        df = _retry(call, attempts=2)
        idx = pd.to_datetime(df.index)
        if idx.tz is not None:
            idx = idx.tz_convert("America/New_York").tz_localize(None)
        df.index = idx
        return _normalize(df)

    def info(self, ticker: str) -> dict[str, Any]:
        import yfinance as yf

        t = yf.Ticker(ticker)
        out: dict[str, Any] = {}
        try:
            info = t.info or {}
            for k_src, k_dst in [
                ("shortName", "name"), ("longName", "long_name"), ("forwardPE", "forward_pe"),
                ("trailingPE", "trailing_pe"), ("sector", "sector"), ("industry", "industry"),
                ("marketCap", "market_cap"), ("quoteType", "quote_type"), ("forwardEps", "forward_eps"),
                ("dividendYield", "dividend_yield"), ("beta", "beta"),
            ]:
                if info.get(k_src) is not None:
                    out[k_dst] = info[k_src]
        except Exception as e:
            log.info("yfinance info failed for %s: %s", ticker, e)
        try:
            cal = t.calendar or {}
            dates = cal.get("Earnings Date") if isinstance(cal, dict) else None
            if dates:
                out["next_earnings"] = str(dates[0])
        except Exception as e:
            log.info("yfinance calendar failed for %s: %s", ticker, e)
        if not out:
            raise ProviderError(f"no info for {ticker}")
        return out
