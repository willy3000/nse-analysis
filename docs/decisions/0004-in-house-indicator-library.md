# ADR-0004: In-house indicator library, validated against an independent implementation

Status: Accepted (2026-11-07)

## Context
The brief suggested pandas-ta or TA-Lib "after validating compatibility":
- TA-Lib needs a native C library build, which is painful on Windows and in slim containers.
- Recent pandas-ta releases require Python ≥ 3.12 plus numba, and the project has had long unmaintained periods.
- Neither library documents warm-up and NaN semantics explicitly enough to prove there is no look-ahead bias.

## Decision
- Indicators are implemented in `nse_platform/analytics/indicators.py` with vectorised pandas/numpy and documented formulas:
  - Wilder smoothing for RSI and ATR, seeded with the simple mean of the first *n* values.
  - EMA with `adjust=False`, seeded with the SMA of the first *n* values.
  - Population standard deviation for Bollinger Bands, matching TA-Lib and TradingView.
- Every indicator returns `NaN` until it has enough observations. Missing values never become zero.
- Validation (tests in `tests/analytics/`):
  1. Hand-computed reference values on small series.
  2. Cross-checks against the independent `ta` library (bukosabino/ta) on long random-walk series.
  3. A **look-ahead test** for every indicator: the value at *t* computed on `series[:t+1]` must equal the value at *t* computed on the full series.
- Every stored indicator value carries `calc_version` (`INDICATOR_CALC_VERSION`).
