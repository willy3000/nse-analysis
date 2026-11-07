"""MyStocks Africa Partner API adapter (read-only market data).

Spec: https://partners.mystocks.africa/openapi.json (retrieved 2026-11-07). See docs/data-sources/provider-research.md.

Data is 15-minute DELAYED (END_OF_DAY outside session). This adapter never reports LIVE.
Only GET data endpoints are used; trading/brokerage endpoints are deliberately not implemented.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx

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
    HolidayRecord,
    InstrumentRecord,
    MarketDataProvider,
    MarketStatusRecord,
    NewsRecord,
    NotSupported,
    ProviderBadResponse,
    ProviderNotConfigured,
    QuoteBatch,
    QuoteRecord,
)
from nse_platform.providers.http import ReadOnlyHttpClient
from nse_platform.providers.parsing import parse_date, parse_dt, to_float, to_int

log = logging.getLogger(__name__)

EXCHANGE = "NSE"
SUFFIX = ".KE"
MAX_QUOTE_BATCH = 50
# Documented max range per candles request.
MAX_RANGE_DAYS = {Timeframe.D1: 366, Timeframe.W1: 1826, Timeframe.MO1: 3653, Timeframe.H1: 90, Timeframe.M15: 31}
INTERVAL = {Timeframe.D1: "1d", Timeframe.W1: "1w", Timeframe.MO1: "1mo", Timeframe.H1: "1h", Timeframe.M15: "15m"}
_STATUS = {"ACTIVE": TradingStatus.ACTIVE, "SUSPENDED": TradingStatus.SUSPENDED, "DELISTED": TradingStatus.DELISTED}
_CA_TYPES = {
    "BONUS": CorporateActionType.BONUS,
    "BONUS_ISSUE": CorporateActionType.BONUS,
    "SPLIT": CorporateActionType.SPLIT,
    "STOCK_SPLIT": CorporateActionType.SPLIT,
    "CONSOLIDATION": CorporateActionType.CONSOLIDATION,
    "REVERSE_SPLIT": CorporateActionType.CONSOLIDATION,
    "RIGHTS": CorporateActionType.RIGHTS,
    "RIGHTS_ISSUE": CorporateActionType.RIGHTS,
    "SUSPENSION": CorporateActionType.SUSPENSION,
}


def local_symbol(provider_symbol: str) -> str:
    return provider_symbol[: -len(SUFFIX)] if provider_symbol.upper().endswith(SUFFIX) else provider_symbol


def _security_type(item: dict[str, Any]) -> SecurityType:
    if item.get("assetType") == "ETF":
        return SecurityType.ETF
    # NSE REITs (e.g. "... I-REIT", "... D-REIT") are reported as STOCK by the provider; the name is the only
    # deterministic signal available. Documented in docs/data-sources/provider-research.md.
    name = str(item.get("name") or "").upper()
    if "REIT" in name.replace("-", " ").split() or "-REIT" in name:
        return SecurityType.REIT
    return SecurityType.EQUITY


class MyStocksProvider(MarketDataProvider):
    code = "mystocks"
    latency_class = LatencyClass.DELAYED

    def __init__(
        self,
        api_key: str | None,
        base_url: str,
        timeout: float = 20.0,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Any = None,
    ):
        super().__init__()
        self._api_key = api_key
        self._http: ReadOnlyHttpClient | None = None
        if api_key:
            kwargs: dict[str, Any] = {"transport": transport}
            if sleep is not None:
                kwargs["sleep"] = sleep
            self._http = ReadOnlyHttpClient(
                base_url,
                headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json",
                         "User-Agent": "nse-intelligence-platform/0.1"},
                timeout=timeout,
                **kwargs,
            )
            self.rate_limit = self._http.rate_limit

    def is_configured(self) -> bool:
        return self._http is not None

    def _client(self) -> ReadOnlyHttpClient:
        if self._http is None:
            raise ProviderNotConfigured("MYSTOCKS_API_KEY is not set")
        return self._http

    async def aclose(self) -> None:
        if self._http:
            await self._http.aclose()

    # ------------------------------------------------------------------ instruments
    async def get_instruments(self) -> list[InstrumentRecord]:
        http = self._client()
        out: list[InstrumentRecord] = []
        cursor: str | None = None
        for _ in range(100):  # hard stop against a misbehaving cursor
            data = await http.get_json("/stocks", {"exchange": EXCHANGE, "limit": 200, "cursor": cursor})
            for item in data.get("stocks", []) or []:
                sym = item.get("symbol")
                if not sym or (item.get("exchange") or EXCHANGE) != EXCHANGE:
                    continue
                out.append(
                    InstrumentRecord(
                        provider_symbol=sym,
                        symbol=local_symbol(sym),
                        name=item.get("name") or sym,
                        security_type=_security_type(item),
                        exchange_code=EXCHANGE,
                        currency=item.get("currency") or "KES",
                        trading_status=_STATUS.get(str(item.get("listingStatus")), TradingStatus.UNKNOWN),
                        sector=item.get("sector"),
                    )
                )
            if not data.get("hasMore") or not data.get("nextCursor"):
                break
            cursor = data["nextCursor"]
        try:
            bonds = await http.get_json("/bonds", {"exchange": EXCHANGE})
        except ProviderBadResponse as exc:
            log.warning("mystocks_bonds_unavailable", extra={"error": str(exc)})
            bonds = {}
        for b in bonds.get("bonds", []) or []:
            if (b.get("exchange") or EXCHANGE) != EXCHANGE:
                continue
            ident = b.get("symbol") or b.get("id")
            if not ident:
                continue
            out.append(
                InstrumentRecord(
                    provider_symbol=f"bond:{b.get('id') or ident}",
                    symbol=str(ident),
                    name=b.get("name") or str(ident),
                    security_type=SecurityType.BOND,
                    exchange_code=EXCHANGE,
                    currency=b.get("currency") or "KES",
                    trading_status=TradingStatus.ACTIVE if b.get("status") in ("ACTIVE", "OPEN") else
                    TradingStatus.UNKNOWN,
                    issuer=b.get("issuer"),
                    coupon_rate=(to_float(b.get("couponRate")) or 0) / 100 if b.get("couponRate") is not None
                    else None,
                    maturity_date=parse_date(b.get("maturityDate")),
                    face_value=to_float(b.get("faceValue")),
                )
            )
        return out

    # ------------------------------------------------------------------ quotes
    def _quote(self, item: dict[str, Any], response_ts: datetime | None) -> QuoteRecord | None:
        dq = item.get("dataQuality") or {}
        market_ts = parse_dt(item.get("asOf")) or parse_dt(dq.get("asOf")) or parse_dt(item.get("lastPriceUpdate"))
        if market_ts is None:
            return None  # cannot place the observation in time -> rejected upstream as data-quality issue
        mode = str(dq.get("delayMode") or "DELAYED").upper()
        latency = LatencyClass.END_OF_DAY if mode == "END_OF_DAY" else LatencyClass.DELAYED
        return QuoteRecord(
            provider_symbol=item["symbol"],
            market_ts=market_ts,
            source_ts=response_ts,
            price=to_float(item.get("price")),
            open=to_float(item.get("open")),
            high=to_float(item.get("dayHigh")),
            low=to_float(item.get("dayLow")),
            previous_close=to_float(item.get("previousClose")),
            volume=to_int(item.get("volume")),
            bid=to_float(item.get("bid")),
            ask=to_float(item.get("ask")),
            currency=item.get("currency") or "KES",
            latency_class=latency,
            delay_minutes=to_int(dq.get("delayMinutes")) or 15,
            provider_stale=bool(item.get("stale", dq.get("stale", False))),
        )

    async def get_quotes(self, symbols: Sequence[str]) -> QuoteBatch:
        http = self._client()
        batch = QuoteBatch()
        syms = [s for s in symbols if not s.startswith("bond:")]
        for i in range(0, len(syms), MAX_QUOTE_BATCH):
            chunk = syms[i : i + MAX_QUOTE_BATCH]
            data = await http.get_json("/market/quotes", {"symbols": ",".join(chunk)})
            response_ts = parse_dt((data.get("meta") or {}).get("timestamp"))
            raw = data.get("data")
            items = raw if isinstance(raw, list) else ([raw] if isinstance(raw, dict) else [])
            if not items and isinstance(data.get("quotes"), list):
                items = data["quotes"]
            seen = set()
            for item in items:
                if not isinstance(item, dict) or not item.get("symbol"):
                    continue
                q = self._quote(item, response_ts)
                seen.add(item["symbol"])
                if q is None:
                    batch.not_found.append(item["symbol"])
                else:
                    batch.quotes.append(q)
            nf = data.get("not_found") or data.get("notFound") or []
            batch.not_found.extend(str(s) for s in nf)
            batch.not_found.extend(s for s in chunk if s not in seen and s not in nf)
        return batch

    # ------------------------------------------------------------------ history
    async def get_historical_prices(
        self, symbol: str, start_date: date, end_date: date, timeframe: Timeframe = Timeframe.D1
    ) -> list[BarRecord]:
        http = self._client()
        if symbol.startswith("bond:"):
            raise NotSupported("MyStocks does not provide bond price history")
        span = MAX_RANGE_DAYS[timeframe]
        out: dict[datetime, BarRecord] = {}
        cur = start_date
        while cur <= end_date:
            win_end = min(end_date, cur + timedelta(days=span - 1))
            data = await http.get_json(
                f"/stocks/{symbol}/candles",
                {"interval": INTERVAL[timeframe], "from": cur.isoformat(), "to": win_end.isoformat(),
                 "adjustment": "raw"},
            )
            currency = data.get("currency") or "KES"
            for c in data.get("candles", []) or []:
                ts = parse_dt(c.get("timestamp"))
                close = to_float(c.get("close"))
                if ts is None or close is None:
                    continue
                ohlc_ok = bool(c.get("ohlcAvailable", True))
                vol_ok = bool(c.get("volumeAvailable", True))
                out[ts] = BarRecord(
                    provider_symbol=symbol,
                    timeframe=timeframe,
                    ts=ts,
                    close=close,
                    open=to_float(c.get("open")) if ohlc_ok else None,
                    high=to_float(c.get("high")) if ohlc_ok else None,
                    low=to_float(c.get("low")) if ohlc_ok else None,
                    volume=to_int(c.get("volume")) if vol_ok else None,
                    ohlc_available=ohlc_ok,
                    volume_available=vol_ok,
                    currency=currency,
                )
            cur = win_end + timedelta(days=1)
        return [out[k] for k in sorted(out)]

    # ------------------------------------------------------------------ market status / calendar
    async def get_market_status(self) -> MarketStatusRecord:
        data = await self._client().get_json("/market/status", {"exchange": EXCHANGE})
        ex = (data.get("exchanges") or {}).get(EXCHANGE)
        if not isinstance(ex, dict):
            raise ProviderBadResponse("market status response has no NSE entry")
        return MarketStatusRecord(
            exchange_code=EXCHANGE,
            is_open=bool(ex.get("isOpen", ex.get("status") == "OPEN")),
            checked_at=parse_dt(data.get("checkedAt")) or datetime.now(timezone.utc),
            reason=ex.get("reason"),
            next_open=parse_dt(ex.get("nextOpen")),
            next_close=parse_dt(ex.get("nextClose")),
        )

    async def get_holidays(self, start: date, end: date) -> list[HolidayRecord]:
        data = await self._client().get_json(
            "/market/holidays", {"exchange": EXCHANGE, "from": start.isoformat(), "to": end.isoformat()}
        )
        out = []
        for h in data.get("data", []) or []:
            d = parse_date(h.get("date"))
            if d:
                out.append(HolidayRecord(EXCHANGE, d, h.get("reason") or "Holiday"))
        return out

    # ------------------------------------------------------------------ corporate data
    async def get_corporate_actions(self, symbol: str) -> list[CorporateActionRecord]:
        http = self._client()
        out: list[CorporateActionRecord] = []
        divs = await http.get_json(f"/dividends/{symbol}/history", {"limit": 100})
        for d in divs.get("history", []) or []:
            if not d.get("id"):
                continue
            out.append(
                CorporateActionRecord(
                    external_id=f"div:{d['id']}",
                    provider_symbol=symbol,
                    action_type=CorporateActionType.DIVIDEND_CASH,
                    status=d.get("status"),
                    title=f"Dividend {d.get('amount')} {d.get('currency') or ''}".strip(),
                    announced_at=parse_dt(d.get("createdAt")),
                    ex_date=parse_date(d.get("exDividendDate")),
                    record_date=parse_date(d.get("recordDate")),
                    payment_date=parse_date(d.get("paymentDate")),
                    cash_amount=to_float(d.get("amount")),
                    currency=d.get("currency"),
                )
            )
        try:
            cas = await http.get_json("/corporate-actions", {"symbol": symbol, "limit": 100})
        except ProviderBadResponse:
            cas = {}
        for a in cas.get("actions", []) or []:
            atype = str(a.get("type") or "").upper()
            if atype.startswith("DIVIDEND") or not a.get("id"):
                continue  # cash dividends come from the dividends endpoint (avoid double counting)
            out.append(
                CorporateActionRecord(
                    external_id=f"ca:{a['id']}",
                    provider_symbol=symbol,
                    action_type=_CA_TYPES.get(atype, CorporateActionType.OTHER),
                    status=a.get("status"),
                    title=a.get("title"),
                    record_date=parse_date(a.get("recordDate")),
                    effective_date=parse_date(a.get("effectiveDate")),
                    source_url=next((doc.get("url") for doc in a.get("documents") or [] if doc.get("url")), None),
                )
            )
        return out

    async def get_company_profile(self, symbol: str) -> CompanyProfileRecord | None:
        data = await self._client().get_json(f"/companies/{symbol}")
        if not isinstance(data, dict) or not data.get("symbol"):
            return None
        return CompanyProfileRecord(
            provider_symbol=symbol,
            description=data.get("description"),
            website=data.get("website"),
            country=(data.get("country") or "")[:2].upper() or None,
            market_cap=to_float(data.get("marketCap")),
            pe_ratio=to_float(data.get("peRatio")),
        )

    async def get_news(self, symbol: str | None = None) -> list[NewsRecord]:
        http = self._client()
        out: list[NewsRecord] = []
        if symbol:
            data = await http.get_json(f"/companies/{symbol}/news", {"limit": 50})
            for it in data.get("items", []) or []:
                if not it.get("id") or not it.get("title"):
                    continue
                is_ca = it.get("source") == "CORPORATE_ACTION"
                out.append(
                    NewsRecord(
                        external_id=f"cn:{it['id']}",
                        kind=NewsKind.CORPORATE_ACTION if is_ca else NewsKind.NEWS,
                        category=it.get("type"),
                        title=it["title"],
                        url=it.get("url") or None,
                        summary=it.get("description"),
                        published_at=parse_dt(it.get("createdAt")),
                        publisher="MyStocks Africa",
                        provider_symbols=(symbol,),
                    )
                )
        else:
            data = await http.get_json("/market-intel", {"exchange": EXCHANGE, "limit": 50})
            for a in data.get("articles", []) or []:
                if not a.get("id") or not a.get("title"):
                    continue
                out.append(
                    NewsRecord(
                        external_id=f"mi:{a['id']}",
                        kind=NewsKind.NEWS,
                        category=a.get("category"),
                        title=a["title"],
                        summary=a.get("summary"),
                        published_at=parse_dt(a.get("publishedAt") or a.get("createdAt")),
                        publisher="MyStocks Market Intel",
                    )
                )
        return out
