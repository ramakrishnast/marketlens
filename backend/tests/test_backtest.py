"""Backtest engine: next-bar execution (no look-ahead), fees/slippage, metrics, custom strategies."""
import math

import numpy as np
import pandas as pd
import pytest

from app.services import backtest as bt


def frame(opens, closes=None):
    opens = np.asarray(opens, dtype=float)
    closes = opens if closes is None else np.asarray(closes, dtype=float)
    idx = pd.bdate_range("2024-01-01", periods=len(opens))
    return pd.DataFrame({"open": opens, "high": np.maximum(opens, closes) * 1.01, "low": np.minimum(opens, closes) * 0.99,
                         "close": closes, "volume": 1e6}, index=idx)


ZERO = bt.Costs(0, 0, 0)


def test_signal_fills_at_next_bar_open():
    df = frame([10, 11, 12, 13, 14], [10.5, 11.5, 12.5, 13.5, 14.5])
    target = pd.Series([1, 1, 1, 1, 1], index=df.index)  # decided at close of bar 0
    res = bt.simulate(df, target, ZERO, 1000)
    t = res.trades[0]
    assert t.entry_date == df.index[1].strftime("%Y-%m-%d")  # not bar 0
    assert t.entry_price == 11  # bar 1 open, not bar 0 close
    assert res.equity.iloc[0] == 1000  # flat on the signal bar
    assert res.equity.iloc[-1] == pytest.approx(1000 / 11 * 14.5)


def test_exit_fills_at_next_open():
    df = frame([10, 10, 12, 15, 9], [10, 11, 14, 8, 9])
    target = pd.Series([1, 1, 0, 0, 0], index=df.index)  # exit decided at close of bar 2
    res = bt.simulate(df, target, ZERO, 1000)
    t = res.trades[0]
    assert (t.entry_price, t.exit_price) == (10, 15)  # entry bar1 open, exit bar3 open
    assert t.exit_date == df.index[3].strftime("%Y-%m-%d")
    assert t.pnl == pytest.approx(1000 / 10 * 15 - 1000)


def test_perfect_foresight_signal_cannot_capture_same_bar_move():
    """A signal that 'knows' today's close went up is acted on tomorrow, so it can't profit from today's move."""
    rng = np.random.default_rng(0)
    closes = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, 300)))
    opens = np.concatenate([[100], closes[:-1]])  # open = previous close, so all moves happen intraday
    df = frame(opens, closes)
    cheat = (df["close"] > df["open"]).astype(int)  # computable only at the close of the bar
    res = bt.simulate(df, cheat, ZERO, 1000)
    oracle = np.prod(np.where(closes > opens, closes / opens, 1.0)) * 1000
    assert res.equity.iloc[-1] < oracle / 10


def test_truncation_invariance():
    rng = np.random.default_rng(1)
    closes = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 500)))
    df = frame(closes, closes)
    for strat in ("sma_cross", "rsi_reversion", "macd_trend", "breakout"):
        full, _ = bt.target_for(df, strat, {"fast": 10, "slow": 30} if strat == "sma_cross" else {})
        part, _ = bt.target_for(df.iloc[:400], strat, {"fast": 10, "slow": 30} if strat == "sma_cross" else {})
        pd.testing.assert_series_equal(full.iloc[:400], part, check_names=False)
        eq_full = bt.simulate(df, full, ZERO).equity.iloc[:400]
        eq_part = bt.simulate(df.iloc[:400], part, ZERO).equity
        pd.testing.assert_series_equal(eq_full, eq_part)


def test_commission_and_slippage_applied_exactly():
    df = frame([100, 100, 110, 120], [100, 105, 115, 120])
    target = pd.Series([1, 1, 0, 0], index=df.index)
    c = bt.Costs(commission_pct=0.001, commission_fixed=1.0, slippage_pct=0.002)
    res = bt.simulate(df, target, c, 10_000)
    t = res.trades[0]
    buy_px = 100 * 1.002
    shares = (10_000 - 1.0) / (buy_px * 1.001)
    buy_fee = shares * buy_px * 0.001 + 1.0
    sell_px = 120 * (1 - 0.002)
    sell_fee = shares * sell_px * 0.001 + 1.0
    assert t.entry_price == pytest.approx(buy_px)
    assert t.exit_price == pytest.approx(sell_px)
    assert t.shares == pytest.approx(shares)
    assert res.fees_paid == pytest.approx(buy_fee + sell_fee)
    final_cash = 10_000 - shares * buy_px - buy_fee + shares * sell_px - sell_fee
    assert res.equity.iloc[-1] == pytest.approx(final_cash)
    free = bt.simulate(df, target, ZERO, 10_000)
    assert free.equity.iloc[-1] > res.equity.iloc[-1]


