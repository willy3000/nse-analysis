# Roadmap

| Phase | Scope | Status |
|---|---|---|
| 1 Research & architecture | Providers, ADRs, schema, plan | Done |
| 2 Foundation | Monorepo, API, DB, migrations, logging, health, tests | Done (Docker/CI written, not verified locally) |
| 3 Market data | Adapters, ingestion, validation, freshness, monitoring | Done (MyStocks delayed/EOD) |
| 4 Dashboard | Overview, catalogue, instrument pages, charts | Done |
| 5 Analytics | Indicators, trends, S/R, signals, scores, Surge Radar | Done |
| 6 Fundamentals & news | PIT metrics, dividends, corporate actions, news | Done; statements need imports |
| 7 Strategy lab | Backtester, costs, OOS validation, reports | Done |
| 8 Portfolio & alerts | Paper portfolio, watchlists, alerts | Done (in-app channel) |
| 9 Hardening | Security review, audits, backups, CI, deploy | Partly done; deployment pending hosting choice |

Next: deploy, e2e tests, email channel, fundamentals imports, score recalibration.
