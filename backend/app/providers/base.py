"""Provider interfaces.

Every external data source sits behind one of these small interfaces, so a paid source
(Polygon, FMP, ...) can be swapped in by writing one class and registering it in
`providers/registry.py`, without touching services, API routes or the frontend.

Return types are plain pandas / dicts so implementations stay trivial.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

import pandas as pd


class ProviderError(RuntimeError):
    """Raised when a provider cannot serve a request (network, rate limit, missing key...)."""


@dataclass
class NewsItem:
    ticker: str
    headline: str
    source: str
    url: str
    published_at: str  # ISO 8601 UTC
    snippet: str = ""  # short snippet only, never full article text
    provider: str = ""


@dataclass
class SocialPost:
    id: str
    ticker: str
    platform: str  # reddit | stocktwits
    community: str  # e.g. r/wallstreetbets
    author: str
    snippet: str
    url: str
    created_at: str
    score: int = 0
    user_label: str | None = None  # stocktwits user-tagged Bullish/Bearish


@dataclass
class MentionStat:
    ticker: str
    platform: str  # e.g. apewisdom:wallstreetbets
    mentions: int
    rank: int | None
    upvotes: int | None = None
    mentions_24h_ago: int | None = None
    rank_24h_ago: int | None = None


@dataclass
class CongressFiling:
    doc_id: str
    chamber: str
    member: str
    state_dst: str
    filing_date: str  # ISO date
    url: str


@dataclass
class CongressTrade:
    doc_id: str
    chamber: str
    member: str
    state_dst: str
    owner: str
    ticker: str | None
    asset: str
    asset_type: str
    tx_type: str  # buy | sell | exchange
    tx_date: str
    disclosure_date: str
    amount_low: float | None
    amount_high: float | None
    url: str
    extra: dict[str, Any] = field(default_factory=dict)


class PriceProvider(Protocol):
    name: str

    def daily(self, tickers: list[str], start: str | None = None) -> dict[str, pd.DataFrame]:
        """Daily OHLCV. DataFrame index = DatetimeIndex (date), columns open, high, low, close, volume."""

    def intraday(self, ticker: str, interval: str = "5m", days: int = 5) -> pd.DataFrame: ...

    def info(self, ticker: str) -> dict[str, Any]:
        """Best-effort extras: name, forward P/E, next earnings date, sector, industry."""


class FundamentalsProvider(Protocol):
    name: str

    def company_facts(self, ticker: str) -> dict[str, Any]: ...

    def submissions(self, ticker: str) -> dict[str, Any]: ...

    def ticker_map(self) -> dict[str, dict[str, Any]]:
        """TICKER -> {cik, name}."""

    def document_text(self, url: str) -> str: ...


class NewsProvider(Protocol):
    name: str

    def news(self, ticker: str, days: int = 30) -> list[NewsItem]: ...


class MacroProvider(Protocol):
    name: str

    def series(self, series_id: str, start: str | None = None) -> pd.Series: ...

    def release_dates(self, days_ahead: int = 30) -> list[dict[str, Any]]: ...


class SocialProvider(Protocol):
    name: str

    def mentions(self) -> list[MentionStat]: ...

    def posts(self, ticker: str) -> list[SocialPost]: ...


class CongressProvider(Protocol):
    name: str

    def filings(self, year: int) -> list[CongressFiling]: ...

    def trades(self, filing: CongressFiling) -> list[CongressTrade]: ...
