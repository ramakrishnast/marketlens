"""Technical indicators, implemented directly in pandas (tested against published reference values).

Conventions (match TradingView / StockCharts):
* RSI and ATR use Wilder smoothing (alpha = 1/period), seeded with a simple average.
* EMA is seeded with the SMA of the first `period` values (as StockCharts does).
* Bollinger Bands use the population standard deviation (ddof=0).
* All indicators are causal: the value at bar t only uses data up to and including bar t.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def sma(s: pd.Series, period: int) -> pd.Series:
    return s.rolling(period, min_periods=period).mean()


def ema(s: pd.Series, period: int) -> pd.Series:
    s = s.astype(float)
    out = pd.Series(np.nan, index=s.index)
    valid = s.dropna()
    if len(valid) < period:
        return out
    alpha = 2.0 / (period + 1)
    vals = valid.to_numpy()
    res = np.full(len(vals), np.nan)
    res[period - 1] = vals[:period].mean()
    for i in range(period, len(vals)):
        res[i] = alpha * vals[i] + (1 - alpha) * res[i - 1]
    out.loc[valid.index] = res
    return out


def _wilder(s: pd.Series, period: int) -> pd.Series:
    """Wilder's smoothing seeded with the simple mean of the first `period` values."""
    vals = s.to_numpy(dtype=float)
    res = np.full(len(vals), np.nan)
    first = np.argmax(~np.isnan(vals)) if (~np.isnan(vals)).any() else len(vals)
    start = first + period - 1
    if start >= len(vals):
        return pd.Series(res, index=s.index)
    res[start] = np.nanmean(vals[first : start + 1])
    for i in range(start + 1, len(vals)):
        res[i] = (res[i - 1] * (period - 1) + vals[i]) / period
    return pd.Series(res, index=s.index)


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = _wilder(gain.iloc[1:], period).reindex(close.index)
    avg_loss = _wilder(loss.iloc[1:], period).reindex(close.index)
    rs = avg_gain / avg_loss
    out = 100 - 100 / (1 + rs)
    out[(avg_loss == 0) & avg_gain.notna()] = 100.0
    return out


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    line = ema(close, fast) - ema(close, slow)
    sig = ema(line, signal)
    return pd.DataFrame({"macd": line, "signal": sig, "hist": line - sig})


def bollinger(close: pd.Series, period: int = 20, mult: float = 2.0) -> pd.DataFrame:
    mid = sma(close, period)
    sd = close.rolling(period, min_periods=period).std(ddof=0)
    return pd.DataFrame({"middle": mid, "upper": mid + mult * sd, "lower": mid - mult * sd})


def true_range(df: pd.DataFrame) -> pd.Series:
    prev = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()], axis=1).max(axis=1)
    tr.iloc[0] = df["high"].iloc[0] - df["low"].iloc[0]
    return tr


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    return _wilder(true_range(df), period)


def vwap(df: pd.DataFrame) -> pd.Series:
    """Session VWAP for intraday bars, reset each calendar day. Uses typical price (H+L+C)/3."""
    tp = (df["high"] + df["low"] + df["close"]) / 3
    day = df.index.normalize()
    pv = (tp * df["volume"]).groupby(day).cumsum()
    vol = df["volume"].groupby(day).cumsum()
    return pv / vol.replace(0, np.nan)


def support_resistance(df: pd.DataFrame, lookback: int = 250, window: int = 5, tolerance: float = 0.015,
                       max_levels: int = 6) -> list[dict[str, Any]]:
    """Horizontal levels from clustered swing highs/lows.

    A swing high is a bar whose high is the max of the surrounding +/- `window` bars (lows likewise).
    Pivots within `tolerance` (1.5%) of each other are merged; levels touched more often rank higher.
    Only uses completed pivots (needs `window` bars after), so it is causal with a `window`-bar lag.
    """
    d = df.iloc[-lookback:]
    if len(d) < window * 2 + 1:
        return []
    highs, lows = d["high"], d["low"]
    span = 2 * window + 1
    piv_hi = highs[(highs == highs.rolling(span, center=True).max())]
    piv_lo = lows[(lows == lows.rolling(span, center=True).min())]
    pivots = sorted([(float(p), "high", i) for i, p in piv_hi.items()] + [(float(p), "low", i) for i, p in piv_lo.items()])
    clusters: list[list[tuple[float, str, Any]]] = []
    for p in pivots:
        if clusters and abs(p[0] / np.mean([x[0] for x in clusters[-1]]) - 1) <= tolerance:
            clusters[-1].append(p)
        else:
            clusters.append([p])
    last = float(d["close"].iloc[-1])
    levels = []
    for c in clusters:
        price = float(np.mean([x[0] for x in c]))
        levels.append({
            "price": round(price, 2), "touches": len(c),
            "kind": "resistance" if price > last else "support",
            "last_touch": max(x[2] for x in c).strftime("%Y-%m-%d"),
        })
    levels.sort(key=lambda x: (-x["touches"], abs(x["price"] / last - 1)))
    chosen = sorted(levels[:max_levels], key=lambda x: x["price"])
    return chosen


def compute_all(df: pd.DataFrame) -> pd.DataFrame:
    """All daily indicators as columns aligned to df's index."""
    c = df["close"]
    out = pd.DataFrame(index=df.index)
    for n in (20, 50, 200):
        out[f"sma{n}"] = sma(c, n)
        out[f"ema{n}"] = ema(c, n)
    out["rsi14"] = rsi(c, 14)
    m = macd(c)
    out["macd"], out["macd_signal"], out["macd_hist"] = m["macd"], m["signal"], m["hist"]
    bb = bollinger(c)
    out["bb_upper"], out["bb_middle"], out["bb_lower"] = bb["upper"], bb["middle"], bb["lower"]
    out["atr14"] = atr(df, 14)
    out["vol_avg20"] = df["volume"].rolling(20, min_periods=20).mean()
    return out


def series_payload(index: pd.Index, s: pd.Series, digits: int = 4) -> list[dict[str, Any]]:
    """[{time, value}] skipping NaN, for Lightweight Charts."""
    out = []
    for t, v in zip(index, s.to_numpy()):
        if v is None or (isinstance(v, float) and np.isnan(v)):
            continue
        out.append({"time": t.strftime("%Y-%m-%d") if hasattr(t, "strftime") else t, "value": round(float(v), digits)})
    return out
