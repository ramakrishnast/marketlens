"""Scenario outlook: the factual alternative to "buy/sell on date X".

It answers "what would change the picture, and when?" with:
* dated catalysts (earnings, macro releases, FOMC, options expiration)
* trigger levels: the next-session close that would fire each technical rule
* historical base rates: what followed each signal in this ticker's own history
* bull-case and bear-case observations, each with its date and source

Nothing here is a recommendation or a prediction: base rates describe the past.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from . import events
from . import indicators as ind
from . import prices, signals

HORIZONS = (5, 20, 60)
DISCLAIMER = ("Scenario analysis, not a recommendation. Base rates describe what happened after past signals in "
              "this ticker; small samples and changing market regimes make them unreliable as forecasts.")


def rsi_trigger_prices(close: pd.Series, period: int = 14) -> dict[str, float | None]:
    """Close needed on the next bar for RSI(period) to reach 70 / 30, given Wilder smoothing."""
    delta = close.diff()
    ag = ind._wilder(delta.clip(lower=0).iloc[1:], period).iloc[-1]
    al = ind._wilder((-delta).clip(lower=0).iloc[1:], period).iloc[-1]
    last = float(close.iloc[-1])
    k = period - 1
    out: dict[str, float | None] = {"rsi_70": None, "rsi_30": None}
    if np.isnan(ag) or np.isnan(al):
        return out
    # up move d: (ag*k + d) / (al*k) = 70/30
    d_up = (7 / 3) * al * k - ag * k
    if d_up > 0:
        out["rsi_70"] = round(last + d_up, 2)
    # down move d: (ag*k) / (al*k + d) = 30/70
    d_dn = (7 / 3) * ag * k - al * k
    if d_dn > 0:
        out["rsi_30"] = round(last - d_dn, 2)
    return out


def ma_cross_trigger_price(close: pd.Series, fast: int = 50, slow: int = 200) -> float | None:
    """Close X on the next bar at which SMA(fast) == SMA(slow)."""
    if len(close) < slow:
        return None
    s_fast = float(close.iloc[-(fast - 1):].sum())
    s_slow = float(close.iloc[-(slow - 1):].sum())
    # (s_fast + X)/fast = (s_slow + X)/slow  ->  X (1/fast - 1/slow) = s_slow/slow - s_fast/fast
    denom = 1 / fast - 1 / slow
    x = (s_slow / slow - s_fast / fast) / denom
    return round(x, 2) if x > 0 else None


def trigger_levels(df: pd.DataFrame, ix: pd.DataFrame) -> list[dict[str, Any]]:
    c = df["close"]
    last = float(c.iloc[-1])
    out: list[dict[str, Any]] = []

    def add(level, label, rule, bias):
        if level is None or not np.isfinite(level) or level <= 0:
            return
        dist = (level / last - 1) * 100
        reachable = abs(dist) <= 25
        out.append({"price": round(float(level), 2), "distance_pct": round(dist, 2), "label": label, "rule": rule,
                    "bias": bias, "reachable_next_session": reachable})

    hi = float(df["high"].iloc[-252:].max())
    lo = float(df["low"].iloc[-252:].min())
    add(hi, "52-week breakout", "Close above the 52-week high", "bullish")
    add(lo, "52-week breakdown", "Close below the 52-week low", "bearish")
    for n in (50, 200):
        ma = ix[f"sma{n}"].iloc[-1]
        if pd.notna(ma):
            above = last > ma
            add(float(ma), f"{'Lose' if above else 'Reclaim'} the {n}-day MA",
                f"Close {'below' if above else 'above'} SMA{n} (≈ today's value)", "bearish" if above else "bullish")
    rt = rsi_trigger_prices(c)
    add(rt["rsi_70"], "RSI reaches 70 (overbought)", "Next close that would lift RSI(14) to 70", "bearish")
    add(rt["rsi_30"], "RSI reaches 30 (oversold)", "Next close that would drop RSI(14) to 30", "bullish")
    x = ma_cross_trigger_price(c)
    if x is not None:
        golden = ix["sma50"].iloc[-1] < ix["sma200"].iloc[-1]
        add(x, "Golden cross" if golden else "Death cross",
            f"Next close at which the 50-day SMA {'crosses above' if golden else 'crosses below'} the 200-day SMA",
            "bullish" if golden else "bearish")
    for lv in ind.support_resistance(df):
        add(lv["price"], f"{lv['kind'].title()} level ({lv['touches']} touches)",
            f"Horizontal level from clustered swing {'highs' if lv['kind'] == 'resistance' else 'lows'}, last touched {lv['last_touch']}",
            "neutral")
    out.sort(key=lambda x: x["price"], reverse=True)
    return out


def base_rates(df: pd.DataFrame, sf: pd.DataFrame) -> dict[str, Any]:
    """Forward returns after each signal vs the unconditional baseline over the same history."""
    c = df["close"]
    fwd = {h: (c.shift(-h) / c - 1) * 100 for h in HORIZONS}
    baseline = {h: {"mean": round(float(fwd[h].mean()), 2), "pct_positive": round(float((fwd[h] > 0).mean() * 100), 1),
                    "n": int(fwd[h].notna().sum())} for h in HORIZONS}
    rows = []
    for r in signals.RULES:
        dates = sf.index[sf[r.id]]
        if len(dates) == 0:
            continue
        stats = {}
        for h in HORIZONS:
            v = fwd[h].loc[dates].dropna()
            if len(v) == 0:
                continue
            stats[str(h)] = {"n": int(len(v)), "mean": round(float(v.mean()), 2), "median": round(float(v.median()), 2),
                             "pct_positive": round(float((v > 0).mean() * 100), 1),
                             "vs_baseline": round(float(v.mean() - baseline[h]["mean"]), 2)}
        if stats:
            n = max(s["n"] for s in stats.values())
            rows.append({"id": r.id, "label": r.label, "rule": r.rule, "bias": r.bias, "occurrences": int(len(dates)),
                         "last": dates[-1].strftime("%Y-%m-%d"), "forward": stats,
                         "reliability": "very low (n<5)" if n < 5 else "low (n<15)" if n < 15 else "moderate"})
    return {"horizons_days": list(HORIZONS), "baseline": baseline, "signals": rows,
            "history": {"start": df.index[0].strftime("%Y-%m-%d"), "end": df.index[-1].strftime("%Y-%m-%d"), "bars": len(df)}}


def observations(df: pd.DataFrame, ix: pd.DataFrame, recent: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    bull, bear, neutral = [], [], []
    for s in signals.state(df, ix):
        item = {"text": s["text"] + (f" since {s['since']}" if s.get("since") else ""), "date": s.get("since") or s["as_of"],
                "source": "MarketLens technicals (daily bars)"}
        {"bullish": bull, "bearish": bear}.get(s["bias"], neutral).append(item)
    for e in recent[:8]:
        item = {"text": f"{e['label']} on {e['date']} ({e['rule']})", "date": e["date"], "source": "MarketLens signals"}
        {"bullish": bull, "bearish": bear}.get(e["bias"], neutral).append(item)
    return {"bull": bull, "bear": bear, "neutral": neutral}


def outlook(ticker: str) -> dict[str, Any]:
    t = ticker.upper()
    df = prices.daily(t)
    if len(df) < 60:
        return {"ticker": t, "error": "Not enough price history for an outlook."}
    ix = ind.compute_all(df)
    sf = signals.signal_frame(df, ix)
    recent = signals.events(df, since_days=60, ix=ix)
    rates = base_rates(df, sf)
    active_ids = {e["id"] for e in signals.events(df, since_days=14, ix=ix)}
    for r in rates["signals"]:
        r["recently_triggered"] = r["id"] in active_ids
    obs = observations(df, ix, recent)
    extra = _extra_observations(t)
    for k in ("bull", "bear", "neutral"):
        obs[k] += extra.get(k, [])
    try:
        reactions = events.earnings_reactions(t, df)
    except Exception:
        reactions = {"n": 0, "moves": []}
    return {
        "ticker": t,
        "price": float(df["close"].iloc[-1]),
        "as_of": prices.as_of(t),
        "catalysts": events.upcoming(t, days_ahead=45),
        "earnings_reaction": reactions,
        "trigger_levels": trigger_levels(df, ix),
        "base_rates": rates,
        "cases": obs,
        "disclaimer": DISCLAIMER,
    }


def _extra_observations(ticker: str) -> dict[str, list[dict[str, Any]]]:
    """Observations contributed by other modules (fundamentals, sentiment, crowd, Congress) when available."""
    out: dict[str, list[dict[str, Any]]] = {"bull": [], "bear": [], "neutral": []}
    import importlib

    for mod in ("fundamentals_obs", "news", "social", "congress"):
        try:
            m = importlib.import_module(f".{mod}", __package__)
            fn = getattr(m, "observations", None)
            if fn is None:
                continue
            for k, v in fn(ticker).items():
                out.setdefault(k, []).extend(v)
        except Exception:
            continue
    return out
