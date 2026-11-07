from __future__ import annotations

from datetime import date
from typing import Any, Literal

import pandas as pd
from fastapi import APIRouter, Depends, Query
from sqlalchemy import Integer, func, or_, select
from sqlalchemy.orm import Session

from nse_platform.analytics.series import adjustment_events, apply_adjustments, load_daily, resample
from nse_platform.api.deps import current_user, get_db, not_found
from nse_platform.marketdata.queries import instrument_ref, price_view, resolve_universe, sector_names, snapshot_dict
from nse_platform.models import (
    AnalyticsReport,
    CompanyProfile,
    CorporateAction,
    DataProvider,
    FinancialStatementItem,
    FundamentalMetric,
    Instrument,
    InstrumentSnapshot,
    NewsArticle,
    NewsArticleInstrument,
    OpportunityOutcome,
    OpportunitySignal,
    PriceBar,
    Sector,
    SupportResistanceLevel,
    TechnicalIndicatorRow,
    TrendAnalysis,
)

router = APIRouter(prefix="/instruments", tags=["instruments"], dependencies=[Depends(current_user)])
Timeframe = Literal["1d", "1w", "1mo"]


def get_instrument(db: Session, symbol: str) -> Instrument:
    inst = db.scalar(select(Instrument).where(func.upper(Instrument.symbol) == symbol.upper()))
    if inst is None:
        raise not_found("Instrument")
    return inst


@router.get("")
def list_instruments(
    q: str | None = Query(None, max_length=64), security_type: str | None = None, sector: str | None = None,
    universe: Literal["real", "synthetic", "all"] | None = None, sort: str = "symbol",
    limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0), db: Session = Depends(get_db),
) -> dict[str, Any]:
    stmt = select(Instrument, InstrumentSnapshot).outerjoin(
        InstrumentSnapshot, (InstrumentSnapshot.instrument_id == Instrument.id) & (InstrumentSnapshot.timeframe == "1d"))
    if universe != "all":
        u = resolve_universe(db, universe)
        stmt = stmt.where(Instrument.is_synthetic.is_(u == "synthetic"))
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Instrument.symbol.ilike(like), Instrument.name.ilike(like)))
    if security_type:
        stmt = stmt.where(Instrument.security_type == security_type.upper())
    if sector:
        stmt = stmt.join(Sector, Sector.id == Instrument.sector_id).where(Sector.name == sector)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    sort_cols = {"symbol": Instrument.symbol.asc(), "name": Instrument.name.asc(),
                 "change": InstrumentSnapshot.change_pct.desc().nulls_last(),
                 "trading_score": InstrumentSnapshot.trading_score.desc().nulls_last()}
    rows = db.execute(stmt.order_by(sort_cols.get(sort, Instrument.symbol.asc())).limit(limit).offset(offset)).all()
    sectors = sector_names(db)
    pv = price_view(db, [i for i, _ in rows])
    return {"total": total, "limit": limit, "offset": offset, "items": [
        {**instrument_ref(i, sectors), "price": pv[i.id], "snapshot": snapshot_dict(s)} for i, s in rows]}


@router.get("/sectors")
def sectors(db: Session = Depends(get_db)) -> list[str]:
    return list(db.scalars(select(Sector.name).order_by(Sector.name)))


def _coverage(db: Session, inst: Instrument) -> dict[str, Any]:
    row = db.execute(select(func.min(PriceBar.session_date), func.max(PriceBar.session_date), func.count(),
                            func.avg(func.cast(PriceBar.ohlc_available, type_=Integer)),
                            func.avg(func.cast(PriceBar.volume_available, type_=Integer)))
                     .where(PriceBar.instrument_id == inst.id, PriceBar.timeframe == "1d",
                            PriceBar.provider_id == inst.primary_provider_id)).one()
    prov = db.get(DataProvider, inst.primary_provider_id) if inst.primary_provider_id else None
    return {"provider": prov.code if prov else None, "provider_name": prov.name if prov else None,
            "latency_class": prov.latency_class if prov else None, "first_bar": row[0], "last_bar": row[1],
            "daily_bars": row[2], "ohlc_coverage": float(row[3]) if row[3] is not None else None,
            "volume_coverage": float(row[4]) if row[4] is not None else None,
            "attribution": prov.attribution if prov else None}


