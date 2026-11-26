# API reference (v1)

Interactive docs (OpenAPI/Swagger): **http://localhost:8080/api/docs**. Machine-readable: `/api/openapi.json`.

## Conventions
- Auth: session cookie `nse_session` (HttpOnly) from `POST /api/v1/auth/login`. All `/api/v1` endpoints except `auth/*` require it.
- CSRF: every POST/PUT/PATCH/DELETE must send header `X-CSRF-Token` equal to the `nse_csrf` cookie (issued by `GET /api/v1/auth/csrf`).
- Errors: `{"error": {"code": "NOT_FOUND", "message": "...", "details": ...}}`. Validation errors return 422 `VALIDATION_ERROR`.
- Pagination: `limit` and `offset`; list responses include `total`.
- Universe: market endpoints accept `universe=real|synthetic` (default real when available).
- Prices include a `freshness` object: status LIVE/DELAYED/END_OF_DAY/HISTORICAL/STALE/UNAVAILABLE, reason, as_of, provider.
- Rate limits: per IP per minute (`RATE_LIMIT_PER_MINUTE`, stricter for login). 429 responses include `Retry-After`.
- Other users' objects return 404, not 403.

## Endpoints

| Area | Method | Path | Summary |
|---|---|---|---|
| analysis | DELETE | `/api/v1/scanner/saved/{scan_id}` | Saved Delete |
| analysis | GET | `/api/v1/fundamentals/summary` | Fundamentals Summary |
| analysis | GET | `/api/v1/methodology/scores` | Score Methodology |
| analysis | GET | `/api/v1/opportunities` | Opportunities |
| analysis | GET | `/api/v1/opportunities/base-rates` | Base Rates |
| analysis | GET | `/api/v1/opportunities/detectors` | Detectors |
| analysis | GET | `/api/v1/opportunities/{signal_id}` | Opportunity |
| analysis | GET | `/api/v1/scanner/results` | Scanner Get |
| analysis | GET | `/api/v1/scanner/saved` | Saved List |
| analysis | GET | `/api/v1/trends` | Trends |
| analysis | POST | `/api/v1/scanner/results` | Scanner Post |
| analysis | POST | `/api/v1/scanner/saved` | Saved Create |
| auth | GET | `/api/v1/auth/csrf` | Issue a CSRF cookie (call once before the first POST) |
| auth | GET | `/api/v1/auth/me` | Me |
| auth | POST | `/api/v1/auth/login` | Login |
| auth | POST | `/api/v1/auth/logout` | Logout |
| auth | POST | `/api/v1/auth/register` | Register |
| data | GET | `/api/v1/admin/users` | Admin Users |
| data | GET | `/api/v1/data/coverage` | Coverage |
| data | GET | `/api/v1/data/health` | Data Health |
| data | GET | `/api/v1/data/issues` | Issues |
| data | GET | `/api/v1/data/providers` | Providers |
| data | GET | `/api/v1/data/runs` | Runs |
| data | GET | `/api/v1/events` | Events |
| data | GET | `/api/v1/news` | News |
| data | GET | `/api/v1/stream/updates` | Stream |
| data | POST | `/api/v1/admin/data-issues/{issue_id}/resolve` | Resolve Issue |
| health | GET | `/health/live` | Live |
| health | GET | `/health/ready` | Ready |
| instruments | GET | `/api/v1/instruments` | List Instruments |
| instruments | GET | `/api/v1/instruments/sectors` | Sectors |
| instruments | GET | `/api/v1/instruments/{symbol}` | Instrument Detail |
| instruments | GET | `/api/v1/instruments/{symbol}/analysis` | Analysis |
| instruments | GET | `/api/v1/instruments/{symbol}/events` | Events |
| instruments | GET | `/api/v1/instruments/{symbol}/fundamentals` | Fundamentals |
| instruments | GET | `/api/v1/instruments/{symbol}/history` | History |
| instruments | GET | `/api/v1/instruments/{symbol}/indicators` | Indicators |
| market | GET | `/api/v1/market/indices` | Indices |
| market | GET | `/api/v1/market/movers` | Movers |
| market | GET | `/api/v1/market/overview` | Overview |
| market | GET | `/api/v1/market/status` | Status |
| market | GET | `/api/v1/market/universes` | Universes |
| strategy-lab | DELETE | `/api/v1/strategies/{sid}` | Delete Strategy |
| strategy-lab | GET | `/api/v1/backtests` | Backtests |
| strategy-lab | GET | `/api/v1/backtests/{rid}` | Backtest |
| strategy-lab | GET | `/api/v1/backtests/{rid}/report.csv` | Backtest Csv |
| strategy-lab | GET | `/api/v1/strategies` | Strategies |
| strategy-lab | GET | `/api/v1/strategies/features` | Features |
| strategy-lab | POST | `/api/v1/backtests` | Queue Backtest |
| strategy-lab | POST | `/api/v1/strategies` | Create Strategy |
| strategy-lab | PUT | `/api/v1/strategies/{sid}` | Update Strategy |
| user | DELETE | `/api/v1/alerts/{rule_id}` | Delete Alert |
| user | DELETE | `/api/v1/portfolio/transactions/{txn_id}` | Delete Transaction |
| user | DELETE | `/api/v1/portfolio/{pid}` | Delete Portfolio |
| user | DELETE | `/api/v1/watchlists/{wid}` | Delete Watchlist |
| user | DELETE | `/api/v1/watchlists/{wid}/items/{item_id}` | Remove Item |
| user | GET | `/api/v1/alerts` | Alerts |
| user | GET | `/api/v1/fee-schedules` | Fee Schedules |
| user | GET | `/api/v1/notifications` | Notifications |
| user | GET | `/api/v1/portfolio` | Portfolios |
| user | GET | `/api/v1/portfolio/{pid}` | Portfolio Detail |
| user | GET | `/api/v1/portfolio/{pid}/performance` | Portfolio Performance |
| user | GET | `/api/v1/watchlists` | Watchlists |
| user | GET | `/api/v1/watchlists/{wid}` | Get Watchlist |
| user | PATCH | `/api/v1/alerts/{rule_id}` | Toggle Alert |
| user | PATCH | `/api/v1/watchlists/{wid}/items/{item_id}` | Update Item |
| user | POST | `/api/v1/alerts` | Create Alert |
| user | POST | `/api/v1/notifications/read` | Mark Read |
| user | POST | `/api/v1/portfolio` | Create Portfolio |
| user | POST | `/api/v1/portfolio/fee-preview` | Fee Preview |
| user | POST | `/api/v1/portfolio/transactions` | Add Transaction |
| user | POST | `/api/v1/watchlists` | Create Watchlist |
| user | POST | `/api/v1/watchlists/{wid}/items` | Add Item |
