"""Signals, scenario outlook, fundamentals, peers, filings and events for a ticker."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from ..providers.base import ProviderError
from ..services import events, fundamentals, outlook, prices, signals
from ..services import indicators as ind

router = APIRouter(prefix="/api/ticker")


@router.get("/{ticker}/signals")
def get_signals(ticker: str, days: int = 365):
    t = ticker.upper()
    df = prices.daily(t)
    if df.empty:
        raise HTTPException(404, f"No price data for {t}")
    ix = ind.compute_all(df)
    evts = signals.events(df, since_days=days, ix=ix)
    return {
        "ticker": t, "events": evts, "state": signals.state(df, ix), "markers": signals.chart_markers(evts),
        "rules": [{"id": r.id, "label": r.label, "rule": r.rule, "bias": r.bias} for r in signals.RULES],
        "as_of": prices.as_of(t),
        "note": "Signals describe chart patterns. They are not buy/sell recommendations.",
    }


@router.get("/{ticker}/outlook")
def get_outlook(ticker: str):
    return outlook.outlook(ticker)


@router.get("/{ticker}/fundamentals")
def get_fundamentals(ticker: str):
    t = ticker.upper()
    try:
        f = fundamentals.facts(t)
    except ProviderError as e:
        return {"ticker": t, "available": False,
                "reason": f"No SEC company facts for {t} (ETFs, funds and foreign filers usually have none). {e}"}
    v = fundamentals.valuation(t, f)
    return {"ticker": t, "available": True, "entity": f["entity"], "quarterly": f["quarterly"], "annual": f["annual"],
            "valuation": v, "as_of": f["as_of"]}


@router.get("/{ticker}/peers")
def get_peers(ticker: str):
    try:
        return fundamentals.peers(ticker)
    except ProviderError as e:
        return {"ticker": ticker.upper(), "peers": [], "error": str(e)}


@router.get("/{ticker}/filings")
def get_filings(ticker: str):
    try:
        return {**fundamentals.filings(ticker), "available": True}
    except ProviderError as e:
        return {"ticker": ticker.upper(), "available": False, "filings": [], "reason": str(e)}


@router.get("/{ticker}/events")
def get_events(ticker: str):
    return {"ticker": ticker.upper(), "upcoming": events.upcoming(ticker, 60), "next_earnings": events.next_earnings(ticker),
            "earnings_reaction": events.earnings_reactions(ticker)}


@router.get("/{ticker}/filings/summary")
def get_filing_summary(ticker: str, accession: str | None = None):
    from ..services import filing_summary
    try:
        return filing_summary.summarize(ticker, accession, generate=False)
    except ProviderError as e:
        return {"ticker": ticker.upper(), "error": str(e)}


@router.post("/{ticker}/filings/summary")
def make_filing_summary(ticker: str, body: dict | None = None):
    from ..services import filing_summary
    try:
        return filing_summary.summarize(ticker, (body or {}).get("accession"), force=bool((body or {}).get("force")))
    except ProviderError as e:
        return {"ticker": ticker.upper(), "error": str(e)}
