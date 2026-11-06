"""Copy an existing SQLite database (data/marketlens.db) into the configured PostgreSQL database.

    python -m scripts.migrate_sqlite_to_postgres [path/to/marketlens.db] [--truncate]

* Safe to re-run: rows that already exist are skipped (ON CONFLICT DO NOTHING). Use --truncate to start clean.
* Requires DATABASE_URL (or POSTGRES_HOST + POSTGRES_*) to point at Postgres.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db  # noqa: E402
from app.config import get_settings  # noqa: E402

BATCH = 5000
# integer ids backed by sequences (the other tables use text/natural keys). value = a column that identifies the row.
SERIAL = {"watchlists": "name", "briefings": "created_at", "ingest_runs": "started_at"}
ORDER = ["watchlists", "watchlist_items", "prices", "intraday", "fetch_log", "kv_cache", "news", "social_posts", "social_mentions",
         "ai_summaries", "briefings", "congress_filings", "congress_trades", "followed_members", "ingest_runs"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("sqlite_path", nargs="?", default=str(get_settings().db_path))
    ap.add_argument("--truncate", action="store_true", help="empty the Postgres tables first")
    a = ap.parse_args()
    if db.backend() != "postgres":
        sys.exit("Set DATABASE_URL (or POSTGRES_HOST) so the target is PostgreSQL.")
    if not Path(a.sqlite_path).exists():
        sys.exit(f"SQLite file not found: {a.sqlite_path}")
    src = sqlite3.connect(a.sqlite_path)
    src.row_factory = sqlite3.Row
    have = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    db.query("SELECT 1")  # creates the schema
    print(f"{a.sqlite_path} -> {db.describe()['target']}")
    if not a.truncate:
        # Serial ids from SQLite would collide with different rows already in Postgres (and watchlist_items would then
        # attach to the wrong list). Re-running the same migration is fine; mixing two different databases is not.
        for t, key in SERIAL.items():
            if t not in have:
                continue
            for r in src.execute(f"SELECT id, {key} AS k FROM {t}"):
                hit = db.query_one(f"SELECT {key} AS k FROM {t} WHERE id=?", (r["id"],))
                if hit and hit["k"] != r["k"]:
                    sys.exit(f"Conflict in {t}: Postgres already has id {r['id']} = {hit['k']!r} but SQLite has {r['k']!r}.\n"
                             "The target database already holds different data. Nothing was copied.\n"
                             "Migrate into an empty database, or re-run with --truncate to REPLACE the Postgres data.")
    if a.truncate:
        with db.tx() as c:
            for t in reversed(ORDER):
                c.execute(f"DELETE FROM {t}")
    for t in ORDER:
        if t not in have or t not in db.TABLES:
            continue
        cols = [c for c in db.TABLES[t]["cols"] if c in {r[1] for r in src.execute(f"PRAGMA table_info({t})")}]
        sql = f"INSERT OR IGNORE INTO {t}({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})"
        total = 0
        cur = src.execute(f"SELECT {', '.join(cols)} FROM {t}")
        while True:
            rows = cur.fetchmany(BATCH)
            if not rows:
                break
            with db.tx() as c:
                c.executemany(sql, [tuple(r) for r in rows])
            total += len(rows)
        if t in SERIAL:  # keep serial sequences ahead of the copied ids
            db.query(f"SELECT setval(pg_get_serial_sequence('{t}', 'id'), COALESCE((SELECT MAX(id) FROM {t}), 1))")
        print(f"  {t}: {total} rows")
    print("done")


if __name__ == "__main__":
    main()
