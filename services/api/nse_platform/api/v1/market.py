from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nse_platform.api.deps import current_user, get_db
from nse_platform.core.cache import get_cache
from nse_platform.marketdata.freshness import EAT, last_completed_session, market_open_now
from nse_platform.marketdata.queries import (
    health_by_provider,
    instrument_ref,
    price_view,
    resolve_universe,
    sector_names,
    snapshot_dict,
)
from nse_platform.models import (
    CorporateAction,
    DataProvider,
    IndexHistory,
    Instrument,
    InstrumentSnapshot,
    MarketIndex,
    OpportunitySignal,
    Portfolio,
    User,
    Watchlist,
    WatchlistItem,
)

router = APIRouter(prefix="/market", tags=["market"], dependencies=[Depends(current_user)])
Universe = Literal["real", "synthetic"] | None


def _rows(db: Session, universe: str) -> list[tuple[Instrument, InstrumentSnapshot]]:
    return db.execute(select(Instrument, InstrumentSnapshot).join(
        InstrumentSnapshot, InstrumentSnapshot.instrument_id == Instrument.id).where(
        Instrument.is_synthetic.is_(universe == "synthetic"), InstrumentSnapshot.timeframe == "1d")).all()


def _item(i: Instrument, s: InstrumentSnapshot, sectors: dict[int, str]) -> dict[str, Any]:
    return {**instrument_ref(i, sectors), "last_close": s.last_close, "change_pct": s.change_pct, "volume": s.volume,
            "relative_volume": s.relative_volume, "trend_class": s.trend_class, "trading_score": s.trading_score,
            "risk_score": s.risk_score, "session_date": s.session_date, "avg_traded_value_20": s.avg_traded_value_20}


@router.get("/status")
def status(db: Session = Depends(get_db)) -> dict[str, Any]:
    is_open, reason = market_open_now(db)
    now = datetime.now(timezone.utc)
    provs = {p.id: p for p in db.scalars(select(DataProvider))}
    return {
        "exchange": "NSE", "is_open": is_open, "reason": reason, "server_time": now.isoformat(),
        "local_time": now.astimezone(EAT).isoformat(), "trading_hours": {"open": "09:30", "close": "15:00",
                                                                        "timezone": "Africa/Nairobi"},
        "last_completed_session": last_completed_session(db).isoformat(),
        "calendar_note": "Session calendar from provider holiday data where available; otherwise weekdays.",
        "providers": [{"code": provs[h.provider_id].code, "status": h.status, "last_success_at": h.last_success_at,
                       "latency_class": provs[h.provider_id].latency_class}
                      for h in health_by_provider(db).values() if h.provider_id in provs],
    }


@router.get("/indices")
def indices(db: Session = Depends(get_db)) -> dict[str, Any]:
    out = []
    for idx in db.scalars(select(MarketIndex).order_by(MarketIndex.code)):
        hist = db.execute(select(IndexHistory.session_date, IndexHistory.close).where(
            IndexHistory.index_id == idx.id).order_by(IndexHistory.ts.desc()).limit(260)).all()
        if not hist:
            continue
        last, prev = hist[0], hist[1] if len(hist) > 1 else None
        out.append({"code": idx.code, "name": idx.name, "date": last[0], "value": last[1],
                    "change_pct": last[1] / prev[1] - 1 if prev else None,
                    "history": [{"date": d, "close": c} for d, c in reversed(hist)]})
    return {"items": out, "available": bool(out),
            "note": None if out else "No index data. The connected providers do not supply NSE index values "
                                     "(NASI, NSE 20, NSE 25). Import licensed index files with "
                                     "`nse-cli import-csv index`."}


@router.get("/movers")
def movers(universe: Universe = None, limit: int = Query(10, ge=1, le=50),
           db: Session = Depends(get_db)) -> dict[str, Any]:
    u = resolve_universe(db, universe)
    sectors = sector_names(db)
    rows = [(i, s) for i, s in _rows(db, u) if s.change_pct is not None]
    latest = max((s.session_date for _, s in rows if s.session_date), default=None)
    cur = [(i, s) for i, s in rows if s.session_date == latest]
    traded = [(i, s) for i, s in cur if (s.volume or 0) > 0]
    return {
        "universe": u, "session_date": latest,
        "gainers": [_item(i, s, sectors) for i, s in sorted(traded, key=lambda r: -r[1].change_pct)[:limit]
                    if s.change_pct > 0],
        "losers": [_item(i, s, sectors) for i, s in sorted(traded, key=lambda r: r[1].change_pct)[:limit]
                   if s.change_pct < 0],
        "most_active": [_item(i, s, sectors) for i, s in sorted(
            traded, key=lambda r: -(r[1].volume or 0) * (r[1].last_close or 0))[:limit]],
        "unusual_volume": [_item(i, s, sectors) for i, s in sorted(
            [r for r in traded if (r[1].relative_volume or 0) >= 2], key=lambda r: -(r[1].relative_volume or 0))[:limit]],
        "note": "Movers rank instruments with trades in the latest session; instruments without trades are excluded.",
    }


