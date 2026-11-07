"""Freshness classification for what the user sees. Never upgrades a provider's latency class.

Status precedence:
  UNAVAILABLE  no observation at all
  HISTORICAL   provider latency class HISTORICAL (e.g. synthetic, archives)
  LIVE         provider latency LIVE, market open, observation < 2 min old    (no current provider qualifies)
  DELAYED      provider latency DELAYED, market open, observation within delay + 2 refresh intervals
  END_OF_DAY   market closed and the observation belongs to the last completed session
  STALE        anything older than expected - with a reason code explaining why

Reason codes distinguish: NO_RECENT_TRADE (bars exist with zero volume), SUSPENDED, HOLIDAY, WEEKEND,
FEED_FAILURE (provider health DOWN/DEGRADED), PROVIDER_NOT_CONFIGURED, MISSING_PROVIDER_DATA.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from nse_platform.models import MarketSession
from nse_platform.models.enums import FreshnessStatus, LatencyClass, ProviderStatus, SessionStatus, TradingStatus

EAT = ZoneInfo("Africa/Nairobi")
OPEN, CLOSE = time(9, 0), time(15, 0)


@dataclass
class Freshness:
    status: FreshnessStatus
    reason: str | None
    as_of: datetime | None
    age_seconds: int | None
    latency_class: str | None
    provider: str | None
    detail: str

    def as_dict(self) -> dict[str, object]:
        return {"status": self.status.value, "reason": self.reason,
                "as_of": self.as_of.isoformat() if self.as_of else None, "age_seconds": self.age_seconds,
                "latency_class": self.latency_class, "provider": self.provider, "detail": self.detail}


def session_status(db: Session, d: date) -> tuple[SessionStatus, str | None]:
    row = db.scalar(select(MarketSession).where(MarketSession.session_date == d))
    if row is not None:
        return SessionStatus(row.status), row.reason
    if d.weekday() >= 5:
        return SessionStatus.WEEKEND, None
    return SessionStatus.TRADING, None  # calendar unknown for this date: assume trading (conservative for STALE)


def market_open_now(db: Session, now: datetime | None = None) -> tuple[bool, str]:
    now_l = (now or datetime.now(timezone.utc)).astimezone(EAT)
    st, reason = session_status(db, now_l.date())
    if st != SessionStatus.TRADING:
        return False, (reason or st.value)
    if not (OPEN <= now_l.time() < CLOSE):
        return False, "OUTSIDE_TRADING_HOURS"
    return True, "OPEN"


def last_completed_session(db: Session, now: datetime | None = None) -> date:
    now_l = (now or datetime.now(timezone.utc)).astimezone(EAT)
    d = now_l.date() if now_l.time() >= CLOSE else now_l.date() - timedelta(days=1)
    for _ in range(15):
        st, _ = session_status(db, d)
        if st == SessionStatus.TRADING:
            return d
        d -= timedelta(days=1)
    return d


def classify(db: Session, *, observed_at: datetime | None, observation_session: date | None, latency_class: str | None,
             provider_code: str | None, provider_health: str | None, trading_status: str | None,
             last_bar_volume: float | None, delay_minutes: int | None = None, refresh_seconds: int = 900,
             now: datetime | None = None) -> Freshness:
    now = now or datetime.now(timezone.utc)
    if observed_at is None:
        reason = "PROVIDER_NOT_CONFIGURED" if provider_health == ProviderStatus.UNCONFIGURED.value else \
            "MISSING_PROVIDER_DATA"
        return Freshness(FreshnessStatus.UNAVAILABLE, reason, None, None, latency_class, provider_code,
                         "No observation available from any configured provider.")
    age = int((now - observed_at).total_seconds())
    base = dict(as_of=observed_at, age_seconds=age, latency_class=latency_class, provider=provider_code)
    if latency_class == LatencyClass.HISTORICAL.value:
        return Freshness(FreshnessStatus.HISTORICAL, None, detail="Historical/archival data - not a current price.",
                         **base)
    if trading_status == TradingStatus.SUSPENDED.value:
        return Freshness(FreshnessStatus.STALE, "SUSPENDED", detail="Trading in this security is suspended; last "
                         "price shown is from before the suspension.", **base)
    is_open, why = market_open_now(db, now)
    last_session = last_completed_session(db, now)
    feed_down = provider_health in (ProviderStatus.DOWN.value, ProviderStatus.DEGRADED.value)
    if is_open:
        if latency_class == LatencyClass.LIVE.value and age <= 120:
            return Freshness(FreshnessStatus.LIVE, None, detail="Real-time exchange data.", **base)
        limit = (delay_minutes or 15) * 60 + 2 * refresh_seconds
        if latency_class == LatencyClass.DELAYED.value and age <= limit:
            return Freshness(FreshnessStatus.DELAYED, None, detail=f"Delayed by at least {delay_minutes or 15} "
                             "minutes. Not real-time.", **base)
    else:
        if observation_session is not None and observation_session >= last_session:
            return Freshness(FreshnessStatus.END_OF_DAY, why if why not in ("OPEN",) else None,
                             detail=f"Market closed ({why}). Showing the {observation_session} session close.",
                             **base)
    # Older than expected - explain why.
    if feed_down:
        reason, detail = "FEED_FAILURE", f"Provider {provider_code} is {provider_health}; data may be outdated."
    elif last_bar_volume == 0:
        reason, detail = "NO_RECENT_TRADE", "No trades were reported in the most recent session(s)."
    else:
        reason, detail = "MISSING_PROVIDER_DATA", "The provider has not supplied data for the latest session."
    return Freshness(FreshnessStatus.STALE, reason, detail=detail, **base)
