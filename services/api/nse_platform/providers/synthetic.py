"""Synthetic development provider (ADR-0007).

Generates deterministic, clearly-fictitious instruments (symbols prefixed 'ZZ', names suffixed '(Synthetic)').
NEVER enabled in production. Used so the analytics, UI and backtester can be developed and tested without a
market-data licence. The generator deliberately reproduces awkward real-world properties:
illiquid sessions with zero volume, a trading suspension (gap), cash dividends with ex-date price drops,
a bonus issue with a raw-price discontinuity, holidays, and published-with-a-lag financial statements.
"""

from __future__ import annotations

import zlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache

import numpy as np

from nse_platform.models.enums import (
    CorporateActionType,
    LatencyClass,
    NewsKind,
    SecurityType,
    Timeframe,
    TradingStatus,
)
from nse_platform.providers.base import (
    BarRecord,
    CompanyProfileRecord,
    CorporateActionRecord,
    FinancialItemRecord,
    HolidayRecord,
    InstrumentRecord,
    MarketDataProvider,
    MarketStatusRecord,
    NewsRecord,
    NotSupported,
    QuoteBatch,
    QuoteRecord,
)
from nse_platform.providers.parsing import day_start_utc

HISTORY_START = date(2016, 1, 4)
EAT = timezone(timedelta(hours=3))


@dataclass(frozen=True)
class SynthSpec:
    symbol: str
    name: str
    sector: str
    security_type: SecurityType
    start_price: float
    drift: float  # annual
    vol: float  # annual
    liquidity: float  # probability a session has trades
    avg_volume: int
    dividend: float | None = None  # annual dividend yield paid once a year on the prevailing price
    shares_out: float = 1e9
    margin: float = 0.15
    suspended_from: date | None = None  # currently suspended since
    gap: tuple[date, date] | None = None  # historical suspension window
    bonus: tuple[date, float, float] | None = None  # (ex_date, ratio_from, ratio_to)
    # Surge events: (sessions before the last session, day return, annual drift for the next 10 sessions).
    # Offset 0 = the latest session, so the demo always shows a fresh surge.
    surges: tuple[tuple[int, float, float], ...] = ()