@router.get("/{symbol}")
def instrument_detail(symbol: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    inst = get_instrument(db, symbol)
    snap = db.scalar(select(InstrumentSnapshot).where(InstrumentSnapshot.instrument_id == inst.id,
                                                      InstrumentSnapshot.timeframe == "1d"))
    prof = db.get(CompanyProfile, inst.id)
    return {
        **instrument_ref(inst), "exchange": "NSE", "issuer": inst.issuer.name if inst.issuer else None,
        "coupon_rate": inst.coupon_rate, "maturity_date": inst.maturity_date, "face_value": inst.face_value,
        "price": price_view(db, [inst])[inst.id], "snapshot": snapshot_dict(snap),
        "scores": snap.score_details if snap else None,
        "profile": {"description": prof.description, "website": prof.website, "market_cap": prof.market_cap,
                    "provider_pe_ratio": prof.provider_pe_ratio, "fetched_at": prof.fetched_at} if prof else None,
        "coverage": _coverage(db, inst),
        "analytics_applicable": inst.security_type in ("EQUITY", "PREFERENCE", "ETF", "REIT"),
    }


@router.get("/{symbol}/history")
def history(symbol: str, timeframe: Timeframe = "1d", adjusted: bool = True, start: date | None = None,
            end: date | None = None, db: Session = Depends(get_db)) -> dict[str, Any]:
    inst = get_instrument(db, symbol)
    raw = load_daily(db, inst)
    events = adjustment_events(db, inst.id)
    df = apply_adjustments(raw, events) if adjusted else raw.copy()
    bars = resample(df, timeframe)
    if start:
        bars = bars[bars.index >= pd.Timestamp(start)]
    if end:
        bars = bars[bars.index <= pd.Timestamp(end)]

    def f(v: Any) -> float | None:
        return None if v is None or pd.isna(v) else round(float(v), 6)

    items = [{"date": ts.date().isoformat(), "open": f(r["open"]), "high": f(r["high"]), "low": f(r["low"]),
              "close": f(r["close"]), "volume": f(r["volume"]), "ohlc_available": bool(r["ohlc_available"]),
              "quality": r.get("quality_status", None) if timeframe == "1d" else None,
              "is_partial": bool(r.get("is_partial", False))}
             for ts, r in bars.iterrows()]
    return {"symbol": inst.symbol, "timeframe": timeframe, "adjusted": adjusted, "is_synthetic": inst.is_synthetic,
            "adjustments": [{"ex_date": e.ex_date, "price_factor": e.factor, "description": e.description}
                            for e in events],
            "adjustment_policy": "Split/bonus adjusted (prices x factor before ex-date). Raw bars are preserved; "
                                 "request adjusted=false for as-reported prices. Cash dividends are not adjusted.",
            "missing_values_policy": "Fields the provider did not report are null - never zero or interpolated.",
            "items": items}


@router.get("/{symbol}/indicators")
def indicators(symbol: str, timeframe: Timeframe = "1d", names: str | None = None, start: date | None = None,
               limit: int = Query(2000, ge=1, le=6000), db: Session = Depends(get_db)) -> dict[str, Any]:
    inst = get_instrument(db, symbol)
    q = select(TechnicalIndicatorRow).where(TechnicalIndicatorRow.instrument_id == inst.id,
                                            TechnicalIndicatorRow.timeframe == timeframe)
    if start:
        q = q.where(TechnicalIndicatorRow.ts >= pd.Timestamp(start, tz="UTC").to_pydatetime())
    rows = db.scalars(q.order_by(TechnicalIndicatorRow.ts.desc()).limit(limit)).all()
    wanted = {n.strip() for n in names.split(",")} if names else None
    items = [{"date": r.ts.date().isoformat(), **({k: v for k, v in r.values.items() if wanted is None or k in wanted})}
             for r in reversed(rows)]
    return {"symbol": inst.symbol, "timeframe": timeframe, "calc_version": rows[0].calc_version if rows else None,
            "note": "Indicators are computed on split-adjusted prices; each value uses only data up to its date.",
            "items": items}


@router.get("/{symbol}/analysis")
def analysis(symbol: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    inst = get_instrument(db, symbol)
    trends = {}
    for tf in ("1d", "1w", "1mo"):
        t = db.scalar(select(TrendAnalysis).where(TrendAnalysis.instrument_id == inst.id, TrendAnalysis.timeframe == tf)
                      .order_by(TrendAnalysis.as_of_ts.desc()).limit(1))
        trends[tf] = {"classification": t.classification, "strength": t.strength, "evidence": t.evidence,
                      "as_of": t.as_of_ts, "calc_version": t.calc_version} if t else None
    levels = db.scalars(select(SupportResistanceLevel).where(SupportResistanceLevel.instrument_id == inst.id)
                        .order_by(SupportResistanceLevel.timeframe, SupportResistanceLevel.price)).all()
    sigs = db.scalars(select(OpportunitySignal).where(OpportunitySignal.instrument_id == inst.id)
                      .order_by(OpportunitySignal.bar_ts.desc()).limit(40)).all()
    outcomes = db.execute(select(OpportunitySignal.signal_type, OpportunityOutcome.horizon_bars,
                                 func.count(), func.avg(OpportunityOutcome.forward_return))
                          .join(OpportunityOutcome).where(OpportunitySignal.instrument_id == inst.id)
                          .group_by(OpportunitySignal.signal_type, OpportunityOutcome.horizon_bars)).all()
    snap = db.scalar(select(InstrumentSnapshot).where(InstrumentSnapshot.instrument_id == inst.id,
                                                      InstrumentSnapshot.timeframe == "1d"))
    return {
        "symbol": inst.symbol, "is_synthetic": inst.is_synthetic, "trends": trends,
        "trend_note": "Timeframes are classified independently and may disagree.",
        "levels": [{"timeframe": lv.timeframe, "type": lv.level_type, "price": lv.price, "touches": lv.touches,
                    "first_touch": lv.first_touch_ts, "last_touch": lv.last_touch_ts} for lv in levels],
        "snapshot": snapshot_dict(snap), "scores": snap.score_details if snap else None,
        "signals": [_signal(s) for s in sigs],
        "instrument_signal_outcomes": [{"signal_type": a, "horizon_bars": h, "n": n, "mean_forward_return": m}
                                       for a, h, n, m in outcomes],
    }


def _signal(s: OpportunitySignal) -> dict[str, Any]:
    return {"id": s.id, "category": s.category, "signal_type": s.signal_type, "direction": s.direction,
            "timeframe": s.timeframe, "bar_ts": s.bar_ts, "detected_at": s.detected_at,
            "reference_price": s.reference_price, "evidence": s.evidence, "invalidation": s.invalidation,
            "risk_factors": s.risk_factors, "status": s.status, "status_changed_at": s.status_changed_at,
            "expires_at": s.expires_at, "is_backfill": s.is_backfill, "calc_version": s.calc_version}


@router.get("/{symbol}/fundamentals")
def fundamentals(symbol: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    inst = get_instrument(db, symbol)
    items = db.scalars(select(FinancialStatementItem).where(FinancialStatementItem.instrument_id == inst.id)
                       .order_by(FinancialStatementItem.period_end)).all()
    periods: dict[str, dict[str, Any]] = {}
    for it in items:
        p = periods.setdefault(f"{it.period_type}:{it.period_end}", {
            "period_end": it.period_end, "period_type": it.period_type, "published_at": it.published_at,
            "currency": it.currency, "sources": set(), "items": {}})
        p["items"][it.line_item] = it.value
        p["sources"].add(it.source)
        p["published_at"] = max(p["published_at"], it.published_at)
    latest_asof = db.scalar(select(func.max(FundamentalMetric.as_of)).where(FundamentalMetric.instrument_id == inst.id))
    metrics = db.scalars(select(FundamentalMetric).where(FundamentalMetric.instrument_id == inst.id,
                                                         FundamentalMetric.as_of == latest_asof)).all()
    divs = db.scalars(select(CorporateAction).where(CorporateAction.instrument_id == inst.id,
                                                    CorporateAction.action_type == "DIVIDEND_CASH")
                      .order_by(CorporateAction.ex_date.desc().nulls_last())).all()
    applicable = inst.security_type in ("EQUITY", "PREFERENCE", "REIT")
    return {
        "symbol": inst.symbol, "is_synthetic": inst.is_synthetic, "applicable": applicable,
        "applicability_note": None if applicable else f"Equity valuation ratios are not applied to "
                                                      f"{inst.security_type} instruments.",
        "periods": [{**p, "sources": sorted(p["sources"])} for p in periods.values()],
        "metrics": {"as_of": latest_asof, "calc_version": metrics[0].calc_version if metrics else None,
                    "values": {m.metric: m.value for m in metrics},
                    "period_end": metrics[0].period_end if metrics else None,
                    "inputs": metrics[0].inputs if metrics else None},
        "dividends": [{"ex_date": d.ex_date, "record_date": d.record_date, "payment_date": d.payment_date,
                       "amount": d.cash_amount, "currency": d.currency, "status": d.status, "title": d.title}
                      for d in divs],
        "availability": "available" if items else "unavailable",
        "note": None if items else "No financial statements on file. Connected providers do not supply statements; "
                                   "import published figures with `nse-cli import-csv financials`.",
    }


@router.get("/{symbol}/events")
def events(symbol: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    inst = get_instrument(db, symbol)
    cas = db.scalars(select(CorporateAction).where(CorporateAction.instrument_id == inst.id).order_by(
        func.coalesce(CorporateAction.ex_date, CorporateAction.effective_date).desc().nulls_last())).all()
    news = db.execute(select(NewsArticle).join(NewsArticleInstrument).where(
        NewsArticleInstrument.instrument_id == inst.id).order_by(NewsArticle.published_at.desc().nulls_last())
        .limit(50)).scalars().all()
    return {
        "symbol": inst.symbol,
        "corporate_actions": [{"id": c.id, "type": c.action_type, "status": c.status, "title": c.title,
                               "announced_at": c.announced_at, "ex_date": c.ex_date, "record_date": c.record_date,
                               "payment_date": c.payment_date, "effective_date": c.effective_date,
                               "amount": c.cash_amount, "currency": c.currency, "ratio_from": c.ratio_from,
                               "ratio_to": c.ratio_to, "source_url": c.source_url} for c in cas],
        "news": [news_dict(n) for n in news],
    }


def news_dict(n: NewsArticle) -> dict[str, Any]:
    return {"id": n.id, "kind": n.kind, "category": n.category, "title": n.title, "publisher": n.publisher,
            "url": n.url, "summary": n.summary, "summary_is_generated": n.summary_is_generated,
            "published_at": n.published_at, "ingested_at": n.ingested_at, "source_reliability": n.source_reliability,
            "is_synthetic": n.is_synthetic}


__all__ = ["AnalyticsReport", "get_instrument", "news_dict", "router"]
