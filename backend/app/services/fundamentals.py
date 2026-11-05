"""Fundamentals, valuation ratios, peers and filings from SEC EDGAR (cached)."""
from __future__ import annotations

import logging
from typing import Any

from .. import db
from ..providers import registry
from ..providers.base import ProviderError
from . import prices, xbrl
from .universe import PEERS

log = logging.getLogger(__name__)

FACTS_TTL = 24 * 60
SUBS_TTL = 6 * 60
EDGAR_ARCHIVE = "https://www.sec.gov/Archives/edgar/data"


def _cached(key: str, ttl: float, live, sample) -> tuple[Any, str, str]:
    hit = db.cache_get(key, max_age_minutes=ttl)
    if hit:
        return hit
    try:
        val, source = registry.live_or_sample(live, sample, key)
    except Exception as e:
        stale = db.cache_get(key)
        if stale:
            return stale
        raise ProviderError(str(e)) from e
    ts = db.cache_put(key, val, source)
    return val, ts, source


def facts(ticker: str) -> dict[str, Any]:
    t = ticker.upper()
    val, ts, source = _cached(
        f"fund:{t}", FACTS_TTL,
        lambda: xbrl.parse_company_facts(xbrl.trim_company_facts(registry.fundamentals().company_facts(t))),
        lambda: xbrl.parse_company_facts(registry.fixtures().company_facts(t)),
    )
    return {**val, "as_of": {"fetched_at": ts, "source": source,
                             "last_period": val["quarterly"][-1]["period_end"] if val.get("quarterly") else None}}


def _trim_submissions(sub: dict[str, Any]) -> dict[str, Any]:
    recent = sub.get("filings", {}).get("recent", {})
    cik = int(sub.get("cik", 0))
    rows = []
    for i, form in enumerate(recent.get("form", [])):
        acc = recent["accessionNumber"][i]
        doc = recent.get("primaryDocument", [""] * (i + 1))[i]
        rows.append({
            "form": form, "filing_date": recent["filingDate"][i], "report_date": recent.get("reportDate", [""] * (i + 1))[i],
            "accession": acc, "items": (recent.get("items") or [""] * (i + 1))[i] or "",
            "description": (recent.get("primaryDocDescription") or [""] * (i + 1))[i] or "",
            "url": f"{EDGAR_ARCHIVE}/{cik}/{acc.replace('-', '')}/{doc}" if doc else
                   f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}",
            "index_url": f"{EDGAR_ARCHIVE}/{cik}/{acc.replace('-', '')}/",
        })
    return {"cik": cik, "name": sub.get("name"), "sic": sub.get("sic"), "sic_description": sub.get("sicDescription"),
            "fiscal_year_end": sub.get("fiscalYearEnd"), "exchanges": sub.get("exchanges"), "filings": rows}


def submissions(ticker: str) -> dict[str, Any]:
    t = ticker.upper()
    val, ts, source = _cached(
        f"subs:{t}", SUBS_TTL,
        lambda: _trim_submissions(registry.fundamentals().submissions(t)),
        lambda: _trim_submissions(registry.fixtures().submissions(t)),
    )
    return {**val, "as_of": {"fetched_at": ts, "source": source}}


def filings(ticker: str, forms: tuple[str, ...] = ("10-K", "10-Q", "8-K"), limit: int = 40) -> dict[str, Any]:
    sub = submissions(ticker)
    rows = [f for f in sub["filings"] if f["form"] in forms][:limit]
    return {"ticker": ticker.upper(), "name": sub["name"], "filings": rows, "as_of": sub["as_of"]}


def earnings_release_dates(ticker: str) -> list[str]:
    """Historical earnings release dates = 8-K filings with Item 2.02 (Results of Operations)."""
    sub = submissions(ticker)
    return sorted({f["filing_date"] for f in sub["filings"] if f["form"] == "8-K" and "2.02" in f["items"]})


