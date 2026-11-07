"""Strategy Lab: strategies, backtest queue, results and CSV reports."""

from __future__ import annotations

import csv
import io
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from nse_platform.api.deps import current_user, get_db, not_found
from nse_platform.backtesting.spec import ALL_FEATURES, FUNDAMENTAL_FEATURES, BacktestParams, StrategySpec
from nse_platform.models import BacktestRun, BacktestTrade, Instrument, Strategy, User

router = APIRouter(tags=["strategy-lab"])

TEMPLATES: list[dict[str, Any]] = [
    {"name": "Golden cross (SMA20 > SMA50)", "definition": {
        "entry": [{"left": "sma_20", "op": "crosses_above", "right": "sma_50"}],
        "exit": [{"left": "sma_20", "op": "crosses_below", "right": "sma_50"}], "stop_loss_pct": 0.12}},
    {"name": "RSI oversold recovery", "definition": {
        "entry": [{"left": "rsi_14", "op": "crosses_above", "right": 30}],
        "exit": [{"left": "rsi_14", "op": ">", "right": 65}], "stop_loss_pct": 0.1, "max_holding_bars": 40}},
    {"name": "Volume-confirmed 20-day breakout", "definition": {
        "entry": [{"left": "close", "op": ">", "right": "close_high_20_prior"},
                  {"left": "rel_volume", "op": ">=", "right": 1.5}],
        "exit": [{"left": "close", "op": "<", "right": "ema_20"}], "stop_loss_pct": 0.08}},
    {"name": "Value + trend (P/E < 8 and above SMA50)", "definition": {
        "entry": [{"left": "fund:pe_ratio", "op": "<", "right": 8}, {"left": "close", "op": ">", "right": "sma_50"}],
        "exit": [{"left": "close", "op": "<", "right": "sma_50"}], "max_holding_bars": 250}},
]


class StrategyIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(None, max_length=4000)
    definition: StrategySpec


class BacktestIn(BaseModel):
    strategy_id: int | None = None
    definition: StrategySpec | None = None
    params: BacktestParams


def _strategy_out(s: Strategy, runs: int = 0) -> dict[str, Any]:
    return {"id": s.id, "name": s.name, "description": s.description, "definition": s.definition,
            "version": s.version, "created_at": s.created_at, "updated_at": s.updated_at, "backtest_runs": runs}


@router.get("/strategies/features")
def features(_: User = Depends(current_user)) -> dict[str, Any]:
    return {"features": sorted(ALL_FEATURES), "fundamental_features": sorted(FUNDAMENTAL_FEATURES),
            "operators": [">", ">=", "<", "<=", "crosses_above", "crosses_below"], "templates": TEMPLATES,
            "notes": ["Conditions are evaluated at the bar close and executed at the next session.",
                      "Indicator values are split-adjusted; numeric price thresholds compare against adjusted prices.",
                      "fund:* features are point-in-time (only statements published by the bar date)."]}


@router.get("/strategies")
def strategies(db: Session = Depends(get_db), user: User = Depends(current_user)) -> list[dict[str, Any]]:
    counts = dict(db.execute(select(BacktestRun.strategy_id, func.count()).where(BacktestRun.user_id == user.id)
                             .group_by(BacktestRun.strategy_id)).all())
    return [_strategy_out(s, counts.get(s.id, 0)) for s in db.scalars(
        select(Strategy).where(Strategy.user_id == user.id).order_by(Strategy.updated_at.desc()))]


@router.post("/strategies")
def create_strategy(body: StrategyIn, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    s = Strategy(user_id=user.id, name=body.name, description=body.description,
                 definition=body.definition.model_dump(mode="json"))
    db.add(s)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, detail={"code": "CONFLICT", "message": "Strategy name already exists"}) from exc
    return _strategy_out(s)


@router.put("/strategies/{sid}")
def update_strategy(sid: int, body: StrategyIn, db: Session = Depends(get_db),
                    user: User = Depends(current_user)) -> dict:
    s = db.scalar(select(Strategy).where(Strategy.id == sid, Strategy.user_id == user.id))
    if s is None:
        raise not_found("Strategy")
    s.name, s.description = body.name, body.description
    s.definition = body.definition.model_dump(mode="json")
    s.version += 1  # earlier backtests keep their own strategy_snapshot, so they stay reproducible
    return _strategy_out(s)


