"""Derived analytics. Every row carries `calc_version` so outputs are reproducible and auditable."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from nse_platform.models.base import Base, enum_check
from nse_platform.models.enums import SignalCategory, SignalDirection, SignalStatus, Timeframe, TrendClass


class TechnicalIndicatorRow(Base):
    """All indicator values for one bar, computed only from data up to and including that bar."""

    __tablename__ = "technical_indicators"
    __table_args__ = (enum_check("timeframe", Timeframe),)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id", ondelete="CASCADE"), primary_key=True)
    timeframe: Mapped[str] = mapped_column(String(4), primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    calc_version: Mapped[str] = mapped_column(String(16), primary_key=True)
    values: Mapped[dict[str, float | None]] = mapped_column(JSONB)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TrendAnalysis(Base):
    __tablename__ = "trend_analysis"
    __table_args__ = (enum_check("classification", TrendClass), enum_check("timeframe", Timeframe))
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id", ondelete="CASCADE"), primary_key=True)
    timeframe: Mapped[str] = mapped_column(String(4), primary_key=True)
    as_of_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    calc_version: Mapped[str] = mapped_column(String(16), primary_key=True)
    classification: Mapped[str] = mapped_column(String(24))
    strength: Mapped[float | None] = mapped_column(Float)  # 0..100 trend-strength (ADX-like), NULL if n/a
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SupportResistanceLevel(Base):
    __tablename__ = "support_resistance_levels"
    __table_args__ = (Index("ix_sr_lookup", "instrument_id", "timeframe", "as_of_ts"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id", ondelete="CASCADE"))
    timeframe: Mapped[str] = mapped_column(String(4))
    as_of_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    level_type: Mapped[str] = mapped_column(String(12))  # SUPPORT | RESISTANCE
    price: Mapped[float] = mapped_column(Float)
    touches: Mapped[int] = mapped_column(Integer)
    first_touch_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_touch_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    calc_version: Mapped[str] = mapped_column(String(16))


class InstrumentSnapshot(Base):
    """Latest per-instrument metrics powering the scanner (volatility, relative strength, liquidity, scores).

    One row per (instrument, timeframe); replaced on each analytics run. Historical values are reconstructible
    from price_bars + calc_version, so history is not duplicated here.
    """

    __tablename__ = "instrument_snapshots"
    __table_args__ = (enum_check("timeframe", Timeframe),)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id", ondelete="CASCADE"), primary_key=True)
    timeframe: Mapped[str] = mapped_column(String(4), primary_key=True)
    as_of_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    session_date: Mapped[date | None] = mapped_column(Date)
    calc_version: Mapped[str] = mapped_column(String(16))
    bars_available: Mapped[int] = mapped_column(Integer)
    last_close: Mapped[float | None] = mapped_column(Float)
    prev_close: Mapped[float | None] = mapped_column(Float)
    change_pct: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[int | None] = mapped_column(BigInteger)
    avg_volume_20: Mapped[float | None] = mapped_column(Float)
    relative_volume: Mapped[float | None] = mapped_column(Float)
    avg_traded_value_20: Mapped[float | None] = mapped_column(Float)
    trading_frequency_60: Mapped[float | None] = mapped_column(Float)  # share of sessions with volume > 0
    rsi_14: Mapped[float | None] = mapped_column(Float)
    return_1w: Mapped[float | None] = mapped_column(Float)
    return_1m: Mapped[float | None] = mapped_column(Float)
    return_3m: Mapped[float | None] = mapped_column(Float)
    return_6m: Mapped[float | None] = mapped_column(Float)
    return_1y: Mapped[float | None] = mapped_column(Float)
    return_ytd: Mapped[float | None] = mapped_column(Float)
    volatility_20: Mapped[float | None] = mapped_column(Float)  # annualised, from log returns
    volatility_60: Mapped[float | None] = mapped_column(Float)
    atr_pct_14: Mapped[float | None] = mapped_column(Float)
    max_drawdown_1y: Mapped[float | None] = mapped_column(Float)
    drawdown_from_high: Mapped[float | None] = mapped_column(Float)
    high_52w: Mapped[float | None] = mapped_column(Float)
    low_52w: Mapped[float | None] = mapped_column(Float)
    relative_strength_3m: Mapped[float | None] = mapped_column(Float)  # percentile 0..100 within universe
    dividend_yield: Mapped[float | None] = mapped_column(Float)
    pe_ratio: Mapped[float | None] = mapped_column(Float)
    trend_class: Mapped[str | None] = mapped_column(String(24))
    trading_score: Mapped[float | None] = mapped_column(Float)
    quality_score: Mapped[float | None] = mapped_column(Float)
    risk_score: Mapped[float | None] = mapped_column(Float)
    score_details: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OpportunitySignal(Base):
    __tablename__ = "opportunity_signals"
    __table_args__ = (
        UniqueConstraint("instrument_id", "signal_type", "timeframe", "bar_ts", "calc_version"),
        enum_check("category", SignalCategory),
        enum_check("direction", SignalDirection),
        enum_check("status", SignalStatus),
        Index("ix_signals_status_detected", "status", "detected_at"),
        Index("ix_signals_type", "signal_type", "timeframe"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id", ondelete="CASCADE"), index=True)
    category: Mapped[str] = mapped_column(String(16))
    signal_type: Mapped[str] = mapped_column(String(48))
    direction: Mapped[str] = mapped_column(String(8))
    timeframe: Mapped[str] = mapped_column(String(4))
    bar_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # bar on which the signal was detected
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reference_price: Mapped[float] = mapped_column(Float)
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    invalidation: Mapped[dict[str, Any]] = mapped_column(JSONB)  # {"price": x, "condition": "close below ..."}
    risk_factors: Mapped[list[str]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(12), default=SignalStatus.ACTIVE.value)
    status_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    is_backfill: Mapped[bool] = mapped_column(default=False)  # detected by historical replay, not live
    calc_version: Mapped[str] = mapped_column(String(16))


class AnalyticsReport(Base):
    """Versioned validation / calibration outputs (e.g. score rank-IC, signal base rates) per data universe."""

    __tablename__ = "analytics_reports"
    code: Mapped[str] = mapped_column(String(48), primary_key=True)
    universe: Mapped[str] = mapped_column(String(16), primary_key=True)  # real | synthetic
    calc_version: Mapped[str] = mapped_column(String(16))
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)


class OpportunityOutcome(Base):
    """Forward performance of a signal after N bars - the basis for any historical base rates shown in the UI."""

    __tablename__ = "opportunity_outcomes"
    signal_id: Mapped[int] = mapped_column(ForeignKey("opportunity_signals.id", ondelete="CASCADE"), primary_key=True)
    horizon_bars: Mapped[int] = mapped_column(Integer, primary_key=True)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    end_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    forward_return: Mapped[float] = mapped_column(Float)
    max_favorable: Mapped[float] = mapped_column(Float)
    max_adverse: Mapped[float] = mapped_column(Float)
    invalidated: Mapped[bool] = mapped_column()