def valuation(ticker: str, fund: dict[str, Any] | None = None) -> dict[str, Any]:
    t = ticker.upper()
    fund = fund or facts(t)
    q = fund["quarterly"]
    df = prices.daily(t)
    price = float(df["close"].iloc[-1]) if not df.empty else None
    shares = (fund.get("latest_shares_outstanding") or {}).get("value")
    if not shares and q:
        shares = next((r.get("diluted_shares") for r in reversed(q) if r.get("diluted_shares")), None)
    ttm = {m: xbrl.ttm(q, m) for m in ("revenue", "net_income", "eps_diluted", "ebitda", "fcf", "operating_income", "gross_profit")}
    prior = {m: xbrl.ttm_prior(q, m) for m in ("revenue", "eps_diluted", "net_income")}
    last_bs = next((r for r in reversed(q) if r.get("total_debt") is not None or r.get("cash") is not None), {})
    debt, cash = last_bs.get("total_debt"), last_bs.get("cash")
    mcap = price * shares if price and shares else None
    ev = mcap + (debt or 0) - (cash or 0) if mcap else None
    out: dict[str, Any] = {
        "ticker": t, "price": price, "price_date": df.index[-1].strftime("%Y-%m-%d") if not df.empty else None,
        "shares_outstanding": shares, "market_cap": mcap, "enterprise_value": ev, "total_debt": debt, "cash": cash,
        "ttm": ttm,
    }

    def ratio(a, b):
        return a / b if a is not None and b not in (None, 0) and b > 0 else None

    out["pe_ttm"] = ratio(price, ttm["eps_diluted"])
    out["ps_ttm"] = ratio(mcap, ttm["revenue"])
    out["ev_ebitda"] = ratio(ev, ttm["ebitda"])
    out["fcf_yield"] = (ttm["fcf"] / mcap) if ttm["fcf"] is not None and mcap else None
    g = {}
    for m in ("revenue", "eps_diluted", "net_income"):
        if ttm[m] is not None and prior[m] not in (None, 0) and prior[m] > 0:
            g[m] = (ttm[m] / prior[m] - 1) * 100
    out["growth_yoy_pct"] = g
    eg = g.get("eps_diluted")
    out["peg"] = out["pe_ttm"] / eg if out["pe_ttm"] and eg and eg > 0 else None
    for num, name in [("gross_profit", "gross_margin"), ("operating_income", "operating_margin"),
                      ("net_income", "net_margin"), ("fcf", "fcf_margin")]:
        out[name] = ttm[num] / ttm["revenue"] if ttm[num] is not None and ttm["revenue"] else None
    info = prices.info(t)
    out["forward_pe"] = info.get("forward_pe")
    out["forward_pe_source"] = "Yahoo Finance (analyst estimates)" if info.get("forward_pe") else None
    out["as_of"] = {**fund["as_of"], "price_date": out["price_date"]}
    return out


def peer_list(ticker: str) -> list[str]:
    t = ticker.upper()
    if t in PEERS:
        return PEERS[t]
    try:
        sic = submissions(t).get("sic")
    except ProviderError:
        return []
    peers = []
    for row in db.query("SELECT key, value FROM kv_cache WHERE key LIKE 'subs:%'"):
        other = row["key"].split(":", 1)[1]
        if other != t and f'"sic": "{sic}"' in row["value"]:
            peers.append(other)
    return peers[:6]


def peers(ticker: str) -> dict[str, Any]:
    t = ticker.upper()
    group = [t] + peer_list(t)
    prices.refresh(group)
    rows = []
    for p in group:
        try:
            v = valuation(p)
            rows.append({
                "ticker": p, "name": submissions(p).get("name"), "market_cap": v["market_cap"], "pe_ttm": v["pe_ttm"],
                "forward_pe": v["forward_pe"], "ps_ttm": v["ps_ttm"], "ev_ebitda": v["ev_ebitda"], "peg": v["peg"],
                "gross_margin": v["gross_margin"], "operating_margin": v["operating_margin"], "net_margin": v["net_margin"],
                "revenue_growth": v["growth_yoy_pct"].get("revenue"), "revenue_ttm": v["ttm"]["revenue"],
                "as_of": v["as_of"],
            })
        except Exception as e:
            rows.append({"ticker": p, "error": str(e)})
    basis = "curated peer group" if t in PEERS else "same SEC SIC code among locally cached companies"
    return {"ticker": t, "peers": rows, "basis": basis}


def warm(tickers: list[str]) -> None:
    for t in tickers:
        try:
            facts(t)
            submissions(t)
        except ProviderError as e:
            log.info("fundamentals warm %s: %s", t, e)
