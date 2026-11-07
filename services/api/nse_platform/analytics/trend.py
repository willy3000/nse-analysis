"""Rule-based, timeframe-specific trend classification with explicit evidence.

The classifier combines several independent observations (MA structure, MA slopes, momentum, swing structure,
volume confirmation, trend strength). No single indicator decides the outcome. Rules are evaluated in a fixed,
documented order (docs/architecture/analytics.md); the first rule whose conditions all hold wins.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from nse_platform.models.enums import TrendClass

TREND_CALC_VERSION = "trend-1.0.0"
MIN_BARS = 60


@dataclass
class TrendResult:
    classification: TrendClass
    strength: float | None
    evidence: list[dict[str, Any]] = field(default_factory=list)


def _e(label: str, value: Any, stance: str, detail: str = "") -> dict[str, Any]:
    if isinstance(value, (float, np.floating)):
        value = None if not np.isfinite(value) else round(float(value), 4)
    return {"label": label, "value": value, "stance": stance, "detail": detail}


def _swing_structure(ind: pd.DataFrame, lookback: int = 120) -> tuple[str, str]:
    w = ind.iloc[-lookback:]
    highs = w["swing_high_price"].dropna().to_numpy()
    lows = w["swing_low_price"].dropna().to_numpy()
    if len(highs) < 2 or len(lows) < 2:
        return "unknown", "fewer than two confirmed swing highs/lows"
    hh, hl = highs[-1] > highs[-2], lows[-1] > lows[-2]
    lh, ll = highs[-1] < highs[-2], lows[-1] < lows[-2]
    if hh and hl:
        return "bullish", "higher high and higher low"
    if lh and ll:
        return "bearish", "lower high and lower low"
    return "mixed", "swing structure mixed"


def classify(bars: pd.DataFrame, ind: pd.DataFrame, trading_frequency: float | None = None) -> TrendResult:
    """Classify the trend as of the last row of `bars`/`ind` (both aligned, chronological)."""
    n = int(bars["close"].notna().sum())
    if n < MIN_BARS or pd.isna(ind["sma_50"].iloc[-1]):
        return TrendResult(TrendClass.INSUFFICIENT_DATA, None,
                           [_e("Bars available", n, "neutral", f"at least {MIN_BARS} bars with SMA50 required")])
    last = ind.iloc[-1]
    c = float(bars["close"].iloc[-1])
    s20, s50 = float(last["sma_20"]), float(last["sma_50"])
    s200 = last.get("sma_200")
    slope20 = s20 / float(ind["sma_20"].iloc[-6]) - 1 if pd.notna(ind["sma_20"].iloc[-6]) else np.nan
    slope50 = s50 / float(ind["sma_50"].iloc[-11]) - 1 if pd.notna(ind["sma_50"].iloc[-11]) else np.nan
    rsi = float(last["rsi_14"]) if pd.notna(last["rsi_14"]) else np.nan
    adx = float(last["adx"]) if pd.notna(last.get("adx")) else np.nan
    hist = ind["macd_hist"]
    hist_rising = bool(pd.notna(hist.iloc[-1]) and pd.notna(hist.iloc[-4]) and hist.iloc[-1] > hist.iloc[-4])
    rsi_min10 = float(ind["rsi_14"].iloc[-10:].min())
    rsi_max10 = float(ind["rsi_14"].iloc[-10:].max())
    recent_golden = bool(ind["golden_cross"].iloc[-10:].max() == 1)
    recent_death = bool(ind["death_cross"].iloc[-10:].max() == 1)
    swing, swing_detail = _swing_structure(ind)
    pv_div = last.get("pv_divergence")

    # Strength: ADX when OHLC is available, otherwise |R^2| of a 50-bar log-price regression * 100.
    if np.isfinite(adx):
        strength = adx
        strength_src = "ADX(14)"
    else:
        y = np.log(bars["close"].iloc[-50:].to_numpy())
        x = np.arange(len(y))
        r = np.corrcoef(x, y)[0, 1] if len(y) > 2 and np.std(y) > 0 else 0.0
        strength = float(r * r * 100)
        strength_src = "R² of 50-bar log-price trend (OHLC unavailable)"

    ev = [
        _e("Close vs SMA20", c / s20 - 1, "bullish" if c > s20 else "bearish", f"close {c:.2f}, SMA20 {s20:.2f}"),
        _e("SMA20 vs SMA50", s20 / s50 - 1, "bullish" if s20 > s50 else "bearish", f"SMA50 {s50:.2f}"),
        _e("SMA20 slope (5 bars)", slope20, "bullish" if slope20 > 0 else "bearish"),
        _e("SMA50 slope (10 bars)", slope50, "bullish" if slope50 > 0 else "bearish"),
        _e("RSI(14)", rsi, "bullish" if rsi > 55 else "bearish" if rsi < 45 else "neutral"),
        _e("MACD histogram rising", hist_rising, "bullish" if hist_rising else "bearish"),
        _e("Swing structure", swing, {"bullish": "bullish", "bearish": "bearish"}.get(swing, "neutral"),
           swing_detail),
        _e("Trend strength", strength, "neutral", strength_src),
    ]
    if pd.notna(s200):
        ev.append(_e("Close vs SMA200", c / float(s200) - 1, "bullish" if c > s200 else "bearish"))
    rv = last.get("rel_volume")
    if pd.notna(rv):
        ev.append(_e("Relative volume", rv, "bullish" if rv > 1.2 else "neutral", "vs prior 20-bar average"))
    if pd.notna(pv_div) and pv_div != 0:
        ev.append(_e("Price/OBV divergence", pv_div, "bullish" if pv_div > 0 else "bearish"))
    if trading_frequency is not None and trading_frequency < 0.5:
        ev.append(_e("Trading frequency (60 sessions)", trading_frequency, "caution",
                     "illiquid: many sessions without trades - technical signals are unreliable"))

    strong = strength >= 25
    up_struct = c > s20 > s50
    down_struct = c < s20 < s50

    def result(cls: TrendClass) -> TrendResult:
        return TrendResult(cls, float(strength), ev)

    # Ordered rules.
    if up_struct and slope20 > 0 and slope50 > 0 and strong and 50 <= rsi <= 80 and swing != "bearish":
        return result(TrendClass.STRONG_BULLISH)
    if down_struct and slope20 < 0 and slope50 < 0 and strong and 20 <= rsi <= 50 and swing != "bullish":
        return result(TrendClass.STRONG_BEARISH)
    if s20 < s50 and slope50 < 0 and rsi_min10 < 30 and rsi > 40 and c > s20 and hist_rising:
        return result(TrendClass.POTENTIAL_REVERSAL)
    if s20 > s50 and slope50 > 0 and rsi_max10 > 70 and rsi < 60 and c < s20 and not hist_rising:
        return result(TrendClass.POTENTIAL_REVERSAL)
    if s20 > s50 and slope50 > 0 and c < s20 and c >= s50 * 0.98 and 35 <= rsi <= 58:
        return result(TrendClass.BULLISH_PULLBACK)
    if s20 > s50 and (c < s50 * 0.98 or (slope20 < 0 and not hist_rising)):
        return result(TrendClass.WEAKENING_BULLISH)
    if c > s20 and slope20 > 0 and (s20 <= s50 or recent_golden or not strong):
        return result(TrendClass.EMERGING_BULLISH)
    if c < s20 and slope20 < 0 and (s20 >= s50 or recent_death or not strong):
        return result(TrendClass.EMERGING_BEARISH)
    if up_struct and slope20 > 0:
        return result(TrendClass.EMERGING_BULLISH)
    if down_struct and slope20 < 0:
        return result(TrendClass.EMERGING_BEARISH)
    return result(TrendClass.SIDEWAYS)


def support_resistance(bars: pd.DataFrame, ind: pd.DataFrame, lookback: int = 250,
                       tolerance: float | None = None) -> list[dict[str, Any]]:
    """Cluster confirmed swing points into horizontal levels. Tolerance defaults to max(1.5%, 1 x ATR%)."""
    w = ind.iloc[-lookback:]
    c = float(bars["close"].iloc[-1])
    atr_pct = w["atr_pct_14"].iloc[-1]
    tol = tolerance or max(0.015, float(atr_pct) if pd.notna(atr_pct) else 0.0)
    pts: list[tuple[float, pd.Timestamp]] = []
    for col in ("swing_high_price", "swing_low_price"):
        s = w[col].dropna()
        pts.extend(zip(s.to_numpy(dtype=float), s.index, strict=False))
    pts.sort(key=lambda p: p[0])
    clusters: list[list[tuple[float, pd.Timestamp]]] = []
    for p in pts:
        if clusters and abs(p[0] / np.mean([q[0] for q in clusters[-1]]) - 1) <= tol:
            clusters[-1].append(p)
        else:
            clusters.append([p])
    levels = []
    for cl in clusters:
        if len(cl) < 2:
            continue
        price = float(np.mean([q[0] for q in cl]))
        levels.append({
            "level_type": "SUPPORT" if price < c else "RESISTANCE",
            "price": round(price, 4),
            "touches": len(cl),
            "first_touch_ts": min(q[1] for q in cl),
            "last_touch_ts": max(q[1] for q in cl),
            "distance_pct": round(price / c - 1, 4),
        })
    levels.sort(key=lambda lv: abs(lv["distance_pct"]))
    return levels[:8]
