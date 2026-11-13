"""User-owned resources: portfolios, watchlists, alerts, notifications. Every query filters by user_id."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from nse_platform.api.deps import current_user, get_db, not_found
from nse_platform.api.v1.instruments import get_instrument
from nse_platform.auth.service import audit
from nse_platform.marketdata.queries import instrument_ref, price_view, sector_names
from nse_platform.models import (
    AlertEvent,
    AlertRule,
    FeeSchedule,
    Instrument,
    InstrumentSnapshot,
    Notification,
    Portfolio,
    PortfolioTransaction,
    User,
    Watchlist,
    WatchlistItem,
)
from nse_platform.models.enums import AlertRuleType
from nse_platform.portfolio import service as psvc

router = APIRouter(tags=["user"])


def _conflict(msg: str) -> HTTPException:
    return HTTPException(409, detail={"code": "CONFLICT", "message": msg})


def _bad(msg: str) -> HTTPException:
    return HTTPException(400, detail={"code": "INVALID", "message": msg})


# --------------------------------------------------------------------------- portfolios


class PortfolioIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    kind: Literal["PAPER", "TRACKED"] = "PAPER"
    initial_cash: float | None = Field(None, ge=0)
    fee_schedule_code: str | None = None
    dividend_tax_rate: float = Field(0.05, ge=0, lt=1)


class TransactionIn(BaseModel):
    portfolio_id: int
    txn_type: Literal["BUY", "SELL", "DIVIDEND", "DEPOSIT", "WITHDRAWAL", "FEE"]
    trade_date: date
    symbol: str | None = None
    quantity: float | None = Field(None, gt=0)
    price: float | None = Field(None, gt=0)
    amount: float | None = Field(None, gt=0)
    fees: float | None = Field(None, ge=0, description="Omit to apply the portfolio fee schedule")
    notes: str | None = Field(None, max_length=2000)

    @field_validator("trade_date")
    @classmethod
    def _not_future(cls, v: date) -> date:
        if v > date.today():
            raise ValueError("trade_date cannot be in the future")
        return v


def _portfolio(db: Session, user: User, pid: int) -> Portfolio:
    p = db.scalar(select(Portfolio).where(Portfolio.id == pid, Portfolio.user_id == user.id))
    if p is None:
        raise not_found("Portfolio")
    return p


@router.get("/portfolio")
def portfolios(db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    items = [psvc.summary(db, p) for p in db.scalars(select(Portfolio).where(Portfolio.user_id == user.id)
                                                      .order_by(Portfolio.created_at))]
    return {"items": items, "disclaimer": "Simulated research portfolios. No orders are sent to any broker."}


@router.post("/portfolio")
def create_portfolio(body: PortfolioIn, db: Session = Depends(get_db),
                     user: User = Depends(current_user)) -> dict[str, Any]:
    fs = None
    if body.fee_schedule_code:
        fs = db.scalar(select(FeeSchedule).where(FeeSchedule.code == body.fee_schedule_code))
        if fs is None:
            raise _bad("Unknown fee schedule")
    p = Portfolio(user_id=user.id, name=body.name, kind=body.kind, fee_schedule_id=fs.id if fs else None,
                  dividend_tax_rate=body.dividend_tax_rate)
    db.add(p)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise _conflict("A portfolio with this name already exists") from exc
    if body.initial_cash:
        psvc.record(db, p, txn_type="DEPOSIT", trade_date=date.today(), instrument=None, quantity=None, price=None,
                    amount=body.initial_cash, fees=None, notes="Initial cash")
    audit(db, "portfolio.create", user.id, entity="portfolio", entity_id=str(p.id))
    return psvc.summary(db, p)


@router.get("/portfolio/{pid}")
def portfolio_detail(pid: int, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    p = _portfolio(db, user, pid)
    txns = db.execute(select(PortfolioTransaction, Instrument.symbol).outerjoin(
        Instrument, Instrument.id == PortfolioTransaction.instrument_id).where(PortfolioTransaction.portfolio_id == p.id)
        .order_by(PortfolioTransaction.trade_date.desc(), PortfolioTransaction.id.desc())).all()
    return {**psvc.summary(db, p), "transactions": [
        {"id": t.id, "txn_type": t.txn_type, "trade_date": t.trade_date, "symbol": sym, "quantity": t.quantity,
         "price": t.price, "gross_amount": t.gross_amount, "fees": t.fees, "fee_breakdown": t.fee_breakdown,
         "tax": t.tax, "net_cash_flow": t.net_cash_flow, "notes": t.notes} for t, sym in txns]}


@router.get("/portfolio/{pid}/performance")
def portfolio_performance(pid: int, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    return psvc.performance(db, _portfolio(db, user, pid))


@router.delete("/portfolio/{pid}")
def delete_portfolio(pid: int, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    p = _portfolio(db, user, pid)
    db.delete(p)
    audit(db, "portfolio.delete", user.id, entity="portfolio", entity_id=str(pid))
    return {"ok": True}


@router.post("/portfolio/transactions")
def add_transaction(body: TransactionIn, db: Session = Depends(get_db),
                    user: User = Depends(current_user)) -> dict[str, Any]:
    p = _portfolio(db, user, body.portfolio_id)
    inst = get_instrument(db, body.symbol) if body.symbol else None
    if inst is not None and inst.security_type == "BOND" and body.txn_type in ("BUY", "SELL"):
        raise _bad("Bond trading is not simulated: the fee model and pricing cover listed equities/ETFs/REITs only")
    try:
        t = psvc.record(db, p, txn_type=body.txn_type, trade_date=body.trade_date, instrument=inst,
                        quantity=body.quantity, price=body.price, amount=body.amount, fees=body.fees, notes=body.notes)
    except psvc.PortfolioError as exc:
        raise _bad(str(exc)) from exc
    audit(db, "portfolio.transaction", user.id, entity="portfolio", entity_id=str(p.id), txn_id=t.id,
          txn_type=t.txn_type)
    return {"id": t.id, "fees": t.fees, "fee_breakdown": t.fee_breakdown, "tax": t.tax,
            "net_cash_flow": t.net_cash_flow, "portfolio": psvc.summary(db, p)}


class OrderIn(BaseModel):
    """Paper market order: fills at the latest available price (delayed/end-of-day), like a broker ticket."""

    symbol: str = Field(min_length=1, max_length=32)
    side: Literal["BUY", "SELL"]
    quantity: int | None = Field(None, gt=0, le=100_000_000)
    amount: float | None = Field(None, gt=0, le=1e11, description="BUY only: KES to spend incl. fees")
    sell_all: bool = False


def _plan_order(db: Session, p: Portfolio, body: OrderIn) -> dict[str, Any]:
    """Resolve price/quantity/fees and check cash, holdings, liquidity and data freshness. Never writes."""
    inst = get_instrument(db, body.symbol)
    if inst.security_type == "BOND":
        raise _bad("Bond trading is not simulated (pricing and fees cover equities, ETFs and REITs)")
    pv = price_view(db, [inst])[inst.id]
    px, fr = pv["price"], pv["freshness"]
    if px is None or fr["status"] == "UNAVAILABLE":
        raise _bad(f"No price available for {inst.symbol}; cannot simulate a market order")
    if inst.trading_status == "SUSPENDED":
        raise _bad(f"{inst.symbol} is suspended: orders cannot be filled")
    fm = psvc.fee_model(db, p)
    lot = inst.lot_size or 1
    today = date.today()
    txns = list(db.scalars(select(PortfolioTransaction).where(PortfolioTransaction.portfolio_id == p.id)))
    cash, hold, _ = psvc._replay(db, txns, today)
    held = hold[inst.id].qty if inst.id in hold else 0.0
    if body.side == "SELL" and body.sell_all:
        qty = int(held)
    elif body.quantity:
        qty = int(body.quantity)
    elif body.amount and body.side == "BUY":
        qty = int(body.amount / (px * (1 + fm.rate_estimate())) // lot * lot)
    else:
        raise _bad("Enter a quantity" + (" or an amount" if body.side == "BUY" else " (or sell all)"))
    if qty <= 0:
        raise _bad("The amount is too small to buy a single share (or lot) after fees")
    gross = qty * px
    fees = fm.breakdown(gross)
    net = -(gross + fees["total"]) if body.side == "BUY" else gross - fees["total"]
    warnings, errors = [], []
    if body.side == "BUY" and p.kind == "PAPER" and cash + net < -1e-6:
        errors.append(f"Insufficient cash: available KES {cash:,.2f}, required KES {-net:,.2f}")
    if body.side == "SELL" and qty > held + 1e-9:
        errors.append(f"You hold {held:g} shares of {inst.symbol}; cannot sell {qty}")
    snap = db.scalar(select(InstrumentSnapshot).where(InstrumentSnapshot.instrument_id == inst.id,
                                                      InstrumentSnapshot.timeframe == "1d"))
    if snap and snap.avg_volume_20 and qty > 0.1 * snap.avg_volume_20:
        warnings.append(f"Order is {qty / snap.avg_volume_20:.0%} of average daily volume - a real order this size "
                        "would likely take several sessions and move the price")
    if fr["status"] in ("STALE", "HISTORICAL"):
        warnings.append(f"Price is {fr['status'].lower()} ({fr.get('reason') or 'see Data Centre'}): the simulated "
                        "fill may differ a lot from what a real order would get")
    warnings.append("Paper fill at the latest available price (15-min delayed or last close) - no real order is sent")
    return {"instrument": instrument_ref(inst), "side": body.side, "quantity": qty, "price": px,
            "price_freshness": fr, "consideration": round(gross, 2), "fees": fees, "net_cash_flow": round(net, 2),
            "cash_before": round(cash, 2), "cash_after": round(cash + net, 2), "held_before": held,
            "held_after": held + qty if body.side == "BUY" else held - qty, "warnings": warnings, "errors": errors,
            "can_execute": not errors}


@router.post("/portfolio/{pid}/orders/preview")
def preview_order(pid: int, body: OrderIn, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    return _plan_order(db, _portfolio(db, user, pid), body)


@router.post("/portfolio/{pid}/orders")
def place_order(pid: int, body: OrderIn, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    p = _portfolio(db, user, pid)
    plan = _plan_order(db, p, body)
    if not plan["can_execute"]:
        raise _bad("; ".join(plan["errors"]))
    inst = get_instrument(db, body.symbol)
    fr = plan["price_freshness"]
    try:
        t = psvc.record(db, p, txn_type=body.side, trade_date=date.today(), instrument=inst, quantity=plan["quantity"],
                        price=plan["price"], amount=None, fees=None,
                        notes=f"Paper market order at {fr['status'].lower().replace('_', ' ')} price "
                              f"({fr.get('provider')}, observed {fr.get('as_of')})")
    except psvc.PortfolioError as exc:
        raise _bad(str(exc)) from exc
    audit(db, "portfolio.order", user.id, entity="portfolio", entity_id=str(p.id), txn_id=t.id, side=body.side,
          symbol=inst.symbol, quantity=plan["quantity"])
    return {"filled": {**plan, "transaction_id": t.id}, "portfolio": psvc.summary(db, p)}


@router.delete("/portfolio/transactions/{txn_id}")
def delete_transaction(txn_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    t = db.scalar(select(PortfolioTransaction).join(Portfolio).where(
        PortfolioTransaction.id == txn_id, Portfolio.user_id == user.id))
    if t is None:
        raise not_found("Transaction")
    p = db.get(Portfolio, t.portfolio_id)
    db.delete(t)
    db.flush()
    # Deleting a transaction must not leave the paper portfolio in an impossible state.
    assert p is not None
    txns = list(db.scalars(select(PortfolioTransaction).where(PortfolioTransaction.portfolio_id == p.id)))
    running_ok = True
    for d in sorted({x.trade_date for x in txns}):
        cash, hold, _ = psvc._replay(db, txns, d)
        if (p.kind == "PAPER" and cash < -1e-6) or any(h.qty < -1e-9 for h in hold.values()):
            running_ok = False
            break
    if not running_ok:
        db.rollback()
        raise _bad("Deleting this transaction would make later transactions invalid (negative cash or holdings)")
    return {"ok": True}


class FeePreviewIn(BaseModel):
    consideration: float = Field(gt=0)
    fee_schedule_code: str | None = None


@router.post("/portfolio/fee-preview")
def fee_preview(body: FeePreviewIn, db: Session = Depends(get_db), _: User = Depends(current_user)) -> dict:
    from nse_platform.backtesting.costs import FeeModel

    fs = db.scalar(select(FeeSchedule).where(FeeSchedule.code == body.fee_schedule_code) if body.fee_schedule_code
                   else select(FeeSchedule).where(FeeSchedule.is_default.is_(True)))
    fm = FeeModel.from_components(fs.components if fs else None)
    return {"schedule": fs.code if fs else "default", "breakdown": fm.breakdown(body.consideration),
            "source_notes": fs.source_notes if fs else None}


@router.get("/fee-schedules")
def fee_schedules(db: Session = Depends(get_db), _: User = Depends(current_user)) -> list[dict[str, Any]]:
    return [{"code": f.code, "name": f.name, "components": f.components, "effective_from": f.effective_from,
             "source_notes": f.source_notes, "is_default": f.is_default} for f in db.scalars(select(FeeSchedule))]


# --------------------------------------------------------------------------- watchlists


class WatchlistIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(None, max_length=2000)


class WatchItemIn(BaseModel):
    symbol: str
    notes: str | None = Field(None, max_length=4000)
    tags: list[str] = Field(default_factory=list, max_length=20)
    target_price: float | None = Field(None, gt=0)
    stop_price: float | None = Field(None, gt=0)
    entry_price: float | None = Field(None, gt=0)


class WatchItemPatch(BaseModel):
    notes: str | None = Field(None, max_length=4000)
    tags: list[str] | None = Field(None, max_length=20)
    target_price: float | None = Field(None, gt=0)
    stop_price: float | None = Field(None, gt=0)
    entry_price: float | None = Field(None, gt=0)


def _watchlist(db: Session, user: User, wid: int) -> Watchlist:
    w = db.scalar(select(Watchlist).where(Watchlist.id == wid, Watchlist.user_id == user.id))
    if w is None:
        raise not_found("Watchlist")
    return w


def _watchlist_view(db: Session, w: Watchlist) -> dict[str, Any]:
    rows = db.execute(select(WatchlistItem, Instrument).join(Instrument).where(WatchlistItem.watchlist_id == w.id)
                      .order_by(Instrument.symbol)).all()
    insts = [i for _, i in rows]
    pv = price_view(db, insts)
    snaps = {s.instrument_id: s for s in db.scalars(select(InstrumentSnapshot).where(
        InstrumentSnapshot.instrument_id.in_([i.id for i in insts]), InstrumentSnapshot.timeframe == "1d"))}
    sectors = sector_names(db)
    items = []
    for it, inst in rows:
        px = pv[inst.id]["price"]
        s = snaps.get(inst.id)
        items.append({
            "id": it.id, "instrument": instrument_ref(inst, sectors), "notes": it.notes, "tags": it.tags or [],
            "target_price": it.target_price, "stop_price": it.stop_price, "entry_price": it.entry_price,
            "added_at": it.added_at, "price_when_added": it.price_when_added, "price": px,
            "freshness": pv[inst.id]["freshness"],
            "change_since_added": px / it.price_when_added - 1 if px and it.price_when_added else None,
            "distance_to_target": px / it.target_price - 1 if px and it.target_price else None,
            "distance_to_stop": px / it.stop_price - 1 if px and it.stop_price else None,
            "change_pct": s.change_pct if s else None, "trend_class": s.trend_class if s else None,
            "trading_score": s.trading_score if s else None, "risk_score": s.risk_score if s else None,
        })
    perf = [x["change_since_added"] for x in items if x["change_since_added"] is not None]
    return {"id": w.id, "name": w.name, "description": w.description, "items": items,
            "performance": {"average_change_since_added": sum(perf) / len(perf) if perf else None,
                            "count_priced": len(perf)}}


@router.get("/watchlists")
def watchlists(db: Session = Depends(get_db), user: User = Depends(current_user)) -> list[dict[str, Any]]:
    return [_watchlist_view(db, w) for w in db.scalars(select(Watchlist).where(Watchlist.user_id == user.id)
                                                       .order_by(Watchlist.name))]


@router.post("/watchlists")
def create_watchlist(body: WatchlistIn, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    w = Watchlist(user_id=user.id, name=body.name, description=body.description)
    db.add(w)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise _conflict("A watchlist with this name already exists") from exc
    return _watchlist_view(db, w)


@router.get("/watchlists/{wid}")
def get_watchlist(wid: int, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    return _watchlist_view(db, _watchlist(db, user, wid))


@router.delete("/watchlists/{wid}")
def delete_watchlist(wid: int, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    db.delete(_watchlist(db, user, wid))
    return {"ok": True}


@router.post("/watchlists/{wid}/items")
def add_item(wid: int, body: WatchItemIn, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    w = _watchlist(db, user, wid)
    inst = get_instrument(db, body.symbol)
    sym = inst.symbol
    px = price_view(db, [inst])[inst.id]["price"]
    db.add(WatchlistItem(watchlist_id=w.id, instrument_id=inst.id, notes=body.notes,
                         tags=[t.strip()[:32] for t in body.tags if t.strip()], target_price=body.target_price,
                         stop_price=body.stop_price, entry_price=body.entry_price, price_when_added=px))
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise _conflict(f"{sym} is already in this watchlist") from exc
    return _watchlist_view(db, w)


@router.patch("/watchlists/{wid}/items/{item_id}")
def update_item(wid: int, item_id: int, body: WatchItemPatch, db: Session = Depends(get_db),
                user: User = Depends(current_user)) -> dict:
    w = _watchlist(db, user, wid)
    it = db.scalar(select(WatchlistItem).where(WatchlistItem.id == item_id, WatchlistItem.watchlist_id == w.id))
    if it is None:
        raise not_found("Watchlist item")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(it, k, v)
    return _watchlist_view(db, w)


@router.delete("/watchlists/{wid}/items/{item_id}")
def remove_item(wid: int, item_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    w = _watchlist(db, user, wid)
    it = db.scalar(select(WatchlistItem).where(WatchlistItem.id == item_id, WatchlistItem.watchlist_id == w.id))
    if it is None:
        raise not_found("Watchlist item")
    db.delete(it)
    db.flush()
    return _watchlist_view(db, w)


# --------------------------------------------------------------------------- alerts & notifications

RULE_PARAMS: dict[str, list[str]] = {
    "PRICE_ABOVE": ["price"], "PRICE_BELOW": ["price"], "RSI_BELOW": ["level"], "RSI_ABOVE": ["level"],
    "SMA_CROSS_UP": [], "SMA_CROSS_DOWN": [], "VOLUME_SPIKE": ["multiple"], "SIGNAL_DETECTED": [],
    "DIVIDEND_ANNOUNCED": [], "ANNOUNCEMENT": [], "RISK_SCORE_ABOVE": ["level"], "FUNDAMENTAL_CHANGE": ["metric",
                                                                                                       "change"],
}
INSTRUMENT_OPTIONAL = {"SIGNAL_DETECTED", "ANNOUNCEMENT", "DIVIDEND_ANNOUNCED"}


class AlertIn(BaseModel):
    rule_type: AlertRuleType
    symbol: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    cooldown_minutes: int = Field(60 * 24, ge=5, le=60 * 24 * 30)
    is_active: bool = True


def _alert_out(r: AlertRule, sym: str | None) -> dict[str, Any]:
    return {"id": r.id, "rule_type": r.rule_type, "symbol": sym, "params": r.params,
            "cooldown_minutes": r.cooldown_minutes, "is_active": r.is_active, "channels": r.channels,
            "last_triggered_at": r.last_triggered_at, "created_at": r.created_at}


@router.get("/alerts")
def alerts(db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    rows = db.execute(select(AlertRule, Instrument.symbol).outerjoin(Instrument).where(AlertRule.user_id == user.id)
                      .order_by(AlertRule.created_at.desc())).all()
    events = db.execute(select(AlertEvent, AlertRule.rule_type).join(AlertRule).where(AlertRule.user_id == user.id)
                        .order_by(AlertEvent.triggered_at.desc()).limit(50)).all()
    return {"rules": [_alert_out(r, s) for r, s in rows],
            "recent_events": [{"id": e.id, "rule_id": e.rule_id, "rule_type": t, "triggered_at": e.triggered_at,
                               "message": e.message} for e, t in events],
            "channels": {"in_app": True, "email": False},
            "note": "Alerts are evaluated after each analytics run (end-of-day/delayed data cadence)."}


@router.post("/alerts")
def create_alert(body: AlertIn, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    rt = body.rule_type.value
    missing = [p for p in RULE_PARAMS[rt] if p not in body.params]
    if missing:
        raise _bad(f"{rt} requires params: {missing}")
    for p in RULE_PARAMS[rt]:
        if p != "metric" and not isinstance(body.params[p], (int, float)):
            raise _bad(f"param {p!r} must be numeric")
    if body.symbol is None and rt not in INSTRUMENT_OPTIONAL:
        raise _bad(f"{rt} requires a symbol")
    inst = get_instrument(db, body.symbol) if body.symbol else None
    n = db.scalar(select(func.count()).select_from(AlertRule).where(AlertRule.user_id == user.id)) or 0
    if n >= 200:
        raise _bad("Alert limit reached (200)")
    r = AlertRule(user_id=user.id, instrument_id=inst.id if inst else None, rule_type=rt, params=body.params,
                  cooldown_minutes=body.cooldown_minutes, is_active=body.is_active, channels=["in_app"])
    db.add(r)
    db.flush()
    return _alert_out(r, inst.symbol if inst else None)


@router.patch("/alerts/{rule_id}")
def toggle_alert(rule_id: int, is_active: bool, db: Session = Depends(get_db),
                 user: User = Depends(current_user)) -> dict:
    r = db.scalar(select(AlertRule).where(AlertRule.id == rule_id, AlertRule.user_id == user.id))
    if r is None:
        raise not_found("Alert")
    r.is_active = is_active
    return {"ok": True, "is_active": r.is_active}


@router.delete("/alerts/{rule_id}")
def delete_alert(rule_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    r = db.scalar(select(AlertRule).where(AlertRule.id == rule_id, AlertRule.user_id == user.id))
    if r is None:
        raise not_found("Alert")
    db.delete(r)
    return {"ok": True}


@router.get("/notifications")
def notifications(unread_only: bool = False, limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_db),
                  user: User = Depends(current_user)) -> dict[str, Any]:
    q = select(Notification).where(Notification.user_id == user.id)
    if unread_only:
        q = q.where(Notification.read_at.is_(None))
    items = db.scalars(q.order_by(Notification.created_at.desc()).limit(limit)).all()
    unread = db.scalar(select(func.count()).select_from(Notification).where(
        Notification.user_id == user.id, Notification.read_at.is_(None)))
    return {"unread": unread, "items": [{"id": n.id, "title": n.title, "body": n.body, "link": n.link,
                                         "created_at": n.created_at, "read_at": n.read_at} for n in items]}


@router.post("/notifications/read")
def mark_read(ids: list[int] | None = None, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    q = select(Notification).where(Notification.user_id == user.id, Notification.read_at.is_(None))
    if ids:
        q = q.where(Notification.id.in_(ids))
    now = datetime.now(timezone.utc)
    n = 0
    for note in db.scalars(q):
        note.read_at = now
        n += 1
    return {"marked": n}
