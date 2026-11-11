"""Batch worker: runs the scheduler (daily collection, briefing, top-ups) without the web server.

    python -m app.worker

This is the `worker` service in docker-compose. Stop it with SIGTERM/Ctrl-C.
"""
from __future__ import annotations

import logging
import os
import signal
import sys
import threading


def main() -> int:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    log = logging.getLogger("marketlens.worker")
    from . import db
    from .config import get_settings
    from .scheduler import jobs, start_scheduler

    s = get_settings()
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    if not s.scheduler_enabled:
        log.warning("SCHEDULER_ENABLED=false: worker is idle (run jobs manually with `make collect`)")
        stop.wait()
        return 0
    log.info("worker starting: DATA_MODE=%s, db=%s", s.data_mode, db.describe()["target"])
    sched = start_scheduler()
    for j in jobs():
        log.info("  %-34s next run %s", j["name"], j["next_run"])
    stop.wait()
    log.info("worker stopping")
    sched.shutdown(wait=False)
    db.reset_connection()
    return 0


if __name__ == "__main__":
    sys.exit(main())
