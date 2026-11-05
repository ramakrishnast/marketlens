"""Macro data from FRED (St. Louis Fed).

With FRED_API_KEY we use the official API (also gives upcoming release dates).
Without a key we fall back to the public fredgraph.csv download, which needs no key.
"""
from __future__ import annotations

import io
from datetime import date, timedelta
from typing import Any

import httpx
import pandas as pd

from ..config import get_settings
from .base import ProviderError

# FRED release ids for the macro events we put on the calendar
KEY_RELEASES = {
    10: "CPI (Consumer Price Index)",
    50: "Jobs report (Employment Situation)",
    53: "GDP",
    54: "PCE / Personal Income and Outlays",
    46: "PPI (Producer Price Index)",
    9: "Retail Sales",
}


class FredProvider:
    name = "fred"

    def series(self, series_id: str, start: str | None = None) -> pd.Series:
        key = get_settings().fred_api_key
        start = start or str(date.today() - timedelta(days=365 * 10))
        if key:
            r = httpx.get("https://api.stlouisfed.org/fred/series/observations", params={
                "series_id": series_id, "api_key": key, "file_type": "json", "observation_start": start,
            }, timeout=30)
            if r.status_code != 200:
                raise ProviderError(f"FRED HTTP {r.status_code}: {r.text[:200]}")
            obs = r.json().get("observations", [])
            s = pd.Series({o["date"]: o["value"] for o in obs})
        else:
            r = httpx.get("https://fred.stlouisfed.org/graph/fredgraph.csv",
                          params={"id": series_id, "cosd": start}, timeout=30, follow_redirects=True)
            if r.status_code != 200:
                raise ProviderError(f"FRED CSV HTTP {r.status_code}")
            df = pd.read_csv(io.StringIO(r.text))
            s = pd.Series(df.iloc[:, 1].values, index=df.iloc[:, 0].values)
        s = pd.to_numeric(s.replace(".", None), errors="coerce").dropna()
        s.index = pd.to_datetime(s.index)
        s.name = series_id
        if s.empty:
            raise ProviderError(f"FRED series {series_id} empty")
        return s

    def release_dates(self, days_ahead: int = 30) -> list[dict[str, Any]]:
        key = get_settings().fred_api_key
        if not key:
            raise ProviderError("FRED_API_KEY needed for the release calendar")
        today = date.today()
        r = httpx.get("https://api.stlouisfed.org/fred/releases/dates", params={
            "api_key": key, "file_type": "json", "realtime_start": str(today),
            "realtime_end": str(today + timedelta(days=days_ahead)),
            "include_release_dates_with_no_data": "true", "limit": 1000, "sort_order": "asc",
        }, timeout=30)
        r.raise_for_status()
        out = []
        for d in r.json().get("release_dates", []):
            rid = int(d["release_id"])
            if rid in KEY_RELEASES:
                out.append({"date": d["date"], "event": KEY_RELEASES[rid], "source": "FRED release calendar",
                            "url": f"https://fred.stlouisfed.org/releases/{rid}"})
        return out
