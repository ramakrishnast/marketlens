"""SQLite -> Postgres migration script (runs only when the suite targets Postgres)."""
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from app import db

pytestmark = pytest.mark.skipif(db.backend() != "postgres", reason="Postgres only")
BACKEND = Path(__file__).resolve().parent.parent


def _sqlite(path, name, tickers):
    c = sqlite3.connect(path)
    c.executescript(db.SCHEMA)
    c.execute("INSERT INTO watchlists(name, position) VALUES (?,1)", (name,))
    c.executemany("INSERT INTO watchlist_items VALUES (1,?, 't')", [(t,) for t in tickers])
    c.execute("INSERT INTO kv_cache VALUES ('k','\"v\"','2026-01-01T00:00:00+00:00','live')")
    c.commit()
    c.close()


def _run(path, *extra):
    env = {**os.environ, "DATABASE_URL": os.environ["DATABASE_URL"]}
    return subprocess.run([sys.executable, "-m", "scripts.migrate_sqlite_to_postgres", str(path), *extra],
                          cwd=BACKEND, env=env, capture_output=True, text=True)


def test_migrate_copy_rerun_conflict_and_truncate(fresh_db, tmp_path):
    a, b = tmp_path / "a.db", tmp_path / "b.db"
    _sqlite(a, "Alpha", ["AAPL", "MSFT"])
    _sqlite(b, "Beta", ["TSLA"])

    r = _run(a)
    assert r.returncode == 0, r.stderr
    assert [x["name"] for x in db.query("SELECT name FROM watchlists")] == ["Alpha"]
    assert db.cache_get("k")[0] == "v"

    assert _run(a).returncode == 0  # same data again: idempotent
    assert len(db.query("SELECT * FROM watchlist_items")) == 2

    r = _run(b)  # different data with a colliding id: refuse, change nothing
    assert r.returncode != 0 and "Conflict in watchlists" in (r.stdout + r.stderr)
    assert len(db.query("SELECT * FROM watchlist_items")) == 2

    assert _run(b, "--truncate").returncode == 0
    rows = db.query("SELECT w.name, i.ticker FROM watchlists w JOIN watchlist_items i ON i.watchlist_id=w.id")
    assert rows == [{"name": "Beta", "ticker": "TSLA"}]
    new_id = db.insert_id("INSERT INTO watchlists(name, position) VALUES (?,?)", ("Next", 2))
    assert new_id > 1  # sequence moved past the copied ids
