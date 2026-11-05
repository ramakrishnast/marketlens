# MarketLens

A local US stock and ETF market analyzer: watchlists, interactive charts with technical indicators, and more features being added in the build order.
MarketLens is a personal research tool: it never places trades and nothing it shows is investment advice.

## Quick start

```bash
cp .env.example .env      # optional: add keys (see below)
make run                  # builds and starts the Docker container at http://localhost:8000
make demo                 # loads a sample watchlist (SPY QQQ AAPL MSFT NVDA AMD GOOGL AMZN)
make test                 # runs the test-suite in the container (offline, sample data)
```

Full documentation is written at the end of the build.
