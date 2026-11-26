# PROJECT_STATUS: final verification report

_Updated 2026-11-13. All phases implemented. Deployment configs are written but not yet run on a Docker host._

## Run it
```bash
bash scripts/dev.sh          # embedded Postgres + migrations + API :8080 + worker + web :3000
bash scripts/dev.sh stop
```
Or `docker compose up --build` (see [operations](docs/deployment/operations.md)). User guide: [docs/USER_GUIDE.md](docs/USER_GUIDE.md).

## Data actually connected
| Provider | Status | Latency | Coverage |
|---|---|---|---|
| MyStocks Africa (sandbox key) | **Connected, verified live** | 15-min **DELAYED** intraday / **END_OF_DAY** after close; never LIVE | 65 NSE securities (60 equities, 2 ETFs, 3 REITs); daily bars Jun 2016 → latest session (~120k bars, 99.6% with OHLC) |
| CSV import (licensed NSE files, statements) | Implemented and tested | EOD | Nothing imported yet |
| NSE direct / ICE | Not connected | — | Commercial licence required |
| Synthetic (`ZZ*`) | Dev/demo only, labelled | Historical | 17 fictitious instruments incl. the `ZZSURGE` Surge Radar demo |

Not available from MyStocks: financial statements, NSE index values, bond prices, and (in the sandbox) dividends. These are shown as *unavailable*, never as zero.

## Features
**Fully implemented and tested**
- **Ingestion:** validation, idempotent upserts, revision archive, provider health, job ledger, holiday calendar.
- **Indicators:** SMA/EMA/RSI/MACD/ROC/Bollinger/ATR/ADX/HV/OBV/relative volume/swings. Validated against StockCharts and the `ta` library, with look-ahead tests.
- **Analysis:** multi-timeframe trend classification with evidence, and support/resistance.
- **Opportunities:** 19 technical detectors plus fundamental, dividend and event signals; lifecycle, outcomes (1/5/10/20/60 bars) and base rates against a baseline.
- **Surge Radar:** intraday (each quote poll) and end-of-day surge detection, pre-breakout squeeze, auto **Surge Watch (auto)** watchlist, notifications, and the **Surge Sheet** (position cost, break-even, historical scenarios in KES after fees, trend-continuation hypothetical with its historical frequency, stop, liquidity, data-quality flags).
- **Scores:** three explainable, versioned scores; walk-forward calibration (real data: no predictive value, shown in the app).
- **Backtester:** NSE fees (~2.08% per side), slippage, liquidity cap, corporate actions, dividends net of WHT, in-sample/validation/out-of-sample segments, reproducibility fingerprint, CSV report.
- **Investment management:** paper portfolio (average cost, P&L, allocation, concentration, time-weighted return vs benchmark), watchlists, alerts (12 rule types, edge-triggered, deduped).
- **Security:** Argon2id, hashed sessions, CSRF, rate limits, lockout, audit log, user isolation (tested), security headers.
- **Frontend:** 14 sections with freshness badges on every price.

**Implemented, awaiting external data:** fundamentals-based metrics, value signals and the Investment Quality Score (need statements); index benchmarks (need index files).

**Partially implemented:** notifications are in-app only (no email channel); deployment is written but not executed.

**Not implemented:** Playwright end-to-end tests, admin UI, MFA and password reset.

## Verification (2026-11-13)
| Check | Result |
|---|---|
| Backend tests (pytest, real PostgreSQL 16) | 65 passed |
| Frontend unit tests (vitest) | 17 passed |
| TypeScript / `next build` | clean / success |
| `npm audit --omit=dev` | 0 vulnerabilities (Next 15.5.29, patched postcss/sharp) |
| `pip-audit` | runtime deps clean; only finding is `filelock` in the dev venv (used by pip-audit itself, not by the platform) |
| Backup → restore into a scratch DB | identical row counts |
| Live MyStocks sandbox | all data endpoints 200; payload differences from the spec handled and regression-tested |

## External actions required (owner)
1. MyStocks production KYB + Partner Agreement for a production key.
2. Review the NSE distribution-licence terms before hosting for anyone other than yourself (`dataservices@nse.co.ke`).
3. Choose hosting; install Docker (or use a VPS) to run `docker compose`.
4. Optionally import financial statements and index history (docs/data-sources/csv-import.md).

Open tasks: [TASKS.md](TASKS.md).
