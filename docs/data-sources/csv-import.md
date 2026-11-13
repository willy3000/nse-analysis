# CSV / Excel import formats

```bash
cd services/api
.venv/Scripts/python -m nse_platform.cli import-csv prices FILE.csv [--mapping map.json]
.venv/Scripts/python -m nse_platform.cli import-csv financials FILE.csv
.venv/Scripts/python -m nse_platform.cli import-csv corporate-actions FILE.csv
.venv/Scripts/python -m nse_platform.cli import-csv index FILE.csv
```

Every import goes through the same validation as provider data and is logged in the Data Centre (job `csv_import:<kind>`). Blank, `-`, `--` and `N/A` cells are treated as **missing**, never zero. `.xlsx` files are accepted.

## prices (licensed NSE end-of-day files)
Required: `date, symbol, close`. Optional: `open, high, low, volume, turnover, trades, name, sector, security_type, isin, currency`.
Unknown symbols are created as instruments under provider `nse_csv`, which has priority over MyStocks.

## financials (published annual/interim reports)
Required: `symbol, period_end, statement_type, line_item, value, published_at, source`.
Optional: `period_type` (FY default, H1, Q), `currency`, `unit` (`1`, `thousands`/`KES'000`, `millions`, `billions`), `source_url`.

- `statement_type`: INCOME, BALANCE or CASHFLOW.
- `published_at` must be the date the figures became public. It prevents look-ahead in backtests, and rows published before the period end are rejected.
- Recognised `line_item` keys: revenue, gross_profit, operating_income, net_income, interest_expense, eps, dps, total_assets, total_equity, total_debt, cash, current_assets, current_liabilities, shares_outstanding, operating_cash_flow, capex.
- `eps` and `dps` are per share and are not scaled by `unit`.

```csv
symbol,period_end,period_type,statement_type,line_item,value,unit,currency,published_at,source,source_url
SCOM,2025-03-31,FY,INCOME,revenue,388.7,billions,KES,2025-05-08,Annual report FY2025 p.12,https://...
SCOM,2025-03-31,FY,INCOME,eps,1.55,1,KES,2025-05-08,Annual report FY2025 p.12,
```

## corporate-actions
Required: `symbol, action_type` (DIVIDEND_CASH, BONUS, SPLIT, CONSOLIDATION, RIGHTS, SUSPENSION, OTHER).
Optional: `ex_date, record_date, payment_date, effective_date, cash_amount, currency, ratio_from, ratio_to, title, status, source_url, external_id`.
A 1-for-10 bonus is `ratio_from=10, ratio_to=11`.

## index
Required: `date, index_code, close`. Optional: `index_name, open, high, low`. Imported indices appear on the Overview and can be used as a backtest benchmark (`benchmark: "NASI"`).

## Mapping file
Adapts any column layout and date format:
```json
{"columns": {"date": "Trade Date", "symbol": "Code", "close": "Day Price", "volume": "Volume"},
 "date_format": "%d-%b-%y", "provider": "nse_csv", "exchange": "NSE"}
```
