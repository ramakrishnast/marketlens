"""Capture bundled sample data from the real free sources.

    python -m scripts.capture_fixtures [--only prices,edgar,news,macro,social,congress]

Sample data powers DATA_MODE=mock, the test-suite, and the fallback in DATA_MODE=auto.
Only headlines/snippets/links are saved for news and social posts, never article text.
"""
from __future__ import annotations

import argparse
import gzip
import json
import logging
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import FIXTURES_DIR  # noqa: E402
from app.providers.congress import HouseClerkProvider  # noqa: E402
from app.providers.edgar import EdgarProvider  # noqa: E402
from app.providers.fred import FredProvider  # noqa: E402
from app.providers.news import GoogleNewsRSSProvider  # noqa: E402
from app.providers.social import ApeWisdomProvider, StockTwitsProvider  # noqa: E402
from app.providers.yahoo import YahooPriceProvider  # noqa: E402
from app.services.universe import (DEMO_TICKERS, MACRO_SERIES, MARKET_TICKERS, PEER_EXTRA,  # noqa: E402
                                   SECTOR_ETF_LIST as SECTOR_ETFS)
from app.services.xbrl import trim_company_facts  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("capture")

ETFS = {"SPY", "QQQ", "IWM", "DIA", "^VIX", "^TNX", *SECTOR_ETFS}
STOCKS = sorted({t for t in DEMO_TICKERS + PEER_EXTRA if t not in ETFS})


def _safe(t: str) -> str:
    return t.upper().replace("^", "_")


