# ADR-0001: Modular monolith, one Python package, separately deployed processes

Status: Accepted (2026-11-07)

## Context
The brief asks for clear modules: market data, analytics, opportunities, fundamentals, backtesting, portfolio, alerts, news, data quality and auth. It also asks that ingestion, analytics and the API be independently runnable, without premature microservices.

## Decision
- All backend code lives in one Python package, `nse_platform` (`services/api/`). Each domain is a subpackage with its own service functions, and no subpackage imports another's internals.
- The package has three entrypoints, all built from the same container image:
  - `nse-api`: FastAPI on uvicorn. Serves requests only and never calls providers on the request path.
  - `nse-worker`: scheduler plus job runner for ingestion, indicators, trends, opportunity detection, alert evaluation and signal-outcome tracking.
  - `nse-cli`: operator commands such as migrations, backfill, CSV import, user creation and synthetic seeding.
- The suggested `services/ingestion`, `services/analytics`, … folders become subpackages (`nse_platform/ingestion`, `nse_platform/analytics`, …) instead of separate distributions. Separate distributions would duplicate models and allow version skew between processes that share one schema.

## Consequences
- There is one test suite and one migration history.
- Processes still scale independently. Any number of `nse-api` replicas can run, alongside one or more `nse-worker` replicas. Job execution is serialised by Postgres advisory locks (ADR-0002).
