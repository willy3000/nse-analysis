# PROJECT_STATUS

_Last updated: 2026-11-07. Phase: 4–8 implemented. Phase 9 (production hardening/deployment) outstanding._

## Run locally
```bash
bash scripts/dev.sh          # Postgres (embedded) + migrations + API :8000 + worker + web :3000
# open http://localhost:3000, create an account (local only)
```
Secrets live in `.env` at the repo root (git-ignored): `MYSTOCKS_API_KEY`, `MYSTOCKS_ENV=sandbox`, `DATABASE_URL`.

## Data
| Provider | Status | Latency |
|---|---|---|
| MyStocks Africa (sandbox key) | **Connected**: 65 NSE instruments (2 ETFs, 3 REITs), daily history from Jun 2016 | 15-min **DELAYED** / **END_OF_DAY**, never live |
| NSE direct / ICE | Not connected: commercial licence required (see docs/data-sources/provider-research.md) | — |
| CSV import | Implemented (`nse-cli import-csv prices|financials|corporate-actions|index`) | EOD |
| Synthetic | Dev only, `ZZ*` symbols, always labelled | Historical |

Not available from MyStocks: financial statements, NSE index values (NASI/NSE 20), bonds, and (in the sandbox) dividends. Fundamentals beyond provider market cap therefore show as **unavailable** until statements are imported.

## Implemented and tested
- Schema + migrations (0001–0003), provenance, quality status, calc versions, bar revision history
- Provider adapters (MyStocks verified against live sandbox), validation, idempotent ingestion, health ledger
- Indicators validated vs StockCharts and `ta`; look-ahead tests; trends (D/W/M); S/R; 17 detectors + outcome tracking and base rates; 3 versioned scores + walk-forward calibration
- Backtester (NSE fees ≈2.08%/side, slippage, liquidity, corporate actions, IS/VAL/OOS), paper portfolio, watchlists, alerts, notifications
- FastAPI `/api/v1` (sessions, CSRF, rate limiting, user isolation); APScheduler worker
- Next.js frontend, all 13 sections
- Tests: backend 61 passed (pytest, real Postgres); frontend 17 passed (vitest); `next build` OK

## Outstanding
- Dockerfiles / docker-compose / GitHub Actions CI (Docker not installed on the dev machine)
- Playwright end-to-end tests; email notification channel
- Full docs set (architecture overview, schema.md, API, ops/backup); TASKS.md
- Clean pre-2026 `market_sessions` rows from the first real sync: `DELETE FROM market_sessions WHERE session_date < '2026-01-01' AND source_provider_id = (SELECT id FROM data_providers WHERE code = 'mystocks');`
- Production: MyStocks KYB + Partner Agreement; review the NSE redistribution licence before any multi-user hosting
