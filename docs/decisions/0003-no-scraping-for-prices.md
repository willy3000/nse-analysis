# ADR-0003: Core price data only from authorised sources; no price scraping

Status: Accepted (2026-11-07)

## Decision
Price, volume and corporate-action data enter the system only through:
1. Licensed provider APIs. Today that is the MyStocks Partner API; NSE direct and ICE once contracted.
2. Licensed file downloads, imported with the CSV import adapter (for example NSE EOD Excel/CSV subscriptions).
3. The **synthetic development provider** (ADR-0007), which is never enabled in production.

No adapter scrapes nse.co.ke or vendor web pages for prices.

## Rationale
NSE requires a distribution licence for any distribution of its data through a software application (see `docs/data-sources/provider-research.md`). Scraped data also has no guaranteed provenance, and its freshness cannot be established.
