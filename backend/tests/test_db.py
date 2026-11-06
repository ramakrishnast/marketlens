"""Storage layer: SQL translation for Postgres, upsert semantics, run history. Runs on whichever backend is active."""
import pytest

from app import db
from app.services import ingest


def test_schema_metadata_parsed():
    assert db.TABLES["prices"]["pk"] == ["ticker", "date"]
    assert db.TABLES["kv_cache"]["pk"] == ["key"]
    assert db.TABLES["watchlist_items"]["pk"] == ["watchlist_id", "ticker"]
    assert "ingest_runs" in db.TABLES and db.TABLES["ingest_runs"]["pk"] == ["id"]


def test_translate_insert_or_replace_with_columns():
    sql = db.to_postgres("INSERT OR REPLACE INTO kv_cache(key, value, fetched_at, source) VALUES (?,?,?,?)")
    assert sql.startswith("INSERT INTO kv_cache(key, value, fetched_at, source) VALUES (%s,%s,%s,%s)")
    assert sql.endswith("ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, fetched_at=EXCLUDED.fetched_at, source=EXCLUDED.source")


def test_translate_insert_or_replace_without_column_list_uses_schema_columns():
    sql = db.to_postgres("INSERT OR REPLACE INTO prices VALUES (?,?,?,?,?,?,?)")
    assert "ON CONFLICT (ticker, date) DO UPDATE SET open=EXCLUDED.open" in sql and "volume=EXCLUDED.volume" in sql


def test_translate_ignore_like_and_percent():
    assert db.to_postgres("INSERT OR IGNORE INTO news(id) VALUES (?)").endswith("ON CONFLICT DO NOTHING")
    like = db.to_postgres("DELETE FROM kv_cache WHERE key LIKE 'congress:board:%'")
    assert "ILIKE 'congress:board:%%'" in like  # literal % escaped for psycopg
    assert db.to_postgres("SELECT 1 WHERE member LIKE ?") == "SELECT 1 WHERE member ILIKE %s"


def test_postgres_schema_types():
    s = db.schema_for_postgres()
    assert "BIGSERIAL PRIMARY KEY" in s and "AUTOINCREMENT" not in s
    assert " REAL" not in s and "DOUBLE PRECISION" in s


def test_every_service_sql_translates():
    """All INSERT OR ... statements in the code base reference known tables, so translation can't KeyError at runtime."""
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parent.parent / "app"
    found = 0
    for f in root.rglob("*.py"):
        for m in re.finditer(r'"(INSERT OR (?:REPLACE|IGNORE) INTO[^"]*)"', f.read_text()):
            db.to_postgres(m.group(1))
            found += 1
    assert found >= 10


def test_upsert_replace_and_ignore(fresh_db):
    db.cache_put("k", {"a": 1}, "live")
    db.cache_put("k", {"a": 2}, "sample-data")  # replace
    value, _, source = db.cache_get("k")
    assert value == {"a": 2} and source == "sample-data"
    with db.tx() as c:
        c.execute("INSERT OR IGNORE INTO followed_members(member, added_at) VALUES (?,?)", ("A", "t1"))
        c.execute("INSERT OR IGNORE INTO followed_members(member, added_at) VALUES (?,?)", ("A", "t2"))
    rows = db.query("SELECT * FROM followed_members")
    assert len(rows) == 1 and rows[0]["added_at"] == "t1"  # first write wins


def test_bulk_upsert_is_incremental(fresh_db):
    rows = [("AAA", f"2026-01-{d:02d}", 1.0, 2.0, 0.5, 1.5, 100.0) for d in range(1, 11)]
    with db.tx() as c:
        c.executemany("INSERT OR REPLACE INTO prices VALUES (?,?,?,?,?,?,?)", rows)
    more = rows[5:] + [("AAA", "2026-01-11", 9.0, 9.0, 9.0, 9.0, 9.0)]
    more[0] = ("AAA", rows[5][1], 5.0, 5.0, 5.0, 7.7, 5.0)  # revised bar
    with db.tx() as c:
        c.executemany("INSERT OR REPLACE INTO prices VALUES (?,?,?,?,?,?,?)", more)
    got = db.query("SELECT date, close FROM prices WHERE ticker=? ORDER BY date", ("AAA",))
    assert len(got) == 11 and got[5]["close"] == 7.7 and got[-1]["date"] == "2026-01-11"


def test_transaction_rolls_back_on_error(fresh_db):
    with pytest.raises(RuntimeError):
        with db.tx() as c:
            c.execute("INSERT INTO followed_members(member, added_at) VALUES (?,?)", ("X", "t"))
            raise RuntimeError("boom")
    assert db.query("SELECT * FROM followed_members") == []


def test_insert_id_and_like_case_insensitive(fresh_db):
    a = db.insert_id("INSERT INTO watchlists(name, position) VALUES (?,?)", ("One", 1))
    b = db.insert_id("INSERT INTO watchlists(name, position) VALUES (?,?)", ("Two", 2))
    assert b == a + 1
    assert [r["name"] for r in db.query("SELECT name FROM watchlists WHERE name LIKE ?", ("%ONE%",))] == ["One"]


def test_ingest_run_records_rows_added_and_errors(fresh_db):
    with ingest.run("unit"):
        with db.tx() as c:
            c.executemany("INSERT OR IGNORE INTO social_mentions(ticker, date, platform, mentions, rank) VALUES (?,?,?,?,?)",
                          [("AAPL", "2026-01-01", "p", 5, 1), ("MSFT", "2026-01-01", "p", 4, 2)])
    with pytest.raises(ValueError):
        with ingest.run("unit-fail"):
            raise ValueError("source down")
    runs = {r["job"]: r for r in ingest.recent()}
    assert runs["unit"]["status"] == "ok" and runs["unit"]["rows_added"] == 2
    assert runs["unit"]["detail"] == {"social_mentions": 2} and runs["unit"]["finished_at"]
    assert runs["unit-fail"]["status"] == "error" and "source down" in runs["unit-fail"]["error"]


def test_status_endpoint_reports_database_and_runs(client):
    with ingest.run("api-check"):
        pass
    d = client.get("/api/status/data").json()
    assert d["database"]["backend"] in ("sqlite", "postgres")
    assert any(r["job"] == "api-check" for r in d["runs"])
    assert "prices" in d["table_counts"]


@pytest.mark.skipif(db.backend() != "postgres", reason="Postgres only")
def test_postgres_specific(fresh_db):
    assert db.describe()["backend"] == "postgres"
    # the JSON cache round-trips unicode and large payloads
    big = {"x": "é" * 100000}
    db.cache_put("big", big, "live")
    assert db.cache_get("big")[0] == big
    # concurrent writers don't conflict (pool + upsert)
    import concurrent.futures as cf

    def w(i):
        db.cache_put("shared", {"i": i}, "live")
        return db.log_fetch(f"k{i}", "live", "")

    with cf.ThreadPoolExecutor(8) as ex:
        list(ex.map(w, range(40)))
    assert len(db.query("SELECT key FROM fetch_log")) == 40
