"""Persist immutable authenticated principals on room access sessions.

Revision ID: 0017_session_principals
Revises: 0016_room_tool_mode
Create Date: 2026-08-06
"""
from __future__ import annotations

from alembic import op


revision = "0017_session_principals"
down_revision = "0016_room_tool_mode"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE room_access_sessions "
        "ADD COLUMN principal_user_id TEXT NOT NULL DEFAULT ''"
    )
    op.execute(
        "ALTER TABLE room_access_sessions "
        "ADD COLUMN principal_is_operator BOOLEAN NOT NULL DEFAULT FALSE"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE room_access_sessions DROP COLUMN principal_is_operator"
    )
    op.execute(
        "ALTER TABLE room_access_sessions DROP COLUMN principal_user_id"
    )