SPECS: tuple[SynthSpec, ...] = (
    SynthSpec("ZZBNK1", "Zeta Commercial Bank (Synthetic)", "Banking", SecurityType.EQUITY, 38, 0.10, 0.24, 0.99,
              900_000, dividend=0.08, shares_out=3.7e9, margin=0.30),
    SynthSpec("ZZBNK2", "Kilima Bank Group (Synthetic)", "Banking", SecurityType.EQUITY, 42, 0.06, 0.22, 0.99,
              700_000, dividend=0.09, shares_out=3.0e9, margin=0.28,
              bonus=(date(2021, 6, 14), 10.0, 11.0)),
    SynthSpec("ZZBNK3", "Savanna Microfinance (Synthetic)", "Banking", SecurityType.EQUITY, 9, -0.08, 0.45, 0.55,
              40_000, shares_out=4.0e8, margin=0.04),
    SynthSpec("ZZTEL", "Twiga Telecom (Synthetic)", "Telecommunication", SecurityType.EQUITY, 22, 0.05, 0.28,
              1.0, 9_000_000, dividend=0.055, shares_out=4.0e10, margin=0.22),
    SynthSpec("ZZPWR", "Rift Power Generation (Synthetic)", "Energy & Petroleum", SecurityType.EQUITY, 6.5, 0.02,
              0.30, 0.95, 1_200_000, dividend=0.06, shares_out=6.6e9, margin=0.12,
              gap=(date(2019, 3, 4), date(2019, 4, 26))),
    SynthSpec("ZZOIL", "Pwani Petroleum (Synthetic)", "Energy & Petroleum", SecurityType.EQUITY, 18, -0.02, 0.35,
              0.85, 120_000, dividend=0.055, shares_out=1.5e8, margin=0.03),
    SynthSpec("ZZBRW", "Highland Breweries (Synthetic)", "Manufacturing & Allied", SecurityType.EQUITY, 160, 0.03,
              0.25, 0.98, 300_000, dividend=0.05, shares_out=8.0e8, margin=0.14),
    SynthSpec("ZZCEM", "Athi Cement Works (Synthetic)", "Construction & Allied", SecurityType.EQUITY, 14, -0.05,
              0.40, 0.70, 60_000, shares_out=3.6e8, margin=-0.02),
    SynthSpec("ZZINS", "Bahari Insurance (Synthetic)", "Insurance", SecurityType.EQUITY, 11, 0.04, 0.30, 0.80,
              150_000, dividend=0.055, shares_out=2.0e9, margin=0.08),
    SynthSpec("ZZTEA", "Kericho Tea Estates (Synthetic)", "Agricultural", SecurityType.EQUITY, 280, 0.04, 0.32,
              0.30, 3_000, dividend=0.07, shares_out=2.0e7, margin=0.10),
    SynthSpec("ZZAIR", "Simba Airways (Synthetic)", "Commercial & Services", SecurityType.EQUITY, 5, -0.10, 0.55,
              0.60, 80_000, shares_out=5.6e9, margin=-0.15, suspended_from=date(2026, 9, 15)),
    SynthSpec("ZZMED", "Mwangaza Media (Synthetic)", "Commercial & Services", SecurityType.EQUITY, 12, 0.00, 0.38,
              0.75, 90_000, dividend=0.04, shares_out=1.9e8, margin=0.05),
    SynthSpec("ZZINV", "Nyota Investments (Synthetic)", "Investment", SecurityType.EQUITY, 3.2, 0.08, 0.42, 0.85,
              500_000, shares_out=2.0e9, margin=0.20),
    SynthSpec("ZZREIT", "Fahari Income REIT (Synthetic)", "Real Estate Investment Trust", SecurityType.REIT, 8,
              -0.03, 0.20, 0.50, 20_000, dividend=0.065, shares_out=1.8e8, margin=0.40),
    SynthSpec("ZZETF", "Dhahabu Gold ETF (Synthetic)", "Exchange Traded Fund", SecurityType.ETF, 2500, 0.07, 0.16,
              0.45, 500, shares_out=4e5, margin=0.0),
    SynthSpec("ZZSURGE", "Kipepeo Surge Holdings (Synthetic)", "Investment", SecurityType.EQUITY, 6.0, 0.0, 0.30,
              0.70, 20_000, shares_out=4.0e8, margin=0.08,
              # Mix of surges that kept running and surges that collapsed, like real NSE small caps.
              surges=((2300, 0.10, 2.0), (1900, 0.095, -3.0), (1500, 0.10, 3.0), (1150, 0.09, -2.5),
                      (800, 0.10, 1.5), (520, 0.10, -4.0), (300, 0.095, 2.5), (90, 0.10, -3.5), (0, 0.10, 0.0))),
    SynthSpec("ZZSUG", "Nzoia Sugar Millers (Synthetic)", "Manufacturing & Allied", SecurityType.EQUITY, 4, 0.12,
              0.50, 0.65, 70_000, shares_out=1.2e9, margin=0.02),
)
BOND = InstrumentRecord(
    provider_symbol="ZZBOND30", symbol="ZZBOND30", name="Synthetic Infrastructure Bond 2030 (Synthetic)",
    security_type=SecurityType.BOND, exchange_code="NSE", currency="KES", trading_status=TradingStatus.ACTIVE,
    issuer="Synthetic Treasury", coupon_rate=0.125, maturity_date=date(2030, 6, 30), face_value=100.0,
)


def _easter(year: int) -> date:
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    m = (32 + 2 * e + 2 * i - h - k) % 7
    n = (a + 11 * h + 22 * m) // 451
    month = (h + m - 7 * n + 114) // 31
    day = ((h + m - 7 * n + 114) % 31) + 1
    return date(year, month, day)


