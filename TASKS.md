# TASKS

Legend: [x] done and verified · [ ] open · (ext) needs an external action by the owner

## Done
- [x] Provider research, ADRs 0001–0007, architecture, data flow, schema and API docs
- [x] PostgreSQL schema + migrations 0001–0003; backup/restore script (restore verified row-for-row)
- [x] MyStocks adapter verified against the live sandbox; CSV import; synthetic dev provider
- [x] Validated ingestion, revision history, provider health, job ledger with advisory locks
- [x] Indicator library (validated vs StockCharts and `ta`), trends, S/R, 19 technical detectors, outcomes, base rates
- [x] Three explainable scores + walk-forward calibration
- [x] Backtester with NSE fees, slippage, liquidity, corporate actions, IS/VAL/OOS segments
- [x] Paper portfolio, watchlists, alerts + in-app notifications
- [x] **Surge Radar**: intraday + EOD surge detection, pre-breakout squeeze, auto "Surge Watch" watchlist, Surge Sheet
- [x] FastAPI v1 (auth, CSRF, rate limits, isolation), worker scheduler, SSE updates
- [x] Next.js frontend: 14 sections incl. Surge Radar; dark/light; keyboard search
- [x] Tests: backend pytest on real Postgres, frontend vitest, production build; npm audit clean
- [x] Dockerfiles, docker-compose, GitHub Actions CI, `.env.example`
- [x] User guide, operations, security docs

## Open
- [ ] Verify Docker images/compose on a machine with Docker (not installed on the dev PC)
- [ ] Playwright end-to-end tests
- [ ] Email notification channel (SMTP) + password reset
- [ ] Admin UI (users, resolve data-quality issues)
- [ ] Calibrate Investment Quality and Risk scores; revisit trading-score weights (no predictive value on real data)
- [ ] Surge Radar: per-user opt-out of auto-watch; tune thresholds once more real surges are recorded
- [ ] (ext) Import financial statements for companies of interest (`import-csv financials`)
- [ ] (ext) Licensed NSE index history (NASI/NSE 20) for benchmarks
- [ ] (ext) MyStocks production KYB + Partner Agreement; NSE redistribution licence before multi-user hosting
- [ ] (ext) Choose hosting and deploy (docs/deployment/operations.md)
