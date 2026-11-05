"""Crowd chatter: Reddit mention ranks (ApeWisdom), StockTwits and Reddit posts, scored with the same model.

Crowd opinion is unverified and often hype or irony. It is shown as context, never as a signal to act on.
"""
from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

from .. import db
from ..config import get_settings
from ..providers import registry
from . import sentiment

log = logging.getLogger(__name__)
MENTIONS_TTL = 60
POSTS_TTL = 30
LABEL = ("Crowd opinion from public forums: unverified, often hype, irony or promotion. "
         "Useful for gauging attention, not for facts.")


def _fresh(key: str, ttl: int) -> bool:
    last = db.last_fetch(key)
    if not last:
        return False
    if last["source"] == registry.SAMPLE and registry.mode() == "mock":
        return True
    return datetime.now(timezone.utc) - datetime.fromisoformat(last["fetched_at"]) < timedelta(minutes=ttl)


def refresh_mentions(force: bool = False) -> None:
    if not force and _fresh("social:mentions", MENTIONS_TTL):
        return
    try:
        stats, source = registry.live_or_sample(lambda: registry.mentions().mentions(), lambda: registry.fixtures().mentions(),
                                                "apewisdom")
    except Exception as e:
        log.warning("mentions refresh failed: %s", e)
        return
    today = date.today().isoformat()
    with db.tx() as c:
        c.executemany(
            "INSERT OR REPLACE INTO social_mentions(ticker, date, platform, mentions, rank, upvotes, mentions_24h_ago, rank_24h_ago) "
            "VALUES (?,?,?,?,?,?,?,?)",
            [(m.ticker, today, m.platform, m.mentions, m.rank, m.upvotes, m.mentions_24h_ago, m.rank_24h_ago) for m in stats])
    db.log_fetch("social:mentions", source, f"{len(stats)} rows")


def refresh_posts(ticker: str, force: bool = False) -> None:
    t = ticker.upper()
    key = f"social:posts:{t}"
    if not force and _fresh(key, POSTS_TTL):
        return

    def live():
        posts, errors = [], []
        try:
            posts += registry.stocktwits().posts(t)
        except Exception as e:
            errors.append(f"stocktwits: {e}")
        s = get_settings()
        if s.reddit_client_id and s.reddit_client_secret:
            try:
                posts += registry.reddit().posts(t)
            except Exception as e:
                errors.append(f"reddit: {e}")
        if not posts and errors:
            raise RuntimeError("; ".join(errors))
        return posts

    try:
        posts, source = registry.live_or_sample(live, lambda: registry.fixtures().posts(t), f"social {t}")
    except Exception as e:
        log.warning("social posts refresh failed for %s: %s", t, e)
        return
    existing = {r["id"] for r in db.query("SELECT id FROM social_posts WHERE ticker=?", (t,))}
    new = [p for p in posts if p.id not in existing and p.snippet]
    if new:
        scores, _ = sentiment.score_texts([p.snippet for p in new])
        with db.tx() as c:
            c.executemany(
                "INSERT OR IGNORE INTO social_posts(id, ticker, platform, community, author, snippet, url, created_at, score, user_label, sentiment, sentiment_label) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                [(p.id, t, p.platform, p.community, p.author, p.snippet, p.url, p.created_at, p.score, p.user_label, s, lab)
                 for p, (s, lab) in zip(new, scores)])
    db.log_fetch(key, source, f"{len(new)} new")


