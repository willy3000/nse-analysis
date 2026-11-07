from __future__ import annotations

from sqlalchemy import delete, func, select

from nse_platform.core.db import session_scope
from nse_platform.models import AnalyticsReport, Instrument, NewsArticle


def purge_synthetic() -> dict[str, int]:
    """Delete every synthetic instrument (cascades to bars, quotes, analytics, signals) and synthetic news."""
    with session_scope() as s:
        n = s.scalar(select(func.count()).select_from(Instrument).where(Instrument.is_synthetic.is_(True))) or 0
        s.execute(delete(Instrument).where(Instrument.is_synthetic.is_(True)))
        news = s.execute(delete(NewsArticle).where(NewsArticle.is_synthetic.is_(True))).rowcount or 0
        s.execute(delete(AnalyticsReport).where(AnalyticsReport.universe == "synthetic"))
    return {"instruments_deleted": n, "news_deleted": news}
