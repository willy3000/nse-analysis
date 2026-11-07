"""Signal persistence, lifecycle (ACTIVE -> INVALIDATED / EXPIRED), outcome tracking and base rates."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from nse_platform.models import AnalyticsReport, Instrument, OpportunityOutcome, OpportunitySignal
from nse_platform.models.enums import SignalCategory, SignalDirection, SignalStatus
from nse_platform.opportunities.detectors import SIGNAL_CALC_VERSION, Detection, detect, features, risk_factors

HORIZONS = (5, 10, 20, 60)
MIN_SAMPLE = 30
DAYS_PER_BAR = {"1d": 7 / 5, "1w": 7.0, "1mo": 30.5}  # calendar days per bar (5 sessions per week)


def _ts(t: pd.Timestamp) -> datetime:
    return datetime(t.year, t.month, t.day, tzinfo=timezone.utc)


def _status_after(det: Detection, f: pd.DataFrame) -> tuple[SignalStatus, datetime | None]:
    """Evaluate a signal's lifecycle against bars after detection (for replayed and existing signals)."""
    pos = f.index.get_loc(det.ts)
    after = f.iloc[pos + 1 :]
    inv = det.detector.invalidation(det.row)
    price = inv.get("price")
    if price is not None and not after.empty:
        hit = after["close"] < price if inv["type"] == "close_below" else after["close"] > price
        within = hit.iloc[: det.detector.expiry_bars]
        if within.any():
            return SignalStatus.INVALIDATED, _ts(within.idxmax())
    if len(after) >= det.detector.expiry_bars:
        return SignalStatus.EXPIRED, _ts(after.index[det.detector.expiry_bars - 1])
    return SignalStatus.ACTIVE, None


def store_detections(session: Session, instrument_id: int, timeframe: str, f: pd.DataFrame,
                     detections: list[Detection], is_backfill: bool) -> int:
    rows = []
    for d in detections:
        status, changed = _status_after(d, f)
        bar_ts = _ts(d.ts)
        rows.append({
            "instrument_id": instrument_id, "category": d.detector.category.value,
            "signal_type": d.detector.signal_type, "direction": d.detector.direction.value, "timeframe": timeframe,
            "bar_ts": bar_ts, "reference_price": float(d.row["close"]),
            "evidence": [{"label": "Rule", "value": d.detector.title, "detail": d.detector.description},
                         *d.detector.evidence(d.row)],
            "invalidation": d.detector.invalidation(d.row), "risk_factors": risk_factors(d.row),
            "status": status.value, "status_changed_at": changed,
            "expires_at": bar_ts + timedelta(days=int(d.detector.expiry_bars * DAYS_PER_BAR.get(timeframe, 1.4)) + 1),
            "is_backfill": is_backfill, "calc_version": SIGNAL_CALC_VERSION,
        })
    n = 0
    for i in range(0, len(rows), 500):
        stmt = insert(OpportunitySignal).values(rows[i : i + 500]).on_conflict_do_nothing(
            constraint="uq_opportunity_signals_instrument_id")
        n += session.execute(stmt).rowcount or 0
    return n


def refresh_statuses(session: Session, instrument_id: int, timeframe: str, f: pd.DataFrame) -> None:
    active = session.scalars(select(OpportunitySignal).where(
        OpportunitySignal.instrument_id == instrument_id, OpportunitySignal.timeframe == timeframe,
        OpportunitySignal.status == SignalStatus.ACTIVE.value, OpportunitySignal.category != "VALUE",
        OpportunitySignal.category != "DIVIDEND")).all()
    if not active or f.empty:
        return
    for s in active:
        ts = pd.Timestamp(s.bar_ts.date())
        if ts not in f.index:
            continue
        after = f.loc[f.index > ts]
        price = (s.invalidation or {}).get("price")
        typ = (s.invalidation or {}).get("type")
        if price is not None and typ in ("close_below", "close_above") and not after.empty:
            hit = after["close"] < price if typ == "close_below" else after["close"] > price
            if hit.any():
                s.status, s.status_changed_at = SignalStatus.INVALIDATED.value, _ts(hit.idxmax())
                continue
        if datetime.now(timezone.utc) > s.expires_at:
            s.status, s.status_changed_at = SignalStatus.EXPIRED.value, datetime.now(timezone.utc)


