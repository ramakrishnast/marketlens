"""Macro series from FRED (fed funds, 10Y/2Y yields, curve, CPI, unemployment), cached daily."""
from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from .. import db
from ..providers import registry
from .universe import MACRO_SERIES

log = logging.getLogger(__name__)


def series(sid: str) -> tuple[pd.Series, dict[str, Any]]:
    key = f"macro:{sid}"
    hit = db.cache_get(key, max_age_minutes=12 * 60)
    if not hit:
        try:
            s, source = registry.live_or_sample(lambda: registry.macro().series(sid), lambda: registry.fixtures().series(sid), f"FRED {sid}")
            db.cache_put(key, {d.strftime("%Y-%m-%d"): float(v) for d, v in s.items()}, source)
        except Exception as e:
            log.warning("macro %s unavailable: %s", sid, e)
        hit = db.cache_get(key)
    if not hit:
        return pd.Series(dtype=float), {"error": f"{sid} unavailable"}
    data, fetched_at, source = hit
    s = pd.Series(data, dtype=float)
    s.index = pd.to_datetime(s.index)
    return s.sort_index(), {"fetched_at": fetched_at, "source": source, "last_obs": s.index.max().strftime("%Y-%m-%d") if len(s) else None,
                            "url": f"https://fred.stlouisfed.org/series/{sid}"}


def _ago(s: pd.Series, days: int) -> float | None:
    if s.empty:
        return None
    target = s.index[-1] - pd.Timedelta(days=days)
    prior = s[s.index <= target]
    return float(prior.iloc[-1]) if len(prior) else None


def summary() -> dict[str, Any]:
    out = []
    for sid, label in MACRO_SERIES.items():
        s, meta = series(sid)
        if s.empty:
            out.append({"id": sid, "label": label, "error": meta.get("error")})
            continue
        row = {"id": sid, "label": label, "value": float(s.iloc[-1]), "date": s.index[-1].strftime("%Y-%m-%d"),
               "change_1m": None, "change_1y": None, **meta}
        a1m, a1y = _ago(s, 30), _ago(s, 365)
        row["change_1m"] = row["value"] - a1m if a1m is not None else None
        row["change_1y"] = row["value"] - a1y if a1y is not None else None
        if sid == "CPIAUCSL" and a1y:
            row["yoy_pct"] = (row["value"] / a1y - 1) * 100
            row["label"] = "CPI inflation, year over year (%)"
        out.append(row)
    return {"series": out, "source": "FRED, Federal Reserve Bank of St. Louis"}


def history(sid: str, years: int = 10) -> dict[str, Any]:
    s, meta = series(sid)
    if s.empty:
        return {"id": sid, "points": [], **meta}
    s = s[s.index >= s.index[-1] - pd.Timedelta(days=365 * years)]
    if sid == "CPIAUCSL":
        s = (s / s.shift(12) - 1).dropna() * 100
    if len(s) > 1500:  # daily series: weekly sampling is plenty for a chart
        s = s.resample("W").last().dropna()
    return {"id": sid, "label": MACRO_SERIES.get(sid, sid) if sid != "CPIAUCSL" else "CPI inflation, YoY (%)",
            "points": [{"time": d.strftime("%Y-%m-%d"), "value": round(float(v), 3)} for d, v in s.items()], **meta}


def warm(_: list[str] | None = None) -> None:
    summary()
