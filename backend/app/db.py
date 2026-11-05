"""SQLite storage: one file, WAL mode, thread-local connections.

Everything the app shows is cached here so free (rate-limited) sources are hit as little as possible.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
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
"""

_local = threading.local()
_init_lock = threading.Lock()
_initialized: set[str] = set()


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def get_conn() -> sqlite3.Connection:
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


def reset_connection() -> None:
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
    _local.conn = None
    _initialized.clear()


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def query(sql: str, params: tuple | list = ()) -> list[dict[str, Any]]:
    return [dict(r) for r in get_conn().execute(sql, params).fetchall()]


def query_one(sql: str, params: tuple | list = ()) -> dict[str, Any] | None:
    r = get_conn().execute(sql, params).fetchone()
    return dict(r) if r else None


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
