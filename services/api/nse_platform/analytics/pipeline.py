"""Analytics pipeline: precomputes everything the API serves (no heavy computation on request paths).

Per instrument (per timeframe): indicators, trend classification, support/resistance, opportunity signals,
signal outcomes, snapshot metrics, point-in-time fundamentals. Then per data universe (real vs synthetic, never
mixed): relative strength, scores, fundamental/event signals and signal base rates.
"""

from __future__ import annotations

import logging
import math
from datetime import date, datetime, timedelta, timezone
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import case, delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from nse_platform.analytics import indicators as ind_lib
from nse_platform.analytics.scores import SCORE_CALC_VERSION, compute_scores
from nse_platform.analytics.series import adjustment_events, apply_adjustments, load_daily, resample
from nse_platform.analytics.trend import TREND_CALC_VERSION, classify, support_resistance
from nse_platform.core.db import advisory_lock, session_scope
from nse_platform.fundamentals.metrics import FUNDAMENTALS_CALC_VERSION, compute, load_dividends, load_statements
from nse_platform.models import (
    CorporateAction,
    DataQualityIssue,
    FundamentalMetric,
    Instrument,
    InstrumentSnapshot,
    PriceBar,
    SupportResistanceLevel,
    TechnicalIndicatorRow,
    TrendAnalysis,
)
from nse_platform.models.enums import CorporateActionType, RunStatus, SecurityType, TradingStatus
from nse_platform.opportunities import service as opp

log = logging.getLogger(__name__)
TIMEFRAMES = ("1d", "1w", "1mo")
PRICED = (SecurityType.EQUITY.value, SecurityType.PREFERENCE.value, SecurityType.ETF.value, SecurityType.REIT.value)
SIGNAL_TIMEFRAMES = ("1d", "1w")


