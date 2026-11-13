"""Surge Radar: detect unusual price/volume surges as they happen, auto-watch them, and build a Surge Sheet.

Honesty rules (docs/USER_GUIDE.md section 6):
* A surge is flagged *as it happens* (intraday from 15-minute-delayed quotes, or at the end-of-day run). Nothing
  here predicts a surge before it starts.
* Every money figure in the sheet comes from the user's own position size, the fee schedule, and the empirical
  distribution of what happened after *past* surges on the same data. None of it is a forecast or a promise.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from nse_platform.backtesting.costs import FeeModel
from nse_platform.models import (
    DataQualityIssue,
    FeeSchedule,
    Instrument,
    InstrumentSnapshot,
    Notification,
    OpportunityOutcome,
    OpportunitySignal,
    PriceBar,
    Quote,
    User,
    Watchlist,
    WatchlistItem,
)
from nse_platform.models.enums import LatencyClass, SignalCategory, SignalDirection, SignalStatus
from nse_platform.opportunities.detectors import SIGNAL_CALC_VERSION

SURGE_TYPES = ("PRICE_VOLUME_SURGE", "INTRADAY_SURGE", "PRE_BREAKOUT_SQUEEZE")
WATCHLIST_NAME = "Surge Watch (auto)"
EAT = timezone(timedelta(hours=3))
INTRADAY_MIN_MOVE = 0.08
INTRADAY_MIN_VOLUME_MULTIPLE = 3.0
MAX_PARTICIPATION = 0.10  # assume you can trade at most 10% of a session's volume without moving the price


# --------------------------------------------------------------------------- auto watchlist


def auto_watch(db: Session, since: datetime) -> int:
    """Add instruments with new live surge signals to every user's 'Surge Watch (auto)' watchlist and notify.

    Idempotent: the (watchlist, instrument) unique constraint prevents duplicates; a notification is only
    created when the item is newly added.
    """
    sigs = db.execute(select(OpportunitySignal, Instrument).join(Instrument).where(
        OpportunitySignal.signal_type.in_(SURGE_TYPES), OpportunitySignal.is_backfill.is_(False),
        OpportunitySignal.detected_at >= since)).all()
    if not sigs:
        return 0
    added = 0
    for user in db.scalars(select(User).where(User.is_active.is_(True))):
        wl = db.scalar(select(Watchlist).where(Watchlist.user_id == user.id, Watchlist.name == WATCHLIST_NAME))
        if wl is None:
            wl = Watchlist(user_id=user.id, name=WATCHLIST_NAME,
                           description="Instruments flagged by the Surge Radar. Added automatically; remove freely.")
            db.add(wl)
            db.flush()
        for sg, inst in sigs:
            label = sg.signal_type.replace("_", " ").title()
            res = db.execute(insert(WatchlistItem).values(
                watchlist_id=wl.id, instrument_id=inst.id, price_when_added=sg.reference_price,
                tags=["auto-surge", sg.signal_type.lower()],
                stop_price=(sg.invalidation or {}).get("price"),
                notes=f"Auto-added {sg.bar_ts:%d %b %Y}: {label}. Open the Surge Sheet before acting.",
            ).on_conflict_do_nothing().returning(WatchlistItem.id))
            if res.scalar() is not None:
                added += 1
                db.add(Notification(
                    user_id=user.id, channel="in_app", title=f"Surge Radar: {inst.symbol}",
                    body=f"{label} on {inst.symbol} at {sg.reference_price:g} (signal #{sg.id}). Added to "
                         f"'{WATCHLIST_NAME}'.", link=f"/surges?id={sg.id}", delivered_at=datetime.now(timezone.utc)))
    return added


# --------------------------------------------------------------------------- intraday detection


def intraday_check(db: Session, provider_id: int) -> int:
    """Flag surges during the session from the latest delayed quotes (called after each quote poll)."""
    now = datetime.now(timezone.utc)
    latest = (select(Quote.instrument_id, func.max(Quote.market_ts).label("ts"))
              .where(Quote.provider_id == provider_id, Quote.market_ts >= now - timedelta(hours=2))
              .group_by(Quote.instrument_id).subquery())
    rows = db.execute(select(Quote, InstrumentSnapshot).join(
        latest, (latest.c.instrument_id == Quote.instrument_id) & (latest.c.ts == Quote.market_ts))
        .join(InstrumentSnapshot, (InstrumentSnapshot.instrument_id == Quote.instrument_id)
              & (InstrumentSnapshot.timeframe == "1d"))
        .where(Quote.provider_id == provider_id)).all()
    n = 0
    for q, snap in rows:
        if q.latency_class != LatencyClass.DELAYED.value or not q.price or not snap.last_close:
            continue  # only during the session; after the close the end-of-day detector takes over
        # Compare with the last *completed* session close we hold (not the provider's previousClose field,
        # which was observed to be unreliable after hours).
        ref = snap.last_close if snap.session_date and snap.session_date < q.market_ts.astimezone(EAT).date() \
            else snap.prev_close
        if not ref or not snap.avg_volume_20:
            continue
        move = q.price / ref - 1
        vol_mult = (q.volume or 0) / snap.avg_volume_20
        if move < INTRADAY_MIN_MOVE or vol_mult < INTRADAY_MIN_VOLUME_MULTIPLE:
            continue
        day = q.market_ts.astimezone(EAT).date()
        bar_ts = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
        res = db.execute(insert(OpportunitySignal).values(
            instrument_id=q.instrument_id, category=SignalCategory.MOMENTUM.value, signal_type="INTRADAY_SURGE",
            direction=SignalDirection.BULLISH.value, timeframe="15m", bar_ts=bar_ts, reference_price=q.price,
            evidence=[
                {"label": "Rule", "value": "Intraday surge",
                 "detail": "Price >= 8% above the last close with session volume already >= 3x the 20-day "
                           "average. From 15-minute delayed quotes."},
                {"label": "Move vs last close", "value": round(move, 4), "detail": f"last close {ref:g}"},
                {"label": "Session volume vs 20-day average", "value": round(vol_mult, 2), "detail": ""},
                {"label": "Quote time (delayed)", "value": q.market_ts.isoformat(), "detail": ""},
            ],
            invalidation={"type": "close_below", "price": ref, "condition": "Price falls back below the last close"},
            risk_factors=["Intraday move from delayed quotes: the price may already have moved further",
                          "Surges on the NSE frequently reverse within days - see the Surge Sheet history",
                          "Round-trip transaction costs are roughly 4%"],
            status=SignalStatus.ACTIVE.value, expires_at=bar_ts + timedelta(days=3), is_backfill=False,
            calc_version=SIGNAL_CALC_VERSION,
        ).on_conflict_do_nothing(constraint="uq_opportunity_signals_instrument_id").returning(OpportunitySignal.id))
        if res.scalar() is not None:
            n += 1
    return n


# --------------------------------------------------------------------------- surge sheet


def _fees(db: Session) -> tuple[FeeModel, str]:
    fs = db.scalar(select(FeeSchedule).where(FeeSchedule.is_default.is_(True)))
    return FeeModel.from_components(fs.components if fs else None), (fs.code if fs else "default")


def _pnl(fm: FeeModel, shares: int, buy: float, sell: float) -> dict[str, float]:
    cost_in = shares * buy
    fee_in = fm.total(cost_in)
    proceeds = shares * sell
    fee_out = fm.total(proceeds)
    net = proceeds - fee_out - cost_in - fee_in
    return {"exit_price": round(sell, 4), "net_pnl": round(net, 2), "return_on_cash": round(net / (cost_in + fee_in), 4)}


def build_sheet(db: Session, sig: OpportunitySignal, inst: Instrument, shares: int | None,
                budget: float | None) -> dict[str, Any]:
    fm, fee_code = _fees(db)
    snap = db.scalar(select(InstrumentSnapshot).where(InstrumentSnapshot.instrument_id == inst.id,
                                                      InstrumentSnapshot.timeframe == "1d"))
    current = snap.last_close if snap and snap.last_close else sig.reference_price
    entry = current
    lot = inst.lot_size or 1
    if shares is None:
        budget = budget or 100_000.0
        shares = max(lot, int(budget / (entry * (1 + fm.rate_estimate())) // lot * lot))
    shares = max(1, int(shares))

    # --- costs & break-even
    buy_cost = shares * entry
    fee_in = fm.breakdown(buy_cost)
    breakeven = entry * (1 + fm.rate_estimate()) / (1 - fm.rate_estimate())

    # --- liquidity: can this size actually be traded?
    adv = snap.avg_volume_20 if snap else None
    max_per_day = int(adv * MAX_PARTICIPATION) if adv else None
    days_to_fill = math.ceil(shares / max_per_day) if max_per_day else None
    liquidity_flag = None
    if max_per_day is not None and shares > max_per_day:
        liquidity_flag = (f"Your order is {shares / adv:.0%} of average daily volume. At <= 10% of daily volume it "
                          f"would take ~{days_to_fill} sessions to buy and as long to sell - prices may move "
                          f"against you meanwhile.")

    # --- historical analogues: what happened after past signals of this type (same data universe)
    analog_type = "PRICE_VOLUME_SURGE" if sig.signal_type == "INTRADAY_SURGE" else sig.signal_type
    rows = db.execute(select(OpportunityOutcome.horizon_bars, OpportunityOutcome.forward_return,
                             OpportunityOutcome.max_adverse, OpportunitySignal.instrument_id)
                      .join(OpportunitySignal).join(Instrument, Instrument.id == OpportunitySignal.instrument_id)
                      .where(OpportunitySignal.signal_type == analog_type, OpportunitySignal.timeframe == "1d",
                             Instrument.is_synthetic.is_(inst.is_synthetic))).all()
    scenarios = []
    for h in (1, 5, 10, 20):
        r = np.array([x[1] for x in rows if x[0] == h])
        own = np.array([x[1] for x in rows if x[0] == h and x[3] == inst.id])
        if r.size == 0:
            continue
        pct = {k: float(np.percentile(r, q)) for k, q in (("bad_p10", 10), ("weak_p25", 25), ("typical_median", 50),
                                                           ("good_p75", 75), ("great_p90", 90))}
        scenarios.append({
            "horizon_sessions": h, "n": int(r.size), "this_stock_n": int(own.size),
            "share_profitable_after_costs": float((r > (breakeven / entry - 1)).mean()),
            "share_positive": float((r > 0).mean()),
            "returns": pct,
            "pnl": {k: _pnl(fm, shares, entry, entry * (1 + v)) for k, v in pct.items()},
            "this_stock_median": float(np.median(own)) if own.size else None,
        })

    # --- "if the trend continues" - explicitly hypothetical, with how often it actually happened
    bars = db.execute(select(PriceBar.session_date, PriceBar.close, PriceBar.high, PriceBar.volume,
                             PriceBar.quality_status)
                      .where(PriceBar.instrument_id == inst.id, PriceBar.timeframe == "1d",
                             PriceBar.provider_id == inst.primary_provider_id)
                      .order_by(PriceBar.ts.desc()).limit(6)).all()
    continuation = None
    if len(bars) >= 2 and bars[-1][1]:
        k = len(bars) - 1
        avg_daily = (bars[0][1] / bars[-1][1]) ** (1 / k) - 1
        five = [x[1] for x in rows if x[0] == 5]
        if avg_daily > 0:
            hyp = min((1 + avg_daily) ** 5, 1.10 ** 5) - 1  # NSE daily price limits cap continuation (~10%/day)
            hit = float(np.mean(np.array(five) >= hyp)) if five else None
            continuation = {
                "assumption": f"The average daily gain of the last {k} sessions ({avg_daily:+.1%}/day) continues for "
                              "5 more sessions (capped at 10%/day).",
                "return": round(hyp, 4), "pnl": _pnl(fm, shares, entry, entry * (1 + hyp)),
                "historical_frequency": hit, "historical_n": len(five),
                "warning": "Hypothetical. Trends rarely continue at surge pace - see how often it happened.",
            }

    # --- data quality around the surge
    dq = []
    for d, close, high, vol, quality in bars[:3]:
        if quality != "OK":
            dq.append(f"{d}: bar flagged {quality} by validation")
        if high and close and high > 2.5 * close:
            dq.append(f"{d}: reported high {high:g} is {high / close:.1f}x the close - likely a bad print")
    open_issues = db.scalar(select(func.count()).select_from(DataQualityIssue).where(
        DataQualityIssue.instrument_id == inst.id, DataQualityIssue.resolved_at.is_(None),
        DataQualityIssue.severity != "info"))
    if open_issues:
        dq.append(f"{open_issues} open data-quality issue(s) for this instrument (Data Centre)")

    stop = (sig.invalidation or {}).get("price")
    stop_loss = _pnl(fm, shares, entry, stop) if stop and stop < entry else None
    chase = (current / sig.reference_price - 1) if sig.reference_price else None
    checklist = [
        "The data is 15-minute delayed: confirm the live price with your broker before any order.",
        f"Break-even needs a {breakeven / entry - 1:.1%} rise just to cover round-trip costs.",
        "Decide your exit before entering: the stop below and a take-profit near the 'good' scenario.",
        "Check News & Events for the cause (results, takeover, rumour). Moves with no news reverse more often.",
        "Size so that hitting the stop loses at most 1-2% of your total capital.",
    ]
    if chase is not None and chase > 0.05:
        checklist.insert(0, f"Price is already {chase:+.1%} above the signal price - you would be chasing.")
    return {
        "signal": {"id": sig.id, "type": sig.signal_type, "timeframe": sig.timeframe, "bar_ts": sig.bar_ts,
                   "reference_price": sig.reference_price, "status": sig.status, "evidence": sig.evidence},
        "instrument": {"symbol": inst.symbol, "name": inst.name, "is_synthetic": inst.is_synthetic,
                       "lot_size": lot, "trading_status": inst.trading_status},
        "position": {"shares": shares, "entry_price": entry, "consideration": round(buy_cost, 2),
                     "buy_fees": fee_in, "total_cash_needed": round(buy_cost + fee_in["total"], 2),
                     "breakeven_price": round(breakeven, 4), "breakeven_move": round(breakeven / entry - 1, 4),
                     "fee_schedule": fee_code, "entry_basis": "latest close in the database (delayed/EOD)"},
        "stop": {"price": stop, "condition": (sig.invalidation or {}).get("condition"), "loss_if_hit": stop_loss},
        "liquidity": {"avg_daily_volume_20": adv, "avg_traded_value_20": snap.avg_traded_value_20 if snap else None,
                      "max_shares_per_session_at_10pct": max_per_day, "sessions_to_fill": days_to_fill,
                      "trading_frequency_60": snap.trading_frequency_60 if snap else None, "warning": liquidity_flag},
        "scenarios": scenarios,
        "analogue_signal_type": analog_type,
        "trend_continuation": continuation,
        "data_quality": dq,
        "checklist": checklist,
        "risk_score": snap.risk_score if snap else None,
        "disclaimer": "Scenario values are what happened after past signals of this type in this data universe, "
                      "applied to your share count after fees. They are not forecasts. Surges often reverse; you "
                      "can lose more than the stop if the price gaps down.",
    }
