"""Ask-AI chat and AI Research Desk endpoints."""
from __future__ import annotations

import threading
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from .. import db
from ..services import chat, llm, research_desk

router = APIRouter(prefix="/api")


class ChatIn(BaseModel):
    question: str
    history: list[dict[str, Any]] = []


@router.get("/chat/meta")
def meta():
    return {**llm.status(), "examples": chat.EXAMPLES}


@router.post("/chat")
def ask(body: ChatIn):
    return chat.answer(body.history, body.question.strip()[:4000])


_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()


@router.post("/ticker/{ticker}/research")
def start_research(ticker: str, body: dict | None = None):
    """Starts the multi-agent memo in the background (it makes ~10 model calls); poll GET for status."""
    t = ticker.upper()
    rounds = int((body or {}).get("rounds", 1))
    force = bool((body or {}).get("force"))
    if not llm.status()["enabled"]:
        return {"ticker": t, "needs_key": True, "message": llm.status()["message"]}
    with _jobs_lock:
        job = _jobs.get(t)
        if job and job["status"] == "running":
            return job
        job = {"ticker": t, "status": "running", "step": "starting", "started_at": db.now_iso()}
        _jobs[t] = job

    def work():
        try:
            res = research_desk.run(t, rounds=rounds, force=force, progress=lambda s: job.update(step=s))
            job.update(status="error" if res.get("error") else "done", result=res)
        except Exception as e:  # pragma: no cover
            job.update(status="error", result={"error": str(e)})

    threading.Thread(target=work, daemon=True).start()
    return job


@router.get("/ticker/{ticker}/research")
def get_research(ticker: str):
    t = ticker.upper()
    job = _jobs.get(t)
    if job:
        return job
    row = db.query_one("SELECT content FROM ai_summaries WHERE kind='desk' AND ticker=? ORDER BY created_at DESC LIMIT 1", (t,))
    if row:
        import json
        return {"ticker": t, "status": "done", "result": json.loads(row["content"])}
    return {"ticker": t, "status": "none", "ai_enabled": llm.status()["enabled"]}
