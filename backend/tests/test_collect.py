"""Daily batch collection: every step runs, is recorded, is idempotent, and one failing source doesn't stop the rest."""
import pytest

from app import collect, db
from app.services import ingest, watchlists


@pytest.fixture()
def wl(fresh_db):
    watchlists.create("Demo", ["AAPL", "NVDA", "SPY"])


def _runs():
    return {r["job"]: r for r in ingest.recent(50)}


def test_daily_runs_every_step_and_records_it(wl):
    out = collect.run_daily()
    assert out["ok"] and [r["step"] for r in out["steps"]] == list(collect.STEPS)
    runs = _runs()
    assert runs["daily"]["status"] == "ok" and runs["daily"]["detail"]["note"] == "6/6 steps ok"
    for step in collect.STEPS:
        assert runs[f"daily:{step}"]["status"] == "ok", step
        assert runs[f"daily:{step}"]["detail"]["note"]
    assert runs["daily:prices"]["rows_added"] > 1000
    assert db.query_one("SELECT COUNT(*) AS n FROM news")["n"] > 0
    assert db.query_one("SELECT COUNT(*) AS n FROM congress_trades")["n"] > 0


def test_rerun_is_idempotent(wl):
    collect.run_daily(["prices", "news"])
    before = ingest.counts()
    out = collect.run_daily(["prices", "news"])
    assert out["ok"] and ingest.counts() == before  # upserts: nothing duplicated
    assert ingest.last("daily:prices")["rows_added"] == 0


def test_one_failing_source_does_not_stop_the_others(wl, monkeypatch):
    def boom():
        raise RuntimeError("source down")

    monkeypatch.setitem(collect.STEPS, "news", ("News", boom))
    out = collect.run_daily(["prices", "news", "macro"])
    assert not out["ok"]
    assert {r["step"]: r["status"] for r in out["steps"]} == {"prices": "ok", "news": "error", "macro": "ok"}
    runs = _runs()
    assert runs["daily:news"]["status"] == "error" and "source down" in runs["daily:news"]["error"]
    assert runs["daily"]["status"] == "partial" and "failed: news" in runs["daily"]["detail"]["note"]


def test_overlapping_batch_is_skipped(wl):
    db.insert_id("INSERT INTO ingest_runs(job, started_at, status) VALUES (?,?,?)", ("daily", db.now_iso(), "running"))
    out = collect.run_daily(["macro"])
    assert out.get("skipped") and out["steps"] == []
    assert ingest.last("daily:macro") is None


def test_cli_exit_codes(wl, monkeypatch, capsys):
    assert collect.main(["--list"]) == 0 and "prices" in capsys.readouterr().out
    assert collect.main(["--only", "nope"]) == 2
    assert collect.main(["--only", "macro"]) == 0
    monkeypatch.setitem(collect.STEPS, "macro", ("Macro", lambda: (_ for _ in ()).throw(RuntimeError("x"))))
    assert collect.main(["--only", "macro"]) == 1


def test_status_api_shows_daily_and_schedule_without_a_scheduler(client):
    client.post("/api/watchlists", json={"name": "W", "tickers": ["AAPL"]})
    collect.run_daily(["macro"])
    d = client.get("/api/status/data").json()
    assert d["daily"]["last"]["status"] == "ok" and d["daily"]["schedule"]["time"] == "18:30"
    steps = {s["step"]: s for s in d["daily"]["steps"]}
    assert steps["macro"]["last"]["status"] == "ok" and steps["prices"]["last"] is None
    jobs = {j["id"]: j for j in d["jobs"]}
    assert jobs["daily"]["next_run"]  # computed from the trigger even though no scheduler runs in the web process


def test_web_role_does_not_start_scheduler(fresh_db, monkeypatch):
    from app.config import get_settings, reset_settings

    monkeypatch.setenv("MARKETLENS_ROLE", "web")
    reset_settings()
    assert get_settings().role == "web"
    from app import scheduler

    assert all(j["runs_in"] == "worker container" for j in scheduler.jobs())
