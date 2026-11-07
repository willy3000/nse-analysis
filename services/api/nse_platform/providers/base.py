"""Provider-independent market-data contract.

Adapters translate provider payloads into these normalized records. Nothing downstream of the ingestion layer
ever sees a provider-specific response shape.
"""

from __future__ import annotations

import abc
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime

from nse_platform.models.enums import (
    CorporateActionType,
    LatencyClass,
    NewsKind,
    SecurityType,
    Timeframe,
    TradingStatus,
)


class ProviderError(Exception):
    """Base class for provider failures. `retryable` indicates whether a retry might succeed."""

    retryable = False


class ProviderNotConfigured(ProviderError):
    pass


class ProviderAuthError(ProviderError):
    pass


class ProviderRateLimited(ProviderError):
    retryable = True

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class ProviderUnavailable(ProviderError):
    retryable = True


class ProviderBadResponse(ProviderError):
    pass


class NotSupported(ProviderError):
    pass


@dataclass(frozen=True)
class InstrumentRecord:
    provider_symbol: str
    symbol: str
    name: str
    security_type: SecurityType
    exchange_code: str
    currency: str
    trading_status: TradingStatus = TradingStatus.UNKNOWN
    sector: str | None = None
    isin: str | None = None
    lot_size: int | None = None
    issuer: str | None = None
    coupon_rate: float | None = None
    maturity_date: date | None = None
    face_value: float | None = None


@dataclass(frozen=True)
class QuoteRecord:
    provider_symbol: str
    market_ts: datetime  # exchange observation time (tz-aware UTC)
    price: float | None
    currency: str
    latency_class: LatencyClass
    source_ts: datetime | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    previous_close: float | None = None
    volume: int | None = None
    bid: float | None = None
    ask: float | None = None
    delay_minutes: int | None = None
    provider_stale: bool | None = None


@dataclass(frozen=True)
class BarRecord:
    provider_symbol: str
    timeframe: Timeframe
    ts: datetime  # bar start, tz-aware UTC. Daily bars: 00:00 UTC of the session date.
    close: float
    open: float | None = None
    high: float | None = None
    low: float | None = None
    volume: int | None = None  # None = not reported. 0 = reported as no trades.
    turnover: float | None = None
    trades_count: int | None = None
    ohlc_available: bool = True
    volume_available: bool = True
    currency: str = "KES"


@dataclass(frozen=True)
class MarketStatusRecord:
    exchange_code: str
    is_open: bool
    checked_at: datetime
    reason: str | None = None
    next_open: datetime | None = None
    next_close: datetime | None = None


@dataclass(frozen=True)
class HolidayRecord:
    exchange_code: str
    day: date
    reason: str


@dataclass(frozen=True)
class CorporateActionRecord:
    external_id: str
    provider_symbol: str
    action_type: CorporateActionType
    status: str | None = None
    title: str | None = None
    announced_at: datetime | None = None
    ex_date: date | None = None
    record_date: date | None = None
    payment_date: date | None = None
    effective_date: date | None = None
    cash_amount: float | None = None
    currency: str | None = None
    ratio_from: float | None = None
    ratio_to: float | None = None
    source_url: str | None = None


@dataclass(frozen=True)
class CompanyProfileRecord:
    provider_symbol: str
    description: str | None = None
    website: str | None = None
    country: str | None = None
    market_cap: float | None = None
    pe_ratio: float | None = None


@dataclass(frozen=True)
class NewsRecord:
    external_id: str
    kind: NewsKind
    title: str
    published_at: datetime | None
    url: str | None = None
    summary: str | None = None
    category: str | None = None
    publisher: str | None = None
    provider_symbols: tuple[str, ...] = ()


@dataclass(frozen=True)
class FinancialItemRecord:
    provider_symbol: str
    period_end: date
    period_type: str  # FY | H1 | Q
    statement_type: str  # INCOME | BALANCE | CASHFLOW
    line_item: str
    value: float
    currency: str
    published_at: date
    source: str
    reported_unit: str | None = None
    source_url: str | None = None


@dataclass
class QuoteBatch:
    quotes: list[QuoteRecord] = field(default_factory=list)
    not_found: list[str] = field(default_factory=list)


@dataclass
class RateLimitInfo:
    limit: int | None = None
    remaining: int | None = None
    reset_at: datetime | None = None


class MarketDataProvider(abc.ABC):
    code: str
    latency_class: LatencyClass
    is_synthetic: bool = False

    def __init__(self) -> None:
        self.rate_limit = RateLimitInfo()

    @abc.abstractmethod
    def is_configured(self) -> bool: ...

    @abc.abstractmethod
    async def get_instruments(self) -> list[InstrumentRecord]: ...

    @abc.abstractmethod
    async def get_quotes(self, symbols: Sequence[str]) -> QuoteBatch: ...

    @abc.abstractmethod
    async def get_historical_prices(
        self, symbol: str, start_date: date, end_date: date, timeframe: Timeframe = Timeframe.D1
    ) -> list[BarRecord]: ...

    async def subscribe_to_quotes(self, symbols: Sequence[str]) -> AsyncIterator[QuoteRecord]:
        """Streaming quotes. Only providers with a genuine push feed implement this."""
        raise NotSupported(f"{self.code} does not offer a streaming price feed")
        yield  # pragma: no cover  (makes this an async generator)

    @abc.abstractmethod
    async def get_market_status(self) -> MarketStatusRecord: ...

    async def get_holidays(self, start: date, end: date) -> list[HolidayRecord]:
        raise NotSupported(f"{self.code} does not provide a holiday calendar")

    async def get_corporate_actions(self, symbol: str) -> list[CorporateActionRecord]:
        raise NotSupported(f"{self.code} does not provide corporate actions")

    async def get_company_profile(self, symbol: str) -> CompanyProfileRecord | None:
        raise NotSupported(f"{self.code} does not provide company profiles")

    async def get_news(self, symbol: str | None = None) -> list[NewsRecord]:
        raise NotSupported(f"{self.code} does not provide news")

    async def get_financial_statements(self, symbol: str) -> list[FinancialItemRecord]:
        raise NotSupported(f"{self.code} does not provide financial statements")

    async def aclose(self) -> None:
        return None
