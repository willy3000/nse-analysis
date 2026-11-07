"""Domain enumerations. Stored as text with CHECK constraints (portable, migration-friendly)."""

from __future__ import annotations

from nse_platform.models.base import StrEnum


class SecurityType(StrEnum):
    EQUITY = "EQUITY"
    PREFERENCE = "PREFERENCE"
    ETF = "ETF"
    REIT = "REIT"
    BOND = "BOND"
    OTHER = "OTHER"


class TradingStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    DELISTED = "DELISTED"
    UNKNOWN = "UNKNOWN"


class LatencyClass(StrEnum):
    """How fresh a provider's data is *by contract*. Never upgrade DELAYED to LIVE."""

    LIVE = "LIVE"
    DELAYED = "DELAYED"
    END_OF_DAY = "END_OF_DAY"
    HISTORICAL = "HISTORICAL"


class FreshnessStatus(StrEnum):
    """Freshness of a specific observation as presented to users."""

    LIVE = "LIVE"
    DELAYED = "DELAYED"
    END_OF_DAY = "END_OF_DAY"
    HISTORICAL = "HISTORICAL"
    STALE = "STALE"
    UNAVAILABLE = "UNAVAILABLE"


class QualityStatus(StrEnum):
    OK = "OK"
    PARTIAL = "PARTIAL"  # e.g. close reported but OHLC derived
    SUSPECT = "SUSPECT"  # passed hard validation but flagged by soft checks
    REJECTED = "REJECTED"


class Timeframe(StrEnum):
    M15 = "15m"
    H1 = "1h"
    D1 = "1d"
    W1 = "1w"
    MO1 = "1mo"


class ProviderStatus(StrEnum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    DOWN = "DOWN"
    UNCONFIGURED = "UNCONFIGURED"
    DISABLED = "DISABLED"


class RunStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    SKIPPED = "skipped"


class SessionStatus(StrEnum):
    TRADING = "TRADING"
    HOLIDAY = "HOLIDAY"
    WEEKEND = "WEEKEND"


class CorporateActionType(StrEnum):
    DIVIDEND_CASH = "DIVIDEND_CASH"
    BONUS = "BONUS"
    SPLIT = "SPLIT"
    CONSOLIDATION = "CONSOLIDATION"
    RIGHTS = "RIGHTS"
    SUSPENSION = "SUSPENSION"
    OTHER = "OTHER"


class NewsKind(StrEnum):
    NEWS = "NEWS"
    ANNOUNCEMENT = "ANNOUNCEMENT"
    RESULTS = "RESULTS"
    CORPORATE_ACTION = "CORPORATE_ACTION"
    REGULATORY = "REGULATORY"
    MACRO = "MACRO"


class TrendClass(StrEnum):
    STRONG_BULLISH = "STRONG_BULLISH"
    EMERGING_BULLISH = "EMERGING_BULLISH"
    BULLISH_PULLBACK = "BULLISH_PULLBACK"
    SIDEWAYS = "SIDEWAYS"
    WEAKENING_BULLISH = "WEAKENING_BULLISH"
    EMERGING_BEARISH = "EMERGING_BEARISH"
    STRONG_BEARISH = "STRONG_BEARISH"
    POTENTIAL_REVERSAL = "POTENTIAL_REVERSAL"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class SignalCategory(StrEnum):
    MOMENTUM = "MOMENTUM"
    BREAKOUT = "BREAKOUT"
    REVERSAL = "REVERSAL"
    PULLBACK = "PULLBACK"
    VALUE = "VALUE"
    DIVIDEND = "DIVIDEND"
    RISK = "RISK"


class SignalDirection(StrEnum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


class SignalStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"


class StatementType(StrEnum):
    INCOME = "INCOME"
    BALANCE = "BALANCE"
    CASHFLOW = "CASHFLOW"


class PeriodType(StrEnum):
    FY = "FY"
    H1 = "H1"
    Q = "Q"


class UserRole(StrEnum):
    USER = "user"
    ADMIN = "admin"


class PortfolioKind(StrEnum):
    PAPER = "PAPER"  # simulated research portfolio
    TRACKED = "TRACKED"  # manually recorded real-world holdings (no brokerage connection)


class TxnType(StrEnum):
    BUY = "BUY"
    SELL = "SELL"
    DIVIDEND = "DIVIDEND"
    DEPOSIT = "DEPOSIT"
    WITHDRAWAL = "WITHDRAWAL"
    FEE = "FEE"


class AlertRuleType(StrEnum):
    PRICE_ABOVE = "PRICE_ABOVE"
    PRICE_BELOW = "PRICE_BELOW"
    RSI_BELOW = "RSI_BELOW"
    RSI_ABOVE = "RSI_ABOVE"
    SMA_CROSS_UP = "SMA_CROSS_UP"
    SMA_CROSS_DOWN = "SMA_CROSS_DOWN"
    VOLUME_SPIKE = "VOLUME_SPIKE"
    SIGNAL_DETECTED = "SIGNAL_DETECTED"
    DIVIDEND_ANNOUNCED = "DIVIDEND_ANNOUNCED"
    ANNOUNCEMENT = "ANNOUNCEMENT"
    RISK_SCORE_ABOVE = "RISK_SCORE_ABOVE"
    FUNDAMENTAL_CHANGE = "FUNDAMENTAL_CHANGE"


class BacktestStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
