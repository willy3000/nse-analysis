"""Views (dividend_history, company_announcements), optional TimescaleDB, reference seed data.

Revision ID: 0002
Revises: 0001
"""
from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

DEFAULT_FEES = {
    "brokerage_rate": 0.015,
    "brokerage_min": 0.0,
    "vat_on_brokerage": 0.16,
    "nse_levy": 0.0012,
    "cma_levy": 0.0012,
    "cdsc_levy": 0.0008,
    "icf_levy": 0.0001,
    "guarantee_fund": 0.0001,
    "stamp_duty": 0.0,
    "capital_gains_tax": 0.0,
}
FEE_NOTES = (
    "Defaults compiled 2026-11-07 from broker-published schedules (FIB, KenyaStocks). No consolidated official "
    "NSE/CMA/CDSC schedule could be retrieved. Brokerage 1.5% + 16% VAT on commission; NSE 0.12%, CMA 0.12%, "
    "CDSC 0.08%, ICF 0.01%, Guarantee Fund 0.01%. Approx 2.08% per side. CGT on NSE-listed securities exempt per "
    "Finance Act 2025 (confirm conditions). Verify against your broker's contract note."
)


def upgrade() -> None:
    op.execute(
        """
        CREATE VIEW dividend_history AS
        SELECT ca.id, ca.instrument_id, ca.provider_id, ca.status, ca.announced_at, ca.ex_date, ca.record_date,
               ca.payment_date, ca.cash_amount AS amount_per_share, ca.currency, ca.title, ca.source_url
        FROM corporate_actions ca
        WHERE ca.action_type = 'DIVIDEND_CASH'
        """
    )
    op.execute(
        """
        CREATE VIEW company_announcements AS
        SELECT n.*, nai.instrument_id
        FROM news_articles n JOIN news_article_instruments nai ON nai.article_id = n.id
        WHERE n.kind IN ('ANNOUNCEMENT', 'RESULTS', 'CORPORATE_ACTION', 'REGULATORY')
        """
    )

    # TimescaleDB hypertables only when the extension is installable (ADR-0006).
    bind = op.get_bind()
    has_ts = bind.execute(
        sa.text("SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb'")
    ).scalar()
    if has_ts:
        op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
        op.execute("SELECT create_hypertable('price_bars', 'ts', migrate_data => true, if_not_exists => true)")
        op.execute("SELECT create_hypertable('quotes', 'market_ts', migrate_data => true, if_not_exists => true)")

    exchanges = sa.table(
        "exchanges",
        sa.column("code", sa.String), sa.column("mic", sa.String), sa.column("name", sa.String),
        sa.column("country", sa.String), sa.column("currency", sa.String), sa.column("timezone", sa.String),
        sa.column("open_time", sa.Time), sa.column("close_time", sa.Time),
    )
    import datetime as dt

    op.bulk_insert(exchanges, [{
        "code": "NSE", "mic": "XNAI", "name": "Nairobi Securities Exchange", "country": "KE", "currency": "KES",
        "timezone": "Africa/Nairobi", "open_time": dt.time(9, 0), "close_time": dt.time(15, 0),
    }])

    providers = sa.table(
        "data_providers",
        sa.column("code", sa.String), sa.column("name", sa.String), sa.column("latency_class", sa.String),
        sa.column("delay_minutes", sa.Integer), sa.column("is_synthetic", sa.Boolean),
        sa.column("enabled", sa.Boolean), sa.column("priority", sa.Integer), sa.column("attribution", sa.Text),
        sa.column("notes", sa.Text),
    )
    op.bulk_insert(providers, [
        {"code": "mystocks", "name": "MyStocks Africa Partner API", "latency_class": "DELAYED", "delay_minutes": 15,
         "is_synthetic": False, "enabled": True, "priority": 20,
         "attribution": "Market data provided by MyStocks Africa",
         "notes": "15-minute delayed NSE data; END_OF_DAY outside session. Requires MYSTOCKS_API_KEY."},
        {"code": "nse_csv", "name": "NSE licensed EOD files (CSV import)", "latency_class": "END_OF_DAY",
         "delay_minutes": None, "is_synthetic": False, "enabled": True, "priority": 10,
         "attribution": "Source: Nairobi Securities Exchange (licensed end-of-day data)",
         "notes": "Operator-imported licensed NSE end-of-day files."},
        {"code": "nse_direct", "name": "NSE Data Services (direct feed)", "latency_class": "LIVE",
         "delay_minutes": 0, "is_synthetic": False, "enabled": False, "priority": 1, "attribution": None,
         "notes": "MITCH-UDP / API feed. Requires commercial licence (dataservices@nse.co.ke). Not implemented."},
        {"code": "ice", "name": "ICE Consolidated Feed", "latency_class": "LIVE", "delay_minutes": 0,
         "is_synthetic": False, "enabled": False, "priority": 2, "attribution": None,
         "notes": "Requires ICE enterprise entitlement. Not implemented."},
        {"code": "synthetic", "name": "Synthetic development data", "latency_class": "HISTORICAL",
         "delay_minutes": None, "is_synthetic": True, "enabled": True, "priority": 1000,
         "attribution": "SYNTHETIC DEVELOPMENT DATA - not market data",
         "notes": "Deterministic generated series for development and tests (ADR-0007)."},
    ])

    op.execute(
        sa.text(
            "INSERT INTO fee_schedules (code, name, components, effective_from, source_notes, is_default) "
            "VALUES (:code, :name, CAST(:components AS jsonb), :eff, :notes, true)"
        ).bindparams(
            code="KE_EQUITY_2026", name="Kenya equities - typical retail (2026)",
            components=json.dumps(DEFAULT_FEES), eff=dt.date(2025, 7, 1), notes=FEE_NOTES,
        )
    )


def downgrade() -> None:
    op.execute("DELETE FROM fee_schedules WHERE code = 'KE_EQUITY_2026'")
    op.execute("DELETE FROM data_providers WHERE code IN ('mystocks','nse_csv','nse_direct','ice','synthetic')")
    op.execute("DELETE FROM exchanges WHERE code = 'NSE'")
    op.execute("DROP VIEW IF EXISTS company_announcements")
    op.execute("DROP VIEW IF EXISTS dividend_history")
