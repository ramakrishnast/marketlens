"""Congressional trades (STOCK Act PTRs): leaderboard, member performance vs the S&P 500, copy-on-disclosure.

Definitions (all hypothetical, amount-weighted by the midpoint of each disclosed range):
* Estimated return: purchases measured from the close on the TRADE date to the latest close.
* Copy-trading return: the same purchases bought at the next OPEN after the DISCLOSURE date (when the
  public could first act), to the latest close.
* Each is compared to SPY over the identical holding windows with identical weights.
* Win rate vs S&P: share of purchases that beat SPY over the same window (by trade date).
Only purchases of tickers with price data count. Sells are listed but not scored.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd

from .. import db
from ..config import FIXTURES_DIR
from ..providers import registry
from ..providers.base import CongressTrade
from . import prices

log = logging.getLogger(__name__)
_lock = threading.Lock()
PERIODS = {"30d": 30, "90d": 90, "6m": 182, "1y": 365, "all": 3650}
LABEL = ("Hypothetical: assumes you bought each disclosed purchase (amount-weighted by the midpoint of the disclosed "
         "range) and held to today. Disclosures can lag trades by up to 45 days. Past performance does not predict "
         "future results.")
MAX_PDFS_PER_REFRESH = 80


def _tid(t: CongressTrade, n: int) -> str:
    return hashlib.sha1(f"{t.doc_id}|{n}|{t.ticker}|{t.tx_date}|{t.tx_type}|{t.amount_low}".encode()).hexdigest()[:20]


def _store(filings, trades) -> None:
    with db.tx() as c:
        c.executemany("INSERT OR IGNORE INTO congress_filings(doc_id, chamber, member, state_dst, filing_date, url, status, n_trades) "
                      "VALUES (?,?,?,?,?,?,?,?)",
                      [(f.doc_id, f.chamber, clean_name(f.member), f.state_dst, f.filing_date, f.url,
                        "parsed" if any(t.doc_id == f.doc_id for t in trades) else "no-text-or-no-trades",
                        sum(1 for t in trades if t.doc_id == f.doc_id)) for f in filings])
        counters: dict[str, int] = {}
        rows = []
        for t in trades:
            n = counters.get(t.doc_id, 0)
            counters[t.doc_id] = n + 1
            rows.append((_tid(t, n), t.doc_id, t.chamber, clean_name(t.member), t.state_dst, t.owner, t.ticker, t.asset, t.asset_type,
                         t.tx_type, t.tx_date, t.disclosure_date, t.amount_low, t.amount_high, t.url))
        c.executemany("INSERT OR IGNORE INTO congress_trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)


def ensure_seeded() -> None:
    """Load bundled sample filings (and their prices) on first use, so pages work instantly."""
    if db.query_one("SELECT 1 AS x FROM congress_filings LIMIT 1"):
        return
    with _lock:
        if db.query_one("SELECT 1 AS x FROM congress_filings LIMIT 1"):
            return
        filings, trades = registry.fixtures().congress()
        _store(filings, trades)
        db.log_fetch("congress:house", registry.SAMPLE, f"seeded {len(filings)} filings")
        # seed prices for traded tickers that aren't cached yet
        tickers = sorted({t.ticker for t in trades if t.ticker} | {"SPY"})
        have = {r["ticker"] for r in db.query("SELECT DISTINCT ticker FROM prices")}
        missing = [t for t in tickers if t not in have]
        data = registry.fixtures().daily(missing)
        rows, logs, now = [], [], db.now_iso()
        for t, df in data.items():
            d = df.reset_index()
            rows += list(zip([t] * len(d), d["date"].dt.strftime("%Y-%m-%d"), d["open"], d["high"], d["low"], d["close"],
                             d["volume"].fillna(0)))
            logs.append((f"daily:{t}", now, registry.SAMPLE, "congress seed"))
        with db.tx() as c:  # one transaction: ~500k rows insert in a couple of seconds
            c.executemany("INSERT OR IGNORE INTO prices VALUES (?,?,?,?,?,?,?)", rows)
            c.executemany("INSERT OR IGNORE INTO fetch_log(key, fetched_at, source, info) VALUES (?,?,?,?)", logs)
        log.info("congress: seeded %d filings, %d trades, prices for %d tickers", len(filings), len(trades), len(data))


def refresh(max_pdfs: int = MAX_PDFS_PER_REFRESH) -> dict[str, Any]:
    """Fetch new House PTR filings (incremental: only DocIDs we haven't seen)."""
    ensure_seeded()
    if registry.mode() == "mock":
        return {"new_filings": 0, "note": "mock mode: using bundled filings"}
    year = date.today().year
    known = {r["doc_id"] for r in db.query("SELECT doc_id FROM congress_filings")}
    new_f, new_t = [], []
    try:
        filings = registry.congress().filings(year)
        if date.today().month == 1:
            filings += registry.congress().filings(year - 1)
    except Exception as e:
        log.warning("House index fetch failed: %s", e)
        return {"error": str(e)}
    todo = sorted([f for f in filings if f.doc_id not in known], key=lambda f: f.filing_date, reverse=True)[:max_pdfs]
    for f in todo:
        try:
            ts = registry.congress().trades(f)
        except Exception as e:
            log.info("PTR %s not parsed: %s", f.doc_id, e)
            ts = []
        new_f.append(f)
        new_t += ts
    _store(new_f, new_t)
    db.log_fetch("congress:house", "live", f"{len(new_f)} new filings")
    tickers = sorted({t.ticker for t in new_t if t.ticker})
    if tickers:
        prices.refresh(tickers)
    with db.tx() as c:
        c.execute("DELETE FROM kv_cache WHERE key LIKE 'congress:board:%'")
    return {"new_filings": len(new_f), "new_trades": len(new_t), "remaining": max(0, len([f for f in filings if f.doc_id not in known]) - len(todo))}


# ---- parties --------------------------------------------------------------------------------

def _legislators() -> dict[str, dict[str, Any]]:
    hit = db.cache_get("congress:legislators", max_age_minutes=7 * 24 * 60)
    if hit:
        return hit[0]
    data: dict[str, dict[str, Any]] = {}
    if registry.mode() != "mock":
        try:
            import httpx
            r = httpx.get("https://unitedstates.github.io/congress-legislators/legislators-current.json", timeout=30)
            r.raise_for_status()
            for x in r.json():
                t = x["terms"][-1]
                key = f"{t['state']}{int(t.get('district') or 0):02d}" if t["type"] == "rep" else f"{t['state']}-SEN-{x['name']['last']}"
                data[key] = {"name": x["name"].get("official_full"), "last": x["name"]["last"], "party": t["party"],
                             "chamber": "House" if t["type"] == "rep" else "Senate", "bioguide": x["id"]["bioguide"]}
        except Exception as e:
            log.info("legislators fetch failed: %s", e)
    if not data:
        data = json.loads((FIXTURES_DIR / "congress" / "legislators.json").read_text())
    db.cache_put("congress:legislators", data, "live" if registry.mode() != "mock" else registry.SAMPLE)
    return data


def party_of(member: str, state_dst: str) -> str | None:
    """Match on district + last name (districts get redrawn, so a district alone isn't enough)."""
    leg = _legislators()
    m = member.lower()
    rec = leg.get(state_dst)
    if rec and rec["last"].split()[-1].lower() in m:
        return rec["party"]
    state = state_dst[:2]
    for key, r in leg.items():
        if key.startswith(state) and r["chamber"] == "House" and r["last"].split()[-1].lower() in m.split():
            return r["party"]
    return None


_TITLES = {"dr", "dr.", "mr", "mr.", "mrs", "mrs.", "ms", "ms.", "hon", "hon."}


def clean_name(name: str) -> str:
    """'Scott Scott Franklin' -> 'Scott Franklin', 'Richard Dean Dr McCormick' -> 'Richard Dean McCormick'."""
    out: list[str] = []
    for tok in name.split():
        if tok.lower() in _TITLES or (out and out[-1].lower() == tok.lower()):
            continue
        out.append(tok)
    return " ".join(out)


# ---- analytics ------------------------------------------------------------------------------

def _trades_df(since: str | None = None, member: str | None = None, ticker: str | None = None) -> pd.DataFrame:
    ensure_seeded()
    sql = "SELECT * FROM congress_trades WHERE 1=1"
    p: list[Any] = []
    if since:
        sql += " AND tx_date>=?"
        p.append(since)
    if member:
        sql += " AND member=?"
        p.append(member)
    if ticker:
        sql += " AND ticker=?"
        p.append(ticker.upper())
    df = pd.DataFrame(db.query(sql, p))
    if df.empty:
        return df
    df["weight"] = df[["amount_low", "amount_high"]].mean(axis=1, skipna=True).fillna(df["amount_low"]).fillna(1000.0)
    df["tx_date"] = pd.to_datetime(df["tx_date"])
    df["disclosure_date"] = pd.to_datetime(df["disclosure_date"])
    return df


def _price_matrix(tickers: list[str], start: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    if not tickers:
        return pd.DataFrame(), pd.DataFrame()
    marks = ",".join("?" * len(tickers))
    rows = db.query(f"SELECT ticker, date, open, close FROM prices WHERE date>=? AND ticker IN ({marks})", [start, *tickers])
    if not rows:
        return pd.DataFrame(), pd.DataFrame()
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    closes = df.pivot_table(index="date", columns="ticker", values="close").sort_index()
    opens = df.pivot_table(index="date", columns="ticker", values="open").sort_index()
    return closes, opens


def _score(buys: pd.DataFrame, closes: pd.DataFrame, opens: pd.DataFrame) -> pd.DataFrame:
    """Per-purchase returns (trade-date and copy-on-disclosure) and matching SPY returns."""
    if buys.empty or closes.empty or "SPY" not in closes:
        return buys.iloc[0:0]
    idx = closes.index
    last = closes.ffill().iloc[-1]
    out = []
    for r in buys.itertuples():
        t = r.ticker
        if t not in closes.columns:
            continue
        col = closes[t]
        # entry on trade date: close of the trade date (or next session)
        i_tx = idx.searchsorted(r.tx_date)
        # copy entry: next session's open AFTER the disclosure date
        i_cp = idx.searchsorted(r.disclosure_date, side="right")
        if i_tx >= len(idx) or pd.isna(col.iloc[i_tx]) or pd.isna(last[t]):
            continue
        e_tx = float(col.iloc[i_tx])
        spy_tx = float(closes["SPY"].iloc[i_tx])
        spy_last = float(last["SPY"])
        row = {"id": r.id, "member": r.member, "ticker": t, "weight": r.weight, "tx_date": r.tx_date, "disclosure_date": r.disclosure_date,
               "ret_tx": float(last[t]) / e_tx - 1, "spy_tx": spy_last / spy_tx - 1, "entry_tx": e_tx,
               "ret_copy": None, "spy_copy": None, "entry_copy": None, "entry_copy_date": None}
        if i_cp < len(idx) and t in opens and pd.notna(opens[t].iloc[i_cp]) and pd.notna(opens["SPY"].iloc[i_cp]):
            e_cp = float(opens[t].iloc[i_cp])
            row.update(ret_copy=float(last[t]) / e_cp - 1, spy_copy=spy_last / float(opens["SPY"].iloc[i_cp]) - 1,
                       entry_copy=e_cp, entry_copy_date=idx[i_cp])
        out.append(row)
    return pd.DataFrame(out)


def _wavg(v: pd.Series, w: pd.Series) -> float | None:
    m = v.notna()
    if not m.any() or w[m].sum() <= 0:
        return None
    return float((v[m] * w[m]).sum() / w[m].sum())


def leaderboard(period: str = "6m", min_trades: int = 3, party: str | None = None, rank_by: str = "estimated") -> dict[str, Any]:
    days = PERIODS.get(period, 182)
    key = f"congress:board:{period}:{min_trades}:{rank_by}"
    hit = db.cache_get(key, max_age_minutes=360)
    if hit:
        board = hit[0]
    else:
        board = _compute_board(days, min_trades, rank_by)
        db.cache_put(key, board, "computed")
    rows = board["rows"]
    if party:
        rows = [r for r in rows if (r.get("party") or "").lower().startswith(party.lower())]
    return {**board, "rows": rows, "period": period}


def _compute_board(days: int, min_trades: int, rank_by: str) -> dict[str, Any]:
    start = (date.today() - timedelta(days=days)).isoformat()
    df = _trades_df(since=start)
    if df.empty:
        return {"rows": [], "headline": None, "sp500": None, "stats": {}, "label": LABEL}
    buys = df[(df["tx_type"] == "buy") & df["ticker"].notna()]
    tickers = sorted(set(buys["ticker"]) | {"SPY"})
    closes, opens = _price_matrix(tickers, (pd.Timestamp(start) - pd.Timedelta(days=7)).strftime("%Y-%m-%d"))
    scored = _score(buys, closes, opens)
    rows = []
    for member, g in df.groupby("member"):
        s = scored[scored["member"] == member] if not scored.empty else scored
        first = g.iloc[0]
        delay = (g["disclosure_date"] - g["tx_date"]).dt.days
        row = {
            "member": member, "chamber": first["chamber"], "state_dst": first["state_dst"],
            "party": party_of(member, first["state_dst"]), "trades": int(len(g)),
            "buys": int((g["tx_type"] == "buy").sum()), "sells": int((g["tx_type"] == "sell").sum()),
            "priced_buys": int(len(s)), "avg_filing_delay_days": float(delay.mean()) if len(delay) else None,
            "money_low": float(g["amount_low"].fillna(0).sum()), "money_high": float(g["amount_high"].fillna(g["amount_low"]).fillna(0).sum()),
            "last_trade": g["tx_date"].max().strftime("%Y-%m-%d"),
        }
        if len(s):
            row["estimated_return_pct"] = _pct(_wavg(s["ret_tx"], s["weight"]))
            row["estimated_spy_pct"] = _pct(_wavg(s["spy_tx"], s["weight"]))
            row["copy_return_pct"] = _pct(_wavg(s["ret_copy"], s["weight"]))
            row["copy_spy_pct"] = _pct(_wavg(s["spy_copy"], s["weight"]))
            row["win_rate_vs_spy_pct"] = float(((s["ret_tx"] > s["spy_tx"]).mean()) * 100)
            for k in ("estimated", "copy"):
                a, b = row.get(f"{k}_return_pct"), row.get(f"{k}_spy_pct")
                row[f"{k}_excess_pct"] = (a - b) if a is not None and b is not None else None
        row["ranked"] = row["priced_buys"] >= min_trades
        rows.append(row)
    sort_key = "copy_return_pct" if rank_by == "copy" else "estimated_return_pct"
    rows.sort(key=lambda r: (not r["ranked"], -(r.get(sort_key) if r.get(sort_key) is not None else -1e9)))
    all_row = None
    if not scored.empty:
        all_row = {"member": "All Congress", "trades": int(len(df)), "members": int(df["member"].nunique()),
                   "priced_buys": int(len(scored)),
                   "estimated_return_pct": _pct(_wavg(scored["ret_tx"], scored["weight"])),
                   "estimated_spy_pct": _pct(_wavg(scored["spy_tx"], scored["weight"])),
                   "copy_return_pct": _pct(_wavg(scored["ret_copy"], scored["weight"])),
                   "copy_spy_pct": _pct(_wavg(scored["spy_copy"], scored["weight"])),
                   "win_rate_vs_spy_pct": float((scored["ret_tx"] > scored["spy_tx"]).mean() * 100),
                   "avg_filing_delay_days": float((df["disclosure_date"] - df["tx_date"]).dt.days.mean()),
                   "money_low": float(df["amount_low"].fillna(0).sum()),
                   "money_high": float(df["amount_high"].fillna(df["amount_low"]).fillna(0).sum())}
    sp = None
    if not closes.empty and "SPY" in closes:
        spy = closes["SPY"].dropna()
        spy = spy[spy.index >= pd.Timestamp(start)]
        if len(spy) > 1:
            sp = _pct(float(spy.iloc[-1] / spy.iloc[0] - 1))
    ranked = [r for r in rows if r["ranked"] and r.get("copy_excess_pct") is not None]
    headline = None
    if ranked:
        best = max(ranked, key=lambda r: r["copy_excess_pct"])
        headline = {"member": best["member"], "trades": best["trades"], "copy_excess_pct": best["copy_excess_pct"],
                    "year": date.today().year}
    as_of = db.last_fetch("congress:house") or {}
    last_price = closes.index[-1].strftime("%Y-%m-%d") if not closes.empty else None
    return {"rows": rows, "all": all_row, "sp500_pct": sp, "headline": headline, "start": start, "label": LABEL,
            "stats": {"members_traded": int(df["member"].nunique()), "members_ranked": sum(1 for r in rows if r["ranked"]),
                      "min_trades": min_trades},
            "as_of": {"filings_fetched_at": as_of.get("fetched_at"), "source": as_of.get("source"), "prices_through": last_price,
                      "latest_filing": (db.query_one("SELECT MAX(filing_date) AS d FROM congress_filings") or {}).get("d")}}


def _pct(v: float | None) -> float | None:
    return None if v is None else v * 100


def performance(members: list[str], period: str = "6m") -> dict[str, Any]:
    """Weekly cumulative return paths for members (amount-weighted open purchases) + All Congress + S&P 500."""
    days = PERIODS.get(period, 182)
    start = (date.today() - timedelta(days=days)).isoformat()
    df = _trades_df(since=start)
    buys = df[(df["tx_type"] == "buy") & df["ticker"].notna()] if not df.empty else df
    if buys.empty:
        return {"series": []}
    closes, _ = _price_matrix(sorted(set(buys["ticker"]) | {"SPY"}), start)
    if closes.empty:
        return {"series": []}
    closes = closes.ffill()
    grid = closes.index[closes.index >= pd.Timestamp(start)]
    weekly = grid[::5].append(grid[-1:]).unique()

    def path(b: pd.DataFrame) -> list[dict[str, Any]]:
        b = b[b["ticker"].isin(closes.columns)]
        if b.empty:
            return []
        pts = []
        for d in weekly:
            open_ = b[b["tx_date"] <= d]
            if open_.empty:
                pts.append({"time": d.strftime("%Y-%m-%d"), "value": 0.0})
                continue
            vals, ws = [], []
            for r in open_.itertuples():
                i = closes.index.searchsorted(r.tx_date)
                if i >= len(closes.index):
                    continue
                e = closes[r.ticker].iloc[i]
                c = closes[r.ticker].loc[d]
                if pd.notna(e) and pd.notna(c) and e > 0:
                    vals.append(c / e - 1)
                    ws.append(r.weight)
            v = float(np.average(vals, weights=ws) * 100) if vals else 0.0
            pts.append({"time": d.strftime("%Y-%m-%d"), "value": round(v, 2)})
        return pts

    series = []
    spy = closes["SPY"]
    base = spy.loc[weekly[0]]
    series.append({"name": "S&P 500", "key": "SPY", "data": [{"time": d.strftime("%Y-%m-%d"), "value": round(float(spy.loc[d] / base - 1) * 100, 2)} for d in weekly]})
    series.append({"name": "All Congress", "key": "ALL", "data": path(buys)})
    for m in members[:10]:
        series.append({"name": m, "key": m, "data": path(buys[buys["member"] == m])})
    return {"series": series, "start": start, "label": LABEL}


def member_detail(member: str, period: str = "6m") -> dict[str, Any]:
    """Member page: all-time stats, estimated holdings, period stats (by disclosure date), timeline, trades."""
    df_all = _trades_df(member=member)
    if df_all.empty:
        return {"member": member, "trades": [], "found": False}
    days = PERIODS.get(period, 182)
    p_start = pd.Timestamp(date.today() - timedelta(days=days))
    first = df_all.iloc[0]
    buys_all = df_all[(df_all["tx_type"] == "buy") & df_all["ticker"].notna()]
    closes, opens = _price_matrix(sorted(set(df_all["ticker"].dropna()) | {"SPY"}),
                                  (df_all["tx_date"].min() - pd.Timedelta(days=7)).strftime("%Y-%m-%d"))
    scored_all = _score(buys_all, closes, opens)
    by_id = {r.id: r for r in scored_all.itertuples()} if not scored_all.empty else {}

    # estimated holdings: tickers with more disclosed buys than full sales (all time)
    holdings = []
    for t, g in df_all[df_all["ticker"].notna()].groupby("ticker"):
        nb, ns = int((g["tx_type"] == "buy").sum()), int((g["tx_type"] == "sell").sum())
        full_sale = g["asset"].str.contains("Full", case=False).any()
        if nb > ns and not full_sale:
            holdings.append({"ticker": t, "buys": nb, "sells": ns, "last": g["tx_date"].max().strftime("%Y-%m-%d")})

    # rank among members (6-month board, estimated return)
    board = leaderboard("6m")
    ranked = [r["member"] for r in board["rows"] if r["ranked"]]
    rank = ranked.index(member) + 1 if member in ranked else None

    # period slice by DISCLOSURE date
    dfp = df_all[df_all["disclosure_date"] >= p_start]
    sp = scored_all[scored_all["id"].isin(dfp["id"])] if not scored_all.empty else scored_all
    stats: dict[str, Any] = {"trades": int(len(dfp)), "buys": int((dfp["tx_type"] == "buy").sum()),
                             "sells": int((dfp["tx_type"] == "sell").sum()),
                             "priced": int(len(sp)), "priceable_buys": int(((dfp["tx_type"] == "buy") & dfp["ticker"].notna()).sum()),
                             "money_low": float(dfp["amount_low"].fillna(0).sum()),
                             "money_high": float(dfp["amount_high"].fillna(dfp["amount_low"]).fillna(0).sum()),
                             "median_filing_days": float((dfp["disclosure_date"] - dfp["tx_date"]).dt.days.median()) if len(dfp) else None}
    if len(sp):
        lo = df_all.set_index("id").loc[sp["id"], "amount_low"].fillna(1000).to_numpy()
        hi = df_all.set_index("id").loc[sp["id"], "amount_high"].fillna(pd.Series(lo)).to_numpy()
        ranges = [_pct(_wavg(sp["ret_tx"], pd.Series(w, index=sp.index))) for w in (lo, hi)]
        stats.update(
            estimated_return_pct=_pct(_wavg(sp["ret_tx"], sp["weight"])), estimated_spy_pct=_pct(_wavg(sp["spy_tx"], sp["weight"])),
            estimated_range=[min(ranges), max(ranges)],
            copy_return_pct=_pct(_wavg(sp["ret_copy"], sp["weight"])), copy_spy_pct=_pct(_wavg(sp["spy_copy"], sp["weight"])),
            win_rate_vs_spy_pct=float((sp["ret_tx"] > sp["spy_tx"]).mean() * 100))
    if not closes.empty and "SPY" in closes:
        spy = closes["SPY"].dropna()
        spy = spy[spy.index >= p_start]
        if len(spy) > 1:
            stats["spy_period_pct"] = float((spy.iloc[-1] / spy.iloc[0] - 1) * 100)

    # timeline: daily counts by trade date and by disclosure date within the period
    timeline = []
    if len(dfp):
        lo_d = min(dfp["tx_date"].min(), p_start)
        rng = pd.date_range(max(lo_d, p_start), pd.Timestamp(date.today()), freq="D")
        tc = dfp[dfp["tx_date"] >= p_start].groupby("tx_date").size().reindex(rng, fill_value=0)
        dc = dfp.groupby("disclosure_date").size().reindex(rng, fill_value=0)
        timeline = [{"date": d.strftime("%Y-%m-%d"), "traded": int(tc.loc[d]), "disclosed": int(dc.loc[d])} for d in rng]

    def px_at(t, d, side="left"):
        if t not in closes.columns:
            return None
        i = closes.index.searchsorted(d, side=side)
        if i >= len(closes.index):
            return None
        v = closes[t].iloc[i]
        return None if pd.isna(v) else float(v)

    trades = []
    for r in dfp.sort_values(["disclosure_date", "tx_date"], ascending=False).itertuples():
        s = by_id.get(r.id)
        ok = s is not None
        copy_ok = ok and s.ret_copy is not None and not pd.isna(s.ret_copy)
        trades.append({
            "ticker": r.ticker, "asset": r.asset, "asset_type": r.asset_type, "owner": r.owner, "tx_type": r.tx_type,
            "tx_date": r.tx_date.strftime("%Y-%m-%d"), "disclosure_date": r.disclosure_date.strftime("%Y-%m-%d"),
            "delay_days": int((r.disclosure_date - r.tx_date).days), "amount_low": r.amount_low, "amount_high": r.amount_high,
            "url": r.url, "price_trade": px_at(r.ticker, r.tx_date) if r.ticker else None,
            "price_disclosure": (float(s.entry_copy) if copy_ok else px_at(r.ticker, r.disclosure_date, "right")) if r.ticker else None,
            "return_pct": _pct(s.ret_tx) if ok else None, "spy_pct": _pct(s.spy_tx) if ok else None,
            "copy_return_pct": _pct(s.ret_copy) if copy_ok else None, "copy_spy_pct": _pct(s.spy_copy) if copy_ok else None,
        })
    return {
        "member": member, "found": True, "chamber": first["chamber"], "state_dst": first["state_dst"],
        "party": party_of(member, first["state_dst"]), "period": period, "period_start": p_start.strftime("%Y-%m-%d"),
        "all_time": {"trades": int(len(df_all)), "rank_6m": rank, "ranked_members": len(ranked),
                     "median_filing_days": float((df_all["disclosure_date"] - df_all["tx_date"]).dt.days.median()),
                     "first_trade": df_all["tx_date"].min().strftime("%Y-%m-%d")},
        "holdings": sorted(holdings, key=lambda h: h["last"], reverse=True), "stats": stats, "timeline": timeline,
        "trades": trades, "followed": bool(db.query_one("SELECT 1 AS x FROM followed_members WHERE member=?", (member,))),
        "prices_through": closes.index[-1].strftime("%Y-%m-%d") if not closes.empty else None, "label": LABEL,
    }


def recent_filings(days: int = 30, limit: int = 200) -> dict[str, Any]:
    ensure_seeded()
    since = (date.today() - timedelta(days=days)).isoformat()
    rows = db.query("SELECT * FROM congress_filings WHERE filing_date>=? ORDER BY filing_date DESC, doc_id DESC LIMIT ?", (since, limit))
    if not rows:
        rows = db.query("SELECT * FROM congress_filings ORDER BY filing_date DESC LIMIT ?", (limit,))
    trades = db.query(f"SELECT * FROM congress_trades WHERE doc_id IN ({','.join('?' * len(rows))})", [r["doc_id"] for r in rows]) if rows else []
    by: dict[str, list[dict[str, Any]]] = {}
    for t in trades:
        by.setdefault(t["doc_id"], []).append(t)
    for r in rows:
        r["trades"] = by.get(r["doc_id"], [])
        r["party"] = party_of(r["member"], r["state_dst"])
    n7 = db.query_one("SELECT COUNT(*) AS n FROM congress_filings WHERE filing_date>=?", ((date.today() - timedelta(days=7)).isoformat(),))
    return {"filings": rows, "count_7d": n7["n"] if n7 else 0, "as_of": db.last_fetch("congress:house")}


def top_stocks(period: str = "90d", limit: int = 40) -> dict[str, Any]:
    days = PERIODS.get(period, 90)
    df = _trades_df(since=(date.today() - timedelta(days=days)).isoformat())
    if df.empty:
        return {"rows": []}
    df = df[df["ticker"].notna()]
    rows = []
    for t, g in df.groupby("ticker"):
        b, s = g[g["tx_type"] == "buy"], g[g["tx_type"] == "sell"]
        rows.append({"ticker": t, "asset": g["asset"].iloc[0], "buys": int(len(b)), "sells": int(len(s)),
                     "members": int(g["member"].nunique()), "buy_volume_mid": float(b["weight"].sum()),
                     "sell_volume_mid": float(s["weight"].sum()), "last_trade": g["tx_date"].max().strftime("%Y-%m-%d"),
                     "member_names": sorted(g["member"].unique().tolist())[:6]})
    rows.sort(key=lambda r: (-(r["members"]), -(r["buys"] + r["sells"])))
    return {"rows": rows[:limit], "period": period}


def for_ticker(ticker: str) -> dict[str, Any]:
    df = _trades_df(ticker=ticker)
    if df.empty:
        return {"ticker": ticker.upper(), "trades": [], "label": LABEL}
    rows = []
    for r in df.sort_values("tx_date", ascending=False).itertuples():
        rows.append({"member": r.member, "party": party_of(r.member, r.state_dst), "chamber": r.chamber, "owner": r.owner,
                     "tx_type": r.tx_type, "tx_date": r.tx_date.strftime("%Y-%m-%d"), "disclosure_date": r.disclosure_date.strftime("%Y-%m-%d"),
                     "amount_low": r.amount_low, "amount_high": r.amount_high, "url": r.url})
    return {"ticker": ticker.upper(), "trades": rows, "label": LABEL}


def follow(member: str, on: bool) -> None:
    with db.tx() as c:
        if on:
            c.execute("INSERT OR IGNORE INTO followed_members(member, added_at) VALUES (?,?)", (member, db.now_iso()))
        else:
            c.execute("DELETE FROM followed_members WHERE member=?", (member,))


def following() -> dict[str, Any]:
    members = [r["member"] for r in db.query("SELECT member FROM followed_members ORDER BY added_at")]
    trades = []
    if members:
        trades = db.query(f"SELECT * FROM congress_trades WHERE member IN ({','.join('?' * len(members))}) ORDER BY disclosure_date DESC, tx_date DESC LIMIT 100", members)
    return {"members": members, "trades": trades}


def search_members(q: str) -> list[str]:
    ensure_seeded()
    return [r["member"] for r in db.query("SELECT DISTINCT member FROM congress_trades WHERE member LIKE ? ORDER BY member LIMIT 15", (f"%{q}%",))]


def observations(ticker: str) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {"bull": [], "bear": [], "neutral": []}
    df = _trades_df(ticker=ticker, since=(date.today() - timedelta(days=120)).isoformat())
    if df.empty:
        return out
    b, s = df[df["tx_type"] == "buy"], df[df["tx_type"] == "sell"]
    if len(b):
        out["bull"].append({"text": f"{b['member'].nunique()} member(s) of Congress disclosed {len(b)} purchase(s) in the last 120 days "
                                    "(disclosures lag trades and reveal nothing about intent)",
                            "date": b["disclosure_date"].max().strftime("%Y-%m-%d"), "source": "House Clerk PTR filings"})
    if len(s):
        out["bear"].append({"text": f"{s['member'].nunique()} member(s) of Congress disclosed {len(s)} sale(s) in the last 120 days",
                            "date": s["disclosure_date"].max().strftime("%Y-%m-%d"), "source": "House Clerk PTR filings"})
    return out


def warm(_: list[str] | None = None) -> None:
    ensure_seeded()
    for p in ("6m", "90d"):
        leaderboard(p)

