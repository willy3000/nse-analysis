"""Reference data and market data tables."""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from nse_platform.models.base import Base, TimestampMixin, enum_check
from nse_platform.models.enums import (
    LatencyClass,
    ProviderStatus,
    QualityStatus,
    RunStatus,
    SecurityType,
    SessionStatus,
    Timeframe,
    TradingStatus,
)

# --------------------------------------------------------------------------- reference


class Exchange(Base):
    __tablename__ = "exchanges"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(16), unique=True)
    mic: Mapped[str | None] = mapped_column(String(8))
    name: Mapped[str] = mapped_column(String(128))
    country: Mapped[str] = mapped_column(String(2))
    currency: Mapped[str] = mapped_column(String(3))
    timezone: Mapped[str] = mapped_column(String(64))
    open_time: Mapped[time | None] = mapped_column(Time)
    close_time: Mapped[time | None] = mapped_column(Time)


class Sector(Base):
    __tablename__ = "sectors"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)


class Industry(Base):
    __tablename__ = "industries"
    __table_args__ = (UniqueConstraint("sector_id", "name"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sector_id: Mapped[int] = mapped_column(ForeignKey("sectors.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(128))


class Issuer(TimestampMixin, Base):
    __tablename__ = "issuers"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(256), unique=True)
    country: Mapped[str | None] = mapped_column(String(2))
    website: Mapped[str | None] = mapped_column(String(512))


class DataProvider(TimestampMixin, Base):
    __tablename__ = "data_providers"
    __table_args__ = (enum_check("latency_class", LatencyClass),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    latency_class: Mapped[str] = mapped_column(String(16))
    delay_minutes: Mapped[int | None] = mapped_column(Integer)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    priority: Mapped[int] = mapped_column(Integer, default=100)  # lower = preferred source
    attribution: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)


class Instrument(TimestampMixin, Base):
    __tablename__ = "instruments"
    __table_args__ = (
        UniqueConstraint("exchange_id", "symbol"),
        enum_check("security_type", SecurityType),
        enum_check("trading_status", TradingStatus),
        Index("ix_instruments_sector", "sector_id"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    exchange_id: Mapped[int] = mapped_column(ForeignKey("exchanges.id"))
    symbol: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(256))
    security_type: Mapped[str] = mapped_column(String(16))
    sector_id: Mapped[int | None] = mapped_column(ForeignKey("sectors.id"))
    industry_id: Mapped[int | None] = mapped_column(ForeignKey("industries.id"))
    issuer_id: Mapped[int | None] = mapped_column(ForeignKey("issuers.id"))
    currency: Mapped[str] = mapped_column(String(3))
    isin: Mapped[str | None] = mapped_column(String(12))
    listing_date: Mapped[date | None] = mapped_column(Date)
    trading_status: Mapped[str] = mapped_column(String(16), default=TradingStatus.UNKNOWN.value)
    lot_size: Mapped[int | None] = mapped_column(Integer)
    # Bond-specific reference fields (NULL for other security types)
    coupon_rate: Mapped[float | None] = mapped_column(Float)
    maturity_date: Mapped[date | None] = mapped_column(Date)
    face_value: Mapped[float | None] = mapped_column(Float)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    primary_provider_id: Mapped[int | None] = mapped_column(ForeignKey("data_providers.id"))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    exchange: Mapped[Exchange] = relationship()
    sector: Mapped[Sector | None] = relationship()
    industry: Mapped[Industry | None] = relationship()
    issuer: Mapped[Issuer | None] = relationship()
    primary_provider: Mapped[DataProvider | None] = relationship()


class InstrumentIdentifier(Base):
    """Maps provider-specific symbols to our instrument ids (e.g. MyStocks 'SCOM.KE' -> instruments.id)."""

    __tablename__ = "instrument_identifiers"
    __table_args__ = (UniqueConstraint("provider_id", "provider_symbol"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id", ondelete="CASCADE"), index=True)
    provider_id: Mapped[int] = mapped_column(ForeignKey("data_providers.id", ondelete="CASCADE"))
    provider_symbol: Mapped[str] = mapped_column(String(64))


class MarketSession(Base):
    __tablename__ = "market_sessions"
    __table_args__ = (UniqueConstraint("exchange_id", "session_date"), enum_check("status", SessionStatus))
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    exchange_id: Mapped[int] = mapped_column(ForeignKey("exchanges.id"))
    session_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str | None] = mapped_column(String(256))
    source_provider_id: Mapped[int | None] = mapped_column(ForeignKey("data_providers.id"))


# --------------------------------------------------------------------------- market data


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"
    __table_args__ = (enum_check("status", RunStatus), Index("ix_ingestion_runs_job_started", "job_name", "started_at"))
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    job_name: Mapped[str] = mapped_column(String(64))
    provider_id: Mapped[int | None] = mapped_column(ForeignKey("data_providers.id"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default=RunStatus.RUNNING.value)
    items_requested: Mapped[int] = mapped_column(Integer, default=0)
    items_succeeded: Mapped[int] = mapped_column(Integer, default=0)
    items_failed: Mapped[int] = mapped_column(Integer, default=0)
    rows_written: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class Quote(Base):
    """A provider quote observation. Never overwritten: one row per (instrument, provider, market_ts)."""

    __tablename__ = "quotes"
    __table_args__ = (
        enum_check("latency_class", LatencyClass),
        enum_check("quality_status", QualityStatus),
        Index("ix_quotes_instrument_ts", "instrument_id", "market_ts"),
    )
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id", ondelete="CASCADE"), primary_key=True)
    provider_id: Mapped[int] = mapped_column(ForeignKey("data_providers.id"), primary_key=True)
    market_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)  # exchange obs time
    source_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # provider's response time
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    price: Mapped[float | None] = mapped_column(Float)
    open: Mapped[float | None] = mapped_column(Float)
    high: Mapped[float | None] = mapped_column(Float)
    low: Mapped[float | None] = mapped_column(Float)
    previous_close: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[int | None] = mapped_column(BigInteger)
    bid: Mapped[float | None] = mapped_column(Float)
    ask: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(3))
    latency_class: Mapped[str] = mapped_column(String(16))
    delay_minutes: Mapped[int | None] = mapped_column(Integer)
    provider_stale: Mapped[bool | None] = mapped_column(Boolean)
    quality_status: Mapped[str] = mapped_column(String(16), default=QualityStatus.OK.value)
    ingestion_run_id: Mapped[int | None] = mapped_column(ForeignKey("ingestion_runs.id", ondelete="SET NULL"))


class PriceBar(Base):
    """Raw (unadjusted) OHLCV bars exactly as reported. Adjusted series are derived, never stored over these."""

    __tablename__ = "price_bars"
    __table_args__ = (
        enum_check("timeframe", Timeframe),
        enum_check("quality_status", QualityStatus),
        Index("ix_price_bars_lookup", "instrument_id", "timeframe", "ts"),
    )
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id", ondelete="CASCADE"), primary_key=True)
    provider_id: Mapped[int] = mapped_column(ForeignKey("data_providers.id"), primary_key=True)
    timeframe: Mapped[str] = mapped_column(String(4), primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)  # bar start, UTC
    session_date: Mapped[date | None] = mapped_column(Date)  # exchange-local trading date (daily bars)
    open: Mapped[float | None] = mapped_column(Float)
    high: Mapped[float | None] = mapped_column(Float)
    low: Mapped[float | None] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[int | None] = mapped_column(BigInteger)  # NULL = not reported (distinct from 0 = no trades)
    turnover: Mapped[float | None] = mapped_column(Float)
    trades_count: Mapped[int | None] = mapped_column(Integer)
    ohlc_available: Mapped[bool] = mapped_column(Boolean, default=True)
    volume_available: Mapped[bool] = mapped_column(Boolean, default=True)
    quality_status: Mapped[str] = mapped_column(String(16), default=QualityStatus.OK.value)
    currency: Mapped[str] = mapped_column(String(3))
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    revised_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ingestion_run_id: Mapped[int | None] = mapped_column(ForeignKey("ingestion_runs.id", ondelete="SET NULL"))


class PriceBarRevision(Base):
    """Previous values of a bar that a provider later revised. Original observations are never lost."""

    __tablename__ = "price_bar_revisions"
    __table_args__ = (Index("ix_pbr_lookup", "instrument_id", "timeframe", "ts"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id", ondelete="CASCADE"))
    provider_id: Mapped[int] = mapped_column(ForeignKey("data_providers.id"))
    timeframe: Mapped[str] = mapped_column(String(4))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    previous: Mapped[dict[str, Any]] = mapped_column(JSONB)
    replaced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ingestion_run_id: Mapped[int | None] = mapped_column(ForeignKey("ingestion_runs.id", ondelete="SET NULL"))


class MarketIndex(Base):
    __tablename__ = "market_indices"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    exchange_id: Mapped[int] = mapped_column(ForeignKey("exchanges.id"))
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)


class IndexHistory(Base):
    __tablename__ = "index_history"
    index_id: Mapped[int] = mapped_column(ForeignKey("market_indices.id", ondelete="CASCADE"), primary_key=True)
    provider_id: Mapped[int] = mapped_column(ForeignKey("data_providers.id"), primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    session_date: Mapped[date | None] = mapped_column(Date)
    open: Mapped[float | None] = mapped_column(Float)
    high: Mapped[float | None] = mapped_column(Float)
    low: Mapped[float | None] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DataSourceHealth(Base):
    __tablename__ = "data_source_health"
    __table_args__ = (enum_check("status", ProviderStatus),)
    provider_id: Mapped[int] = mapped_column(ForeignKey("data_providers.id", ondelete="CASCADE"), primary_key=True)
    status: Mapped[str] = mapped_column(String(16))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    latest_market_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rate_limit_limit: Mapped[int | None] = mapped_column(Integer)
    rate_limit_remaining: Mapped[int | None] = mapped_column(Integer)
    rate_limit_reset_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class DataQualityIssue(Base):
    __tablename__ = "data_quality_issues"
    __table_args__ = (Index("ix_dq_open", "resolved_at", "detected_at"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    instrument_id: Mapped[int | None] = mapped_column(ForeignKey("instruments.id", ondelete="CASCADE"))
    provider_id: Mapped[int | None] = mapped_column(ForeignKey("data_providers.id"))
    issue_type: Mapped[str] = mapped_column(String(48))
    severity: Mapped[str] = mapped_column(String(8))  # info | warn | error
    observed_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    ingestion_run_id: Mapped[int | None] = mapped_column(ForeignKey("ingestion_runs.id", ondelete="SET NULL"))
