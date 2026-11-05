"""Static ticker universes used across the app."""
from __future__ import annotations

DEMO_TICKERS = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMD", "GOOGL", "AMZN"]

INDEX_STRIP = [
    {"ticker": "SPY", "label": "S&P 500"},
    {"ticker": "QQQ", "label": "Nasdaq 100"},
    {"ticker": "IWM", "label": "Russell 2000"},
    {"ticker": "DIA", "label": "Dow 30"},
    {"ticker": "^VIX", "label": "VIX"},
    {"ticker": "^TNX", "label": "10Y yield"},
]
MARKET_TICKERS = [x["ticker"] for x in INDEX_STRIP]

SECTOR_ETFS = {
    "XLK": "Technology", "XLF": "Financials", "XLE": "Energy", "XLV": "Health Care",
    "XLY": "Consumer Discr.", "XLP": "Consumer Staples", "XLI": "Industrials", "XLB": "Materials",
    "XLU": "Utilities", "XLRE": "Real Estate", "XLC": "Communication",
}
SECTOR_ETF_LIST = list(SECTOR_ETFS)

# Hand-curated peer groups (industry peers). Unknown tickers fall back to SEC SIC-code matching
# among tickers already in the local cache.
PEERS: dict[str, list[str]] = {
    "AAPL": ["MSFT", "GOOGL", "AMZN", "META"],
    "MSFT": ["AAPL", "GOOGL", "AMZN", "ORCL"],
    "GOOGL": ["META", "MSFT", "AMZN", "AAPL"],
    "META": ["GOOGL", "MSFT", "AMZN", "NFLX"],
    "AMZN": ["MSFT", "GOOGL", "WMT", "AAPL"],
    "NVDA": ["AMD", "AVGO", "INTC", "QCOM"],
    "AMD": ["NVDA", "INTC", "AVGO", "QCOM"],
    "INTC": ["AMD", "NVDA", "TXN", "QCOM"],
    "AVGO": ["NVDA", "AMD", "QCOM", "TXN"],
    "QCOM": ["AVGO", "NVDA", "AMD", "TXN"],
    "TXN": ["INTC", "QCOM", "AVGO", "AMD"],
    "MU": ["NVDA", "AMD", "INTC", "AVGO"],
    "ORCL": ["MSFT", "CRM", "AMZN", "GOOGL"],
    "TSLA": ["GM", "F", "TM", "RIVN"],
    "NFLX": ["DIS", "META", "GOOGL", "AMZN"],
    "JPM": ["BAC", "WFC", "C", "GS"],
    "XOM": ["CVX", "COP", "SHEL", "BP"],
}
PEER_EXTRA = ["META", "AVGO", "INTC", "QCOM", "TXN", "MU", "ORCL", "TSLA", "NFLX", "WMT"]

MACRO_SERIES = {
    "DFF": "Fed funds rate (%)",
    "DGS10": "10-year Treasury yield (%)",
    "DGS2": "2-year Treasury yield (%)",
    "T10Y2Y": "10Y minus 2Y spread (pp)",
    "CPIAUCSL": "CPI, all items (index)",
    "UNRATE": "Unemployment rate (%)",
}
