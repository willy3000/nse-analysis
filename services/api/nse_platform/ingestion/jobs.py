"""Ingestion jobs. Each job: advisory lock -> run ledger -> per-provider, per-instrument isolated failures.

A failure for one provider or instrument never aborts the others; it is recorded in ingestion_runs,
data_quality_issues and data_source_health, which the Data Centre exposes.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from nse_platform.core.config import get_settings
from nse_platform.core.db import advisory_lock, is_transient_db_error, session_scope
from nse_platform.ingestion import store
from nse_platform.ingestion.validation import validate_bars, validate_quote
from nse_platform.models import CorporateAction, DataProvider, Instrument, InstrumentIdentifier
from nse_platform.models.enums import RunStatus, SecurityType, Timeframe, TradingStatus
from nse_platform.providers.base import (
    MarketDataProvider,
    NotSupported,
    ProviderError,
    ProviderNotConfigured,
)
from nse_platform.providers.parsing import parse_dt

log = logging.getLogger(__name__)
PRICED_TYPES = {SecurityType.EQUITY.value, SecurityType.PREFERENCE.value, SecurityType.ETF.value,
                SecurityType.REIT.value}


def _enabled_rows(session: Session, providers: dict[str, MarketDataProvider]) -> list[tuple[MarketDataProvider,
                                                                                            DataProvider]]:
    out = []
    for code, p in providers.items():
        row = store.provider_row(session, code)
        if row.enabled:
            out.append((p, row))
    return out


def _instrument_targets(session: Session, provider_row: DataProvider, priced_only: bool = True,
                        include_suspended: bool = False) -> list[tuple[int, str, str]]:
    q = (select(Instrument.id, InstrumentIdentifier.provider_symbol, Instrument.currency)
         .join(InstrumentIdentifier, InstrumentIdentifier.instrument_id == Instrument.id)
         .where(InstrumentIdentifier.provider_id == provider_row.id))
    if priced_only:
        q = q.where(Instrument.security_type.in_(PRICED_TYPES))
    if not include_suspended:
        q = q.where(Instrument.trading_status != TradingStatus.DELISTED.value)
    return [(r[0], r[1], r[2]) for r in session.execute(q.order_by(Instrument.symbol)).all()]


def _resume_after_db_loss(s: Session, run: store.IngestionRun,
                          original: Exception) -> store.IngestionRun:
    """Make `s` usable again after its connection died, so a per-instrument loop can carry on.

    A dropped connection invalidates the session, so it must be rolled back before anything else; the
    rollback itself usually fails against a dead socket, which is expected and not worth propagating.
    Then we wait for the server to come back - continuing immediately would just mark every remaining
    instrument failed during the few seconds a restart takes.

    Returns the run row re-attached to the recovered session. If the database does not come back, the
    original error is re-raised so the run ledger records the failure exactly as it always did - the
    instruments already committed are still safe either way.
    """
    from nse_platform.core.db import db_gate

    try:
        s.rollback()
    except Exception as rollback_exc:  # noqa: BLE001 - expected on a dead socket; reconnecting is the point
        log.debug("rollback_after_db_loss_failed", extra={"error": repr(rollback_exc)})
    if not db_gate.wait(get_settings().db_ready_timeout_seconds):
        raise original
    # `run` was committed by start_run, so the row exists; merge re-attaches it to the fresh connection.
    return s.merge(run)


async def _run_job(job_name: str, providers: dict[str, MarketDataProvider],
                   body: Callable[[Session, MarketDataProvider, DataProvider, store.IngestionRun], Awaitable[None]]
                   ) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    with advisory_lock(f"job:{job_name}") as acquired:
        if not acquired:
            log.info("job_skipped_locked", extra={"job": job_name})
            return [{"job": job_name, "status": RunStatus.SKIPPED.value}]
        with session_scope() as s:
            pairs = _enabled_rows(s, providers)
        for provider, prow_detached in pairs:
            with session_scope() as s:
                prow = s.get(DataProvider, prow_detached.id)
                assert prow is not None
                run = store.start_run(s, job_name, prow)
                s.commit()
                if not provider.is_configured():
                    store.mark_provider_failure(s, prow, f"{prow.code} not configured (credentials missing)",
                                                unconfigured=True)
                    run.status, run.finished_at = RunStatus.SKIPPED.value, datetime.now(timezone.utc)
                    run.error = "provider not configured"
                    results.append({"job": job_name, "provider": prow.code, "status": run.status})
                    continue
                error: str | None = None
                try:
                    await body(s, provider, prow, run)
                except ProviderNotConfigured as exc:
                    store.mark_provider_failure(s, prow, str(exc), unconfigured=True)
                    error = str(exc)
                except ProviderError as exc:
                    s.rollback()
                    run = s.merge(run)
                    store.mark_provider_failure(s, prow, f"{type(exc).__name__}: {exc}")
                    error = f"{type(exc).__name__}: {exc}"
                    log.exception("job_provider_failed", extra={"job": job_name, "provider": prow.code})
                except Exception as exc:  # noqa: BLE001 - recorded, not swallowed: ledger + log + re-raise-free
                    s.rollback()
                    run = s.merge(run)
                    error = f"unexpected {type(exc).__name__}: {exc}"
                    log.exception("job_unexpected_error", extra={"job": job_name, "provider": prow.code})
                else:
                    if run.items_failed == 0 or run.items_succeeded > 0:
                        store.mark_provider_success(s, prow, provider.rate_limit,
                                                    parse_dt((run.details or {}).get("latest_market_ts")))
                store.finish_run(s, run, error)
                results.append({"job": job_name, "provider": prow.code, "status": run.status,
                                "succeeded": run.items_succeeded, "failed": run.items_failed,
                                "rows": run.rows_written, "error": error})
    return results


# --------------------------------------------------------------------------- jobs


async def sync_instruments(providers: dict[str, MarketDataProvider]) -> list[dict[str, object]]:
    async def body(s: Session, p: MarketDataProvider, prow: DataProvider, run: store.IngestionRun) -> None:
        records = await p.get_instruments()
        counts = store.sync_instruments(s, prow, records)
        run.items_requested = len(records)
        run.items_succeeded = len(records)
        run.rows_written = counts["created"] + counts["updated"]
        run.details = counts
        # Calendar is best-effort and must never undo the instrument sync. Providers cap the range (MyStocks: 2y).
        today = date.today()
        start = today - timedelta(days=365 * 11)
        cur, holidays, cal_error = start, [], None
        while cur <= today + timedelta(days=365):
            win_end = min(cur + timedelta(days=364), today + timedelta(days=365))
            try:
                holidays.extend(await p.get_holidays(cur, win_end))
            except NotSupported:
                cal_error = "not supported"
                break
            except ProviderError as exc:
                cal_error = str(exc)[:300]
                break
            cur = win_end + timedelta(days=1)
        if cal_error != "not supported" and holidays:
            # Only assert TRADING/HOLIDAY status for years the provider actually covers; earlier dates stay unknown
            # rather than being presumed trading days.
            covered_from = date(min(h.day for h in holidays).year, 1, 1)
            store.store_calendar(s, prow, holidays, covered_from, today + timedelta(days=365))
        run.details = {**counts, "holidays": len(holidays), "calendar_error": cal_error}

    return await _run_job("sync_instruments", providers, body)


async def ingest_history(providers: dict[str, MarketDataProvider], start: date | None = None,
                         end: date | None = None, incremental: bool = True,
                         symbols: list[str] | None = None) -> list[dict[str, object]]:
    """Daily bars. Incremental mode fetches from (last stored bar - 10 days) to catch provider revisions."""
    end = end or date.today()

    async def body(s: Session, p: MarketDataProvider, prow: DataProvider, run: store.IngestionRun) -> None:
        targets = _instrument_targets(s, prow, include_suspended=True)
        if not targets:
            raise ProviderError("no instruments are linked to this provider - run sync_instruments first")
        if symbols:
            targets = [t for t in targets if t[1] in symbols or t[1].split(".")[0] in symbols]
        run.items_requested = len(targets)
        sym_map = {t[1]: t[0] for t in targets}
        latest_overall: datetime | None = None
        # Default window comes from BOOTSTRAP_BACKFILL_YEARS rather than a hard-coded decade: the longest
        # indicator lookback is ~120 sessions, and a 10-year pull per instrument made a single pass so long
        # that it could not finish between database restarts.
        default_start = date(end.year - get_settings().bootstrap_backfill_years, 1, 1)
        for iid, psym, ccy in targets:
            st = start or default_start
            if incremental:
                last = store.latest_bar_ts(s, iid, prow.id)
                if last is not None:
                    st = max(st, last.date() - timedelta(days=10))
            try:
                bars = await p.get_historical_prices(psym, st, end, Timeframe.D1)
            except NotSupported:
                run.items_requested -= 1
                continue
            except ProviderError as exc:
                if not exc.retryable and "404" not in str(exc):
                    raise  # auth / config errors affect the whole provider
                run.items_failed += 1
                store.record_issues(s, [store.Issue("HISTORY_FETCH_FAILED", "warn", psym, None,
                                                    {"error": str(exc)[:300]})], prow, run, sym_map)
                continue
            try:
                ca_dates = {r[0] for r in s.execute(select(CorporateAction.ex_date).where(
                    CorporateAction.instrument_id == iid, CorporateAction.ex_date.is_not(None))).all()}
                prev = store.last_close_before(s, iid, prow.id, "1d", bars[0].ts) if bars else None
                validated, issues = validate_bars(bars, prev, corporate_action_dates=ca_dates)
                written, revised = store.store_bars(s, prow, iid, ccy, validated, run)
                store.record_issues(s, issues, prow, run, sym_map)
                run.items_succeeded += 1
                run.rows_written += written
                if validated:
                    ts = validated[-1].bar.ts
                    latest_overall = ts if latest_overall is None or ts > latest_overall else latest_overall
                s.commit()  # commit per instrument: partial progress survives a later failure
            except Exception as exc:  # noqa: BLE001 - only connectivity is handled here; the rest re-raises
                if not is_transient_db_error(exc):
                    raise
                # The database went away mid-instrument (managed Postgres restarts). Everything committed
                # before this instrument is safe; this one is simply not done, and the next pass will pick
                # it up because `incremental` reads the stored high-water mark. Without this, one restart
                # aborted the whole job and every instrument after it was never fetched at all.
                log.warning("history_instrument_db_lost", extra={"symbol": psym, "instrument_id": iid,
                                                                 "error": f"{type(exc).__name__}: {exc}"})
                run = _resume_after_db_loss(s, run, exc)
                run.items_failed += 1
        run.details = {"latest_market_ts": latest_overall.isoformat() if latest_overall else None,
                       "start": str(start), "end": str(end), "incremental": incremental}

    return await _run_job("ingest_history", providers, body)


async def poll_quotes(providers: dict[str, MarketDataProvider]) -> list[dict[str, object]]:
    async def body(s: Session, p: MarketDataProvider, prow: DataProvider, run: store.IngestionRun) -> None:
        targets = _instrument_targets(s, prow)
        if not targets:
            raise ProviderError("no instruments are linked to this provider - run sync_instruments first")
        sym_map = {t[1]: t[0] for t in targets}
        run.items_requested = len(targets)
        batch = await p.get_quotes(list(sym_map))
        items, issues = [], []
        latest: datetime | None = None
        for q in batch.quotes:
            iid = sym_map.get(q.provider_symbol)
            if iid is None:
                continue
            qs, iss = validate_quote(q)
            issues.extend(iss)
            items.append((iid, q, qs))
            latest = q.market_ts if latest is None or q.market_ts > latest else latest
        for nf in batch.not_found:
            issues.append(store.Issue("QUOTE_NOT_FOUND", "info", nf, None))
        run.rows_written = store.store_quotes(s, prow, items, run)
        from nse_platform.opportunities.surge import auto_watch, intraday_check

        surges = intraday_check(s, prow.id)
        if surges:
            auto_watch(s, datetime.now(timezone.utc) - timedelta(minutes=30))
        store.record_issues(s, issues, prow, run, sym_map)
        run.items_succeeded = len(items)
        run.items_failed = len(batch.not_found)
        run.details = {"latest_market_ts": latest.isoformat() if latest else None,
                       "not_found": batch.not_found[:50], "intraday_surges": surges}

    return await _run_job("poll_quotes", providers, body)


async def ingest_corporate(providers: dict[str, MarketDataProvider]) -> list[dict[str, object]]:
    """Corporate actions, company profiles, company news, market news and financial statements."""

    async def body(s: Session, p: MarketDataProvider, prow: DataProvider, run: store.IngestionRun) -> None:
        targets = _instrument_targets(s, prow, include_suspended=True)
        if not targets:
            raise ProviderError("no instruments are linked to this provider - run sync_instruments first")
        sym_map = {t[1]: t[0] for t in targets}
        run.items_requested = len(targets)
        counts = {"corporate_actions": 0, "news": 0, "profiles": 0, "financial_items": 0}
        unsupported: set[str] = set()
        for iid, psym, _ in targets:
            ok = True
            for kind in ("corporate_actions", "profiles", "news", "financial_items"):
                if kind in unsupported:
                    continue
                try:
                    if kind == "corporate_actions":
                        counts[kind] += store.store_corporate_actions(s, prow, iid, await p.get_corporate_actions(psym))
                    elif kind == "profiles":
                        prof = await p.get_company_profile(psym)
                        if prof:
                            store.store_profile(s, prow, iid, prof)
                            counts[kind] += 1
                    elif kind == "news":
                        counts[kind] += store.store_news(s, prow, await p.get_news(psym), sym_map)
                    else:
                        counts[kind] += store.store_financials(s, iid, await p.get_financial_statements(psym))
                except NotSupported:
                    unsupported.add(kind)
                except ProviderError as exc:
                    if not exc.retryable and "404" not in str(exc):
                        raise
                    ok = False
                    store.record_issues(s, [store.Issue(f"{kind.upper()}_FETCH_FAILED", "warn", psym, None,
                                                        {"error": str(exc)[:300]})], prow, run, sym_map)
            run.items_succeeded += ok
            run.items_failed += not ok
            s.commit()
        try:
            counts["news"] += store.store_news(s, prow, await p.get_news(None), sym_map)
        except NotSupported:
            pass
        run.rows_written = sum(counts.values())
        run.details = {**counts, "unsupported": sorted(unsupported)}

    return await _run_job("ingest_corporate", providers, body)


async def refresh_market_status(providers: dict[str, MarketDataProvider]) -> dict[str, object]:
    """Returns provider market-status (not persisted beyond health); used by /market/status via cache."""
    out: dict[str, object] = {}
    for code, p in providers.items():
        if not p.is_configured():
            continue
        try:
            st = await p.get_market_status()
            out[code] = {"is_open": st.is_open, "reason": st.reason, "checked_at": st.checked_at.isoformat(),
                         "next_open": st.next_open.isoformat() if st.next_open else None,
                         "next_close": st.next_close.isoformat() if st.next_close else None}
        except ProviderError as exc:
            out[code] = {"error": str(exc)[:300]}
    return out
