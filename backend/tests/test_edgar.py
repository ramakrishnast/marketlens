"""EDGAR company-facts parsing on a saved fixture (Apple, captured from data.sec.gov)."""
import pytest

from app.providers.fixtures import FixtureStore
from app.services import xbrl
from app.services.filing_summary import extract_sections


@pytest.fixture(scope="module")
def aapl():
    return xbrl.parse_company_facts(FixtureStore().company_facts("AAPL"))


def _row(rows, end):
    return next(r for r in rows if r["period_end"] == end)


def test_annual_values_match_10k(aapl):
    fy24 = _row(aapl["annual"], "2024-09-28")
    assert fy24["revenue"] == 391_035_000_000  # Apple FY2024 10-K
    assert fy24["net_income"] == 93_736_000_000
    assert fy24["eps_diluted"] == pytest.approx(6.08)
    assert fy24["gross_margin"] == pytest.approx(0.462, abs=0.001)


def test_q4_is_derived_from_full_year_minus_nine_months(aapl):
    q4 = _row(aapl["quarterly"], "2024-09-28")
    assert q4["revenue"] == 94_930_000_000  # FY24 Q4 per Apple's press release
    assert "revenue" in q4["derived"]
    assert q4["period_start"] == "2024-06-30"


def test_reported_quarter_is_not_derived(aapl):
    q1 = _row(aapl["quarterly"], "2024-12-28")
    assert q1["revenue"] == 124_300_000_000
    assert "revenue" not in q1["derived"]


def test_ytd_cash_flow_is_differenced_into_quarters(aapl):
    q = aapl["quarterly"]
    # every quarter with CFO should look like a quarter (no year-to-date cumulative values)
    cfo = [r["cfo"] for r in q if r.get("cfo") is not None]
    assert max(cfo) < 60e9  # Apple's quarterly CFO is ~20-55B; a YTD value would exceed this
    assert all(r.get("fcf") == pytest.approx(r["cfo"] - r["capex"]) for r in q if r.get("cfo") is not None and r.get("capex") is not None)


def test_ttm_sums_four_quarters(aapl):
    q = aapl["quarterly"]
    ttm = xbrl.ttm(q, "revenue")
    assert ttm == sum(r["revenue"] for r in [r for r in q if r.get("revenue")][-4:])


def test_trim_keeps_only_known_concepts():
    raw = {"cik": 1, "entityName": "X", "facts": {"us-gaap": {
        "Revenues": {"label": "R", "units": {"USD": [{"start": "2024-01-01", "end": "2024-03-31", "val": 10, "form": "10-Q", "filed": "2024-05-01"}]}},
        "SomethingElse": {"units": {"USD": []}}}}}
    t = xbrl.trim_company_facts(raw)
    assert list(t["facts"]["us-gaap"]) == ["Revenues"]
    parsed = xbrl.parse_company_facts(t)
    assert parsed["quarterly"][0]["revenue"] == 10


def test_submissions_fixture_has_earnings_8ks():
    from app.services.fundamentals import _trim_submissions

    sub = _trim_submissions(FixtureStore().submissions("AAPL"))
    assert any(f["form"] == "10-K" for f in sub["filings"])
    earnings = [f for f in sub["filings"] if f["form"] == "8-K" and "2.02" in f["items"]]
    assert len(earnings) >= 4
    assert all(f["url"].startswith("https://www.sec.gov/Archives/edgar/data/320193/") for f in sub["filings"][:5])


def test_section_extraction_skips_table_of_contents():
    toc = "Table of Contents\nItem 2. Management's Discussion and Analysis ... 20\nItem 1A. Risk Factors ... 30\n"
    body = "x " * 3000
    mdna = "Management's Discussion and Analysis of Financial Condition. " + "Revenue grew. " * 400
    risk = "Risk Factors. " + "Demand may fall. " * 300
    text = toc + body + mdna + risk
    out = extract_sections(text, max_chars=10000)
    assert "Revenue grew." in out and "Demand may fall." in out
    assert "... 20" not in out
