"""Parse SEC company facts (XBRL JSON) into clean quarterly + annual fundamentals.

Key details:
* Companies switch concepts over time (e.g. SalesRevenueNet -> RevenueFromContract...), so each
  metric has a priority list of concepts which are merged per period.
* 10-Qs report cash-flow items year-to-date and 10-Ks don't report Q4 at all. Missing quarters are
  derived by differencing cumulative periods that share a start date (e.g. Q4 = FY - 9M YTD).
  Derived EPS is an approximation (EPS isn't strictly additive) and is flagged as derived.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

# metric -> list of (taxonomy, concept, unit) in priority order
CONCEPTS: dict[str, list[tuple[str, str, str]]] = {
    "revenue": [
        ("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax", "USD"),
        ("us-gaap", "Revenues", "USD"),
        ("us-gaap", "SalesRevenueNet", "USD"),
        ("us-gaap", "RevenueFromContractWithCustomerIncludingAssessedTax", "USD"),
        ("us-gaap", "SalesRevenueGoodsNet", "USD"),
    ],
    "gross_profit": [("us-gaap", "GrossProfit", "USD")],
    "operating_income": [("us-gaap", "OperatingIncomeLoss", "USD")],
    "net_income": [("us-gaap", "NetIncomeLoss", "USD"), ("us-gaap", "ProfitLoss", "USD")],
    "eps_diluted": [("us-gaap", "EarningsPerShareDiluted", "USD/shares")],
    "eps_basic": [("us-gaap", "EarningsPerShareBasic", "USD/shares")],
    "cfo": [
        ("us-gaap", "NetCashProvidedByUsedInOperatingActivities", "USD"),
        ("us-gaap", "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations", "USD"),
    ],
    "capex": [
        ("us-gaap", "PaymentsToAcquirePropertyPlantAndEquipment", "USD"),
        ("us-gaap", "PaymentsToAcquireProductiveAssets", "USD"),
        ("us-gaap", "PaymentsToAcquirePropertyPlantAndEquipmentAndIntangibleAssets", "USD"),
    ],
    "d_and_a": [
        ("us-gaap", "DepreciationDepletionAndAmortization", "USD"),
        ("us-gaap", "DepreciationAndAmortization", "USD"),
        ("us-gaap", "DepreciationAmortizationAndAccretionNet", "USD"),
        ("us-gaap", "Depreciation", "USD"),
    ],
    "cash": [
        ("us-gaap", "CashAndCashEquivalentsAtCarryingValue", "USD"),
        ("us-gaap", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "USD"),
    ],
    "long_term_debt": [
        ("us-gaap", "LongTermDebtNoncurrent", "USD"),
        ("us-gaap", "LongTermDebt", "USD"),
        ("us-gaap", "LongTermDebtAndCapitalLeaseObligations", "USD"),
    ],
    "short_term_debt": [
        ("us-gaap", "LongTermDebtCurrent", "USD"),
        ("us-gaap", "DebtCurrent", "USD"),
        ("us-gaap", "ShortTermBorrowings", "USD"),
    ],
    "commercial_paper": [("us-gaap", "CommercialPaper", "USD")],
    "shares_outstanding": [
        ("dei", "EntityCommonStockSharesOutstanding", "shares"),
        ("us-gaap", "CommonStockSharesOutstanding", "shares"),
    ],
    "diluted_shares": [("us-gaap", "WeightedAverageNumberOfDilutedSharesOutstanding", "shares")],
}

INSTANT = {"cash", "long_term_debt", "short_term_debt", "commercial_paper", "shares_outstanding"}
NON_ADDITIVE = {"eps_diluted", "eps_basic", "diluted_shares"}


def _d(s: str) -> date:
    return date.fromisoformat(s)


def _days(start: str, end: str) -> int:
    return (_d(end) - _d(start)).days + 1


def trim_company_facts(facts: dict[str, Any]) -> dict[str, Any]:
    """Keep only the concepts we use (fixtures stay small)."""
    out: dict[str, Any] = {"cik": facts.get("cik"), "entityName": facts.get("entityName"), "facts": {}}
    for concepts in CONCEPTS.values():
        for tax, concept, unit in concepts:
            node = facts.get("facts", {}).get(tax, {}).get(concept)
            if node and unit in node.get("units", {}):
                out["facts"].setdefault(tax, {})[concept] = {"label": node.get("label"), "units": {unit: node["units"][unit]}}
    return out


def _raw_points(facts: dict[str, Any], metric: str) -> tuple[dict[tuple[str | None, str], float], dict]:
    """Merge concepts for a metric. Returns {(start, end): value} and provenance {(start,end): concept}."""
    merged: dict[tuple[str | None, str], float] = {}
    prov: dict[tuple[str | None, str], str] = {}
    for tax, concept, unit in CONCEPTS[metric]:
        rows = facts.get("facts", {}).get(tax, {}).get(concept, {}).get("units", {}).get(unit, [])
        best: dict[tuple[str | None, str], tuple[str, float]] = {}
        for r in rows:
            form = r.get("form", "")
            if not form.startswith(("10-K", "10-Q", "20-F", "40-F", "8-K")):
                continue
            key = (r.get("start"), r["end"])
            filed = r.get("filed", "")
            if key not in best or filed > best[key][0]:  # latest filing wins (restatements)
                best[key] = (filed, float(r["val"]))
        for key, (_, val) in best.items():
            if metric in INSTANT:
                # for instants, compare by end date only
                if any(k[1] == key[1] for k in merged):
                    continue
            elif key in merged:
                continue
            merged[key] = val
            prov[key] = concept
    return merged, prov


def _durations(points: dict[tuple[str | None, str], float]) -> tuple[dict, dict]:
    """Split duration points into quarterly (direct + derived) and annual dicts keyed by end date."""
    quarters: dict[str, dict[str, Any]] = {}
    annual: dict[str, dict[str, Any]] = {}
    spans = [(s, e, v) for (s, e), v in points.items() if s]
    for s, e, v in spans:
        n = _days(s, e)
        if 80 <= n <= 100:
            quarters[e] = {"start": s, "end": e, "value": v, "derived": False}
        elif 350 <= n <= 380:
            annual[e] = {"start": s, "end": e, "value": v, "derived": False}
    # derive missing quarters from cumulative periods with the same start
    by_start: dict[str, list[tuple[str, float]]] = {}
    for s, e, v in spans:
        by_start.setdefault(s, []).append((e, v))
    for s, items in by_start.items():
        items.sort()
        for i in range(len(items)):
            for j in range(i):
                e_long, v_long = items[i]
                e_short, v_short = items[j]
                gap = (_d(e_long) - _d(e_short)).days
                if 80 <= gap <= 100 and not _has_near(quarters, e_long):
                    qs = str(_d(e_short) + timedelta(days=1))
                    quarters[e_long] = {"start": qs, "end": e_long, "value": v_long - v_short, "derived": True}
    return quarters, annual


def _has_near(d: dict[str, Any], end: str, tol: int = 5) -> bool:
    de = _d(end)
    return any(abs((_d(k) - de).days) <= tol for k in d)


def _near(d: dict[str, Any], end: str, tol: int = 5) -> Any:
    de = _d(end)
    best = None
    for k, v in d.items():
        diff = abs((_d(k) - de).days)
        if diff <= tol and (best is None or diff < best[0]):
            best = (diff, v)
    return best[1] if best else None


def parse_company_facts(facts: dict[str, Any], max_quarters: int = 24, max_years: int = 12) -> dict[str, Any]:
    flows: dict[str, tuple[dict, dict]] = {}
    instants: dict[str, dict[str, float]] = {}
    for metric in CONCEPTS:
        pts, _ = _raw_points(facts, metric)
        if metric in INSTANT:
            instants[metric] = {e: v for (_, e), v in pts.items()}
        else:
            if metric in NON_ADDITIVE:
                # EPS / share counts: use direct quarters; derive Q4 only for EPS by difference (approximation)
                pass
            flows[metric] = _durations(pts)

    # period skeleton: quarters/years where we have revenue or net income
    anchor_q = {**flows["net_income"][0], **flows["revenue"][0]}
    anchor_a = {**flows["net_income"][1], **flows["revenue"][1]}

    def build(anchor: dict[str, Any], idx: int) -> list[dict[str, Any]]:
        rows = []
        for end in sorted(anchor):
            row: dict[str, Any] = {"period_end": end, "period_start": anchor[end]["start"], "derived": []}
            for metric, pair in flows.items():
                hit = _near(pair[idx], end)
                if hit is not None:
                    if metric == "diluted_shares" and hit["derived"]:
                        continue  # share counts can't be differenced
                    row[metric] = hit["value"]
                    if hit["derived"]:
                        row["derived"].append(metric)
            for metric, series in instants.items():
                hit = _near(series, end, tol=10 if metric == "shares_outstanding" else 5)
                if hit is not None:
                    row[metric] = hit
            _ratios(row)
            rows.append(row)
        return rows

    quarterly = build(anchor_q, 0)[-max_quarters:]
    annual = build(anchor_a, 1)[-max_years:]
    latest_shares = None
    if instants.get("shares_outstanding"):
        last_end = max(instants["shares_outstanding"])
        latest_shares = {"value": instants["shares_outstanding"][last_end], "as_of": last_end}
    return {
        "entity": facts.get("entityName"),
        "cik": facts.get("cik"),
        "quarterly": quarterly,
        "annual": annual,
        "latest_shares_outstanding": latest_shares,
    }


def _ratios(row: dict[str, Any]) -> None:
    rev = row.get("revenue")
    if row.get("cfo") is not None and row.get("capex") is not None:
        row["fcf"] = row["cfo"] - row["capex"]
    debt = sum(row.get(k) or 0 for k in ("long_term_debt", "short_term_debt", "commercial_paper"))
    if any(row.get(k) is not None for k in ("long_term_debt", "short_term_debt")):
        row["total_debt"] = debt
    if row.get("operating_income") is not None and row.get("d_and_a") is not None:
        row["ebitda"] = row["operating_income"] + row["d_and_a"]
    if rev:
        for num, name in [("gross_profit", "gross_margin"), ("operating_income", "operating_margin"),
                          ("net_income", "net_margin"), ("fcf", "fcf_margin")]:
            if row.get(num) is not None:
                row[name] = row[num] / rev


def ttm(quarterly: list[dict[str, Any]], metric: str) -> float | None:
    """Sum of the last 4 contiguous quarters."""
    rows = [r for r in quarterly if r.get(metric) is not None]
    if len(rows) < 4:
        return None
    last4 = rows[-4:]
    span = (_d(last4[-1]["period_end"]) - _d(last4[0]["period_end"])).days
    if span > 300:  # gap in the data
        return None
    return sum(r[metric] for r in last4)


def ttm_prior(quarterly: list[dict[str, Any]], metric: str) -> float | None:
    rows = [r for r in quarterly if r.get(metric) is not None]
    if len(rows) < 8:
        return None
    return ttm(rows[:-4], metric)
