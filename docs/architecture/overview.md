# Architecture overview

A **modular monolith with independently deployed processes** (ADR-0001). One Python package (`nse_platform`) runs as three process types from the same container image. A separate Next.js app provides the UI.

```
                     ┌──────────────────────────────────────────────┐
                     │                 nse_platform                 │
 MyStocks API ─┐     │  providers/   adapters + resilient GET client│
 CSV files ────┼────►│  ingestion/   validation, upserts, run ledger│
 Synthetic ────┘     │  analytics/   indicators, series, trend,     │
 (dev only)          │               scores, calibration, pipeline  │
                     │  opportunities/ detectors, outcomes, rates   │
                     │  fundamentals/  point-in-time metrics        │
                     │  backtesting/   spec, costs, engine, runner  │
                     │  portfolio/ alerts/ auth/ marketdata/        │
                     │  api/v1/      FastAPI routers                │
                     └──────┬───────────────┬───────────────┬───────┘
                            │               │               │
                     nse-worker        nse-api          nse-cli
                     (APScheduler)     (uvicorn :8000)  (operator)
                            │               │
                            └──► PostgreSQL ◄┘   Redis (optional cache/rate limits)
                                            ▲
                       Next.js web (:3000) ─┘  via /api rewrite (same origin)
```

## Modules

| Module | Responsibility |
|---|---|
| `providers` | Common contract (`MarketDataProvider`); MyStocks adapter (GET-only, retries, rate-limit pacing); synthetic dev provider; CSV importer |
| `ingestion` | Validation (reject/flag, never impute), idempotent upserts, bar revision history, provider health, job ledger with advisory locks |
| `analytics` | Indicator library, corporate-action adjustment, resampling, trend classification, support/resistance, snapshot metrics, scores, calibration |
| `opportunities` | 17 technical detectors plus fundamental/event signals; lifecycle (active, invalidated, expired); forward outcomes; base rates |
| `fundamentals` | Point-in-time ratios from statements (publication-date aware) and dividends |
| `backtesting` | Strategy spec validation, NSE fee model, event-driven engine, queue runner |
| `portfolio` | Transaction-derived positions (average cost), P&L, allocation, time-weighted performance |
| `alerts` | Edge-triggered rule evaluation, dedupe and cooldown, pluggable notifiers (in-app) |
| `marketdata` | Freshness classification, latest-price views |
| `auth` | Argon2id, hashed session tokens, lockout, audit log |
| `api` | Versioned REST `/api/v1`, SSE update stream, error envelope, rate limiting |

## Key design rules

- **The API never calls providers.** All analytics are precomputed by the worker, so a provider outage only shows up as *stale* data.
- **Raw data is immutable.** Adjustments are derived, and provider corrections are archived in `price_bar_revisions`.
- **No look-ahead.** Indicators, detectors, fundamentals and backtests use only data available at each date; this is enforced by tests.
- **Versioned outputs.** Every analytical row stores `calc_version`, and backtests store a data fingerprint.
- **Real and synthetic data are never mixed.** Every aggregate is computed per universe.

Decisions are recorded in [`docs/decisions/`](../decisions/).
