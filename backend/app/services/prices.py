"""Daily / intraday price cache on top of the price provider.

Reads always come from SQLite. A refresh is triggered when the cached data is older than
PRICE_TTL_MINUTES; refreshes are incremental (only recent bars are re-downloaded) and batched.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
import pandas as pd

from .. import db
from ..config import get_settings
from ..providers import registry
from ..providers.base import ProviderError

log = logging.getLogger(__name__)
_refresh_lock = threading.Lock()


def _key(t: str) -> str:
    return f"daily:{t.upper()}"


def _is_stale(ticker: str) -> bool:
    row = db.last_fetch(_key(ticker))
    if row is None:
        return True
    if row["source"] == registry.SAMPLE and registry.mode() == "mock":
        return False
    age = datetime.now(timezone.utc) - datetime.fromisoformat(row["fetched_at"])
    ttl = get_settings().price_ttl_minutes
    if row["source"] == registry.SAMPLE:
        ttl = min(ttl, 60)  # retry live hourly after a fallback
    return age > timedelta(minutes=ttl)


def _store(ticker: str, df: pd.DataFrame) -> None:
    rows = [
        (ticker, d.strftime("%Y-%m-%d"), float(r.open), float(r.high), float(r.low), float(r.close),
         float(r.volume) if not np.isnan(r.volume) else 0.0)
        for d, r in df.iterrows()
    ]
    with db.tx() as c:
        c.executemany("INSERT OR REPLACE INTO prices VALUES (?,?,?,?,?,?,?)", rows)


def _last_date(ticker: str) -> str | None:
    r = db.query_one("SELECT MAX(date) AS d FROM prices WHERE ticker=?", (ticker,))
    return r["d"] if r else None


def refresh(tickers: list[str], force: bool = False) -> dict[str, str]:
    """Refresh stale tickers in one batch. Returns {ticker: source}."""
    tickers = sorted({t.upper() for t in tickers if t})
    todo = [t for t in tickers if force or _is_stale(t)]
    if not todo:
        return {}
    with _refresh_lock:
        todo = [t for t in todo if force or _is_stale(t)]
        if not todo:
            return {}
        # incremental: start a few days before the oldest "last date" among cached tickers
        lasts = [_last_date(t) for t in todo]
        start = None
        if all(lasts):
            start = str((pd.Timestamp(min(lasts)) - pd.Timedelta(days=7)).date())

        def live():
            data = registry.prices().daily(todo, start=start)
            if not data:
                raise ProviderError("no data returned")
            return data

        try:
            data, source = registry.live_or_sample(live, lambda: registry.fixtures().daily(todo), "prices")
        except ProviderError as e:
            log.warning("price refresh failed: %s", e)
            return {}
        out = {}
        for t in todo:
            df = data.get(t)
            if df is not None and not df.empty:
                _store(t, df)
                db.log_fetch(_key(t), source, f"{len(df)} bars")
                out[t] = source
            elif source != "live" or _last_date(t) is None:
                db.log_fetch(_key(t), source, "no data")
        return out


def daily(ticker: str, start: str | None = None, refresh_if_stale: bool = True) -> pd.DataFrame:
    t = ticker.upper()
    if refresh_if_stale:
        refresh([t])
    sql = "SELECT date, open, high, low, close, volume FROM prices WHERE ticker=?"
    params: list[Any] = [t]
    if start:
        sql += " AND date>=?"
        params.append(start)
    rows = db.query(sql + " ORDER BY date", params)
    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date").astype(float)


def daily_many(tickers: list[str], start: str | None = None) -> dict[str, pd.DataFrame]:
    refresh(tickers)
    return {t.upper(): daily(t, start, refresh_if_stale=False) for t in tickers}


def as_of(ticker: str) -> dict[str, Any]:
    f = db.last_fetch(_key(ticker)) or {}
    return {"last_bar": _last_date(ticker.upper()), "fetched_at": f.get("fetched_at"), "source": f.get("source")}


def intraday(ticker: str, interval: str = "5m") -> tuple[pd.DataFrame, dict[str, Any]]:
    t = ticker.upper()
    key = f"intraday:{t}:{interval}"
    cached = db.cache_get(key, max_age_minutes=get_settings().price_ttl_minutes)
    if cached is None:
        try:
            df, source = registry.live_or_sample(
                lambda: registry.prices().intraday(t, interval, 5),
                lambda: registry.fixtures().intraday(t, interval, 5), "intraday")
            payload = [{"ts": i.isoformat(), **{k: float(v) for k, v in r.items()}} for i, r in df.iterrows()]
            db.cache_put(key, payload, source)
            cached = db.cache_get(key)
        except ProviderError as e:
            log.info("intraday unavailable for %s: %s", t, e)
            return pd.DataFrame(), {"error": str(e)}
    payload, fetched_at, source = cached
    if not payload:
        return pd.DataFrame(), {"fetched_at": fetched_at, "source": source}
    df = pd.DataFrame(payload)
    df["ts"] = pd.to_datetime(df["ts"])
    return df.set_index("ts"), {"fetched_at": fetched_at, "source": source}


def info(ticker: str) -> dict[str, Any]:
    """Name, forward P/E, next earnings date etc. Cached 12h."""
    t = ticker.upper()
    key = f"info:{t}"
    hit = db.cache_get(key, max_age_minutes=12 * 60)
    if hit:
        return {**hit[0], "_fetched_at": hit[1], "_source": hit[2]}
    try:
        val, source = registry.live_or_sample(lambda: registry.prices().info(t),
                                              lambda: registry.fixtures().info(t), "info")
    except ProviderError:
        stale = db.cache_get(key)
        return {**stale[0], "_fetched_at": stale[1], "_source": stale[2]} if stale else {}
    ts = db.cache_put(key, val, source)
    return {**val, "_fetched_at": ts, "_source": source}


def snapshot(df: pd.DataFrame) -> dict[str, Any]:
    """Quote-like stats from daily bars."""
    if df is None or len(df) < 2:
        return {}
    c, v = df["close"], df["volume"]
    last, prev = float(c.iloc[-1]), float(c.iloc[-2])
    yr = df.iloc[-252:]
    hi, lo = float(yr["high"].max()), float(yr["low"].min())
    avg_vol = float(v.iloc[-51:-1].mean()) if len(v) > 5 else None
    out: dict[str, Any] = {
        "price": last, "prev_close": prev, "change": last - prev, "change_pct": (last / prev - 1) * 100,
        "volume": float(v.iloc[-1]), "avg_volume_50d": avg_vol,
        "volume_vs_avg": float(v.iloc[-1]) / avg_vol if avg_vol else None,
        "high_52w": hi, "low_52w": lo,
        "range_52w_pos": (last - lo) / (hi - lo) * 100 if hi > lo else None,
        "date": df.index[-1].strftime("%Y-%m-%d"),
    }
    for n in (50, 200):
        if len(c) >= n:
            ma = float(c.iloc[-n:].mean())
            out[f"sma{n}"] = ma
            out[f"dist_sma{n}_pct"] = (last / ma - 1) * 100
    for days, label in [(5, "1w"), (21, "1m"), (63, "3m"), (252, "1y")]:
        if len(c) > days:
            out[f"ret_{label}"] = (last / float(c.iloc[-days - 1]) - 1) * 100
    ytd = c[c.index.year == c.index[-1].year]
    prior = c[c.index.year < c.index[-1].year]
    if len(prior):
        out["ret_ytd"] = (last / float(prior.iloc[-1]) - 1) * 100
    return out
