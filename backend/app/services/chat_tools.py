"""Tools the Ask-AI model can call. Every number in an answer must come from one of these.

Each tool returns a JSON-able dict with `as_of` (data dates) and `sources` (label + url) so the answer
can cite what it used. Schemas follow the OpenAI function-calling format.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

import pandas as pd

from . import congress, events, fundamentals, macro, market, news, outlook, prices, signals, social, watchlists
from . import indicators as ind

log = logging.getLogger(__name__)

PERIOD_DAYS = {"1w": 7, "1m": 31, "3m": 92, "6m": 183, "ytd": None, "1y": 366, "2y": 731, "5y": 1827}


def _src(label: str, url: str | None = None) -> dict[str, Any]:
    return {"label": label, "url": url}


def _price_src(t: str) -> dict[str, Any]:
    return _src(f"Yahoo Finance daily prices ({t}) via yfinance", f"https://finance.yahoo.com/quote/{t}")


def tool_get_quote(tickers: list[str]) -> dict[str, Any]:
    tickers = [t.upper() for t in tickers][:12]
    prices.refresh(tickers)
    out = {}
    for t in tickers:
        df = prices.daily(t, refresh_if_stale=False)
        if df.empty:
            out[t] = {"error": "no price data"}
            continue
        snap = prices.snapshot(df)
        r = ind.rsi(df["close"]).dropna()
        snap["rsi14"] = float(r.iloc[-1]) if len(r) else None
        out[t] = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in snap.items()}
    return {"quotes": out, "as_of": {t: prices.as_of(t)["last_bar"] for t in tickers}, "sources": [_price_src(t) for t in tickers]}


def tool_get_price_history(ticker: str, period: str = "1m", start: str | None = None, end: str | None = None) -> dict[str, Any]:
    t = ticker.upper()
    df = prices.daily(t)
    if df.empty:
        return {"error": f"no price data for {t}"}
    last = df.index[-1]
    if start:
        s = pd.Timestamp(start)
    elif period == "ytd":
        s = pd.Timestamp(year=last.year, month=1, day=1)
    else:
        s = last - pd.Timedelta(days=PERIOD_DAYS.get(period, 31))
    e = pd.Timestamp(end) if end else last
    w = df[(df.index >= s) & (df.index <= e)]
    if w.empty:
        return {"error": "no bars in that window"}
    prev = df[df.index < w.index[0]]
    base = float(prev["close"].iloc[-1]) if len(prev) else float(w["open"].iloc[0])
    daily = w["close"].pct_change()
    daily.iloc[0] = w["close"].iloc[0] / base - 1
    big = daily.abs().sort_values(ascending=False).head(5)
    step = max(1, len(w) // 60)
    return {
        "ticker": t, "start": w.index[0].strftime("%Y-%m-%d"), "end": w.index[-1].strftime("%Y-%m-%d"),
        "start_reference_close": round(base, 4), "end_close": round(float(w["close"].iloc[-1]), 4),
        "return_pct": round((float(w["close"].iloc[-1]) / base - 1) * 100, 3),
        "high": round(float(w["high"].max()), 4), "high_date": w["high"].idxmax().strftime("%Y-%m-%d"),
        "low": round(float(w["low"].min()), 4), "low_date": w["low"].idxmin().strftime("%Y-%m-%d"),
        "avg_volume": float(w["volume"].mean()),
        "largest_daily_moves": [{"date": d.strftime("%Y-%m-%d"), "change_pct": round(float(v) * 100, 2),
                                 "volume_vs_20d_avg": round(float(df["volume"].loc[d] / df["volume"].shift(1).rolling(20).mean().loc[d]), 2)
                                 if pd.notna(df["volume"].shift(1).rolling(20).mean().loc[d]) else None}
                                for d, v in daily.loc[big.index].items()],
        "closes_sampled": [{"date": d.strftime("%Y-%m-%d"), "close": round(float(c), 2)} for d, c in w["close"].iloc[::step].items()],
        "as_of": prices.as_of(t)["last_bar"], "sources": [_price_src(t)],
    }


def tool_get_technicals(ticker: str) -> dict[str, Any]:
    t = ticker.upper()
    df = prices.daily(t)
    if df.empty:
        return {"error": f"no price data for {t}"}
    ix = ind.compute_all(df)
    last = ix.iloc[-1]
    vals = {k: (round(float(last[k]), 4) if pd.notna(last[k]) else None) for k in
            ("sma20", "sma50", "sma200", "ema20", "rsi14", "macd", "macd_signal", "bb_upper", "bb_lower", "atr14")}
    return {"ticker": t, "close": float(df["close"].iloc[-1]), "indicators": vals, "state": signals.state(df, ix),
            "signals_last_90d": signals.events(df, since_days=90, ix=ix)[:15],
            "support_resistance": ind.support_resistance(df), "as_of": prices.as_of(t)["last_bar"],
            "sources": [_price_src(t), _src("MarketLens indicator calculations (daily closes)")]}


def tool_get_fundamentals(ticker: str) -> dict[str, Any]:
    t = ticker.upper()
    f = fundamentals.facts(t)
    v = fundamentals.valuation(t, f)
    keep = ("period_end", "revenue", "gross_margin", "operating_margin", "net_margin", "net_income", "eps_diluted", "fcf",
            "total_debt", "cash", "shares_outstanding")
    q = [{k: r.get(k) for k in keep} for r in f["quarterly"][-6:]]
    a = [{k: r.get(k) for k in keep} for r in f["annual"][-4:]]
    sub = fundamentals.submissions(t)
    return {"ticker": t, "company": f["entity"], "valuation": {k: v[k] for k in (
        "price", "price_date", "market_cap", "enterprise_value", "pe_ttm", "forward_pe", "ps_ttm", "ev_ebitda", "peg",
        "fcf_yield", "gross_margin", "operating_margin", "net_margin", "growth_yoy_pct", "total_debt", "cash")},
            "ttm": v["ttm"], "quarterly": q, "annual": a, "as_of": {"latest_period": f["as_of"]["last_period"], "price_date": v["price_date"]},
            "sources": [_src(f"SEC EDGAR company facts (CIK {sub['cik']})", f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={sub['cik']}"),
                        _price_src(t)],
            "notes": "TTM = sum of last four quarters. Margins are fractions (0.45 = 45%). Forward P/E from Yahoo when available."}


def tool_compare_companies(tickers: list[str]) -> dict[str, Any]:
    tickers = [t.upper() for t in tickers][:8]
    prices.refresh(tickers)
    rows, srcs, asof = {}, [], {}
    for t in tickers:
        try:
            v = fundamentals.valuation(t)
            rows[t] = {k: v[k] for k in ("market_cap", "pe_ttm", "forward_pe", "ps_ttm", "ev_ebitda", "peg", "gross_margin",
                                         "operating_margin", "net_margin", "fcf_margin", "growth_yoy_pct")}
            asof[t] = v["as_of"].get("last_period")
            srcs.append(_src(f"SEC EDGAR company facts ({t})", f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={t}"))
        except Exception as e:
            rows[t] = {"error": str(e)}
    return {"comparison": rows, "as_of": asof, "sources": srcs,
            "notes": "Ratios on trailing-twelve-month SEC data and cached prices; margins are fractions."}


def tool_get_news(ticker: str, days: int = 7) -> dict[str, Any]:
    d = news.headlines(ticker, days=days, limit=25)
    items = [{"headline": i["headline"], "source": i["source"], "url": i["url"], "published_at": i["published_at"],
              "sentiment": i["sentiment"], "snippet": i["snippet"]} for i in d["items"]]
    s = news.sentiment_score(ticker, days=min(days, 30))
    return {"ticker": ticker.upper(), "headlines": items, "sentiment_avg": s, "as_of": d["as_of"].get("fetched_at"),
            "sources": [_src(f"{i['source']}: {i['headline'][:80]}", i["url"]) for i in items[:10]],
            "notes": "Headlines/snippets only. Sentiment in [-1,1], scored locally."}


def tool_get_filings(ticker: str, forms: list[str] | None = None) -> dict[str, Any]:
    f = fundamentals.filings(ticker, tuple(forms or ["10-K", "10-Q", "8-K"]), limit=15)
    return {"ticker": ticker.upper(), "filings": f["filings"], "as_of": f["as_of"].get("fetched_at"),
            "sources": [_src(f"SEC {x['form']} filed {x['filing_date']}", x["url"]) for x in f["filings"][:8]]}


SCREENS: dict[str, tuple[str, Callable[[dict[str, Any]], bool]]] = {
    "above_sma200": ("Close above 200-day SMA", lambda r: (r.get("dist_sma200_pct") or -1) > 0),
    "below_sma200": ("Close below 200-day SMA", lambda r: r.get("dist_sma200_pct") is not None and r["dist_sma200_pct"] < 0),
    "above_sma50": ("Close above 50-day SMA", lambda r: (r.get("dist_sma50_pct") or -1) > 0),
    "below_sma50": ("Close below 50-day SMA", lambda r: r.get("dist_sma50_pct") is not None and r["dist_sma50_pct"] < 0),
    "rsi_overbought": ("RSI(14) above 70", lambda r: (r.get("rsi14") or 0) > 70),
    "rsi_oversold": ("RSI(14) below 30", lambda r: (r.get("rsi14") or 100) < 30),
    "near_52w_high": ("Within 5% of 52-week high", lambda r: r.get("price") and r.get("high_52w") and r["price"] >= 0.95 * r["high_52w"]),
    "near_52w_low": ("Within 5% of 52-week low", lambda r: r.get("price") and r.get("low_52w") and r["price"] <= 1.05 * r["low_52w"]),
    "unusual_volume": ("Volume > 2x 50-day average", lambda r: (r.get("volume_vs_avg") or 0) > 2),
}


def tool_screen_watchlist(condition: str, watchlist: str | None = None) -> dict[str, Any]:
    wls = watchlists.list_watchlists()
    if watchlist:
        wls = [w for w in wls if w["name"].lower() == watchlist.lower()] or wls
    tickers = sorted({t for w in wls for t in w["tickers"]})
    if condition not in SCREENS:
        return {"error": f"unknown condition; use one of {list(SCREENS)}"}
    label, fn = SCREENS[condition]
    rows = market.watchlist_table(tickers)
    keep = ("ticker", "price", "dist_sma50_pct", "dist_sma200_pct", "rsi14", "high_52w", "low_52w", "volume_vs_avg")
    matches = [{k: r.get(k) for k in keep} for r in rows if not r.get("error") and fn(r)]
    others = [r["ticker"] for r in rows if not r.get("error") and not fn(r)]
    return {"condition": label, "watchlists": [w["name"] for w in wls], "matches": matches, "not_matching": others,
            "as_of": {r["ticker"]: (r.get("as_of") or {}).get("last_bar") for r in rows},
            "sources": [_src("Yahoo Finance daily prices via yfinance; MarketLens indicators")]}


def tool_get_watchlists() -> dict[str, Any]:
    return {"watchlists": watchlists.list_watchlists(), "as_of": None, "sources": [_src("MarketLens watchlists")]}


def tool_get_outlook(ticker: str) -> dict[str, Any]:
    o = outlook.outlook(ticker)
    if o.get("error"):
        return o
    br = [{"signal": s["label"], "recently_triggered": s["recently_triggered"], "fwd_20d": s["forward"].get("20"),
           "reliability": s["reliability"]} for s in o["base_rates"]["signals"]]
    return {"ticker": o["ticker"], "price": o["price"], "catalysts": o["catalysts"], "earnings_reaction": {k: o["earnings_reaction"].get(k) for k in ("n", "avg_abs_move_pct", "max_abs_move_pct")},
            "trigger_levels": o["trigger_levels"][:10], "base_rates_20d": br, "baseline_20d": o["base_rates"]["baseline"][20],
            "bull_case": o["cases"]["bull"], "bear_case": o["cases"]["bear"], "disclaimer": o["disclaimer"],
            "as_of": o["as_of"]["last_bar"], "sources": [_price_src(o["ticker"]), _src("SEC EDGAR 8-K Item 2.02 (earnings dates)"),
                                                         _src("Federal Reserve FOMC calendar", "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm")]}


def tool_get_crowd(ticker: str) -> dict[str, Any]:
    c = social.crowd(ticker)
    return {"ticker": c["ticker"], "mentions": c["mentions"], "stocktwits_tags": c["stocktwits_tags"], "model_sentiment": c["model_sentiment"],
            "sample_posts": [{"community": p["community"], "snippet": p["snippet"], "url": p["url"], "user_label": p["user_label"]} for p in c["posts"][:8]],
            "label": c["label"], "as_of": c["as_of"]["mentions"].get("fetched_at"),
            "sources": [_src("ApeWisdom (Reddit mention counts)", "https://apewisdom.io/"), _src("StockTwits public stream", f"https://stocktwits.com/symbol/{c['ticker']}")]}


def tool_get_congress_trades(ticker: str | None = None, member: str | None = None) -> dict[str, Any]:
    if member:
        d = congress.member_detail(member, "1y")
        trades = d.get("trades", [])[:25]
        return {"member": member, "party": d.get("party"), "stats_1y": d.get("stats"), "trades": trades, "as_of": d.get("prices_through"),
                "sources": [_src(f"House PTR {t['disclosure_date']}", t["url"]) for t in trades[:8]], "label": congress.LABEL}
    if ticker:
        d = congress.for_ticker(ticker)
        return {"ticker": ticker.upper(), "trades": d["trades"][:30], "as_of": None,
                "sources": [_src(f"House PTR {t['disclosure_date']} ({t['member']})", t["url"]) for t in d["trades"][:8]]}
    b = congress.leaderboard("6m")
    return {"leaderboard_6m_top10": b["rows"][:10], "all_congress": b["all"], "sp500_pct": b["sp500_pct"], "label": b["label"],
            "as_of": b.get("as_of"), "sources": [_src("House Clerk financial disclosures", "https://disclosures-clerk.house.gov/FinancialDisclosure")]}


def tool_get_macro() -> dict[str, Any]:
    s = macro.summary()
    ev = events.macro_events(30)
    return {"series": [{k: r.get(k) for k in ("id", "label", "value", "date", "change_1m", "change_1y", "yoy_pct")} for r in s["series"]],
            "upcoming_events": ev, "as_of": {r["id"]: r.get("date") for r in s["series"]},
            "sources": [_src(f"FRED {r['id']}", f"https://fred.stlouisfed.org/series/{r['id']}") for r in s["series"]]}


def tool_render_chart(tickers: list[str], period: str = "6m", normalize: bool = True, title: str | None = None) -> dict[str, Any]:
    """Builds chart data server-side (so the numbers are real) for the UI to render inline."""
    tickers = [t.upper() for t in tickers][:6]
    prices.refresh(tickers)
    series = []
    for t in tickers:
        h = tool_get_price_history(t, period)
        if h.get("error"):
            continue
        df = prices.daily(t, refresh_if_stale=False)
        w = df[df.index >= pd.Timestamp(h["start"])]["close"]
        base = float(w.iloc[0])
        series.append({"name": t, "data": [{"time": d.strftime("%Y-%m-%d"), "value": round(float(v / base * 100 - 100 if normalize else v), 3)} for d, v in w.items()]})
    return {"rendered": True, "visual": {"type": "chart", "title": title or f"{', '.join(tickers)}: {period}{' (% change)' if normalize else ''}",
                                          "percent": normalize, "series": series},
            "summary": {s["name"]: s["data"][-1]["value"] for s in series if s["data"]},
            "as_of": {t: prices.as_of(t)["last_bar"] for t in tickers}, "sources": [_price_src(t) for t in tickers]}


def tool_render_table(title: str, columns: list[str], rows: list[list[Any]]) -> dict[str, Any]:
    return {"rendered": True, "visual": {"type": "table", "title": title, "columns": columns[:10], "rows": [r[:10] for r in rows[:30]]},
            "as_of": None, "sources": []}


def _obj(props: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


TICKER = {"type": "string", "description": "US stock/ETF ticker symbol, e.g. AAPL"}
TICKERS = {"type": "array", "items": {"type": "string"}, "description": "Ticker symbols"}

TOOLS: dict[str, dict[str, Any]] = {
    "get_quote": {"fn": tool_get_quote, "description": "Latest daily snapshot for tickers: price, day change, volume vs average, 52-week range, distance from 50/200-day SMA, returns (1w/1m/3m/1y/YTD), RSI.",
                  "parameters": _obj({"tickers": TICKERS}, ["tickers"])},
    "get_price_history": {"fn": tool_get_price_history, "description": "Price performance over a period or date range: return, high/low with dates, largest daily moves (with volume ratio), sampled closes. Use for 'why did X drop this week' to find the exact move dates.",
                          "parameters": _obj({"ticker": TICKER, "period": {"type": "string", "enum": list(PERIOD_DAYS), "description": "Lookback window (ignored if start given)"},
                                              "start": {"type": ["string", "null"], "description": "YYYY-MM-DD"}, "end": {"type": ["string", "null"], "description": "YYYY-MM-DD"}}, ["ticker", "period", "start", "end"])},
    "get_technicals": {"fn": tool_get_technicals, "description": "Indicator values (SMA/EMA/RSI/MACD/Bollinger/ATR), current technical state, rule-based signals in the last 90 days with dates, support/resistance levels.",
                       "parameters": _obj({"ticker": TICKER}, ["ticker"])},
    "get_fundamentals": {"fn": tool_get_fundamentals, "description": "SEC EDGAR fundamentals: valuation ratios (P/E, forward P/E, P/S, EV/EBITDA, PEG), TTM figures, margins, growth, last 6 quarters and 4 years.",
                         "parameters": _obj({"ticker": TICKER}, ["ticker"])},
    "compare_companies": {"fn": tool_compare_companies, "description": "Side-by-side valuation, margins and growth for several companies.",
                          "parameters": _obj({"tickers": TICKERS}, ["tickers"])},
    "get_news": {"fn": tool_get_news, "description": "Recent headlines (with source URLs and per-headline sentiment) and average sentiment for a ticker.",
                 "parameters": _obj({"ticker": TICKER, "days": {"type": "integer", "description": "Lookback in days (1-30)"}}, ["ticker", "days"])},
    "get_filings": {"fn": tool_get_filings, "description": "Recent SEC filings (10-K, 10-Q, 8-K with item numbers) with links.",
                    "parameters": _obj({"ticker": TICKER, "forms": {"type": ["array", "null"], "items": {"type": "string", "enum": ["10-K", "10-Q", "8-K"]}}}, ["ticker", "forms"])},
    "screen_watchlist": {"fn": tool_screen_watchlist, "description": "Screen the user's watchlist tickers for a technical condition.",
                         "parameters": _obj({"condition": {"type": "string", "enum": list(SCREENS)},
                                             "watchlist": {"type": ["string", "null"], "description": "Watchlist name; null = all watchlists"}}, ["condition", "watchlist"])},
    "get_watchlists": {"fn": tool_get_watchlists, "description": "The user's watchlists and their tickers.", "parameters": _obj({}, [])},
    "get_outlook": {"fn": tool_get_outlook, "description": "Scenario outlook: upcoming dated catalysts (earnings, FOMC, CPI), trigger price levels for technical rules, historical base rates after similar signals, bull/bear observations. Use for buy/sell or timing questions (explain, never recommend).",
                    "parameters": _obj({"ticker": TICKER}, ["ticker"])},
    "get_crowd": {"fn": tool_get_crowd, "description": "Crowd chatter: Reddit (r/wallstreetbets etc.) mention ranks, StockTwits bull/bear tags, sample posts. Unverified opinion.",
                  "parameters": _obj({"ticker": TICKER}, ["ticker"])},
    "get_congress_trades": {"fn": tool_get_congress_trades, "description": "US House members' disclosed stock trades: by ticker, by member, or (both null) the 6-month leaderboard.",
                            "parameters": _obj({"ticker": {"type": ["string", "null"]}, "member": {"type": ["string", "null"]}}, ["ticker", "member"])},
    "get_macro": {"fn": tool_get_macro, "description": "Latest macro data (fed funds, 10Y/2Y yields, 10Y-2Y spread, CPI YoY, unemployment) and upcoming macro events.",
                  "parameters": _obj({}, [])},
    "render_chart": {"fn": tool_render_chart, "description": "Show an inline price chart in the answer (data is fetched server-side). Use when a visual comparison helps.",
                     "parameters": _obj({"tickers": TICKERS, "period": {"type": "string", "enum": list(PERIOD_DAYS)}, "normalize": {"type": "boolean", "description": "Plot % change from start"},
                                         "title": {"type": ["string", "null"]}}, ["tickers", "period", "normalize", "title"])},
    "render_table": {"fn": tool_render_table, "description": "Show a small inline table in the answer. Only put numbers in it that came from other tool results.",
                     "parameters": _obj({"title": {"type": "string"}, "columns": {"type": "array", "items": {"type": "string"}},
                                         "rows": {"type": "array", "items": {"type": "array", "items": {"type": ["string", "number", "null"]}}}}, ["title", "columns", "rows"])},
}


def openai_tools() -> list[dict[str, Any]]:
    return [{"type": "function", "function": {"name": name, "description": spec["description"], "parameters": spec["parameters"], "strict": True}}
            for name, spec in TOOLS.items()]


def call(name: str, args: dict[str, Any]) -> dict[str, Any]:
    spec = TOOLS.get(name)
    if spec is None:
        return {"error": f"unknown tool {name}"}
    clean = {k: v for k, v in (args or {}).items() if v is not None}
    try:
        return spec["fn"](**clean)
    except TypeError as e:
        return {"error": f"bad arguments for {name}: {e}"}
    except Exception as e:  # tools must never crash the chat
        log.warning("tool %s failed: %s", name, e)
        return {"error": f"{name} failed: {e}"}