def write_json(path: Path, data, gz: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if gz:
        with gzip.open(str(path) + ".gz" if not str(path).endswith(".gz") else path, "wt") as f:
            json.dump(data, f, separators=(",", ":"), default=str)
    else:
        path.write_text(json.dumps(data, indent=1, default=str))


def write_frame(path: Path, df: pd.DataFrame, index_label: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df = df.copy()
    for c in ("open", "high", "low", "close"):
        if c in df:
            df[c] = df[c].round(4)
    df.to_csv(path, index_label=index_label, compression="gzip")


def cap_prices() -> None:
    yp = YahooPriceProvider()
    universe = sorted(set(DEMO_TICKERS + MARKET_TICKERS + SECTOR_ETFS + PEER_EXTRA))
    data = yp.daily(universe)
    for t, df in data.items():
        write_frame(FIXTURES_DIR / "prices" / f"{_safe(t)}.csv.gz", df, "date")
    log.info("prices: %d/%d tickers", len(data), len(universe))
    for t in [x for x in DEMO_TICKERS if x not in ETFS] + ["SPY", "QQQ"]:
        try:
            df = yp.intraday(t, "5m", 5)
            write_frame(FIXTURES_DIR / "intraday" / f"{_safe(t)}.csv.gz", df, "ts")
        except Exception as e:
            log.warning("intraday %s: %s", t, e)
    info = {}
    for t in universe:
        try:
            info[t] = yp.info(t)
        except Exception as e:
            log.warning("info %s: %s", t, e)
        time.sleep(0.4)
    (FIXTURES_DIR / "info.json").write_text(json.dumps(info, indent=1, default=str))


def cap_edgar() -> None:
    ep = EdgarProvider()
    tm = ep.ticker_map()
    write_json(FIXTURES_DIR / "edgar" / "company_tickers.json", tm)
    for t in STOCKS:
        try:
            facts = trim_company_facts(ep.company_facts(t))
            write_json(FIXTURES_DIR / "edgar" / "companyfacts" / f"{t}.json", facts)
            sub = ep.submissions(t)
            recent = sub.get("filings", {}).get("recent", {})
            n = min(400, len(recent.get("form", [])))
            sub["filings"] = {"recent": {k: v[:n] for k, v in recent.items()}}
            write_json(FIXTURES_DIR / "edgar" / "submissions" / f"{t}.json", sub)
            log.info("edgar %s ok", t)
        except Exception as e:
            log.warning("edgar %s: %s", t, e)


def cap_news() -> None:
    gp = GoogleNewsRSSProvider()
    names = json.loads((FIXTURES_DIR / "info.json").read_text()) if (FIXTURES_DIR / "info.json").exists() else {}
    for t in DEMO_TICKERS:
        try:
            company = (names.get(t) or {}).get("name")
            items = gp.news(t, 30, company=company if t not in ETFS else None)
            write_json(FIXTURES_DIR / "news" / f"{t}.json", [asdict(i) for i in items], gz=False)
            log.info("news %s: %d", t, len(items))
        except Exception as e:
            log.warning("news %s: %s", t, e)
        time.sleep(1)


def cap_macro() -> None:
    fp = FredProvider()
    for sid in MACRO_SERIES:
        try:
            s = fp.series(sid)
            df = s.rename("value").to_frame()
            df.to_csv(FIXTURES_DIR / "macro" / f"{sid}.csv" if (FIXTURES_DIR / "macro").exists()
                      else _mk(FIXTURES_DIR / "macro") / f"{sid}.csv", index_label="date")
            log.info("macro %s: %d", sid, len(s))
        except Exception as e:
            log.warning("macro %s: %s", sid, e)


def _mk(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


def cap_social() -> None:
    try:
        ms = ApeWisdomProvider().mentions()
        write_json(FIXTURES_DIR / "social" / "apewisdom.json", [asdict(m) for m in ms], gz=False)
        log.info("apewisdom: %d", len(ms))
    except Exception as e:
        log.warning("apewisdom: %s", e)
    st = StockTwitsProvider()
    for t in DEMO_TICKERS:
        try:
            posts = st.posts(t)
            write_json(FIXTURES_DIR / "social" / "posts" / f"{t}.json", [asdict(p) for p in posts], gz=False)
            log.info("stocktwits %s: %d", t, len(posts))
        except Exception as e:
            log.warning("stocktwits %s: %s", t, e)
        time.sleep(1)


def cap_congress(years: list[int]) -> None:
    hp = HouseClerkProvider()
    filings, trades = [], []
    for y in years:
        fs = hp.filings(y)
        log.info("house %d: %d PTR filings", y, len(fs))
        for i, f in enumerate(fs):
            try:
                ts = hp.trades(f)
                trades += [asdict(t) for t in ts]
                filings.append({**asdict(f)})
            except Exception as e:
                log.warning("ptr %s: %s", f.doc_id, e)
                filings.append({**asdict(f)})
            if i % 50 == 0:
                log.info("  %d/%d parsed, %d trades so far", i, len(fs), len(trades))
            time.sleep(0.25)
    write_json(FIXTURES_DIR / "congress" / "house.json", {"filings": filings, "trades": trades})
    tickers = sorted({t["ticker"] for t in trades if t.get("ticker") and t["asset_type"] in ("ST", "EF")})
    start = f"{min(years) - 1}-06-01"
    data = YahooPriceProvider().daily(tickers + ["SPY"], start=start)
    frames = []
    for t, df in data.items():
        df = df.copy()
        df["ticker"] = t
        frames.append(df)
    if frames:
        all_df = pd.concat(frames)
        for c in ("open", "high", "low", "close"):
            all_df[c] = all_df[c].round(3)
        all_df.to_csv(FIXTURES_DIR / "prices" / "_congress_universe.csv.gz", index_label="date", compression="gzip")
    log.info("congress: %d trades, %d tickers priced", len(trades), len(data))


def cap_congress_accounts() -> None:
    """Re-parse the PDFs already in the bundled House sample and add each trade's account (no new filings, prices untouched)."""
    from app.providers.base import CongressFiling

    path = FIXTURES_DIR / "congress" / "house.json.gz"
    with gzip.open(path, "rt") as f:
        data = json.load(f)
    hp = HouseClerkProvider()
    by_doc: dict[str, list[dict]] = {}
    for t in data["trades"]:
        by_doc.setdefault(t["doc_id"], []).append(t)
    filings = {f["doc_id"]: f for f in data["filings"]}
    for i, (doc, rows) in enumerate(by_doc.items()):
        try:
            parsed = hp.trades(CongressFiling(**filings[doc]))
        except Exception as e:
            log.warning("accounts %s: %s", doc, e)
            continue
        if len(parsed) != len(rows):
            log.warning("accounts %s: parsed %d trades but fixture has %d; skipped", doc, len(parsed), len(rows))
            continue
        for row, p in zip(rows, parsed):
            row["account"] = p.account
        if i % 50 == 0:
            log.info("  %d/%d filings", i, len(by_doc))
        time.sleep(0.2)
    write_json(path, data)
    log.info("accounts added for %d filings", len(by_doc))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="prices,edgar,news,macro,social,congress")
    ap.add_argument("--years", default="2025,2026")
    a = ap.parse_args()
    parts = a.only.split(",")
    if "edgar" in parts and not os.environ.get("SEC_USER_AGENT"):
        sys.exit("Set SEC_USER_AGENT to capture EDGAR fixtures")
    if "congress-accounts" in parts:
        cap_congress_accounts()
        return
    for name in ["prices", "edgar", "news", "macro", "social", "congress"]:
        if name in parts:
            log.info("=== %s ===", name)
            if name == "congress":
                cap_congress([int(y) for y in a.years.split(",")])
            else:
                globals()[f"cap_{name}"]()


if __name__ == "__main__":
    main()
