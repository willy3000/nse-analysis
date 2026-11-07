"""Opportunity / risk detectors.

Each technical detector is a vectorised boolean condition over a point-in-time feature frame (bars + indicators,
every column computed only from data <= t). The same code therefore serves live detection (last row) and
historical replay (all rows), and replayed signals are look-ahead free by construction.

"Edge" filtering suppresses repeated triggers: a detector fires only if it did not fire in the previous
`cooldown` bars.

No detector outputs a probability or confidence. Historical base rates are computed separately from tracked
outcomes (opportunities/outcomes.py) and shown with their sample size.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from nse_platform.models.enums import SignalCategory, SignalDirection

SIGNAL_CALC_VERSION = "sig-1.0.0"


@dataclass(frozen=True)
class Detector:
    signal_type: str
    category: SignalCategory
    direction: SignalDirection
    title: str
    description: str
    expiry_bars: int
    cooldown: int
    condition: Callable[[pd.DataFrame], pd.Series]
    evidence: Callable[[pd.Series], list[dict[str, Any]]]
    invalidation: Callable[[pd.Series], dict[str, Any]]


def _r(x: Any, nd: int = 4) -> float | None:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return round(f, nd) if np.isfinite(f) else None


def ev(label: str, value: Any, detail: str = "") -> dict[str, Any]:
    return {"label": label, "value": _r(value) if not isinstance(value, (str, bool)) else value, "detail": detail}


def _edge(cond: pd.Series, cooldown: int) -> pd.Series:
    cond = cond.fillna(False).astype(bool)
    prior = cond.shift(1, fill_value=False).rolling(cooldown, min_periods=1).max().astype(bool)
    return cond & ~prior


def features(bars: pd.DataFrame, ind: pd.DataFrame) -> pd.DataFrame:
    f = bars[["open", "high", "low", "close", "volume"]].join(ind)
    f["low_or_close"] = f["low"].fillna(f["close"])
    f["traded"] = (f["volume"] > 0).astype(float).where(f["volume"].notna())
    f["trade_freq_20"] = f["traded"].rolling(20, min_periods=20).mean()
    f["trade_freq_prior_120"] = f["traded"].shift(20).rolling(120, min_periods=100).mean()
    f["rel_vol_avg_10"] = f["rel_volume"].rolling(10, min_periods=8).mean()
    f["hv20_baseline"] = f["hv_20"].shift(20).rolling(120, min_periods=100).median()
    f["sma50_rising"] = f["sma_50"] > f["sma_50"].shift(10)
    f["close_min_10"] = f["close"].rolling(10, min_periods=10).min()
    f["last_swing_low"] = f["swing_low_price"].ffill()
    f["gap"] = f["open"] / f["close"].shift(1) - 1
    f["ret_1"] = f["close"] / f["close"].shift(1) - 1
    _divergence_columns(f)
    return f


def _divergence_columns(f: pd.DataFrame, k: int = 3, max_gap: int = 60) -> None:
    """Mark confirmation bars of regular RSI divergences between consecutive confirmed swing points."""
    rsi_at_pivot = f["rsi_14"].shift(k)
    pos = pd.Series(np.arange(len(f)), index=f.index)
    for kind, col in (("bull", "swing_low_price"), ("bear", "swing_high_price")):
        out = pd.Series(np.nan, index=f.index)
        prev_price = prev_rsi = prev_pos = None
        for ts in f.index[f[col].notna()]:
            price, r, p = float(f.at[ts, col]), rsi_at_pivot.at[ts], int(pos.at[ts])
            if prev_price is not None and pd.notna(r) and pd.notna(prev_rsi) and p - prev_pos <= max_gap:
                if kind == "bull" and price < prev_price and r > prev_rsi:
                    out.at[ts] = price
                if kind == "bear" and price > prev_price and r < prev_rsi:
                    out.at[ts] = price
            prev_price, prev_rsi, prev_pos = price, r, p
        f[f"{kind}_divergence"] = out


# --------------------------------------------------------------------------- invalidation helpers


def below(price: float | None, what: str) -> dict[str, Any]:
    return {"type": "close_below", "price": _r(price), "condition": f"Daily close below {what}"}


def above(price: float | None, what: str) -> dict[str, Any]:
    return {"type": "close_above", "price": _r(price), "condition": f"Daily close above {what}"}


TECHNICAL: tuple[Detector, ...] = (
    Detector(
        "MOMENTUM_STRONG", SignalCategory.MOMENTUM, SignalDirection.BULLISH, "Strong momentum",
        "60-bar return above 10% with positive 20-bar return, price above rising EMA20 > EMA50, and above-average "
        "trading activity.", 15, 10,
        lambda f: (f.roc_60 > 0.10) & (f.roc_20 > 0) & (f.close > f.ema_20) & (f.ema_20 > f.ema_50)
        & (f.rel_vol_avg_10 > 1.1),
        lambda r: [ev("60-bar return", r.roc_60), ev("20-bar return", r.roc_20), ev("EMA20", r.ema_20),
                   ev("EMA50", r.ema_50), ev("Avg relative volume (10 bars)", r.rel_vol_avg_10)],
        lambda r: below(r.ema_50, "EMA50"),
    ),
    Detector(
        "MA_ALIGNMENT", SignalCategory.MOMENTUM, SignalDirection.BULLISH, "Moving-average alignment",
        "SMA20 > SMA50 > SMA200 has just been established.", 20, 40,
        lambda f: (f.sma_20 > f.sma_50) & (f.sma_50 > f.sma_200) & (f.close > f.sma_20),
        lambda r: [ev("SMA20", r.sma_20), ev("SMA50", r.sma_50), ev("SMA200", r.sma_200)],
        lambda r: below(r.sma_50, "SMA50"),
    ),
    Detector(
        "RESISTANCE_BREAKOUT", SignalCategory.BREAKOUT, SignalDirection.BULLISH, "Breakout (unconfirmed volume)",
        "Close more than 0.5% above the highest close of the prior 20 bars, without volume confirmation.", 10, 10,
        lambda f: (f.close > f.close_high_20_prior * 1.005) & ~(f.rel_volume >= 1.5),
        lambda r: [ev("Breakout level (20-bar closing high)", r.close_high_20_prior), ev("Close", r.close),
                   ev("Relative volume", r.rel_volume, "below 1.5x: not volume-confirmed")],
        lambda r: below(r.close_high_20_prior, "the breakout level"),
    ),
    Detector(
        "VOLUME_BREAKOUT", SignalCategory.BREAKOUT, SignalDirection.BULLISH, "Volume-confirmed breakout",
        "Close more than 0.5% above the prior 20-bar closing high on at least 1.5x average volume.", 10, 10,
        lambda f: (f.close > f.close_high_20_prior * 1.005) & (f.rel_volume >= 1.5),
        lambda r: [ev("Breakout level (20-bar closing high)", r.close_high_20_prior), ev("Close", r.close),
                   ev("Relative volume", r.rel_volume)],
        lambda r: below(r.close_high_20_prior, "the breakout level"),
    ),
    Detector(
        "HIGH_52W_BREAKOUT", SignalCategory.BREAKOUT, SignalDirection.BULLISH, "52-week high breakout",
        "Close above the highest close of the prior 52 weeks.", 15, 20,
        lambda f: f.close > f.close_high_52w_prior,
        lambda r: [ev("Prior 52-week closing high", r.close_high_52w_prior), ev("Close", r.close),
                   ev("Relative volume", r.rel_volume)],
        lambda r: below(r.close_high_52w_prior * 0.97 if pd.notna(r.close_high_52w_prior) else None,
                        "3% under the prior 52-week high"),
    ),
    Detector(
        "CONSOLIDATION_BREAKOUT", SignalCategory.BREAKOUT, SignalDirection.BULLISH, "Consolidation breakout",
        "Bollinger bandwidth was in the narrowest 20% of the last 120 bars within the past 5 bars and price has "
        "closed above the upper band.", 10, 15,
        lambda f: (f.bb_width_pctile_120.shift(1).rolling(5, min_periods=1).min() <= 0.2) & (f.close > f.bb_upper),
        lambda r: [ev("Upper Bollinger band", r.bb_upper), ev("Bandwidth percentile (120 bars)",
                                                              r.bb_width_pctile_120), ev("Relative volume", r.rel_volume)],
        lambda r: below(r.bb_mid, "the 20-bar mean (Bollinger middle band)"),
    ),
    Detector(
        "OVERSOLD_RECOVERY", SignalCategory.REVERSAL, SignalDirection.BULLISH, "Oversold recovery",
        "RSI(14) crossed back above 30 from oversold territory.", 10, 10,
        lambda f: (f.rsi_14 > 30) & (f.rsi_14.shift(1) <= 30),
        lambda r: [ev("RSI(14)", r.rsi_14), ev("Lowest close (10 bars)", r.close_min_10)],
        lambda r: below(r.close_min_10, "the 10-bar low"),
    ),
    Detector(
        "BULLISH_DIVERGENCE", SignalCategory.REVERSAL, SignalDirection.BULLISH, "Bullish RSI divergence",
        "Price made a lower swing low while RSI(14) made a higher low (confirmed 3 bars after the pivot).", 15, 10,
        lambda f: f.bull_divergence.notna(),
        lambda r: [ev("Swing low", r.bull_divergence), ev("RSI(14) now", r.rsi_14)],
        lambda r: below(r.bull_divergence, "the divergence swing low"),
    ),
    Detector(
        "BEARISH_DIVERGENCE", SignalCategory.REVERSAL, SignalDirection.BEARISH, "Bearish RSI divergence",
        "Price made a higher swing high while RSI(14) made a lower high.", 15, 10,
        lambda f: f.bear_divergence.notna(),
        lambda r: [ev("Swing high", r.bear_divergence), ev("RSI(14) now", r.rsi_14)],
        lambda r: above(r.bear_divergence, "the divergence swing high"),
    ),
    Detector(
        "GOLDEN_CROSS", SignalCategory.REVERSAL, SignalDirection.BULLISH, "Trend transition: SMA20 crossed above SMA50",
        "The 20-bar average crossed above the 50-bar average.", 20, 20,
        lambda f: f.golden_cross == 1,
        lambda r: [ev("SMA20", r.sma_20), ev("SMA50", r.sma_50), ev("RSI(14)", r.rsi_14)],
        lambda r: below(r.sma_50, "SMA50"),
    ),
    Detector(
        "DEATH_CROSS", SignalCategory.REVERSAL, SignalDirection.BEARISH, "Trend transition: SMA20 crossed below SMA50",
        "The 20-bar average crossed below the 50-bar average.", 20, 20,
        lambda f: f.death_cross == 1,
        lambda r: [ev("SMA20", r.sma_20), ev("SMA50", r.sma_50), ev("RSI(14)", r.rsi_14)],
        lambda r: above(r.sma_50, "SMA50"),
    ),
    Detector(
        "PULLBACK_TO_MA", SignalCategory.PULLBACK, SignalDirection.BULLISH, "Pullback to EMA20 in uptrend",
        "Rising SMA50, price above SMA50, session low within 1% of EMA20 with a close back above it, RSI 40-55.",
        10, 10,
        lambda f: f.sma50_rising & (f.close > f.sma_50) & (f.low_or_close <= f.ema_20 * 1.01) & (f.close > f.ema_20)
        & f.rsi_14.between(40, 55) & (f.close.shift(5) > f.ema_20.shift(5) * 1.02),
        lambda r: [ev("EMA20", r.ema_20), ev("SMA50", r.sma_50), ev("RSI(14)", r.rsi_14)],
        lambda r: below(r.sma_50, "SMA50"),
    ),
    Detector(
        "SUPPORT_RETEST", SignalCategory.PULLBACK, SignalDirection.BULLISH, "Support retest",
        "Rising SMA50 and a retest (within 2%) of the most recent confirmed swing low, closing above it.", 10, 15,
        lambda f: f.sma50_rising & (f.low_or_close <= f.last_swing_low * 1.02) & (f.close > f.last_swing_low)
        & (f.close.shift(10) > f.last_swing_low * 1.05),
        lambda r: [ev("Support (last swing low)", r.last_swing_low), ev("Close", r.close)],
        lambda r: below(r.last_swing_low * 0.98 if pd.notna(r.last_swing_low) else None, "2% under support"),
    ),
    # ----------------------------------------------------------------- risk alerts
    Detector(
        "VOLATILITY_SPIKE", SignalCategory.RISK, SignalDirection.NEUTRAL, "Unusual volatility",
        "20-bar historical volatility more than double its 6-month median.", 20, 20,
        lambda f: f.hv_20 > 2 * f.hv20_baseline,
        lambda r: [ev("HV20 (annualised)", r.hv_20), ev("Baseline (median of prior 120)", r.hv20_baseline)],
        lambda r: {"type": "none", "price": None, "condition": "Expires when volatility normalises"},
    ),
    Detector(
        "LIQUIDITY_DETERIORATION", SignalCategory.RISK, SignalDirection.NEUTRAL, "Liquidity deterioration",
        "Share of sessions with trades in the last 20 fell below half of the prior 6-month level.", 20, 20,
        lambda f: (f.trade_freq_prior_120 >= 0.6) & (f.trade_freq_20 < 0.5 * f.trade_freq_prior_120),
        lambda r: [ev("Trading frequency (20 sessions)", r.trade_freq_20),
                   ev("Prior trading frequency (120 sessions)", r.trade_freq_prior_120)],
        lambda r: {"type": "none", "price": None, "condition": "Expires after 20 bars"},
    ),
    Detector(
        "SIGNIFICANT_DRAWDOWN", SignalCategory.RISK, SignalDirection.BEARISH, "Significant drawdown",
        "Price fell more than 30% below its highest close in the available history.", 30, 60,
        lambda f: (f.drawdown <= -0.30) & (f.drawdown.shift(1) > -0.30),
        lambda r: [ev("Drawdown from peak", r.drawdown)],
        lambda r: {"type": "none", "price": None, "condition": "Informational"},
    ),
    Detector(
        "BEARISH_MOMENTUM", SignalCategory.RISK, SignalDirection.BEARISH, "Bearish momentum",
        "20-bar return below -10% with price below EMA20 < EMA50.", 15, 15,
        lambda f: (f.roc_20 < -0.10) & (f.close < f.ema_20) & (f.ema_20 < f.ema_50),
        lambda r: [ev("20-bar return", r.roc_20), ev("EMA20", r.ema_20), ev("EMA50", r.ema_50)],
        lambda r: above(r.ema_20, "EMA20"),
    ),
    Detector(
        "PRICE_GAP", SignalCategory.RISK, SignalDirection.NEUTRAL, "Abnormal price gap",
        "Opening gap above 8% (or close-to-close move above 10% where the open is not reported).", 5, 1,
        lambda f: (f.gap.abs() > 0.08) | (f.gap.isna() & (f.ret_1.abs() > 0.10)),
        lambda r: [ev("Opening gap", r.gap), ev("Close-to-close move", r.ret_1)],
        lambda r: {"type": "none", "price": None, "condition": "Informational"},
    ),
)


def risk_factors(r: pd.Series) -> list[str]:
    out = []
    tf = r.get("trade_freq_20")
    if pd.notna(tf) and tf < 0.6:
        out.append(f"Illiquid: trades on only {tf:.0%} of the last 20 sessions")
    hv = r.get("hv_60")
    if pd.notna(hv) and hv > 0.5:
        out.append(f"High volatility: {hv:.0%} annualised (60 bars)")
    if pd.isna(r.get("rel_volume")):
        out.append("Volume baseline unavailable")
    if pd.isna(r.get("high")):
        out.append("Provider did not report OHLC for this bar (close-only analysis)")
    dd = r.get("drawdown")
    if pd.notna(dd) and dd < -0.4:
        out.append(f"Deep drawdown: {dd:.0%} from peak")
    rsi = r.get("rsi_14")
    if pd.notna(rsi) and rsi > 75:
        out.append(f"Overbought: RSI(14) {rsi:.0f}")
    out.append("Round-trip transaction costs are roughly 4% on the NSE; small moves may not cover costs")
    return out


@dataclass
class Detection:
    detector: Detector
    ts: pd.Timestamp
    row: pd.Series


def detect(f: pd.DataFrame, only_last: bool = False, min_bars: int = 60) -> list[Detection]:
    out: list[Detection] = []
    if len(f) < min_bars:
        return out
    for d in TECHNICAL:
        try:
            cond = _edge(d.condition(f), d.cooldown)
        except (KeyError, AttributeError):
            continue
        cond.iloc[:min_bars] = False
        hits = cond[cond]
        if only_last:
            hits = hits[hits.index == f.index[-1]]
        for ts in hits.index:
            out.append(Detection(d, ts, f.loc[ts]))
    return out
