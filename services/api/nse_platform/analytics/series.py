"""Loading price series, corporate-action adjustment, and timeframe resampling.

Adjustment policy (reproducible, non-destructive):
* price_bars always hold raw provider values.
* Share-ratio events (BONUS, SPLIT, CONSOLIDATION) with ratio_from -> ratio_to produce a factor
  f = ratio_from / ratio_to applied to all bars strictly before the ex/effective date
  (prices * f, volumes / f). Factors compound across events.
* Cash dividends do NOT adjust the technical series; total-return analysis treats dividends as cash flows.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from nse_platform.models import CorporateAction, Instrument, PriceBar
from nse_platform.models.enums import CorporateActionType

ADJUSTMENT_CALC_VERSION = "adj-1.0.0"
RATIO_TYPES = (CorporateActionType.BONUS.value, CorporateActionType.SPLIT.value,
               CorporateActionType.CONSOLIDATION.value)
COLUMNS = ["open", "high", "low", "close", "volume", "ohlc_available", "volume_available", "quality_status"]


@dataclass(frozen=True)
class AdjustmentEvent:
    ex_date: date
    factor: float  # price multiplier for bars before ex_date
    action_id: int | None = None
    description: str = ""


BAD_PRINT_RATIO = 3.0


def exclude_bad_prints(df: pd.DataFrame, window: int = 5) -> pd.DataFrame:
    """Drop bars whose close is > 3x away from the median close of up to `window` sessions on each side.

    The provider's history contains isolated unit errors (e.g. 2.20 reported as 220.00 for a day). Such bars
    were never tradeable, so they are excluded from analytics, base rates and backtests. Raw bars stay stored
    (quality SUSPECT). Uses neighbouring bars on both sides, so the most recent bar - which has no later
    neighbours - is judged only against earlier ones (window still requires 3 neighbours).
    """
    if len(df) < 7:
        return df
    c = df["close"].to_numpy(dtype=float)
    keep = np.ones(len(c), dtype=bool)
    for i in range(len(c)):
        lo, hi = max(0, i - window), min(len(c), i + window + 1)
        neigh = np.concatenate([c[lo:i], c[i + 1:hi]])
        neigh = neigh[np.isfinite(neigh)]
        if len(neigh) < 3:
            continue
        med = float(np.median(neigh))
        if med > 0 and (c[i] / med > BAD_PRINT_RATIO or c[i] / med < 1 / BAD_PRINT_RATIO):
            keep[i] = False
    return df[keep]


def load_daily(session: Session, instrument: Instrument, provider_id: int | None = None) -> pd.DataFrame:
    pid = provider_id or instrument.primary_provider_id
    rows = session.execute(
        select(PriceBar.ts, PriceBar.open, PriceBar.high, PriceBar.low, PriceBar.close, PriceBar.volume,
               PriceBar.ohlc_available, PriceBar.volume_available, PriceBar.quality_status)
        .where(PriceBar.instrument_id == instrument.id, PriceBar.provider_id == pid, PriceBar.timeframe == "1d")
        .order_by(PriceBar.ts)
    ).all()
    return exclude_bad_prints(frame_from_rows(rows))


def frame_from_rows(rows: list) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=COLUMNS, index=pd.DatetimeIndex([], name="date"))
    df = pd.DataFrame(rows, columns=["ts", *COLUMNS])
    df.index = pd.DatetimeIndex(pd.to_datetime(df.pop("ts"), utc=True).dt.tz_localize(None).dt.normalize(),
                                name="date")
    for col in ("open", "high", "low", "close"):
        df[col] = df[col].astype(float)
    df["volume"] = df["volume"].astype(float)  # NaN = not reported
    return df


def adjustment_events(session: Session, instrument_id: int) -> list[AdjustmentEvent]:
    rows = session.scalars(select(CorporateAction).where(
        CorporateAction.instrument_id == instrument_id, CorporateAction.action_type.in_(RATIO_TYPES))).all()
    events = []
    for a in rows:
        d = a.ex_date or a.effective_date
        if d is None or not a.ratio_from or not a.ratio_to or a.ratio_from <= 0 or a.ratio_to <= 0:
            continue
        if (a.status or "").upper() == "CANCELLED":
            continue
        events.append(AdjustmentEvent(d, a.ratio_from / a.ratio_to, a.id, a.title or a.action_type))
    return sorted(events, key=lambda e: e.ex_date)


def apply_adjustments(df: pd.DataFrame, events: list[AdjustmentEvent], as_of: date | None = None) -> pd.DataFrame:
    """Return an adjusted copy. Only events with ex_date <= as_of are applied (point-in-time safe)."""
    if df.empty or not events:
        return df.copy()
    out = df.copy()
    factor = pd.Series(1.0, index=out.index)
    for e in events:
        if as_of is not None and e.ex_date > as_of:
            continue
        factor[out.index < pd.Timestamp(e.ex_date)] *= e.factor
    for col in ("open", "high", "low", "close"):
        out[col] = out[col] * factor
    out["volume"] = out["volume"] / factor
    return out


def resample(df: pd.DataFrame, timeframe: str, as_of: date | None = None) -> pd.DataFrame:
    """Aggregate daily bars into weekly (Mon-start) or monthly (1st-start) bars.

    High/low/volume aggregate strictly: if any constituent value is missing the aggregate is missing.
    `is_partial` marks a period that has not finished as of `as_of` (default: last bar date).
    """
    if timeframe == "1d" or df.empty:
        out = df.copy()
        out["is_partial"] = False
        return out
    rule = {"1w": "W-MON", "1mo": "MS"}[timeframe]
    label_kwargs = {"label": "left", "closed": "left"}
    g = df.resample(rule, **label_kwargs)

    def strict(series: pd.Series, how: str) -> pd.Series:
        agg = getattr(series.resample(rule, **label_kwargs), how)()
        has_nan = series.isna().resample(rule, **label_kwargs).max().astype(bool)
        return agg.where(~has_nan)

    out = pd.DataFrame({
        "open": g["open"].first(), "high": strict(df["high"], "max"), "low": strict(df["low"], "min"),
        "close": g["close"].last(), "volume": strict(df["volume"], "sum"),
        "n_sessions": g["close"].count(),
    })
    out["ohlc_available"] = out[["open", "high", "low"]].notna().all(axis=1)
    out = out[out["n_sessions"] > 0]
    last = pd.Timestamp(as_of) if as_of else df.index.max()
    if timeframe == "1w":
        period_end = out.index + pd.Timedelta(days=4)  # Friday
    else:
        period_end = out.index + pd.offsets.MonthEnd(0)
    # A period is partial if its last calendar trading day is after the as-of date.
    out["is_partial"] = np.asarray(period_end > last)
    return out
