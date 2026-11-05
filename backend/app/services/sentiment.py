"""Sentiment scoring for headlines and social snippets.

* FinBERT (ProsusAI/finbert) runs locally when the optional extra is installed (`make finbert`).
* Otherwise a lightweight finance lexicon scorer is used (fast, no downloads, less accurate).
Scores are in [-1, 1]; labels: positive / neutral / negative.
"""
from __future__ import annotations

import logging
import re
import threading
from functools import lru_cache
from typing import Any

from ..config import get_settings

log = logging.getLogger(__name__)

# Compact finance-oriented lexicon (inspired by Loughran-McDonald categories)
POSITIVE = {
    "beat", "beats", "beating", "surge", "surges", "surged", "soar", "soars", "soared", "jump", "jumps", "jumped",
    "rally", "rallies", "rallied", "gain", "gains", "gained", "rise", "rises", "rising", "rose", "record", "strong",
    "stronger", "strength", "growth", "grow", "grows", "upgrade", "upgrades", "upgraded", "outperform", "outperforms",
    "bullish", "buyback", "buybacks", "raise", "raises", "raised", "boost", "boosts", "boosted", "profit", "profitable",
    "exceed", "exceeds", "exceeded", "top", "tops", "topped", "expand", "expands", "expansion", "optimistic", "upbeat",
    "momentum", "breakthrough", "win", "wins", "approval", "approved", "partnership", "dividend", "accelerate",
    "accelerates", "accelerating", "rebound", "rebounds", "recover", "recovers", "recovery", "higher", "high", "best",
    "positive", "improve", "improves", "improved", "improvement", "upside", "demand", "robust", "solid", "climb",
    "climbs", "climbed", "overweight", "buy",
}
NEGATIVE = {
    "miss", "misses", "missed", "plunge", "plunges", "plunged", "fall", "falls", "fell", "drop", "drops", "dropped",
    "slump", "slumps", "slumped", "decline", "declines", "declined", "loss", "losses", "lose", "weak", "weaker",
    "weakness", "downgrade", "downgrades", "downgraded", "underperform", "bearish", "cut", "cuts", "lower", "lowers",
    "lowered", "warn", "warns", "warning", "lawsuit", "sue", "sued", "probe", "investigation", "fraud", "recall",
    "layoff", "layoffs", "risk", "risks", "concern", "concerns", "fear", "fears", "selloff", "sell-off", "slide",
    "slides", "slid", "tumble", "tumbles", "tumbled", "crash", "crashes", "sink", "sinks", "sank", "worst", "negative",
    "delay", "delays", "delayed", "halt", "halts", "ban", "bans", "tariff", "tariffs", "shortage", "volatile",
    "volatility", "uncertain", "uncertainty", "slowdown", "slowing", "downside", "pressure", "default", "bankruptcy",
    "underweight", "sell", "short", "dump", "puts",
}
NEGATORS = {"not", "no", "never", "without", "despite", "fails", "failed"}
_WORD = re.compile(r"[a-z][a-z\-']*")


def lexicon_score(text: str) -> float:
    words = _WORD.findall(text.lower())
    score, hits = 0.0, 0
    for i, w in enumerate(words):
        s = 1 if w in POSITIVE else -1 if w in NEGATIVE else 0
        if s == 0:
            continue
        if any(words[j] in NEGATORS for j in range(max(0, i - 3), i)):
            s = -s
        score += s
        hits += 1
    if hits == 0:
        return 0.0
    return max(-1.0, min(1.0, score / (hits + 1)))


def label_for(score: float) -> str:
    return "positive" if score > 0.15 else "negative" if score < -0.15 else "neutral"


_finbert_lock = threading.Lock()


@lru_cache(maxsize=1)
def _finbert() -> Any | None:
    mode = get_settings().sentiment_model
    if mode == "lexicon":
        return None
    try:
        from transformers import pipeline  # type: ignore
    except Exception:
        if mode == "finbert":
            log.warning("SENTIMENT_MODEL=finbert but transformers/torch are not installed; using lexicon")
        return None
    try:
        return pipeline("text-classification", model="ProsusAI/finbert", top_k=None, truncation=True)
    except Exception as e:
        log.warning("FinBERT failed to load (%s); using lexicon scorer", e)
        return None


def active_scorer_name() -> str:
    try:
        import importlib.util
        if get_settings().sentiment_model != "lexicon" and importlib.util.find_spec("transformers"):
            return "finbert"
    except Exception:
        pass
    return "lexicon"


def score_texts(texts: list[str]) -> tuple[list[tuple[float, str]], str]:
    """Score many texts. Returns ([(score, label)], scorer_name)."""
    if not texts:
        return [], active_scorer_name()
    pipe = _finbert() if active_scorer_name() == "finbert" else None
    if pipe is not None:
        with _finbert_lock:
            results = pipe([t[:512] for t in texts], batch_size=16)
        out = []
        for res in results:
            probs = {r["label"].lower(): r["score"] for r in res}
            s = probs.get("positive", 0) - probs.get("negative", 0)
            out.append((round(s, 4), label_for(s)))
        return out, "finbert"
    out = []
    for t in texts:
        s = lexicon_score(t)
        out.append((round(s, 4), label_for(s)))
    return out, "lexicon"
