"""News, sentiment trend, AI narrative and crowd endpoints."""
from __future__ import annotations

from fastapi import APIRouter

from ..services import news, social

router = APIRouter(prefix="/api")


@router.get("/ticker/{ticker}/news")
def get_news(ticker: str, days: int = 30):
    return news.headlines(ticker, days)


@router.get("/ticker/{ticker}/sentiment")
def get_sentiment(ticker: str, days: int = 90):
    return {**news.trend(ticker, days), "score_7d": news.sentiment_score(ticker, 7)}


@router.get("/ticker/{ticker}/news/summary")
def get_news_summary(ticker: str):
    """Cached summary only (does not call the AI)."""
    return news.ai_summary(ticker, generate=False)


@router.post("/ticker/{ticker}/news/summary")
def make_news_summary(ticker: str, body: dict | None = None):
    return news.ai_summary(ticker, force=bool((body or {}).get("force")))


@router.post("/ticker/{ticker}/news/refresh")
def refresh_news(ticker: str):
    news.refresh(ticker, force=True)
    social.refresh_posts(ticker, force=True)
    return {"ok": True}


@router.get("/ticker/{ticker}/crowd")
def get_crowd(ticker: str):
    return social.crowd(ticker)


@router.get("/social/trending")
def get_trending(platform: str = "apewisdom:wallstreetbets", limit: int = 25):
    return social.trending(platform, limit)
