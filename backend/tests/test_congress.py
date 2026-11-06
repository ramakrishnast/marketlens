"""House PTR parsing and Congress performance math."""
import pandas as pd
import pytest

from app.providers.base import CongressFiling
from app.providers.congress import _clean_name, parse_ptr_text
from app.services import congress

PTR_TEXT = """Filing ID #20034018
ID Owner Asset Transaction
Type
Date Notification
Date
Amount Cap.
Gains >
$200?
Advanced Micro Devices, Inc. (AMD)
[ST]
P 02/03/2026 02/03/2026 $50,001 -
$100,000
F\x00\x00 S\x00\x00: New
S\x00\x00 O\x00: Morgan Stanley - E*TRADE #2
SP Alphabet Inc. - Class A Common
Stock (GOOGL) [ST]
S (partial) 02/03/2026 02/10/2026 $100,001 -
$250,000
F S: New
usdc [CT] P 12/26/2025 12/26/2025 $1,001 - $15,000
* For the complete list of asset type abbreviations, please visit https://fd.house.gov/reference/asset-type-codes.aspx.
"""

FILING = CongressFiling(doc_id="20034018", chamber="House", member="Jane Doe", state_dst="CA12", filing_date="2026-02-20",
                        url="https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/20034018.pdf")


def test_parse_ptr_rows():
    trades = parse_ptr_text(PTR_TEXT, FILING)
    assert len(trades) == 3
    amd, googl, usdc = trades
    assert (amd.ticker, amd.tx_type, amd.tx_date, amd.amount_low, amd.amount_high, amd.owner) == (
        "AMD", "buy", "2026-02-03", 50001.0, 100000.0, "self")
    assert (googl.ticker, googl.tx_type, googl.owner, googl.extra["partial"]) == ("GOOGL", "sell", "spouse", True)
    assert googl.disclosure_date == "2026-02-20"
    assert usdc.ticker is None and usdc.asset_type == "CT"


def test_clean_member_names():
    assert _clean_name("Scott Scott Franklin") == "Scott Franklin"
    assert _clean_name("Richard Dean Dr McCormick") == "Richard Dean McCormick"


def test_score_trade_date_vs_copy_on_disclosure():
    idx = pd.bdate_range("2026-01-05", periods=10)
    closes = pd.DataFrame({"XYZ": [10, 11, 12, 13, 14, 15, 16, 17, 18, 20.0], "SPY": [100.0] * 9 + [110.0]}, index=idx)
    opens = closes - 0.5
    buys = pd.DataFrame([{"id": "a", "member": "M", "ticker": "XYZ", "weight": 1000.0,
                          "tx_date": idx[0], "disclosure_date": idx[3]}])
    s = congress._score(buys, closes, opens).iloc[0]
    assert s["ret_tx"] == pytest.approx(20 / 10 - 1)  # close on trade date -> latest close
    assert s["entry_copy"] == pytest.approx(14 - 0.5)  # next session's OPEN after disclosure date
    assert s["ret_copy"] == pytest.approx(20 / 13.5 - 1)
    assert s["spy_tx"] == pytest.approx(0.10)


def test_weighted_average_uses_amount_midpoints():
    v = pd.Series([0.10, -0.20])
    w = pd.Series([8000.0, 2000.0])
    assert congress._wavg(v, w) == pytest.approx(0.04)


def test_leaderboard_and_member_api(client):
    b = client.get("/api/congress/leaderboard?period=1y").json()
    assert b["rows"] and b["all"]["priced_buys"] > 0 and "Hypothetical" in b["label"]
    ranked = [r for r in b["rows"] if r["ranked"]]
    assert ranked == sorted(ranked, key=lambda r: -r["estimated_return_pct"])
    m = client.get("/api/congress/member", params={"name": ranked[0]["member"], "period": "1y"}).json()
    assert m["found"] and m["trades"] and m["timeline"]
    for t in m["trades"]:
        assert t["url"].startswith("https://disclosures-clerk.house.gov/")
    nvda = client.get("/api/congress/ticker/NVDA").json()
    assert all(t["tx_type"] in ("buy", "sell", "exchange") for t in nvda["trades"])


