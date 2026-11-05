"""End-to-end API checks on bundled sample data."""


def test_status_without_keys(client):
    s = client.get("/api/status").json()
    assert s["data_mode"] == "mock"
    assert s["ai_enabled"] is False


def test_watchlist_crud_and_table(client):
    wl = client.post("/api/watchlists", json={"name": "Test", "tickers": ["AAPL", "NVDA"]}).json()
    assert wl["tickers"] == ["AAPL", "NVDA"]
    client.post(f"/api/watchlists/{wl['id']}/items", json={"ticker": "msft"})
    client.delete(f"/api/watchlists/{wl['id']}/items/NVDA")
    table = client.get(f"/api/watchlists/{wl['id']}/table").json()
    rows = {r["ticker"]: r for r in table["rows"]}
    assert set(rows) == {"AAPL", "MSFT"}
    r = rows["AAPL"]
    for k in ("price", "change_pct", "volume_vs_avg", "range_52w_pos", "rsi14", "dist_sma50_pct", "dist_sma200_pct"):
        assert r[k] is not None, k
    assert r["as_of"]["source"] == "sample-data" and r["as_of"]["last_bar"]


def test_chart_payload(client):
    ch = client.get("/api/ticker/AAPL/chart?range=6m").json()
    assert len(ch["candles"]) == 126
    for k in ("sma50", "sma200", "rsi14", "macd", "bb_upper", "atr14"):
        assert ch["indicators"][k], k
    # long moving averages are computed on full history, so valid from the first visible bar
    assert ch["indicators"]["sma200"][0]["time"] == ch["candles"][0]["time"]
    assert ch["as_of"]["last_bar"]


def test_unknown_ticker_404(client):
    assert client.get("/api/ticker/ZZZZZZ/chart").status_code == 404


def test_search(client):
    res = client.get("/api/search?q=NVD").json()
    assert res and res[0]["ticker"] == "NVDA"


def test_market_overview(client):
    ov = client.get("/api/market/overview").json()
    assert {x["ticker"] for x in ov["indices"]} >= {"SPY", "QQQ", "^VIX"}
    assert len(ov["sectors"]) >= 10
