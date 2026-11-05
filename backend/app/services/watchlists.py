from __future__ import annotations

from typing import Any

from .. import db


class NotFound(Exception):
    pass


def list_watchlists() -> list[dict[str, Any]]:
    wls = db.query("SELECT id, name, position FROM watchlists ORDER BY position, id")
    items = db.query("SELECT watchlist_id, ticker FROM watchlist_items ORDER BY added_at, ticker")
    by: dict[int, list[str]] = {}
    for it in items:
        by.setdefault(it["watchlist_id"], []).append(it["ticker"])
    return [{**w, "tickers": by.get(w["id"], [])} for w in wls]


def create(name: str, tickers: list[str] | None = None) -> dict[str, Any]:
    name = name.strip()[:60] or "Watchlist"
    with db.tx() as c:
        c.execute("INSERT OR IGNORE INTO watchlists(name, position) VALUES (?, (SELECT COALESCE(MAX(position),0)+1 FROM watchlists))", (name,))
    wl = db.query_one("SELECT id FROM watchlists WHERE name=?", (name,))
    assert wl
    for t in tickers or []:
        add_ticker(wl["id"], t)
    return get(wl["id"])


def get(wid: int) -> dict[str, Any]:
    for w in list_watchlists():
        if w["id"] == wid:
            return w
    raise NotFound(f"watchlist {wid} not found")


def rename(wid: int, name: str) -> dict[str, Any]:
    get(wid)
    with db.tx() as c:
        c.execute("UPDATE watchlists SET name=? WHERE id=?", (name.strip()[:60], wid))
    return get(wid)


def delete(wid: int) -> None:
    with db.tx() as c:
        c.execute("DELETE FROM watchlist_items WHERE watchlist_id=?", (wid,))
        c.execute("DELETE FROM watchlists WHERE id=?", (wid,))


def add_ticker(wid: int, ticker: str) -> dict[str, Any]:
    get(wid)
    t = ticker.strip().upper()
    if not t or len(t) > 10:
        raise ValueError("invalid ticker")
    with db.tx() as c:
        c.execute("INSERT OR IGNORE INTO watchlist_items(watchlist_id, ticker, added_at) VALUES (?,?,?)", (wid, t, db.now_iso()))
    return get(wid)


def remove_ticker(wid: int, ticker: str) -> dict[str, Any]:
    with db.tx() as c:
        c.execute("DELETE FROM watchlist_items WHERE watchlist_id=? AND ticker=?", (wid, ticker.upper()))
    return get(wid)


def all_tickers() -> list[str]:
    return [r["ticker"] for r in db.query("SELECT DISTINCT ticker FROM watchlist_items ORDER BY ticker")]


def ensure_default() -> None:
    if not db.query_one("SELECT id FROM watchlists LIMIT 1"):
        create("My Watchlist")
