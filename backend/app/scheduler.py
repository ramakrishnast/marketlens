"""Scheduled jobs (APScheduler), all in the configured TIMEZONE:

* daily collection: DAILY_COLLECT_DAYS at DAILY_COLLECT_TIME (default weekdays 18:30): every source, see app/collect.py
* daily briefing: weekdays at BRIEFING_TIME (default 07:30)
* intraday top-ups: prices every 30 min in market hours, news + crowd hourly, Congress filings at 07:15

In Docker the jobs run in the separate `worker` container (python -m app.worker); the web container only serves
pages. Natively (MARKETLENS_ROLE=all, the default) they run inside the web process.
"""
from __future__ import annotations

import logging
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import get_settings

log = logging.getLogger(__name__)
_sched: BackgroundScheduler | None = None


def _daily():
    from . import collect

    out = collect.run_daily()
    if not out["ok"]:
        log.error("daily collection finished with failed steps: %s", [r["step"] for r in out["steps"] if r["status"] == "error"])


def _safe(name: str, fn) -> Any:
    def run():
        from .services import ingest

        try:
            log.info("job %s starting", name)
            if name == "daily":  # run_daily records its own parent + per-step runs
                fn()
            elif ingest.is_running(name):
                log.warning("job %s is already running elsewhere; skipped", name)
                return
            else:
                with ingest.run(name):
                    fn()
            log.info("job %s done", name)
        except Exception as e:  # never let a job crash the scheduler
            log.exception("job %s failed: %s", name, e)
    return run


def _prices():
    from .services import prices, watchlists
    from .services.universe import MARKET_TICKERS, SECTOR_ETF_LIST
    prices.refresh(watchlists.all_tickers() + MARKET_TICKERS + SECTOR_ETF_LIST)


def _news():
    from .services import news, social, watchlists
    social.refresh_mentions()
    for t in watchlists.all_tickers():
        news.refresh(t)
        social.refresh_posts(t)


def _congress():
    from .services import congress
    congress.refresh()


def _briefing():
    from .services import briefing
    _prices()
    briefing.generate(send_email=True)


def _hm(value: str, default: tuple[int, int]) -> tuple[int, int]:
    try:
        h, m = (value.split(":") + ["0"])[:2]
        return int(h), int(m)
    except ValueError:
        return default


def job_specs() -> list[dict[str, Any]]:
    """Every scheduled job with its trigger. Used by the scheduler and by the Status page (even in the web container)."""
    s = get_settings()
    tz = s.timezone
    bh, bm = _hm(s.briefing_time, (7, 30))
    dh, dm = _hm(s.daily_collect_time, (18, 30))
    return [
        {"id": "daily", "name": "Daily collection (all sources)", "fn": _daily,
         "trigger": CronTrigger(day_of_week=s.daily_collect_days, hour=dh, minute=dm, timezone=tz)},
        {"id": "briefing", "name": "Daily briefing", "fn": _briefing,
         "trigger": CronTrigger(day_of_week="mon-fri", hour=bh, minute=bm, timezone=tz)},
        {"id": "prices", "name": "Price top-up (market hours)", "fn": _prices,
         "trigger": CronTrigger(day_of_week="mon-fri", hour="9-17", minute="*/30", timezone=tz)},
        {"id": "news", "name": "News + crowd top-up", "fn": _news, "trigger": CronTrigger(minute=5, timezone=tz)},
        {"id": "congress", "name": "Congress filings (morning)", "fn": _congress,
         "trigger": CronTrigger(day_of_week="mon-fri", hour=7, minute=15, timezone=tz)},
    ]


def start_scheduler() -> BackgroundScheduler:
    global _sched
    s = get_settings()
    try:
        from .services import ingest
        ingest.mark_stale_running()
    except Exception as e:  # pragma: no cover
        log.warning("could not reconcile previous runs: %s", e)
    sched = BackgroundScheduler(timezone=s.timezone, job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 3600})
    for j in job_specs():
        sched.add_job(_safe(j["id"], j["fn"]), j["trigger"], id=j["id"], name=j["name"])
    sched.start()
    _sched = sched
    log.info("scheduler started (%s): daily collection %s at %s, briefing weekdays %s", s.timezone, s.daily_collect_days,
             s.daily_collect_time, s.briefing_time)
    return sched


def jobs() -> list[dict[str, Any]]:
    """Schedule for the Status page. Next run times are computed from the triggers, so this also works in the web
    container, where the jobs themselves run in the worker."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    s = get_settings()
    now = datetime.now(ZoneInfo(s.timezone))
    where = "this process" if _sched is not None else ("worker container" if s.role == "web" else "not scheduled here")
    out = []
    for j in job_specs():
        nxt = j["trigger"].get_next_fire_time(None, now)
        out.append({"id": j["id"], "name": j["name"], "next_run": nxt.isoformat() if nxt else None, "runs_in": where,
                    "enabled": s.scheduler_enabled})
    return out


def run_now(job_id: str) -> bool:
    """Run a job immediately in a background thread of this process (works with or without the scheduler)."""
    fns = {j["id"]: j["fn"] for j in job_specs()}
    if job_id not in fns:
        return False
    import threading
    threading.Thread(target=_safe(job_id, fns[job_id]), daemon=True).start()
    return True
