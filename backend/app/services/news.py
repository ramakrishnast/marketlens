"""News headlines with per-article sentiment, sentiment trend vs price, spikes, and an AI narrative."""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd

from .. import db
from ..providers import registry
from ..providers.base import NewsItem
from . import llm, market, prices, sentiment

log = logging.getLogger(__name__)
NEWS_TTL_MIN = 60


def _id(item: NewsItem) -> str:
    key = (item.url or "") + "|" + item.headline.lower()[:120]
    return hashlib.sha1(key.encode()).hexdigest()[:20]


def _fetch_live(ticker: str) -> list[NewsItem]:
    company = market.company_name(ticker)
    if company:
        company = company.split(",")[0].replace(" INC", "").replace(" CORP", "").replace(" Inc.", "").strip()
    items: list[NewsItem] = []
    errors = []
    for p in registry.news_providers():
        try:
            if p.name == "google-news-rss":
                items += p.news(ticker, 30, company=company if company and ticker not in market.EXTRA_SYMBOLS else None)
            else:
                items += p.news(ticker, 30)
        except Exception as e:
            errors.append(f"{p.name}: {e}")
    if not items and errors:
        raise RuntimeError("; ".join(errors))
    return items


def refresh(ticker: str, force: bool = False) -> str | None:
    t = ticker.upper()
    key = f"news:{t}"
    last = db.last_fetch(key)
    if last and not force:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(last["fetched_at"])
        if age < timedelta(minutes=NEWS_TTL_MIN) or (last["source"] == registry.SAMPLE and registry.mode() == "mock"):
            return last["source"]
    try:
        items, source = registry.live_or_sample(lambda: _fetch_live(t), lambda: registry.fixtures().news(t), f"news {t}")
    except Exception as e:
        log.warning("news refresh failed for %s: %s", t, e)
        return None
    seen = {r["id"] for r in db.query("SELECT id FROM news WHERE ticker=?", (t,))}
    new = []
    for it in items:
        i = _id(it)
        if i in seen or not it.headline:
            continue
        seen.add(i)
        new.append((i, it))
    if new:
        scores, scorer = sentiment.score_texts([f"{it.headline}. {it.snippet}".strip() for _, it in new])
        with db.tx() as c:
            c.executemany(
                "INSERT OR IGNORE INTO news(id, ticker, headline, source, url, published_at, snippet, sentiment, sentiment_label, scorer, provider) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                [(i, t, it.headline, it.source, it.url, it.published_at, it.snippet, s, lab, scorer, it.provider)
                 for (i, it), (s, lab) in zip(new, scores)])
    db.log_fetch(key, source, f"{len(new)} new")
    return source


def headlines(ticker: str, days: int = 30, limit: int = 100) -> dict[str, Any]:
    t = ticker.upper()
    source = refresh(t)
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = db.query("SELECT * FROM news WHERE ticker=? AND published_at>=? ORDER BY published_at DESC LIMIT ?", (t, since, limit))
    if not rows and registry.mode() != "live":
        # sample data is a fixed snapshot: show it even if older than the window
        rows = db.query("SELECT * FROM news WHERE ticker=? ORDER BY published_at DESC LIMIT ?", (t, limit))
    f = db.last_fetch(f"news:{t}") or {}
    return {"ticker": t, "items": rows, "as_of": {"fetched_at": f.get("fetched_at"), "source": f.get("source") or source},
            "scorer": rows[0]["scorer"] if rows else sentiment.active_scorer_name()}


def sentiment_score(ticker: str, days: int = 7) -> dict[str, Any]:
    t = ticker.upper()
    refresh(t)
    rows = db.query("SELECT sentiment, published_at FROM news WHERE ticker=? ORDER BY published_at DESC LIMIT 200", (t,))
    if not rows:
        return {"score": None, "n": 0}
    latest = max(r["published_at"] for r in rows)
    cutoff = (datetime.fromisoformat(latest) - timedelta(days=days)).isoformat()
    vals = [r["sentiment"] for r in rows if r["published_at"] >= cutoff and r["sentiment"] is not None]
    return {"score": round(sum(vals) / len(vals), 3) if vals else None, "n": len(vals), "days": days}


def trend(ticker: str, days: int = 90) -> dict[str, Any]:
    """Daily mean sentiment and headline count, aligned with closes; spikes vs the ticker's own baseline."""
    t = ticker.upper()
    refresh(t)
    rows = db.query("SELECT published_at, sentiment FROM news WHERE ticker=? AND sentiment IS NOT NULL", (t,))
    px = prices.daily(t)
    if px.empty:
        return {"ticker": t, "points": [], "spikes": []}
    end = px.index[-1]
    start = end - pd.Timedelta(days=days)
    if rows:
        df = pd.DataFrame(rows)
        df["day"] = pd.to_datetime(df["published_at"], utc=True, format="ISO8601").dt.tz_localize(None).dt.normalize()
        daily = df.groupby("day")["sentiment"].agg(["mean", "count"])
    else:
        daily = pd.DataFrame(columns=["mean", "count"])
    closes = px["close"][px.index >= start]
    idx = closes.index.union(daily.index[(daily.index >= start) & (daily.index <= end + pd.Timedelta(days=3))])
    joined = pd.DataFrame(index=idx)
    joined["close"] = px["close"].reindex(idx).ffill()
    joined["sentiment"] = daily["mean"].reindex(idx)
    joined["count"] = daily["count"].reindex(idx).fillna(0)
    # rolling baseline: mean/std of the prior 30 calendar days of daily sentiment (headline-weighted days only)
    joined["sent_7d"] = joined["sentiment"].rolling(7, min_periods=1).mean()
    spikes = detect_spikes(daily["mean"], daily["count"])
    spikes = [s for s in spikes if pd.Timestamp(s["date"]) >= start]
    points = [{"date": d.strftime("%Y-%m-%d"), "close": None if pd.isna(r.close) else round(float(r.close), 2),
               "sentiment": None if pd.isna(r.sentiment) else round(float(r.sentiment), 3),
               "sent_7d": None if pd.isna(r.sent_7d) else round(float(r.sent_7d), 3), "count": int(r["count"])}
              for d, r in joined.iterrows()]
    n = int(daily["count"][daily.index >= start].sum()) if len(daily) else 0
    return {"ticker": t, "days": days, "points": points, "spikes": spikes, "n_headlines": n,
            "coverage_note": None if n >= 20 else "Few headlines in this window; trends are noisy. Free sources only go back ~30 days, so history builds up as the app keeps running."}


