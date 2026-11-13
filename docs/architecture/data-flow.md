# Data flow

## Ingestion (worker)

| Schedule (EAT) | Job | Steps |
|---|---|---|
| Every 15 min, Mon–Fri 09:00–15:45 | `poll_quotes` | Batch quotes (≤ 50 symbols per request) → validate → insert immutable observation (`quotes`) |
| Mon–Fri 16:30 | `ingest_history` → `analytics` → `alerts` | Daily bars from last stored bar minus 10 days (catches revisions) → validate → upsert + revision archive → full analytics → alert evaluation |
| Mon–Fri 17:00 | `ingest_corporate` | Corporate actions (sandbox fixtures skipped), dividends, company profiles, company news, market intel |
| Sun 02:00 | weekly | Instrument sync + holiday calendar (2-year windows), full signal replay, score calibration |
| Every 5 s | backtest queue | `SELECT … FOR UPDATE SKIP LOCKED` → run → persist |

Every job takes a Postgres advisory lock (no double runs), writes an `ingestion_runs` row (`running` → `succeeded`/`partial`/`failed`/`skipped`), and isolates failures per provider and per instrument. Provider health (`data_source_health`) becomes HEALTHY, DEGRADED (1–2 consecutive failures) or DOWN (3 or more).

## Validation outcomes

| Condition | Result |
|---|---|
| Close ≤ 0, NaN, future timestamp, naive timestamp, negative volume | Rejected, issue recorded |
| Duplicate bar in a batch | Second copy dropped, issue recorded |
| High/low inconsistent with open/close | Close kept; O/H/L set to NULL; quality SUSPECT |
| O/H/L or volume not reported | Kept as NULL; quality PARTIAL |
| Move > 50% without a corporate action | Kept; quality SUSPECT |
| Price change on zero volume | Kept; informational issue |

## Analytics pipeline (per data universe)

1. Load raw daily bars from the instrument's primary provider.
2. Apply share-ratio adjustments (bonus/split factors).
3. Resample to weekly/monthly (strict aggregation; incomplete periods excluded from classification).
4. Indicators → `technical_indicators`.
5. Trend classification → `trend_analysis`.
6. Support/resistance → `support_resistance_levels`.
7. Detectors on 1d and 1w → `opportunity_signals`; lifecycle updates; forward outcomes → `opportunity_outcomes`.
8. Point-in-time fundamentals → `fundamental_metrics`.
9. Cross-sectional: relative strength, three scores → `instrument_snapshots`; fundamental/event signals; base rates → `analytics_reports`.

## Serving

The browser calls `/api/v1/*` on the web origin; Next.js rewrites it to the API. Responses carry provenance (`provider`, `is_synthetic`) and a freshness object per price. An SSE stream (`/api/v1/stream/updates`) emits an event when a new job run finishes, and the UI then refetches.
