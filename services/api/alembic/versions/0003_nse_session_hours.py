"""NSE continuous trading opens at 09:30 EAT (verified against provider market status 2026-11-07).

Revision ID: 0003
Revises: 0002
"""
from __future__ import annotations

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE exchanges SET open_time = '09:30' WHERE code = 'NSE'")


def downgrade() -> None:
    op.execute("UPDATE exchanges SET open_time = '09:00' WHERE code = 'NSE'")
