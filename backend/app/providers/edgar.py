"""SEC EDGAR: company facts (XBRL), submissions (filing index) and filing documents.

EDGAR requires a descriptive User-Agent with contact info (SEC_USER_AGENT in .env) and
allows at most 10 requests/second. We stay well below that.
"""
from __future__ import annotations

import html
import re
import threading
import time
from typing import Any

import httpx

from ..config import get_settings
from .base import ProviderError

_lock = threading.Lock()
_last_call = [0.0]
MIN_INTERVAL = 0.15  # seconds between requests (~6/s)


def _throttle() -> None:
    with _lock:
        wait = _last_call[0] + MIN_INTERVAL - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.monotonic()


def _get(url: str, *, as_json: bool = True, timeout: float = 30.0) -> Any:
    ua = get_settings().sec_user_agent
    if not ua:
        raise ProviderError("SEC_USER_AGENT is not set in .env (EDGAR requires 'Name email@example.com')")
    last: Exception | None = None
    for attempt in range(4):
        _throttle()
        try:
            r = httpx.get(url, headers={"User-Agent": ua, "Accept-Encoding": "gzip, deflate"},
                          timeout=timeout, follow_redirects=True)
            if r.status_code == 429 or r.status_code >= 500:
                raise ProviderError(f"EDGAR HTTP {r.status_code}")
            if r.status_code == 404:
                raise ProviderError(f"EDGAR 404 for {url}")
            r.raise_for_status()
            return r.json() if as_json else r.text
        except ProviderError as e:
            if "404" in str(e):
                raise
            last = e
        except Exception as e:
            last = e
        time.sleep(1.5 * (2**attempt))
    raise ProviderError(f"EDGAR request failed: {last}")


def html_to_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style|ix:header)[^>]*>.*?</\1>", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</h\d>", "\n", raw)
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    raw = html.unescape(raw)
    raw = re.sub(r"[ \t\xa0]+", " ", raw)
    raw = re.sub(r"\n\s*\n+", "\n\n", raw)
    return raw.strip()


class EdgarProvider:
    name = "sec-edgar"

    def __init__(self) -> None:
        self._map: dict[str, dict[str, Any]] | None = None

    def ticker_map(self) -> dict[str, dict[str, Any]]:
        if self._map is None:
            data = _get("https://www.sec.gov/files/company_tickers.json")
            self._map = {
                v["ticker"].upper(): {"cik": int(v["cik_str"]), "name": v["title"]} for v in data.values()
            }
        return self._map

    def cik(self, ticker: str) -> int:
        m = self.ticker_map().get(ticker.upper().replace(".", "-"))
        if not m:
            raise ProviderError(f"{ticker} not found in SEC ticker map (ETFs/funds have no company facts)")
        return m["cik"]

    def company_facts(self, ticker: str) -> dict[str, Any]:
        return _get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{self.cik(ticker):010d}.json")

    def submissions(self, ticker: str) -> dict[str, Any]:
        return _get(f"https://data.sec.gov/submissions/CIK{self.cik(ticker):010d}.json")

    def document_text(self, url: str) -> str:
        return html_to_text(_get(url, as_json=False, timeout=60))
