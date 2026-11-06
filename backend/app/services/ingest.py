"""Run history for incremental data collection.

Every scheduled collection job (prices, news + crowd, Congress filings, briefing) is recorded in `ingest_runs`
with its start/finish time, status and how many rows each table gained, so you can see what was collected,
when, and whether it failed.
"""
from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from typing import Any, Iterator

from .. import db

log = logging.getLogger(__name__)

# tables whose growth is reported per run
TRACKED = ("prices", "news", "social_posts", "social_mentions", "congress_filings", "congress_trades", "briefings", "ai_summaries")


def counts() -> dict[str, int]:
    out = {}
    for t in TRACKED:
        row = db.query_one(f"SELECT COUNT(*) AS n FROM {t}")
        out[t] = int(row["n"]) if row else 0
    return out


@contextmanager
def run(job: str) -> Iterator[None]:
    """Record one collection run: `with ingest.run("news"): ...`. Errors are recorded and re-raised."""
    before = counts()
    rid = db.insert_id("INSERT INTO ingest_runs(job, started_at, status) VALUES (?,?,?)", (job, db.now_iso(), "running"))
    status, error = "ok", None
    try:
        yield
    except Exception as e:
        status, error = "error", f"{type(e).__name__}: {e}"[:500]
        raise
    finally:
        after = counts()
        delta = {t: after[t] - before[t] for t in TRACKED if after[t] != before[t]}
        with db.tx() as c:
            c.execute("UPDATE ingest_runs SET finished_at=?, status=?, rows_added=?, detail=?, error=? WHERE id=?",
                      (db.now_iso(), status, sum(v for v in delta.values() if v > 0), json.dumps(delta), error, rid))


def recent(limit: int = 40) -> list[dict[str, Any]]:
    rows = db.query("SELECT * FROM ingest_runs ORDER BY id DESC LIMIT ?", (limit,))
    for r in rows:
        r["detail"] = json.loads(r["detail"]) if r.get("detail") else {}
    return rows


def mark_stale_running() -> int:
    """Runs left 'running' by a crash/restart are marked interrupted."""
    with db.tx() as c:
        cur = c.execute("UPDATE ingest_runs SET status='interrupted', finished_at=? WHERE status='running'", (db.now_iso(),))
        return getattr(cur, "rowcount", 0) or 0
