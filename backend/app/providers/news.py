"""News providers. We store headline, source, url, time and a short snippet only.

Full article text is never fetched, stored or displayed; the UI always links to the original.
"""
from __future__ import annotations

import html
import json
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus

import httpx

from ..config import CONFIG_DIR, get_settings
from .base import NewsItem, ProviderError

SNIPPET_LEN = 280
UA = {"User-Agent": "Mozilla/5.0 (MarketLens personal research)"}


def clean_snippet(text: str | None, limit: int = SNIPPET_LEN) -> str:
    if not text:
        return ""
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        text = text[: limit - 1].rsplit(" ", 1)[0] + "…"
    return text


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


class FinnhubNewsProvider:
    name = "finnhub"

    def news(self, ticker: str, days: int = 30) -> list[NewsItem]:
        key = get_settings().finnhub_api_key
        if not key:
            raise ProviderError("FINNHUB_API_KEY not set")
        end = datetime.now(timezone.utc).date()
        start = end - timedelta(days=days)
        r = httpx.get(
            "https://finnhub.io/api/v1/company-news",
            params={"symbol": ticker, "from": str(start), "to": str(end), "token": key}, timeout=20,
        )
        if r.status_code == 429:
            raise ProviderError("Finnhub rate limit")
        r.raise_for_status()
        out = []
        for a in r.json()[:100]:
            if not a.get("headline") or not a.get("url"):
                continue
            out.append(NewsItem(
                ticker=ticker, headline=clean_snippet(a["headline"], 300), source=a.get("source") or "Finnhub",
                url=a["url"], published_at=_iso(datetime.fromtimestamp(a.get("datetime", 0), timezone.utc)),
                snippet=clean_snippet(a.get("summary")), provider=self.name,
            ))
        return out


class AlphaVantageNewsProvider:
    name = "alphavantage"

    def news(self, ticker: str, days: int = 30) -> list[NewsItem]:
        key = get_settings().alpha_vantage_api_key
        if not key:
            raise ProviderError("ALPHA_VANTAGE_API_KEY not set")
        r = httpx.get("https://www.alphavantage.co/query", params={
            "function": "NEWS_SENTIMENT", "tickers": ticker, "limit": 50, "apikey": key}, timeout=20)
        r.raise_for_status()
        data = r.json()
        if "feed" not in data:
            raise ProviderError(f"Alpha Vantage: {data.get('Note') or data.get('Information') or 'no feed'}")
        out = []
        for a in data["feed"]:
            ts = datetime.strptime(a["time_published"], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
            out.append(NewsItem(ticker=ticker, headline=clean_snippet(a["title"], 300), source=a.get("source", ""),
                                url=a["url"], published_at=_iso(ts), snippet=clean_snippet(a.get("summary")),
                                provider=self.name))
        return out


def parse_rss(xml_text: str, ticker: str, provider: str, default_source: str = "") -> list[NewsItem]:
    import feedparser

    feed = feedparser.parse(xml_text)
    out = []
    for e in feed.entries:
        title = e.get("title", "")
        source = default_source
        if e.get("source") and getattr(e.source, "title", None):
            source = e.source.title
        # Google News titles end with " - Source"
        if provider == "google-news-rss" and " - " in title:
            title, src = title.rsplit(" - ", 1)
            source = source or src
        published = e.get("published") or e.get("updated")
        try:
            ts = _iso(parsedate_to_datetime(published)) if published else _iso(datetime.now(timezone.utc))
        except (TypeError, ValueError):
            try:
                ts = _iso(datetime.fromisoformat(published.replace("Z", "+00:00")))
            except Exception:
                ts = _iso(datetime.now(timezone.utc))
        snippet = clean_snippet(e.get("summary"))
        if provider == "google-news-rss":
            snippet = ""  # Google's summary just repeats the title as HTML
        out.append(NewsItem(ticker=ticker, headline=clean_snippet(title, 300), source=source or provider,
                            url=e.get("link", ""), published_at=ts, snippet=snippet, provider=provider))
    return out


class GoogleNewsRSSProvider:
    """Public Google News RSS search (no key). Query is '<TICKER> stock' plus company name when known."""

    name = "google-news-rss"

    def news(self, ticker: str, days: int = 30, company: str | None = None) -> list[NewsItem]:
        q = f'"{ticker}" stock' if not company else f'"{ticker}" OR "{company}" stock'
        url = f"https://news.google.com/rss/search?q={quote_plus(q + f' when:{min(days, 30)}d')}&hl=en-US&gl=US&ceid=US:en"
        r = httpx.get(url, headers=UA, timeout=20, follow_redirects=True)
        if r.status_code != 200:
            raise ProviderError(f"Google News RSS HTTP {r.status_code}")
        return parse_rss(r.text, ticker, self.name)[:80]


class InvestorRelationsRSSProvider:
    """Company investor-relations RSS feeds listed in config/ir_feeds.json ({"AAPL": "https://..."})."""

    name = "ir-rss"

    def news(self, ticker: str, days: int = 30) -> list[NewsItem]:
        path = CONFIG_DIR / "ir_feeds.json"
        feeds = json.loads(path.read_text()) if path.exists() else {}
        url = feeds.get(ticker.upper())
        if not url:
            return []
        r = httpx.get(url, headers=UA, timeout=20, follow_redirects=True)
        r.raise_for_status()
        return parse_rss(r.text, ticker, self.name, default_source=f"{ticker} Investor Relations")
