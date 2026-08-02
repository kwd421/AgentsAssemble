"""Add one-time guest identity recovery codes.

Revision ID: 0014_guest_recovery_codes
Revises: 0013_durable_server_room_ids
Create Date: 2026-08-02
"""
from __future__ import annotations

from alembic import op


revision = "0014_guest_recovery_codes"
down_revision = "0013_durable_server_room_ids"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE identity_recovery_codes (
               token_fingerprint TEXT PRIMARY KEY,
               user_id TEXT NOT NULL
                   REFERENCES identity_users(user_id) ON DELETE CASCADE,
               created_at TEXT NOT NULL,
               consumed_at TEXT NOT NULL DEFAULT '',
               revoked_at TEXT NOT NULL DEFAULT '',
               replacement_fingerprint TEXT NOT NULL DEFAULT ''
           )"""
    )
    op.execute(
        """CREATE INDEX idx_identity_recovery_user
           ON identity_recovery_codes(user_id, created_at)"""
    )


def downgrade() -> None:
    op.execute("DROP TABLE identity_recovery_codes")
