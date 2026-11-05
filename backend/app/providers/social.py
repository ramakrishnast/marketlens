"""Crowd chatter: ApeWisdom (Reddit mention counts), StockTwits (public stream), Reddit API.

This is crowd opinion: unverified, often hype or ironic. The UI labels it accordingly.
Only short snippets + links are stored.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

import httpx

from ..config import get_settings
from .base import MentionStat, ProviderError, SocialPost
from .news import clean_snippet

UA = {"User-Agent": "Mozilla/5.0 (MarketLens personal research)"}


class ApeWisdomProvider:
    """Aggregated ticker mention counts across r/wallstreetbets and other stock subreddits (no key)."""

    name = "apewisdom"
    FILTERS = {"wallstreetbets": "r/wallstreetbets", "all-stocks": "all stock subreddits"}

    def mentions(self, max_pages: int = 3) -> list[MentionStat]:
        out: list[MentionStat] = []
        for flt in self.FILTERS:
            for page in range(1, max_pages + 1):
                r = httpx.get(f"https://apewisdom.io/api/v1.0/filter/{flt}/page/{page}", headers=UA, timeout=20)
                if r.status_code != 200:
                    raise ProviderError(f"ApeWisdom HTTP {r.status_code}")
                data = r.json()
                for x in data.get("results", []):
                    def _i(v):
                        try:
                            return int(v)
                        except (TypeError, ValueError):
                            return None
                    out.append(MentionStat(
                        ticker=x["ticker"].upper(), platform=f"apewisdom:{flt}", mentions=_i(x.get("mentions")) or 0,
                        rank=_i(x.get("rank")), upvotes=_i(x.get("upvotes")),
                        mentions_24h_ago=_i(x.get("mentions_24h_ago")), rank_24h_ago=_i(x.get("rank_24h_ago")),
                    ))
                if page >= int(data.get("pages", 1)):
                    break
                time.sleep(0.5)
        return out


class StockTwitsProvider:
    """Public symbol stream; users optionally tag posts Bullish/Bearish. Unofficial, may rate-limit."""

    name = "stocktwits"

    def posts(self, ticker: str) -> list[SocialPost]:
        r = httpx.get(f"https://api.stocktwits.com/api/2/streams/symbol/{ticker}.json", headers=UA, timeout=20)
        if r.status_code != 200:
            raise ProviderError(f"StockTwits HTTP {r.status_code}")
        out = []
        for m in r.json().get("messages", []):
            user = (m.get("user") or {}).get("username", "")
            label = ((m.get("entities") or {}).get("sentiment") or {}) or {}
            out.append(SocialPost(
                id=f"st-{m['id']}", ticker=ticker, platform="stocktwits", community="StockTwits", author=user,
                snippet=clean_snippet(m.get("body")), url=f"https://stocktwits.com/{user}/message/{m['id']}",
                created_at=m.get("created_at", "").replace("Z", "+00:00"),
                score=int(((m.get("likes") or {}).get("total")) or 0), user_label=label.get("basic"),
            ))
        return out


class RedditProvider:
    """Reddit search via the official API (free 'script' app: REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET)."""

    name = "reddit"
    SUBREDDITS = "wallstreetbets+stocks+investing+StockMarket+options"

    def __init__(self) -> None:
        self._token: tuple[str, float] | None = None

    def _auth(self) -> str:
        s = get_settings()
        if not (s.reddit_client_id and s.reddit_client_secret):
            raise ProviderError("REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET not set")
        if self._token and self._token[1] > time.time() + 60:
            return self._token[0]
        r = httpx.post("https://www.reddit.com/api/v1/access_token",
                       auth=(s.reddit_client_id, s.reddit_client_secret),
                       data={"grant_type": "client_credentials"}, headers={"User-Agent": s.reddit_user_agent},
                       timeout=20)
        if r.status_code != 200:
            raise ProviderError(f"Reddit auth HTTP {r.status_code}")
        d = r.json()
        self._token = (d["access_token"], time.time() + int(d.get("expires_in", 3600)))
        return self._token[0]

    def posts(self, ticker: str) -> list[SocialPost]:
        token = self._auth()
        s = get_settings()
        r = httpx.get(f"https://oauth.reddit.com/r/{self.SUBREDDITS}/search", params={
            "q": f'"${ticker}" OR "{ticker}"', "restrict_sr": "1", "sort": "new", "t": "week", "limit": 50,
        }, headers={"Authorization": f"Bearer {token}", "User-Agent": s.reddit_user_agent}, timeout=20)
        if r.status_code != 200:
            raise ProviderError(f"Reddit search HTTP {r.status_code}")
        out = []
        for c in r.json().get("data", {}).get("children", []):
            d = c["data"]
            text = d.get("title", "") + (" — " + d["selftext"] if d.get("selftext") else "")
            out.append(SocialPost(
                id=f"rd-{d['id']}", ticker=ticker, platform="reddit", community=f"r/{d.get('subreddit')}",
                author=d.get("author", ""), snippet=clean_snippet(text),
                url=f"https://www.reddit.com{d.get('permalink', '')}",
                created_at=datetime.fromtimestamp(d.get("created_utc", 0), timezone.utc).isoformat(),
                score=int(d.get("score") or 0),
            ))
        return out