def test_metrics_on_known_series():
    df = frame([100] * 253, [100 * 1.001 ** i for i in range(253)])
    res = bt.simulate(df, pd.Series(0, index=df.index), ZERO, 1000)
    m = bt.metrics(res, 1000)
    assert m["total_return_pct"] == 0 and m["num_trades"] == 0 and m["exposure_pct"] == 0
    res2 = bt.simulate(df, pd.Series(1, index=df.index), ZERO, 1000)
    m2 = bt.metrics(res2, 1000)
    assert m2["max_drawdown_pct"] == pytest.approx(0, abs=1e-9)
    assert m2["exposure_pct"] == pytest.approx(252 / 253 * 100)


def test_win_rate_and_profit_factor():
    df = frame([10, 10, 12, 12, 12, 10, 10], [10, 10, 12, 12, 12, 10, 10])
    #   trade 1: buy bar1 @10, sell bar3 @12 (+); trade 2: buy bar4 @12, sell bar6 @10 (-)
    target = pd.Series([1, 1, 0, 1, 1, 0, 0], index=df.index)
    res = bt.simulate(df, target, ZERO, 1000)
    m = bt.metrics(res, 1000)
    assert m["closed_trades"] == 2 and m["win_rate_pct"] == 50
    win, loss = res.trades[0].pnl, -res.trades[1].pnl
    assert m["profit_factor"] == pytest.approx(win / loss)


def test_custom_strategy_runs_and_lookahead_is_rejected():
    rng = np.random.default_rng(2)
    closes = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 400)))
    df = frame(closes, closes)
    out = bt.run(df, "custom", code=bt.CUSTOM_TEMPLATE, include_series=False)
    assert out["metrics"]["num_trades"] >= 1
    cheat = """class Peek(Strategy):
    def signals(self, df):
        return (df["close"].shift(-1) > df["close"]).astype(int)
"""
    with pytest.raises(bt.StrategyError, match="Look-ahead"):
        bt.run(df, "custom", code=cheat)
    with pytest.raises(bt.StrategyError):
        bt.run(df, "custom", code="x = 1")


def test_sweep_and_walk_forward_shapes():
    rng = np.random.default_rng(4)
    closes = 100 * np.exp(np.cumsum(rng.normal(0.0003, 0.012, 900)))
    df = frame(closes, closes)
    s = bt.sweep(df, "sma_cross", "fast", [10, 20], "slow", [50, 100, 150])
    assert len(s["grid"]) == 3 and len(s["grid"][0]) == 2 and s["best"]
    w = bt.walk_forward(df, "sma_cross", "fast", [10, 20], "slow", [50, 100], folds=3)
    assert w["split"]["test"][0] > w["split"]["train"][1]  # test strictly after train
    for f in w["folds"]:
        assert f["test"][0] > f["train"][1]
    times = [p["time"] for p in w["stitched_equity"]]
    assert times == sorted(set(times))  # strictly increasing, no overlapping folds
    assert "overfit" in w["warning"].lower() or "noise" in w["warning"].lower()


def test_api_run_has_benchmark_and_label(client):
    r = client.post("/api/backtest/run", json={"ticker": "AAPL", "strategy": "sma_cross", "params": {"fast": 20, "slow": 100},
                                               "start": "2020-01-01"}).json()
    assert "Hypothetical" in r["label"] and "Past performance" in r["label"]
    assert r["benchmark"]["ticker"] == "SPY" and r["benchmark"]["equity"]
    for k in ("cagr_pct", "sharpe", "sortino", "max_drawdown_pct", "win_rate_pct", "profit_factor", "num_trades", "exposure_pct"):
        assert k in r["metrics"]
    assert all(math.isfinite(p["value"]) for p in r["equity"])
