import numpy as np
import pandas as pd
import pytest

from app.services import indicators as ind
from app.services import outlook, signals


def _frame(close, vol=None):
    close = np.asarray(close, dtype=float)
    idx = pd.bdate_range("2022-01-03", periods=len(close))
    return pd.DataFrame({"open": close, "high": close * 1.005, "low": close * 0.995, "close": close,
                         "volume": vol if vol is not None else np.full(len(close), 1e6)}, index=idx)


def test_golden_cross_detected_on_the_crossing_bar():
    # long decline then strong rally -> 50d SMA crosses above 200d SMA once
    close = np.concatenate([np.linspace(200, 100, 260), np.linspace(100, 260, 200)])
    df = _frame(close)
    ix = ind.compute_all(df)
    sf = signals.signal_frame(df, ix)
    days = sf.index[sf["golden_cross"]]
    assert len(days) == 1
    d = days[0]
    i = df.index.get_loc(d)
    assert ix["sma50"].iloc[i] > ix["sma200"].iloc[i]
    assert ix["sma50"].iloc[i - 1] <= ix["sma200"].iloc[i - 1]


def test_unusual_volume_uses_prior_average_only():
    vol = np.full(60, 1e6)
    vol[45] = 3e6
    df = _frame(np.linspace(100, 110, 60), vol)
    evts = [e for e in signals.events(df) if e["id"] == "unusual_volume"]
    assert [e["date"] for e in evts] == [df.index[45].strftime("%Y-%m-%d")]
    assert evts[0]["volume_ratio"] == pytest.approx(3.0)


def test_breakout_only_on_first_close_above_prior_high():
    close = np.concatenate([np.full(260, 100.0) + np.sin(np.arange(260)), [105, 106, 107]])
    df = _frame(close)
    sf = signals.signal_frame(df)
    assert sf["breakout_52w"].sum() == 1
    assert sf.index[sf["breakout_52w"]][0] == df.index[260]


def test_signals_are_causal():
    rng = np.random.default_rng(7)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.015, 600)))
    df = _frame(close, rng.integers(5e5, 3e6, 600).astype(float))
    full = signals.signal_frame(df)
    part = signals.signal_frame(df.iloc[:450])
    pd.testing.assert_frame_equal(full.iloc[:450], part)


def test_rsi_trigger_price_actually_produces_rsi_70():
    rng = np.random.default_rng(3)
    close = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, 120))))
    trig = outlook.rsi_trigger_prices(close)
    for key, target in (("rsi_70", 70), ("rsi_30", 30)):
        if trig[key] is None:
            continue
        nxt = pd.concat([close, pd.Series([trig[key]])], ignore_index=True)
        assert ind.rsi(nxt).iloc[-1] == pytest.approx(target, abs=0.01)


def test_ma_cross_trigger_price_equalizes_moving_averages():
    close = pd.Series(np.concatenate([np.linspace(150, 100, 200), np.linspace(100, 104, 30)]))
    x = outlook.ma_cross_trigger_price(close)
    nxt = pd.concat([close, pd.Series([x])], ignore_index=True)
    assert ind.sma(nxt, 50).iloc[-1] == pytest.approx(ind.sma(nxt, 200).iloc[-1], rel=1e-4)


def test_outlook_endpoint_has_no_recommendation_language(client):
    o = client.get("/api/ticker/AAPL/outlook").json()
    assert o["catalysts"] and o["trigger_levels"] and o["base_rates"]["signals"]
    assert "not a recommendation" in o["disclaimer"].lower()
    text = str(o).lower()
    for phrase in ("you should buy", "you should sell", "strong buy", "recommend buying"):
        assert phrase not in text


def test_signals_endpoint(client):
    s = client.get("/api/ticker/NVDA/signals").json()
    assert s["state"] and s["rules"]
    for e in s["events"]:
        assert e["rule"] and e["date"]
