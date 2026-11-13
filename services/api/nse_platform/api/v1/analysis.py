"""Scanner, opportunities, trends, methodology and calibration reports."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nse_platform.analytics.scores import methodology_document
from nse_platform.api.deps import current_user, get_db, not_found
from nse_platform.api.v1.instruments import _signal
from nse_platform.marketdata.queries import instrument_ref, resolve_universe, sector_names, snapshot_dict
from nse_platform.models import (
    AnalyticsReport,
    Instrument,
    InstrumentSnapshot,
    OpportunitySignal,
    SavedScan,
    Sector,
    TrendAnalysis,
    User,
)
from nse_platform.opportunities.detectors import SIGNAL_CALC_VERSION, TECHNICAL

router = APIRouter(tags=["analysis"], dependencies=[Depends(current_user)])
Universe = Literal["real", "synthetic"] | None


class ScanFilters(BaseModel):
    security_types: list[str] | None = None
    sectors: list[str] | None = None
    price_min: float | None = None
    price_max: float | None = None
    volume_min: float | None = None
    relative_volume_min: float | None = None
    change_min: float | None = None
    change_max: float | None = None
    rsi_min: float | None = Field(None, ge=0, le=100)
    rsi_max: float | None = Field(None, ge=0, le=100)
    trend_classes: list[str] | None = None
    volatility_max: float | None = None
    dividend_yield_min: float | None = None
    trading_score_min: float | None = None
    quality_score_min: float | None = None
    risk_score_max: float | None = None
    trading_frequency_min: float | None = Field(None, ge=0, le=1)
    sort: str = "trading_score"
    descending: bool = True


RANGE = {
    "price_min": ("last_close", ">="), "price_max": ("last_close", "<="), "volume_min": ("volume", ">="),
    "relative_volume_min": ("relative_volume", ">="), "change_min": ("change_pct", ">="),
    "change_max": ("change_pct", "<="), "rsi_min": ("rsi_14", ">="), "rsi_max": ("rsi_14", "<="),
    "volatility_max": ("volatility_60", "<="), "dividend_yield_min": ("dividend_yield", ">="),
    "trading_score_min": ("trading_score", ">="), "quality_score_min": ("quality_score", ">="),
    "risk_score_max": ("risk_score", "<="), "trading_frequency_min": ("trading_frequency_60", ">="),
}
SORTABLE = {"trading_score", "quality_score", "risk_score", "change_pct", "relative_volume", "rsi_14", "return_3m",
            "return_1y", "dividend_yield", "volatility_60", "volume", "last_close", "pe_ratio",
            "avg_traded_value_20", "relative_strength_3m"}


def run_scan(db: Session, f: ScanFilters, universe: str, limit: int, offset: int) -> dict[str, Any]:
    stmt = select(Instrument, InstrumentSnapshot).join(
        InstrumentSnapshot, (InstrumentSnapshot.instrument_id == Instrument.id) & (InstrumentSnapshot.timeframe == "1d")
    ).where(Instrument.is_synthetic.is_(universe == "synthetic"))
    if f.security_types:
        stmt = stmt.where(Instrument.security_type.in_([t.upper() for t in f.security_types]))
    if f.sectors:
        stmt = stmt.join(Sector, Sector.id == Instrument.sector_id).where(Sector.name.in_(f.sectors))
    if f.trend_classes:
        stmt = stmt.where(InstrumentSnapshot.trend_class.in_(f.trend_classes))
    for key, (col, op) in RANGE.items():
        v = getattr(f, key)
        if v is None:
            continue
        c = getattr(InstrumentSnapshot, col)
        stmt = stmt.where(c >= v if op == ">=" else c <= v)  # NULL values never pass a numeric filter
    if f.sort not in SORTABLE:
        raise HTTPException(400, detail={"code": "BAD_SORT", "message": f"sort must be one of {sorted(SORTABLE)}"})
    col = getattr(InstrumentSnapshot, f.sort)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.execute(stmt.order_by(col.desc().nulls_last() if f.descending else col.asc().nulls_last(),
                                    Instrument.symbol).limit(limit).offset(offset)).all()
    sectors = sector_names(db)
    return {"universe": universe, "total": total, "limit": limit, "offset": offset,
            "note": "Instruments with a missing value for a filtered metric are excluded, not treated as zero.",
            "items": [{**instrument_ref(i, sectors), **(snapshot_dict(s) or {})} for i, s in rows]}


@router.post("/scanner/results")
def scanner_post(f: ScanFilters, universe: Universe = None, limit: int = Query(100, ge=1, le=500),
                 offset: int = Query(0, ge=0), db: Session = Depends(get_db)) -> dict[str, Any]:
    return run_scan(db, f, resolve_universe(db, universe), limit, offset)


@router.get("/scanner/results")
def scanner_get(universe: Universe = None, sort: str = "trading_score", limit: int = Query(100, ge=1, le=500),
                offset: int = Query(0, ge=0), db: Session = Depends(get_db)) -> dict[str, Any]:
    return run_scan(db, ScanFilters(sort=sort), resolve_universe(db, universe), limit, offset)


class SavedScanIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    filters: ScanFilters


@router.get("/scanner/saved")
def saved_list(db: Session = Depends(get_db), user: User = Depends(current_user)) -> list[dict[str, Any]]:
    return [{"id": s.id, "name": s.name, "filters": s.filters, "updated_at": s.updated_at}
            for s in db.scalars(select(SavedScan).where(SavedScan.user_id == user.id).order_by(SavedScan.name))]


@router.post("/scanner/saved")
def saved_create(body: SavedScanIn, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    existing = db.scalar(select(SavedScan).where(SavedScan.user_id == user.id, SavedScan.name == body.name))
    if existing:
        existing.filters = body.filters.model_dump()
        db.flush()
        return {"id": existing.id, "name": existing.name}
    s = SavedScan(user_id=user.id, name=body.name, filters=body.filters.model_dump())
    db.add(s)
    db.flush()
    return {"id": s.id, "name": s.name}


@router.delete("/scanner/saved/{scan_id}")
def saved_delete(scan_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    s = db.scalar(select(SavedScan).where(SavedScan.id == scan_id, SavedScan.user_id == user.id))
    if s is None:
        raise not_found("Saved scan")
    db.delete(s)
    return {"ok": True}


@router.get("/opportunities")
def opportunities(
    universe: Universe = None, category: str | None = None, signal_type: str | None = None,
    direction: str | None = None, timeframe: str | None = None,
    status: Literal["ACTIVE", "INVALIDATED", "EXPIRED", "ALL"] = "ACTIVE", include_backfill: bool = False,
    symbol: str | None = None, limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    u = resolve_universe(db, universe)
    stmt = select(OpportunitySignal, Instrument).join(Instrument).where(
        Instrument.is_synthetic.is_(u == "synthetic"), OpportunitySignal.calc_version == SIGNAL_CALC_VERSION)
    if not include_backfill:
        stmt = stmt.where(OpportunitySignal.is_backfill.is_(False))
    if status != "ALL":
        stmt = stmt.where(OpportunitySignal.status == status)
    for col, v in ((OpportunitySignal.category, category), (OpportunitySignal.signal_type, signal_type),
                   (OpportunitySignal.direction, direction), (OpportunitySignal.timeframe, timeframe)):
        if v:
            stmt = stmt.where(col == v.upper() if col is not OpportunitySignal.timeframe else col == v)
    if symbol:
        stmt = stmt.where(func.upper(Instrument.symbol) == symbol.upper())
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.execute(stmt.order_by(OpportunitySignal.bar_ts.desc(), OpportunitySignal.id.desc())
                      .limit(limit).offset(offset)).all()
    rates = _base_rate_index(db, u)
    sectors = sector_names(db)
    snaps = {s.instrument_id: s for s in db.scalars(select(InstrumentSnapshot).where(
        InstrumentSnapshot.instrument_id.in_([i.id for _, i in rows]), InstrumentSnapshot.timeframe == "1d"))}
    items = []
    for sg, inst in rows:
        snap = snaps.get(inst.id)
        items.append({**_signal(sg), "instrument": instrument_ref(inst, sectors),
                      "current_price": snap.last_close if snap else None,
                      "change_since_signal": (snap.last_close / sg.reference_price - 1)
                      if snap and snap.last_close and sg.reference_price else None,
                      "risk_score": snap.risk_score if snap else None,
                      "historical_performance": rates.get((sg.signal_type, sg.timeframe))})
    return {"universe": u, "total": total, "limit": limit, "offset": offset, "items": items}


def _base_rate_index(db: Session, universe: str) -> dict[tuple[str, str], list[dict[str, Any]]]:
    rep = db.scalar(select(AnalyticsReport).where(AnalyticsReport.code == "signal_base_rates",
                                                  AnalyticsReport.universe == universe))
    out: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for s in (rep.payload.get("stats", []) if rep else []):
        out.setdefault((s["signal_type"], s["timeframe"]), []).append(s)
    for v in out.values():
        v.sort(key=lambda s: s["horizon_bars"])
    return out


@router.get("/opportunities/base-rates")
def base_rates(universe: Universe = None, db: Session = Depends(get_db)) -> dict[str, Any]:
    u = resolve_universe(db, universe)
    rep = db.scalar(select(AnalyticsReport).where(AnalyticsReport.code == "signal_base_rates",
                                                  AnalyticsReport.universe == u))
    return {"universe": u, "computed_at": rep.computed_at if rep else None, **(rep.payload if rep else {"stats": []})}


@router.get("/opportunities/detectors")
def detectors() -> list[dict[str, Any]]:
    return [{"signal_type": d.signal_type, "category": d.category.value, "direction": d.direction.value,
             "title": d.title, "description": d.description, "expiry_bars": d.expiry_bars,
             "cooldown_bars": d.cooldown} for d in TECHNICAL]


@router.get("/surges")
def surges(universe: Universe = None, days: int = Query(30, ge=1, le=3650), db: Session = Depends(get_db)) -> dict:
    """Surge Radar: live surge / pre-breakout signals (newest first)."""
    from datetime import datetime, timedelta, timezone

    from nse_platform.opportunities.surge import SURGE_TYPES

    u = resolve_universe(db, universe)
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = db.execute(select(OpportunitySignal, Instrument).join(Instrument).where(
        Instrument.is_synthetic.is_(u == "synthetic"), OpportunitySignal.signal_type.in_(SURGE_TYPES),
        # Replayed detections are included: they used only data available at that day's close.
        OpportunitySignal.calc_version == SIGNAL_CALC_VERSION, OpportunitySignal.bar_ts >= since)
        .order_by(OpportunitySignal.bar_ts.desc(), OpportunitySignal.id.desc()).limit(200)).all()
    snaps = {s.instrument_id: s for s in db.scalars(select(InstrumentSnapshot).where(
        InstrumentSnapshot.instrument_id.in_([i.id for _, i in rows]), InstrumentSnapshot.timeframe == "1d"))}
    sectors = sector_names(db)
    return {"universe": u, "items": [{**_signal(sg), "instrument": instrument_ref(i, sectors),
                                      "current_price": snaps[i.id].last_close if i.id in snaps else None,
                                      "change_since_signal": (snaps[i.id].last_close / sg.reference_price - 1)
                                      if i.id in snaps and snaps[i.id].last_close else None}
                                     for sg, i in rows]}


@router.get("/opportunities/{signal_id}/sheet")
def surge_sheet(signal_id: int, shares: int | None = Query(None, ge=1, le=100_000_000),
                budget: float | None = Query(None, gt=0, le=1e11), db: Session = Depends(get_db)) -> dict:
    """Surge Sheet: position costs, liquidity, historical outcome scenarios in KES, stop, data quality."""
    from nse_platform.opportunities.surge import build_sheet

    row = db.execute(select(OpportunitySignal, Instrument).join(Instrument).where(
        OpportunitySignal.id == signal_id)).first()
    if row is None:
        raise not_found("Signal")
    return build_sheet(db, row[0], row[1], shares, budget)


@router.get("/opportunities/{signal_id}")
def opportunity(signal_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    row = db.execute(select(OpportunitySignal, Instrument).join(Instrument).where(
        OpportunitySignal.id == signal_id)).first()
    if row is None:
        raise not_found("Signal")
    sg, inst = row
    from nse_platform.models import OpportunityOutcome

    outs = db.scalars(select(OpportunityOutcome).where(OpportunityOutcome.signal_id == sg.id)
                      .order_by(OpportunityOutcome.horizon_bars)).all()
    rates = _base_rate_index(db, "synthetic" if inst.is_synthetic else "real")
    return {**_signal(sg), "instrument": instrument_ref(inst),
            "outcomes": [{"horizon_bars": o.horizon_bars, "forward_return": o.forward_return,
                          "max_favorable": o.max_favorable, "max_adverse": o.max_adverse,
                          "invalidated": o.invalidated, "end_ts": o.end_ts} for o in outs],
            "historical_performance": rates.get((sg.signal_type, sg.timeframe))}


@router.get("/trends")
def trends(universe: Universe = None, timeframe: Literal["1d", "1w", "1mo"] = "1d",
           classification: str | None = None, db: Session = Depends(get_db)) -> dict[str, Any]:
    u = resolve_universe(db, universe)
    latest = (select(TrendAnalysis.instrument_id, func.max(TrendAnalysis.as_of_ts).label("ts"))
              .where(TrendAnalysis.timeframe == timeframe).group_by(TrendAnalysis.instrument_id).subquery())
    stmt = (select(TrendAnalysis, Instrument).join(latest, (latest.c.instrument_id == TrendAnalysis.instrument_id)
                                                    & (latest.c.ts == TrendAnalysis.as_of_ts))
            .join(Instrument, Instrument.id == TrendAnalysis.instrument_id)
            .where(TrendAnalysis.timeframe == timeframe, Instrument.is_synthetic.is_(u == "synthetic")))
    if classification:
        stmt = stmt.where(TrendAnalysis.classification == classification.upper())
    rows = db.execute(stmt.order_by(TrendAnalysis.strength.desc().nulls_last())).all()
    others = {}
    for tf in ("1d", "1w", "1mo"):
        if tf == timeframe:
            continue
        sub = (select(TrendAnalysis.instrument_id, func.max(TrendAnalysis.as_of_ts).label("ts"))
               .where(TrendAnalysis.timeframe == tf).group_by(TrendAnalysis.instrument_id).subquery())
        for t in db.scalars(select(TrendAnalysis).join(sub, (sub.c.instrument_id == TrendAnalysis.instrument_id)
                                                       & (sub.c.ts == TrendAnalysis.as_of_ts))
                            .where(TrendAnalysis.timeframe == tf)):
            others.setdefault(t.instrument_id, {})[tf] = t.classification
    sectors = sector_names(db)
    dist: dict[str, int] = {}
    for t, _ in rows:
        dist[t.classification] = dist.get(t.classification, 0) + 1
    return {"universe": u, "timeframe": timeframe, "distribution": dist, "items": [
        {**instrument_ref(i, sectors), "classification": t.classification, "strength": t.strength,
         "as_of": t.as_of_ts, "evidence": t.evidence, "other_timeframes": others.get(i.id, {})} for t, i in rows]}


@router.get("/methodology/scores")
def score_methodology(universe: Universe = None, db: Session = Depends(get_db)) -> dict[str, Any]:
    u = resolve_universe(db, universe)
    cal = db.scalar(select(AnalyticsReport).where(AnalyticsReport.code == "trading_score_calibration",
                                                  AnalyticsReport.universe == u))
    return {**methodology_document(), "calibration": {"universe": u, "computed_at": cal.computed_at if cal else None,
                                                      **(cal.payload if cal else {"status": "not_computed"})}}


@router.get("/fundamentals/summary")
def fundamentals_summary(universe: Universe = None, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Latest point-in-time fundamental metrics per instrument (for cross-sectional comparison)."""
    from nse_platform.models import FundamentalMetric

    u = resolve_universe(db, universe)
    latest = (select(FundamentalMetric.instrument_id, func.max(FundamentalMetric.as_of).label("as_of"))
              .group_by(FundamentalMetric.instrument_id).subquery())
    rows = db.execute(select(FundamentalMetric, Instrument).join(
        latest, (latest.c.instrument_id == FundamentalMetric.instrument_id) & (latest.c.as_of == FundamentalMetric.as_of))
        .join(Instrument, Instrument.id == FundamentalMetric.instrument_id)
        .where(Instrument.is_synthetic.is_(u == "synthetic"))).all()
    sectors = sector_names(db)
    by: dict[int, dict[str, Any]] = {}
    for m, inst in rows:
        e = by.setdefault(inst.id, {**instrument_ref(inst, sectors), "as_of": m.as_of, "period_end": m.period_end,
                                    "metrics": {}})
        e["metrics"][m.metric] = m.value
    snaps = {s.instrument_id: s.quality_score for s in db.scalars(select(InstrumentSnapshot).where(
        InstrumentSnapshot.instrument_id.in_(list(by)), InstrumentSnapshot.timeframe == "1d"))}
    items = sorted(by.values(), key=lambda e: e["symbol"])
    for e in items:
        e["quality_score"] = snaps.get(e["id"])
        e["has_statements"] = e["period_end"] is not None
    return {"universe": u, "items": items,
            "note": "Values are point-in-time as of each instrument's last session. Missing statements mean the "
                    "metric is unavailable, not zero."}
