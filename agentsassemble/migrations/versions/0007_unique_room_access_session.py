"""Enforce one active room access session per participant and room.

Revision ID: 0007_unique_room_access_session
Revises: 0006_identity_authority
Create Date: 2026-07-15
"""
from __future__ import annotations

from alembic import op


revision = "0007_unique_room_access_session"
down_revision = "0006_identity_authority"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP INDEX idx_room_access_sessions_participant")
    op.execute(
        """CREATE UNIQUE INDEX uq_room_access_sessions_participant
           ON room_access_sessions(room_id, participant_id)"""
    )


def downgrade() -> None:
    op.execute("DROP INDEX uq_room_access_sessions_participant")
    op.execute(
        """CREATE INDEX idx_room_access_sessions_participant
           ON room_access_sessions(room_id, participant_id)"""
    )