CLEO_TEXT = """$200?
NVIDIA Corporation - Common Stock
(NVDA) [ST]
P 10/01/2026 10/01/2026 $1,001 - $15,000
F S: New
S O: Morgan Stanley - E*TRADE #2
NVIDIA Corporation - Common Stock
(NVDA) [ST]
P 10/01/2026 10/01/2026 $1,001 - $15,000
F S: New
S O: Morgan Stanley - E*TRADE - Fields Law Firm 2, LLC
NVIDIA Corporation - Common Stock
(NVDA) [ST]
P 10/01/2026 10/01/2026 $1,001 - $15,000
F S: New
S O: Morgan Stanley - E*TRADE IRA
Taiwan Semiconductor
Manufacturing Company Ltd. (TSM)
[ST]
P 10/09/2026 10/09/2026 $1,001 - $15,000
F S: New
* For the complete list of asset type abbreviations, please visit https://fd.house.gov/reference/asset-type-codes.aspx.
"""


def test_identical_looking_rows_keep_their_distinct_accounts():
    trades = parse_ptr_text(CLEO_TEXT, FILING)
    nvda = [t for t in trades if t.ticker == "NVDA"]
    assert len(nvda) == 3  # three real purchases, one per account
    assert [t.account for t in nvda] == ["Morgan Stanley - E*TRADE #2", "Morgan Stanley - E*TRADE - Fields Law Firm 2, LLC",
                                         "Morgan Stanley - E*TRADE IRA"]
    assert trades[-1].ticker == "TSM" and trades[-1].account == ""  # no account line -> empty, not carried over


def _row(doc, i, disclosed, account="", ticker="AAA"):
    return {"id": f"{doc}-{i}", "doc_id": doc, "member": "M", "ticker": ticker, "asset": "A Inc", "tx_type": "buy",
            "tx_date": "2026-03-02", "amount_low": 1001.0, "amount_high": 15000.0, "owner": "self", "account": account,
            "disclosure_date": disclosed}


def test_collapse_refilings_keeps_accounts_but_drops_reported_again():
    import pandas as pd

    # filing 1 has three identical-looking rows (3 accounts); filing 2 is an amendment repeating all three + one new trade
    rows = [_row("1", i, "2026-03-10", account=f"acct{i}") for i in range(3)]
    rows += [_row("2", i, "2026-03-20", account=f"acct{i}") for i in range(3)] + [_row("2", 9, "2026-03-20", ticker="BBB")]
    out = congress.collapse_refilings(pd.DataFrame(rows))
    assert len(out) == 4  # 3 accounts + the one new trade; the three repeats are gone
    assert set(out[out["ticker"] == "AAA"]["doc_id"]) == {"1"}  # the earliest disclosure is kept
    # same account-less rows inside ONE filing are never collapsed
    same = pd.DataFrame([_row("3", i, "2026-04-01") for i in range(3)])
    assert len(congress.collapse_refilings(same)) == 3
    # ... but if a re-filing repeats them, only the largest single-filing count survives
    both = pd.concat([same, pd.DataFrame([_row("4", i, "2026-04-09") for i in range(3)])])
    assert len(congress.collapse_refilings(both)) == 3


def test_account_column_added_to_an_older_database(tmp_path, monkeypatch):
    import sqlite3

    old = tmp_path / "marketlens.db"
    c = sqlite3.connect(old)
    c.execute("CREATE TABLE congress_trades (id TEXT PRIMARY KEY, doc_id TEXT, chamber TEXT, member TEXT, state_dst TEXT, owner TEXT, "
              "ticker TEXT, asset TEXT, asset_type TEXT, tx_type TEXT, tx_date TEXT, disclosure_date TEXT, amount_low REAL, "
              "amount_high REAL, url TEXT)")
    c.commit()
    c.close()
    from app import db
    from app.config import reset_settings

    if db.backend() == "postgres":
        pytest.skip("SQLite migration path")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    reset_settings()
    db.reset_connection()
    try:
        assert db.query("SELECT account FROM congress_trades") == []  # column exists after init
    finally:
        db.reset_connection()
        reset_settings()


def test_api_exposes_account(client):
    b = client.get("/api/congress/leaderboard?period=1y").json()
    m = client.get("/api/congress/member", params={"name": b["rows"][0]["member"], "period": "1y"}).json()
    assert all("account" in t for t in m["trades"])
    assert all("account" in t for t in client.get("/api/congress/ticker/NVDA").json()["trades"])
