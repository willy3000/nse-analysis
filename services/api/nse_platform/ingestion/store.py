"""Persistence for normalized provider records. All writes are idempotent upserts on natural keys."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from nse_platform.ingestion.validation import Issue, ValidatedBar
from nse_platform.models import (
    CompanyProfile,
    CorporateAction,
    DataProvider,
    DataQualityIssue,
    DataSourceHealth,
    Exchange,
    FinancialStatementItem,
    IngestionRun,
    Instrument,
    InstrumentIdentifier,
    Issuer,
    MarketSession,
    NewsArticle,
    NewsArticleInstrument,
    PriceBar,
    PriceBarRevision,
    Quote,
    Sector,
)
from nse_platform.models.enums import ProviderStatus, QualityStatus, RunStatus, SessionStatus
from nse_platform.providers.base import (
    CompanyProfileRecord,
    CorporateActionRecord,
    FinancialItemRecord,
    HolidayRecord,
    InstrumentRecord,
    NewsRecord,
    QuoteRecord,
    RateLimitInfo,
)

log = logging.getLogger(__name__)
_BAR_FIELDS = ("open", "high", "low", "close", "volume", "ohlc_available", "volume_available")


def provider_row(session: Session, code: str) -> DataProvider:
    row = session.scalar(select(DataProvider).where(DataProvider.code == code))
    if row is None:
        raise LookupError(f"data provider {code!r} not registered (run migrations)")
    return row


def exchange_row(session: Session, code: str = "NSE") -> Exchange:
    row = session.scalar(select(Exchange).where(Exchange.code == code))
    if row is None:
        raise LookupError(f"exchange {code!r} not registered")
    return row


# --------------------------------------------------------------------------- runs & health


def start_run(session: Session, job_name: str, provider: DataProvider | None) -> IngestionRun:
    run = IngestionRun(job_name=job_name, provider_id=provider.id if provider else None,
                       status=RunStatus.RUNNING.value)
    session.add(run)
    session.flush()
    return run


def finish_run(session: Session, run: IngestionRun, error: str | None = None) -> None:
    run.finished_at = datetime.now(timezone.utc)
    if error and run.items_succeeded == 0:
        run.status = RunStatus.FAILED.value
    elif error or run.items_failed:
        run.status = RunStatus.PARTIAL.value
    else:
        run.status = RunStatus.SUCCEEDED.value
    run.error = error


def _health(session: Session, provider: DataProvider) -> DataSourceHealth:
    h = session.get(DataSourceHealth, provider.id)
    if h is None:
        h = DataSourceHealth(provider_id=provider.id, status=ProviderStatus.UNCONFIGURED.value,
                             consecutive_failures=0)
        session.add(h)
    return h


def mark_provider_success(session: Session, provider: DataProvider, rate: RateLimitInfo | None = None,
                          latest_market_ts: datetime | None = None) -> None:
    h = _health(session, provider)
    h.status = ProviderStatus.HEALTHY.value
    h.last_success_at = datetime.now(timezone.utc)
    h.consecutive_failures = 0
    if latest_market_ts and (h.latest_market_ts is None or latest_market_ts > h.latest_market_ts):
        h.latest_market_ts = latest_market_ts
    if rate:
        h.rate_limit_limit, h.rate_limit_remaining, h.rate_limit_reset_at = rate.limit, rate.remaining, rate.reset_at


def mark_provider_failure(session: Session, provider: DataProvider, error: str, unconfigured: bool = False) -> None:
    h = _health(session, provider)
    if unconfigured:
        h.status = ProviderStatus.UNCONFIGURED.value
    else:
        h.consecutive_failures = (h.consecutive_failures or 0) + 1
        h.status = ProviderStatus.DOWN.value if h.consecutive_failures >= 3 else ProviderStatus.DEGRADED.value
        h.last_failure_at = datetime.now(timezone.utc)
    h.last_error = error[:2000]


def record_issues(session: Session, issues: list[Issue], provider: DataProvider, run: IngestionRun | None,
                  instrument_by_symbol: dict[str, int] | None = None) -> None:
    for i in issues:
        session.add(DataQualityIssue(
            instrument_id=(instrument_by_symbol or {}).get(i.provider_symbol), provider_id=provider.id,
            issue_type=i.issue_type, severity=i.severity, observed_ts=i.observed_ts,
            details={"provider_symbol": i.provider_symbol, **i.details},
            ingestion_run_id=run.id if run else None,
        ))


# --------------------------------------------------------------------------- instruments


def _sector_id(session: Session, name: str | None) -> int | None:
    if not name:
        return None
    stmt = insert(Sector).values(name=name.strip()).on_conflict_do_nothing(index_elements=["name"])
    session.execute(stmt)
    return session.scalar(select(Sector.id).where(Sector.name == name.strip()))


def _issuer_id(session: Session, name: str | None) -> int | None:
    if not name:
        return None
    session.execute(insert(Issuer).values(name=name.strip()).on_conflict_do_nothing(index_elements=["name"]))
    return session.scalar(select(Issuer.id).where(Issuer.name == name.strip()))


def sync_instruments(session: Session, provider: DataProvider, records: list[InstrumentRecord]) -> dict[str, int]:
    """Upsert instruments discovered from a provider. Returns counts. Never deletes instruments."""
    counts = {"created": 0, "updated": 0, "linked": 0}
    now = datetime.now(timezone.utc)
    exchanges: dict[str, Exchange] = {}
    for rec in records:
        ex = exchanges.get(rec.exchange_code) or exchange_row(session, rec.exchange_code)
        exchanges[rec.exchange_code] = ex
        ident = session.scalar(select(InstrumentIdentifier).where(
            InstrumentIdentifier.provider_id == provider.id, InstrumentIdentifier.provider_symbol == rec.provider_symbol))
        inst = session.get(Instrument, ident.instrument_id) if ident else None
        if inst is None:
            inst = session.scalar(select(Instrument).where(Instrument.exchange_id == ex.id,
                                                           Instrument.symbol == rec.symbol))
            if inst is not None and inst.is_synthetic != provider.is_synthetic:
                log.error("synthetic_real_symbol_collision", extra={"symbol": rec.symbol})
                continue
        if inst is None:
            inst = Instrument(exchange_id=ex.id, symbol=rec.symbol, name=rec.name,
                              security_type=rec.security_type.value, currency=rec.currency,
                              is_synthetic=provider.is_synthetic, primary_provider_id=provider.id)
            session.add(inst)
            counts["created"] += 1
        else:
            counts["updated"] += 1
        inst.name = rec.name
        inst.security_type = rec.security_type.value
        inst.trading_status = rec.trading_status.value
        inst.currency = rec.currency
        inst.sector_id = _sector_id(session, rec.sector) or inst.sector_id
        inst.issuer_id = _issuer_id(session, rec.issuer) or inst.issuer_id
        inst.isin = rec.isin or inst.isin
        inst.lot_size = rec.lot_size or inst.lot_size
        inst.coupon_rate, inst.maturity_date, inst.face_value = rec.coupon_rate, rec.maturity_date, rec.face_value
        inst.last_seen_at = now
        # Prefer the highest-priority (lowest number) provider as the instrument's primary series source.
        if inst.primary_provider_id is None:
            inst.primary_provider_id = provider.id
        else:
            current = session.get(DataProvider, inst.primary_provider_id)
            if current is not None and provider.priority < current.priority and provider.enabled:
                inst.primary_provider_id = provider.id
        session.flush()
        if ident is None:
            session.execute(insert(InstrumentIdentifier).values(
                instrument_id=inst.id, provider_id=provider.id, provider_symbol=rec.provider_symbol,
            ).on_conflict_do_nothing())
            counts["linked"] += 1
    return counts


def identifier_map(session: Session, provider: DataProvider) -> dict[str, int]:
    rows = session.execute(select(InstrumentIdentifier.provider_symbol, InstrumentIdentifier.instrument_id)
                           .where(InstrumentIdentifier.provider_id == provider.id)).all()
    return {r[0]: r[1] for r in rows}


# --------------------------------------------------------------------------- prices


def last_close_before(session: Session, instrument_id: int, provider_id: int, timeframe: str,
                      ts: datetime) -> float | None:
    return session.scalar(
        select(PriceBar.close).where(PriceBar.instrument_id == instrument_id, PriceBar.provider_id == provider_id,
                                     PriceBar.timeframe == timeframe, PriceBar.ts < ts)
        .order_by(PriceBar.ts.desc()).limit(1)
    )


def store_bars(session: Session, provider: DataProvider, instrument_id: int, currency: str,
               validated: list[ValidatedBar], run: IngestionRun | None) -> tuple[int, int]:
    """Upsert bars. Changed existing bars get their prior values archived in price_bar_revisions.

    Returns (written, revised).
    """
    if not validated:
        return 0, 0
    tf = validated[0].bar.timeframe.value
    keys = [v.bar.ts for v in validated]
    existing = {
        r.ts: r for r in session.scalars(select(PriceBar).where(
            PriceBar.instrument_id == instrument_id, PriceBar.provider_id == provider.id,
            PriceBar.timeframe == tf, PriceBar.ts.in_(keys)))
    }
    rows, revised, now = [], 0, datetime.now(timezone.utc)
    for v in validated:
        b = v.bar
        new = {"open": b.open, "high": b.high, "low": b.low, "close": b.close, "volume": b.volume,
               "ohlc_available": b.ohlc_available, "volume_available": b.volume_available}
        old = existing.get(b.ts)
        revised_at = None
        if old is not None:
            prev = {f: getattr(old, f) for f in _BAR_FIELDS}
            if prev == new:
                continue  # identical re-delivery: nothing to write
            session.add(PriceBarRevision(instrument_id=instrument_id, provider_id=provider.id, timeframe=tf, ts=b.ts,
                                         previous={**prev, "quality_status": old.quality_status},
                                         ingestion_run_id=run.id if run else None))
            revised += 1
            revised_at = now
        rows.append({
            "instrument_id": instrument_id, "provider_id": provider.id, "timeframe": tf, "ts": b.ts,
            "session_date": b.ts.date() if tf in ("1d", "1w", "1mo") else None, **new,
            "turnover": b.turnover, "trades_count": b.trades_count, "quality_status": v.quality.value,
            "currency": b.currency or currency, "revised_at": revised_at,
            "ingestion_run_id": run.id if run else None,
        })
    if rows:
        for i in range(0, len(rows), 1000):
            stmt = insert(PriceBar).values(rows[i : i + 1000])
            stmt = stmt.on_conflict_do_update(
                index_elements=["instrument_id", "provider_id", "timeframe", "ts"],
                set_={c: stmt.excluded[c] for c in (*_BAR_FIELDS, "turnover", "trades_count", "quality_status",
                                                    "revised_at", "ingestion_run_id", "session_date")},
            )
            session.execute(stmt)
    return len(rows), revised


def store_quotes(session: Session, provider: DataProvider, items: list[tuple[int, QuoteRecord, QualityStatus]],
                 run: IngestionRun | None) -> int:
    rows = [{
        "instrument_id": iid, "provider_id": provider.id, "market_ts": q.market_ts, "source_ts": q.source_ts,
        "price": q.price, "open": q.open, "high": q.high, "low": q.low, "previous_close": q.previous_close,
        "volume": q.volume, "bid": q.bid, "ask": q.ask, "currency": q.currency,
        "latency_class": q.latency_class.value, "delay_minutes": q.delay_minutes, "provider_stale": q.provider_stale,
        "quality_status": qs.value, "ingestion_run_id": run.id if run else None,
    } for iid, q, qs in items if qs != QualityStatus.REJECTED]
    if not rows:
        return 0
    # Same exchange observation re-polled -> no new row (observations are immutable).
    res = session.execute(insert(Quote).values(rows).on_conflict_do_nothing())
    return res.rowcount or 0


# --------------------------------------------------------------------------- corporate / news / calendar


def store_corporate_actions(session: Session, provider: DataProvider, instrument_id: int,
                            recs: list[CorporateActionRecord]) -> int:
    n = 0
    for r in recs:
        values = {
            "instrument_id": instrument_id, "provider_id": provider.id, "external_id": r.external_id,
            "action_type": r.action_type.value, "status": r.status, "title": r.title, "announced_at": r.announced_at,
            "ex_date": r.ex_date, "record_date": r.record_date, "payment_date": r.payment_date,
            "effective_date": r.effective_date, "cash_amount": r.cash_amount, "currency": r.currency,
            "ratio_from": r.ratio_from, "ratio_to": r.ratio_to, "source_url": r.source_url,
        }
        stmt = insert(CorporateAction).values(values)
        stmt = stmt.on_conflict_do_update(
            index_elements=["provider_id", "external_id"],
            set_={k: stmt.excluded[k] for k in values if k not in ("provider_id", "external_id")}
            | {"updated_at": func.now()},
        )
        session.execute(stmt)
        n += 1
    return n


def store_profile(session: Session, provider: DataProvider, instrument_id: int, rec: CompanyProfileRecord) -> None:
    values = {"instrument_id": instrument_id, "provider_id": provider.id, "description": rec.description,
              "website": rec.website, "country": rec.country, "market_cap": rec.market_cap,
              "provider_pe_ratio": rec.pe_ratio, "fetched_at": datetime.now(timezone.utc)}
    stmt = insert(CompanyProfile).values(values)
    session.execute(stmt.on_conflict_do_update(index_elements=["instrument_id"],
                                               set_={k: stmt.excluded[k] for k in values if k != "instrument_id"}))


def store_news(session: Session, provider: DataProvider, recs: list[NewsRecord], symbol_map: dict[str, int]) -> int:
    n = 0
    for r in recs:
        stmt = insert(NewsArticle).values(
            provider_id=provider.id, external_id=r.external_id, kind=r.kind.value, category=r.category,
            title=r.title[:1024], publisher=r.publisher, url=r.url, summary=r.summary, published_at=r.published_at,
            source_reliability="SYNTHETIC" if provider.is_synthetic else "PROVIDER",
            is_synthetic=provider.is_synthetic,
        ).on_conflict_do_nothing(index_elements=["provider_id", "external_id"]).returning(NewsArticle.id)
        aid = session.scalar(stmt)
        if aid is None:
            continue
        n += 1
        for ps in r.provider_symbols:
            iid = symbol_map.get(ps)
            if iid:
                session.execute(insert(NewsArticleInstrument).values(article_id=aid, instrument_id=iid)
                                .on_conflict_do_nothing())
    return n


def store_financials(session: Session, instrument_id: int, recs: list[FinancialItemRecord]) -> int:
    if not recs:
        return 0
    rows = [{
        "instrument_id": instrument_id, "period_end": r.period_end, "period_type": r.period_type,
        "statement_type": r.statement_type, "line_item": r.line_item, "value": r.value, "currency": r.currency,
        "reported_unit": r.reported_unit, "published_at": r.published_at, "source": r.source,
        "source_url": r.source_url,
    } for r in recs]
    stmt = insert(FinancialStatementItem).values(rows)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_financial_statements_instrument_id",
        set_={"value": stmt.excluded.value, "published_at": stmt.excluded.published_at,
              "reported_unit": stmt.excluded.reported_unit, "source_url": stmt.excluded.source_url},
    )
    session.execute(stmt)
    return len(rows)


def store_calendar(session: Session, provider: DataProvider, holidays: list[HolidayRecord], start: date,
                   end: date) -> int:
    """Materialize market_sessions for [start, end]: weekends, provider holidays, otherwise TRADING."""
    ex = exchange_row(session, "NSE")
    hol = {h.day: h.reason for h in holidays}
    d, n = start, 0
    while d <= end:
        if d in hol:
            status, reason = SessionStatus.HOLIDAY, hol[d]
        elif d.weekday() >= 5:
            status, reason = SessionStatus.WEEKEND, None
        else:
            status, reason = SessionStatus.TRADING, None
        stmt = insert(MarketSession).values(exchange_id=ex.id, session_date=d, status=status.value, reason=reason,
                                            source_provider_id=provider.id)
        if provider.is_synthetic:
            # The synthetic calendar only fills gaps; it never overwrites a real provider's calendar.
            session.execute(stmt.on_conflict_do_nothing(index_elements=["exchange_id", "session_date"]))
        else:
            session.execute(stmt.on_conflict_do_update(
                index_elements=["exchange_id", "session_date"],
                set_={"status": stmt.excluded.status, "reason": stmt.excluded.reason,
                      "source_provider_id": stmt.excluded.source_provider_id},
            ))
        n += 1
        d += timedelta(days=1)
    return n


def latest_bar_ts(session: Session, instrument_id: int, provider_id: int, timeframe: str = "1d") -> datetime | None:
    return session.scalar(select(func.max(PriceBar.ts)).where(
        PriceBar.instrument_id == instrument_id, PriceBar.provider_id == provider_id, PriceBar.timeframe == timeframe))


__all__ = [
    "QualityStatus", "exchange_row", "finish_run", "identifier_map", "last_close_before", "latest_bar_ts",
    "mark_provider_failure", "mark_provider_success", "provider_row", "record_issues", "start_run",
    "store_bars", "store_calendar", "store_corporate_actions", "store_financials", "store_news", "store_profile",
    "store_quotes", "sync_instruments",
]
