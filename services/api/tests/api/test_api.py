"""API integration tests against PostgreSQL with the seeded synthetic universe."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from tests.conftest import register

pytestmark = pytest.mark.db


def test_seed_ingestion_and_analytics_succeeded(seeded):
    assert all(r["status"] == "succeeded" for r in seeded["history"])
    assert seeded["analytics"]["failed"] == [] and seeded["analytics"]["instruments"] >= 10


def test_requires_auth_and_csrf(client):
    assert client.get("/api/v1/market/overview").status_code == 401
    client.get("/api/v1/auth/csrf")
    r = client.post("/api/v1/auth/login", json={"email": "a@b.cd", "password": "x"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "CSRF_FAILED"


def test_password_policy_and_login_lockout(client):
    client.get("/api/v1/auth/csrf")
    h = {"X-CSRF-Token": client.cookies.get("nse_csrf")}
    r = client.post("/api/v1/auth/register", json={"email": "weak@x.io", "password": "short"}, headers=h)
    assert r.status_code == 400
    register(client, "lock@x.io")
    client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": client.cookies.get("nse_csrf")})
    h = {"X-CSRF-Token": client.cookies.get("nse_csrf")}
    for _ in range(5):
        assert client.post("/api/v1/auth/login", json={"email": "lock@x.io", "password": "Wrong-pass1"},
                           headers=h).status_code == 401
    r = client.post("/api/v1/auth/login", json={"email": "lock@x.io", "password": "Test-passw0rd"}, headers=h)
    assert r.status_code == 401 and "locked" in r.json()["error"]["message"]


def test_user_data_isolation(seeded):
    from fastapi.testclient import TestClient

    from nse_platform.main import create_app

    a, b = TestClient(create_app()), TestClient(create_app())
    ha = register(a, "alice@x.io")
    hb = register(b, "bob@x.io")
    p = a.post("/api/v1/portfolio", json={"name": "A", "initial_cash": 1000}, headers=ha).json()
    w = a.post("/api/v1/watchlists", json={"name": "AW"}, headers=ha).json()
    assert b.get(f"/api/v1/portfolio/{p['id']}").status_code == 404
    assert b.get(f"/api/v1/watchlists/{w['id']}").status_code == 404
    assert b.delete(f"/api/v1/portfolio/{p['id']}", headers=hb).status_code == 404
    r = b.post("/api/v1/portfolio/transactions", json={"portfolio_id": p["id"], "txn_type": "DEPOSIT",
                                                       "trade_date": date.today().isoformat(), "amount": 5},
               headers=hb)
    assert r.status_code == 404
    assert b.get("/api/v1/portfolio").json()["items"] == []


def test_synthetic_data_labelled_and_never_live(client):
    h = register(client, "label@x.io")
    ov = client.get("/api/v1/market/overview").json()
    assert ov["universe"] == "synthetic" and ov["is_synthetic"] is True
    items = client.get("/api/v1/instruments?limit=50").json()["items"]
    assert items and all(i["is_synthetic"] for i in items)
    assert all(i["price"]["freshness"]["status"] != "LIVE" for i in items)
    assert all(i["symbol"].startswith("ZZ") for i in items)
    assert h


def test_history_adjusted_vs_raw_and_nulls(client):
    register(client, "hist@x.io")
    raw = client.get("/api/v1/instruments/ZZBNK2/history?adjusted=false").json()
    adj = client.get("/api/v1/instruments/ZZBNK2/history?adjusted=true").json()
    assert len(raw["items"]) == len(adj["items"]) > 100
    assert raw["items"][-1]["close"] == adj["items"][-1]["close"]


def test_scanner_filters_exclude_nulls_and_validate(client):
    h = register(client, "scan@x.io")
    r = client.post("/api/v1/scanner/results", json={"dividend_yield_min": 0.0}, headers=h).json()
    assert all(i["dividend_yield"] is not None for i in r["items"])
    assert client.post("/api/v1/scanner/results", json={"rsi_min": 500}, headers=h).status_code == 422


def test_portfolio_accounting(client):
    h = register(client, "pf@x.io")
    p = client.post("/api/v1/portfolio", json={"name": "P"}, headers=h).json()
    d = "2026-06-01"
    # Cash must exist on the trade date: a buy dated before the deposit is rejected.
    early = client.post("/api/v1/portfolio/transactions", json={"portfolio_id": p["id"], "txn_type": "BUY",
                                                                "trade_date": d, "symbol": "ZZTEL", "quantity": 1,
                                                                "price": 20}, headers=h)
    assert early.status_code == 400 and "Insufficient cash" in early.json()["error"]["message"]
    client.post("/api/v1/portfolio/transactions", json={"portfolio_id": p["id"], "txn_type": "DEPOSIT",
                                                        "trade_date": "2026-05-01", "amount": 100_000}, headers=h)
    tx = client.post("/api/v1/portfolio/transactions", json={"portfolio_id": p["id"], "txn_type": "BUY",
                                                             "trade_date": d, "symbol": "ZZTEL", "quantity": 1000,
                                                             "price": 20}, headers=h).json()
    assert tx["fees"] == pytest.approx(416.0)  # 2.08% of 20,000
    r = client.post("/api/v1/portfolio/transactions", json={"portfolio_id": p["id"], "txn_type": "SELL",
                                                            "trade_date": d, "symbol": "ZZTEL", "quantity": 400,
                                                            "price": 25}, headers=h).json()
    pos = r["portfolio"]["positions"][0]
    assert pos["quantity"] == 600
    assert pos["avg_cost"] == pytest.approx(20.416)
    expected_realized = 400 * 25 * (1 - 0.0208) - 400 * 20.416
    assert r["portfolio"]["realized_pnl"] == pytest.approx(expected_realized, rel=1e-6)
    assert r["portfolio"]["cash"] == pytest.approx(100_000 - 20_416 + 400 * 25 * (1 - 0.0208))


def test_backtest_reproducible(client):
    from nse_platform.backtesting.runner import process_queue

    h = register(client, "bt@x.io")
    body = {"definition": {"entry": [{"left": "rsi_14", "op": "crosses_above", "right": 30}],
                           "exit": [{"left": "rsi_14", "op": ">", "right": 65}], "stop_loss_pct": 0.1,
                           "universe": {"synthetic": True}},
            "params": {"start_date": "2023-06-01", "end_date": "2026-06-30"}}
    ids = [client.post("/api/v1/backtests", json=body, headers=h).json()["id"] for _ in range(2)]
    process_queue(5)
    r1, r2 = (client.get(f"/api/v1/backtests/{i}").json() for i in ids)
    assert r1["status"] == r2["status"] == "succeeded", (r1.get("error"), r2.get("error"))
    assert r1["data_fingerprint"] == r2["data_fingerprint"]
    assert r1["metrics"]["overall"] == r2["metrics"]["overall"]
    assert r1["metrics"]["fee_rate_per_side_estimate"] == pytest.approx(0.0208)


def test_alert_edge_trigger_and_dedupe(client):
    from nse_platform.alerts.engine import evaluate_all

    h = register(client, "al@x.io")
    client.post("/api/v1/alerts", json={"rule_type": "PRICE_ABOVE", "symbol": "ZZTEL", "params": {"price": 0.01},
                                        "cooldown_minutes": 5}, headers=h)
    evaluate_all()
    evaluate_all()  # condition still true -> no duplicate notification
    n = client.get("/api/v1/notifications").json()
    assert n["unread"] == 1


def test_freshness_rules(seeded):
    from nse_platform.core.db import session_scope
    from nse_platform.marketdata.freshness import classify

    with session_scope() as db:
        tue_noon = datetime(2026, 6, 30, 9, 30, tzinfo=timezone.utc)  # 12:30 EAT, Tuesday
        ok = classify(db, observed_at=datetime(2026, 6, 30, 9, 10, tzinfo=timezone.utc),
                      observation_session=date(2026, 6, 30), latency_class="DELAYED", provider_code="mystocks",
                      provider_health="HEALTHY", trading_status="ACTIVE", last_bar_volume=10, delay_minutes=15,
                      now=tue_noon)
        assert ok.status.value == "DELAYED"
        old = classify(db, observed_at=datetime(2026, 6, 29, 12, 0, tzinfo=timezone.utc),
                       observation_session=date(2026, 6, 29), latency_class="DELAYED", provider_code="mystocks",
                       provider_health="DOWN", trading_status="ACTIVE", last_bar_volume=10, now=tue_noon)
        assert old.status.value == "STALE" and old.reason == "FEED_FAILURE"
        eod = classify(db, observed_at=datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc),
                       observation_session=date(2026, 6, 30), latency_class="DELAYED", provider_code="mystocks",
                       provider_health="HEALTHY", trading_status="ACTIVE", last_bar_volume=0,
                       now=datetime(2026, 6, 30, 16, 0, tzinfo=timezone.utc))
        assert eod.status.value == "END_OF_DAY"
        live_claim = classify(db, observed_at=tue_noon, observation_session=date(2026, 6, 30),
                              latency_class="DELAYED", provider_code="mystocks", provider_health="HEALTHY",
                              trading_status="ACTIVE", last_bar_volume=1, now=tue_noon)
        assert live_claim.status.value != "LIVE"  # a delayed feed is never presented as live
        susp = classify(db, observed_at=tue_noon, observation_session=date(2026, 6, 30), latency_class="DELAYED",
                        provider_code="mystocks", provider_health="HEALTHY", trading_status="SUSPENDED",
                        last_bar_volume=0, now=tue_noon)
        assert susp.reason == "SUSPENDED"
        none = classify(db, observed_at=None, observation_session=None, latency_class=None, provider_code=None,
                        provider_health="UNCONFIGURED", trading_status=None, last_bar_volume=None)
        assert none.status.value == "UNAVAILABLE" and none.reason == "PROVIDER_NOT_CONFIGURED"


def test_ingestion_idempotent_and_revisions(seeded):
    import asyncio

    from sqlalchemy import func, select

    from nse_platform.core.db import session_scope
    from nse_platform.ingestion import jobs
    from nse_platform.models import PriceBar, PriceBarRevision
    from nse_platform.providers.synthetic import SyntheticProvider
    from tests.conftest import SYNTH_END

    with session_scope() as db:
        before = db.scalar(select(func.count()).select_from(PriceBar))
    res = asyncio.run(jobs.ingest_history({"synthetic": SyntheticProvider(end_date=SYNTH_END)}, end=SYNTH_END))
    assert res[0]["status"] == "succeeded" and res[0]["rows"] == 0  # identical re-delivery writes nothing
    with session_scope() as db:
        assert db.scalar(select(func.count()).select_from(PriceBar)) == before
        bar = db.scalar(select(PriceBar).order_by(PriceBar.ts.desc()).limit(1))
        bar.close = bar.close * 1.5  # simulate a provider later reporting a different value -> revision kept
    asyncio.run(jobs.ingest_history({"synthetic": SyntheticProvider(end_date=SYNTH_END)}, end=SYNTH_END))
    with session_scope() as db:
        assert db.scalar(select(func.count()).select_from(PriceBarRevision)) >= 1


def test_csv_import_prices(seeded, tmp_path):
    from nse_platform.providers.csv_import import run_import

    f = tmp_path / "eod.csv"
    f.write_text("date,symbol,name,open,high,low,close,volume\n"
                 "2026-06-29,TESTCO,Test Co,10,10.5,9.8,10.2,1500\n"
                 "2026-06-30,TESTCO,Test Co,,,,10.4,-\n"
                 "bad-date,TESTCO,Test Co,1,1,1,1,1\n")
    res = run_import("prices", str(f))
    assert res["accepted"] == 2 and res["rejected"] == 1


def test_health_endpoints(client):
    assert client.get("/health/live").json() == {"status": "ok"}
    r = client.get("/health/ready").json()
    assert r["checks"]["database"]["ok"] is True


def test_surge_radar_sheet_and_auto_watchlist(client):
    from nse_platform.core.db import session_scope
    from nse_platform.opportunities.surge import WATCHLIST_NAME, auto_watch

    h = register(client, "surge@x.io")
    items = client.get("/api/v1/surges", params={"days": 3650}).json()["items"]
    zz = [s for s in items if s["instrument"]["symbol"] == "ZZSURGE" and s["signal_type"] == "PRICE_VOLUME_SURGE"]
    assert zz, "synthetic demo surge on the last session must be detected live"
    with session_scope() as db:
        assert auto_watch(db, datetime(2000, 1, 1, tzinfo=timezone.utc)) >= 1
        assert auto_watch(db, datetime(2000, 1, 1, tzinfo=timezone.utc)) == 0  # idempotent
    wl = [w for w in client.get("/api/v1/watchlists").json() if w["name"] == WATCHLIST_NAME]
    assert wl and any(i["instrument"]["symbol"] == "ZZSURGE" for i in wl[0]["items"])
    assert client.get("/api/v1/notifications").json()["unread"] >= 1
    sheet = client.get(f"/api/v1/opportunities/{zz[0]['id']}/sheet", params={"shares": 1000}).json()
    pos = sheet["position"]
    assert pos["shares"] == 1000 and pos["buy_fees"]["total"] == pytest.approx(pos["consideration"] * 0.0208, rel=1e-3)
    assert pos["breakeven_move"] == pytest.approx(0.0208 * 2, abs=0.002)
    assert sheet["scenarios"], "historical analogues from the replayed surges"
    s5 = next(s for s in sheet["scenarios"] if s["horizon_sessions"] == 5)
    med = s5["returns"]["typical_median"]
    exp = 1000 * pos["entry_price"] * (1 + med) * (1 - 0.0208) - pos["total_cash_needed"]
    assert s5["pnl"]["typical_median"]["net_pnl"] == pytest.approx(exp, abs=1.0)
    assert sheet["stop"]["price"] is not None and sheet["stop"]["loss_if_hit"]["net_pnl"] < 0
    assert client.get("/api/v1/opportunities/999999/sheet").status_code == 404
    assert h


def test_intraday_surge_only_during_session(seeded):
    from sqlalchemy import select

    from nse_platform.core.db import session_scope
    from nse_platform.ingestion import store as st
    from nse_platform.models import Instrument, InstrumentSnapshot, OpportunitySignal, Quote
    from nse_platform.opportunities.surge import intraday_check

    with session_scope() as db:
        prow = st.provider_row(db, "synthetic")
        inst = db.scalar(select(Instrument).where(Instrument.symbol == "ZZTEL"))
        snap = db.scalar(select(InstrumentSnapshot).where(InstrumentSnapshot.instrument_id == inst.id,
                                                          InstrumentSnapshot.timeframe == "1d"))
        now = datetime.now(timezone.utc)
        for latency, ts in (("END_OF_DAY", now - timedelta(minutes=30)), ("DELAYED", now - timedelta(minutes=20))):
            db.add(Quote(instrument_id=inst.id, provider_id=prow.id, market_ts=ts, price=snap.last_close * 1.12,
                         volume=int(snap.avg_volume_20 * 4), currency="KES", latency_class=latency))
        db.flush()
        assert intraday_check(db, prow.id) == 1
        assert intraday_check(db, prow.id) == 0  # once per instrument per session
        sig = db.scalar(select(OpportunitySignal).where(OpportunitySignal.signal_type == "INTRADAY_SURGE"))
        assert sig.timeframe == "15m" and sig.invalidation["price"] > 0
        db.rollback()


def test_paper_order_ticket(client):
    h = register(client, "ticket@x.io")
    p = client.post("/api/v1/portfolio", json={"name": "T", "initial_cash": 50_000}, headers=h).json()
    pv = client.post(f"/api/v1/portfolio/{p['id']}/orders/preview", json={"symbol": "ZZTEL", "side": "BUY",
                                                                          "amount": 20_000}, headers=h).json()
    assert pv["can_execute"] and pv["price"] > 0 and pv["quantity"] > 0
    assert pv["consideration"] + pv["fees"]["total"] <= 20_000
    fill = client.post(f"/api/v1/portfolio/{p['id']}/orders", json={"symbol": "ZZTEL", "side": "BUY",
                                                                    "amount": 20_000}, headers=h).json()
    assert fill["filled"]["quantity"] == pv["quantity"]
    assert fill["portfolio"]["positions"][0]["quantity"] == pv["quantity"]
    big = client.post(f"/api/v1/portfolio/{p['id']}/orders/preview", json={"symbol": "ZZTEL", "side": "BUY",
                                                                           "quantity": 10_000_000}, headers=h).json()
    assert not big["can_execute"] and "Insufficient cash" in big["errors"][0]
    assert client.post(f"/api/v1/portfolio/{p['id']}/orders", json={"symbol": "ZZTEL", "side": "BUY",
                                                                     "quantity": 10_000_000}, headers=h).status_code == 400
    sold = client.post(f"/api/v1/portfolio/{p['id']}/orders", json={"symbol": "ZZTEL", "side": "SELL",
                                                                    "sell_all": True}, headers=h).json()
    assert sold["portfolio"]["positions"] == [] and sold["filled"]["held_after"] == 0
    assert client.post(f"/api/v1/portfolio/{p['id']}/orders", json={"symbol": "ZZAIR", "side": "BUY",
                                                                     "quantity": 100}, headers=h).status_code == 400
