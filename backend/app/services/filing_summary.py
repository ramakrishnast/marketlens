"""AI summary of the latest 10-Q / 10-K: key changes, risks, guidance. Clearly labeled AI-generated."""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from .. import db
from ..providers import registry
from ..providers.base import ProviderError
from . import fundamentals, llm

log = logging.getLogger(__name__)

SECTION_PATTERNS = {
    "mdna": r"management[’'`s]{0,2}\s+discussion\s+and\s+analysis",
    "risk": r"risk\s+factors",
    "guidance": r"(outlook|guidance)",
}


def extract_sections(text: str, max_chars: int = 45000) -> str:
    """Pull MD&A and Risk Factors out of a filing's text, skipping the table of contents.

    The table of contents mentions every heading early; real sections are the occurrence followed by the
    longest stretch of prose, so we take the last occurrence that has substantial text after it.
    """
    lower = text.lower()
    parts = []
    for name, budget in (("mdna", int(max_chars * 0.7)), ("risk", int(max_chars * 0.3))):
        hits = [m.start() for m in re.finditer(SECTION_PATTERNS[name], lower)]
        best = None
        for h in hits:
            chunk = text[h:h + budget]
            if len(chunk) > 3000 and chunk.count(".") > 20:
                best = h
                if h > len(text) * 0.05:  # past the table of contents
                    break
        if best is not None:
            parts.append(f"=== {name.upper()} ===\n" + text[best:best + budget])
    if not parts:
        parts.append(text[:max_chars])
    return "\n\n".join(parts)


def _facts_context(ticker: str) -> str:
    try:
        f = fundamentals.facts(ticker)
    except Exception:
        return ""
    keep = ("period_end", "revenue", "gross_margin", "operating_margin", "net_income", "eps_diluted", "fcf", "total_debt", "cash")
    rows = [{k: r.get(k) for k in keep if r.get(k) is not None} for r in f["quarterly"][-5:]]
    return "XBRL figures from SEC company facts (last 5 quarters):\n" + json.dumps(rows, default=str)


SYSTEM = (
    "You summarize SEC filings for an investor doing their own research. Be factual, concise and neutral. "
    "Only use facts from the provided filing text and XBRL figures. If something is not in the text, say so."
)

PROMPT = """Summarize {ticker}'s {form} filed {date} for a reader who wants to know what changed.

Return markdown with these sections:
## Key changes vs the prior period
(4-6 bullets with specific numbers from the text or XBRL figures)
## Segment / business highlights
## Risks and new or changed risk factors
## Guidance and outlook statements
(quote management's forward-looking language briefly; if none, say "No explicit guidance in this filing.")
## Items to verify in the full filing

{facts}

FILING TEXT (excerpts):
{text}
"""


def latest_periodic(ticker: str) -> dict[str, Any] | None:
    fl = fundamentals.filings(ticker, forms=("10-Q", "10-K"), limit=5)["filings"]
    return fl[0] if fl else None


def summarize(ticker: str, accession: str | None = None, force: bool = False, generate: bool = True) -> dict[str, Any]:
    t = ticker.upper()
    filings = fundamentals.filings(t, forms=("10-Q", "10-K"), limit=10)["filings"]
    target = next((f for f in filings if f["accession"] == accession), None) if accession else (filings[0] if filings else None)
    if target is None:
        return {"ticker": t, "error": "No 10-Q/10-K found for this ticker."}
    key = f"filing:{target['accession']}"
    if not force:
        row = db.query_one("SELECT content, model, created_at FROM ai_summaries WHERE key=?", (key,))
        if row:
            return {"ticker": t, "filing": target, "summary": row["content"], "model": row["model"],
                    "created_at": row["created_at"], "ai_generated": True, "cached": True}
    if not generate:
        return {"ticker": t, "filing": target, "summary": None, "ai_enabled": llm.status()["enabled"]}
    if not llm.status()["enabled"]:
        return {"ticker": t, "filing": target, "needs_key": True, "message": llm.status()["message"]}
    if registry.mode() == "mock":
        return {"ticker": t, "filing": target,
                "error": "Filing documents are fetched live from SEC; switch DATA_MODE to auto or live to summarize."}
    try:
        text = registry.fundamentals().document_text(target["url"])
    except ProviderError as e:
        return {"ticker": t, "filing": target, "error": f"Could not fetch the filing from SEC: {e}"}
    prompt = PROMPT.format(ticker=t, form=target["form"], date=target["filing_date"], facts=_facts_context(t),
                           text=extract_sections(text))
    try:
        content = llm.complete(SYSTEM, prompt, max_tokens=2500, effort="low")
    except (llm.AIUnavailable, llm.AIError) as e:
        return {"ticker": t, "filing": target, "error": str(e)}
    with db.tx() as c:
        c.execute("INSERT OR REPLACE INTO ai_summaries(key, kind, ticker, content, model, created_at) VALUES (?,?,?,?,?,?)",
                  (key, "filing", t, content, llm.model(), db.now_iso()))
    return {"ticker": t, "filing": target, "summary": content, "model": llm.model(), "created_at": db.now_iso(),
            "ai_generated": True, "cached": False}
