"""Fail closed for memberships without historical write authority.

Revision ID: 0021_membership_scope_audit
Revises: 0020_session_credential_grant
Create Date: 2026-08-10
"""
from __future__ import annotations

from alembic import op


revision = "0021_membership_scope_audit"
down_revision = "0020_session_credential_grant"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """UPDATE identity_memberships AS membership
           SET invite_scope = CASE
               WHEN EXISTS (
                   SELECT 1 FROM room_access_sessions AS session
                   WHERE session.room_id = membership.meeting_id
                     AND session.participant_id = membership.participant_id
                     AND session.invite_scope = 'room'
               ) THEN 'room'
               ELSE 'read_only'
           END"""
    )


def downgrade() -> None:
    # Historical write authority cannot be reconstructed after a fail-closed audit.
    pass