@router.get("/overview")
def overview(universe: Universe = None, db: Session = Depends(get_db),
             user: User = Depends(current_user)) -> dict[str, Any]:
    u = resolve_universe(db, universe)
    cache_key = f"overview:{u}:{user.id}"
    cached = get_cache().get_json(cache_key)
    if cached:
        return cached
    sectors = sector_names(db)
    rows = _rows(db, u)
    latest = max((s.session_date for _, s in rows if s.session_date), default=None)
    cur = [(i, s) for i, s in rows if s.session_date == latest]
    traded = [(i, s) for i, s in cur if (s.volume or 0) > 0]
    adv = sum(1 for _, s in traded if (s.change_pct or 0) > 0)
    dec = sum(1 for _, s in traded if (s.change_pct or 0) < 0)
    unch = sum(1 for _, s in traded if s.change_pct == 0)
    no_trade = len(cur) - len(traded)
    stale = len(rows) - len(cur)
    turnover = sum((s.volume or 0) * (s.last_close or 0) for _, s in traded)
    ew = [s.change_pct for _, s in traded if s.change_pct is not None]
    trends: dict[str, int] = {}
    for _, s in rows:
        trends[s.trend_class or "INSUFFICIENT_DATA"] = trends.get(s.trend_class or "INSUFFICIENT_DATA", 0) + 1
    bull = sorted([r for r in rows if r[1].trend_class == "STRONG_BULLISH"], key=lambda r: -(r[1].trading_score or 0))
    bear = sorted([r for r in rows if r[1].trend_class == "STRONG_BEARISH"], key=lambda r: (r[1].trading_score or 0))
    since = datetime.now(timezone.utc) - timedelta(days=7)
    sigs = db.execute(select(OpportunitySignal, Instrument).join(Instrument).where(
        Instrument.is_synthetic.is_(u == "synthetic"), OpportunitySignal.is_backfill.is_(False),
        OpportunitySignal.status == "ACTIVE", OpportunitySignal.detected_at >= since)
        .order_by(OpportunitySignal.bar_ts.desc(), OpportunitySignal.id.desc()).limit(12)).all()
    today = date.today()
    events = db.execute(select(CorporateAction, Instrument).join(Instrument).where(
        Instrument.is_synthetic.is_(u == "synthetic"),
        func.coalesce(CorporateAction.ex_date, CorporateAction.payment_date, CorporateAction.effective_date) >= today)
        .order_by(func.coalesce(CorporateAction.ex_date, CorporateAction.payment_date, CorporateAction.effective_date))
        .limit(10)).all()
    wl = db.execute(select(Watchlist.name, Instrument, InstrumentSnapshot).join(
        WatchlistItem, WatchlistItem.watchlist_id == Watchlist.id).join(
        Instrument, Instrument.id == WatchlistItem.instrument_id).outerjoin(
        InstrumentSnapshot, (InstrumentSnapshot.instrument_id == Instrument.id) & (InstrumentSnapshot.timeframe == "1d"))
        .where(Watchlist.user_id == user.id).limit(12)).all()
    n_portfolios = db.scalar(select(func.count()).select_from(Portfolio).where(Portfolio.user_id == user.id))
    insts = [i for i, _ in rows]
    pv = price_view(db, insts[:200])
    fresh_counts: dict[str, int] = {}
    for v in pv.values():
        fresh_counts[v["freshness"]["status"]] = fresh_counts.get(v["freshness"]["status"], 0) + 1
    result = {
        "universe": u, "is_synthetic": u == "synthetic", "session_date": latest,
        "breadth": {"advancing": adv, "declining": dec, "unchanged": unch, "no_trades": no_trade,
                    "not_updated": stale, "total": len(rows)},
        "equal_weight_change": sum(ew) / len(ew) if ew else None,
        "turnover_estimate": turnover if traded else None,
        "turnover_note": "Sum of close x volume over instruments that traded (approximation; not official turnover).",
        "trend_distribution": trends,
        "strongest_bullish": [_item(i, s, sectors) for i, s in bull[:6]],
        "strongest_bearish": [_item(i, s, sectors) for i, s in bear[:6]],
        "new_opportunities": [{"id": sg.id, "symbol": i.symbol, "name": i.name, "signal_type": sg.signal_type,
                               "category": sg.category, "direction": sg.direction, "timeframe": sg.timeframe,
                               "bar_ts": sg.bar_ts, "reference_price": sg.reference_price} for sg, i in sigs],
        "upcoming_events": [{"symbol": i.symbol, "action_type": ca.action_type, "title": ca.title,
                             "ex_date": ca.ex_date, "payment_date": ca.payment_date, "amount": ca.cash_amount,
                             "currency": ca.currency} for ca, i in events],
        "watchlist_highlights": [{"watchlist": w, "symbol": i.symbol, "name": i.name,
                                  "change_pct": s.change_pct if s else None, "last_close": s.last_close if s else None,
                                  "trend_class": s.trend_class if s else None} for w, i, s in wl],
        "portfolio_count": n_portfolios,
        "freshness_summary": fresh_counts,
    }
    get_cache().set_json(cache_key, result, 60)
    return result


@router.get("/universes")
def universes(db: Session = Depends(get_db)) -> dict[str, Any]:
    counts = dict(db.execute(select(Instrument.is_synthetic, func.count()).group_by(Instrument.is_synthetic)).all())
    return {"default": resolve_universe(db, None), "real_instruments": counts.get(False, 0),
            "synthetic_instruments": counts.get(True, 0)}


__all__ = ["router", "snapshot_dict"]
