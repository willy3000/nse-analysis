"""Transaction-cost model for NSE equity trades (configurable; defaults documented in fee_schedules)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

DEFAULT_COMPONENTS: dict[str, float] = {
    "brokerage_rate": 0.015, "brokerage_min": 0.0, "vat_on_brokerage": 0.16, "nse_levy": 0.0012,
    "cma_levy": 0.0012, "cdsc_levy": 0.0008, "icf_levy": 0.0001, "guarantee_fund": 0.0001, "stamp_duty": 0.0,
    "capital_gains_tax": 0.0,
}


@dataclass(frozen=True)
class FeeModel:
    brokerage_rate: float = 0.015
    brokerage_min: float = 0.0
    vat_on_brokerage: float = 0.16
    nse_levy: float = 0.0012
    cma_levy: float = 0.0012
    cdsc_levy: float = 0.0008
    icf_levy: float = 0.0001
    guarantee_fund: float = 0.0001
    stamp_duty: float = 0.0
    capital_gains_tax: float = 0.0

    @classmethod
    def from_components(cls, comp: dict[str, Any] | None) -> FeeModel:
        merged = {**DEFAULT_COMPONENTS, **(comp or {})}
        return cls(**{k: float(v) for k, v in merged.items() if k in cls.__dataclass_fields__})

    def breakdown(self, consideration: float) -> dict[str, float]:
        if consideration <= 0:
            return {"total": 0.0}
        brokerage = max(self.brokerage_rate * consideration, self.brokerage_min)
        parts = {
            "brokerage": brokerage,
            "vat_on_brokerage": brokerage * self.vat_on_brokerage,
            "nse_levy": consideration * self.nse_levy,
            "cma_levy": consideration * self.cma_levy,
            "cdsc_levy": consideration * self.cdsc_levy,
            "icf_levy": consideration * self.icf_levy,
            "guarantee_fund": consideration * self.guarantee_fund,
            "stamp_duty": consideration * self.stamp_duty,
        }
        parts = {k: round(v, 4) for k, v in parts.items()}
        parts["total"] = round(sum(parts.values()), 4)
        return parts

    def total(self, consideration: float) -> float:
        return self.breakdown(consideration)["total"]

    def rate_estimate(self) -> float:
        """Approximate all-in proportional cost per side (ignores minimum brokerage)."""
        return (self.brokerage_rate * (1 + self.vat_on_brokerage) + self.nse_levy + self.cma_levy + self.cdsc_levy
                + self.icf_levy + self.guarantee_fund + self.stamp_duty)

    def as_dict(self) -> dict[str, float]:
        return asdict(self)
