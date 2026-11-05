"""Ticker search, watchlist table rows and the market-overview strip."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .. import db
from ..providers import registry
from . import indicators as ind
from . import prices
from .universe import INDEX_STRIP, SECTOR_ETFS

log = logging.getLogger(__name__)

EXTRA_SYMBOLS = {
    "SPY": "SPDR S&P 500 ETF", "QQQ": "Invesco QQQ (Nasdaq 100)", "IWM": "iShares Russell 2000 ETF",
    "DIA": "SPDR Dow Jones Industrial Average ETF", "^VIX": "CBOE Volatility Index", "^TNX": "10-Year Treasury Yield",
    "VOO": "Vanguard S&P 500 ETF", "VTI": "Vanguard Total Stock Market ETF", "TLT": "iShares 20+ Year Treasury ETF",
    "GLD": "SPDR Gold Shares", "ARKK": "ARK Innovation ETF", "SMH": "VanEck Semiconductor ETF",
    "SOXX": "iShares Semiconductor ETF",
    **{k: f"{v} Select Sector SPDR" for k, v in SECTOR_ETFS.items()},
}


def ticker_map() -> dict[str, dict[str, Any]]:
    hit = db.cache_get("edgar:ticker_map", max_age_minutes=7 * 24 * 60)
    if hit:
        return hit[0]
    try:
        m, source = registry.live_or_sample(lambda: registry.fundamentals().ticker_map(),
                                            lambda: registry.fixtures().ticker_map(), "ticker map")
    except Exception as e:
        log.warning("ticker map unavailable: %s", e)
        m, source = registry.fixtures().ticker_map(), registry.SAMPLE
    if m:
        db.cache_put("edgar:ticker_map", m, source)
    return m


def company_name(ticker: str) -> str | None:
    t = ticker.upper()
    if t in EXTRA_SYMBOLS:
        return EXTRA_SYMBOLS[t]
    m = ticker_map().get(t.replace(".", "-"))
    return m["name"] if m else None


def search(q: str, limit: int = 12) -> list[dict[str, Any]]:
    q = q.strip().upper()
    if not q:
        return []
    universe = {**{k: {"name": v} for k, v in EXTRA_SYMBOLS.items()}, **ticker_map()}
    exact, prefix, name_hits = [], [], []
    for t, v in universe.items():
        name = (v.get("name") or "").upper()
        if t == q:
            exact.append((t, v))
        elif t.startswith(q):
            prefix.append((t, v))
        elif len(q) >= 3 and q in name:
            name_hits.append((t, v))
    prefix.sort(key=lambda x: len(x[0]))
    out = [{"ticker": t, "name": v.get("name")} for t, v in (exact + prefix + name_hits)[:limit]]
    return out


def table_row(ticker: str) -> dict[str, Any]:
    df = prices.daily(ticker, refresh_if_stale=False)
    row: dict[str, Any] = {"ticker": ticker, "name": company_name(ticker)}
    if df.empty:
        row["error"] = "no price data"
        return row
    row.update(prices.snapshot(df))
    r = ind.rsi(df["close"]).dropna()
    row["rsi14"] = float(r.iloc[-1]) if len(r) else None
    row["as_of"] = prices.as_of(ticker)
    return row


def watchlist_table(tickers: list[str]) -> list[dict[str, Any]]:
    prices.refresh(tickers)
    with ThreadPoolExecutor(max_workers=4) as ex:
        rows = list(ex.map(table_row, tickers))
    _enrich(rows)
    return rows


def _enrich(rows: list[dict[str, Any]]) -> None:
    """Add next-earnings and sentiment when those modules have data (best effort)."""
    try:
        from . import events
        for r in rows:
            r["next_earnings"] = events.next_earnings(r["ticker"])
    except Exception as e:  # pragma: no cover - optional enrichment
        log.debug("earnings enrich failed: %s", e)
    try:
        from . import news
        for r in rows:
            r["sentiment"] = news.sentiment_score(r["ticker"], days=7)
    except Exception as e:  # pragma: no cover
        log.debug("sentiment enrich failed: %s", e)


def overview() -> dict[str, Any]:
    tickers = [x["ticker"] for x in INDEX_STRIP] + list(SECTOR_ETFS)
    prices.refresh(tickers)
    strip = []
    for x in INDEX_STRIP:
        df = prices.daily(x["ticker"], refresh_if_stale=False)
        snap = prices.snapshot(df)
        if x["ticker"] == "^TNX" and snap:
            # Yahoo quotes ^TNX as yield x10 in older data; newer data is already in percent
            if snap["price"] > 20:
                for k in ("price", "prev_close", "change", "high_52w", "low_52w"):
                    snap[k] = snap[k] / 10
        spark = [round(float(v), 2) for v in df["close"].iloc[-30:]] if not df.empty else []
        strip.append({**x, **{k: snap.get(k) for k in ("price", "change", "change_pct", "date")}, "spark": spark,
                      "as_of": prices.as_of(x["ticker"])})
    sectors = []
    for t, name in SECTOR_ETFS.items():
        snap = prices.snapshot(prices.daily(t, refresh_if_stale=False))
        if snap:
            sectors.append({"ticker": t, "name": name, "change_pct": snap.get("change_pct"),
                            "ret_1w": snap.get("ret_1w"), "ret_1m": snap.get("ret_1m"), "ret_ytd": snap.get("ret_ytd"),
                            "date": snap.get("date")})
    return {"indices": strip, "sectors": sectors}
