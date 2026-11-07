"""End-to-end smoke test of the API against the configured database (dev use).

Usage: DATABASE_URL=... services/api/.venv/Scripts/python scripts/smoke_api.py
Exercises auth + CSRF, every read endpoint, and the write flows (portfolio, watchlist, alert, strategy,
backtest executed through the worker runner). Exits non-zero on the first failure.
"""

from __future__ import annotations

import secrets
import sys
from datetime import date, timedelta

from fastapi.testclient import TestClient

from nse_platform.backtesting.runner import process_queue
from nse_platform.main import app

c = TestClient(app)
fails = 0


def check(label: str, r, status: int = 200):  # noqa: ANN001, ANN201
    global fails
    ok = r.status_code == status
    print(f"{'OK  ' if ok else 'FAIL'} {r.status_code} {label}")
    if not ok:
        fails += 1
        print("     ", r.text[:800])
    return r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text


check("unauthenticated overview -> 401", c.get("/api/v1/market/overview"), 401)
c.get("/api/v1/auth/csrf")
csrf = c.cookies.get("nse_csrf")
check("register without CSRF -> 403", c.post("/api/v1/auth/register", json={"email": "x@y.zz", "password": "a"}), 403)
H = {"X-CSRF-Token": csrf}
email = f"smoke-{secrets.token_hex(4)}@example.com"
check("register", c.post("/api/v1/auth/register", json={"email": email, "password": "Sm0keTest-pass"}, headers=H))
H = {"X-CSRF-Token": c.cookies.get("nse_csrf")}
check("me", c.get("/api/v1/auth/me"))
ov = check("overview", c.get("/api/v1/market/overview"))
print("      universe:", ov.get("universe"), "breadth:", ov.get("breadth"))
check("status", c.get("/api/v1/market/status"))
check("indices", c.get("/api/v1/market/indices"))
check("movers", c.get("/api/v1/market/movers"))
inst = check("instruments", c.get("/api/v1/instruments?limit=5"))
sym = inst["items"][0]["symbol"]
print("      first:", sym, inst["items"][0]["price"]["freshness"])
check("instrument detail", c.get(f"/api/v1/instruments/{sym}"))
h = check("history 1w", c.get(f"/api/v1/instruments/{sym}/history?timeframe=1w"))
print("      weekly bars:", len(h["items"]))
check("indicators", c.get(f"/api/v1/instruments/{sym}/indicators?names=rsi_14,sma_20&limit=10"))
a = check("analysis", c.get(f"/api/v1/instruments/{sym}/analysis"))
print("      trends:", {k: (v or {}).get("classification") for k, v in a["trends"].items()})
check("fundamentals", c.get(f"/api/v1/instruments/{sym}/fundamentals"))
check("events", c.get(f"/api/v1/instruments/{sym}/events"))
check("sectors", c.get("/api/v1/instruments/sectors"))
sc = check("scanner", c.post("/api/v1/scanner/results", json={"rsi_max": 70, "trading_score_min": 40}, headers=H))
print("      scanner total:", sc["total"])
check("scanner bad sort -> 400", c.post("/api/v1/scanner/results", json={"sort": "x"}, headers=H), 400)
check("save scan", c.post("/api/v1/scanner/saved", json={"name": "test", "filters": {"rsi_max": 30}}, headers=H))
check("saved scans", c.get("/api/v1/scanner/saved"))
op = check("opportunities", c.get("/api/v1/opportunities"))
print("      active signals:", op["total"])
if op["items"]:
    check("opportunity detail", c.get(f"/api/v1/opportunities/{op['items'][0]['id']}"))
check("base rates", c.get("/api/v1/opportunities/base-rates"))
check("detectors", c.get("/api/v1/opportunities/detectors"))
check("trends", c.get("/api/v1/trends?timeframe=1w"))
check("score methodology", c.get("/api/v1/methodology/scores"))
check("news", c.get("/api/v1/news"))
check("events calendar", c.get("/api/v1/events?days_back=400"))
check("data health", c.get("/api/v1/data/health"))
check("data providers", c.get("/api/v1/data/providers"))
check("data runs", c.get("/api/v1/data/runs"))
check("data issues", c.get("/api/v1/data/issues"))
check("data coverage", c.get("/api/v1/data/coverage"))
check("fee schedules", c.get("/api/v1/fee-schedules"))
check("fee preview", c.post("/api/v1/portfolio/fee-preview", json={"consideration": 100000}, headers=H))

