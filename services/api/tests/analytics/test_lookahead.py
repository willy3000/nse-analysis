"""No look-ahead: the value at bar t must not change when future bars are appended."""

from __future__ import annotations

import numpy as np
import pandas as pd

from nse_platform.analytics.indicators import compute_all
from tests.analytics.test_indicators import random_ohlcv


def test_compute_all_is_point_in_time():
    df = random_ohlcv(420, seed=7)
    full = compute_all(df)
    for t in (60, 199, 250, 333, 419):
        partial = compute_all(df.iloc[: t + 1])
        a = partial.iloc[-1]
        b = full.iloc[t]
        for col in full.columns:
            va, vb = a[col], b[col]
            if pd.isna(va) and pd.isna(vb):
                continue
            assert np.isclose(va, vb, rtol=1e-9, atol=1e-12), f"{col} at t={t}: truncated={va} full={vb}"
