"""Rule-based technical signals. Every signal states its rule and the bar it triggered on.

All rules are causal: a signal on bar t only uses data up to and including bar t's close.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import numpy as np
import pandas as pd

from . import indicators as ind


@dataclass(frozen=True)
class Rule:
    id: str
    label: str
    rule: str
    bias: str  # bullish | bearish | neutral (describes the pattern, not a recommendation)
    fn: Callable[[pd.DataFrame, pd.DataFrame], pd.Series]


def _cross_up(a: pd.Series, b: pd.Series | float) -> pd.Series:
    b_prev = b.shift(1) if isinstance(b, pd.Series) else b
    return (a > b) & (a.shift(1) <= b_prev)


def _cross_down(a: pd.Series, b: pd.Series | float) -> pd.Series:
    b_prev = b.shift(1) if isinstance(b, pd.Series) else b
    return (a < b) & (a.shift(1) >= b_prev)


def _prior_high(df: pd.DataFrame, n: int = 252) -> pd.Series:
    return df["high"].shift(1).rolling(n, min_periods=n).max()


def _prior_low(df: pd.DataFrame, n: int = 252) -> pd.Series:
    return df["low"].shift(1).rolling(n, min_periods=n).min()


def _breakout(df: pd.DataFrame, ix: pd.DataFrame) -> pd.Series:
    hi = _prior_high(df)
    above = df["close"] > hi
    return above & ~above.shift(1, fill_value=False)  # first close above the prior 52-week high


def _breakdown(df: pd.DataFrame, ix: pd.DataFrame) -> pd.Series:
    lo = _prior_low(df)
    below = df["close"] < lo
    return below & ~below.shift(1, fill_value=False)


def _unusual_volume(df: pd.DataFrame, ix: pd.DataFrame) -> pd.Series:
    avg = df["volume"].shift(1).rolling(20, min_periods=20).mean()
    return df["volume"] > 2.0 * avg


RULES: list[Rule] = [
    Rule("golden_cross", "Golden cross", "50-day SMA crosses above the 200-day SMA", "bullish",
         lambda df, ix: _cross_up(ix["sma50"], ix["sma200"])),
    Rule("death_cross", "Death cross", "50-day SMA crosses below the 200-day SMA", "bearish",
         lambda df, ix: _cross_down(ix["sma50"], ix["sma200"])),
    Rule("rsi_overbought", "RSI overbought", "RSI(14) crosses above 70", "bearish",
         lambda df, ix: _cross_up(ix["rsi14"], 70.0)),
    Rule("rsi_oversold", "RSI oversold", "RSI(14) crosses below 30", "bullish",
         lambda df, ix: _cross_down(ix["rsi14"], 30.0)),
    Rule("macd_bull_cross", "MACD bullish crossover", "MACD(12,26) crosses above its 9-day signal line", "bullish",
         lambda df, ix: _cross_up(ix["macd"], ix["macd_signal"])),
    Rule("macd_bear_cross", "MACD bearish crossover", "MACD(12,26) crosses below its 9-day signal line", "bearish",
         lambda df, ix: _cross_down(ix["macd"], ix["macd_signal"])),
    Rule("breakout_52w", "52-week breakout", "Close above the highest high of the prior 252 sessions", "bullish", _breakout),
    Rule("breakdown_52w", "52-week breakdown", "Close below the lowest low of the prior 252 sessions", "bearish", _breakdown),
    Rule("cross_above_200", "Crossed above 200-day MA", "Close crosses above the 200-day SMA", "bullish",
         lambda df, ix: _cross_up(df["close"], ix["sma200"])),
    Rule("cross_below_200", "Crossed below 200-day MA", "Close crosses below the 200-day SMA", "bearish",
         lambda df, ix: _cross_down(df["close"], ix["sma200"])),
    Rule("unusual_volume", "Unusual volume", "Volume more than 2x its prior 20-day average", "neutral", _unusual_volume),
]
RULES_BY_ID = {r.id: r for r in RULES}


def signal_frame(df: pd.DataFrame, ix: pd.DataFrame | None = None) -> pd.DataFrame:
    """Boolean frame: one column per rule, True on bars where the rule triggered."""
    ix = ind.compute_all(df) if ix is None else ix
    return pd.DataFrame({r.id: r.fn(df, ix).fillna(False).astype(bool) for r in RULES}, index=df.index)


def events(df: pd.DataFrame, since_days: int | None = None, ix: pd.DataFrame | None = None) -> list[dict[str, Any]]:
    if len(df) < 30:
        return []
    ix = ind.compute_all(df) if ix is None else ix
    sf = signal_frame(df, ix)
    if since_days:
        sf = sf[sf.index >= df.index[-1] - pd.Timedelta(days=since_days)]
    out = []
    for r in RULES:
        for d in sf.index[sf[r.id]]:
            detail = {}
            if r.id == "unusual_volume":
                avg = df["volume"].shift(1).rolling(20).mean().loc[d]
                detail["volume_ratio"] = round(float(df["volume"].loc[d] / avg), 2)
                detail["day_change_pct"] = round(float(df["close"].pct_change().loc[d] * 100), 2)
            if r.id.startswith("rsi"):
                detail["rsi"] = round(float(ix["rsi14"].loc[d]), 1)
            out.append({"id": r.id, "label": r.label, "rule": r.rule, "bias": r.bias,
                        "date": d.strftime("%Y-%m-%d"), "close": round(float(df["close"].loc[d]), 2), **detail})
    out.sort(key=lambda x: x["date"], reverse=True)
    return out


def state(df: pd.DataFrame, ix: pd.DataFrame | None = None) -> list[dict[str, Any]]:
    """Current technical conditions (descriptive)."""
    ix = ind.compute_all(df) if ix is None else ix
    last = df.index[-1]
    c = float(df["close"].iloc[-1])
    out: list[dict[str, Any]] = []

    def since(mask: pd.Series) -> str | None:
        """Date the current True-streak of mask began."""
        if not bool(mask.iloc[-1]):
            return None
        flips = mask.ne(mask.shift(1))
        return mask.index[flips][-1].strftime("%Y-%m-%d")

    for n in (50, 200):
        ma = ix[f"sma{n}"]
        if pd.notna(ma.iloc[-1]):
            above = df["close"] > ma
            cond = above if above.iloc[-1] else ~above
            out.append({"id": f"vs_sma{n}", "text": f"Price {'above' if above.iloc[-1] else 'below'} the {n}-day SMA ({ma.iloc[-1]:.2f})",
                        "bias": "bullish" if above.iloc[-1] else "bearish", "since": since(cond),
                        "value": round((c / ma.iloc[-1] - 1) * 100, 2)})
    if pd.notna(ix["sma200"].iloc[-1]):
        gc = ix["sma50"] > ix["sma200"]
        cond = gc if gc.iloc[-1] else ~gc
        out.append({"id": "ma_trend", "text": f"50-day SMA {'above' if gc.iloc[-1] else 'below'} 200-day SMA",
                    "bias": "bullish" if gc.iloc[-1] else "bearish", "since": since(cond)})
    r = ix["rsi14"].iloc[-1]
    if pd.notna(r):
        zone = "overbought (>70)" if r > 70 else "oversold (<30)" if r < 30 else "neutral (30-70)"
        out.append({"id": "rsi", "text": f"RSI(14) {r:.1f}: {zone}", "value": round(float(r), 1),
                    "bias": "bearish" if r > 70 else "bullish" if r < 30 else "neutral"})
    m, s = ix["macd"].iloc[-1], ix["macd_signal"].iloc[-1]
    if pd.notna(m) and pd.notna(s):
        above = ix["macd"] > ix["macd_signal"]
        cond = above if above.iloc[-1] else ~above
        out.append({"id": "macd", "text": f"MACD {'above' if m > s else 'below'} signal line (hist {m - s:+.2f})",
                    "bias": "bullish" if m > s else "bearish", "since": since(cond)})
    hi, lo = df["high"].iloc[-252:].max(), df["low"].iloc[-252:].min()
    pos = (c - lo) / (hi - lo) * 100 if hi > lo else np.nan
    out.append({"id": "range52", "text": f"{pos:.0f}% of the 52-week range ({lo:.2f} – {hi:.2f})", "value": round(float(pos), 1),
                "bias": "neutral"})
    a = ix["atr14"].iloc[-1]
    if pd.notna(a):
        out.append({"id": "atr", "text": f"ATR(14) {a:.2f} ({a / c * 100:.1f}% of price): typical daily range",
                    "value": round(float(a), 2), "bias": "neutral"})
    for o in out:
        o.setdefault("as_of", last.strftime("%Y-%m-%d"))
    return out


def chart_markers(evts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Lightweight Charts markers (oldest first)."""
    shapes = {"bullish": ("belowBar", "arrowUp", "#22c55e"), "bearish": ("aboveBar", "arrowDown", "#ef4444"),
              "neutral": ("aboveBar", "circle", "#f59e0b")}
    short = {"golden_cross": "GC", "death_cross": "DC", "rsi_overbought": "RSI>70", "rsi_oversold": "RSI<30",
             "macd_bull_cross": "MACD↑", "macd_bear_cross": "MACD↓", "breakout_52w": "52wH", "breakdown_52w": "52wL",
             "cross_above_200": "↑200", "cross_below_200": "↓200", "unusual_volume": "Vol"}
    out = []
    for e in sorted(evts, key=lambda x: x["date"]):
        pos, shape, color = shapes[e["bias"]]
        out.append({"time": e["date"], "position": pos, "shape": shape, "color": color, "text": short.get(e["id"], e["id"])})
    return out
