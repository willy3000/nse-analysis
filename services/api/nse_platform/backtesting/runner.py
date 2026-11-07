"""Loads point-in-time inputs from the DB, runs the engine, persists results. Runs inside the worker only."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, datetime, timezone
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from nse_platform.analytics import indicators as ind_lib
from nse_platform.analytics.series import adjustment_events, apply_adjustments, load_daily, resample
from nse_platform.backtesting.costs import FeeModel
from nse_platform.backtesting.engine import BACKTEST_CALC_VERSION, InstrumentData, run
from nse_platform.backtesting.spec import BacktestParams, StrategySpec
from nse_platform.core.db import session_scope
from nse_platform.fundamentals.metrics import compute, load_dividends, load_statements
from nse_platform.models import (
    BacktestRun,
    BacktestTrade,
    CorporateAction,
    FeeSchedule,
    IndexHistory,
    Instrument,
    MarketIndex,
)
from nse_platform.models.enums import BacktestStatus, CorporateActionType, TradingStatus

log = logging.getLogger(__name__)


def _pit_fundamentals(statements: pd.DataFrame, dividends: pd.DataFrame, raw: pd.DataFrame,
                      events: list[tuple[date, float]], needed: set[str]) -> pd.DataFrame:
    """Daily point-in-time fundamental features. Recomputed on each publication date and each ex-date,
    price-dependent ratios are refreshed daily from the raw close."""
    out = pd.DataFrame(index=raw.index)
    if not needed:
        return out
    change_dates = set()
    if not statements.empty:
        change_dates |= set(pd.to_datetime(statements["published_at"]).dt.normalize())
    if not dividends.empty:
        change_dates |= set(pd.to_datetime(dividends["ex_date"].dropna()).dt.normalize())
    change_dates |= {pd.Timestamp(year=y, month=m, day=1) for y in range(raw.index[0].year, raw.index[-1].year + 1)
                     for m in range(1, 13)}  # monthly refresh for trailing-dividend windows
    points = sorted(d for d in change_dates if raw.index[0] <= d <= raw.index[-1])
    rows = {}
    for d in points:
        snap = compute(statements, dividends, d.date(), None, ratio_events=events)
        rows[d] = snap.metrics
    snaps = pd.DataFrame.from_dict(rows, orient="index").reindex(raw.index, method="ffill") if rows else pd.DataFrame(
        index=raw.index)
    close = raw["close"]
    for feat in needed:
        key = feat.split(":", 1)[1]
        if key == "pe_ratio":
            eps = snaps.get("eps")
            out[feat] = (close / eps).where(eps > 0) if eps is not None else np.nan
        elif key == "dividend_yield":
            # Trailing-365-day dividends known at each date divided by the raw close.
            ttm = pd.Series(np.nan, index=raw.index)
            if not dividends.empty:
                ex = pd.to_datetime(dividends["ex_date"]).dt.normalize()
                amt = pd.Series(dividends["amount"].to_numpy(), index=ex).groupby(level=0).sum()
                daily = amt.reindex(raw.index.union(amt.index), fill_value=0.0)
                ttm = daily.rolling("365D").sum().reindex(raw.index)
            out[feat] = ttm / close
        elif key == "pb_ratio":
            eq, sh = snaps.get("total_equity"), snaps.get("shares")
            out[feat] = np.nan  # book value per share requires shares; computed below if available
            if "roe" in snaps.columns and eq is not None and sh is not None:
                out[feat] = close / (eq / sh)
        else:
            out[feat] = snaps[key] if key in snaps.columns else np.nan
    return out


def load_universe(session: Session, spec: StrategySpec, params: BacktestParams) -> tuple[list[InstrumentData],
                                                                                        list[str], str]:
    q = select(Instrument).where(Instrument.security_type.in_(spec.universe.security_types),
                                 Instrument.is_synthetic.is_(spec.universe.synthetic),
                                 Instrument.primary_provider_id.is_not(None))
    if spec.universe.symbols:
        q = q.where(Instrument.symbol.in_(spec.universe.symbols))
    insts = session.scalars(q.order_by(Instrument.symbol)).all()
    warnings: list[str] = []
    needed_fund = {c.left for c in spec.entry + spec.exit if c.left.startswith("fund:")} | {
        c.right for c in spec.entry + spec.exit if isinstance(c.right, str) and c.right.startswith("fund:")}
    data: list[InstrumentData] = []
    fp = hashlib.sha256()
    for inst in insts:
        raw = load_daily(session, inst)
        raw = raw[raw.index <= pd.Timestamp(params.end_date)]
        if len(raw) < 30:
            continue
        evs = adjustment_events(session, inst.id)
        adj = apply_adjustments(raw, evs)
        if spec.universe.min_trading_frequency > 0:
            window = raw[raw.index >= pd.Timestamp(params.start_date)]
            tf = float((window["volume"] > 0).mean()) if len(window) else 0.0
            if tf < spec.universe.min_trading_frequency:
                continue
        if spec.timeframe == "1w":
            wk = resample(adj, "1w", as_of=params.end_date)
            wk = wk[~wk["is_partial"]]
            ind = ind_lib.compute_all(wk, "1w").join(wk[["open", "high", "low", "close", "volume"]])
            # Map each completed weekly bar to the last daily session of that week (when it became known).
            last_daily = raw.index.to_series().groupby(raw.index.to_period("W-SUN").start_time).max()
            ind.index = [last_daily.get(ts, pd.NaT) for ts in ind.index]
            feats = ind[ind.index.notna()]
        else:
            feats = ind_lib.compute_all(adj, "1d").join(adj[["open", "high", "low", "close", "volume"]])
        if needed_fund:
            fund = _pit_fundamentals(load_statements(session, inst.id), load_dividends(session, inst.id), raw,
                                     [(e.ex_date, e.factor) for e in evs], needed_fund)
            feats = feats.join(fund, how="left")
        divs = session.execute(select(CorporateAction.ex_date, CorporateAction.payment_date,
                                      CorporateAction.cash_amount).where(
            CorporateAction.instrument_id == inst.id,
            CorporateAction.action_type == CorporateActionType.DIVIDEND_CASH.value,
            CorporateAction.ex_date.is_not(None), CorporateAction.cash_amount.is_not(None))).all()
        data.append(InstrumentData(
            instrument_id=inst.id, symbol=inst.symbol, raw=raw[["open", "high", "low", "close", "volume"]],
            features=feats, lot_size=inst.lot_size or 1,
            ratio_events=[(e.ex_date, 1.0, 1.0 / e.factor) for e in evs],
            dividends=[(r[0], r[1], float(r[2])) for r in divs],
        ))
        fp.update(f"{inst.id}:{len(raw)}:{raw.index[0]}:{raw.index[-1]}:{raw['close'].sum():.6f}:"
                  f"{raw['volume'].sum():.0f}:{len(evs)}:{len(divs)}|".encode())
        if inst.trading_status == TradingStatus.SUSPENDED.value:
            warnings.append(f"{inst.symbol} is currently suspended; positions are marked at the last traded price.")
    if not any(i.trading_status == TradingStatus.DELISTED.value for i in insts):
        warnings.append("Universe contains no delisted instruments. If the data provider omits delisted "
                        "companies, results carry survivorship bias.")
    if spec.universe.synthetic:
        warnings.append("SYNTHETIC DEVELOPMENT DATA - results say nothing about real NSE securities.")
    return data, warnings, fp.hexdigest()


def _benchmark_index(session: Session, code: str, params: BacktestParams) -> pd.Series | None:
    idx = session.scalar(select(MarketIndex).where(MarketIndex.code == code))
    if idx is None:
        return None
    rows = session.execute(select(IndexHistory.ts, IndexHistory.close).where(
        IndexHistory.index_id == idx.id, IndexHistory.session_date >= params.start_date,
        IndexHistory.session_date <= params.end_date).order_by(IndexHistory.ts)).all()
    if len(rows) < 2:
        return None
    s = pd.Series([r[1] for r in rows], index=pd.to_datetime([r[0] for r in rows]).tz_localize(None).normalize())
    return s / s.iloc[0] * params.initial_capital


def execute_run(run_id: int) -> None:
    with session_scope() as s:
        run_row = s.get(BacktestRun, run_id)
        assert run_row is not None
        try:
            spec = StrategySpec.model_validate(run_row.strategy_snapshot)
            params = BacktestParams.model_validate(run_row.params)
            fs = s.scalar(select(FeeSchedule).where(FeeSchedule.code == params.fee_schedule_code))
            if fs is None:
                raise ValueError(f"fee schedule {params.fee_schedule_code!r} not found")
            fees = FeeModel.from_components({**fs.components, **(params.fee_overrides or {})})
            data, warnings, fingerprint = load_universe(s, spec, params)
            if not data:
                raise ValueError("no instruments with price data match the strategy universe")
            bench = None if params.benchmark == "equal_weight_universe" else _benchmark_index(s, params.benchmark,
                                                                                              params)
            if params.benchmark != "equal_weight_universe" and bench is None:
                warnings.append(f"Benchmark index {params.benchmark!r} unavailable; using equal-weight universe.")
            result = run(spec, params, data, fees, bench)
            n_trades = len(result["trades"])
            if n_trades < 30:
                warnings.append(f"Only {n_trades} closed trades: performance statistics are not statistically "
                                "meaningful.")
            prior = s.scalar(select(func.count()).select_from(BacktestRun).where(
                BacktestRun.user_id == run_row.user_id, BacktestRun.strategy_id == run_row.strategy_id,
                BacktestRun.id != run_row.id)) if run_row.strategy_id else 0
            if prior and prior >= 5:
                warnings.append(f"This strategy has been backtested {prior + 1} times. Repeatedly tuning rules on "
                                "the same history overfits; judge it on the out-of-sample segment only.")
            oos = result["metrics"]["segments"]["out_of_sample"]
            ins = result["metrics"]["segments"]["in_sample"]
            if not oos.get("insufficient_data") and not ins.get("insufficient_data"):
                if (ins.get("sharpe") or 0) > 0.5 and (oos.get("sharpe") or 0) < 0.5 * (ins.get("sharpe") or 0):
                    warnings.append("Out-of-sample Sharpe is less than half the in-sample Sharpe: likely overfit.")
            for t in result["trades"]:
                s.add(BacktestTrade(run_id=run_id, instrument_id=t["instrument_id"], segment=t["segment"],
                                    entry_date=t["entry_date"], entry_price=t["entry_price"],
                                    exit_date=t["exit_date"], exit_price=t["exit_price"], quantity=t["quantity"],
                                    entry_fees=t["entry_fees"], exit_fees=t["exit_fees"], net_pnl=t["net_pnl"],
                                    return_pct=t["return_pct"], bars_held=t["bars_held"],
                                    exit_reason=t["exit_reason"]))
            run_row.metrics = {**result["metrics"], "open_positions": json.loads(json.dumps(
                result["open_positions"], default=str))}
            run_row.equity_curve = result["equity_curve"]
            run_row.warnings = warnings
            run_row.data_fingerprint = fingerprint
            run_row.calc_version = BACKTEST_CALC_VERSION
            run_row.status = BacktestStatus.SUCCEEDED.value
        except Exception as exc:  # noqa: BLE001 - failure is persisted on the run and logged
            log.exception("backtest_failed", extra={"run_id": run_id})
            s.rollback()
            run_row = s.get(BacktestRun, run_id)
            assert run_row is not None
            run_row.status = BacktestStatus.FAILED.value
            run_row.error = f"{type(exc).__name__}: {exc}"
        run_row.finished_at = datetime.now(timezone.utc)


def process_queue(limit: int = 3) -> list[int]:
    """Claim queued runs with SKIP LOCKED (safe with multiple workers) and execute them."""
    done = []
    for _ in range(limit):
        with session_scope() as s:
            rid = s.execute(text(
                "SELECT id FROM backtest_runs WHERE status = 'queued' ORDER BY created_at "
                "FOR UPDATE SKIP LOCKED LIMIT 1")).scalar()
            if rid is None:
                break
            s.execute(text("UPDATE backtest_runs SET status='running', started_at=now() WHERE id=:i"), {"i": rid})
        execute_run(rid)
        done.append(rid)
    return done


__all__ = ["execute_run", "load_universe", "process_queue", "Any"]