def record_outcomes(session: Session, instrument_id: int, timeframe: str, f: pd.DataFrame) -> int:
    """Forward returns for signals whose horizon has elapsed. Uses adjusted closes; idempotent."""
    sigs = session.scalars(select(OpportunitySignal).where(
        OpportunitySignal.instrument_id == instrument_id, OpportunitySignal.timeframe == timeframe)).all()
    if not sigs:
        return 0
    done = {(o.signal_id, o.horizon_bars) for o in session.scalars(select(OpportunityOutcome).where(
        OpportunityOutcome.signal_id.in_([s.id for s in sigs])))}
    closes = f["close"]
    n = 0
    for s in sigs:
        ts = pd.Timestamp(s.bar_ts.date())
        if ts not in closes.index:
            continue
        pos = closes.index.get_loc(ts)
        base = float(closes.iloc[pos])
        inv = s.invalidation or {}
        for h in HORIZONS:
            if (s.id, h) in done or pos + h >= len(closes):
                continue
            window = closes.iloc[pos + 1 : pos + h + 1]
            rets = window / base - 1
            sign = -1.0 if s.direction == SignalDirection.BEARISH.value else 1.0
            price = inv.get("price")
            invalidated = False
            if price is not None and inv.get("type") == "close_below":
                invalidated = bool((window < price).any())
            elif price is not None and inv.get("type") == "close_above":
                invalidated = bool((window > price).any())
            session.add(OpportunityOutcome(
                signal_id=s.id, horizon_bars=h, end_ts=_ts(window.index[-1]),
                forward_return=float(rets.iloc[-1]), max_favorable=float((sign * rets).max()),
                max_adverse=float((sign * rets).min()), invalidated=invalidated,
            ))
            n += 1
    return n


def run_for_instrument(session: Session, instrument: Instrument, timeframe: str, bars: pd.DataFrame,
                       ind: pd.DataFrame, replay: bool) -> dict[str, int]:
    f = features(bars, ind)
    if replay:
        session.execute(delete(OpportunitySignal).where(
            OpportunitySignal.instrument_id == instrument.id, OpportunitySignal.timeframe == timeframe,
            OpportunitySignal.is_backfill.is_(True), OpportunitySignal.calc_version != SIGNAL_CALC_VERSION))
        dets = detect(f, only_last=False)
        # The most recent bar's detections are "live", older ones are historical replay.
        last = f.index[-1]
        n_hist = store_detections(session, instrument.id, timeframe, f, [d for d in dets if d.ts != last], True)
        n_live = store_detections(session, instrument.id, timeframe, f, [d for d in dets if d.ts == last], False)
    else:
        n_hist = 0
        n_live = store_detections(session, instrument.id, timeframe, f, detect(f, only_last=True), False)
    refresh_statuses(session, instrument.id, timeframe, f)
    session.flush()
    n_out = record_outcomes(session, instrument.id, timeframe, f)
    return {"replayed": n_hist, "live": n_live, "outcomes": n_out}


def baseline_forward_returns(closes_by_instrument: dict[int, pd.Series], horizon: int) -> np.ndarray:
    vals = []
    for c in closes_by_instrument.values():
        fr = (c.shift(-horizon) / c - 1).dropna()
        vals.append(fr.to_numpy())
    return np.concatenate(vals) if vals else np.array([])


def compute_base_rates(session: Session, universe: str, closes_by_instrument: dict[int, pd.Series]) -> dict[str, Any]:
    synthetic = universe == "synthetic"
    rows = session.execute(
        select(OpportunitySignal.signal_type, OpportunitySignal.timeframe, OpportunitySignal.direction,
               OpportunityOutcome.horizon_bars, OpportunityOutcome.forward_return, OpportunityOutcome.invalidated,
               OpportunityOutcome.max_adverse)
        .join(OpportunityOutcome, OpportunityOutcome.signal_id == OpportunitySignal.id)
        .join(Instrument, Instrument.id == OpportunitySignal.instrument_id)
        .where(Instrument.is_synthetic.is_(synthetic), OpportunitySignal.calc_version == SIGNAL_CALC_VERSION)
    ).all()
    df = pd.DataFrame(rows, columns=["signal_type", "timeframe", "direction", "horizon", "ret", "invalidated", "mae"])
    baselines = {h: baseline_forward_returns(closes_by_instrument, h) for h in HORIZONS}
    stats: list[dict[str, Any]] = []
    if not df.empty:
        for (st, tf, direction, h), g in df.groupby(["signal_type", "timeframe", "direction", "horizon"]):
            r = g["ret"].to_numpy()
            sign = -1 if direction == "BEARISH" else 1
            base = baselines.get(int(h), np.array([]))
            n = len(r)
            mean = float(r.mean())
            sd = float(r.std(ddof=1)) if n > 1 else float("nan")
            stats.append({
                "signal_type": st, "timeframe": tf, "direction": direction, "horizon_bars": int(h), "n": n,
                "mean_return": mean, "median_return": float(np.median(r)),
                "hit_rate": float((sign * r > 0).mean()),
                "invalidation_rate": float(g["invalidated"].mean()),
                "median_max_adverse": float(np.median(g["mae"])),
                "baseline_mean_return": float(base.mean()) if base.size else None,
                "baseline_hit_rate": float((sign * base > 0).mean()) if base.size else None,
                "excess_mean_return": mean - float(base.mean()) if base.size else None,
                "t_stat_naive": mean / (sd / np.sqrt(n)) if n > 1 and sd > 0 else None,
                "sufficient_sample": n >= MIN_SAMPLE,
            })
    payload = {
        "universe": universe,
        "min_sample": MIN_SAMPLE,
        "method": "Forward returns of every detected signal (live and look-ahead-free historical replay) measured "
                  "on split-adjusted closes, compared with the unconditional forward return of all bars of the "
                  "same instruments. Excludes transaction costs (~4% round trip) and dividends. Overlapping "
                  "signals are not independent, so the naive t-statistic overstates significance.",
        "stats": stats,
    }
    stmt = insert(AnalyticsReport).values(code="signal_base_rates", universe=universe,
                                          calc_version=SIGNAL_CALC_VERSION, payload=payload)
    session.execute(stmt.on_conflict_do_update(index_elements=["code", "universe"], set_={
        "payload": stmt.excluded.payload, "calc_version": stmt.excluded.calc_version, "computed_at": datetime.now(
            timezone.utc)}))
    return payload


