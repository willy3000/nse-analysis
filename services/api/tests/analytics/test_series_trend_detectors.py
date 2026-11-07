from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from nse_platform.analytics.indicators import compute_all
from nse_platform.analytics.series import AdjustmentEvent, apply_adjustments, resample
from nse_platform.analytics.trend import classify
from nse_platform.models.enums import TrendClass
from nse_platform.opportunities.detectors import _edge, detect, features


def frame(close: np.ndarray, start: str = "2024-01-01") -> pd.DataFrame:
    idx = pd.bdate_range(start, periods=len(close))
    return pd.DataFrame({"open": close, "high": close * 1.01, "low": close * 0.99, "close": close,
                         "volume": np.full(len(close), 1000.0)}, index=idx)


def test_bonus_adjustment_point_in_time():
    df = frame(np.array([110.0, 110.0, 100.0, 100.0]))
    ev = [AdjustmentEvent(df.index[2].date(), 10 / 11)]
    adj = apply_adjustments(df, ev)
    assert np.allclose(adj["close"], [100, 100, 100, 100])
    assert np.allclose(adj["volume"].iloc[:2], 1100) and np.allclose(adj["volume"].iloc[2:], 1000)
    before = apply_adjustments(df, ev, as_of=df.index[1].date())
    assert np.allclose(before["close"], df["close"])  # event not yet known -> no adjustment
    assert np.allclose(df["close"].iloc[0], 110)  # original untouched


def test_resample_strict_and_partial():
    df = frame(np.arange(1.0, 11.0), "2026-01-05")  # Mon 5 Jan .. Fri 16 Jan
    df.loc[df.index[1], "high"] = np.nan
    wk = resample(df, "1w", as_of=date(2026, 1, 14))
    assert len(wk) == 2
    assert np.isnan(wk["high"].iloc[0])  # a missing constituent makes the aggregate missing
    assert wk["volume"].iloc[1] == 5000
    assert bool(wk["is_partial"].iloc[1]) and not bool(wk["is_partial"].iloc[0])


def test_trend_insufficient_and_directional():
    short = frame(np.linspace(10, 12, 30))
    assert classify(short, compute_all(short)).classification == TrendClass.INSUFFICIENT_DATA
    rng = np.random.default_rng(3)
    up = frame(100 * np.exp(np.cumsum(0.004 + rng.normal(0, 0.006, 260))))
    res = classify(up, compute_all(up))
    assert res.classification in (TrendClass.STRONG_BULLISH, TrendClass.EMERGING_BULLISH, TrendClass.BULLISH_PULLBACK)
    assert res.evidence and all("label" in e for e in res.evidence)
    down = frame(100 * np.exp(np.cumsum(-0.004 + rng.normal(0, 0.006, 260))))
    assert classify(down, compute_all(down)).classification in (TrendClass.STRONG_BEARISH,
                                                                 TrendClass.EMERGING_BEARISH)


def test_edge_cooldown():
    s = pd.Series([False, True, True, True, False, False, True])
    assert _edge(s, 3).tolist() == [False, True, False, False, False, False, False]
    assert _edge(s, 1).tolist() == [False, True, False, False, False, False, True]


def test_detector_replay_equals_live_detection():
    rng = np.random.default_rng(11)
    df = frame(50 * np.exp(np.cumsum(rng.normal(0, 0.02, 400))))
    full = features(df, compute_all(df))
    replay = {(d.detector.signal_type, d.ts) for d in detect(full)}
    for t in (150, 260, 399):
        sub = df.iloc[: t + 1]
        live = {(d.detector.signal_type, d.ts) for d in detect(features(sub, compute_all(sub)), only_last=True)}
        expected = {x for x in replay if x[1] == df.index[t]}
        assert live == expected, f"t={t}"


def test_oversold_recovery_fires_on_cross():
    close = np.concatenate([np.linspace(100, 60, 80), np.linspace(60, 66, 10)])
    df = frame(close)
    f = features(df, compute_all(df))
    hits = [d for d in detect(f) if d.detector.signal_type == "OVERSOLD_RECOVERY"]
    assert hits
    for h in hits:
        pos = f.index.get_loc(h.ts)
        assert f["rsi_14"].iloc[pos] > 30 >= f["rsi_14"].iloc[pos - 1]
        assert h.detector.invalidation(h.row)["price"] is not None
