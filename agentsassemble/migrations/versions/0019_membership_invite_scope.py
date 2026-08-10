"""Persist the admission scope on durable room memberships.

Revision ID: 0019_membership_invite_scope
Revises: 0018_room_write_budget
Create Date: 2026-08-10
"""
from __future__ import annotations

from alembic import op


revision = "0019_membership_invite_scope"
down_revision = "0018_room_write_budget"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE identity_memberships "
        "ADD COLUMN invite_scope TEXT NOT NULL DEFAULT 'room'"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE identity_memberships DROP COLUMN invite_scope")
