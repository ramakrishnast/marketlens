"""Background jobs (APScheduler), all in the configured TIMEZONE:

* daily briefing: weekdays at BRIEFING_TIME (default 07:30 America/New_York)
* watchlist + market prices: every 30 min, weekdays 09:00-17:00
* news + crowd for watchlist tickers: hourly
* House Congress filings: weekdays 18:15 and 07:00
"""
from __future__ import annotations

import logging
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import get_settings

log = logging.getLogger(__name__)
_sched: BackgroundScheduler | None = None


def _safe(name: str, fn) -> Any:
    def run():
        from .services import ingest

        try:
            log.info("job %s starting", name)
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


def start_scheduler() -> BackgroundScheduler:
    global _sched
    s = get_settings()
    tz = s.timezone
    hh, mm = (s.briefing_time.split(":") + ["0"])[:2]
    try:
        from .services import ingest
        ingest.mark_stale_running()
    except Exception as e:  # pragma: no cover
        log.warning("could not reconcile previous runs: %s", e)
    sched = BackgroundScheduler(timezone=tz, job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 3600})
    sched.add_job(_safe("briefing", _briefing), CronTrigger(day_of_week="mon-fri", hour=int(hh), minute=int(mm), timezone=tz), id="briefing", name="Daily briefing")
    sched.add_job(_safe("prices", _prices), CronTrigger(day_of_week="mon-fri", hour="9-17", minute="*/30", timezone=tz), id="prices", name="Price refresh")
    sched.add_job(_safe("news", _news), CronTrigger(minute=5, timezone=tz), id="news", name="News + crowd refresh")
    sched.add_job(_safe("congress", _congress), CronTrigger(day_of_week="mon-fri", hour="7,18", minute=15, timezone=tz), id="congress", name="Congress filings")
    sched.start()
    _sched = sched
    log.info("scheduler started (%s): briefing weekdays %s", tz, s.briefing_time)
    return sched


def jobs() -> list[dict[str, Any]]:
    if _sched is None:
        return []
    return [{"id": j.id, "name": j.name, "next_run": j.next_run_time.isoformat() if j.next_run_time else None} for j in _sched.get_jobs()]


def run_now(job_id: str) -> bool:
    fns = {"briefing": _briefing, "prices": _prices, "news": _news, "congress": _congress}
    if job_id not in fns:
        return False
    import threading
    threading.Thread(target=_safe(job_id, fns[job_id]), daemon=True).start()
    return True
