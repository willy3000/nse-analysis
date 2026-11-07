from __future__ import annotations

from datetime import datetime, timedelta, timezone

from nse_platform.ingestion.validation import validate_bars, validate_quote
from nse_platform.models.enums import LatencyClass, QualityStatus, Timeframe
from nse_platform.providers.base import BarRecord, QuoteRecord

T0 = datetime(2026, 1, 5, tzinfo=timezone.utc)


def bar(i: int, close: float, **kw: object) -> BarRecord:
    base = dict(provider_symbol="X", timeframe=Timeframe.D1, ts=T0 + timedelta(days=i), close=close, open=close,
                high=close * 1.01, low=close * 0.99, volume=100)
    base.update(kw)
    return BarRecord(**base)  # type: ignore[arg-type]


def test_rejects_invalid_and_duplicates():
    bars = [bar(0, 10), bar(0, 10), bar(1, -1), bar(2, 10, volume=-5), bar(3, 10.1),
            bar(400, 10, ts=datetime(2099, 1, 1, tzinfo=timezone.utc))]
    ok, issues = validate_bars(bars, now=T0 + timedelta(days=10))
    assert [v.bar.ts for v in ok] == [T0, T0 + timedelta(days=3)]
    kinds = {i.issue_type for i in issues}
    assert {"DUPLICATE_BAR", "INVALID_CLOSE", "NEGATIVE_VOLUME", "FUTURE_TIMESTAMP"} <= kinds


def test_inconsistent_ohlc_keeps_close_drops_range():
    ok, issues = validate_bars([bar(0, 10, high=9, low=8)], now=T0 + timedelta(days=1))
    assert ok[0].bar.close == 10 and ok[0].bar.high is None and not ok[0].bar.ohlc_available
    assert ok[0].quality == QualityStatus.SUSPECT
    assert issues[0].issue_type == "OHLC_INCONSISTENT"


def test_missing_volume_stays_missing():
    ok, _ = validate_bars([bar(0, 10, volume=None, volume_available=False)], now=T0 + timedelta(days=1))
    assert ok[0].bar.volume is None and ok[0].quality == QualityStatus.PARTIAL


def test_extreme_move_flagged_unless_corporate_action():
    bars = [bar(0, 10), bar(1, 20)]
    ok, issues = validate_bars(bars, now=T0 + timedelta(days=5))
    assert ok[1].quality == QualityStatus.SUSPECT and any(i.issue_type == "EXTREME_MOVE" for i in issues)
    ok, issues = validate_bars(bars, now=T0 + timedelta(days=5), corporate_action_dates={(T0 + timedelta(1)).date()})
    assert ok[1].quality == QualityStatus.OK


def test_price_change_without_volume_noted():
    _, issues = validate_bars([bar(0, 10), bar(1, 10.5, volume=0)], now=T0 + timedelta(days=5))
    assert any(i.issue_type == "PRICE_CHANGE_WITHOUT_VOLUME" for i in issues)


def test_quote_validation():
    q = QuoteRecord(provider_symbol="X", market_ts=T0, price=None, currency="KES", latency_class=LatencyClass.DELAYED)
    assert validate_quote(q, now=T0)[0] == QualityStatus.REJECTED
    q2 = QuoteRecord(provider_symbol="X", market_ts=T0, price=10, previous_close=9.9, currency="KES",
                     latency_class=LatencyClass.DELAYED)
    assert validate_quote(q2, now=T0)[0] == QualityStatus.OK
    q3 = QuoteRecord(provider_symbol="X", market_ts=T0 + timedelta(hours=1), price=10, currency="KES",
                     latency_class=LatencyClass.DELAYED)
    assert validate_quote(q3, now=T0)[0] == QualityStatus.REJECTED
