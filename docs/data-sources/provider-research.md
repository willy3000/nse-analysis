# NSE Market-Data Provider Research

Research date: 2026-11-07. Every statement below is sourced from a page that was actually retrieved on that date; items that could not be verified are marked **UNVERIFIED**.

## Summary

| Provider | Access type | Latency class | Credentials needed | Status in this platform |
|---|---|---|---|---|
| NSE direct (Data Services) | Licensed: MITCH-UDP feed / API / EOD Excel-CSV files | Real-time, delayed (≥15 min), EOD | Commercial licence + evaluation form to `dataservices@nse.co.ke` | Not connectable without licence. EOD files are ingestible through the **CSV import adapter**. |
| ICE Data Services | Licensed: ICE Consolidated Feed / History / Data API | Real-time, delayed, EOD; history from Jan 2012 | Commercial ICE entitlement | Not connectable without contract. Adapter contract documented, not implemented (no public API spec). |
| MyStocks Africa Partner API | REST/JSON, documented OpenAPI | **15-minute DELAYED** intraday / END_OF_DAY outside session | API key (sandbox `sk_sandbox_…`, production `pk_live_…`/`pk_data_…`) | **Adapter implemented** against the published OpenAPI spec. Activates when `MYSTOCKS_API_KEY` is set. |
| Synergy Systems (live.mystocks.co.ke) | NSE-licensed information vendor | **UNVERIFIED** | Commercial | Listed on the NSE vendor page. Relationship to MyStocks Africa is UNVERIFIED. |
| Bloomberg, Refinitiv/LSEG, SIX, S&P Global, GTN | NSE-licensed information vendors | Varies | Enterprise contracts | Out of scope for a personal research platform. |
| Apify scraper actors | Scraping of nse.co.ke | — | — | **Rejected.** Unlicensed scraping must not be the core price source (see ADR-0003). |

**Bottom line:** The only provider that a single developer can integrate end to end today without a commercial agreement is the **MyStocks Africa Partner API (sandbox tier)**. Its data is **15-minute delayed**, so the platform labels it `DELAYED` (or `END_OF_DAY` outside the trading session) and never `LIVE`.

## 1. Nairobi Securities Exchange — direct

Sources:
- https://www.nse.co.ke/dataservices/delayed-data/
- https://www.nse.co.ke/dataservices/end-of-day-data/
- https://www.nse.co.ke/dataservices/real-time-data/
- https://www.nse.co.ke/dataservices/nse-licensed-information-vendors/
- https://www.nse.co.ke/dataservices/api-specification-documents/

