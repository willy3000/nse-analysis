"""MyStocks adapter against mocked HTTP responses shaped like the published OpenAPI spec."""

from __future__ import annotations

import asyncio
from datetime import date

import httpx
import pytest
import respx

from nse_platform.models.enums import LatencyClass, SecurityType, TradingStatus
from nse_platform.providers.base import ProviderAuthError, ProviderNotConfigured, ProviderUnavailable
from nse_platform.providers.mystocks import MyStocksProvider

BASE = "https://mystocks.africa/api/sandbox/v1/partner"


async def _nosleep(_: float) -> None:
    return None


def provider() -> MyStocksProvider:
    return MyStocksProvider("sk_sandbox_test", BASE, sleep=_nosleep)


def run(coro):  # noqa: ANN001, ANN201
    return asyncio.run(coro)


def stock(sym: str, **kw: object) -> dict[str, object]:
    base = {"id": sym, "symbol": sym, "name": f"{sym} PLC", "exchange": "NSE", "exchangeMic": "XNAI",
            "currency": "KES", "sector": "Banking", "assetType": "STOCK", "listingStatus": "ACTIVE", "price": 10.5,
            "previousClose": 10.0, "volume": 1000, "asOf": "2026-11-06T12:00:00Z", "stale": False,
            "dataQuality": {"asOf": "2026-11-06T12:00:00Z", "delayMode": "DELAYED", "delayMinutes": 15}}
    base.update(kw)
    return base


def test_not_configured_without_key():
    p = MyStocksProvider(None, BASE)
    assert not p.is_configured()
    with pytest.raises(ProviderNotConfigured):
        run(p.get_instruments())


@respx.mock
def test_instruments_paginate_and_map_types():
    respx.get(f"{BASE}/stocks", params={"cursor": "c2"}).mock(return_value=httpx.Response(200, json={
        "stocks": [stock("ILAM.KE", name="ILAM Fahari I-REIT", listingStatus="SUSPENDED")], "hasMore": False}))
    respx.get(f"{BASE}/stocks").mock(return_value=httpx.Response(200, json={
        "stocks": [stock("SCOM.KE"), stock("GLD.KE", assetType="ETF"), stock("X.NG", exchange="NGX")],
        "hasMore": True, "nextCursor": "c2"}))
    respx.get(f"{BASE}/bonds").mock(return_value=httpx.Response(200, json={"bonds": [
        {"id": "b1", "symbol": "IFB1/2023/17", "name": "Infrastructure bond", "couponRate": 14.4, "exchange": "NSE",
         "status": "ACTIVE", "maturityDate": "2040-01-01", "faceValue": 100}]}))
    recs = run(provider().get_instruments())
    by = {r.provider_symbol: r for r in recs}
    assert set(by) == {"SCOM.KE", "GLD.KE", "ILAM.KE", "bond:b1"}  # NGX listing filtered out
    assert by["SCOM.KE"].symbol == "SCOM" and by["SCOM.KE"].security_type == SecurityType.EQUITY
    assert by["GLD.KE"].security_type == SecurityType.ETF
    assert by["ILAM.KE"].security_type == SecurityType.REIT
    assert by["ILAM.KE"].trading_status == TradingStatus.SUSPENDED
    assert by["bond:b1"].security_type == SecurityType.BOND and by["bond:b1"].coupon_rate == pytest.approx(0.144)


@respx.mock
def test_quotes_are_delayed_never_live_and_missing_timestamp_rejected():
    respx.get(f"{BASE}/market/quotes").mock(return_value=httpx.Response(200, json={
        "data": [stock("SCOM.KE"), stock("KCB.KE", dataQuality={"delayMode": "END_OF_DAY"},
                                         asOf="2026-11-06T12:00:00Z"),
                 stock("EQTY.KE", asOf=None, dataQuality={}, lastPriceUpdate=None)],
        "not_found": ["NOPE.KE"], "meta": {"timestamp": "2026-11-06T12:16:00Z"}}))
    batch = run(provider().get_quotes(["SCOM.KE", "KCB.KE", "EQTY.KE", "NOPE.KE"]))
    q = {x.provider_symbol: x for x in batch.quotes}
    assert q["SCOM.KE"].latency_class == LatencyClass.DELAYED
    assert q["KCB.KE"].latency_class == LatencyClass.END_OF_DAY
    assert all(x.latency_class != LatencyClass.LIVE for x in batch.quotes)
    assert "EQTY.KE" in batch.not_found and "NOPE.KE" in batch.not_found
    assert q["SCOM.KE"].market_ts.isoformat() == "2026-11-06T12:00:00+00:00"


@respx.mock
def test_candles_window_split_and_missing_ohlc_preserved_as_none():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.url.params["from"], request.url.params["to"]))
        return httpx.Response(200, json={"currency": "KES", "candles": [
            {"timestamp": f"{request.url.params['from']}T00:00:00.000Z", "open": 1, "high": 2, "low": 0.5,
             "close": 1.5, "volume": 10, "ohlcAvailable": False, "volumeAvailable": True}]})

    respx.get(f"{BASE}/stocks/SCOM.KE/candles").mock(side_effect=handler)
    bars = run(provider().get_historical_prices("SCOM.KE", date(2023, 1, 1), date(2025, 6, 30)))
    assert len(calls) == 3  # <=366-day windows
    assert all(b.open is None and b.high is None and b.low is None and not b.ohlc_available for b in bars)
    assert bars[0].close == 1.5


@respx.mock
def test_auth_error_not_retried():
    route = respx.get(f"{BASE}/market/status").mock(return_value=httpx.Response(401, json={"error": {}}))
    with pytest.raises(ProviderAuthError):
        run(provider().get_market_status())
    assert route.call_count == 1


@respx.mock
def test_rate_limit_retry_after_then_success_and_headers_captured():
    route = respx.get(f"{BASE}/market/status").mock(side_effect=[
        httpx.Response(429, headers={"Retry-After": "1"}),
        httpx.Response(200, headers={"X-RateLimit-Limit": "300", "X-RateLimit-Remaining": "299",
                                     "X-RateLimit-Reset": "1800000000"},
                       json={"anyOpen": False, "checkedAt": "2026-11-07T08:00:00Z",
                             "exchanges": {"NSE": {"isOpen": False, "reason": "NON_TRADING_DAY"}}})])
    p = provider()
    st = run(p.get_market_status())
    assert route.call_count == 2 and st.is_open is False and st.reason == "NON_TRADING_DAY"
    assert p.rate_limit.limit == 300 and p.rate_limit.remaining == 299


@respx.mock
def test_server_errors_retried_then_unavailable():
    route = respx.get(f"{BASE}/market/status").mock(return_value=httpx.Response(503))
    with pytest.raises(ProviderUnavailable):
        run(provider().get_market_status())
    assert route.call_count == 5  # initial + 4 retries


def test_http_client_is_read_only():
    p = provider()
    with pytest.raises(PermissionError):
        run(p._client().request("POST", "/trade"))
