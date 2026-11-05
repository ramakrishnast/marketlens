"""Briefing, macro and status endpoints."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import db
from ..config import get_settings
from ..services import briefing, events, macro
from ..services.universe import MACRO_SERIES

router = APIRouter(prefix="/api")


@router.get("/briefings")
def list_briefings():
    s = get_settings()
    return {"items": briefing.history(), "schedule": {"time": s.briefing_time, "timezone": s.timezone, "enabled": s.scheduler_enabled},
            "email": bool(s.smtp_host and s.briefing_email_to)}


@router.get("/briefings/latest")
def latest():
    b = briefing.get()
    return b or {"none": True}


@router.get("/briefings/{bid}")
def one(bid: int):
    b = briefing.get(bid)
    if not b:
        raise HTTPException(404, "not found")
    return b


@router.post("/briefings/generate")
def generate(body: dict | None = None):
    return briefing.generate(send_email=bool((body or {}).get("email", False)))


@router.get("/macro")
def macro_summary():
    return {**macro.summary(), "events": events.macro_events(45)}


@router.get("/macro/{sid}")
def macro_history(sid: str, years: int = 10):
    if sid not in MACRO_SERIES:
        raise HTTPException(404, "unknown series")
    return macro.history(sid, years)


@router.get("/status/data")
def data_status():
    from ..scheduler import jobs

    rows = db.query("SELECT key, fetched_at, source, info FROM fetch_log ORDER BY fetched_at DESC")
    groups: dict[str, dict] = {}
    for r in rows:
        kind = r["key"].split(":")[0]
        g = groups.setdefault(kind, {"kind": kind, "count": 0, "latest": r["fetched_at"], "oldest": r["fetched_at"], "sources": {}})
        g["count"] += 1
        g["oldest"] = min(g["oldest"], r["fetched_at"])
        g["sources"][r["source"]] = g["sources"].get(r["source"], 0) + 1
    return {"groups": list(groups.values()), "jobs": jobs(), "db_path": str(get_settings().db_path)}


@router.post("/status/run/{job_id}")
def run_job(job_id: str):
    from ..scheduler import run_now

    if not run_now(job_id):
        raise HTTPException(404, "unknown job")
    return {"started": job_id}