def kenya_holidays(year: int) -> dict[date, str]:
    """Fixed-date Kenyan public holidays + Good Friday/Easter Monday. (Synthetic calendar; Eid dates omitted.)"""
    e = _easter(year)
    hol = {
        date(year, 1, 1): "New Year's Day", e - timedelta(days=2): "Good Friday",
        e + timedelta(days=1): "Easter Monday", date(year, 5, 1): "Labour Day",
        date(year, 6, 1): "Madaraka Day", date(year, 10, 10): "Mazingira Day",
        date(year, 10, 20): "Mashujaa Day", date(year, 12, 12): "Jamhuri Day",
        date(year, 12, 25): "Christmas Day", date(year, 12, 26): "Boxing Day",
    }
    for d in list(hol):  # Sunday holidays observed on Monday
        if d.weekday() == 6:
            hol.setdefault(d + timedelta(days=1), hol[d] + " (observed)")
    return hol


def trading_days(start: date, end: date) -> list[date]:
    out, hol = [], {}
    for y in range(start.year, end.year + 1):
        hol.update(kenya_holidays(y))
    d = start
    while d <= end:
        if d.weekday() < 5 and d not in hol:
            out.append(d)
        d += timedelta(days=1)
    return out


def _seed(symbol: str, seed: int) -> int:
    return (zlib.crc32(symbol.encode()) ^ seed) & 0xFFFFFFFF


def _series(symbol: str, seed: int, end: date) -> tuple[tuple, ...]:
    return _simulate(symbol, seed, end)[0]


def _dividends(symbol: str, seed: int, end: date) -> dict[date, float]:
    return _simulate(symbol, seed, end)[1]


@lru_cache(maxsize=64)
def _simulate(symbol: str, seed: int, end: date) -> tuple[tuple[tuple, ...], dict[date, float]]:
    spec = next(s for s in SPECS if s.symbol == symbol)
    rng = np.random.default_rng(_seed(symbol, seed))
    days = trading_days(HISTORY_START, end)
    dt = 1 / 250
    price = spec.start_price
    regime_drift = spec.drift
    rows = []
    div_days = _dividend_schedule(spec, end)
    paid: dict[date, float] = {}
    surge_at: dict[date, tuple[float, float]] = {}
    if spec.surges:
        live_days = [d for d in days if not (spec.gap and spec.gap[0] <= d <= spec.gap[1])]
        for off, jump, post in spec.surges:
            if off < len(live_days):
                surge_at[live_days[len(live_days) - 1 - off]] = (jump, post)
    post_days, post_drift = 0, 0.0
    for d in days:
        if spec.gap and spec.gap[0] <= d <= spec.gap[1]:
            continue  # suspended: no bars at all
        if spec.suspended_from and d >= spec.suspended_from:
            break
        if rng.random() < 0.01:  # regime switch
            regime_drift = spec.drift + rng.normal(0, 0.35)
        # Drift is a total-return drift: the dividend yield is added back because ex-date drops remove it.
        ret = (regime_drift + (spec.dividend or 0.0) - 0.5 * spec.vol**2) * dt + spec.vol * np.sqrt(dt) * rng.standard_t(5) * 0.77
        prev_close = price
        if spec.bonus and d == spec.bonus[0]:
            prev_close = prev_close * spec.bonus[1] / spec.bonus[2]  # theoretical ex-bonus price
        if d in div_days:
            amt = round(prev_close * div_days[d], 2)
            if amt > 0:
                paid[d] = amt
                prev_close = prev_close - amt
        if d in surge_at:
            jump, post_drift = surge_at[d]
            post_days = 10
            close = round(prev_close * (1 + jump), 2)
            hi = round(close * 1.01, 2)
            rows.append((d, round(prev_close * 1.01, 2), hi, round(prev_close, 2), close,
                         int(spec.avg_volume * rng.uniform(8, 15) / 100) * 100))
            price = close
            continue
        if post_days > 0:
            post_days -= 1
            ret += post_drift * dt + spec.vol * np.sqrt(dt) * rng.normal(0, 0.5)
        traded = rng.random() < spec.liquidity
        if not traded:
            close = round(prev_close, 2)
            rows.append((d, close, close, close, close, 0))
            price = close
            continue
        close = max(0.05, prev_close * float(np.exp(ret)))
        intraday = abs(rng.normal(0, spec.vol * np.sqrt(dt))) + 0.002
        o = prev_close * float(np.exp(rng.normal(0, spec.vol * np.sqrt(dt) * 0.3)))
        hi = max(o, close) * (1 + intraday * rng.random())
        lo = min(o, close) * (1 - intraday * rng.random())
        vol_mult = float(np.exp(rng.normal(0, 0.6))) * (1 + 6 * abs(ret) / max(spec.vol * np.sqrt(dt), 1e-9) / 4)
        volume = max(100, int(spec.avg_volume * vol_mult / 100) * 100)
        rows.append((d, round(o, 2), round(hi, 2), round(lo, 2), round(close, 2), volume))
        price = close
    return tuple(rows), paid


