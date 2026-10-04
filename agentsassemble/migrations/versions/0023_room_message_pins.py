"""Add durable channel-scoped message pins.

Revision ID: 0023_room_message_pins
Revises: 0022_google_profile_minimization
Create Date: 2026-08-22
"""
from __future__ import annotations

from alembic import op


revision = "0023_room_message_pins"
down_revision = "0022_google_profile_minimization"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE room_message_pins (
               room_id TEXT NOT NULL REFERENCES rooms(room_id) ON DELETE CASCADE,
               channel_id TEXT NOT NULL,
               event_id TEXT NOT NULL,
               pinned_by TEXT NOT NULL,
               pinned_at TIMESTAMPTZ NOT NULL,
               PRIMARY KEY (room_id, channel_id, event_id)
           )"""
    )
    op.execute(
        """CREATE INDEX idx_message_pins_channel
           ON room_message_pins(room_id, channel_id, pinned_at DESC)"""
    )


def downgrade() -> None:
    op.execute("DROP TABLE room_message_pins")
