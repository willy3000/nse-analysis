"""Paper / tracked portfolio accounting. Positions are derived from transactions (never stored separately).

Cost basis: average cost (fees capitalised into cost on buys; deducted from proceeds on sells).
Bonus/split events between transactions rescale held quantity and average cost automatically.
Performance: daily time-weighted return (external cash flows = deposits/withdrawals neutralised).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from nse_platform.analytics.series import adjustment_events, apply_adjustments, load_daily
from nse_platform.backtesting.costs import FeeModel
from nse_platform.marketdata.queries import instrument_ref, price_view, sector_names
from nse_platform.models import FeeSchedule, Instrument, Portfolio, PortfolioTransaction
from nse_platform.models.enums import PortfolioKind, TxnType


class PortfolioError(ValueError):
    pass


@dataclass
class Holding:
    qty: float = 0.0
    cost: float = 0.0  # total cost basis incl. buy fees
    realized: float = 0.0
    dividends: float = 0.0
    fees: float = 0.0


def fee_model(db: Session, p: Portfolio) -> FeeModel:
    fs = db.get(FeeSchedule, p.fee_schedule_id) if p.fee_schedule_id else db.scalar(
        select(FeeSchedule).where(FeeSchedule.is_default.is_(True)))
    return FeeModel.from_components(fs.components if fs else None)


def _replay(db: Session, txns: list[PortfolioTransaction], upto: date | None = None) -> tuple[float, dict[int, Holding],
                                                                                              dict[str, float]]:
    cash = 0.0
    totals = defaultdict(float)
    hold: dict[int, Holding] = defaultdict(Holding)
    # One timeline of share-ratio events and transactions. Events sort before same-day transactions because a
    # trade on the ex-date is already on the post-event share base.
    timeline: list[tuple[date, int, int, Any]] = []
    for iid in {t.instrument_id for t in txns if t.instrument_id}:
        for e in adjustment_events(db, iid):
            timeline.append((e.ex_date, 0, iid, e))
    for t in txns:
        timeline.append((t.trade_date, 1, t.id or 0, t))
    for d, kind, iid, item in sorted(timeline, key=lambda x: (x[0], x[1], x[2])):
        if upto and d > upto:
            break
        if kind == 0:
            h = hold[iid]
            if h.qty > 0:
                h.qty = h.qty / item.factor  # cost basis unchanged; average cost falls accordingly
            continue
        t = item
        cash += t.net_cash_flow
        if t.txn_type == TxnType.DEPOSIT.value:
            totals["deposits"] += t.gross_amount
        elif t.txn_type == TxnType.WITHDRAWAL.value:
            totals["withdrawals"] += t.gross_amount
        elif t.txn_type == TxnType.BUY.value:
            h = hold[t.instrument_id]  # type: ignore[index]
            h.qty += t.quantity or 0
            h.cost += t.gross_amount + t.fees
            h.fees += t.fees
            totals["fees"] += t.fees
        elif t.txn_type == TxnType.SELL.value:
            h = hold[t.instrument_id]  # type: ignore[index]
            q = t.quantity or 0
            if h.qty <= 0:
                continue
            avg = h.cost / h.qty
            h.realized += (t.gross_amount - t.fees) - avg * q
            h.cost -= avg * q
            h.qty -= q
            h.fees += t.fees
            totals["fees"] += t.fees
            totals["realized"] += (t.gross_amount - t.fees) - avg * q
        elif t.txn_type == TxnType.DIVIDEND.value:
            hold[t.instrument_id].dividends += t.net_cash_flow  # type: ignore[index]
            totals["dividends_net"] += t.net_cash_flow
            totals["dividend_tax"] += t.tax
        elif t.txn_type == TxnType.FEE.value:
            totals["fees"] += -t.net_cash_flow
    return cash, hold, dict(totals)


def holdings_qty(db: Session, p: Portfolio, instrument_id: int, on: date) -> float:
    txns = db.scalars(select(PortfolioTransaction).where(PortfolioTransaction.portfolio_id == p.id)).all()
    _, hold, _ = _replay(db, list(txns), on)
    return hold[instrument_id].qty if instrument_id in hold else 0.0


def record(db: Session, p: Portfolio, *, txn_type: str, trade_date: date, instrument: Instrument | None,
           quantity: float | None, price: float | None, amount: float | None, fees: float | None,
           notes: str | None) -> PortfolioTransaction:
    fm = fee_model(db, p)
    tt = TxnType(txn_type)
    txns = list(db.scalars(select(PortfolioTransaction).where(PortfolioTransaction.portfolio_id == p.id)))
    cash, hold, _ = _replay(db, txns, trade_date)
    breakdown: dict[str, float] | None = None
    tax = 0.0
    if tt in (TxnType.BUY, TxnType.SELL):
        if instrument is None or not quantity or quantity <= 0 or not price or price <= 0:
            raise PortfolioError("BUY/SELL require an instrument, a positive quantity and a positive price")
        gross = quantity * price
        if fees is None:
            breakdown = fm.breakdown(gross)
            fees = breakdown["total"]
        if tt == TxnType.BUY:
            net = -(gross + fees)
            if p.kind == PortfolioKind.PAPER.value and cash + net < -1e-6:
                raise PortfolioError(f"Insufficient cash: available {cash:,.2f}, required {-net:,.2f}")
        else:
            held = hold[instrument.id].qty if instrument.id in hold else 0.0
            if quantity > held + 1e-9:
                raise PortfolioError(f"Cannot sell {quantity:g}: only {held:g} held on {trade_date}")
            net = gross - fees
    elif tt == TxnType.DIVIDEND:
        if instrument is None or not amount or amount <= 0:
            raise PortfolioError("DIVIDEND requires an instrument and a positive gross amount")
        gross = amount
        fees = 0.0
        tax = round(gross * p.dividend_tax_rate, 4)
        net = gross - tax
    elif tt in (TxnType.DEPOSIT, TxnType.WITHDRAWAL):
        if not amount or amount <= 0:
            raise PortfolioError(f"{tt.value} requires a positive amount")
        gross, fees = amount, 0.0
        net = amount if tt == TxnType.DEPOSIT else -amount
        if tt == TxnType.WITHDRAWAL and p.kind == PortfolioKind.PAPER.value and cash - amount < -1e-6:
            raise PortfolioError(f"Insufficient cash: available {cash:,.2f}")
    else:
        if not amount or amount <= 0:
            raise PortfolioError("FEE requires a positive amount")
        gross, fees, net = amount, 0.0, -amount
    t = PortfolioTransaction(portfolio_id=p.id, instrument_id=instrument.id if instrument else None,
                             txn_type=tt.value, trade_date=trade_date, quantity=quantity, price=price,
                             gross_amount=round(gross, 4), fees=round(fees or 0.0, 4), fee_breakdown=breakdown,
                             tax=tax, net_cash_flow=round(net, 4), notes=notes)
    db.add(t)
    db.flush()
    return t


def summary(db: Session, p: Portfolio) -> dict[str, Any]:
    txns = list(db.scalars(select(PortfolioTransaction).where(PortfolioTransaction.portfolio_id == p.id)))
    cash, hold, totals = _replay(db, txns)
    ids = [iid for iid, h in hold.items() if h.qty > 1e-9]
    insts = {i.id: i for i in db.scalars(select(Instrument).where(Instrument.id.in_(list(hold))))}
    pv = price_view(db, [insts[i] for i in ids]) if ids else {}
    sectors = sector_names(db)
    positions = []
    mv_total = 0.0
    priced_all = True
    for iid in ids:
        h, inst = hold[iid], insts[iid]
        px = pv[iid]["price"]
        mv = h.qty * px if px is not None else None
        priced_all &= mv is not None
        mv_total += mv or 0.0
        positions.append({
            "instrument": instrument_ref(inst, sectors), "quantity": h.qty, "avg_cost": h.cost / h.qty,
            "cost_basis": h.cost, "price": px, "freshness": pv[iid]["freshness"], "market_value": mv,
            "unrealized_pnl": mv - h.cost if mv is not None else None,
            "unrealized_pct": (mv / h.cost - 1) if mv is not None and h.cost > 0 else None,
            "realized_pnl": h.realized, "dividends_net": h.dividends, "fees_paid": h.fees,
        })
    total_value = cash + mv_total
    contributions = totals.get("deposits", 0.0) - totals.get("withdrawals", 0.0)
    for pos in positions:
        pos["weight"] = pos["market_value"] / total_value if pos["market_value"] is not None and total_value > 0 else None
    weights = np.array([p_["weight"] for p_ in positions if p_["weight"] is not None])
    by_sector: dict[str, float] = defaultdict(float)
    by_type: dict[str, float] = defaultdict(float)
    for pos in positions:
        if pos["market_value"] is not None:
            by_sector[pos["instrument"]["sector"] or "Unclassified"] += pos["market_value"]
            by_type[pos["instrument"]["security_type"]] += pos["market_value"]
    return {
        "id": p.id, "name": p.name, "kind": p.kind, "base_currency": p.base_currency,
        "dividend_tax_rate": p.dividend_tax_rate, "cash": cash, "market_value": mv_total, "total_value": total_value,
        "all_positions_priced": priced_all,
        "net_contributions": contributions, "total_return": (total_value / contributions - 1)
        if contributions > 0 else None,
        "unrealized_pnl": sum(x["unrealized_pnl"] or 0 for x in positions),
        "realized_pnl": totals.get("realized", 0.0), "dividends_net": totals.get("dividends_net", 0.0),
        "dividend_tax_withheld": totals.get("dividend_tax", 0.0), "fees_paid": totals.get("fees", 0.0),
        "positions": sorted(positions, key=lambda x: -(x["market_value"] or 0)),
        "allocation": {"by_sector": {k: v / total_value for k, v in by_sector.items()} if total_value > 0 else {},
                       "by_security_type": {k: v / total_value for k, v in by_type.items()} if total_value > 0 else {},
                       "cash": cash / total_value if total_value > 0 else None},
        "concentration": {"top_position_weight": float(weights.max()) if weights.size else None,
                          "hhi": float((weights**2).sum()) if weights.size else None,
                          "positions": len(positions),
                          "warning": "Top position exceeds 25% of portfolio" if weights.size and weights.max() > 0.25
                          else None},
        "is_simulation": p.kind == PortfolioKind.PAPER.value,
    }


def performance(db: Session, p: Portfolio) -> dict[str, Any]:
    txns = sorted(db.scalars(select(PortfolioTransaction).where(PortfolioTransaction.portfolio_id == p.id)),
                  key=lambda t: (t.trade_date, t.id))
    if not txns:
        return {"series": [], "note": "No transactions yet"}
    start = txns[0].trade_date
    inst_ids = sorted({t.instrument_id for t in txns if t.instrument_id})
    closes = {}
    universe_synthetic = None
    for iid in inst_ids:
        inst = db.get(Instrument, iid)
        assert inst is not None
        universe_synthetic = inst.is_synthetic
        raw = load_daily(db, inst)
        closes[iid] = raw["close"]
    idx = sorted({d for s in closes.values() for d in s.index if d >= pd.Timestamp(start)} | {pd.Timestamp(start)})
    if not idx:
        return {"series": []}
    calendar = pd.DatetimeIndex(idx)
    px = pd.DataFrame({iid: s.reindex(s.index.union(calendar)).ffill().reindex(calendar) for iid, s in closes.items()})
    values, flows = [], []
    for d in calendar:
        cash, hold, _ = _replay(db, txns, d.date())
        mv = sum(h.qty * float(px.at[d, iid]) for iid, h in hold.items()
                 if h.qty > 0 and iid in px.columns and pd.notna(px.at[d, iid]))
        values.append(cash + mv)
        flows.append(sum(t.net_cash_flow for t in txns if t.trade_date == d.date()
                         and t.txn_type in (TxnType.DEPOSIT.value, TxnType.WITHDRAWAL.value)))
    v = pd.Series(values, index=calendar)
    f = pd.Series(flows, index=calendar)
    prev = v.shift(1)
    r = ((v - f) / prev - 1).where(prev > 0).fillna(0.0)
    twr = (1 + r).cumprod() - 1
    # Benchmark: equal-weight, split-adjusted price index of the same data universe.
    bench = None
    if universe_synthetic is not None:
        insts = db.scalars(select(Instrument).where(Instrument.is_synthetic.is_(universe_synthetic),
                                                    Instrument.security_type.in_(["EQUITY", "ETF", "REIT"]))).all()
        cols = {}
        for inst in insts:
            raw = load_daily(db, inst)
            if raw.empty:
                continue
            adj = apply_adjustments(raw, adjustment_events(db, inst.id))["close"]
            s = adj.reindex(adj.index.union(calendar)).ffill().reindex(calendar)
            if pd.notna(s.iloc[0]):
                cols[inst.id] = s / s.iloc[0]
        if cols:
            bench = pd.DataFrame(cols).mean(axis=1) - 1
    step = max(1, len(calendar) // 500)
    series = [{"date": d.date().isoformat(), "value": round(float(v[d]), 2), "twr": round(float(twr[d]), 6),
               "benchmark": round(float(bench[d]), 6) if bench is not None else None}
              for d in calendar[::step]]
    return {"series": series, "twr": float(twr.iloc[-1]),
            "benchmark_return": float(bench.iloc[-1]) if bench is not None else None,
            "benchmark": "Equal-weight split-adjusted price index of the same data universe (excludes dividends)",
            "method": "Daily time-weighted return; deposits/withdrawals neutralised."}
