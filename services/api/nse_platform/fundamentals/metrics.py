"""Point-in-time fundamental metrics.

Only statement items with published_at <= as_of are used, so the same function is safe for backtests.
Every metric is returned with its inputs; a metric whose inputs are unavailable is None (never 0).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from nse_platform.models import CorporateAction, FinancialStatementItem
from nse_platform.models.enums import CorporateActionType

FUNDAMENTALS_CALC_VERSION = "fund-1.0.0"


@dataclass
class FundamentalSnapshot:
    as_of: date
    period_end: date | None
    metrics: dict[str, float | None] = field(default_factory=dict)
    inputs: dict[str, Any] = field(default_factory=dict)
    sources: list[str] = field(default_factory=list)
    available: bool = False


def load_statements(session: Session, instrument_id: int) -> pd.DataFrame:
    rows = session.execute(select(
        FinancialStatementItem.period_end, FinancialStatementItem.period_type, FinancialStatementItem.statement_type,
        FinancialStatementItem.line_item, FinancialStatementItem.value, FinancialStatementItem.published_at,
        FinancialStatementItem.source, FinancialStatementItem.currency,
    ).where(FinancialStatementItem.instrument_id == instrument_id)).all()
    return pd.DataFrame(rows, columns=["period_end", "period_type", "statement_type", "line_item", "value",
                                       "published_at", "source", "currency"])


def load_dividends(session: Session, instrument_id: int) -> pd.DataFrame:
    rows = session.execute(select(CorporateAction.ex_date, CorporateAction.cash_amount, CorporateAction.status,
                                  CorporateAction.announced_at, CorporateAction.payment_date)
                           .where(CorporateAction.instrument_id == instrument_id,
                                  CorporateAction.action_type == CorporateActionType.DIVIDEND_CASH.value)).all()
    df = pd.DataFrame(rows, columns=["ex_date", "amount", "status", "announced_at", "payment_date"])
    if not df.empty:
        df = df[df["status"].fillna("").str.upper() != "CANCELLED"]
    return df


def _safe_div(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or b == 0 or pd.isna(a) or pd.isna(b):
        return None
    return float(a) / float(b)


def _growth(cur: float | None, prev: float | None) -> float | None:
    if cur is None or prev is None or prev <= 0 or pd.isna(cur) or pd.isna(prev):
        return None  # growth from a non-positive base is undefined
    return float(cur) / float(prev) - 1


def compute(statements: pd.DataFrame, dividends: pd.DataFrame, as_of: date, price: float | None,
            price_currency: str = "KES", ratio_events: list[tuple[date, float]] | None = None) -> FundamentalSnapshot:
    """`ratio_events`: (ex_date, price factor) share-ratio events (bonus/split). Per-share figures reported for a
    period are restated onto the share base prevailing at `as_of` so they are comparable with the raw price."""
    snap = FundamentalSnapshot(as_of=as_of, period_end=None)
    m = snap.metrics
    if not statements.empty:
        st = statements[(statements["published_at"] <= as_of) & (statements["period_type"] == "FY")]
    else:
        st = statements
    # Dividend metrics only need dividend history and price.
    div_known = dividends
    if not dividends.empty:
        ann = pd.to_datetime(dividends["announced_at"], utc=True).dt.date
        known = dividends["ex_date"].notna() & ((ann.isna() & (dividends["ex_date"] <= as_of)) | (ann <= as_of))
        div_known = dividends[known]
    trailing = div_known[(div_known["ex_date"] <= as_of) & (div_known["ex_date"] > as_of - timedelta(days=365))] \
        if not div_known.empty else div_known
    ttm_div = float(trailing["amount"].sum()) if not trailing.empty else (0.0 if not div_known.empty else None)
    m["dividend_ttm"] = ttm_div
    m["dividend_yield"] = _safe_div(ttm_div, price) if ttm_div is not None else None
    if not div_known.empty:
        years = sorted({d.year for d in div_known["ex_date"] if d is not None and d <= as_of})
        streak = 0
        for y in range(as_of.year - (0 if any(y == as_of.year for y in years) else 1), 1900, -1):
            if y in years:
                streak += 1
            else:
                break
        m["dividend_years_streak"] = float(streak)
        by_year = div_known[div_known["ex_date"] <= as_of].groupby(
            div_known["ex_date"].map(lambda d: d.year))["amount"].sum()
        if len(by_year) >= 4:
            last, prev3 = by_year.iloc[-1], by_year.iloc[-4]
            m["dividend_growth_3y"] = (last / prev3) ** (1 / 3) - 1 if prev3 > 0 and last > 0 else None
    if st.empty:
        return snap

    wide = st.pivot_table(index="period_end", columns="line_item", values="value", aggfunc="last").sort_index()
    latest_pe = wide.index.max()
    snap.period_end = latest_pe
    snap.available = True
    snap.sources = sorted(set(st.loc[st["period_end"] == latest_pe, "source"]))
    cur = wide.loc[latest_pe]
    prev = wide.iloc[-2] if len(wide) >= 2 else None
    three = wide.iloc[-4] if len(wide) >= 4 else None

    def g(row: pd.Series | None, key: str) -> float | None:
        if row is None or key not in row.index or pd.isna(row[key]):
            return None
        return float(row[key])

    revenue, net, op = g(cur, "revenue"), g(cur, "net_income"), g(cur, "operating_income")
    equity, assets, debt = g(cur, "total_equity"), g(cur, "total_assets"), g(cur, "total_debt")
    share_factor = 1.0
    for ex, f in ratio_events or []:
        if latest_pe < ex <= as_of:
            share_factor *= f  # e.g. 1:10 bonus => f = 10/11
    shares = g(cur, "shares_outstanding")
    shares = shares / share_factor if shares else shares
    eps = g(cur, "eps")
    eps = eps * share_factor if eps is not None else _safe_div(net, shares)
    avg_equity = (equity + g(prev, "total_equity")) / 2 if equity and g(prev, "total_equity") else equity
    m.update({
        "revenue": revenue, "net_income": net, "eps": eps,
        "gross_margin": _safe_div(g(cur, "gross_profit"), revenue),
        "operating_margin": _safe_div(op, revenue), "net_margin": _safe_div(net, revenue),
        "roe": _safe_div(net, avg_equity), "roa": _safe_div(net, assets),
        "debt_to_equity": _safe_div(debt, equity) if equity and equity > 0 else None,
        "current_ratio": _safe_div(g(cur, "current_assets"), g(cur, "current_liabilities")),
        "interest_coverage": _safe_div(op, g(cur, "interest_expense")),
        "operating_cash_flow": g(cur, "operating_cash_flow"),
        "free_cash_flow": (g(cur, "operating_cash_flow") + g(cur, "capex"))
        if g(cur, "operating_cash_flow") is not None and g(cur, "capex") is not None else None,
        "cash_conversion": _safe_div(g(cur, "operating_cash_flow"), net) if net and net > 0 else None,
        "revenue_growth": _growth(revenue, g(prev, "revenue")),
        "earnings_growth": _growth(net, g(prev, "net_income")),
        # Growth of reported (un-restated) EPS; both periods are on their own reported share base, so a bonus
        # issue between them would distort this - the net-income growth above is share-base independent.
        "eps_growth": _growth(g(cur, "eps") or _safe_div(net, g(cur, "shares_outstanding")),
                              g(prev, "eps") or _safe_div(g(prev, "net_income"), g(prev, "shares_outstanding"))),
        "revenue_cagr_3y": ((revenue / g(three, "revenue")) ** (1 / 3) - 1)
        if revenue and g(three, "revenue") and g(three, "revenue") > 0 and revenue > 0 else None,
    })
    dps = g(cur, "dps")
    dps = dps * share_factor if dps is not None else None
    m["payout_ratio"] = _safe_div(dps, eps) if eps and eps > 0 and dps is not None else None
    if price is not None:
        m["pe_ratio"] = _safe_div(price, eps) if eps and eps > 0 else None  # negative earnings: P/E undefined
        bvps = _safe_div(equity, shares)
        m["pb_ratio"] = _safe_div(price, bvps) if bvps and bvps > 0 else None
        mcap = price * shares if shares else None
        m["market_cap"] = mcap
        m["ps_ratio"] = _safe_div(mcap, revenue) if mcap and revenue and revenue > 0 else None
        cash = g(cur, "cash")
        if mcap is not None and debt is not None and cash is not None and op:
            m["ev_ebit"] = _safe_div(mcap + debt - cash, op)
        else:
            m["ev_ebit"] = None  # cash balance not available -> EV not computable
    snap.inputs = {"period_end": str(latest_pe), "price": price, "price_currency": price_currency,
                   "statement_currency": str(st["currency"].iloc[0]), "shares_outstanding": shares}
    return snap