# --------------------------------------------------------------------------- fundamental / event signals


def fundamental_signals(session: Session, instrument: Instrument, snapshot: dict[str, Any],
                        sector_median_pe: float | None, upcoming_ex: tuple[date, float] | None,
                        bar_ts: datetime, close: float, open_issue_count: int, suspended: bool) -> int:
    """Latest-only signals derived from fundamentals, dividends, corporate events and data quality."""
    items: list[dict[str, Any]] = []
    m = snapshot
    pe, roe, eg = m.get("pe_ratio"), m.get("roe"), m.get("earnings_growth")
    if pe and sector_median_pe and pe < 0.75 * sector_median_pe and (roe or 0) > 0.10 and (eg or 0) > 0:
        items.append(dict(cat=SignalCategory.VALUE, typ="VALUE_DISCOUNT", dir=SignalDirection.BULLISH,
                          ev=[("P/E", pe, ""), ("Sector median P/E", sector_median_pe, "same data universe"),
                              ("ROE", roe, ""), ("Earnings growth", eg, "")],
                          inv={"type": "none", "price": None, "condition": "Re-evaluated when new results publish"},
                          days=90))
    dy, streak, payout = m.get("dividend_yield"), m.get("dividend_years_streak"), m.get("payout_ratio")
    if dy and dy >= 0.06 and (streak or 0) >= 3 and (payout is None or payout < 0.9):
        items.append(dict(cat=SignalCategory.DIVIDEND, typ="HIGH_DIVIDEND_YIELD", dir=SignalDirection.BULLISH,
                          ev=[("Trailing dividend yield", dy, ""), ("Consecutive dividend years", streak, ""),
                              ("Payout ratio", payout, "unavailable" if payout is None else "")],
                          inv={"type": "none", "price": None, "condition": "Dividend cut or yield below 6%"},
                          days=60))
    if upcoming_ex:
        ex, amt = upcoming_ex
        items.append(dict(cat=SignalCategory.DIVIDEND, typ="UPCOMING_EX_DIVIDEND", dir=SignalDirection.NEUTRAL,
                          ev=[("Ex-dividend date", str(ex), ""), ("Dividend per share", amt, ""),
                              ("Implied yield on this payment", amt / close if close else None, "")],
                          inv={"type": "none", "price": None, "condition": f"Expires on ex-date {ex}"},
                          days=max(1, (ex - bar_ts.date()).days)))
    if suspended:
        items.append(dict(cat=SignalCategory.RISK, typ="TRADING_SUSPENDED", dir=SignalDirection.BEARISH,
                          ev=[("Trading status", "SUSPENDED", "")],
                          inv={"type": "none", "price": None, "condition": "Until trading resumes"}, days=30))
    if open_issue_count >= 3:
        items.append(dict(cat=SignalCategory.RISK, typ="DATA_QUALITY", dir=SignalDirection.NEUTRAL,
                          ev=[("Open data-quality issues (30 days)", open_issue_count, "see Data Centre")],
                          inv={"type": "none", "price": None, "condition": "Until issues are resolved"}, days=7))
    n = 0
    for it in items:
        values = {
            "instrument_id": instrument.id, "category": it["cat"].value, "signal_type": it["typ"],
            "direction": it["dir"].value, "timeframe": "1d", "bar_ts": bar_ts, "reference_price": close,
            "evidence": [{"label": a, "value": b, "detail": c} for a, b, c in it["ev"]],
            "invalidation": it["inv"], "risk_factors": [], "status": SignalStatus.ACTIVE.value,
            "expires_at": bar_ts + timedelta(days=it["days"]), "is_backfill": False,
            "calc_version": SIGNAL_CALC_VERSION,
        }
        res = session.execute(insert(OpportunitySignal).values(values).on_conflict_do_nothing(
            constraint="uq_opportunity_signals_instrument_id"))
        n += res.rowcount or 0
    return n
