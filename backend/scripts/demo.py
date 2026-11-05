"""`make demo`: load the sample watchlist and warm every page's data.

Works in any DATA_MODE: live sources are used when reachable, bundled sample data otherwise.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services import prices, watchlists  # noqa: E402
from app.services.universe import DEMO_TICKERS, MARKET_TICKERS, SECTOR_ETF_LIST  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("demo")


def step(name: str, fn) -> None:
    try:
        fn()
        log.info("✓ %s", name)
    except Exception as e:  # keep going: the demo should populate as much as possible
        log.warning("✗ %s: %s", name, e)


def main() -> None:
    wl = watchlists.create("Demo", DEMO_TICKERS)
    watchlists.create("Semis", ["NVDA", "AMD", "AVGO", "INTC", "QCOM", "MU"])
    log.info("watchlist '%s' with %s", wl["name"], ", ".join(wl["tickers"]))
    step("prices", lambda: prices.refresh(DEMO_TICKERS + MARKET_TICKERS + SECTOR_ETF_LIST + ["AVGO", "INTC", "QCOM", "MU"], force=True))

    import importlib
    for mod, fn in [
        ("app.services.fundamentals", "warm"), ("app.services.news", "warm"), ("app.services.social", "warm"),
        ("app.services.macro", "warm"), ("app.services.congress", "warm"), ("app.services.briefing", "warm"),
    ]:
        try:
            m = importlib.import_module(mod)
        except ModuleNotFoundError:
            continue
        step(mod.rsplit(".", 1)[-1], lambda m=m, fn=fn: getattr(m, fn)(DEMO_TICKERS))
    log.info("Demo ready.")


if __name__ == "__main__":
    main()
