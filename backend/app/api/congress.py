"""Congress trades endpoints."""
from __future__ import annotations

import threading

from fastapi import APIRouter

from ..services import congress

router = APIRouter(prefix="/api/congress")
_refreshing = threading.Event()


@router.get("/leaderboard")
def leaderboard(period: str = "6m", min_trades: int = 3, party: str | None = None, rank_by: str = "estimated"):
    return congress.leaderboard(period, min_trades, party, rank_by)


@router.get("/performance")
def performance(members: str = "", period: str = "6m"):
    names = [m for m in members.split("|") if m]
    return congress.performance(names, period)


@router.get("/member")
def member(name: str, period: str = "6m"):
    return congress.member_detail(name, period)


@router.get("/filings")
def filings(days: int = 30):
    return congress.recent_filings(days)


@router.get("/stocks")
def stocks(period: str = "90d"):
    return congress.top_stocks(period)


@router.get("/ticker/{ticker}")
def for_ticker(ticker: str):
    return congress.for_ticker(ticker)


@router.get("/following")
def following():
    return congress.following()


@router.post("/follow")
def follow(body: dict):
    congress.follow(body["member"], bool(body.get("on", True)))
    return congress.following()


@router.get("/search")
def search(q: str):
    return congress.search_members(q)


@router.post("/refresh")
def refresh():
    """Fetch new House filings in the background (PDF parsing takes a while)."""
    if _refreshing.is_set():
        return {"status": "already running"}

    def job():
        _refreshing.set()
        try:
            congress.refresh()
        finally:
            _refreshing.clear()

    threading.Thread(target=job, daemon=True).start()
    return {"status": "started"}
