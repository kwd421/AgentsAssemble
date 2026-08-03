"""Add the room tool-mode setting.

Revision ID: 0016_room_tool_mode
Revises: 0015_public_accounts
Create Date: 2026-08-04
"""
from __future__ import annotations

from alembic import op


revision = "0016_room_tool_mode"
down_revision = "0015_public_accounts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """UPDATE room_settings
           SET data_json = jsonb_set(
               data_json,
               '{tool_mode}',
               '\"chat\"'::jsonb,
               true
           )
           WHERE NOT data_json ? 'tool_mode'"""
    )


def downgrade() -> None:
    op.execute(
        """UPDATE room_settings
           SET data_json = data_json - 'tool_mode'"""
    )