def detect_spikes(daily_mean: pd.Series, daily_count: pd.Series, z: float = 2.0, min_count: int = 3,
                  window: int = 30) -> list[dict[str, Any]]:
    """Flag days whose mean sentiment deviates > z std from the ticker's prior baseline.

    Baseline = mean/std of daily sentiment over the previous `window` days (excluding the day itself).
    Needs at least `min_count` headlines that day and 5 prior days of data.
    """
    s = daily_mean.sort_index()
    c = daily_count.reindex(s.index).fillna(0)
    out = []
    for i, (d, v) in enumerate(s.items()):
        prior = s[(s.index < d) & (s.index >= d - pd.Timedelta(days=window))]
        if len(prior) < 5 or c.loc[d] < min_count:
            continue
        mu, sd = float(prior.mean()), float(prior.std(ddof=0))
        sd = max(sd, 0.08)  # floor so near-constant baselines don't flag tiny moves
        zz = (float(v) - mu) / sd
        if abs(zz) >= z:
            out.append({"date": d.strftime("%Y-%m-%d"), "sentiment": round(float(v), 3), "baseline": round(mu, 3),
                        "z": round(zz, 2), "headlines": int(c.loc[d]), "direction": "positive" if zz > 0 else "negative"})
    return out


SUMMARY_SYSTEM = (
    "You summarize the recent news narrative for one stock. Use only the numbered headlines and snippets provided. "
    "Cite sources inline as [n] using the headline numbers. Do not invent facts. Be neutral and concise."
)


def ai_summary(ticker: str, force: bool = False, generate: bool = True) -> dict[str, Any]:
    t = ticker.upper()
    data = headlines(t, days=14, limit=40)
    items = data["items"]
    if not items:
        return {"ticker": t, "error": "No recent headlines to summarize."}
    sig = hashlib.sha1("|".join(i["id"] for i in items).encode()).hexdigest()[:12]
    key = f"news:{t}:{sig}"
    row = None if force else db.query_one("SELECT content, model, created_at FROM ai_summaries WHERE key=?", (key,))
    refs = [{"n": n + 1, "headline": i["headline"], "source": i["source"], "url": i["url"], "published_at": i["published_at"]}
            for n, i in enumerate(items)]
    if row:
        return {"ticker": t, "summary": row["content"], "model": row["model"], "created_at": row["created_at"], "refs": refs,
                "ai_generated": True}
    if not generate:
        return {"ticker": t, "summary": None, "refs": refs, "ai_enabled": llm.status()["enabled"]}
    if not llm.status()["enabled"]:
        return {"ticker": t, "needs_key": True, "message": llm.status()["message"], "refs": refs}
    lines = [f"[{r['n']}] {r['published_at'][:10]} | {r['source']} | {r['headline']}" +
             (f" | {items[r['n'] - 1]['snippet']}" if items[r['n'] - 1].get("snippet") else "") for r in refs]
    prompt = (f"Recent headlines about {t} ({market.company_name(t) or t}):\n" + "\n".join(lines) +
              "\n\nWrite markdown: a 2-3 sentence overview of the narrative, then '### Themes' with 3-5 bullets "
              "(each citing [n]), then '### What the headlines do not tell you' with 1-2 bullets.")
    try:
        content = llm.complete(SUMMARY_SYSTEM, prompt, max_tokens=1200)
    except (llm.AIUnavailable, llm.AIError) as e:
        return {"ticker": t, "error": str(e), "refs": refs}
    with db.tx() as c:
        c.execute("INSERT OR REPLACE INTO ai_summaries(key, kind, ticker, content, model, created_at) VALUES (?,?,?,?,?,?)",
                  (key, "news", t, content, llm.model(), db.now_iso()))
    return {"ticker": t, "summary": content, "model": llm.model(), "created_at": db.now_iso(), "refs": refs, "ai_generated": True}


def observations(ticker: str) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {"bull": [], "bear": []}
    s = sentiment_score(ticker, 7)
    if s["n"] >= 5 and s["score"] is not None:
        item = {"text": f"7-day news sentiment {s['score']:+.2f} across {s['n']} headlines",
                "source": f"MarketLens news sentiment ({sentiment.active_scorer_name()})"}
        if s["score"] > 0.15:
            out["bull"].append(item)
        elif s["score"] < -0.15:
            out["bear"].append(item)
    try:
        tr = trend(ticker, 30)
        for sp in tr["spikes"][-2:]:
            out["bull" if sp["direction"] == "positive" else "bear"].append({
                "text": f"News-sentiment spike ({sp['direction']}, z={sp['z']}) on {sp['date']}", "date": sp["date"],
                "source": "MarketLens news sentiment vs 30-day baseline"})
    except Exception:
        pass
    return out


def warm(tickers: list[str]) -> None:
    for t in tickers:
        refresh(t)


def to_json(o: Any) -> str:
    return json.dumps(o, default=str)