p = check("create portfolio", c.post("/api/v1/portfolio", json={"name": "Smoke", "initial_cash": 500000}, headers=H))
pid = p["id"]
d = (date.today() - timedelta(days=60)).isoformat()
check("deposit dated earlier", c.post("/api/v1/portfolio/transactions", json={
    "portfolio_id": pid, "txn_type": "DEPOSIT", "trade_date": d, "amount": 1000}, headers=H))
check("buy", c.post("/api/v1/portfolio/transactions", json={
    "portfolio_id": pid, "txn_type": "BUY", "trade_date": date.today().isoformat(), "symbol": sym, "quantity": 100,
    "price": 10}, headers=H))
check("oversell -> 400", c.post("/api/v1/portfolio/transactions", json={
    "portfolio_id": pid, "txn_type": "SELL", "trade_date": date.today().isoformat(), "symbol": sym,
    "quantity": 1000, "price": 10}, headers=H), 400)
check("overspend -> 400", c.post("/api/v1/portfolio/transactions", json={
    "portfolio_id": pid, "txn_type": "BUY", "trade_date": date.today().isoformat(), "symbol": sym,
    "quantity": 10_000_000, "price": 10}, headers=H), 400)
pd_ = check("portfolio detail", c.get(f"/api/v1/portfolio/{pid}"))
print("      cash:", round(pd_["cash"], 2), "positions:", len(pd_["positions"]), "fees:", pd_["fees_paid"])
check("portfolio performance", c.get(f"/api/v1/portfolio/{pid}/performance"))
check("portfolio list", c.get("/api/v1/portfolio"))

w = check("create watchlist", c.post("/api/v1/watchlists", json={"name": "Smoke WL"}, headers=H))
check("add item", c.post(f"/api/v1/watchlists/{w['id']}/items", json={"symbol": sym, "tags": ["core"],
                                                                       "target_price": 999}, headers=H))
check("duplicate item -> 409", c.post(f"/api/v1/watchlists/{w['id']}/items", json={"symbol": sym}, headers=H), 409)
check("watchlists", c.get("/api/v1/watchlists"))

check("create alert", c.post("/api/v1/alerts", json={"rule_type": "PRICE_ABOVE", "symbol": sym,
                                                     "params": {"price": 0.01}}, headers=H))
check("alert missing params -> 400", c.post("/api/v1/alerts", json={"rule_type": "RSI_BELOW", "symbol": sym},
                                            headers=H), 400)
from nse_platform.alerts.engine import evaluate_all  # noqa: E402

print("      alert eval:", evaluate_all())
n = check("notifications", c.get("/api/v1/notifications"))
print("      notifications:", n["unread"])

feat = check("strategy features", c.get("/api/v1/strategies/features"))
tpl = feat["templates"][0]["definition"]
tpl["universe"] = {"synthetic": ov.get("universe") == "synthetic"}
s = check("create strategy", c.post("/api/v1/strategies", json={"name": "Smoke GC", "definition": tpl}, headers=H))
check("invalid strategy -> 422", c.post("/api/v1/strategies", json={"name": "bad", "definition": {
    "entry": [{"left": "nope", "op": ">", "right": 1}]}}, headers=H), 422)
bt = check("queue backtest", c.post("/api/v1/backtests", json={"strategy_id": s["id"], "params": {
    "start_date": "2018-01-01", "end_date": date.today().isoformat()}}, headers=H), 202)
print("      processed:", process_queue())
r = check("backtest result", c.get(f"/api/v1/backtests/{bt['id']}"))
print("      status:", r["status"], r.get("error"), "trades:", len(r.get("trades", [])))
print("      overall:", {k: r["metrics"]["overall"].get(k) for k in ("total_return", "cagr", "sharpe", "max_drawdown",
                                                                     "trades", "win_rate")} if r.get("metrics") else None)
print("      OOS:", r["metrics"]["segments"]["out_of_sample"] if r.get("metrics") else None)
print("      warnings:", r.get("warnings"))
check("backtest csv", c.get(f"/api/v1/backtests/{bt['id']}/report.csv"))
check("admin endpoint as user -> 403", c.get("/api/v1/admin/users"), 403)
check("logout", c.post("/api/v1/auth/logout", headers=H))
check("after logout -> 401", c.get("/api/v1/auth/me"), 401)
check("health ready", c.get("/health/ready"))
print("FAILURES:", fails)
sys.exit(1 if fails else 0)
