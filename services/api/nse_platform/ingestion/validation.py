"""Validation of normalized observations before they are persisted.

Hard failures -> REJECTED (not stored as prices; recorded as data_quality_issues).
Soft anomalies -> stored with quality_status SUSPECT/PARTIAL and an issue recorded.
Missing values stay missing: nothing here substitutes zeros or interpolates.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone

from nse_platform.models.enums import QualityStatus
from nse_platform.providers.base import BarRecord, QuoteRecord

MAX_ABS_DAILY_MOVE = 0.5  # NSE has daily price limits (~10%); >50% almost certainly a data or corporate-action event


@dataclass
class Issue:
    issue_type: str
    severity: str  # info | warn | error
    provider_symbol: str
    observed_ts: datetime | None
    details: dict[str, object] = field(default_factory=dict)


@dataclass
class ValidatedBar:
    bar: BarRecord
    quality: QualityStatus


def _finite_pos(x: float | None) -> bool:
    return x is not None and math.isfinite(x) and x > 0


def validate_bars(
    bars: list[BarRecord], previous_close: float | None = None, now: datetime | None = None,
    corporate_action_dates: set | None = None,
) -> tuple[list[ValidatedBar], list[Issue]]:
    now = now or datetime.now(timezone.utc)
    ca_dates = corporate_action_dates or set()
    accepted: list[ValidatedBar] = []
    issues: list[Issue] = []
    seen: set[datetime] = set()
    prev = previous_close
    for b in sorted(bars, key=lambda x: x.ts):
        sym, ts = b.provider_symbol, b.ts
        if ts.tzinfo is None:
            issues.append(Issue("NAIVE_TIMESTAMP", "error", sym, None, {"ts": str(ts)}))
            continue
        if ts in seen:
            issues.append(Issue("DUPLICATE_BAR", "warn", sym, ts))
            continue
        seen.add(ts)
        if ts > now + timedelta(days=1):
            issues.append(Issue("FUTURE_TIMESTAMP", "error", sym, ts))
            continue
        if not _finite_pos(b.close):
            issues.append(Issue("INVALID_CLOSE", "error", sym, ts, {"close": b.close}))
            continue
        if b.volume is not None and b.volume < 0:
            issues.append(Issue("NEGATIVE_VOLUME", "error", sym, ts, {"volume": b.volume}))
            continue
        quality = QualityStatus.OK
        bar = b
        if b.ohlc_available:
            o, h, lo, c = b.open, b.high, b.low, b.close
            if not all(_finite_pos(x) for x in (o, h, lo)):
                issues.append(Issue("OHLC_MISSING", "info", sym, ts))
                bar = replace(b, open=None, high=None, low=None, ohlc_available=False)
                quality = QualityStatus.PARTIAL
            else:
                assert o is not None and h is not None and lo is not None
                tol = 1e-9 * max(h, 1.0)
                if lo - tol > min(o, c) or h + tol < max(o, c) or lo > h:
                    # Inconsistent range: keep close (authoritative for EOD) but drop untrustworthy H/L/O.
                    issues.append(Issue("OHLC_INCONSISTENT", "warn", sym, ts, {"o": o, "h": h, "l": lo, "c": c}))
                    bar = replace(b, open=None, high=None, low=None, ohlc_available=False)
                    quality = QualityStatus.SUSPECT
        else:
            quality = QualityStatus.PARTIAL
        if not b.volume_available or b.volume is None:
            bar = replace(bar, volume=None, volume_available=False)
            if quality == QualityStatus.OK:
                quality = QualityStatus.PARTIAL
        if prev is not None and prev > 0:
            move = bar.close / prev - 1
            if abs(move) > MAX_ABS_DAILY_MOVE and ts.date() not in ca_dates:
                issues.append(Issue("EXTREME_MOVE", "warn", sym, ts, {"move": round(move, 4), "prev": prev}))
                quality = QualityStatus.SUSPECT
            if bar.volume == 0 and abs(move) > 1e-9:
                issues.append(Issue("PRICE_CHANGE_WITHOUT_VOLUME", "info", sym, ts, {"move": round(move, 4)}))
        accepted.append(ValidatedBar(bar, quality))
        prev = bar.close
    return accepted, issues


def validate_quote(q: QuoteRecord, now: datetime | None = None) -> tuple[QualityStatus, list[Issue]]:
    now = now or datetime.now(timezone.utc)
    issues: list[Issue] = []
    if q.market_ts.tzinfo is None:
        return QualityStatus.REJECTED, [Issue("NAIVE_TIMESTAMP", "error", q.provider_symbol, None)]
    if q.market_ts > now + timedelta(minutes=5):
        return QualityStatus.REJECTED, [Issue("FUTURE_TIMESTAMP", "error", q.provider_symbol, q.market_ts)]
    if q.price is None:
        return QualityStatus.REJECTED, [Issue("QUOTE_NO_PRICE", "warn", q.provider_symbol, q.market_ts)]
    if not _finite_pos(q.price):
        return QualityStatus.REJECTED, [Issue("INVALID_PRICE", "error", q.provider_symbol, q.market_ts,
                                              {"price": q.price})]
    status = QualityStatus.OK
    if q.previous_close and q.previous_close > 0 and abs(q.price / q.previous_close - 1) > MAX_ABS_DAILY_MOVE:
        issues.append(Issue("EXTREME_MOVE", "warn", q.provider_symbol, q.market_ts))
        status = QualityStatus.SUSPECT
    if q.high is not None and q.low is not None and q.low > q.high:
        issues.append(Issue("QUOTE_RANGE_INCONSISTENT", "warn", q.provider_symbol, q.market_ts))
        status = QualityStatus.SUSPECT
    return status, issues
