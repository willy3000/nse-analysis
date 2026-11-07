"""Three independent, explainable scores (0-100). Versioned and configurable methodology.

These are *cross-sectional rankings* within a data universe (real or synthetic), not forecasts. Each component
is converted to a 0-100 sub-score (mostly percentile ranks within the universe), then combined with documented
weights. Missing components are excluded and the remaining weights renormalised; if the available weight falls
below `min_coverage` the score is None ("insufficient data"). The predictive value of the Trading Opportunity
Score is measured, not assumed: see analytics/validation.py (rank IC vs forward returns).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

SCORE_CALC_VERSION = "score-1.0.0"
MIN_UNIVERSE = 5


@dataclass(frozen=True)
class Component:
    key: str
    weight: float
    label: str
    method: str  # pct_high | pct_low | map | clip01
    description: str


TREND_POINTS = {
    "STRONG_BULLISH": 100, "EMERGING_BULLISH": 75, "BULLISH_PULLBACK": 65, "POTENTIAL_REVERSAL": 50,
    "SIDEWAYS": 40, "WEAKENING_BULLISH": 30, "EMERGING_BEARISH": 20, "STRONG_BEARISH": 5,
}

METHODOLOGY: dict[str, dict[str, Any]] = {
    "trading": {
        "label": "Trading Opportunity Score",
        "higher_is": "more technically attractive (relative to the universe)",
        "min_coverage": 0.6,
        "components": [
            Component("return_3m", 0.20, "Momentum (3-month return)", "pct_high", "Percentile of 3-month return"),
            Component("trend_class", 0.25, "Trend classification (daily)", "map", "Points per trend class"),
            Component("relative_volume", 0.10, "Relative volume", "clip01", "min(relative volume, 3) / 3"),
            Component("proximity_52w_high", 0.15, "Breakout structure", "pct_high",
                      "Percentile of close / 52-week high"),
            Component("relative_strength_3m", 0.15, "Relative strength", "direct",
                      "Universe percentile of 3-month return vs universe median"),
            Component("liquidity", 0.15, "Liquidity", "pct_high",
                      "Percentile of trading frequency x average traded value"),
        ],
    },
    "quality": {
        "label": "Investment Quality Score",
        "higher_is": "stronger business quality and valuation (relative to the universe)",
        "min_coverage": 0.5,
        "components": [
            Component("revenue_growth", 0.10, "Revenue growth", "pct_high", "Latest FY revenue growth"),
            Component("earnings_growth", 0.10, "Earnings growth", "pct_high", "Latest FY net income growth"),
            Component("net_margin", 0.10, "Profitability (net margin)", "pct_high", "Net income / revenue"),
            Component("roe", 0.15, "Return on equity", "pct_high", "Net income / average equity"),
            Component("debt_to_equity", 0.10, "Leverage", "pct_low", "Lower debt/equity scores higher"),
            Component("cash_conversion", 0.10, "Cash flow quality", "pct_high", "Operating cash flow / net income"),
            Component("pe_ratio", 0.10, "Valuation (P/E)", "pct_low", "Lower P/E scores higher (positive EPS only)"),
            Component("pb_ratio", 0.05, "Valuation (P/B)", "pct_low", "Lower P/B scores higher"),
            Component("dividend_yield", 0.10, "Dividend yield", "pct_high", "Trailing 12-month yield"),
            Component("dividend_years_streak", 0.10, "Dividend consistency", "pct_high",
                      "Consecutive years with a cash dividend"),
        ],
    },
    "risk": {
        "label": "Risk Score",
        "higher_is": "MORE risk",
        "min_coverage": 0.5,
        "components": [
            Component("volatility_60", 0.25, "Volatility", "pct_high", "60-bar annualised volatility"),
            Component("max_drawdown_1y", 0.20, "Drawdown", "pct_low", "1-year max drawdown (more negative = riskier)"),
            Component("illiquidity", 0.20, "Liquidity risk", "pct_high", "1 - trading frequency (60 sessions)"),
            Component("gap_count_1y", 0.10, "Price gaps", "pct_high", "Moves >8% in a session over 1 year"),
            Component("debt_to_equity", 0.10, "Financial leverage", "pct_high", "Debt / equity"),
            Component("event_risk", 0.10, "Corporate events", "direct",
                      "100 if suspended, 60 if a suspension/restructuring event in the last year, else 0"),
            Component("data_risk", 0.05, "Data reliability", "direct",
                      "Share of recent bars with partial/suspect quality and open data-quality issues"),
        ],
    },
}


def _pct(series: pd.Series, high_good: bool) -> pd.Series:
    valid = series.dropna()
    if len(valid) < MIN_UNIVERSE:
        return pd.Series(np.nan, index=series.index)
    r = series.rank(pct=True, method="average")
    return (r if high_good else 1 - r + 1 / len(valid)) * 100


def compute_scores(frame: pd.DataFrame) -> dict[int, dict[str, Any]]:
    """`frame` indexed by instrument_id with one column per component key. Returns per-instrument scores."""
    sub: dict[str, pd.DataFrame] = {}
    for name, spec in METHODOLOGY.items():
        cols = {}
        for comp in spec["components"]:
            raw = frame.get(comp.key, pd.Series(np.nan, index=frame.index))
            if comp.method == "pct_high":
                cols[comp.key] = _pct(pd.to_numeric(raw, errors="coerce"), True)
            elif comp.method == "pct_low":
                cols[comp.key] = _pct(pd.to_numeric(raw, errors="coerce"), False)
            elif comp.method == "map":
                cols[comp.key] = raw.map(TREND_POINTS).astype(float)
            elif comp.method == "clip01":
                cols[comp.key] = pd.to_numeric(raw, errors="coerce").clip(0, 3) / 3 * 100
            else:
                cols[comp.key] = pd.to_numeric(raw, errors="coerce").clip(0, 100)
        sub[name] = pd.DataFrame(cols, index=frame.index)

    out: dict[int, dict[str, Any]] = {}
    for iid in frame.index:
        res: dict[str, Any] = {"calc_version": SCORE_CALC_VERSION}
        for name, spec in METHODOLOGY.items():
            comps, total_w, acc = [], 0.0, 0.0
            for comp in spec["components"]:
                v = sub[name].at[iid, comp.key]
                raw = frame.at[iid, comp.key] if comp.key in frame.columns else None
                available = pd.notna(v)
                comps.append({
                    "key": comp.key, "label": comp.label, "weight": comp.weight, "description": comp.description,
                    "raw": None if raw is None or (isinstance(raw, float) and not np.isfinite(raw)) else
                    (raw if isinstance(raw, str) else round(float(raw), 6) if pd.notna(raw) else None),
                    "sub_score": round(float(v), 2) if available else None, "available": bool(available),
                })
                if available:
                    total_w += comp.weight
                    acc += comp.weight * float(v)
            coverage = total_w / sum(c.weight for c in spec["components"])
            score = round(acc / total_w, 1) if total_w > 0 and coverage >= spec["min_coverage"] else None
            res[name] = {"score": score, "coverage": round(coverage, 3), "components": comps,
                         "insufficient_data": score is None, "higher_is": spec["higher_is"]}
        out[int(iid)] = res
    return out


def methodology_document() -> dict[str, Any]:
    return {
        "calc_version": SCORE_CALC_VERSION,
        "minimum_universe": MIN_UNIVERSE,
        "trend_points": TREND_POINTS,
        "scores": {
            k: {"label": v["label"], "higher_is": v["higher_is"], "min_coverage": v["min_coverage"],
                "components": [c.__dict__ for c in v["components"]]}
            for k, v in METHODOLOGY.items()
        },
        "disclaimer": "Scores rank instruments relative to each other within the same data universe using "
                      "current data. They are not forecasts and have not been shown to predict returns unless "
                      "the calibration report says so.",
    }
