"""Storage with two interchangeable backends behind one tiny API (tx / query / query_one / insert_id).

* PostgreSQL when DATABASE_URL (or POSTGRES_HOST + POSTGRES_*) is set: the Docker default, for durable
  incremental data collection (prices, news, crowd, Congress filings, AI outputs, briefings, run history).
* SQLite otherwise (one file, WAL): native development and the default test run.

Services write SQLite-flavoured SQL (`?` placeholders, INSERT OR REPLACE / IGNORE). For Postgres it is
translated automatically (`%s`, ON CONFLICT upserts, ILIKE), so there is a single set of queries.
"""
from __future__ import annotations

import atexit
import json
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Iterator

from .config import get_settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS prices (
    ticker TEXT NOT NULL, date TEXT NOT NULL,
    open REAL, high REAL, low REAL, close REAL, volume REAL,
    PRIMARY KEY (ticker, date)
);
CREATE TABLE IF NOT EXISTS intraday (
    ticker TEXT NOT NULL, ts TEXT NOT NULL,
    open REAL, high REAL, low REAL, close REAL, volume REAL,
    PRIMARY KEY (ticker, ts)
);
CREATE TABLE IF NOT EXISTS fetch_log (
    key TEXT PRIMARY KEY, fetched_at TEXT NOT NULL, source TEXT, info TEXT
);
CREATE TABLE IF NOT EXISTS kv_cache (
    key TEXT PRIMARY KEY, value TEXT NOT NULL, fetched_at TEXT NOT NULL, source TEXT
);
CREATE TABLE IF NOT EXISTS watchlists (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, position INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS watchlist_items (
    watchlist_id INTEGER NOT NULL REFERENCES watchlists(id) ON DELETE CASCADE,
    ticker TEXT NOT NULL, added_at TEXT NOT NULL,
    PRIMARY KEY (watchlist_id, ticker)
);
CREATE TABLE IF NOT EXISTS news (
    id TEXT PRIMARY KEY, ticker TEXT NOT NULL, headline TEXT NOT NULL, source TEXT, url TEXT,
    published_at TEXT, snippet TEXT, sentiment REAL, sentiment_label TEXT, scorer TEXT, provider TEXT
);
CREATE INDEX IF NOT EXISTS idx_news_ticker_time ON news(ticker, published_at);
CREATE TABLE IF NOT EXISTS social_posts (
    id TEXT PRIMARY KEY, ticker TEXT NOT NULL, platform TEXT NOT NULL, community TEXT, author TEXT,
    snippet TEXT, url TEXT, created_at TEXT, score INTEGER, user_label TEXT,
    sentiment REAL, sentiment_label TEXT
);
CREATE INDEX IF NOT EXISTS idx_social_ticker_time ON social_posts(ticker, created_at);
CREATE TABLE IF NOT EXISTS social_mentions (
    ticker TEXT NOT NULL, date TEXT NOT NULL, platform TEXT NOT NULL,
    mentions INTEGER, rank INTEGER, upvotes INTEGER, mentions_24h_ago INTEGER, rank_24h_ago INTEGER,
    PRIMARY KEY (ticker, date, platform)
);
CREATE TABLE IF NOT EXISTS ai_summaries (
    key TEXT PRIMARY KEY, kind TEXT, ticker TEXT, content TEXT, model TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS briefings (
    id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, for_date TEXT NOT NULL,
    content_md TEXT NOT NULL, facts TEXT NOT NULL, generator TEXT, emailed INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS congress_filings (
    doc_id TEXT PRIMARY KEY, chamber TEXT, member TEXT, state_dst TEXT, filing_date TEXT,
    url TEXT, status TEXT, n_trades INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS congress_trades (
    id TEXT PRIMARY KEY, doc_id TEXT, chamber TEXT, member TEXT, state_dst TEXT, owner TEXT,
    ticker TEXT, asset TEXT, asset_type TEXT, tx_type TEXT, tx_date TEXT, disclosure_date TEXT,
    amount_low REAL, amount_high REAL, url TEXT
);
CREATE INDEX IF NOT EXISTS idx_congress_member ON congress_trades(member);
CREATE INDEX IF NOT EXISTS idx_congress_ticker ON congress_trades(ticker);
CREATE TABLE IF NOT EXISTS followed_members (member TEXT PRIMARY KEY, added_at TEXT);
CREATE TABLE IF NOT EXISTS ingest_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, job TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
    status TEXT NOT NULL, rows_added INTEGER DEFAULT 0, detail TEXT, error TEXT
);
CREATE INDEX IF NOT EXISTS idx_ingest_runs_job ON ingest_runs(job, started_at);
"""

# ---- backend selection -------------------------------------------------------------------

def backend() -> str:
    return "postgres" if get_settings().database_url else "sqlite"


def describe() -> dict[str, Any]:
    s = get_settings()
    if backend() == "postgres":
        from psycopg.conninfo import conninfo_to_dict

        d = conninfo_to_dict(s.database_url)
        return {"backend": "postgres", "target": f"{d.get('host', 'localhost')}:{d.get('port', 5432)}/{d.get('dbname', '')}"}
    return {"backend": "sqlite", "target": str(s.db_path)}


# ---- schema metadata (parsed from SCHEMA) and SQL translation ------------------------------

def _split_top(body: str) -> list[str]:
    parts, depth, cur = [], 0, ""
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        parts.append(cur.strip())
    return parts


def _parse_tables() -> dict[str, dict[str, list[str]]]:
    out: dict[str, dict[str, list[str]]] = {}
    for m in re.finditer(r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\n?\);", SCHEMA, re.S):
        cols, pk = [], []
        for part in _split_top(m.group(2)):
            if part.upper().startswith("PRIMARY KEY"):
                pk = [c.strip() for c in part[part.index("(") + 1 : part.rindex(")")].split(",")]
            else:
                name = part.split()[0]
                cols.append(name)
                if "PRIMARY KEY" in part.upper():
                    pk = [name]
        out[m.group(1)] = {"cols": cols, "pk": pk}
    return out


TABLES = _parse_tables()
_INSERT_OR = re.compile(r"^\s*INSERT\s+OR\s+(REPLACE|IGNORE)\s+INTO\s+(\w+)\s*(\(([^)]*)\))?", re.I)


@lru_cache(maxsize=512)
def to_postgres(sql: str) -> str:
    """Translate the SQLite-flavoured SQL used by the services into PostgreSQL."""
    suffix = ""
    m = _INSERT_OR.match(sql)
    if m:
        mode, table = m.group(1).upper(), m.group(2)
        meta = TABLES[table]
        if mode == "IGNORE":
            suffix = " ON CONFLICT DO NOTHING"
        else:
            cols = [c.strip() for c in m.group(4).split(",")] if m.group(4) else meta["cols"]
            sets = [f"{c}=EXCLUDED.{c}" for c in cols if c not in meta["pk"]]
            suffix = (f" ON CONFLICT ({', '.join(meta['pk'])}) DO UPDATE SET {', '.join(sets)}" if sets else " ON CONFLICT DO NOTHING")
        sql = re.sub(r"^\s*INSERT\s+OR\s+(REPLACE|IGNORE)\s+INTO", "INSERT INTO", sql, flags=re.I)
    sql = sql.rstrip().rstrip(";") + suffix
    sql = sql.replace("%", "%%").replace("?", "%s")
    return re.sub(r"\bLIKE\b", "ILIKE", sql)  # SQLite LIKE is case-insensitive; Postgres LIKE is not


def schema_for_postgres() -> str:
    s = SCHEMA.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY")
    return re.sub(r"\bREAL\b", "DOUBLE PRECISION", s)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# ---- SQLite backend ------------------------------------------------------------------------

_local = threading.local()
_init_lock = threading.Lock()
_initialized: set[str] = set()


def _sqlite_conn() -> sqlite3.Connection:
    path = str(get_settings().db_path)
    conn = getattr(_local, "conn", None)
    if conn is None or getattr(_local, "path", None) != path:
        conn = sqlite3.connect(path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn, _local.path = conn, path
        with _init_lock:
            if path not in _initialized:
                conn.executescript(SCHEMA)
                conn.commit()
                _initialized.add(path)
    return conn


# ---- PostgreSQL backend --------------------------------------------------------------------

_pool = None
_pool_url: str | None = None
_pool_lock = threading.Lock()


class _PgConn:
    """Gives a psycopg connection the same execute/executemany surface the services use on sqlite3."""

    def __init__(self, conn) -> None:
        self._c = conn

    def execute(self, sql: str, params: tuple | list = ()):
        return self._c.execute(to_postgres(sql), params)

    def executemany(self, sql: str, rows) -> None:
        with self._c.cursor() as cur:
            cur.executemany(to_postgres(sql), list(rows))


def _pg_pool():
    global _pool, _pool_url
    url = get_settings().database_url
    with _pool_lock:
        if _pool is not None and _pool_url == url:
            return _pool
        if _pool is not None:
            _pool.close()
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool

        pool = ConnectionPool(url, min_size=1, max_size=12, open=False, check=ConnectionPool.check_connection,
                              kwargs={"row_factory": dict_row, "autocommit": True})
        deadline = time.monotonic() + 45
        while True:  # the database container may still be starting
            try:
                pool.open(wait=True, timeout=5)
                break
            except Exception:
                pool.close()
                if time.monotonic() > deadline:
                    raise
                time.sleep(1.5)
                pool = ConnectionPool(url, min_size=1, max_size=12, open=False, check=ConnectionPool.check_connection,
                                      kwargs={"row_factory": dict_row, "autocommit": True})
        _pool, _pool_url = pool, url
        with pool.connection() as conn:
            conn.execute("SELECT pg_advisory_lock(727001)")  # several processes may initialise at once
            try:
                for stmt in schema_for_postgres().split(";"):
                    if stmt.strip():
                        conn.execute(stmt)
            finally:
                conn.execute("SELECT pg_advisory_unlock(727001)")
        return pool


def reset_connection() -> None:
    global _pool, _pool_url
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
    _local.conn = None
    _initialized.clear()
    with _pool_lock:
        if _pool is not None:
            _pool.close()
        _pool, _pool_url = None, None


atexit.register(reset_connection)  # close the pool cleanly on interpreter exit


def wipe_postgres() -> None:
    """Test helper: drop everything and let the schema be recreated on next use."""
    with _pg_pool().connection() as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
    reset_connection()


# ---- common API ----------------------------------------------------------------------------

@contextmanager
def tx() -> Iterator[Any]:
    """One atomic transaction. Yields an object with execute() / executemany()."""
    if backend() == "postgres":
        with _pg_pool().connection() as conn:
            with conn.transaction():
                yield _PgConn(conn)
        return
    conn = _sqlite_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def query(sql: str, params: tuple | list = ()) -> list[dict[str, Any]]:
    if backend() == "postgres":
        with _pg_pool().connection() as conn:
            return [dict(r) for r in conn.execute(to_postgres(sql), params).fetchall()]
    return [dict(r) for r in _sqlite_conn().execute(sql, params).fetchall()]


def query_one(sql: str, params: tuple | list = ()) -> dict[str, Any] | None:
    rows = query(sql, params)
    return rows[0] if rows else None


def insert_id(sql: str, params: tuple | list = ()) -> int:
    """INSERT one row into a table with an integer `id` key and return that id."""
    if backend() == "postgres":
        with tx() as c:
            return int(c.execute(sql + " RETURNING id", params).fetchone()["id"])
    with tx() as c:
        return int(c.execute(sql, params).lastrowid)


# ---- generic JSON cache -------------------------------------------------------------------

def cache_get(key: str, max_age_minutes: float | None = None) -> tuple[Any, str, str] | None:
    """Return (value, fetched_at_iso, source) or None if missing/expired."""
    row = query_one("SELECT value, fetched_at, source FROM kv_cache WHERE key=?", (key,))
    if not row:
        return None
    if max_age_minutes is not None:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(row["fetched_at"])
        if age.total_seconds() > max_age_minutes * 60:
            return None
    return json.loads(row["value"]), row["fetched_at"], row["source"]


def cache_put(key: str, value: Any, source: str) -> str:
    ts = now_iso()
    with tx() as c:
        c.execute(
            "INSERT OR REPLACE INTO kv_cache(key, value, fetched_at, source) VALUES (?,?,?,?)",
            (key, json.dumps(value, default=str), ts, source),
        )
    return ts


def log_fetch(key: str, source: str, info: str = "") -> None:
    with tx() as c:
        c.execute(
            "INSERT OR REPLACE INTO fetch_log(key, fetched_at, source, info) VALUES (?,?,?,?)",
            (key, now_iso(), source, info),
        )


def last_fetch(key: str) -> dict[str, Any] | None:
    return query_one("SELECT * FROM fetch_log WHERE key=?", (key,))