@router.delete("/strategies/{sid}")
def delete_strategy(sid: int, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    s = db.scalar(select(Strategy).where(Strategy.id == sid, Strategy.user_id == user.id))
    if s is None:
        raise not_found("Strategy")
    db.delete(s)
    return {"ok": True}


@router.post("/backtests", status_code=202)
def queue_backtest(body: BacktestIn, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    if body.strategy_id:
        s = db.scalar(select(Strategy).where(Strategy.id == body.strategy_id, Strategy.user_id == user.id))
        if s is None:
            raise not_found("Strategy")
        try:
            spec = StrategySpec.model_validate(s.definition)
        except ValidationError as exc:
            raise HTTPException(400, detail={"code": "INVALID_STRATEGY", "message": str(exc)}) from exc
    elif body.definition:
        spec = body.definition
    else:
        raise HTTPException(400, detail={"code": "INVALID", "message": "Provide strategy_id or definition"})
    pending = db.scalar(select(func.count()).select_from(BacktestRun).where(
        BacktestRun.user_id == user.id, BacktestRun.status.in_(["queued", "running"])))
    if pending and pending >= 3:
        raise HTTPException(429, detail={"code": "TOO_MANY_RUNS", "message": "At most 3 queued/running backtests"})
    run = BacktestRun(user_id=user.id, strategy_id=body.strategy_id, strategy_snapshot=spec.model_dump(mode="json"),
                      params=body.params.model_dump(mode="json"), status="queued")
    db.add(run)
    db.flush()
    return {"id": run.id, "status": run.status,
            "note": "Backtests run in the background worker; poll GET /api/v1/backtests/{id}."}


def _run_out(r: BacktestRun, full: bool) -> dict[str, Any]:
    out = {"id": r.id, "strategy_id": r.strategy_id, "status": r.status, "created_at": r.created_at,
           "started_at": r.started_at, "finished_at": r.finished_at, "error": r.error, "params": r.params,
           "calc_version": r.calc_version, "data_fingerprint": r.data_fingerprint, "warnings": r.warnings or [],
           "headline": (r.metrics or {}).get("overall") if r.metrics else None}
    if full:
        out.update(strategy_snapshot=r.strategy_snapshot, metrics=r.metrics, equity_curve=r.equity_curve)
    return out


@router.get("/backtests")
def backtests(strategy_id: int | None = None, limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_db),
              user: User = Depends(current_user)) -> list[dict[str, Any]]:
    q = select(BacktestRun).where(BacktestRun.user_id == user.id)
    if strategy_id:
        q = q.where(BacktestRun.strategy_id == strategy_id)
    return [_run_out(r, False) for r in db.scalars(q.order_by(BacktestRun.created_at.desc()).limit(limit))]


def _get_run(db: Session, user: User, rid: int) -> BacktestRun:
    r = db.scalar(select(BacktestRun).where(BacktestRun.id == rid, BacktestRun.user_id == user.id))
    if r is None:
        raise not_found("Backtest")
    return r


@router.get("/backtests/{rid}")
def backtest(rid: int, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    r = _get_run(db, user, rid)
    trades = db.execute(select(BacktestTrade, Instrument.symbol).join(Instrument).where(BacktestTrade.run_id == r.id)
                        .order_by(BacktestTrade.entry_date)).all()
    return {**_run_out(r, True), "trades": [
        {"symbol": sym, "segment": t.segment, "entry_date": t.entry_date, "entry_price": t.entry_price,
         "exit_date": t.exit_date, "exit_price": t.exit_price, "quantity": t.quantity, "entry_fees": t.entry_fees,
         "exit_fees": t.exit_fees, "net_pnl": t.net_pnl, "return_pct": t.return_pct, "bars_held": t.bars_held,
         "exit_reason": t.exit_reason} for t, sym in trades],
        "disclaimer": "Historical simulation. Past performance does not indicate future results."}


@router.get("/backtests/{rid}/report.csv")
def backtest_csv(rid: int, db: Session = Depends(get_db), user: User = Depends(current_user)) -> StreamingResponse:
    r = _get_run(db, user, rid)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["# backtest", r.id, "calc_version", r.calc_version, "data_fingerprint", r.data_fingerprint])
    for seg, m in ((r.metrics or {}).get("segments") or {}).items():
        w.writerow(["# segment", seg, *(f"{k}={v}" for k, v in m.items())])
    w.writerow(["symbol", "segment", "entry_date", "entry_price", "exit_date", "exit_price", "quantity",
                "entry_fees", "exit_fees", "net_pnl", "return_pct", "bars_held", "exit_reason"])
    for t, sym in db.execute(select(BacktestTrade, Instrument.symbol).join(Instrument).where(
            BacktestTrade.run_id == r.id).order_by(BacktestTrade.entry_date)).all():
        w.writerow([sym, t.segment, t.entry_date, t.entry_price, t.exit_date, t.exit_price, t.quantity, t.entry_fees,
                    t.exit_fees, t.net_pnl, t.return_pct, t.bars_held, t.exit_reason])
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="backtest-{r.id}.csv"'})
