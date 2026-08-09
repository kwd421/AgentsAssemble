"""Add a durable room-wide write admission budget.

Revision ID: 0018_room_write_budget
Revises: 0017_session_principals
Create Date: 2026-08-10
"""
from __future__ import annotations

from alembic import op


revision = "0018_room_write_budget"
down_revision = "0017_session_principals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE room_write_budgets (
               room_id TEXT NOT NULL,
               window_started_at BIGINT NOT NULL,
               command_count BIGINT NOT NULL,
               payload_bytes BIGINT NOT NULL,
               PRIMARY KEY (room_id, window_started_at)
           )"""
    )
    op.execute(
        "CREATE INDEX idx_room_write_budgets_window "
        "ON room_write_budgets(window_started_at)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE room_write_budgets")
