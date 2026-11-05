"""Which concrete provider serves each kind of data.

To swap in a paid API, implement the matching Protocol from `base.py` and change the factory
below (e.g. `prices()` -> PolygonPriceProvider()). Nothing else in the app needs to change.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any, Callable, TypeVar

from ..config import get_settings
from .base import ProviderError
from .congress import HouseClerkProvider
from .edgar import EdgarProvider
from .fixtures import FixtureStore
from .fred import FredProvider
from .news import (AlphaVantageNewsProvider, FinnhubNewsProvider, GoogleNewsRSSProvider,
                   InvestorRelationsRSSProvider)
from .social import ApeWisdomProvider, RedditProvider, StockTwitsProvider
from .yahoo import YahooPriceProvider

log = logging.getLogger(__name__)
T = TypeVar("T")

SAMPLE = "sample-data"


@lru_cache
def fixtures() -> FixtureStore:
    return FixtureStore()


@lru_cache
def prices() -> YahooPriceProvider:
    return YahooPriceProvider()


@lru_cache
def fundamentals() -> EdgarProvider:
    return EdgarProvider()


@lru_cache
def macro() -> FredProvider:
    return FredProvider()


@lru_cache
def mentions() -> ApeWisdomProvider:
    return ApeWisdomProvider()


@lru_cache
def stocktwits() -> StockTwitsProvider:
    return StockTwitsProvider()


@lru_cache
def reddit() -> RedditProvider:
    return RedditProvider()


@lru_cache
def congress() -> HouseClerkProvider:
    return HouseClerkProvider()


def news_providers() -> list[Any]:
    """Keyed providers first (when configured), then free RSS."""
    s = get_settings()
    out: list[Any] = []
    if s.finnhub_api_key:
        out.append(FinnhubNewsProvider())
    if s.alpha_vantage_api_key:
        out.append(AlphaVantageNewsProvider())
    out += [GoogleNewsRSSProvider(), InvestorRelationsRSSProvider()]
    return out


def mode() -> str:
    return get_settings().data_mode


def live_or_sample(live: Callable[[], T], sample: Callable[[], T], what: str) -> tuple[T, str]:
    """Run the live fetch according to DATA_MODE.

    mock  -> sample only; live -> live only; auto -> live, falling back to bundled sample data.
    Returns (value, source_label).
    """
    m = mode()
    if m == "mock":
        return sample(), SAMPLE
    try:
        return live(), "live"
    except Exception as e:
        if m == "live":
            raise ProviderError(f"{what}: {e}") from e
        log.warning("%s: live fetch failed (%s); using bundled sample data", what, e)
        return sample(), SAMPLE
