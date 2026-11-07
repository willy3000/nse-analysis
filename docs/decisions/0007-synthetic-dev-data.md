# ADR-0007: Isolated, labelled synthetic development data

Status: Accepted (2026-11-07)

## Context
No market-data credentials were available at build time. The UI, analytics, backtester and end-to-end tests still need realistic time series.

## Decision
- **Provider:** code `synthetic`, with `data_providers.is_synthetic = true`.
- **Instruments:**
  - Fictitious symbols prefixed `ZZ` (e.g. `ZZBNK1`), so none resembles a real NSE ticker.
  - Names end with "(Synthetic)".
  - `instruments.is_synthetic = true`.
- **Prices:** a seeded geometric random walk with regime switches, illiquid days with no trades, and an occasional suspension. Output is deterministic for a given seed, so tests are reproducible.
- **Production guard:** `nse-cli seed-synthetic` refuses to run when `APP_ENV=production`, and the API refuses to start in production if synthetic rows exist unless `ALLOW_SYNTHETIC_IN_PRODUCTION=true` is set explicitly.
- **Labelling:**
  - Every instrument, quote and bar response carries `provider` and `is_synthetic`.
  - The frontend shows a non-dismissable banner, "SYNTHETIC DEVELOPMENT DATA — not market data", whenever synthetic instruments are present.
  - Synthetic instruments carry a `SYNTHETIC` badge everywhere they appear.
- **Separation:** market-wide aggregates (breadth, movers, overview) are computed per data universe. Real and synthetic instruments are never mixed in one aggregate.
- **Cleanup:** `nse-cli purge-synthetic` deletes all synthetic rows (cascading).
