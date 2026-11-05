"""Dated catalysts: earnings (next + history), macro releases, FOMC, monthly options expiration."""
from __future__ import annotations

import json
import logging
import statistics
from datetime import date, timedelta
from typing import Any

import pandas as pd

from .. import db
from ..config import CONFIG_DIR
from ..providers import registry
from . import fundamentals, prices

log = logging.getLogger(__name__)


def past_earnings(ticker: str) -> list[str]:
    try:
        return fundamentals.earnings_release_dates(ticker)
    except Exception as e:
        log.debug("no earnings history for %s: %s", ticker, e)
        return []


def next_earnings(ticker: str) -> dict[str, Any] | None:
    """Next earnings date: Yahoo calendar when available, else estimated from EDGAR 8-K Item 2.02 history."""
    t = ticker.upper()
    today = date.today()
    info = prices.info(t)
    ne = info.get("next_earnings")
    if ne:
        try:
            d = date.fromisoformat(str(ne)[:10])
            if d >= today - timedelta(days=1):
                return {"date": d.isoformat(), "estimated": False, "source": "Yahoo Finance earnings calendar"}
        except ValueError:
            pass
    hist = past_earnings(t)
    if len(hist) < 2:
        return None
    ds = [date.fromisoformat(x) for x in hist[-9:]]
    gaps = [(b - a).days for a, b in zip(ds, ds[1:]) if 60 < (b - a).days < 120]
    gap = int(statistics.median(gaps)) if gaps else 91
    nxt = ds[-1] + timedelta(days=gap)
    while nxt < today:
        nxt += timedelta(days=gap)
    # earnings are usually announced on the same weekday pattern; snap weekends to Monday
    while nxt.weekday() >= 5:
        nxt += timedelta(days=1)
    return {"date": nxt.isoformat(), "estimated": True, "last_reported": ds[-1].isoformat(),
            "source": f"Estimated: last earnings 8-K ({ds[-1].isoformat()}) + typical {gap}-day cadence (SEC EDGAR)"}


def earnings_reactions(ticker: str, df: pd.DataFrame | None = None) -> dict[str, Any]:
    """Price move around past earnings releases (close before -> close after the release)."""
    df = prices.daily(ticker) if df is None else df
    hist = past_earnings(ticker)
    moves = []
    for d in hist:
        ts = pd.Timestamp(d)
        before = df[df.index < ts]
        after = df[df.index > ts]  # releases are usually after the close or pre-market; use next session
        if len(before) == 0 or len(after) == 0:
            continue
        # if released pre-market the reaction is on the filing day itself; include it via the day's close
        on_day = df[df.index == ts]
        c0 = float(before["close"].iloc[-1])
        c1 = float(after["close"].iloc[0]) if on_day.empty else max(
            [float(on_day["close"].iloc[0]), float(after["close"].iloc[0])], key=lambda x: abs(x / c0 - 1))
        moves.append({"date": d, "move_pct": round((c1 / c0 - 1) * 100, 2)})
    if not moves:
        return {"n": 0, "moves": []}
    absm = [abs(m["move_pct"]) for m in moves]
    return {"n": len(moves), "avg_abs_move_pct": round(sum(absm) / len(absm), 2),
            "max_abs_move_pct": round(max(absm), 2), "moves": moves[-12:],
            "note": "Largest close-to-close move over the release day and the next session"}


def options_expirations(days_ahead: int = 45) -> list[dict[str, Any]]:
    out = []
    today = date.today()
    for m in range(0, 3):
        y, mo = today.year + (today.month - 1 + m) // 12, (today.month - 1 + m) % 12 + 1
        d = date(y, mo, 15)
        while d.weekday() != 4:
            d += timedelta(days=1)
        if today <= d <= today + timedelta(days=days_ahead):
            out.append({"date": d.isoformat(), "event": "Monthly options expiration (3rd Friday)", "kind": "market",
                        "source": "Exchange calendar rule"})
    return out


def macro_events(days_ahead: int = 30) -> list[dict[str, Any]]:
    today = date.today()
    end = today + timedelta(days=days_ahead)
    out: list[dict[str, Any]] = []
    key = f"macro:releases:{today.isoformat()}"
    hit = db.cache_get(key, max_age_minutes=12 * 60)
    if hit:
        out = hit[0]
    elif registry.mode() != "mock":
        try:
            out = registry.macro().release_dates(days_ahead)
            db.cache_put(key, out, "live")
        except Exception as e:
            log.info("FRED release calendar unavailable: %s", e)
    try:
        cal = json.loads((CONFIG_DIR / "macro_calendar.json").read_text())["events"]
        out = out + [{**e, "source": "Federal Reserve FOMC calendar (config/macro_calendar.json)"} for e in cal]
    except Exception:
        pass
    out = [e for e in out if today.isoformat() <= e["date"] <= end.isoformat()]
    for e in out:
        e.setdefault("kind", "macro")
    return sorted(out, key=lambda e: e["date"])


def upcoming(ticker: str | None, days_ahead: int = 30) -> list[dict[str, Any]]:
    out = macro_events(days_ahead) + options_expirations(days_ahead)
    if ticker:
        ne = next_earnings(ticker)
        if ne and ne["date"] <= (date.today() + timedelta(days=max(days_ahead, 100))).isoformat():
            out.append({"date": ne["date"], "event": f"{ticker.upper()} earnings{' (estimated date)' if ne['estimated'] else ''}",
                        "kind": "earnings", "source": ne["source"], "estimated": ne["estimated"]})
    return sorted(out, key=lambda e: e["date"])
