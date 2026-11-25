"""Data Centre (provider health, coverage, runs, issues), news & events, live update stream, admin."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import Integer, func, select
from sqlalchemy.orm import Session

from nse_platform.api.deps import current_user, current_user_no_session, get_db, not_found, require_admin
from nse_platform.api.v1.instruments import news_dict
from nse_platform.core.cache import get_cache
from nse_platform.core.config import get_settings
from nse_platform.core.db import get_sessionmaker
from nse_platform.marketdata.freshness import last_completed_session
from nse_platform.marketdata.queries import resolve_universe
from nse_platform.models import (
    CorporateAction,
    DataProvider,
    DataQualityIssue,
    DataSourceHealth,
    IngestionRun,
    Instrument,
    InstrumentIdentifier,
    NewsArticle,
    NewsArticleInstrument,
    PriceBar,
    Quote,
    User,
)

router = APIRouter(tags=["data"])


@router.get("/data/providers")
def providers(db: Session = Depends(get_db), _: User = Depends(current_user)) -> list[dict[str, Any]]:
    s = get_settings()
    configured = {"mystocks": s.mystocks_api_key is not None, "synthetic": s.enable_synthetic_provider,
                  "nse_csv": True, "nse_direct": False, "ice": False}
    health = {h.provider_id: h for h in db.scalars(select(DataSourceHealth))}
    counts = dict(db.execute(select(InstrumentIdentifier.provider_id, func.count())
                             .group_by(InstrumentIdentifier.provider_id)).all())
    out = []
    for p in db.scalars(select(DataProvider).order_by(DataProvider.priority)):
        h = health.get(p.id)
        out.append({
            "code": p.code, "name": p.name, "latency_class": p.latency_class, "delay_minutes": p.delay_minutes,
            "is_synthetic": p.is_synthetic, "enabled": p.enabled, "configured": configured.get(p.code, False),
            "priority": p.priority, "attribution": p.attribution, "notes": p.notes,
            "status": h.status if h else ("UNCONFIGURED" if not configured.get(p.code) else "UNKNOWN"),
            "last_success_at": h.last_success_at if h else None, "last_failure_at": h.last_failure_at if h else None,
            "last_error": h.last_error if h else None, "consecutive_failures": h.consecutive_failures if h else 0,
            "latest_market_ts": h.latest_market_ts if h else None,
            "rate_limit": {"limit": h.rate_limit_limit, "remaining": h.rate_limit_remaining,
                           "reset_at": h.rate_limit_reset_at} if h and h.rate_limit_limit else None,
            "instruments": counts.get(p.id, 0),
        })
    return out


@router.get("/data/health")
def data_health(db: Session = Depends(get_db), _: User = Depends(current_user)) -> dict[str, Any]:
    last_session = last_completed_session(db)
    insts = db.scalars(select(Instrument).where(Instrument.security_type.in_(["EQUITY", "ETF", "REIT",
                                                                               "PREFERENCE"]))).all()
    latest = dict(db.execute(select(PriceBar.instrument_id, func.max(PriceBar.session_date))
                             .where(PriceBar.timeframe == "1d").group_by(PriceBar.instrument_id)).all())
    by_universe: dict[str, dict[str, Any]] = {}
    for i in insts:
        u = by_universe.setdefault("synthetic" if i.is_synthetic else "real", {
            "instruments": 0, "with_history": 0, "current": 0, "stale": [], "no_history": [], "suspended": []})
        u["instruments"] += 1
        d = latest.get(i.id)
        if i.trading_status == "SUSPENDED":
            u["suspended"].append(i.symbol)
        if d is None:
            u["no_history"].append(i.symbol)
            continue
        u["with_history"] += 1
        if d >= last_session:
            u["current"] += 1
        elif i.trading_status != "SUSPENDED":
            u["stale"].append({"symbol": i.symbol, "last_bar": d})
    failed = db.scalars(select(IngestionRun).where(IngestionRun.status.in_(["failed", "partial"]),
                                                   IngestionRun.started_at >= datetime.now(timezone.utc)
                                                   - timedelta(days=7)).order_by(IngestionRun.started_at.desc())
                        .limit(20)).all()
    open_issues = db.scalar(select(func.count()).select_from(DataQualityIssue).where(
        DataQualityIssue.resolved_at.is_(None), DataQualityIssue.severity != "info"))
    real = by_universe.get("real", {"instruments": 0, "current": 0})
    provs = providers(db, _)
    real_live = [p for p in provs if not p["is_synthetic"] and p["status"] == "HEALTHY"]
    if real["instruments"] == 0:
        overall = "UNAVAILABLE"
        msg = ("No real market data is loaded. Configure a licensed provider (see Data Centre > Providers). "
               "Any data shown is synthetic development data.")
    elif not real_live:
        overall = "DEGRADED"
        msg = "No healthy real-data provider. Prices may be stale."
    elif real["current"] < real["instruments"]:
        overall = "PARTIAL"
        msg = f"{real['current']} of {real['instruments']} instruments are current for {last_session}."
    else:
        overall = "COMPLETE"
        msg = f"All instruments current for the {last_session} session."
    latency = sorted({p["latency_class"] for p in real_live})
    cache = get_cache()
    return {
        "overall": overall, "message": msg, "last_completed_session": last_session,
        "best_available_latency": latency[0] if latency else None,
        "real_time_available": "LIVE" in latency, "universes": by_universe,
        "open_quality_issues": open_issues,
        "recent_failed_runs": [{"id": r.id, "job": r.job_name, "status": r.status, "started_at": r.started_at,
                                "error": r.error} for r in failed],
        "cache": {"configured": cache.configured, "reachable": cache.ping(), "last_error": cache.last_error},
    }


@router.get("/data/runs")
def runs(job: str | None = None, status: str | None = None, limit: int = Query(50, ge=1, le=500),
         db: Session = Depends(get_db), _: User = Depends(current_user)) -> list[dict[str, Any]]:
    q = select(IngestionRun, DataProvider.code).outerjoin(DataProvider)
    if job:
        q = q.where(IngestionRun.job_name == job)
    if status:
        q = q.where(IngestionRun.status == status)
    return [{"id": r.id, "job": r.job_name, "provider": code, "status": r.status, "started_at": r.started_at,
             "finished_at": r.finished_at, "requested": r.items_requested, "succeeded": r.items_succeeded,
             "failed": r.items_failed, "rows_written": r.rows_written, "error": r.error, "details": r.details}
            for r, code in db.execute(q.order_by(IngestionRun.started_at.desc()).limit(limit)).all()]


@router.get("/data/issues")
def issues(open_only: bool = True, severity: str | None = None, symbol: str | None = None,
           limit: int = Query(100, ge=1, le=1000), db: Session = Depends(get_db),
           _: User = Depends(current_user)) -> dict[str, Any]:
    q = select(DataQualityIssue, Instrument.symbol, DataProvider.code).outerjoin(
        Instrument, Instrument.id == DataQualityIssue.instrument_id).outerjoin(
        DataProvider, DataProvider.id == DataQualityIssue.provider_id)
    if open_only:
        q = q.where(DataQualityIssue.resolved_at.is_(None))
    if severity:
        q = q.where(DataQualityIssue.severity == severity)
    if symbol:
        q = q.where(func.upper(Instrument.symbol) == symbol.upper())
    summary = dict(db.execute(select(DataQualityIssue.issue_type, func.count()).where(
        DataQualityIssue.resolved_at.is_(None)).group_by(DataQualityIssue.issue_type)).all())
    rows = db.execute(q.order_by(DataQualityIssue.detected_at.desc()).limit(limit)).all()
    return {"summary": summary, "items": [
        {"id": i.id, "symbol": sym, "provider": code, "issue_type": i.issue_type, "severity": i.severity,
         "observed_ts": i.observed_ts, "detected_at": i.detected_at, "resolved_at": i.resolved_at,
         "details": i.details} for i, sym, code in rows]}


@router.get("/data/coverage")
def coverage(universe: Literal["real", "synthetic"] | None = None, db: Session = Depends(get_db),
             _: User = Depends(current_user)) -> dict[str, Any]:
    u = resolve_universe(db, universe)
    rows = db.execute(select(
        Instrument.symbol, Instrument.name, Instrument.security_type, Instrument.trading_status, DataProvider.code,
        func.min(PriceBar.session_date), func.max(PriceBar.session_date), func.count(PriceBar.ts),
        func.sum(func.cast(PriceBar.ohlc_available, Integer)), func.sum(func.cast(PriceBar.volume == 0, Integer)),
        func.sum(func.cast(PriceBar.quality_status != "OK", Integer)),
    ).outerjoin(PriceBar, (PriceBar.instrument_id == Instrument.id) & (PriceBar.timeframe == "1d")
                & (PriceBar.provider_id == Instrument.primary_provider_id))
        .outerjoin(DataProvider, DataProvider.id == Instrument.primary_provider_id)
        .where(Instrument.is_synthetic.is_(u == "synthetic"))
        .group_by(Instrument.id, DataProvider.code).order_by(Instrument.symbol)).all()
    q_latest = dict(db.execute(select(Quote.instrument_id, func.max(Quote.market_ts)).group_by(Quote.instrument_id)
                               ).all())
    sym_id = dict(db.execute(select(Instrument.symbol, Instrument.id)).all())
    return {"universe": u, "items": [
        {"symbol": r[0], "name": r[1], "security_type": r[2], "trading_status": r[3], "provider": r[4],
         "first_bar": r[5], "last_bar": r[6], "bars": r[7], "ohlc_share": (r[8] or 0) / r[7] if r[7] else None,
         "zero_volume_share": (r[9] or 0) / r[7] if r[7] else None,
         "non_ok_quality_share": (r[10] or 0) / r[7] if r[7] else None,
         "latest_quote": q_latest.get(sym_id.get(r[0]))} for r in rows]}


@router.get("/news")
def news(universe: Literal["real", "synthetic"] | None = None, kind: str | None = None, symbol: str | None = None,
         limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0), db: Session = Depends(get_db),
         _: User = Depends(current_user)) -> dict[str, Any]:
    u = resolve_universe(db, universe)
    q = select(NewsArticle).where(NewsArticle.is_synthetic.is_(u == "synthetic"))
    if kind:
        q = q.where(NewsArticle.kind == kind.upper())
    if symbol:
        q = q.join(NewsArticleInstrument).join(Instrument).where(func.upper(Instrument.symbol) == symbol.upper())
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    items = db.scalars(q.order_by(NewsArticle.published_at.desc().nulls_last()).limit(limit).offset(offset)).all()
    links = db.execute(select(NewsArticleInstrument.article_id, Instrument.symbol).join(Instrument).where(
        NewsArticleInstrument.article_id.in_([n.id for n in items]))).all()
    by_article: dict[int, list[str]] = {}
    for aid, sym in links:
        by_article.setdefault(aid, []).append(sym)
    return {"universe": u, "total": total, "items": [{**news_dict(n), "symbols": by_article.get(n.id, [])}
                                                     for n in items],
            "policy": "Headlines and summaries are shown as supplied by the source with a link where available. "
                      "No AI-generated summaries or sentiment scores are produced."}


@router.get("/events")
def events(universe: Literal["real", "synthetic"] | None = None, days_back: int = Query(30, ge=0, le=3650),
           days_ahead: int = Query(90, ge=0, le=730), action_type: str | None = None, db: Session = Depends(get_db),
           _: User = Depends(current_user)) -> dict[str, Any]:
    u = resolve_universe(db, universe)
    d0, d1 = date.today() - timedelta(days=days_back), date.today() + timedelta(days=days_ahead)
    key = func.coalesce(CorporateAction.ex_date, CorporateAction.effective_date, CorporateAction.payment_date)
    q = select(CorporateAction, Instrument).join(Instrument).where(
        Instrument.is_synthetic.is_(u == "synthetic"), key >= d0, key <= d1)
    if action_type:
        q = q.where(CorporateAction.action_type == action_type.upper())
    rows = db.execute(q.order_by(key)).all()
    return {"universe": u, "from": d0, "to": d1, "items": [
        {"symbol": i.symbol, "name": i.name, "type": c.action_type, "status": c.status, "title": c.title,
         "announced_at": c.announced_at, "ex_date": c.ex_date, "record_date": c.record_date,
         "payment_date": c.payment_date, "effective_date": c.effective_date, "amount": c.cash_amount,
         "currency": c.currency, "ratio_from": c.ratio_from, "ratio_to": c.ratio_to,
         "source_url": c.source_url} for c, i in rows]}


@router.get("/stream/updates")
async def stream(request: Request, _: User = Depends(current_user_no_session)) -> StreamingResponse:
    """Server-Sent Events: emits an event only when a new ingestion/analytics run finishes. Upstream data is
    delayed/EOD, so updates are infrequent by design; nothing is re-broadcast or synthesised."""

    async def gen() -> AsyncIterator[bytes]:
        last_id = None
        ticks = 0
        while not await request.is_disconnected():
            def latest() -> tuple[int | None, dict[str, Any] | None]:
                with get_sessionmaker()() as db:
                    r = db.scalar(select(IngestionRun).where(IngestionRun.finished_at.is_not(None))
                                  .order_by(IngestionRun.id.desc()).limit(1))
                    return (r.id, {"id": r.id, "job": r.job_name, "status": r.status,
                                   "finished_at": r.finished_at.isoformat() if r.finished_at else None}) if r else (
                        None, None)

            rid, payload = await asyncio.to_thread(latest)
            if rid is not None and rid != last_id:
                if last_id is not None:
                    yield f"event: data_updated\ndata: {json.dumps(payload)}\n\n".encode()
                last_id = rid
            ticks += 1
            if ticks % 3 == 0:
                yield b": keep-alive\n\n"
            await asyncio.sleep(10)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/data/gaps")
def data_gaps_report(_: User = Depends(current_user)) -> dict[str, Any]:
    """Exactly which instruments still have no price bars, and which have bars but no analytics snapshot.

    Overview inner-joins instrument_snapshots, so an instrument missing a snapshot does not appear there at
    all. On a host with no shell this is the only way to see whether a load actually finished.
    """
    from nse_platform.worker.jobs import data_gaps

    g = data_gaps()
    total = len(g["instruments"])
    missing_bars, missing_snaps = g["missing_bars"], g["missing_snapshot_ids"]
    return {
        "instruments": total,
        "complete": total > 0 and not missing_bars and not missing_snaps,
        "loaded": total - len(missing_bars) - len(missing_snaps),
        "missing_bars": {"count": len(missing_bars), "symbols": missing_bars[:200]},
        "missing_snapshots": {"count": len(missing_snaps), "instrument_ids": missing_snaps[:200]},
        "note": "Overview shows only instruments that have a 1d analytics snapshot.",
    }


@router.post("/admin/data/catchup")
def trigger_catchup(_: User = Depends(require_admin)) -> dict[str, Any]:
    """Run a catch-up pass now, on a background thread, instead of waiting for the schedule.

    Returns immediately: a pass can take minutes, and the request must not hold a connection open for it.
    The job takes the same advisory lock as the scheduled one, so a concurrent request or a scheduled run
    skips rather than duplicating work. Poll /api/v1/data/gaps to watch the gap shrink.
    """
    import threading

    from nse_platform.worker.jobs import job_catchup

    threading.Thread(target=job_catchup, name="catchup-manual", daemon=True).start()
    return {"started": True, "watch": "/api/v1/data/gaps"}


@router.get("/admin/users")
def admin_users(db: Session = Depends(get_db), _: User = Depends(require_admin)) -> list[dict[str, Any]]:
    return [{"id": u.id, "email": u.email, "role": u.role, "is_active": u.is_active, "created_at": u.created_at,
             "last_login_at": u.last_login_at} for u in db.scalars(select(User).order_by(User.id))]


@router.post("/admin/data-issues/{issue_id}/resolve")
def resolve_issue(issue_id: int, db: Session = Depends(get_db), _: User = Depends(require_admin)) -> dict:
    i = db.get(DataQualityIssue, issue_id)
    if i is None:
        raise not_found("Issue")
    i.resolved_at = datetime.now(timezone.utc)
    return {"ok": True}