Findings:
- **Delayed data:** "delayed by at least 15 minutes and updated every minute. Subscribers can connect directly to the NSE Data Source through APIs for Equities, bonds and equity futures products." Delayed data includes Level 1 (best bid/ask price and size, last price and size) and Level 2 market depth.
- **End-of-day data:** "available directly for subscription from the NSE in Excel/CSV versions or through NSE authorized Information Vendors." NSE also offers weekly equity statistics (dividend yield, P/E, EPS, DPS and weekly trading data) and weekly bond statistics (implied yields and yield curve).
- **Real-time data:** NSE lists real-time packages for debt, equity and futures, the FTSE NSE equity indices and the FTSE NSE Bond Index.
- **Licensing:** "A distribution license is required of any entity that passes NSE data to third parties or clients in any format, including … software application, extranet or web distribution." Creating derived data products also needs a licence.
- **Onboarding:** Complete the *NSE Market Data User Evaluation Form* (https://www.nse.co.ke/dataservices/wp-content/uploads/NSE-MARKET-DATA-USER-EVALUATION-FORM.pdf) and email it to `dataservices@nse.co.ke`, phone +254 20 2831000. Historical data is purchased through a separate *Historical Data Request Form*.
- **Protocols (from the API specification documents page):**
  - Equities market data: *Equities – Market Data Feed (MITCH – UDP) v1.22*. This is the Millennium-IT ITCH binary protocol over UDP, which normally requires network connectivity to the exchange rather than an internet REST call.
  - Equities trading: FIX 5.0 trading gateway v1.25 and Drop Copy v1.22. These are **order-execution interfaces and are deliberately not used**, because this platform must never execute trades.
  - Bonds and derivatives: MITS Market Data API and FIX specifications.
  - NSE widgets guide for the ticker and investor-relations widgets.
- **Pricing:** No price list is published on the pages retrieved. **UNVERIFIED**; it must be requested from NSE Data Services.
- **Personal-use terms, cloud-hosting restrictions, retention:** Not published. **UNVERIFIED**; these must be part of the licence negotiation.

### External action required — NSE direct
1. Fill in the Market Data User Evaluation Form and state the intended use: personal, non-redistributed research. Ask whether a hosted single-user web app counts as "redistribution".
2. Ask for: EOD CSV file specification and delivery method, historical data price list and depth, delayed API (not MITCH) availability and transport, cloud-hosting conditions, and retention rights.
3. Once EOD files are licensed, load them with `nse-cli import-csv` (see `docs/data-sources/csv-import.md`). No code changes are needed if the column mapping is configured.

## 2. ICE Data Services

Source: https://developer.ice.com/fixed-income-data-services/catalog/nairobi-securities-exchange-nse

- Level 1 streaming data (best bid/ask and last trade) for NSE equities and indices via **ICE Consolidated Feed**. Level 2 is **not** listed for NSE.
- End-of-day data via **ICE Consolidated History**, with history from **January 2012** at daily frequency.
- Delivery channels: ICE Connect, Consolidated History, Consolidated Feed, ICE Data API, ICE Data Files and ICE Global Network.
- Reference data includes instrument status (normal/halted/suspended) and market phase. Timestamps come in native exchange time plus ICE UTC time. CUSIP and ISIN identifiers may need separate licensing.
- Pricing, entitlements and field lists sit behind a login. Sales contact: https://www.ice.com/catalog/fixed-income-data-services/contact-us?venue=NairobiSE

### External action required — ICE
An enterprise data contract is needed. This is unrealistic for personal use; it only makes sense if the platform becomes a commercial product.

## 3. MyStocks Africa Partner API (integrated)

Sources: https://partners.mystocks.africa/partners/docs/getting-started-market-data, `/market-data`, `/auth`, `/rate-limits`, `/data-licensing`, `/historical-market-data`, and the machine-readable spec https://partners.mystocks.africa/openapi.json (retrieved and parsed).

- **Base URLs:** production `https://mystocks.africa/api/v1/partner`, sandbox `https://mystocks.africa/api/sandbox/v1/partner`.
- **Auth:** `Authorization: Bearer <key>` or `x-api-key: <key>`. Without a key the server returns `401 {"error":{"code":"AUTH_INVALID",…}}`; this was verified with a live request on 2026-11-07.
- **Onboarding:**
  - Sandbox: sign in to the partner portal (Firebase), then `POST /api/sandbox/v1/register`, which returns `sk_sandbox_…`.
  - Production: apply at `/partners/register`, complete KYB, then submit `POST /upgrade-request`. The docs say a `pk_live_…` key is issued within 1–2 business days.
  - A read-only `pk_data_…` key can then be derived. That is the recommended key type for this platform.
- **NSE identifiers:** exchange code `NSE`, MIC `XNAI`, currency `KES`, timezone `Africa/Nairobi`, symbols suffixed `.KE` (e.g. `SCOM.KE`). Example trading hours in the docs: 09:00–15:00.
- **Latency:** all African equities are exchange-supplied at a **15-minute delay**. Real-time is "unavailable pending feed verification". The refresh target is 900 s. Each quote carries `dataQuality{asOf, ageSeconds, stale, delayMode: DELAYED|END_OF_DAY, delayMinutes, refreshIntervalSeconds, provider, disclaimer}`. `asOf` is the exchange observation time.
- **Endpoints used by the adapter (GET only):**

| Purpose | Endpoint | Key fields |
|---|---|---|
| Instruments | `/stocks?exchange=NSE&limit=200&cursor=` | `symbol, name, exchange, exchangeMic, currency, sector, assetType (STOCK/ETF), listingStatus (ACTIVE/SUSPENDED/DELISTED)` |
| Bonds | `/bonds?exchange=NSE` | `id, symbol, issuer, couponRate, maturityDate, faceValue, pricePerUnit, yieldToMaturity` |
| Quotes | `/market/quotes?symbols=A,B` (≤50) | stock shape + `asOf, stale, dataQuality, marketStatus` |
| History | `/stocks/{symbol}/candles?interval=1d&from&to&adjustment=raw` | `candles[{timestamp, open, high, low, close, volume, ohlcAvailable, volumeAvailable}]`, `meta{source, ohlcQuality, qualityStatus, qualityIssues}` |
| Market status | `/market/status?exchange=NSE` | `anyOpen, checkedAt, exchanges{NSE:{…}}` |
| Holidays | `/market/holidays?exchange=NSE` | `data[{exchange, date, reason}]` |
| Company profile | `/companies/{symbol}` | `description, website, marketCap, peRatio` |
| Company news / corporate actions | `/companies/{symbol}/news` | `items[{source, type, title, url, exDate, payDate, amount, createdAt}]` |
| Dividends | `/dividends/{symbol}/history` | `history[{amount, currency, exDividendDate, recordDate, paymentDate, status}]` |
| Market intel | `/market-intel?exchange=NSE` | `articles[{id, title, summary, category, publishedAt}]` |

- **History range limits per request:** 1d up to 366 days, 1w up to 1,826 days, 1mo up to 3,653 days. The adapter splits longer daily backfills into windows of 366 days or less. Prices are **raw (unadjusted)**; the platform computes adjustments itself (see `docs/database/schema.md`, `price_bars` / `corporate_actions`).
- **OHLC quality caveat:** `ohlcQuality` may be `close_derived`, meaning open/high/low were not reported. The adapter keeps `ohlcAvailable=false` per bar and stores `quality_status`. Analytics that need high/low (ATR, Bollinger-on-range, swing levels) skip bars where OHLC is not genuine instead of treating close as high/low.
- **Rate limits:** Sandbox 300/min, Starter 100/min, Growth 500/min, Enterprise 2,000/min. Limits use a sliding 1-minute window and responses carry `X-RateLimit-Limit/Remaining/Reset` headers. A `429` comes with `Retry-After`. The adapter honours `Retry-After` and records the headers in `data_source_health`.
- **Licensing:** Displaying prices and charts inside the app, portfolio analytics and derived analytics are permitted. Caching is allowed for **up to 24 hours**. Redistributing raw data or building a data feed is prohibited. **Attribution is required**: "Market data provided by MyStocks Africa". The platform shows this text in the footer whenever MyStocks data is displayed.
- **Endpoints deliberately NOT used:** `/trade`, `/orders*`, `/users*`, `/portfolio*` and every other execution or brokerage endpoint. The adapter's HTTP client refuses any non-GET method.
- **Known doc discrepancy:** The `/stocks` schema says `changePct` is a percentage (example 1.54), but the quotes page says it is a decimal return. The platform **does not use** the provider's `changePct`; it recomputes change from `price` and `previousClose`.

### External action required — MyStocks
1. Create a sandbox account at https://partners.mystocks.africa/partners/sandbox and obtain the `sk_sandbox_…` key.
2. Put it in `.env` as `MYSTOCKS_API_KEY` with `MYSTOCKS_ENV=sandbox`.
3. Run `nse-cli sync-instruments && nse-cli backfill --years 5` (see `docs/deployment/operations.md`).
4. Production use requires partner KYB and acceptance of the Partner Agreement.

## 4. Transaction costs (used by the backtester and paper portfolio)

No official, consolidated NSE/CMA/CDSC fee schedule could be retrieved on 2026-11-07. The defaults below are the most consistently cited figures across broker-published schedules. They are **configurable** in `fee_schedules` and shown on every backtest report.

| Component | Default | Basis |
|---|---|---|
| Brokerage commission | 1.50% of consideration | Commonly cited cap; brokers publish 1.5–1.76% for trades under KES 100k (negotiable above) |
| VAT on brokerage | 16% of commission | Kenyan standard VAT rate |
| NSE transaction levy | 0.12% | Cited by FIB and multiple broker schedules |
| CMA levy | 0.12% | Most common figure (some sources cite 0.14%) |
| CDSC levy | 0.08% | CDSC published equity levy |
| Investor Compensation Fund | 0.01% | Bundled in the "0.34% levies" figure cited by brokers |
| Guarantee fund | 0.01% | As above |
| **All-in, per side** | **≈2.08%** | Matches the "≈2.1% all-in" figure cited for small trades |
| Dividend withholding tax (resident) | 5% | **UNVERIFIED** against the Finance Act 2025 text |
| CGT on NSE-listed securities | 0% | Finance Act 2025 exempted gains on securities traded on a CMA-licensed exchange (EY Tax News, 16 Jul 2025). Confirm the exact conditions. |

Sources: https://fib.co.ke/faqs/, https://kenyastocks.co.ke/learn/nse-stockbrokers-list-and-fees, https://taxnews.ey.com/news/2025-1498-kenya-enacts-finance-act-2025

## 5. Rejected approaches
- **Scraping nse.co.ke or vendor websites for prices.** Scraping is fragile and conflicts with NSE's distribution-licence requirement. It is not used for prices.
- **Using MyStocks execution endpoints.** This is a research platform; trade execution is out of scope by design.
- **Labelling delayed data as live.** No currently available provider delivers real-time NSE data to this platform.