def _dividend_schedule(spec: SynthSpec, end: date) -> dict[date, float]:
    """Ex-dates (first session on/after 20 May) mapped to the yield paid; every fifth year the dividend is cut."""
    if not spec.dividend:
        return {}
    out = {}
    for y in range(HISTORY_START.year, end.year + 1):
        days = trading_days(date(y, 5, 20), date(y, 6, 10))
        if days and days[0] <= end:
            out[days[0]] = spec.dividend * (1.0 if y % 5 else 0.6)
    return out


class SyntheticProvider(MarketDataProvider):
    code = "synthetic"
    latency_class = LatencyClass.HISTORICAL
    is_synthetic = True

    def __init__(self, seed: int = 42, end_date: date | None = None):
        super().__init__()
        self.seed = seed
        self.end_date = end_date

    def _end(self) -> date:
        today = self.end_date or datetime.now(EAT).date()
        days = trading_days(today - timedelta(days=10), today)
        # Today's session counts only once it has closed (15:00 EAT); otherwise use previous session.
        if days and days[-1] == today and self.end_date is None and datetime.now(EAT).time() < time(15, 0):
            days = days[:-1]
        return days[-1] if days else today

    def is_configured(self) -> bool:
        return True

    async def get_instruments(self) -> list[InstrumentRecord]:
        out = [
            InstrumentRecord(
                provider_symbol=s.symbol, symbol=s.symbol, name=s.name, security_type=s.security_type,
                exchange_code="NSE", currency="KES", sector=s.sector, lot_size=100,
                trading_status=TradingStatus.SUSPENDED if s.suspended_from else TradingStatus.ACTIVE,
            )
            for s in SPECS
        ]
        out.append(BOND)
        return out

    async def get_historical_prices(
        self, symbol: str, start_date: date, end_date: date, timeframe: Timeframe = Timeframe.D1
    ) -> list[BarRecord]:
        if timeframe != Timeframe.D1:
            raise NotSupported("synthetic provider only generates daily bars; higher timeframes are resampled")
        if symbol == BOND.symbol:
            raise NotSupported("synthetic bond has no price history")
        rows = _series(symbol, self.seed, self._end())
        return [
            BarRecord(provider_symbol=symbol, timeframe=Timeframe.D1, ts=day_start_utc(d), open=o, high=h, low=lo,
                      close=c, volume=v, ohlc_available=True, volume_available=True)
            for (d, o, h, lo, c, v) in rows
            if start_date <= d <= end_date
        ]

    async def get_quotes(self, symbols: Sequence[str]) -> QuoteBatch:
        batch = QuoteBatch()
        for sym in symbols:
            if not any(s.symbol == sym for s in SPECS):
                batch.not_found.append(sym)
                continue
            rows = _series(sym, self.seed, self._end())
            if len(rows) < 2:
                batch.not_found.append(sym)
                continue
            d, o, h, lo, c, v = rows[-1]
            batch.quotes.append(
                QuoteRecord(
                    provider_symbol=sym, market_ts=datetime.combine(d, time(15, 0), EAT).astimezone(timezone.utc),
                    source_ts=datetime.now(timezone.utc), price=c, open=o, high=h, low=lo,
                    previous_close=rows[-2][4], volume=v, currency="KES", latency_class=LatencyClass.HISTORICAL,
                )
            )
        return batch

    async def get_market_status(self) -> MarketStatusRecord:
        # The synthetic universe never trades live; report closed so nothing is ever presented as live.
        return MarketStatusRecord(exchange_code="NSE", is_open=False, checked_at=datetime.now(timezone.utc),
                                  reason="SYNTHETIC_DATA")

    async def get_holidays(self, start: date, end: date) -> list[HolidayRecord]:
        out = []
        for y in range(start.year, end.year + 1):
            for d, name in kenya_holidays(y).items():
                if start <= d <= end:
                    out.append(HolidayRecord("NSE", d, name))
        return sorted(out, key=lambda h: h.day)

    async def get_corporate_actions(self, symbol: str) -> list[CorporateActionRecord]:
        spec = next((s for s in SPECS if s.symbol == symbol), None)
        if spec is None:
            return []
        end = self._end()
        out = []
        for ex, amt in _dividends(symbol, self.seed, end).items():
            out.append(CorporateActionRecord(
                external_id=f"{symbol}:div:{ex.isoformat()}", provider_symbol=symbol,
                action_type=CorporateActionType.DIVIDEND_CASH, status="PAID" if ex < end else "DECLARED",
                title=f"Final dividend KES {amt:.2f} per share (synthetic)",
                announced_at=day_start_utc(ex - timedelta(days=45)), ex_date=ex,
                record_date=ex + timedelta(days=2), payment_date=ex + timedelta(days=30), cash_amount=amt,
                currency="KES",
            ))
        if spec.bonus and spec.bonus[0] <= end:
            ex, rf, rt = spec.bonus
            out.append(CorporateActionRecord(
                external_id=f"{symbol}:bonus:{ex.isoformat()}", provider_symbol=symbol,
                action_type=CorporateActionType.BONUS, status="EFFECTIVE",
                title=f"Bonus issue {int(rt - rf)} for {int(rf)} (synthetic)",
                announced_at=day_start_utc(ex - timedelta(days=30)), ex_date=ex, effective_date=ex,
                ratio_from=rf, ratio_to=rt,
            ))
        if spec.suspended_from:
            out.append(CorporateActionRecord(
                external_id=f"{symbol}:susp:{spec.suspended_from.isoformat()}", provider_symbol=symbol,
                action_type=CorporateActionType.SUSPENSION, status="EFFECTIVE",
                title="Trading suspension pending restructuring (synthetic)", effective_date=spec.suspended_from,
                announced_at=day_start_utc(spec.suspended_from),
            ))
        return out

    async def get_company_profile(self, symbol: str) -> CompanyProfileRecord | None:
        spec = next((s for s in SPECS if s.symbol == symbol), None)
        if spec is None:
            return None
        rows = _series(symbol, self.seed, self._end())
        mcap = rows[-1][4] * spec.shares_out if rows else None
        return CompanyProfileRecord(
            provider_symbol=symbol, country="KE", market_cap=mcap,
            description=f"{spec.name} is a fictitious {spec.sector.lower()} company generated for development. "
                        "It does not exist and its data is not market data.",
        )

    async def get_financial_statements(self, symbol: str) -> list[FinancialItemRecord]:
        spec = next((s for s in SPECS if s.symbol == symbol), None)
        if spec is None or spec.security_type in (SecurityType.ETF,):
            return []
        rng = np.random.default_rng(_seed(symbol, self.seed + 7))
        end = self._end()
        out: list[FinancialItemRecord] = []
        shares = spec.shares_out
        all_divs = _dividends(symbol, self.seed, end)
        rows = _series(symbol, self.seed, end)
        avg_close = {}
        for r in rows:
            avg_close.setdefault(r[0].year, []).append(r[4])
        for y in range(2015, end.year):
            pe = date(y, 12, 31)
            pub = date(y + 1, 3, 28)
            if pub > end:
                break
            # Earnings track the (synthetic) share price so valuation multiples stay plausible.
            yr_px = float(np.mean(avg_close[y])) if y in avg_close else None
            if yr_px is None:
                continue
            revenue = yr_px * shares * 0.09 / max(spec.margin, 0.06) * (1 + rng.normal(0, 0.08))
            margin = spec.margin + rng.normal(0, 0.03)
            net = revenue * margin
            equity = revenue * (0.9 + rng.normal(0, 0.05))
            assets = equity * (6.0 if spec.sector == "Banking" else 1.8)
            debt = equity * (0.3 + abs(rng.normal(0, 0.25)))
            ocf = net * (1.1 + rng.normal(0, 0.2))
            if spec.bonus and pe.year == spec.bonus[0].year:
                shares = shares * spec.bonus[2] / spec.bonus[1]
            items = {
                ("INCOME", "revenue"): revenue, ("INCOME", "net_income"): net,
                ("INCOME", "operating_income"): net * 1.35, ("INCOME", "gross_profit"): revenue * 0.45,
                ("INCOME", "interest_expense"): debt * 0.11,
                ("INCOME", "eps"): net / shares,
                # Final dividend for FY y is paid (ex-date) in year y+1.
                ("INCOME", "dps"): sum(a for d, a in all_divs.items() if d.year == y + 1),
                ("BALANCE", "total_assets"): assets, ("BALANCE", "total_equity"): equity,
                ("BALANCE", "total_debt"): debt, ("BALANCE", "current_assets"): assets * 0.35,
                ("BALANCE", "current_liabilities"): assets * 0.25, ("BALANCE", "shares_outstanding"): shares,
                ("CASHFLOW", "operating_cash_flow"): ocf, ("CASHFLOW", "capex"): -abs(ocf) * 0.3,
            }
            for (st, li), val in items.items():
                out.append(FinancialItemRecord(
                    provider_symbol=symbol, period_end=pe, period_type="FY", statement_type=st, line_item=li,
                    value=float(round(val, 4)), currency="KES", published_at=pub,
                    source="synthetic generator (not a real filing)",
                ))
        return out

    async def get_news(self, symbol: str | None = None) -> list[NewsRecord]:
        specs = [s for s in SPECS if symbol is None or s.symbol == symbol]
        out = []
        end = self._end()
        for s in specs:
            for ex, amt in list(_dividends(s.symbol, self.seed, end).items())[-2:]:
                out.append(NewsRecord(
                    external_id=f"{s.symbol}:news:div:{ex.isoformat()}", kind=NewsKind.CORPORATE_ACTION,
                    category="DIVIDEND", title=f"{s.name} declares dividend of KES {amt:.2f}",
                    published_at=day_start_utc(ex - timedelta(days=45)), publisher="Synthetic newswire",
                    summary="Synthetic announcement generated for development. Not a real event.",
                    provider_symbols=(s.symbol,),
                ))
            out.append(NewsRecord(
                external_id=f"{s.symbol}:news:results:{end.year}", kind=NewsKind.RESULTS, category="RESULTS",
                title=f"{s.name} publishes full-year results", publisher="Synthetic newswire",
                published_at=day_start_utc(date(end.year, 3, 28)),
                summary="Synthetic results announcement generated for development. Not a real event.",
                provider_symbols=(s.symbol,),
            ))
        return out
