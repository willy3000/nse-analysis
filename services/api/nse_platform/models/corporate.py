"""Corporate actions, fundamentals, company profiles and news."""

from __future__ import annotations

from datetime import date, datetime
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
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from nse_platform.models.base import Base, TimestampMixin, enum_check
from nse_platform.models.enums import CorporateActionType, NewsKind, PeriodType, StatementType


class CorporateAction(TimestampMixin, Base):
    """Dividends, splits, bonus issues, rights, suspensions. `dividend_history` is a SQL view over this table."""

    __tablename__ = "corporate_actions"
    __table_args__ = (
        UniqueConstraint("provider_id", "external_id"),
        enum_check("action_type", CorporateActionType),
        Index("ix_ca_instrument_ex", "instrument_id", "ex_date"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id", ondelete="CASCADE"))
    provider_id: Mapped[int] = mapped_column(ForeignKey("data_providers.id"))
    external_id: Mapped[str] = mapped_column(String(128))
    action_type: Mapped[str] = mapped_column(String(24))
    status: Mapped[str | None] = mapped_column(String(32))  # DECLARED / PAID / CANCELLED / EFFECTIVE ...
    title: Mapped[str | None] = mapped_column(String(512))
    announced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ex_date: Mapped[date | None] = mapped_column(Date)
    record_date: Mapped[date | None] = mapped_column(Date)
    payment_date: Mapped[date | None] = mapped_column(Date)
    effective_date: Mapped[date | None] = mapped_column(Date)
    cash_amount: Mapped[float | None] = mapped_column(Float)  # per share, in `currency`
    currency: Mapped[str | None] = mapped_column(String(3))
    # Share ratio: new_shares = old_shares * ratio_to / ratio_from (e.g. 1:1 bonus => from=1, to=2)
    ratio_from: Mapped[float | None] = mapped_column(Float)
    ratio_to: Mapped[float | None] = mapped_column(Float)
    source_url: Mapped[str | None] = mapped_column(String(1024))
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class CompanyProfile(Base):
    __tablename__ = "company_profiles"
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id", ondelete="CASCADE"), primary_key=True)
    provider_id: Mapped[int] = mapped_column(ForeignKey("data_providers.id"))
    description: Mapped[str | None] = mapped_column(Text)
    website: Mapped[str | None] = mapped_column(String(512))
    country: Mapped[str | None] = mapped_column(String(2))
    market_cap: Mapped[float | None] = mapped_column(Float)
    provider_pe_ratio: Mapped[float | None] = mapped_column(Float)  # as reported by provider; we also compute our own
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FinancialStatementItem(Base):
    """Long-format financial statement line items with publication date (prevents look-ahead in backtests)."""

    __tablename__ = "financial_statements"
    __table_args__ = (
        UniqueConstraint("instrument_id", "period_end", "period_type", "statement_type", "line_item", "source"),
        enum_check("statement_type", StatementType),
        enum_check("period_type", PeriodType),
        Index("ix_fs_instrument_pub", "instrument_id", "published_at"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id", ondelete="CASCADE"))
    period_end: Mapped[date] = mapped_column(Date)
    period_type: Mapped[str] = mapped_column(String(4))
    statement_type: Mapped[str] = mapped_column(String(12))
    line_item: Mapped[str] = mapped_column(String(64))  # canonical key, e.g. revenue, net_income, total_equity
    value: Mapped[float] = mapped_column(Float)  # in base units of `currency` (unit multiplier already applied)
    currency: Mapped[str] = mapped_column(String(3))
    reported_unit: Mapped[str | None] = mapped_column(String(16))  # e.g. "KES'000" as published
    published_at: Mapped[date] = mapped_column(Date)  # date the figure became public
    source: Mapped[str] = mapped_column(String(256))  # e.g. "Annual report 2024, p.45" or provider code
    source_url: Mapped[str | None] = mapped_column(String(1024))
    quality: Mapped[str] = mapped_column(String(16), default="REPORTED")  # REPORTED | DERIVED | RESTATED
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FundamentalMetric(Base):
    """Computed point-in-time fundamental metric (e.g. pe_ratio as of date X using only info public by X)."""

    __tablename__ = "fundamental_metrics"
    __table_args__ = (Index("ix_fm_lookup", "instrument_id", "metric", "as_of"),)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id", ondelete="CASCADE"), primary_key=True)
    metric: Mapped[str] = mapped_column(String(48), primary_key=True)
    as_of: Mapped[date] = mapped_column(Date, primary_key=True)
    calc_version: Mapped[str] = mapped_column(String(16), primary_key=True)
    value: Mapped[float | None] = mapped_column(Float)
    period_end: Mapped[date | None] = mapped_column(Date)
    inputs: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NewsArticle(Base):
    __tablename__ = "news_articles"
    __table_args__ = (
        UniqueConstraint("provider_id", "external_id"),
        enum_check("kind", NewsKind),
        Index("ix_news_published", "published_at"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    provider_id: Mapped[int] = mapped_column(ForeignKey("data_providers.id"))
    external_id: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(24))
    category: Mapped[str | None] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(1024))
    publisher: Mapped[str | None] = mapped_column(String(256))
    url: Mapped[str | None] = mapped_column(String(2048))
    summary: Mapped[str | None] = mapped_column(Text)
    summary_is_generated: Mapped[bool] = mapped_column(Boolean, default=False)  # AI/derived summary label
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    source_reliability: Mapped[str] = mapped_column(String(16), default="PROVIDER")  # OFFICIAL|PROVIDER|MEDIA
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)


class NewsArticleInstrument(Base):
    __tablename__ = "news_article_instruments"
    article_id: Mapped[int] = mapped_column(ForeignKey("news_articles.id", ondelete="CASCADE"), primary_key=True)
    instrument_id: Mapped[int] = mapped_column(
        ForeignKey("instruments.id", ondelete="CASCADE"), primary_key=True, index=True
    )
