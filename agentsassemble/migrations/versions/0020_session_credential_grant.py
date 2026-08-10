"""Link room sessions to the credential grant that created them.

Revision ID: 0020_session_credential_grant
Revises: 0019_membership_invite_scope
Create Date: 2026-08-10
"""
from __future__ import annotations

from alembic import op


revision = "0020_session_credential_grant"
down_revision = "0019_membership_invite_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE room_access_sessions "
        "ADD COLUMN credential_auth_key TEXT NOT NULL DEFAULT ''"
    )
    op.execute(
        "CREATE INDEX idx_room_access_sessions_credential "
        "ON room_access_sessions(credential_auth_key)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX idx_room_access_sessions_credential")
    op.execute("ALTER TABLE room_access_sessions DROP COLUMN credential_auth_key")
