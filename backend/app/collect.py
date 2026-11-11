"""Daily batch job: collect every source into the database, one isolated step per source.

    python -m app.collect                      # all steps
    python -m app.collect --only prices,news   # some steps
    python -m app.collect --list

Each step is incremental (only new bars / headlines / filings are fetched), idempotent (upserts, so re-running a
day is safe) and recorded in `ingest_runs` as `daily:<step>`, with a parent `daily` row for the whole batch.
One step failing does not stop the others; the process exits 1 if any step failed.
The web app only reads what this job stored, so pages show data "as of" the last successful run.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from typing import Any, Callable

log = logging.getLogger("marketlens.collect")


def _universe() -> tuple[list[str], list[str]]:
    """(watchlist tickers, watchlist + market strip + sector ETFs)."""
    from .services import watchlists
    from .services.universe import MARKET_TICKERS, SECTOR_ETF_LIST

    wl = watchlists.all_tickers()
    return wl, sorted(set(wl + MARKET_TICKERS + SECTOR_ETF_LIST))


def step_prices() -> str:
    from .services import prices

    _, everything = _universe()
    done = prices.refresh(everything, force=True)
    return f"{len(done)}/{len(everything)} tickers refreshed"


def step_fundamentals() -> str:
    from .providers.base import ProviderError
    from .services import fundamentals

    wl, _ = _universe()
    ok = 0
    for t in wl:
        try:
            fundamentals.facts(t)
            fundamentals.submissions(t)
            ok += 1
        except ProviderError:
            continue  # ETFs and funds have no SEC company facts
    return f"{ok}/{len(wl)} companies"


def step_news() -> str:
    from .services import news

    wl, _ = _universe()
    ok = sum(1 for t in wl if news.refresh(t, force=True))
    if wl and ok == 0:
        raise RuntimeError("no news source answered for any ticker")
    return f"{ok}/{len(wl)} tickers"


def step_social() -> str:
    from .services import social

    wl, _ = _universe()
    social.refresh_mentions(force=True)
    for t in wl:
        social.refresh_posts(t, force=True)
    return f"mention ranks + posts for {len(wl)} tickers"


def step_macro() -> str:
    from .services import macro
    from .services.universe import MACRO_SERIES

    missing = [sid for sid in MACRO_SERIES if macro.series(sid)[0].empty]
    if len(missing) == len(MACRO_SERIES):
        raise RuntimeError("no FRED series available")
    return f"{len(MACRO_SERIES) - len(missing)}/{len(MACRO_SERIES)} series" + (f" (missing {', '.join(missing)})" if missing else "")


def step_congress() -> str:
    from .services import congress

    res = congress.refresh()
    if res.get("error"):
        raise RuntimeError(res["error"])
    return res.get("note") or f"{res.get('new_filings', 0)} new filings, {res.get('new_trades', 0)} trades"


# order matters only for readability; steps are independent
STEPS: dict[str, tuple[str, Callable[[], str]]] = {
    "prices": ("Daily prices (yfinance)", step_prices),
    "fundamentals": ("Fundamentals and filings (SEC EDGAR)", step_fundamentals),
    "news": ("News headlines + sentiment", step_news),
    "social": ("Crowd: Reddit mention ranks, StockTwits/Reddit posts", step_social),
    "macro": ("Macro series (FRED)", step_macro),
    "congress": ("Congress filings (House Clerk)", step_congress),
}


def run_daily(only: list[str] | None = None) -> dict[str, Any]:
    """Run the batch. Returns {"ok": bool, "steps": [{step, status, seconds, info|error}]}."""
    from .services import ingest

    names = only or list(STEPS)
    unknown = [n for n in names if n not in STEPS]
    if unknown:
        raise ValueError(f"unknown step(s): {', '.join(unknown)}; choose from {', '.join(STEPS)}")
    if ingest.is_running("daily"):
        log.warning("a daily batch is already running; skipping this one")
        return {"ok": True, "skipped": "already running", "steps": []}
    results: list[dict[str, Any]] = []
    with ingest.run("daily") as parent:
        for name in names:
            t0 = time.monotonic()
            try:
                with ingest.run(f"daily:{name}") as rec:
                    info = STEPS[name][1]()
                    rec["note"] = info
                results.append({"step": name, "status": "ok", "seconds": round(time.monotonic() - t0, 1), "info": info})
                log.info("step %-12s ok     %5.1fs  %s", name, time.monotonic() - t0, info)
            except Exception as e:  # isolate sources: one failing must not stop the rest
                results.append({"step": name, "status": "error", "seconds": round(time.monotonic() - t0, 1),
                                "error": f"{type(e).__name__}: {e}"})
                log.error("step %-12s FAILED %5.1fs  %s", name, time.monotonic() - t0, e)
        failed = [r["step"] for r in results if r["status"] == "error"]
        parent["note"] = f"{len(results) - len(failed)}/{len(results)} steps ok" + (f"; failed: {', '.join(failed)}" if failed else "")
        if failed:
            parent["status"] = "partial" if len(failed) < len(results) else "error"
    return {"ok": not failed, "steps": results}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="MarketLens daily data collection")
    ap.add_argument("--only", help="comma-separated steps (default: all)")
    ap.add_argument("--list", action="store_true", help="list the steps and exit")
    a = ap.parse_args(argv)
    if a.list:
        for k, (label, _) in STEPS.items():
            print(f"{k:13s} {label}")
        return 0
    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    from . import db

    log.info("daily collection -> %s", db.describe()["target"])
    try:
        out = run_daily([s.strip() for s in a.only.split(",") if s.strip()] if a.only else None)
    except ValueError as e:
        print(e, file=sys.stderr)
        return 2
    db.reset_connection()
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
