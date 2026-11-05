"""Status, search, watchlists, market overview and ticker chart endpoints."""
from __future__ import annotations

from typing import Any

import pandas as pd
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..config import get_settings
from ..providers import registry
from ..services import indicators as ind
from ..services import market, prices, watchlists

router = APIRouter(prefix="/api")

RANGES = {"3m": 63, "6m": 126, "1y": 252, "2y": 504, "5y": 1260, "max": 100000}


@router.get("/status")
def status():
    s = get_settings()
    from ..services import sentiment
    return {
        "data_mode": s.data_mode,
        "ai_enabled": s.ai_enabled,
        "ai_model": s.openai_model if s.ai_enabled else None,
        "keys": s.key_status(),
        "sentiment_model": sentiment.active_scorer_name(),
        "scheduler": {"enabled": s.scheduler_enabled, "briefing_time": s.briefing_time, "timezone": s.timezone},
        "disclaimer": "MarketLens is a personal research tool. Nothing here is investment advice.",
    }


@router.get("/search")
def search(q: str):
    return market.search(q)


class WatchlistIn(BaseModel):
    name: str
    tickers: list[str] = []


class TickerIn(BaseModel):
    ticker: str


@router.get("/watchlists")
def get_watchlists():
    watchlists.ensure_default()
    return watchlists.list_watchlists()


@router.post("/watchlists")
def create_watchlist(body: WatchlistIn):
    return watchlists.create(body.name, body.tickers)


@router.patch("/watchlists/{wid}")
def rename_watchlist(wid: int, body: WatchlistIn):
    try:
        return watchlists.rename(wid, body.name)
    except watchlists.NotFound as e:
        raise HTTPException(404, str(e))


@router.delete("/watchlists/{wid}")
def delete_watchlist(wid: int):
    watchlists.delete(wid)
    return {"ok": "deleted"}


@router.post("/watchlists/{wid}/items")
def add_item(wid: int, body: TickerIn):
    try:
        return watchlists.add_ticker(wid, body.ticker)
    except watchlists.NotFound as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.delete("/watchlists/{wid}/items/{ticker}")
def remove_item(wid: int, ticker: str):
    return watchlists.remove_ticker(wid, ticker)


@router.get("/watchlists/{wid}/table")
def watchlist_table(wid: int):
    try:
        wl = watchlists.get(wid)
    except watchlists.NotFound as e:
        raise HTTPException(404, str(e))
    return {"watchlist": wl, "rows": market.watchlist_table(wl["tickers"])}


@router.get("/market/overview")
def overview():
    return market.overview()


@router.post("/refresh")
def refresh(body: dict[str, Any] | None = None):
    tickers = (body or {}).get("tickers") or watchlists.all_tickers()
    return {"refreshed": prices.refresh(tickers, force=True)}


def _candles(df: pd.DataFrame):
    return [
        {"time": d.strftime("%Y-%m-%d"), "open": round(r.open, 4), "high": round(r.high, 4),
         "low": round(r.low, 4), "close": round(r.close, 4), "volume": float(r.volume)}
        for d, r in df.iterrows()
    ]


@router.get("/ticker/{ticker}/chart")
def chart(ticker: str, range: str = "1y"):
    t = ticker.upper()
    df = prices.daily(t)
    if df.empty:
        raise HTTPException(404, f"No price data for {t}. Check the symbol, or the data source may be rate-limited.")
    ix = ind.compute_all(df)  # computed on full history so long MAs are valid at the left edge
    n = RANGES.get(range, 252)
    view, ixv = df.iloc[-n:], ix.iloc[-n:]
    series = {col: ind.series_payload(ixv.index, ixv[col]) for col in ixv.columns if col != "vol_avg20"}
    return {
        "ticker": t,
        "name": market.company_name(t),
        "candles": _candles(view),
        "indicators": series,
        "levels": ind.support_resistance(df),
        "snapshot": prices.snapshot(df),
        "as_of": prices.as_of(t),
    }


@router.get("/ticker/{ticker}/intraday")
def intraday(ticker: str, interval: str = "5m"):
    df, meta = prices.intraday(ticker, interval)
    if df.empty:
        return {"ticker": ticker.upper(), "bars": [], "vwap": [], "as_of": meta,
                "note": "Intraday data unavailable (free source limit or market closed)."}
    vw = ind.vwap(df)
    bars = [{"time": int(i.timestamp()), "open": r.open, "high": r.high, "low": r.low, "close": r.close,
             "volume": r.volume} for i, r in df.iterrows()]
    vwap = [{"time": int(i.timestamp()), "value": round(float(v), 4)} for i, v in vw.items() if pd.notna(v)]
    return {"ticker": ticker.upper(), "bars": bars, "vwap": vwap, "as_of": meta,
            "note": "Times shown in US/Eastern. Free intraday data is delayed ~15 minutes."}


@router.get("/ticker/{ticker}/info")
def info(ticker: str):
    t = ticker.upper()
    i = prices.info(t)
    return {"ticker": t, "name": i.get("name") or market.company_name(t), **i, "data_mode": registry.mode()}
