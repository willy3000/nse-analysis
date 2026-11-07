"""Indicator validation: hand-computed references, independent-implementation cross-checks, edge cases."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
import ta

from nse_platform.analytics import indicators as ind

# StockCharts ChartSchool RSI example (Wilder smoothing), widely used as a reference dataset.
RSI_CLOSES = [44.3389, 44.0902, 44.1497, 43.6124, 44.3278, 44.8264, 45.0955, 45.4245, 45.8433, 46.0826, 45.8931,
              46.0328, 45.6140, 46.2820, 46.2820, 46.0028, 46.0328, 46.4116, 46.2222, 45.6439, 46.2122, 46.2521,
              45.7137, 46.4515, 45.7835, 45.3548, 44.0288, 44.1783, 44.2181, 44.5672, 43.4205, 42.6628, 43.1314]
RSI_EXPECTED = [70.53, 66.32, 66.55, 69.41, 66.36, 57.97, 62.93, 63.26, 56.06, 62.38, 54.71, 50.42, 39.99, 41.46,
                41.87, 45.46, 37.30, 33.08, 37.77]


def random_ohlcv(n: int = 1500, seed: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 50 * np.exp(np.cumsum(rng.normal(0.0003, 0.02, n)))
    openp = close * np.exp(rng.normal(0, 0.005, n))
    high = np.maximum(openp, close) * (1 + np.abs(rng.normal(0, 0.01, n)))
    low = np.minimum(openp, close) * (1 - np.abs(rng.normal(0, 0.01, n)))
    vol = rng.integers(1_000, 1_000_000, n).astype(float)
    idx = pd.bdate_range("2018-01-01", periods=n)
    return pd.DataFrame({"open": openp, "high": high, "low": low, "close": close, "volume": vol}, index=idx)


def test_rsi_matches_stockcharts_reference():
    out = ind.rsi(pd.Series(RSI_CLOSES), 14)
    assert out.iloc[:14].isna().all(), "RSI must be NaN before 14 changes are available"
    np.testing.assert_allclose(out.iloc[14:].round(2).to_numpy(), RSI_EXPECTED, atol=0.011)


def test_sma_hand_computed():
    s = pd.Series([1.0, 2, 3, 4, 5])
    out = ind.sma(s, 3)
    assert out.iloc[:2].isna().all()
    assert out.tolist()[2:] == [2.0, 3.0, 4.0]


def test_ema_seeded_with_sma():
    s = pd.Series([2.0, 4, 6, 8, 10, 12])
    out = ind.ema(s, 3)
    alpha = 0.5
    seed = 4.0  # mean(2,4,6)
    exp = [np.nan, np.nan, seed]
    for v in [8, 10, 12]:
        exp.append(alpha * v + (1 - alpha) * exp[-1])
    np.testing.assert_allclose(out.to_numpy(), exp, equal_nan=True)


def test_bollinger_population_std():
    s = pd.Series(np.arange(1.0, 26.0))
    bb = ind.bollinger(s, 20, 2)
    window = np.arange(6.0, 26.0)
    assert bb["bb_mid"].iloc[-1] == pytest.approx(window.mean())
    assert bb["bb_upper"].iloc[-1] == pytest.approx(window.mean() + 2 * window.std(ddof=0))
    assert bb["bb_mid"].iloc[:19].isna().all()


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_cross_check_against_ta_library(seed):
    df = random_ohlcv(seed=seed)
    tail = slice(-300, None)  # ta seeds EMA/Wilder from the first value; compare after convergence
    np.testing.assert_allclose(ind.rsi(df.close, 14).iloc[tail],
                               ta.momentum.RSIIndicator(df.close, 14).rsi().iloc[tail], rtol=1e-6)
    np.testing.assert_allclose(ind.ema(df.close, 20).iloc[tail],
                               ta.trend.EMAIndicator(df.close, 20).ema_indicator().iloc[tail], rtol=1e-8)
    np.testing.assert_allclose(ind.sma(df.close, 50).iloc[tail],
                               ta.trend.SMAIndicator(df.close, 50).sma_indicator().iloc[tail], rtol=1e-12)
    m = ind.macd(df.close)
    tm = ta.trend.MACD(df.close)
    np.testing.assert_allclose(m.macd.iloc[tail], tm.macd().iloc[tail], rtol=1e-6, atol=1e-9)
    np.testing.assert_allclose(m.macd_signal.iloc[tail], tm.macd_signal().iloc[tail], rtol=1e-6, atol=1e-9)
    bb = ind.bollinger(df.close)
    tb = ta.volatility.BollingerBands(df.close, 20, 2)
    np.testing.assert_allclose(bb.bb_upper.iloc[tail], tb.bollinger_hband().iloc[tail], rtol=1e-10)
    np.testing.assert_allclose(bb.bb_lower.iloc[tail], tb.bollinger_lband().iloc[tail], rtol=1e-10)
    np.testing.assert_allclose(ind.atr(df.high, df.low, df.close, 14).iloc[tail],
                               ta.volatility.AverageTrueRange(df.high, df.low, df.close, 14)
                               .average_true_range().iloc[tail], rtol=1e-6)
    np.testing.assert_allclose(ind.obv(df.close, df.volume).diff().iloc[tail],
                               ta.volume.OnBalanceVolumeIndicator(df.close, df.volume)
                               .on_balance_volume().diff().iloc[tail], rtol=1e-12)
    np.testing.assert_allclose(ind.roc(df.close, 10).iloc[tail] * 100,
                               ta.momentum.ROCIndicator(df.close, 10).roc().iloc[tail], rtol=1e-10)
    np.testing.assert_allclose(ind.adx(df.high, df.low, df.close, 14)["adx"].iloc[tail],
                               ta.trend.ADXIndicator(df.high, df.low, df.close, 14).adx().iloc[tail], rtol=1e-3)


def test_insufficient_history_is_nan_not_zero():
    s = pd.Series([10.0, 10.5, 10.2])
    assert ind.sma(s, 20).isna().all()
    assert ind.ema(s, 20).isna().all()
    assert ind.rsi(s, 14).isna().all()
    assert ind.historical_volatility(s, 20).isna().all()


def test_missing_volume_never_becomes_zero():
    df = random_ohlcv(100)
    df.loc[df.index[50], "volume"] = np.nan
    o = ind.obv(df.close, df.volume)
    assert o.iloc[:50].notna().all() and o.iloc[50:].isna().all()
    rv = ind.relative_volume(df.volume, 20)
    assert np.isnan(rv.iloc[50])
    assert ind.sma(df.volume, 20).iloc[50:70].isna().all()


def test_relative_volume_excludes_current_bar():
    v = pd.Series([100.0] * 20 + [500.0])
    assert ind.relative_volume(v, 20).iloc[-1] == pytest.approx(5.0)


def test_atr_nan_when_ohlc_missing():
    df = random_ohlcv(60)
    df.loc[df.index[30], ["high", "low"]] = np.nan
    tr = ind.true_range(df.high, df.low, df.close)
    assert np.isnan(tr.iloc[30])


def test_flat_series_rsi_neutral():
    out = ind.rsi(pd.Series([10.0] * 30), 14)
    assert out.iloc[-1] == 50.0


def test_swing_points_confirmed_with_delay():
    high = pd.Series([1, 2, 3, 10, 3, 2, 1, 2, 3], dtype=float)
    low = high - 0.5
    sp = ind.swing_points(high, low, k=3)
    # Pivot at index 3 is only known at index 6.
    assert np.isnan(sp.swing_high_price.iloc[3])
    assert sp.swing_high_price.iloc[6] == 10.0