def crowd(ticker: str) -> dict[str, Any]:
    t = ticker.upper()
    refresh_mentions()
    refresh_posts(t)
    rows = db.query("SELECT * FROM social_mentions WHERE ticker=? ORDER BY date", (t,))
    latest: dict[str, Any] = {}
    history: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        latest[r["platform"]] = r
        history.setdefault(r["platform"], []).append({"date": r["date"], "mentions": r["mentions"], "rank": r["rank"]})
    mentions = []
    for platform, r in latest.items():
        prev = r["mentions_24h_ago"]
        mentions.append({
            "platform": platform, "label": "r/wallstreetbets" if platform.endswith("wallstreetbets") else "All stock subreddits",
            "rank": r["rank"], "mentions_24h": r["mentions"], "mentions_prev_24h": prev, "upvotes": r["upvotes"],
            "rank_24h_ago": r["rank_24h_ago"], "change_pct": ((r["mentions"] / prev - 1) * 100) if prev else None,
            "date": r["date"], "url": "https://apewisdom.io/" + ("wallstreetbets/" if platform.endswith("wallstreetbets") else ""),
        })
    posts = db.query("SELECT * FROM social_posts WHERE ticker=? ORDER BY created_at DESC LIMIT 60", (t,))
    labeled = [p for p in posts if p["user_label"] in ("Bullish", "Bearish")]
    bull = sum(1 for p in labeled if p["user_label"] == "Bullish")
    model_scores = [p["sentiment"] for p in posts if p["sentiment"] is not None]
    by_platform: dict[str, int] = {}
    for p in posts:
        by_platform[p["platform"]] = by_platform.get(p["platform"], 0) + 1
    m_as_of = db.last_fetch("social:mentions") or {}
    p_as_of = db.last_fetch(f"social:posts:{t}") or {}
    return {
        "ticker": t, "label": LABEL, "mentions": mentions, "mention_history": history,
        "stocktwits_tags": {"bullish": bull, "bearish": len(labeled) - bull, "tagged": len(labeled), "total": by_platform.get("stocktwits", 0),
                            "bullish_pct": round(bull / len(labeled) * 100, 1) if labeled else None},
        "model_sentiment": {"score": round(sum(model_scores) / len(model_scores), 3) if model_scores else None, "n": len(model_scores),
                            "scorer": sentiment.active_scorer_name()},
        "posts": posts[:40], "counts": by_platform,
        "reddit_enabled": bool(get_settings().reddit_client_id and get_settings().reddit_client_secret),
        "as_of": {"mentions": {"fetched_at": m_as_of.get("fetched_at"), "source": m_as_of.get("source")},
                  "posts": {"fetched_at": p_as_of.get("fetched_at"), "source": p_as_of.get("source")}},
    }


def trending(platform: str = "apewisdom:wallstreetbets", limit: int = 25) -> dict[str, Any]:
    refresh_mentions()
    d = db.query_one("SELECT MAX(date) AS d FROM social_mentions WHERE platform=?", (platform,))
    rows = db.query("SELECT * FROM social_mentions WHERE platform=? AND date=? ORDER BY rank LIMIT ?", (platform, d["d"] if d else "", limit))
    f = db.last_fetch("social:mentions") or {}
    return {"platform": platform, "rows": rows, "label": LABEL, "as_of": {"fetched_at": f.get("fetched_at"), "source": f.get("source")}}


def observations(ticker: str) -> dict[str, list[dict[str, Any]]]:
    c = crowd(ticker)
    out: dict[str, list[dict[str, Any]]] = {"bull": [], "bear": [], "neutral": []}
    for m in c["mentions"]:
        if m["rank"] and m["rank"] <= 20:
            out["neutral"].append({"text": f"Heavily discussed on {m['label']}: rank #{m['rank']}, {m['mentions_24h']} mentions in 24h "
                                           "(crowd attention can mean elevated volatility)", "date": m["date"],
                                   "source": "ApeWisdom (Reddit mention counts)", "url": m["url"]})
    st = c["stocktwits_tags"]
    if st["tagged"] >= 10:
        item = {"text": f"StockTwits users tagged {st['bullish_pct']:.0f}% of {st['tagged']} recent posts bullish (crowd opinion)",
                "source": "StockTwits user tags"}
        if st["bullish_pct"] >= 70:
            out["bull"].append(item)
        elif st["bullish_pct"] <= 40:
            out["bear"].append(item)
    return out


def warm(tickers: list[str]) -> None:
    refresh_mentions()
    for t in tickers:
        refresh_posts(t)


__all__ = ["crowd", "trending", "refresh_mentions", "refresh_posts", "asdict"]
