"""AI Research Desk: a multi-agent research memo, inspired by TauricResearch/TradingAgents (Apache-2.0).

Pipeline (all agents run on gpt-6-luna over data packs built from MarketLens's own cached data):
  1. Analyst team (in parallel): technical, fundamentals, news, crowd, Congress. Each writes a short report.
  2. Research debate: a bull researcher and a bear researcher argue from the analyst reports (N rounds).
  3. Risk lenses: conservative, neutral and aggressive perspectives on what could go wrong.
  4. Research manager: a scenario memo with dated things to watch.

Unlike TradingAgents there is NO trader or portfolio-manager agent and NO BUY/SELL/HOLD rating: the output
describes scenarios and evidence, and the decision stays with the user.
"""
from __future__ import annotations

import hashlib
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Any, Callable

from .. import db
from . import chat_tools, llm

log = logging.getLogger(__name__)

ANALYSTS: dict[str, dict[str, Any]] = {
    "technical": {"title": "Technical analyst", "tools": [("get_technicals", {}), ("get_price_history", {"period": "6m"}), ("get_outlook", {})],
                  "focus": "trend, momentum, key levels, recent signals and what historically followed them"},
    "fundamentals": {"title": "Fundamentals analyst", "tools": [("get_fundamentals", {})],
                     "focus": "growth, margins, cash flow, balance sheet, valuation vs its own history"},
    "news": {"title": "News analyst", "tools": [("get_news", {"days": 14}), ("get_filings", {"forms": ["8-K", "10-Q", "10-K"]})],
             "focus": "the current news narrative, recent filings, and upcoming catalysts"},
    "crowd": {"title": "Crowd-sentiment analyst", "tools": [("get_crowd", {})],
              "focus": "retail attention and tone on Reddit/StockTwits, and what that implies about volatility. Treat it as unverified opinion"},
    "congress": {"title": "Congress-disclosures analyst", "tools": [("get_congress_trades", {})],
                 "focus": "disclosed trades by members of Congress in this stock (they lag and don't reveal intent)"},
}

RULES = ("Use only the data provided; cite figures with their dates. No buy/sell/hold ratings, price targets or advice. "
         "Be concise and specific (bullets, under 180 words).")


def _pack(ticker: str, tools: list[tuple[str, dict[str, Any]]]) -> str:
    parts = []
    for name, extra in tools:
        args = {"ticker": ticker, **extra}
        res = chat_tools.call(name, args)
        res = {k: v for k, v in res.items() if k not in ("sources",)}
        parts.append(f"## {name}\n{json.dumps(res, default=str)[:9000]}")
    return "\n\n".join(parts)


def _ask(system: str, user: str, max_tokens: int = 900) -> str:
    return llm.complete(system, user, max_tokens=max_tokens, effort="low")


def run(ticker: str, rounds: int = 1, force: bool = False, progress: Callable[[str], None] | None = None) -> dict[str, Any]:
    t = ticker.upper()
    key = f"desk:{t}:{date.today().isoformat()}:{rounds}"
    if not force:
        row = db.query_one("SELECT content, model, created_at FROM ai_summaries WHERE key=?", (key,))
        if row:
            return {**json.loads(row["content"]), "cached": True}
    if not llm.status()["enabled"]:
        return {"ticker": t, "needs_key": True, "message": llm.status()["message"]}
    step = progress or (lambda s: None)

    # 1. analysts (parallel)
    step("analysts")
    packs = {k: _pack(t, a["tools"]) for k, a in ANALYSTS.items()}

    def analyst(k: str) -> tuple[str, str]:
        a = ANALYSTS[k]
        sys = f"You are the {a['title']} on an equity research desk. Focus: {a['focus']}. {RULES}"
        return k, _ask(sys, f"Ticker: {t}\n\nDATA:\n{packs[k]}\n\nWrite your report.")

    try:
        with ThreadPoolExecutor(max_workers=5) as ex:
            reports = dict(ex.map(analyst, ANALYSTS))
    except (llm.AIUnavailable, llm.AIError) as e:
        return {"ticker": t, "error": str(e)}
    team = "\n\n".join(f"### {ANALYSTS[k]['title']}\n{v}" for k, v in reports.items())

    # 2. bull vs bear debate
    step("debate")
    debate: list[dict[str, str]] = []
    transcript = ""
    try:
        for r in range(max(1, min(rounds, 3))):
            for side in ("bull", "bear"):
                sys = (f"You are the {side} researcher. Build the strongest evidence-based {side} case for {t} from the analyst "
                       f"reports and respond directly to the other side's last points. {RULES}")
                msg = _ask(sys, f"ANALYST REPORTS:\n{team}\n\nDEBATE SO FAR:\n{transcript or '(none yet)'}\n\nYour turn (round {r + 1}).", 700)
                debate.append({"side": side, "round": r + 1, "text": msg})
                transcript += f"\n\n[{side.upper()} r{r + 1}] {msg}"

        # 3. risk lenses (parallel)
        step("risk")

        def lens(name: str) -> tuple[str, str]:
            sys = (f"You are the {name} risk analyst. From the reports and debate, list what could go wrong for someone "
                   f"exposed to {t} from a {name} perspective, the dated events that matter, and what evidence would change "
                   f"your view. No recommendations. {RULES}")
            return name, _ask(sys, f"REPORTS:\n{team}\n\nDEBATE:\n{transcript}", 600)

        with ThreadPoolExecutor(max_workers=3) as ex:
            risks = dict(ex.map(lens, ["conservative", "neutral", "aggressive"]))

        # 4. research manager memo
        step("memo")
        sys = ("You are the research manager. Write the final research memo in markdown with sections: "
               "'## Summary' (3 sentences), '## Bull scenario' and '## Bear scenario' (each: what has to happen, evidence for "
               "it, early signs to watch), '## Dates and levels to watch' (a table: date or price level | why it matters | source), "
               "'## Open questions'. Do NOT give a buy/sell/hold rating, price target, or tell the reader what to do. "
               "End with: 'This memo is AI-generated research, not investment advice. The decision is yours.'")
        memo = _ask(sys, f"TICKER {t}\n\nANALYST REPORTS:\n{team}\n\nDEBATE:\n{transcript}\n\nRISK LENSES:\n" +
                    "\n\n".join(f"[{k}] {v}" for k, v in risks.items()), 1800)
    except (llm.AIUnavailable, llm.AIError) as e:
        return {"ticker": t, "error": str(e), "reports": reports}

    out = {"ticker": t, "date": date.today().isoformat(), "model": llm.model(), "reports": reports,
           "analyst_titles": {k: a["title"] for k, a in ANALYSTS.items()}, "debate": debate, "risk": risks, "memo": memo,
           "ai_generated": True, "inspired_by": "TauricResearch/TradingAgents (Apache-2.0): analyst team → researcher debate → risk review",
           "digest": hashlib.sha1(team.encode()).hexdigest()[:10]}
    with db.tx() as c:
        c.execute("INSERT OR REPLACE INTO ai_summaries(key, kind, ticker, content, model, created_at) VALUES (?,?,?,?,?,?)",
                  (key, "desk", t, json.dumps(out), llm.model(), db.now_iso()))
    return out
