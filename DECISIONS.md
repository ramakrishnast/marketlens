# Decisions

Choices made while building MarketLens, with the reasoning. Newest sections are added at the bottom.

## Product scope

| # | Decision | Why |
|---|----------|-----|
| 1 | **No buy/sell calls.** The "buy/sell dates" request is implemented as a **Scenario outlook**: dated catalysts (earnings, CPI/jobs/FOMC), the price levels that would trigger each rule, and historical base rates ("when RSI < 30 in this stock, the next 20 days averaged X%, n=…"). Bull and bear cases are both shown, with no rating. | Your brief says MarketLens never presents output as advice; you chose this option when asked. Point predictions of buy/sell dates aren't reliable, but base rates and catalyst dates are factual and checkable. |
| 2 | **Crowd talk** comes from ApeWisdom (Reddit mention counts, no key), StockTwits (public stream, user-tagged bull/bear) and the official Reddit API (optional free key). It is always labeled as unverified crowd opinion. | You picked all three. Reddit blocks anonymous API access, so its posts need a free "script" app key; ApeWisdom still covers r/wallstreetbets mention trends without one. |
| 3 | **AI = OpenAI only, model `gpt-6-luna`** (`OPENAI_API_KEY`). The original spec's `ANTHROPIC_API_KEY` is not used. | Your instruction mid-build. The model name is configurable via `OPENAI_MODEL` but defaults to, and is documented as, `gpt-6-luna`. |
| 4 | **Congress Trades** section (leaderboard, member-vs-S&P performance, copy-on-disclosure strategy, filings feed, per-stock view), modeled on the screenshot you shared, with original design and naming. | Your request mid-build. |
| 5 | **TradingAgents** (TauricResearch, Apache-2.0) is used as a *design pattern*, not a dependency: an "AI Research Desk" runs analyst agents (technical, fundamentals, news, crowd, Congress) over MarketLens's own tools, then a bull vs bear debate and risk perspectives, and ends in a scenario memo with **no BUY/SELL/HOLD rating**. Its trader and portfolio-manager agents are deliberately omitted. | Its final output is a trade decision, which conflicts with decision #1. Its dependency stack (LangGraph plus its own data vendors) would duplicate our provider layer. Running the pattern natively keeps every number sourced from the app's cached data. |

## Runtime and packaging

| # | Decision | Why |
|---|----------|-----|
| 6 | **Docker-only** run path: one image (FastAPI serves the built React app), `make run` / `make demo` / `make test` wrap `docker compose`. Native `make dev-*` targets exist only for development. | You chose Docker only. A single container keeps "fresh clone → make run" to one step. The port binds to 127.0.0.1 only. |
| 7 | **FinBERT is an optional extra** (`make finbert`, ~2 GB PyTorch). Default sentiment is a compact finance lexicon scorer; FinBERT is used automatically when installed. | You chose this. Keeps the default install fast. |
| 8 | **SQLite only** (no DuckDB). Prices live in a `(ticker, date)` table; all other sources go in a JSON key/value cache with `fetched_at` and `source`. | The data volume (tens of tickers × 10y daily) is small; a single file is simpler to back up and inspect. |
| 9 | **DATA_MODE = auto / live / mock.** `auto` (default) tries live and falls back to bundled sample data, which the UI labels "sample data". `mock` uses only bundled data (tests, offline). | Free sources rate-limit and break. The app should degrade visibly, not crash. |
| 10 | **Bundled sample data is real**, captured from the live sources by `scripts/capture_fixtures.py` (prices from yfinance, trimmed EDGAR company facts and submissions, Google News RSS headlines, FRED CSV, ApeWisdom, StockTwits, House PTR filings). | Realistic demo and tests. News and social keep only headline/snippet/link. |

## Data

| # | Decision | Why |
|---|----------|-----|
| 11 | **Indicators implemented in pandas** (not pandas-ta / TA-Lib). Wilder smoothing for RSI/ATR, SMA-seeded EMA, population-std Bollinger Bands. Tests check them against the StockCharts reference spreadsheets. | pandas-ta lags pandas 3 / NumPy 2, and TA-Lib needs a C build. ~150 lines are easy to verify. |
| 12 | **yfinance** requests are batched (40 tickers), retried with exponential backoff, cached, and refreshed incrementally (only the last ~week is re-downloaded). TTL 15 min (`PRICE_TTL_MINUTES`). | yfinance is unofficial and rate-limited. |
| 13 | yfinance `.info` / calendar (forward P/E, next earnings) is best-effort. When it fails, the **next earnings date is estimated** from the company's historical 8-K Item 2.02 ("Results of Operations") filing dates on EDGAR, and labeled "est.". | Yahoo's quote-summary endpoint often returns 401 "Invalid Crumb"; EDGAR is reliable. |
| 14 | **EDGAR fundamentals**: each metric has a priority list of XBRL concepts merged per period. Missing quarters (Q4, and YTD cash-flow items) are **derived by differencing** cumulative periods with the same start. Derived EPS is flagged as an approximation. | Q4 is never filed separately, and 10-Q cash flows are year-to-date. |
| 15 | **Peers**: a hand-curated peer map for common large caps (`services/universe.py`); other tickers fall back to SEC SIC-code matches among locally cached tickers. | No free "industry peers" API. |
| 16 | **FRED without a key** uses the public `fredgraph.csv` endpoint. The upcoming release calendar (CPI, jobs, GDP…) needs `FRED_API_KEY`; without it, a static FOMC schedule from `config/macro_calendar.json` is used. | Macro charts work without a key. |
| 17 | **News**: Finnhub / Alpha Vantage when keys exist, plus Google News RSS search (no key) and optional company IR feeds (`config/ir_feeds.json`). SEC 8-Ks come from the filings tab. Only headline, source, url, time and a ≤280-char snippet are stored. | Your brief's copyright constraint. Yahoo's per-ticker RSS now returns 404. |
| 18 | **Congress data**: House Clerk yearly disclosure index + parsing the text layer of electronic PTR PDFs. Paper (scanned) filings are listed with a link but not parsed. **Senate is TODO**: the eFD site needs an interactive terms-acceptance session, and the free community dataset stops in 2020. | Only free, official, current source. |

## Backtesting

| # | Decision | Why |
|---|----------|-----|
| 19 | **Own ~300-line engine** instead of backtesting.py / vectorbt. Long/flat only, fractional shares, fills at the next bar's open with slippage, commission as % plus fixed $ per fill. | backtesting.py is AGPL-licensed and vectorbt is heavy. A small engine makes the no-look-ahead and fee rules explicit and exactly testable (`tests/test_backtest.py`). |
| 20 | **Custom strategies** are Python classes (`class X(Strategy): def signals(self, df)`) run with `exec` inside the container, enabled by `ALLOW_CUSTOM_STRATEGIES`. Before running, the engine recomputes signals on truncated history and **rejects any strategy whose past signals change** (look-ahead detection). | Local single-user tool: you run your own code. The truncation check catches `shift(-1)`, centered windows and the like. |
| 21 | Signals are computed on the **full price history** (so a 200-day MA is valid on day 1 of the test window), then traded only inside the chosen date range. | Avoids artificial warm-up gaps; indicators are causal, so this adds no look-ahead. |
| 22 | Walk-forward = a 70/30 train/test split **plus** anchored walk-forward folds (re-optimized before each fold, out-of-sample equity stitched). The heatmap is explicitly labeled in-sample. | Shows how much of an optimized result survives on unseen data. |
