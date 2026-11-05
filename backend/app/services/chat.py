"""Ask-AI chat: OpenAI (gpt-6-luna) tool calling over MarketLens's own data.

The model must call tools for every number; answers cite the tools' sources and data dates. Inline
charts/tables are produced by render_* tools and returned to the UI as `visuals`.
"""
from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any

from . import chat_tools, llm

log = logging.getLogger(__name__)
MAX_ROUNDS = 8
MAX_TOOL_CHARS = 14000

SYSTEM = f"""You are MarketLens's research assistant for US stocks and ETFs. Today is {{today}}.

Rules:
1. Get every number (prices, returns, ratios, dates, counts) from tool calls in this conversation. Never state
   figures from memory. If a tool lacks the data, say so.
2. Cite as you go: after a claim, add a markdown link to the source URL from the tool result, e.g. ([Reuters](url)),
   and mention data dates ("as of 2026-11-04 close").
3. For "why did X move" questions: call get_price_history to find the move dates, then get_news for those dates,
   and connect only what the headlines support. Say when the cause is unclear.
4. You never give buy/sell/hold recommendations, price targets or position sizes. For "should I buy/sell X" or
   timing questions, call get_outlook (and other tools as needed), lay out the data, bull and bear cases, dated
   catalysts and risks, then say plainly that the decision is the user's and that this is not advice.
5. Crowd/Reddit data is unverified opinion; label it as such. Congress trades are disclosures, not signals.
6. Use render_chart or render_table when a visual helps (comparisons, screens). Keep answers concise: short
   paragraphs and bullet points, markdown tables where useful.
"""


def _trim(result: dict[str, Any]) -> str:
    s = json.dumps(result, default=str)
    if len(s) > MAX_TOOL_CHARS:
        s = s[:MAX_TOOL_CHARS] + '..."(truncated)"'
    return s


def answer(history: list[dict[str, str]], question: str) -> dict[str, Any]:
    if not llm.status()["enabled"]:
        return {"needs_key": True, "message": llm.status()["message"]}
    messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM.format(today=date.today().isoformat()) + "\n" + llm.NO_ADVICE}]
    for m in history[-12:]:
        if m.get("role") in ("user", "assistant") and m.get("content"):
            messages.append({"role": m["role"], "content": str(m["content"])[:6000]})
    messages.append({"role": "user", "content": question})
    tool_log: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    as_of: dict[str, Any] = {}
    visuals: list[dict[str, Any]] = []
    tools = chat_tools.openai_tools()
    for _ in range(MAX_ROUNDS):
        try:
            resp = llm.create(messages, tools=tools, max_tokens=3000, effort="low")
        except (llm.AIUnavailable, llm.AIError) as e:
            return {"error": str(e), "tool_calls": tool_log}
        msg = resp.choices[0].message
        calls = getattr(msg, "tool_calls", None) or []
        if not calls:
            return {"answer": (msg.content or "").strip(), "tool_calls": tool_log, "sources": _dedupe(sources),
                    "as_of": as_of, "visuals": visuals, "model": llm.model(), "ai_generated": True}
        messages.append({"role": "assistant", "content": msg.content or "",
                         "tool_calls": [{"id": c.id, "type": "function", "function": {"name": c.function.name, "arguments": c.function.arguments}} for c in calls]})
        for c in calls:
            try:
                args = json.loads(c.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            result = chat_tools.call(c.function.name, args)
            tool_log.append({"name": c.function.name, "args": args, "ok": "error" not in result})
            sources += result.get("sources", []) or []
            if result.get("as_of"):
                as_of[f"{c.function.name}({', '.join(str(v) for v in args.values() if v)})"] = result["as_of"]
            if result.get("visual"):
                visuals.append(result["visual"])
                result = {k: v for k, v in result.items() if k != "visual"}  # the model doesn't need the raw series
            messages.append({"role": "tool", "tool_call_id": c.id, "content": _trim(result)})
    return {"answer": "I couldn't finish within the tool-call limit. Try a narrower question.", "tool_calls": tool_log,
            "sources": _dedupe(sources), "as_of": as_of, "visuals": visuals, "model": llm.model()}


def _dedupe(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen, out = set(), []
    for s in sources:
        k = (s.get("label"), s.get("url"))
        if k not in seen:
            seen.add(k)
            out.append(s)
    return out[:40]


EXAMPLES = [
    "Compare NVDA and AMD on margins and valuation",
    "Why did TSLA drop this week?",
    "Which of my watchlist stocks are above their 200-day MA?",
    "Should I buy MSFT now?",
    "What did members of Congress trade in NVDA recently?",
    "How have yields and the fed funds rate changed over the past year?",
]
