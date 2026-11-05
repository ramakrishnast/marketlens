"""Daily-bar backtesting engine: long/flat, next-bar execution, commission + slippage, SPY benchmark.

Execution model (no look-ahead):
* A strategy outputs a target position (0 = flat, 1 = long) computed from data up to and including the
  close of bar t.
* The order fills at the OPEN of bar t+1, at open × (1 + slippage) for buys and open × (1 − slippage) for
  sells. Commission = commission_pct × notional + commission_fixed per fill.
* Equity is marked to market at each close. Fractional shares; all available cash is invested on entry.

Every result is hypothetical: past performance does not predict future results.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

import numpy as np
import pandas as pd

from ..config import get_settings
from . import indicators as ind

LABEL = "Hypothetical results. Past performance does not predict future results."
OVERFIT_WARNING = (
    "Optimizing parameters on past data tends to find settings that fit noise. Expect out-of-sample results to be "
    "worse than in-sample results; prefer parameter regions that are good across the heatmap over single peaks."
)


@dataclass
class Costs:
    commission_pct: float = 0.0005  # 5 bps of notional per fill
    commission_fixed: float = 0.0  # $ per fill
    slippage_pct: float = 0.0005  # 5 bps adverse price per fill


@dataclass
class Trade:
    entry_date: str
    entry_price: float
    shares: float
    exit_date: str | None = None
    exit_price: float | None = None
    fees: float = 0.0
    pnl: float | None = None
    return_pct: float | None = None
    bars_held: int = 0
    open: bool = True


@dataclass
class Result:
    equity: pd.Series
    position: pd.Series
    trades: list[Trade] = field(default_factory=list)
    fees_paid: float = 0.0


def simulate(df: pd.DataFrame, target: pd.Series, costs: Costs | None = None, initial: float = 10_000.0) -> Result:
    costs = costs or Costs()
    target = target.reindex(df.index).fillna(0).astype(float)
    opens, closes = df["open"].to_numpy(float), df["close"].to_numpy(float)
    want = (target.to_numpy() > 0.5).astype(int)
    n = len(df)
    cash, shares = float(initial), 0.0
    equity = np.empty(n)
    pos = np.zeros(n)
    trades: list[Trade] = []
    fees_total = 0.0
    entry_i = None
    for i in range(n):
        if i > 0:
            desired = want[i - 1]  # decided at the previous close, filled at this open
            if desired == 1 and shares == 0:
                price = opens[i] * (1 + costs.slippage_pct)
                investable = cash - costs.commission_fixed
                if investable > 0:
                    shares = investable / (price * (1 + costs.commission_pct))
                    fee = shares * price * costs.commission_pct + costs.commission_fixed
                    cash -= shares * price + fee
                    fees_total += fee
                    entry_i = i
                    trades.append(Trade(entry_date=df.index[i].strftime("%Y-%m-%d"), entry_price=price, shares=shares, fees=fee))
            elif desired == 0 and shares > 0:
                price = opens[i] * (1 - costs.slippage_pct)
                proceeds = shares * price
                fee = proceeds * costs.commission_pct + costs.commission_fixed
                cash += proceeds - fee
                fees_total += fee
                t = trades[-1]
                t.exit_date, t.exit_price, t.open = df.index[i].strftime("%Y-%m-%d"), price, False
                t.fees += fee
                cost_basis = t.shares * t.entry_price
                t.pnl = proceeds - cost_basis - t.fees
                t.return_pct = t.pnl / (cost_basis + (t.fees - fee)) * 100
                t.bars_held = i - (entry_i or i)
                shares = 0.0
        pos[i] = 1.0 if shares > 0 else 0.0
        equity[i] = cash + shares * closes[i]
    if trades and trades[-1].open:
        t = trades[-1]
        mv = t.shares * closes[-1]
        t.pnl = mv - t.shares * t.entry_price - t.fees
        t.return_pct = t.pnl / (t.shares * t.entry_price) * 100
        t.bars_held = n - 1 - (entry_i or n - 1)
    return Result(pd.Series(equity, index=df.index), pd.Series(pos, index=df.index), trades, fees_total)


def metrics(res: Result, initial: float) -> dict[str, Any]:
    eq = res.equity
    rets = eq.pct_change().dropna()
    years = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
    total = eq.iloc[-1] / initial - 1
    cagr = (eq.iloc[-1] / initial) ** (1 / years) - 1 if eq.iloc[-1] > 0 else -1.0
    sd = rets.std(ddof=0)
    sharpe = rets.mean() / sd * math.sqrt(252) if sd > 0 else None
    downside = math.sqrt((np.minimum(rets, 0) ** 2).mean()) if len(rets) else 0
    sortino = rets.mean() / downside * math.sqrt(252) if downside > 0 else None
    dd = eq / eq.cummax() - 1
    closed = [t for t in res.trades if not t.open]
    wins = [t for t in closed if (t.pnl or 0) > 0]
    gross_win = sum(t.pnl for t in wins)
    gross_loss = -sum(t.pnl for t in closed if (t.pnl or 0) < 0)
    return {
        "total_return_pct": total * 100,
        "cagr_pct": cagr * 100,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown_pct": float(dd.min() * 100),
        "max_drawdown_date": dd.idxmin().strftime("%Y-%m-%d") if len(dd) else None,
        "volatility_pct": float(sd * math.sqrt(252) * 100) if sd > 0 else 0.0,
        "num_trades": len(res.trades),
        "closed_trades": len(closed),
        "win_rate_pct": len(wins) / len(closed) * 100 if closed else None,
        "profit_factor": gross_win / gross_loss if gross_loss > 0 else (None if not wins else float("inf")),
        "avg_trade_pct": float(np.mean([t.return_pct for t in closed])) if closed else None,
        "exposure_pct": float(res.position.mean() * 100),
        "fees_paid": res.fees_paid,
        "final_equity": float(eq.iloc[-1]),
        "years": years,
    }


# ---------------------------------------------------------------------------------------------
# Strategies: each returns a target position series (0/1) computed causally from df.

def _sma_cross(df: pd.DataFrame, fast: int = 50, slow: int = 200) -> pd.Series:
    return (ind.sma(df["close"], int(fast)) > ind.sma(df["close"], int(slow))).astype(int)


def _rsi_reversion(df: pd.DataFrame, period: int = 14, lower: float = 30, upper: float = 70) -> pd.Series:
    r = ind.rsi(df["close"], int(period)).to_numpy()
    out = np.zeros(len(df))
    holding = 0
    for i, v in enumerate(r):
        if np.isnan(v):
            out[i] = holding
            continue
        if holding == 0 and v < lower:
            holding = 1
        elif holding == 1 and v > upper:
            holding = 0
        out[i] = holding
    return pd.Series(out, index=df.index)


def _macd_trend(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.Series:
    m = ind.macd(df["close"], int(fast), int(slow), int(signal))
    return (m["macd"] > m["signal"]).astype(int)


def _breakout(df: pd.DataFrame, entry: int = 55, exit: int = 20) -> pd.Series:
    hi = df["high"].shift(1).rolling(int(entry)).max().to_numpy()
    lo = df["low"].shift(1).rolling(int(exit)).min().to_numpy()
    c = df["close"].to_numpy()
    out = np.zeros(len(df))
    holding = 0
    for i in range(len(df)):
        if holding == 0 and not np.isnan(hi[i]) and c[i] > hi[i]:
            holding = 1
        elif holding == 1 and not np.isnan(lo[i]) and c[i] < lo[i]:
            holding = 0
        out[i] = holding
    return pd.Series(out, index=df.index)


def _buy_hold(df: pd.DataFrame) -> pd.Series:
    return pd.Series(1, index=df.index)


PRESETS: dict[str, dict[str, Any]] = {
    "sma_cross": {"label": "SMA crossover", "fn": _sma_cross, "params": {"fast": 50, "slow": 200},
                  "description": "Long while the fast SMA is above the slow SMA, flat otherwise.",
                  "sweep": {"fast": [10, 20, 30, 50, 75, 100], "slow": [100, 150, 200, 250]}},
    "rsi_reversion": {"label": "RSI mean reversion", "fn": _rsi_reversion, "params": {"period": 14, "lower": 30, "upper": 70},
                      "description": "Buy when RSI drops below `lower`, sell when it rises above `upper`.",
                      "sweep": {"lower": [20, 25, 30, 35, 40], "upper": [55, 60, 65, 70, 75, 80]}},
    "macd_trend": {"label": "MACD trend", "fn": _macd_trend, "params": {"fast": 12, "slow": 26, "signal": 9},
                   "description": "Long while MACD is above its signal line.",
                   "sweep": {"fast": [8, 12, 16, 20], "slow": [21, 26, 34, 40, 50]}},
    "breakout": {"label": "Breakout (Donchian)", "fn": _breakout, "params": {"entry": 55, "exit": 20},
                 "description": "Buy on a close above the prior `entry`-day high; sell on a close below the prior `exit`-day low.",
                 "sweep": {"entry": [20, 40, 55, 80, 100], "exit": [10, 20, 30, 40]}},
    "buy_hold": {"label": "Buy and hold", "fn": _buy_hold, "params": {}, "description": "Always long.", "sweep": {}},
}

CUSTOM_TEMPLATE = '''class MyStrategy(Strategy):
    """Long when price is above its 100-day SMA and RSI is not overbought."""
    params = {"ma": 100, "rsi_max": 75}

    def signals(self, df):
        # df has columns open, high, low, close, volume (daily). Return 1 (long) or 0 (flat) per bar.
        # Use only data up to each bar: the engine checks for look-ahead and trades at the NEXT open.
        above = df["close"] > ind.sma(df["close"], self.p["ma"])
        calm = ind.rsi(df["close"], 14) < self.p["rsi_max"]
        return (above & calm).astype(int)
'''


class Strategy:
    """Base class for user strategies. Override `signals(self, df) -> pd.Series of 0/1`."""

    params: dict[str, Any] = {}

    def __init__(self, **overrides: Any) -> None:
        self.p = {**self.params, **overrides}

    def signals(self, df: pd.DataFrame) -> pd.Series:  # pragma: no cover - abstract
        raise NotImplementedError


class StrategyError(ValueError):
    pass


def load_custom(code: str) -> type[Strategy]:
    if not get_settings().allow_custom_strategies:
        raise StrategyError("Custom strategies are disabled (ALLOW_CUSTOM_STRATEGIES=false).")
    ns: dict[str, Any] = {"Strategy": Strategy, "pd": pd, "np": np, "ind": ind}
    try:
        exec(compile(code, "<strategy>", "exec"), ns)  # local, single-user tool: runs the user's own code
    except Exception as e:
        raise StrategyError(f"Your strategy code failed to load: {type(e).__name__}: {e}") from e
    classes = [v for v in ns.values() if isinstance(v, type) and issubclass(v, Strategy) and v is not Strategy]
    if not classes:
        raise StrategyError("Define a class that subclasses Strategy, e.g. `class MyStrategy(Strategy):`")
    return classes[-1]


def _clean_target(raw: Any, df: pd.DataFrame) -> pd.Series:
    if not isinstance(raw, pd.Series):
        raise StrategyError("signals() must return a pandas Series aligned to df.index")
    s = raw.reindex(df.index).astype(float).fillna(0)
    if not s.isin([0.0, 1.0]).all():
        s = (s > 0.5).astype(float)
    return s


def check_lookahead(fn: Callable[[pd.DataFrame], pd.Series], df: pd.DataFrame, checks: int = 3) -> None:
    """Recompute signals on truncated history; any change on overlapping bars means the strategy peeks ahead."""
    full = _clean_target(fn(df), df)
    n = len(df)
    for k in np.linspace(n * 0.5, n - 5, checks).astype(int):
        part_df = df.iloc[:k]
        part = _clean_target(fn(part_df), part_df)
        diff = (full.iloc[:k] != part).to_numpy()
        if diff.any():
            d = df.index[int(np.argmax(diff))].strftime("%Y-%m-%d")
            raise StrategyError(f"Look-ahead detected: the signal on {d} changes when later data is removed. "
                                "Use only past/current bars (e.g. no shift(-1), no centered windows).")


def target_for(df: pd.DataFrame, strategy: str, params: dict[str, Any] | None = None, code: str | None = None,
               verify: bool = True) -> tuple[pd.Series, dict[str, Any]]:
    params = dict(params or {})
    if strategy == "custom":
        if not code:
            raise StrategyError("Provide strategy code")
        cls = load_custom(code)
        inst = cls(**params)

        def fn(d):
            return inst.signals(d.copy())

        if verify:
            check_lookahead(fn, df)
        try:
            return _clean_target(fn(df), df), inst.p
        except StrategyError:
            raise
        except Exception as e:
            raise StrategyError(f"signals() raised {type(e).__name__}: {e}") from e
    if strategy not in PRESETS:
        raise StrategyError(f"Unknown strategy {strategy}")
    preset = PRESETS[strategy]
    p = {**preset["params"], **{k: v for k, v in params.items() if k in preset["params"]}}
    return _clean_target(preset["fn"](df, **p), df), p


def _slice(df: pd.DataFrame, start: str | None, end: str | None) -> pd.DataFrame:
    if start:
        df = df[df.index >= pd.Timestamp(start)]
    if end:
        df = df[df.index <= pd.Timestamp(end)]
    return df


def run(df: pd.DataFrame, strategy: str, params: dict[str, Any] | None = None, *, start: str | None = None,
        end: str | None = None, costs: Costs | None = None, initial: float = 10_000.0, code: str | None = None,
        benchmark: pd.DataFrame | None = None, include_series: bool = True) -> dict[str, Any]:
    """Signals are computed on the full history (warm-up for long indicators), then traded only inside [start, end]."""
    costs = costs or Costs()
    target, used = target_for(df, strategy, params, code)
    win = _slice(df, start, end)
    if len(win) < 30:
        raise StrategyError("Need at least 30 bars in the selected date range")
    res = simulate(win, target.reindex(win.index), costs, initial)
    out: dict[str, Any] = {"strategy": strategy, "params": used, "costs": asdict(costs), "initial": initial,
                           "start": win.index[0].strftime("%Y-%m-%d"), "end": win.index[-1].strftime("%Y-%m-%d"),
                           "metrics": metrics(res, initial), "label": LABEL}
    bench_res = None
    if benchmark is not None and not benchmark.empty:
        bwin = benchmark.reindex(win.index).ffill().dropna()
        if len(bwin) > 30:
            bench_res = simulate(bwin, pd.Series(1, index=bwin.index), costs, initial)
            out["benchmark"] = {"ticker": "SPY", "label": "SPY buy-and-hold (same costs)", "metrics": metrics(bench_res, initial)}
    if include_series:
        out["equity"] = [{"time": d.strftime("%Y-%m-%d"), "value": round(float(v), 2)} for d, v in res.equity.items()]
        if bench_res is not None:
            out["benchmark"]["equity"] = [{"time": d.strftime("%Y-%m-%d"), "value": round(float(v), 2)} for d, v in bench_res.equity.items()]
        dd = res.equity / res.equity.cummax() - 1
        out["drawdown"] = [{"time": d.strftime("%Y-%m-%d"), "value": round(float(v) * 100, 2)} for d, v in dd.items()]
        out["trades"] = [asdict(t) for t in res.trades]
        out["markers"] = _markers(res.trades)
    return out


def _markers(trades: list[Trade]) -> list[dict[str, Any]]:
    m = []
    for t in trades:
        m.append({"time": t.entry_date, "position": "belowBar", "shape": "arrowUp", "color": "#22c55e", "text": "Buy"})
        if t.exit_date:
            m.append({"time": t.exit_date, "position": "aboveBar", "shape": "arrowDown", "color": "#ef4444", "text": "Sell"})
    return m


def sweep(df: pd.DataFrame, strategy: str, x_param: str, x_values: list[Any], y_param: str, y_values: list[Any],
          base_params: dict[str, Any] | None = None, *, start: str | None = None, end: str | None = None,
          costs: Costs | None = None, metric: str = "sharpe", code: str | None = None) -> dict[str, Any]:
    if len(x_values) * len(y_values) > 225:
        raise StrategyError("Grid too large (max 15 x 15)")
    grid = []
    for y in y_values:
        row = []
        for x in x_values:
            p = {**(base_params or {}), x_param: x, y_param: y}
            try:
                r = run(df, strategy, p, start=start, end=end, costs=costs, code=code, include_series=False)
                v = r["metrics"].get(metric)
                row.append(None if v is None or (isinstance(v, float) and not math.isfinite(v)) else round(float(v), 3))
            except StrategyError:
                row.append(None)
        grid.append(row)
    flat = [(v, x_values[i], y_values[j]) for j, r in enumerate(grid) for i, v in enumerate(r) if v is not None]
    best = None
    if flat:
        # every supported metric is "higher is better" (drawdown is negative, so closer to 0 wins)
        v, bx, by = max(flat, key=lambda t: t[0])
        best = {"value": v, x_param: bx, y_param: by}
    return {"x_param": x_param, "x_values": x_values, "y_param": y_param, "y_values": y_values, "metric": metric,
            "grid": grid, "best": best, "warning": OVERFIT_WARNING, "label": LABEL}


def walk_forward(df: pd.DataFrame, strategy: str, x_param: str, x_values: list[Any], y_param: str, y_values: list[Any],
                 base_params: dict[str, Any] | None = None, *, start: str | None = None, end: str | None = None,
                 costs: Costs | None = None, train_frac: float = 0.7, folds: int = 4, metric: str = "sharpe",
                 code: str | None = None, benchmark: pd.DataFrame | None = None) -> dict[str, Any]:
    """(1) Simple split: optimize on the first `train_frac`, test the winner on the rest.
    (2) Anchored walk-forward: for each fold, optimize on all data before it and trade the fold out-of-sample."""
    win = _slice(df, start, end)
    if len(win) < 300:
        raise StrategyError("Walk-forward needs at least ~300 bars")
    cut = win.index[int(len(win) * train_frac)]
    train_end = (cut - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    test_start = cut.strftime("%Y-%m-%d")
    s0 = win.index[0].strftime("%Y-%m-%d")
    e0 = win.index[-1].strftime("%Y-%m-%d")
    opt = sweep(df, strategy, x_param, x_values, y_param, y_values, base_params, start=s0, end=train_end, costs=costs,
                metric=metric, code=code)
    if not opt["best"]:
        raise StrategyError("No valid parameter combination on the training window")
    best_p = {**(base_params or {}), x_param: opt["best"][x_param], y_param: opt["best"][y_param]}
    ins = run(df, strategy, best_p, start=s0, end=train_end, costs=costs, code=code, include_series=False)
    oos = run(df, strategy, best_p, start=test_start, end=e0, costs=costs, code=code, benchmark=benchmark)
    # anchored walk-forward
    edges = np.linspace(int(len(win) * 0.4), len(win) - 1, folds + 1).astype(int)
    fold_rows, stitched = [], []
    equity_level = 10_000.0
    for k in range(folds):
        tr_end = win.index[edges[k] - 1].strftime("%Y-%m-%d")
        last = edges[k + 1] if k == folds - 1 else edges[k + 1] - 1  # folds don't share a boundary day
        te_s, te_e = win.index[edges[k]].strftime("%Y-%m-%d"), win.index[last].strftime("%Y-%m-%d")
        o = sweep(df, strategy, x_param, x_values, y_param, y_values, base_params, start=s0, end=tr_end, costs=costs,
                  metric=metric, code=code)
        if not o["best"]:
            continue
        p = {**(base_params or {}), x_param: o["best"][x_param], y_param: o["best"][y_param]}
        r = run(df, strategy, p, start=te_s, end=te_e, costs=costs, code=code, initial=equity_level)
        equity_level = r["metrics"]["final_equity"]
        stitched += r["equity"]
        fold_rows.append({"train": [s0, tr_end], "test": [te_s, te_e], "params": {x_param: p[x_param], y_param: p[y_param]},
                          "in_sample": o["best"]["value"], "out_of_sample": r["metrics"].get(metric),
                          "oos_return_pct": r["metrics"]["total_return_pct"]})
    ins_v, oos_v = ins["metrics"].get(metric), oos["metrics"].get(metric)
    degradation = None
    if ins_v not in (None, 0) and oos_v is not None:
        degradation = (1 - oos_v / ins_v) * 100 if ins_v > 0 else None
    return {
        "metric": metric, "split": {"train": [s0, train_end], "test": [test_start, e0], "best_params": best_p,
                                    "in_sample": ins["metrics"], "out_of_sample": oos["metrics"],
                                    "degradation_pct": degradation},
        "oos_run": oos, "heatmap_train": opt, "folds": fold_rows, "stitched_equity": stitched,
        "warning": OVERFIT_WARNING, "label": LABEL,
    }


def presets_payload() -> list[dict[str, Any]]:
    return [{"id": k, "label": v["label"], "params": v["params"], "description": v["description"], "sweep": v["sweep"]}
            for k, v in PRESETS.items()] + [{"id": "custom", "label": "Custom Python class", "params": {}, "template": CUSTOM_TEMPLATE,
                                              "description": "Write a Strategy subclass. Runs locally; checked for look-ahead.",
                                              "sweep": {}, "enabled": get_settings().allow_custom_strategies}]


__all__ = ["Costs", "simulate", "metrics", "run", "sweep", "walk_forward", "PRESETS", "Strategy", "StrategyError"]
