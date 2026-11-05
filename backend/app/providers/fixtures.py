"""Bundled sample data (captured from the real sources by scripts/capture_fixtures.py).

Used when DATA_MODE=mock, in tests, and as a fallback in DATA_MODE=auto when a live fetch fails.
Everything served from here is labeled "sample data" in the UI.
"""
from __future__ import annotations

import gzip
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd

from ..config import FIXTURES_DIR
from .base import CongressFiling, CongressTrade, MentionStat, NewsItem, ProviderError, SocialPost


def _read_json(path: Path) -> Any:
    if path.suffix == ".gz":
        with gzip.open(path, "rt") as f:
            return json.load(f)
    return json.loads(path.read_text())


def _find(base: Path, stem: str) -> Path | None:
    for ext in (".json.gz", ".json", ".csv.gz", ".csv"):
        p = base / f"{stem}{ext}"
        if p.exists():
            return p
    return None


def _safe(t: str) -> str:
    return t.upper().replace("^", "_")


@lru_cache(maxsize=512)
def _price_frame(ticker: str) -> pd.DataFrame | None:
    p = _find(FIXTURES_DIR / "prices", _safe(ticker))
    if p is None:
        return None
    df = pd.read_csv(p, parse_dates=["date"]).set_index("date")
    return df[["open", "high", "low", "close", "volume"]].astype(float)


@lru_cache(maxsize=1)
def _congress_prices() -> dict[str, pd.DataFrame]:
    p = FIXTURES_DIR / "prices" / "_congress_universe.csv.gz"
    if not p.exists():
        return {}
    df = pd.read_csv(p, parse_dates=["date"])
    return {t: g.set_index("date")[["open", "high", "low", "close", "volume"]].astype(float)
            for t, g in df.groupby("ticker")}


class FixtureStore:
    name = "sample-data"

    # --- prices ---
    def daily(self, tickers: list[str], start: str | None = None) -> dict[str, pd.DataFrame]:
        out = {}
        for t in tickers:
            df = _price_frame(t.upper())
            if df is None:
                df = _congress_prices().get(t.upper())
            if df is not None:
                out[t.upper()] = df[df.index >= pd.Timestamp(start)] if start else df
        return out

    def intraday(self, ticker: str, interval: str = "5m", days: int = 5) -> pd.DataFrame:
        p = _find(FIXTURES_DIR / "intraday", _safe(ticker))
        if p is None:
            raise ProviderError(f"no intraday sample for {ticker}")
        return pd.read_csv(p, parse_dates=["ts"]).set_index("ts").astype(float)

    def info(self, ticker: str) -> dict[str, Any]:
        p = FIXTURES_DIR / "info.json"
        data = _read_json(p) if p.exists() else {}
        if ticker.upper() not in data:
            raise ProviderError(f"no info sample for {ticker}")
        return data[ticker.upper()]

    def available_tickers(self) -> list[str]:
        d = FIXTURES_DIR / "prices"
        if not d.exists():
            return []
        return sorted(p.name.split(".")[0].replace("_", "^") for p in d.iterdir() if not p.name.startswith("_"))

    # --- EDGAR ---
    def company_facts(self, ticker: str) -> dict[str, Any]:
        p = _find(FIXTURES_DIR / "edgar" / "companyfacts", ticker.upper())
        if p is None:
            raise ProviderError(f"no company-facts sample for {ticker}")
        return _read_json(p)

    def submissions(self, ticker: str) -> dict[str, Any]:
        p = _find(FIXTURES_DIR / "edgar" / "submissions", ticker.upper())
        if p is None:
            raise ProviderError(f"no submissions sample for {ticker}")
        return _read_json(p)

    def ticker_map(self) -> dict[str, dict[str, Any]]:
        p = _find(FIXTURES_DIR / "edgar", "company_tickers")
        return _read_json(p) if p else {}

    def document_text(self, url: str) -> str:
        raise ProviderError("filing documents are not bundled as sample data (live mode only)")

    # --- news ---
    def news(self, ticker: str, days: int = 30, **_: Any) -> list[NewsItem]:
        p = _find(FIXTURES_DIR / "news", ticker.upper())
        if p is None:
            return []
        return [NewsItem(**x) for x in _read_json(p)]

    # --- macro ---
    def series(self, series_id: str, start: str | None = None) -> pd.Series:
        p = _find(FIXTURES_DIR / "macro", series_id)
        if p is None:
            raise ProviderError(f"no macro sample for {series_id}")
        df = pd.read_csv(p, parse_dates=["date"]).set_index("date")
        s = df["value"].astype(float)
        s.name = series_id
        return s[s.index >= pd.Timestamp(start)] if start else s

    def release_dates(self, days_ahead: int = 30) -> list[dict[str, Any]]:
        raise ProviderError("no release-date sample")

    # --- social ---
    def mentions(self) -> list[MentionStat]:
        p = _find(FIXTURES_DIR / "social", "apewisdom")
        return [MentionStat(**x) for x in _read_json(p)] if p else []

    def posts(self, ticker: str) -> list[SocialPost]:
        p = _find(FIXTURES_DIR / "social" / "posts", ticker.upper())
        return [SocialPost(**x) for x in _read_json(p)] if p else []

    # --- congress ---
    def congress(self) -> tuple[list[CongressFiling], list[CongressTrade]]:
        p = _find(FIXTURES_DIR / "congress", "house")
        if p is None:
            return [], []
        d = _read_json(p)
        return [CongressFiling(**f) for f in d["filings"]], [CongressTrade(**t) for t in d["trades"]]
