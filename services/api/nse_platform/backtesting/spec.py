"""Strategy and backtest parameter schemas (validated; stored verbatim for reproducibility)."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

PRICE_FEATURES = {"open", "high", "low", "close", "volume"}
INDICATOR_FEATURES = {
    "sma_5", "sma_10", "sma_20", "sma_50", "sma_100", "sma_200", "ema_9", "ema_12", "ema_20", "ema_26", "ema_50",
    "rsi_7", "rsi_14", "macd", "macd_signal", "macd_hist", "roc_10", "roc_20", "roc_60", "momentum_10", "bb_mid",
    "bb_upper", "bb_lower", "bb_width", "bb_pctb", "bb_width_pctile_120", "atr_14", "atr_pct_14", "adx", "plus_di",
    "minus_di", "hv_20", "hv_60", "vol_sma_20", "vol_sma_50", "rel_volume", "obv", "close_high_20_prior",
    "close_low_20_prior", "close_high_52w_prior", "high_52w", "low_52w", "drawdown",
}
FUNDAMENTAL_FEATURES = {"fund:pe_ratio", "fund:pb_ratio", "fund:dividend_yield", "fund:roe", "fund:net_margin",
                        "fund:debt_to_equity", "fund:earnings_growth", "fund:revenue_growth"}
ALL_FEATURES = PRICE_FEATURES | INDICATOR_FEATURES | FUNDAMENTAL_FEATURES

Op = Literal[">", ">=", "<", "<=", "crosses_above", "crosses_below"]


class Condition(BaseModel):
    left: str
    op: Op
    right: float | str

    @field_validator("left")
    @classmethod
    def _left(cls, v: str) -> str:
        if v not in ALL_FEATURES:
            raise ValueError(f"unknown feature {v!r}")
        return v

    @field_validator("right")
    @classmethod
    def _right(cls, v: float | str) -> float | str:
        if isinstance(v, str) and v not in ALL_FEATURES:
            raise ValueError(f"unknown feature {v!r}")
        return v


class PositionSizing(BaseModel):
    method: Literal["equal_weight", "fixed_fraction"] = "equal_weight"
    fraction: float = Field(0.2, gt=0, le=1)
    max_positions: int = Field(5, ge=1, le=50)


class Universe(BaseModel):
    symbols: list[str] | None = None
    security_types: list[Literal["EQUITY", "ETF", "REIT", "PREFERENCE"]] = ["EQUITY", "ETF", "REIT"]
    min_trading_frequency: float = Field(0.0, ge=0, le=1)
    synthetic: bool = False


class StrategySpec(BaseModel):
    timeframe: Literal["1d", "1w"] = "1d"
    entry: list[Condition] = Field(min_length=1)
    exit: list[Condition] = []
    stop_loss_pct: float | None = Field(None, gt=0, lt=1)
    take_profit_pct: float | None = Field(None, gt=0, le=10)
    max_holding_bars: int | None = Field(None, ge=1, le=2000)
    position_sizing: PositionSizing = PositionSizing()
    universe: Universe = Universe()

    @model_validator(mode="after")
    def _has_exit(self) -> StrategySpec:
        if not self.exit and not self.stop_loss_pct and not self.take_profit_pct and not self.max_holding_bars:
            raise ValueError("strategy needs at least one exit rule (conditions, stop, target or max holding)")
        return self


class ValidationSplit(BaseModel):
    in_sample: float = Field(0.6, gt=0, lt=1)
    validation: float = Field(0.2, ge=0, lt=1)

    @model_validator(mode="after")
    def _sum(self) -> ValidationSplit:
        if self.in_sample + self.validation >= 1:
            raise ValueError("in_sample + validation must leave room for an out-of-sample period")
        return self


class BacktestParams(BaseModel):
    start_date: date
    end_date: date
    initial_capital: float = Field(1_000_000, gt=0)
    fee_schedule_code: str = "KE_EQUITY_2026"
    fee_overrides: dict[str, float] | None = None
    slippage_bps: float = Field(25, ge=0, le=1000)
    max_participation: float = Field(0.10, gt=0, le=1)
    execution: Literal["next_open", "next_close"] = "next_open"
    order_ttl_bars: int = Field(5, ge=1, le=20)
    include_dividends: bool = True
    dividend_tax_rate: float = Field(0.05, ge=0, lt=1)
    risk_free_rate: float = Field(0.0, ge=0, lt=1)
    lot_size: int | None = Field(None, ge=1)
    validation_split: ValidationSplit = ValidationSplit()
    benchmark: str = "equal_weight_universe"

    @model_validator(mode="after")
    def _dates(self) -> BacktestParams:
        if self.end_date <= self.start_date:
            raise ValueError("end_date must be after start_date")
        return self
