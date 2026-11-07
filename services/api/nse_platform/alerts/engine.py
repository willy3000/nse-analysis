"""Alert evaluation. Runs in the worker after each analytics pass.

* Level rules (price/RSI/risk) are edge-triggered: they fire when the condition becomes true, not on every run
  while it stays true (state kept in alert_rules.last_state).
* Each firing has a dedupe key (rule + observation) enforced by a unique constraint, plus a cooldown.
* Notification channels are pluggable; in-app is always available, email only when SMTP is configured.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from nse_platform.core.db import advisory_lock, session_scope
from nse_platform.models import (
    AlertEvent,
    AlertRule,
    CorporateAction,
    FundamentalMetric,
    Instrument,
    InstrumentSnapshot,
    NewsArticle,
    NewsArticleInstrument,
    Notification,
    OpportunitySignal,
    TechnicalIndicatorRow,
)

log = logging.getLogger(__name__)


class Notifier(Protocol):
    channel: str

    def send(self, db: Session, user_id: int, title: str, body: str, link: str | None, event_id: int) -> None: ...


class InAppNotifier:
    channel = "in_app"

    def send(self, db: Session, user_id: int, title: str, body: str, link: str | None, event_id: int) -> None:
        db.add(Notification(user_id=user_id, channel="in_app", title=title, body=body, link=link,
                            alert_event_id=event_id, delivered_at=datetime.now(timezone.utc)))


NOTIFIERS: dict[str, Notifier] = {"in_app": InAppNotifier()}


def _latest_two_indicators(db: Session, iid: int) -> list[dict[str, Any]]:
    rows = db.scalars(select(TechnicalIndicatorRow).where(TechnicalIndicatorRow.instrument_id == iid,
                                                          TechnicalIndicatorRow.timeframe == "1d")
                      .order_by(TechnicalIndicatorRow.ts.desc()).limit(2)).all()
    return [{"ts": r.ts, **r.values} for r in rows]


def _evaluate(db: Session, rule: AlertRule, inst: Instrument | None) -> tuple[bool, str, str, dict[str, Any]] | None:
    """Returns (fire, dedupe_key, message, new_state) or None when inputs are unavailable."""
    p = rule.params or {}
    rt = rule.rule_type
    snap = db.scalar(select(InstrumentSnapshot).where(InstrumentSnapshot.instrument_id == inst.id,
                                                      InstrumentSnapshot.timeframe == "1d")) if inst else None
    sym = inst.symbol if inst else "market"
    if rt in ("PRICE_ABOVE", "PRICE_BELOW", "RSI_ABOVE", "RSI_BELOW", "RISK_SCORE_ABOVE"):
        if snap is None:
            return None
        value = {"PRICE_ABOVE": snap.last_close, "PRICE_BELOW": snap.last_close, "RSI_ABOVE": snap.rsi_14,
                 "RSI_BELOW": snap.rsi_14, "RISK_SCORE_ABOVE": snap.risk_score}[rt]
        if value is None:
            return None
        level = float(p.get("price", p.get("level")))
        cond = value > level if rt.endswith("ABOVE") else value < level
        prev = (rule.last_state or {}).get("cond")
        fire = cond and prev is not True
        label = {"PRICE_ABOVE": "closed above", "PRICE_BELOW": "closed below", "RSI_ABOVE": "RSI(14) rose above",
                 "RSI_BELOW": "RSI(14) fell below", "RISK_SCORE_ABOVE": "risk score rose above"}[rt]
        return fire, f"{rt}:{snap.session_date}", f"{sym} {label} {level:g} (now {value:.2f}, session " \
                                                  f"{snap.session_date})", {"cond": cond}
    if rt in ("SMA_CROSS_UP", "SMA_CROSS_DOWN"):
        rows = _latest_two_indicators(db, inst.id) if inst else []
        if len(rows) < 2 or None in (rows[0].get("sma_20"), rows[0].get("sma_50"), rows[1].get("sma_20"),
                                     rows[1].get("sma_50")):
            return None
        up = rows[0]["sma_20"] > rows[0]["sma_50"] and rows[1]["sma_20"] <= rows[1]["sma_50"]
        down = rows[0]["sma_20"] < rows[0]["sma_50"] and rows[1]["sma_20"] >= rows[1]["sma_50"]
        fire = up if rt == "SMA_CROSS_UP" else down
        return fire, f"{rt}:{rows[0]['ts'].date()}", f"{sym}: SMA20 crossed {'above' if up else 'below'} SMA50 " \
                                                     f"({rows[0]['ts'].date()})", {}
    if rt == "VOLUME_SPIKE":
        if snap is None or snap.relative_volume is None:
            return None
        mult = float(p["multiple"])
        return (snap.relative_volume >= mult, f"{rt}:{snap.session_date}",
                f"{sym} traded {snap.relative_volume:.1f}x its 20-session average volume on {snap.session_date}", {})
    since = rule.last_triggered_at or rule.created_at
    if rt == "SIGNAL_DETECTED":
        q = select(OpportunitySignal, Instrument.symbol).join(Instrument).where(
            OpportunitySignal.is_backfill.is_(False), OpportunitySignal.detected_at > since)
        if inst:
            q = q.where(OpportunitySignal.instrument_id == inst.id)
        if p.get("signal_type"):
            q = q.where(OpportunitySignal.signal_type == p["signal_type"])
        if p.get("category"):
            q = q.where(OpportunitySignal.category == p["category"])
        hit = db.execute(q.order_by(OpportunitySignal.id.desc()).limit(1)).first()
        if not hit:
            return False, "", "", {}
        sg, s = hit
        return True, f"SIG:{sg.id}", f"New {sg.signal_type.replace('_', ' ').lower()} signal on {s} " \
                                     f"({sg.timeframe}, ref {sg.reference_price:g})", {}
    if rt == "DIVIDEND_ANNOUNCED":
        q = select(CorporateAction, Instrument.symbol).join(Instrument).where(
            CorporateAction.action_type == "DIVIDEND_CASH", CorporateAction.created_at > since)
        if inst:
            q = q.where(CorporateAction.instrument_id == inst.id)
        hit = db.execute(q.order_by(CorporateAction.id.desc()).limit(1)).first()
        if not hit:
            return False, "", "", {}
        ca, s = hit
        return True, f"DIV:{ca.id}", f"{s} dividend: {ca.cash_amount} {ca.currency or ''} (ex-date {ca.ex_date})", {}
    if rt == "ANNOUNCEMENT":
        q = select(NewsArticle).where(NewsArticle.ingested_at > since)
        if inst:
            q = q.join(NewsArticleInstrument).where(NewsArticleInstrument.instrument_id == inst.id)
        n = db.scalar(q.order_by(NewsArticle.id.desc()).limit(1))
        if n is None:
            return False, "", "", {}
        return True, f"NEWS:{n.id}", f"{sym}: {n.title}", {}
    if rt == "FUNDAMENTAL_CHANGE":
        if inst is None:
            return None
        rows = db.scalars(select(FundamentalMetric).where(FundamentalMetric.instrument_id == inst.id,
                                                          FundamentalMetric.metric == p["metric"])
                          .order_by(FundamentalMetric.as_of.desc()).limit(260)).all()
        vals = [r for r in rows if r.value is not None]
        if len(vals) < 2:
            return None
        cur, base = vals[0], vals[-1]
        if not base.value:
            return None
        change = cur.value / base.value - 1
        fire = abs(change) >= float(p["change"])
        return fire, f"FUND:{p['metric']}:{cur.as_of}", f"{sym} {p['metric']} changed {change:+.1%} " \
                                                         f"({base.as_of} to {cur.as_of})", {}
    return None


def evaluate_all() -> dict[str, int]:
    stats = {"rules": 0, "fired": 0, "skipped_cooldown": 0, "unavailable": 0}
    with advisory_lock("job:alerts") as ok:
        if not ok:
            return stats
        with session_scope() as db:
            now = datetime.now(timezone.utc)
            for rule in db.scalars(select(AlertRule).where(AlertRule.is_active.is_(True))):
                stats["rules"] += 1
                inst = db.get(Instrument, rule.instrument_id) if rule.instrument_id else None
                try:
                    res = _evaluate(db, rule, inst)
                except (KeyError, TypeError, ValueError) as exc:
                    log.warning("alert_rule_invalid", extra={"rule_id": rule.id, "error": str(exc)})
                    stats["unavailable"] += 1
                    continue
                if res is None:
                    stats["unavailable"] += 1
                    continue
                fire, key, msg, state = res
                if state:
                    rule.last_state = state
                if not fire:
                    continue
                if rule.last_triggered_at and now - rule.last_triggered_at < timedelta(minutes=rule.cooldown_minutes):
                    stats["skipped_cooldown"] += 1
                    continue
                ev_id = db.scalar(insert(AlertEvent).values(rule_id=rule.id, dedupe_key=key[:128], message=msg,
                                                            context={"rule_type": rule.rule_type})
                                  .on_conflict_do_nothing().returning(AlertEvent.id))
                if ev_id is None:
                    continue  # already notified for this exact observation
                rule.last_triggered_at = now
                link = f"/instruments/{inst.symbol}" if inst else "/alerts"
                for ch in rule.channels or ["in_app"]:
                    notifier = NOTIFIERS.get(ch)
                    if notifier:
                        notifier.send(db, rule.user_id, f"Alert: {rule.rule_type.replace('_', ' ').title()}", msg,
                                      link, ev_id)
                stats["fired"] += 1
    return stats
