"""Read-side helpers shared by API routers: universes, latest observations, freshness, instrument summaries."""

from __future__ import annotations

from datetime import datetime, time, timezone
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from nse_platform.marketdata.freshness import EAT, classify
from nse_platform.models import (
    DataProvider,
    DataSourceHealth,
    Instrument,
    InstrumentSnapshot,
    PriceBar,
    Quote,
    Sector,
)


def resolve_universe(db: Session, requested: str | None) -> str:
    """'real' or 'synthetic'. Default: real if any real priced instrument has bars, else synthetic."""
    if requested in ("real", "synthetic"):
        return requested
    has_real = db.scalar(select(func.count()).select_from(InstrumentSnapshot).join(
        Instrument, Instrument.id == InstrumentSnapshot.instrument_id).where(Instrument.is_synthetic.is_(False)))
    return "real" if has_real else ("synthetic" if db.scalar(select(func.count()).select_from(Instrument).where(
        Instrument.is_synthetic.is_(True))) else "real")


def providers_by_id(db: Session) -> dict[int, DataProvider]:
    return {p.id: p for p in db.scalars(select(DataProvider))}


def health_by_provider(db: Session) -> dict[int, DataSourceHealth]:
    return {h.provider_id: h for h in db.scalars(select(DataSourceHealth))}


def latest_quotes(db: Session, ids: list[int]) -> dict[int, Quote]:
    if not ids:
        return {}
    sub = (select(Quote.instrument_id, func.max(Quote.market_ts).label("mts"))
           .where(Quote.instrument_id.in_(ids), Quote.quality_status != "REJECTED")
           .group_by(Quote.instrument_id).subquery())
    rows = db.scalars(select(Quote).join(sub, and_(Quote.instrument_id == sub.c.instrument_id,
                                                   Quote.market_ts == sub.c.mts))).all()
    return {q.instrument_id: q for q in rows}


def latest_bars(db: Session, insts: list[Instrument]) -> dict[int, PriceBar]:
    out = {}
    pairs = [(i.id, i.primary_provider_id) for i in insts if i.primary_provider_id]
    if not pairs:
        return out
    sub = (select(PriceBar.instrument_id, PriceBar.provider_id, func.max(PriceBar.ts).label("mts"))
           .where(PriceBar.timeframe == "1d", PriceBar.instrument_id.in_([p[0] for p in pairs]))
           .group_by(PriceBar.instrument_id, PriceBar.provider_id).subquery())
    rows = db.scalars(select(PriceBar).join(sub, and_(
        PriceBar.instrument_id == sub.c.instrument_id, PriceBar.provider_id == sub.c.provider_id,
        PriceBar.ts == sub.c.mts, PriceBar.timeframe == "1d"))).all()
    want = dict(pairs)
    for b in rows:
        if want.get(b.instrument_id) == b.provider_id:
            out[b.instrument_id] = b
    return out


def last_traded_dates(db: Session, insts: list[Instrument]) -> dict[int, Any]:
    if not insts:
        return {}
    rows = db.execute(select(PriceBar.instrument_id, func.max(PriceBar.session_date)).where(
        PriceBar.instrument_id.in_([i.id for i in insts]), PriceBar.timeframe == "1d", PriceBar.volume > 0)
        .group_by(PriceBar.instrument_id)).all()
    return dict(rows)


def instrument_ref(i: Instrument, sectors: dict[int, str] | None = None) -> dict[str, Any]:
    return {
        "id": i.id, "symbol": i.symbol, "name": i.name, "security_type": i.security_type,
        "sector": (sectors or {}).get(i.sector_id) if sectors is not None else (i.sector.name if i.sector else None),
        "currency": i.currency, "trading_status": i.trading_status, "is_synthetic": i.is_synthetic,
        "isin": i.isin, "lot_size": i.lot_size,
    }


def sector_names(db: Session) -> dict[int, str]:
    return {s.id: s.name for s in db.scalars(select(Sector))}


def price_view(db: Session, insts: list[Instrument], now: datetime | None = None) -> dict[int, dict[str, Any]]:
    """Latest price for each instrument with provenance and freshness. Prefers the newest observation between
    the latest quote and the latest daily bar; never fabricates a price when neither exists."""
    provs = providers_by_id(db)
    health = health_by_provider(db)
    quotes = latest_quotes(db, [i.id for i in insts])
    bars = latest_bars(db, insts)
    traded = last_traded_dates(db, insts)
    out: dict[int, dict[str, Any]] = {}
    for i in insts:
        q, b = quotes.get(i.id), bars.get(i.id)
        bar_close_ts = (datetime.combine(b.session_date, time(15, 0), EAT).astimezone(timezone.utc)
                        if b is not None and b.session_date else None)
        use_quote = q is not None and q.price is not None and (bar_close_ts is None or q.market_ts >= bar_close_ts)
        if use_quote:
            assert q is not None
            prov = provs.get(q.provider_id)
            price, prev = q.price, q.previous_close
            observed, session_day = q.market_ts, q.market_ts.astimezone(EAT).date()
            latency, delay, volume = q.latency_class, q.delay_minutes, q.volume
            source = "quote"
        elif b is not None:
            prov = provs.get(b.provider_id)
            price, prev, volume = b.close, None, b.volume
            observed, session_day = bar_close_ts, b.session_date
            latency, delay = (prov.latency_class if prov else None), (prov.delay_minutes if prov else None)
            if latency == "DELAYED":
                latency = "END_OF_DAY"  # a daily bar from a delayed feed is an end-of-day observation
            source = "daily_bar"
        else:
            prov, price, prev, observed, session_day, latency, delay, volume, source = (
                provs.get(i.primary_provider_id) if i.primary_provider_id else None, None, None, None, None, None,
                None, None, None)
        h = health.get(prov.id) if prov else None
        fr = classify(db, observed_at=observed, observation_session=session_day, latency_class=latency,
                      provider_code=prov.code if prov else None, provider_health=h.status if h else None,
                      trading_status=i.trading_status, last_bar_volume=b.volume if b else None,
                      delay_minutes=delay, now=now)
        out[i.id] = {
            "price": price, "previous_close": prev, "volume": volume, "source": source,
            "provider": prov.code if prov else None, "is_synthetic": bool(prov.is_synthetic) if prov else i.is_synthetic,
            "attribution": prov.attribution if prov else None,
            "last_traded_date": traded.get(i.id).isoformat() if traded.get(i.id) else None,
            "freshness": fr.as_dict(),
        }
    return out


def snapshot_dict(s: InstrumentSnapshot | None) -> dict[str, Any] | None:
    if s is None:
        return None
    keys = ("as_of_ts", "session_date", "bars_available", "last_close", "prev_close", "change_pct", "volume",
            "avg_volume_20", "relative_volume", "avg_traded_value_20", "trading_frequency_60", "rsi_14", "return_1w",
            "return_1m", "return_3m", "return_6m", "return_1y", "return_ytd", "volatility_20", "volatility_60",
            "atr_pct_14", "max_drawdown_1y", "drawdown_from_high", "high_52w", "low_52w", "relative_strength_3m",
            "dividend_yield", "pe_ratio", "trend_class", "trading_score", "quality_score", "risk_score",
            "calc_version")
    return {k: getattr(s, k) for k in keys}
