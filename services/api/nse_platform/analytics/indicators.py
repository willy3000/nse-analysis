"""Technical indicator library (ADR-0004).

Conventions
-----------
* Inputs are pandas Series indexed chronologically. Output at position t uses only inputs at positions <= t
  (verified by tests/analytics/test_lookahead.py).
* Insufficient history -> NaN. Missing inputs are never treated as zero.
* Ratios/returns are decimals (0.05 == 5%).

Formulas
--------
SMA(n)        mean of last n values (all n must be present).
EMA(n)        alpha = 2/(n+1); seeded with SMA(n) of the first n valid values, then recursive.
RSI(n)        Wilder: avg gain/loss seeded with simple mean of first n changes, then
              avg_t = (avg_{t-1}*(n-1) + x_t)/n.  RSI = 100 - 100/(1+avg_gain/avg_loss); avg_loss=0 -> 100.
MACD          EMA12 - EMA26; signal = EMA9(MACD); hist = MACD - signal.
ROC(n)        close/close[t-n] - 1.        Momentum(n) = close - close[t-n].
Bollinger     middle SMA(20); bands +/- 2 * population std (ddof=0) of last 20 closes.
TR            max(high-low, |high-prev_close|, |low-prev_close|)  (first bar: high-low).
ATR(n)        Wilder smoothing of TR seeded with mean of first n TR values.
ADX(n)        Wilder DMI/ADX.
HV(n)         sample std (ddof=1) of n log returns * sqrt(periods_per_year).
OBV           cumulative sum of sign(close change) * volume; NaN from the first missing volume onward.
RelVolume     volume / mean(volume of the *previous* 20 bars) - the current bar is excluded from its baseline.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

INDICATOR_CALC_VERSION = "ind-1.1.0"  # 1.1: bad-print exclusion in series loading
PERIODS_PER_YEAR = {"1d": 252, "1w": 52, "1mo": 12, "1h": 252 * 6, "15m": 252 * 24}


def sma(x: pd.Series, n: int) -> pd.Series:
    return x.rolling(n, min_periods=n).mean()


def _recursive(values: np.ndarray, n: int, alpha: float) -> np.ndarray:
    """Seeded recursive smoother: seed = mean of first n consecutive valid values."""
    out = np.full(values.shape, np.nan)
    valid = ~np.isnan(values)
    run = 0
    start = -1
    for i, ok in enumerate(valid):
        run = run + 1 if ok else 0
        if run == n:
            start = i
            break
    if start < 0:
        return out
    prev = float(np.mean(values[start - n + 1 : start + 1]))
    out[start] = prev
    for i in range(start + 1, len(values)):
        v = values[i]
        if np.isnan(v):
            continue  # gap: output NaN, carry state
        prev = alpha * v + (1 - alpha) * prev
        out[i] = prev
    return out


def ema(x: pd.Series, n: int) -> pd.Series:
    return pd.Series(_recursive(x.to_numpy(dtype=float), n, 2.0 / (n + 1)), index=x.index)


def wilder(x: pd.Series, n: int) -> pd.Series:
    return pd.Series(_recursive(x.to_numpy(dtype=float), n, 1.0 / n), index=x.index)


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    gain[delta.isna()] = np.nan
    loss[delta.isna()] = np.nan
    ag, al = wilder(gain, n), wilder(loss, n)
    rs = ag / al
    out = 100 - 100 / (1 + rs)
    out[(al == 0) & ag.notna()] = 100.0
    out[(al == 0) & (ag == 0)] = 50.0  # perfectly flat series: neither gains nor losses
    return out


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    line = ema(close, fast) - ema(close, slow)
    sig = ema(line, signal)
    return pd.DataFrame({"macd": line, "macd_signal": sig, "macd_hist": line - sig})


def roc(close: pd.Series, n: int) -> pd.Series:
    return close / close.shift(n) - 1


def momentum(close: pd.Series, n: int) -> pd.Series:
    return close - close.shift(n)


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0) -> pd.DataFrame:
    mid = sma(close, n)
    sd = close.rolling(n, min_periods=n).std(ddof=0)
    up, lo = mid + k * sd, mid - k * sd
    return pd.DataFrame({"bb_mid": mid, "bb_upper": up, "bb_lower": lo, "bb_width": (up - lo) / mid,
                         "bb_pctb": (close - lo) / (up - lo)})


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    pc = close.shift(1)
    tr = pd.concat([high - low, (high - pc).abs(), (low - pc).abs()], axis=1).max(axis=1, skipna=False)
    tr.iloc[:1] = (high - low).iloc[:1]
    tr[high.isna() | low.isna()] = np.nan
    return tr


def atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    return wilder(true_range(high, low, close), n)


def adx(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.DataFrame:
    up = high.diff()
    down = -low.diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=high.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=high.index)
    missing = up.isna() | down.isna()
    plus_dm[missing] = np.nan
    minus_dm[missing] = np.nan
    tr = true_range(high, low, close)
    tr.iloc[:1] = np.nan  # DM undefined on first bar; align
    atr_ = wilder(tr, n)
    pdi = 100 * wilder(plus_dm, n) / atr_
    mdi = 100 * wilder(minus_dm, n) / atr_
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi)
    return pd.DataFrame({"plus_di": pdi, "minus_di": mdi, "adx": wilder(dx, n)})


def historical_volatility(close: pd.Series, n: int, periods_per_year: int = 252) -> pd.Series:
    lr = np.log(close / close.shift(1))
    return lr.rolling(n, min_periods=n).std(ddof=1) * np.sqrt(periods_per_year)


def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    sign = np.sign(close.diff()).fillna(0.0)
    contrib = sign * volume
    out = contrib.cumsum()
    missing = volume.isna()
    if missing.any():
        first = missing.to_numpy().argmax()
        out.iloc[first:] = np.nan
    return out


def relative_volume(volume: pd.Series, n: int = 20) -> pd.Series:
    base = volume.shift(1).rolling(n, min_periods=n).mean()
    return volume / base.where(base > 0)


def percentile_rank(x: pd.Series, n: int) -> pd.Series:
    """Rank of the current value within the trailing n values (inclusive), 0..1. Look-ahead free."""

    def _rank(w: np.ndarray) -> float:
        return float((w[:-1] < w[-1]).sum() + 0.5 * (w[:-1] == w[-1]).sum()) / (len(w) - 1) if len(w) > 1 else np.nan

    return x.rolling(n, min_periods=n).apply(_rank, raw=True)


def swing_points(high: pd.Series, low: pd.Series, k: int = 3) -> pd.DataFrame:
    """Fractal swing highs/lows. A swing at bar t is only *confirmed* at bar t+k.

    Returns columns aligned to the confirmation bar: swing_high_price / swing_low_price (price at the pivot)
    and swing_*_age (k). Using these columns at bar t never uses data after t.
    """
    h, lo = high.to_numpy(dtype=float), low.to_numpy(dtype=float)
    n = len(h)
    sh = np.full(n, np.nan)
    sl = np.full(n, np.nan)
    for t in range(k, n - k):
        wh = h[t - k : t + k + 1]
        wl = lo[t - k : t + k + 1]
        if not np.isnan(wh).any() and h[t] == wh.max() and (wh == h[t]).sum() == 1:
            sh[t + k] = h[t]
        if not np.isnan(wl).any() and lo[t] == wl.min() and (wl == lo[t]).sum() == 1:
            sl[t + k] = lo[t]
    return pd.DataFrame({"swing_high_price": sh, "swing_low_price": sl}, index=high.index)


def crossed_above(a: pd.Series, b: pd.Series) -> pd.Series:
    return (a > b) & (a.shift(1) <= b.shift(1))


def crossed_below(a: pd.Series, b: pd.Series) -> pd.Series:
    return (a < b) & (a.shift(1) >= b.shift(1))


def compute_all(df: pd.DataFrame, timeframe: str = "1d") -> pd.DataFrame:
    """Full indicator set for an OHLCV frame (columns: open, high, low, close, volume).

    high/low may contain NaN where the provider did not report OHLC; range-based indicators are NaN there.
    """
    c, h, lo, v = df["close"], df["high"], df["low"], df["volume"]
    ppy = PERIODS_PER_YEAR.get(timeframe, 252)
    out = pd.DataFrame(index=df.index)
    for n in (5, 10, 20, 50, 100, 200):
        out[f"sma_{n}"] = sma(c, n)
    for n in (9, 12, 20, 26, 50):
        out[f"ema_{n}"] = ema(c, n)
    out["rsi_7"] = rsi(c, 7)
    out["rsi_14"] = rsi(c, 14)
    out = out.join(macd(c))
    out["roc_10"] = roc(c, 10)
    out["roc_20"] = roc(c, 20)
    out["roc_60"] = roc(c, 60)
    out["momentum_10"] = momentum(c, 10)
    out = out.join(bollinger(c))
    out["bb_width_pctile_120"] = percentile_rank(out["bb_width"], 120)
    out["atr_14"] = atr(h, lo, c, 14)
    out["atr_pct_14"] = out["atr_14"] / c
    out = out.join(adx(h, lo, c, 14))
    out["hv_20"] = historical_volatility(c, 20, ppy)
    out["hv_60"] = historical_volatility(c, 60, ppy)
    out["range_pct"] = (h - lo) / c
    out["vol_sma_20"] = sma(v, 20)
    out["vol_sma_50"] = sma(v, 50)
    out["rel_volume"] = relative_volume(v, 20)
    out["volume_spike"] = (out["rel_volume"] >= 2.5).astype(float).where(out["rel_volume"].notna())
    out["obv"] = obv(c, v)
    # Price-volume divergence: 20-bar direction of price vs OBV disagree (+1 bullish, -1 bearish, 0 none).
    pslope = np.sign(c - c.shift(20))
    oslope = np.sign(out["obv"] - out["obv"].shift(20))
    div = pd.Series(0.0, index=df.index)
    div[(pslope < 0) & (oslope > 0)] = 1.0
    div[(pslope > 0) & (oslope < 0)] = -1.0
    div[pslope.isna() | oslope.isna()] = np.nan
    out["pv_divergence"] = div
    out["close_high_20_prior"] = c.shift(1).rolling(20, min_periods=20).max()
    out["close_low_20_prior"] = c.shift(1).rolling(20, min_periods=20).min()
    window_52w = {"1d": 250, "1w": 52, "1mo": 12}.get(timeframe, 250)
    out["close_high_52w_prior"] = c.shift(1).rolling(window_52w, min_periods=window_52w).max()
    out["high_52w"] = c.rolling(window_52w, min_periods=min(window_52w, 20)).max()
    out["low_52w"] = c.rolling(window_52w, min_periods=min(window_52w, 20)).min()
    out["drawdown"] = c / c.cummax() - 1
    out = out.join(swing_points(h.fillna(c) if h.isna().all() else h, lo.fillna(c) if lo.isna().all() else lo))
    out["golden_cross"] = crossed_above(out["sma_20"], out["sma_50"]).astype(float)
    out["death_cross"] = crossed_below(out["sma_20"], out["sma_50"]).astype(float)
    return out