def _clean(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _utc(ts: pd.Timestamp) -> datetime:
    return datetime(ts.year, ts.month, ts.day, tzinfo=timezone.utc)


def _store_indicators(session: Session, iid: int, tf: str, ind: pd.DataFrame) -> None:
    stored = session.scalar(select(func.count()).select_from(TechnicalIndicatorRow).where(
        TechnicalIndicatorRow.instrument_id == iid, TechnicalIndicatorRow.timeframe == tf,
        TechnicalIndicatorRow.calc_version == ind_lib.INDICATOR_CALC_VERSION)) or 0
    frame = ind if stored < len(ind) - 5 else ind.iloc[-30:]
    cols = list(frame.columns)
    rows = []
    for ts, values in zip(frame.index, frame.to_numpy(dtype=float), strict=True):
        rows.append({"instrument_id": iid, "timeframe": tf, "ts": _utc(ts),
                     "calc_version": ind_lib.INDICATOR_CALC_VERSION,
                     "values": {c: (None if not np.isfinite(v) else round(float(v), 6)) for c, v in zip(cols, values,
                                                                                                         strict=True)}})
    for i in range(0, len(rows), 500):
        stmt = insert(TechnicalIndicatorRow).values(rows[i : i + 500])
        session.execute(stmt.on_conflict_do_update(
            index_elements=["instrument_id", "timeframe", "ts", "calc_version"],
            set_={"values": stmt.excluded["values"], "computed_at": func.now()}))


def _snapshot_metrics(raw: pd.DataFrame, adj: pd.DataFrame, ind: pd.DataFrame) -> dict[str, Any]:
    c = adj["close"]
    last = c.iloc[-1]

    def ret(n: int) -> float | None:
        return _clean(last / c.iloc[-1 - n] - 1) if len(c) > n else None

    prior_year = c[c.index.year < c.index[-1].year]
    vol = adj["volume"]
    traded = (vol > 0).astype(float).where(vol.notna())
    tv = (adj["close"] * vol).iloc[-20:]
    one_year = c.iloc[-252:]
    gaps = (adj["open"] / c.shift(1) - 1).abs().iloc[-252:]
    cc = (c / c.shift(1) - 1).abs().iloc[-252:]
    gap_count = int(((gaps > 0.08) | (gaps.isna() & (cc > 0.10))).sum())
    i = ind.iloc[-1]
    high_52w = _clean(i.get("high_52w"))
    return {
        "bars_available": int(len(c)),
        "last_close": _clean(raw["close"].iloc[-1]),
        "prev_close": _clean(raw["close"].iloc[-2]) if len(raw) > 1 else None,
        "change_pct": _clean(adj["close"].iloc[-1] / adj["close"].iloc[-2] - 1) if len(adj) > 1 else None,
        "volume": int(raw["volume"].iloc[-1]) if pd.notna(raw["volume"].iloc[-1]) else None,
        "avg_volume_20": _clean(i.get("vol_sma_20")),
        "relative_volume": _clean(i.get("rel_volume")),
        "avg_traded_value_20": _clean(tv.mean()) if tv.notna().sum() == 20 else None,
        "trading_frequency_60": _clean(traded.iloc[-60:].mean()) if traded.iloc[-60:].notna().sum() >= 40 else None,
        "rsi_14": _clean(i.get("rsi_14")),
        "return_1w": ret(5), "return_1m": ret(21), "return_3m": ret(63), "return_6m": ret(126), "return_1y": ret(252),
        "return_ytd": _clean(last / prior_year.iloc[-1] - 1) if not prior_year.empty else None,
        "volatility_20": _clean(i.get("hv_20")), "volatility_60": _clean(i.get("hv_60")),
        "atr_pct_14": _clean(i.get("atr_pct_14")),
        "max_drawdown_1y": _clean((one_year / one_year.cummax() - 1).min()) if len(one_year) >= 120 else None,
        "drawdown_from_high": _clean(last / high_52w - 1) if high_52w else None,
        "high_52w": high_52w, "low_52w": _clean(i.get("low_52w")),
        "_gap_count_1y": gap_count if len(c) >= 250 else None,
    }


def analyse_instrument(session: Session, inst: Instrument, replay: bool) -> dict[str, Any] | None:
    raw = load_daily(session, inst)
    if raw.empty or len(raw) < 2:
        return None
    events = adjustment_events(session, inst.id)
    adj = apply_adjustments(raw, events)
    as_of = raw.index[-1].date()
    out: dict[str, Any] = {"instrument_id": inst.id, "as_of": as_of, "signals": {}}
    daily_ind = None
    trade_freq = None
    for tf in TIMEFRAMES:
        bars = resample(adj, tf)
        if tf != "1d":
            bars = bars[~bars["is_partial"]]  # classify/score only completed weekly/monthly bars
        if len(bars) < 2:
            continue
        ind = ind_lib.compute_all(bars, tf)
        _store_indicators(session, inst.id, tf, ind)
        if tf == "1d":
            daily_ind = ind
            out["metrics"] = _snapshot_metrics(raw, adj, ind)
            trade_freq = out["metrics"]["trading_frequency_60"]
        trend = classify(bars, ind, trade_freq if tf == "1d" else None)
        last_ts = _utc(bars.index[-1])
        stmt = insert(TrendAnalysis).values(
            instrument_id=inst.id, timeframe=tf, as_of_ts=last_ts, calc_version=TREND_CALC_VERSION,
            classification=trend.classification.value, strength=trend.strength, evidence=trend.evidence)
        session.execute(stmt.on_conflict_do_update(
            index_elements=["instrument_id", "timeframe", "as_of_ts", "calc_version"],
            set_={"classification": stmt.excluded.classification, "strength": stmt.excluded.strength,
                  "evidence": stmt.excluded.evidence, "computed_at": func.now()}))
        out[f"trend_{tf}"] = trend.classification.value
        if len(bars) >= 60 and tf in ("1d", "1w"):
            session.execute(delete(SupportResistanceLevel).where(
                SupportResistanceLevel.instrument_id == inst.id, SupportResistanceLevel.timeframe == tf))
            for lv in support_resistance(bars, ind):
                session.add(SupportResistanceLevel(
                    instrument_id=inst.id, timeframe=tf, as_of_ts=last_ts, level_type=lv["level_type"],
                    price=lv["price"], touches=lv["touches"], first_touch_ts=_utc(lv["first_touch_ts"]),
                    last_touch_ts=_utc(lv["last_touch_ts"]), calc_version=TREND_CALC_VERSION))
        if tf in SIGNAL_TIMEFRAMES and len(bars) >= 60:
            out["signals"][tf] = opp.run_for_instrument(session, inst, tf, bars, ind, replay)
    if daily_ind is None:
        return None
    # Point-in-time fundamentals at the last session date, on raw price.
    statements = load_statements(session, inst.id)
    dividends = load_dividends(session, inst.id)
    fsnap = compute(statements, dividends, as_of, float(raw["close"].iloc[-1]),
                    ratio_events=[(e.ex_date, e.factor) for e in events])
    for k, v in fsnap.metrics.items():
        stmt = insert(FundamentalMetric).values(
            instrument_id=inst.id, metric=k, as_of=as_of, calc_version=FUNDAMENTALS_CALC_VERSION, value=_clean(v),
            period_end=fsnap.period_end, inputs={**fsnap.inputs, "sources": fsnap.sources})
        session.execute(stmt.on_conflict_do_update(
            index_elements=["instrument_id", "metric", "as_of", "calc_version"],
            set_={"value": stmt.excluded.value, "period_end": stmt.excluded.period_end,
                  "inputs": stmt.excluded.inputs, "computed_at": func.now()}))
    out["fundamentals"] = fsnap.metrics
    out["closes"] = adj["close"]
    out["bar_ts"] = _utc(raw.index[-1])
    return out


def _universe_scores(session: Session, results: list[dict[str, Any]], synthetic: bool) -> None:
    if not results:
        return
    idx = [r["instrument_id"] for r in results]
    frame = pd.DataFrame(index=idx)
    m = {r["instrument_id"]: r["metrics"] for r in results}
    fm = {r["instrument_id"]: r["fundamentals"] for r in results}
    for key in ("return_3m", "volatility_60", "max_drawdown_1y", "relative_volume"):
        frame[key] = [m[i].get(key) for i in idx]
    frame["trend_class"] = [r.get("trend_1d") for r in results]
    frame["proximity_52w_high"] = [
        (m[i]["last_close"] / m[i]["high_52w"]) if m[i].get("high_52w") and m[i].get("last_close") else None
        for i in idx]
    frame["liquidity"] = [
        (m[i]["trading_frequency_60"] or 0) * (m[i]["avg_traded_value_20"] or 0)
        if m[i].get("trading_frequency_60") is not None and m[i].get("avg_traded_value_20") is not None else None
        for i in idx]
    frame["illiquidity"] = [1 - m[i]["trading_frequency_60"] if m[i].get("trading_frequency_60") is not None
                            else None for i in idx]
    frame["gap_count_1y"] = [m[i].get("_gap_count_1y") for i in idx]
    r3 = pd.to_numeric(frame["return_3m"], errors="coerce")
    frame["relative_strength_3m"] = (r3.rank(pct=True) * 100) if r3.notna().sum() >= 5 else np.nan
    for key in ("revenue_growth", "earnings_growth", "net_margin", "roe", "debt_to_equity", "cash_conversion",
                "pe_ratio", "pb_ratio", "dividend_yield", "dividend_years_streak"):
        frame[key] = [fm[i].get(key) for i in idx]

    # Event & data risk.
    year_ago = date.today() - timedelta(days=365)
    insts = {i.id: i for i in session.scalars(select(Instrument).where(Instrument.id.in_(idx)))}
    susp_events = {r[0] for r in session.execute(select(CorporateAction.instrument_id).where(
        CorporateAction.instrument_id.in_(idx), CorporateAction.action_type == CorporateActionType.SUSPENSION.value,
        (CorporateAction.effective_date >= year_ago) | (CorporateAction.announced_at >= datetime.combine(
            year_ago, datetime.min.time(), timezone.utc))))}
    frame["event_risk"] = [100.0 if insts[i].trading_status == TradingStatus.SUSPENDED.value else
                           60.0 if i in susp_events else 0.0 for i in idx]
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    issues = dict(session.execute(select(DataQualityIssue.instrument_id, func.count()).where(
        DataQualityIssue.instrument_id.in_(idx), DataQualityIssue.resolved_at.is_(None),
        DataQualityIssue.severity != "info", DataQualityIssue.detected_at >= cutoff)
        .group_by(DataQualityIssue.instrument_id)).all())
    partial_share = dict(session.execute(
        select(PriceBar.instrument_id, func.avg(case((PriceBar.quality_status != "OK", 1.0), else_=0.0)))
        .where(PriceBar.instrument_id.in_(idx), PriceBar.timeframe == "1d",
               PriceBar.ts >= datetime.now(timezone.utc) - timedelta(days=120))
        .group_by(PriceBar.instrument_id)).all())
    frame["data_risk"] = [min(100.0, 100 * float(partial_share.get(i) or 0) + 10 * issues.get(i, 0)) for i in idx]

    scores = compute_scores(frame)
    for r in results:
        iid = r["instrument_id"]
        met = {k: v for k, v in r["metrics"].items() if not k.startswith("_")}
        sc = scores[iid]
        values = {
            "instrument_id": iid, "timeframe": "1d", "as_of_ts": r["bar_ts"], "session_date": r["as_of"],
            "calc_version": SCORE_CALC_VERSION, **met,
            "relative_strength_3m": _clean(frame.at[iid, "relative_strength_3m"]),
            "dividend_yield": _clean(r["fundamentals"].get("dividend_yield")),
            "pe_ratio": _clean(r["fundamentals"].get("pe_ratio")),
            "trend_class": r.get("trend_1d"),
            "trading_score": sc["trading"]["score"], "quality_score": sc["quality"]["score"],
            "risk_score": sc["risk"]["score"], "score_details": sc,
        }
        stmt = insert(InstrumentSnapshot).values(values)
        session.execute(stmt.on_conflict_do_update(
            index_elements=["instrument_id", "timeframe"],
            set_={k: stmt.excluded[k] for k in values if k not in ("instrument_id", "timeframe")}
            | {"computed_at": func.now()}))

    # Fundamental / dividend / event signals (latest only).
    sector_pe: dict[int | None, list[float]] = {}
    for r in results:
        pe = r["fundamentals"].get("pe_ratio")
        if pe:
            sector_pe.setdefault(insts[r["instrument_id"]].sector_id, []).append(pe)
    today = date.today()
    upcoming = {row[0]: (row[1], row[2]) for row in session.execute(
        select(CorporateAction.instrument_id, CorporateAction.ex_date, CorporateAction.cash_amount).where(
            CorporateAction.instrument_id.in_(idx), CorporateAction.action_type == "DIVIDEND_CASH",
            CorporateAction.ex_date > today, CorporateAction.ex_date <= today + timedelta(days=30),
            CorporateAction.cash_amount.is_not(None)))}
    for r in results:
        inst = insts[r["instrument_id"]]
        peers = sector_pe.get(inst.sector_id, [])
        med = float(np.median(peers)) if len(peers) >= 3 else None
        opp.fundamental_signals(session, inst, r["fundamentals"], med, upcoming.get(inst.id), r["bar_ts"],
                                r["metrics"]["last_close"] or 0.0, issues.get(inst.id, 0),
                                inst.trading_status == TradingStatus.SUSPENDED.value)
    opp.compute_base_rates(session, "synthetic" if synthetic else "real",
                           {r["instrument_id"]: r["closes"] for r in results})


def run_analytics(replay: bool = False, instrument_ids: list[int] | None = None) -> dict[str, Any]:
    summary: dict[str, Any] = {"status": RunStatus.SUCCEEDED.value, "instruments": 0, "failed": [], "replay": replay}
    with advisory_lock("job:analytics") as acquired:
        if not acquired:
            return {"status": RunStatus.SKIPPED.value}
        for synthetic in (False, True):
            with session_scope() as s:
                q = select(Instrument).where(Instrument.is_synthetic.is_(synthetic), Instrument.security_type.in_(PRICED),
                                             Instrument.primary_provider_id.is_not(None))
                if instrument_ids:
                    q = q.where(Instrument.id.in_(instrument_ids))
                ids = [i.id for i in s.scalars(q)]
            results = []
            for iid in ids:
                try:
                    with session_scope() as s:
                        inst = s.get(Instrument, iid)
                        assert inst is not None
                        r = analyse_instrument(s, inst, replay)
                    if r:
                        results.append(r)
                        summary["instruments"] += 1
                except Exception as exc:  # noqa: BLE001 - logged and reported in summary; others continue
                    log.exception("analytics_instrument_failed", extra={"instrument_id": iid})
                    summary["failed"].append({"instrument_id": iid, "error": f"{type(exc).__name__}: {exc}"})
            if results:
                with session_scope() as s:
                    _universe_scores(s, results, synthetic)
    if summary["failed"]:
        summary["status"] = RunStatus.PARTIAL.value
    return summary


__all__ = ["analyse_instrument", "run_analytics"]
