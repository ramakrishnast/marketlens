"""Daily briefing facts, template render, scheduler jobs, macro endpoints."""
from app.services import briefing, watchlists


def test_briefing_template_is_sourced_and_has_no_advice(fresh_db):
    watchlists.create("Demo", ["AAPL", "NVDA", "MSFT", "SPY"])
    facts = briefing.build_facts()
    assert facts["indices"] and facts["movers"]
    md = briefing.render_template(facts)
    for section in ("## Market overview", "## Biggest movers", "## New technical signals", "## This week", "## New filings"):
        assert section in md
    assert "not investment advice" in md.lower()
    for m in facts["movers"]:
        for h in m["headlines"]:
            assert h["url"] in md or m["headlines"].index(h) >= 2
    low = md.lower()
    assert "you should buy" not in low and "strong buy" not in low


def test_generate_and_history_api(client):
    client.post("/api/watchlists", json={"name": "W", "tickers": ["AAPL"]})
    r = client.post("/api/briefings/generate", json={}).json()
    assert r["generator"] == "template"  # no key in tests
    hist = client.get("/api/briefings").json()
    assert hist["items"][0]["id"] == r["id"]
    doc = client.get(f"/api/briefings/{r['id']}").json()
    assert doc["content_md"].startswith("# Market briefing")


def test_scheduler_registers_jobs(fresh_db):
    from app import scheduler

    s = scheduler.start_scheduler()
    try:
        ids = {j["id"] for j in scheduler.jobs()}
        assert ids == {"daily", "briefing", "prices", "news", "congress"}
        assert "hour='18', minute='30'" in str(s.get_job("daily").trigger)
        b = s.get_job("briefing")
        assert "mon-fri" in str(b.trigger)
    finally:
        s.shutdown(wait=False)
        scheduler._sched = None


def test_macro_endpoints(client):
    m = client.get("/api/macro").json()
    ids = {s["id"] for s in m["series"]}
    assert {"DFF", "DGS10", "DGS2", "CPIAUCSL", "UNRATE"} <= ids
    cpi = next(s for s in m["series"] if s["id"] == "CPIAUCSL")
    assert 0 < cpi["yoy_pct"] < 15
    h = client.get("/api/macro/DGS10?years=1").json()
    assert h["points"]
