"""Event-driven daily backtest engine (pure: no DB access, fully unit-testable).

Execution model (documented in docs/architecture/backtesting.md):
* Conditions are evaluated on bar close t using point-in-time features (indicators are look-ahead free;
  fundamentals use publication dates).
* Orders execute on the next session t+1 at the open (or close if `next_close` or the open is unavailable),
  adjusted by slippage. Never on the signal bar.
* Fills are capped at `max_participation` x that session's reported volume. Zero/unreported volume -> no fill;
  the order stays pending up to `order_ttl_bars` sessions then is cancelled (counted in diagnostics).
* Stops/targets are evaluated on closes and executed next session (no intrabar assumptions).
* Simulation runs on RAW prices; bonus/split events change share quantity on the ex-date; cash dividends are
  credited (net of withholding tax) on the payment date. Indicators use split-adjusted series.
* Suspended / non-trading instruments: no bar -> no trading; positions are marked at the last known price.
* Every trade pays the full fee schedule on both sides.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from nse_platform.backtesting.costs import FeeModel
from nse_platform.backtesting.spec import BacktestParams, Condition, StrategySpec

BACKTEST_CALC_VERSION = "bt-1.0.0"


@dataclass
class InstrumentData:
    instrument_id: int
    symbol: str
    raw: pd.DataFrame  # daily raw OHLCV, DatetimeIndex (normalized dates)
    features: pd.DataFrame  # features on the strategy timeframe, aligned to daily dates of signal bars
    ratio_events: list[tuple[date, float, float]] = field(default_factory=list)  # (ex_date, ratio_from, ratio_to)
    dividends: list[tuple[date, date | None, float]] = field(default_factory=list)  # (ex, pay, amount per share)
    lot_size: int = 1


@dataclass
class Position:
    qty: float
    entry_date: date
    entry_price: float
    entry_fees: float
    cost_basis: float
    bars_held: int = 0
    segment: str = "in_sample"


def evaluate(cond: Condition, f: pd.DataFrame) -> pd.Series:
    left = f[cond.left] if cond.left in f.columns else pd.Series(np.nan, index=f.index)
    right = f[cond.right] if isinstance(cond.right, str) else pd.Series(float(cond.right), index=f.index)
    if isinstance(cond.right, str) and cond.right not in f.columns:
        right = pd.Series(np.nan, index=f.index)
    valid = left.notna() & right.notna()
    if cond.op == ">":
        out = left > right
    elif cond.op == ">=":
        out = left >= right
    elif cond.op == "<":
        out = left < right
    elif cond.op == "<=":
        out = left <= right
    elif cond.op == "crosses_above":
        out = (left > right) & (left.shift(1) <= right.shift(1))
        valid &= left.shift(1).notna() & right.shift(1).notna()
    else:
        out = (left < right) & (left.shift(1) >= right.shift(1))
        valid &= left.shift(1).notna() & right.shift(1).notna()
    return (out & valid).fillna(False)  # a missing input never satisfies a condition


def signals(spec: StrategySpec, f: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    entry = pd.Series(True, index=f.index)
    for c in spec.entry:
        entry &= evaluate(c, f)
    exit_ = pd.Series(False, index=f.index)
    for c in spec.exit:
        exit_ |= evaluate(c, f)
    return entry, exit_


def segment_of(d: date, bounds: tuple[date, date]) -> str:
    if d < bounds[0]:
        return "in_sample"
    if d < bounds[1]:
        return "validation"
    return "out_of_sample"


def run(spec: StrategySpec, params: BacktestParams, data: list[InstrumentData], fees: FeeModel,
        benchmark: pd.Series | None = None) -> dict[str, Any]:
    start, end = pd.Timestamp(params.start_date), pd.Timestamp(params.end_date)
    by_id = {d.instrument_id: d for d in data}
    sigs = {d.instrument_id: signals(spec, d.features) for d in data}
    calendar = sorted({ts for d in data for ts in d.raw.index if start <= ts <= end})
    if len(calendar) < 2:
        raise ValueError("no price data in the requested period for the selected universe")
    n = len(calendar)
    b1 = calendar[min(n - 1, int(n * params.validation_split.in_sample))].date()
    b2 = calendar[min(n - 1, int(n * (params.validation_split.in_sample + params.validation_split.validation)))].date()
    bounds = (b1, b2)
    slip = params.slippage_bps / 10_000
    cash = params.initial_capital
    positions: dict[int, Position] = {}
    pending: dict[int, dict[str, Any]] = {}  # iid -> {"side", "age", "qty"(sell) or "budget"(buy), "reason"}
    last_close: dict[int, float] = {}
    dividends_due: list[tuple[date, int, float]] = []
    trades: list[dict[str, Any]] = []
    equity_curve: list[dict[str, Any]] = []
    diag = {"orders_cancelled_no_liquidity": 0, "partial_fills": 0, "dividends_received": 0.0, "fees_paid": 0.0,
            "taxes_withheld": 0.0, "ratio_adjustments": 0, "signals_skipped_no_cash": 0,
            "signals_skipped_max_positions": 0}
    lot_override = params.lot_size

    def lot(d: InstrumentData) -> int:
        return lot_override or d.lot_size or 1

    for i, ts in enumerate(calendar):
        today = ts.date()
        # 1. Corporate actions on held positions.
        for iid, pos in list(positions.items()):
            d = by_id[iid]
            for ex, rf, rt in d.ratio_events:
                if ex == today:
                    pos.qty = pos.qty * rt / rf
                    pos.entry_price = pos.entry_price * rf / rt
                    diag["ratio_adjustments"] += 1
            if params.include_dividends:
                for ex, pay, amt in d.dividends:
                    if ex == today:
                        dividends_due.append((pay or ex, iid, pos.qty * amt))
        for due in [x for x in dividends_due if x[0] <= today]:
            gross = due[2]
            tax = gross * params.dividend_tax_rate
            cash += gross - tax
            diag["dividends_received"] += gross - tax
            diag["taxes_withheld"] += tax
            dividends_due.remove(due)

        # 2. Execute pending orders at today's price.
        for iid, order in list(pending.items()):
            d = by_id[iid]
            if ts not in d.raw.index:
                order["age"] += 1
                if order["age"] >= params.order_ttl_bars:
                    pending.pop(iid)
                    diag["orders_cancelled_no_liquidity"] += 1
                continue
            bar = d.raw.loc[ts]
            px = bar["open"] if params.execution == "next_open" and pd.notna(bar["open"]) else bar["close"]
            vol = bar["volume"]
            if pd.isna(vol) or vol <= 0 or pd.isna(px):
                order["age"] += 1
                if order["age"] >= params.order_ttl_bars:
                    pending.pop(iid)
                    diag["orders_cancelled_no_liquidity"] += 1
                continue
            cap = math.floor(vol * params.max_participation / lot(d)) * lot(d)
            if order["side"] == "buy":
                fill_px = px * (1 + slip)
                est_rate = fees.rate_estimate()
                qty = math.floor(order["budget"] / (fill_px * (1 + est_rate)) / lot(d)) * lot(d)
                if qty > cap:
                    qty = cap
                    diag["partial_fills"] += 1
                consideration = qty * fill_px
                fee = fees.total(consideration)
                while qty > 0 and consideration + fee > cash:
                    qty -= lot(d)
                    consideration = qty * fill_px
                    fee = fees.total(consideration)
                pending.pop(iid)
                if qty <= 0:
                    diag["orders_cancelled_no_liquidity" if cap <= 0 else "signals_skipped_no_cash"] += 1
                    continue
                cash -= consideration + fee
                diag["fees_paid"] += fee
                positions[iid] = Position(qty=qty, entry_date=today, entry_price=fill_px, entry_fees=fee,
                                          cost_basis=consideration + fee, segment=segment_of(today, bounds))
            else:
                pos = positions.get(iid)
                if pos is None:
                    pending.pop(iid)
                    continue
                fill_px = px * (1 - slip)
                qty = min(pos.qty, max(cap, 0))
                if qty < pos.qty:
                    diag["partial_fills"] += 1
                if qty <= 0:
                    order["age"] += 1
                    continue  # sells retry until liquidity appears (no TTL for exits)
                consideration = qty * fill_px
                fee = fees.total(consideration)
                cash += consideration - fee
                diag["fees_paid"] += fee
                frac = qty / pos.qty
                basis = pos.cost_basis * frac
                trades.append({
                    "instrument_id": iid, "symbol": d.symbol, "segment": pos.segment, "entry_date": pos.entry_date,
                    "entry_price": round(pos.entry_price, 6), "exit_date": today, "exit_price": round(fill_px, 6),
                    "quantity": qty, "entry_fees": round(pos.entry_fees * frac, 4), "exit_fees": round(fee, 4),
                    "net_pnl": round(consideration - fee - basis, 4),
                    "return_pct": round((consideration - fee) / basis - 1, 6), "bars_held": pos.bars_held,
                    "exit_reason": order["reason"],
                })
                if qty >= pos.qty - 1e-9:
                    positions.pop(iid)
                    pending.pop(iid)
                else:
                    pos.qty -= qty
                    pos.cost_basis -= basis
                    pos.entry_fees *= 1 - frac

        # 3. Mark to market.
        for d in data:
            if ts in d.raw.index and pd.notna(d.raw.at[ts, "close"]):
                last_close[d.instrument_id] = float(d.raw.at[ts, "close"])
        holdings = sum(p.qty * last_close.get(iid, p.entry_price) for iid, p in positions.items())
        equity = cash + holdings
        equity_curve.append({"date": today.isoformat(), "equity": round(equity, 2), "cash": round(cash, 2),
                             "exposure": round(holdings / equity, 4) if equity > 0 else 0.0,
                             "segment": segment_of(today, bounds)})
        if i == len(calendar) - 1:
            break

        # 4. Exits (evaluated on today's close, executed next session).
        for iid, pos in positions.items():
            if iid in pending:
                continue
            d = by_id[iid]
            if ts not in d.raw.index:
                continue
            pos.bars_held += 1
            c = float(d.raw.at[ts, "close"])
            reason = None
            if spec.stop_loss_pct and c <= pos.entry_price * (1 - spec.stop_loss_pct):
                reason = "stop_loss"
            elif spec.take_profit_pct and c >= pos.entry_price * (1 + spec.take_profit_pct):
                reason = "take_profit"
            elif spec.max_holding_bars and pos.bars_held >= spec.max_holding_bars:
                reason = "max_holding"
            elif ts in sigs[iid][1].index and bool(sigs[iid][1].get(ts, False)):
                reason = "exit_signal"
            if reason:
                pending[iid] = {"side": "sell", "age": 0, "reason": reason}

        # 5. Entries.
        candidates = []
        for d in data:
            iid = d.instrument_id
            if iid in positions or iid in pending or ts not in d.features.index:
                continue
            if bool(sigs[iid][0].get(ts, False)):
                strength = d.features.at[ts, "roc_60"] if "roc_60" in d.features.columns else np.nan
                candidates.append((-(strength if pd.notna(strength) else -np.inf), d.symbol, iid))
        candidates.sort()
        open_slots = spec.position_sizing.max_positions - len(positions) - sum(
            1 for o in pending.values() if o["side"] == "buy")
        for _, _, iid in candidates:
            if open_slots <= 0:
                diag["signals_skipped_max_positions"] += 1
                continue
            if spec.position_sizing.method == "equal_weight":
                budget = equity / spec.position_sizing.max_positions
            else:
                budget = equity * spec.position_sizing.fraction
            budget = min(budget, cash)
            if budget <= 0:
                diag["signals_skipped_no_cash"] += 1
                continue
            pending[iid] = {"side": "buy", "age": 0, "budget": budget}
            open_slots -= 1

    open_positions = [{
        "instrument_id": iid, "symbol": by_id[iid].symbol, "segment": p.segment, "entry_date": p.entry_date,
        "entry_price": round(p.entry_price, 6), "quantity": p.qty, "entry_fees": round(p.entry_fees, 4),
        "mark_price": last_close.get(iid), "unrealized_pnl": round(p.qty * last_close.get(iid, p.entry_price)
                                                                   - p.cost_basis, 2),
        "bars_held": p.bars_held,
    } for iid, p in positions.items()]
    eq = pd.Series([e["equity"] for e in equity_curve], index=pd.to_datetime([e["date"] for e in equity_curve]))
    bench = _benchmark_curve(data, calendar, params, fees) if benchmark is None else benchmark
    metrics = {
        "overall": performance(eq, trades, params, equity_curve),
        "segments": {seg: performance(eq[[e["segment"] == seg for e in equity_curve]],
                                      [t for t in trades if t["segment"] == seg], params,
                                      [e for e in equity_curve if e["segment"] == seg])
                     for seg in ("in_sample", "validation", "out_of_sample")},
        "segment_bounds": {"validation_start": b1.isoformat(), "out_of_sample_start": b2.isoformat()},
        "benchmark": performance(bench, [], params, None) if bench is not None and len(bench) > 1 else None,
        "diagnostics": {k: round(v, 2) if isinstance(v, float) else v for k, v in diag.items()},
        "fee_model": fees.as_dict(), "fee_rate_per_side_estimate": round(fees.rate_estimate(), 6),
    }
    for e, b in zip(equity_curve, bench.reindex(eq.index).ffill().tolist() if bench is not None else [], strict=False):
        e["benchmark"] = round(b, 2) if pd.notna(b) else None
    return {"metrics": metrics, "trades": trades, "open_positions": open_positions, "equity_curve": equity_curve}


def _benchmark_curve(data: list[InstrumentData], calendar: list[pd.Timestamp], params: BacktestParams,
                     fees: FeeModel) -> pd.Series | None:
    """Equal-weight buy-and-hold of every universe instrument that traded on the first session (entry costs paid).

    Uses split-adjusted closes (bonus/split neutral); dividends excluded, matching price-return convention.
    """
    first = calendar[0]
    cols = {}
    for d in data:
        c = d.raw["close"]
        adj = c.copy()
        for ex, rf, rt in d.ratio_events:
            adj[adj.index < pd.Timestamp(ex)] *= rf / rt
        if first in adj.index and pd.notna(adj.at[first]):
            cols[d.symbol] = adj.reindex(calendar).ffill() / adj.at[first]
    if not cols:
        return None
    rel = pd.DataFrame(cols).mean(axis=1)
    return rel * params.initial_capital * (1 - fees.rate_estimate())


def performance(eq: pd.Series, trades: list[dict[str, Any]], params: BacktestParams,
                curve: list[dict[str, Any]] | None) -> dict[str, Any]:
    if eq is None or len(eq) < 2 or eq.iloc[0] <= 0:
        return {"insufficient_data": True}
    rets = eq.pct_change().dropna()
    days = (eq.index[-1] - eq.index[0]).days
    years = days / 365.25
    total = eq.iloc[-1] / eq.iloc[0] - 1
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1 if years >= 1 and eq.iloc[-1] > 0 else None
    ann_ret = (1 + total) ** (1 / years) - 1 if years > 0 and (1 + total) > 0 else None
    vol = float(rets.std(ddof=1) * np.sqrt(252)) if len(rets) > 2 else None
    rf_d = (1 + params.risk_free_rate) ** (1 / 252) - 1
    ex = rets - rf_d
    sharpe = float(ex.mean() / rets.std(ddof=1) * np.sqrt(252)) if len(rets) > 2 and rets.std() > 0 else None
    downside = np.sqrt((np.minimum(ex, 0) ** 2).mean())
    sortino = float(ex.mean() / downside * np.sqrt(252)) if downside > 0 else None
    dd = eq / eq.cummax() - 1
    wins = [t["net_pnl"] for t in trades if t["net_pnl"] > 0]
    losses = [t["net_pnl"] for t in trades if t["net_pnl"] <= 0]
    win_r = [t["return_pct"] for t in trades if t["net_pnl"] > 0]
    loss_r = [t["return_pct"] for t in trades if t["net_pnl"] <= 0]
    out = {
        "start": eq.index[0].date().isoformat(), "end": eq.index[-1].date().isoformat(),
        "start_equity": round(float(eq.iloc[0]), 2), "end_equity": round(float(eq.iloc[-1]), 2),
        "total_return": round(float(total), 6), "annualized_return": _r(ann_ret), "cagr": _r(cagr),
        "volatility": _r(vol), "sharpe": _r(sharpe), "sortino": _r(sortino),
        "max_drawdown": round(float(dd.min()), 6), "trades": len(trades),
        "win_rate": round(len(wins) / len(trades), 4) if trades else None,
        "avg_win_pct": _r(float(np.mean(win_r))) if win_r else None,
        "avg_loss_pct": _r(float(np.mean(loss_r))) if loss_r else None,
        "profit_factor": _r(sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else None,
        "payoff_ratio": _r(np.mean(win_r) / abs(np.mean(loss_r))) if win_r and loss_r and np.mean(loss_r) != 0
        else None,
        "avg_bars_held": _r(float(np.mean([t["bars_held"] for t in trades]))) if trades else None,
    }
    if curve:
        out["exposure"] = round(float(np.mean([c["exposure"] for c in curve])), 4)
    return out


def _r(x: Any, nd: int = 6) -> float | None:
    if x is None:
        return None
    f = float(x)
    return round(f, nd) if math.isfinite(f) else None
