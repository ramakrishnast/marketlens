"""Indicator calculations checked against published reference values and hand calculations."""
import numpy as np
import pandas as pd
import pytest

from app.services import indicators as ind

# StockCharts "RSI" ChartSchool spreadsheet (Wilder's 14-period RSI)
RSI_CLOSES = [44.3389, 44.0902, 44.1497, 43.6124, 44.2779, 44.8264, 45.0955, 45.4245, 45.8433, 46.0826, 45.8931,
              46.0328, 45.614, 46.282, 46.282, 46.0028, 46.0328, 46.4116, 46.2222, 45.6439, 46.2122, 46.2521, 45.7137,
              46.4515, 45.7835, 45.3548, 44.0288, 44.1783, 44.2181, 44.5672, 43.4205, 42.6628, 43.1314]
RSI_EXPECTED = [70.53, 66.32, 66.55, 69.41, 66.36, 57.97, 62.93, 63.26, 56.06, 62.38, 54.71, 50.42, 39.99, 41.46,
                41.87, 45.46, 37.30, 33.08, 37.77]

# StockCharts "Moving Averages" spreadsheet: 10-day EMA seeded with the 10-day SMA
EMA_CLOSES = [22.27, 22.19, 22.08, 22.17, 22.18, 22.13, 22.23, 22.43, 22.24, 22.29, 22.15, 22.39, 22.38, 22.61,
              23.36, 24.05, 23.75, 23.83, 23.95, 23.63, 23.82, 23.87, 23.65, 23.19, 23.10, 23.33, 22.68, 23.10,
              22.40, 22.17]
EMA_EXPECTED = [22.22, 22.21, 22.24, 22.27, 22.33, 22.52, 22.80, 22.97, 23.13, 23.28, 23.34, 23.43, 23.51, 23.54,
                23.47, 23.40, 23.39, 23.26, 23.23, 23.08, 22.92]


def test_rsi_matches_stockcharts_reference():
    out = ind.rsi(pd.Series(RSI_CLOSES)).dropna().round(2).tolist()
    assert out == pytest.approx(RSI_EXPECTED, abs=0.011)


def test_rsi_first_value_index_and_bounds():
    r = ind.rsi(pd.Series(RSI_CLOSES))
    assert r.iloc[:14].isna().all() and not np.isnan(r.iloc[14])
    up = ind.rsi(pd.Series(np.arange(1.0, 40.0)))
    assert up.dropna().eq(100).all()


def test_ema_matches_stockcharts_reference():
    out = ind.ema(pd.Series(EMA_CLOSES), 10).dropna().round(2).tolist()
    assert out == pytest.approx(EMA_EXPECTED, abs=0.011)


def test_sma_simple_values():
    s = ind.sma(pd.Series([1.0, 2, 3, 4, 5, 6]), 3)
    assert s.dropna().tolist() == [2.0, 3.0, 4.0, 5.0]


def test_macd_is_ema_difference_and_signal():
    c = pd.Series(np.linspace(10, 30, 80) + np.sin(np.arange(80)))
    m = ind.macd(c)
    expected = ind.ema(c, 12) - ind.ema(c, 26)
    pd.testing.assert_series_equal(m["macd"], expected, check_names=False)
    assert (m["hist"] - (m["macd"] - m["signal"])).abs().max() < 1e-12
    assert m["signal"].first_valid_index() == 25 + 8  # 26-bar EMA, then 9-bar signal


def test_bollinger_population_std():
    c = pd.Series([1.0, 2, 3, 4, 5])
    bb = ind.bollinger(c, period=5, mult=2)
    # mean 3, population std sqrt(2)
    assert bb["middle"].iloc[-1] == pytest.approx(3.0)
    assert bb["upper"].iloc[-1] == pytest.approx(3 + 2 * np.sqrt(2))
    assert bb["lower"].iloc[-1] == pytest.approx(3 - 2 * np.sqrt(2))


def test_atr_wilder_hand_calculation():
    df = pd.DataFrame({
        "high": [10, 11, 12, 11.5, 13], "low": [9, 10, 10.5, 10, 11.5], "close": [9.5, 10.5, 11.5, 10.2, 12.8],
    }, dtype=float)
    # TR: 1, 1.5 (11-9.5), 1.5, 1.5 (11.5-10, |11.5-11.5|, |10-11.5|), 2.8 (13-10.2)
    tr = ind.true_range(df)
    assert tr.tolist() == pytest.approx([1, 1.5, 1.5, 1.5, 2.8])
    a = ind.atr(df, 3)
    seed = (1 + 1.5 + 1.5) / 3
    assert a.iloc[2] == pytest.approx(seed)
    assert a.iloc[3] == pytest.approx((seed * 2 + 1.5) / 3)
    assert a.iloc[4] == pytest.approx(((seed * 2 + 1.5) / 3 * 2 + 2.8) / 3)


def test_vwap_resets_each_session():
    idx = pd.to_datetime(["2026-01-05 09:30", "2026-01-05 09:35", "2026-01-06 09:30"])
    df = pd.DataFrame({"high": [11, 12, 20], "low": [9, 10, 18], "close": [10, 11, 19], "volume": [100, 300, 50]},
                      index=idx, dtype=float)
    v = ind.vwap(df)
    assert v.iloc[0] == pytest.approx(10)
    assert v.iloc[1] == pytest.approx((10 * 100 + 11 * 300) / 400)
    assert v.iloc[2] == pytest.approx(19)  # new session


def test_indicators_are_causal():
    """Appending future bars must not change past indicator values (no look-ahead)."""
    rng = np.random.default_rng(1)
    n = 400
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    df = pd.DataFrame({"open": close, "high": close * 1.01, "low": close * 0.99, "close": close,
                       "volume": rng.integers(1e5, 1e6, n).astype(float)},
                      index=pd.bdate_range("2020-01-01", periods=n))
    full = ind.compute_all(df)
    part = ind.compute_all(df.iloc[:300])
    pd.testing.assert_frame_equal(full.iloc[:300], part)


def test_support_resistance_finds_repeated_level():
    # oscillate between ~100 and ~110 so both levels are touched repeatedly
    base = np.tile(np.concatenate([np.linspace(100, 110, 10), np.linspace(110, 100, 10)]), 10)
    df = pd.DataFrame({"high": base + 0.2, "low": base - 0.2, "close": base}, index=pd.bdate_range("2024-01-01", periods=len(base)))
    levels = ind.support_resistance(df, lookback=200, window=3)
    prices = [lv["price"] for lv in levels]
    assert any(abs(p - 110.2) < 1 for p in prices) and any(abs(p - 99.8) < 1 for p in prices)
