"""Store one server profile per authenticated user.

Revision ID: 0012_user_profiles
Revises: 0011_ordered_previous_speaker
Create Date: 2026-08-02
"""
from __future__ import annotations

from alembic import op


revision = "0012_user_profiles"
down_revision = "0011_ordered_previous_speaker"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE identity_user_profiles (
               user_id TEXT PRIMARY KEY
                   REFERENCES identity_users(user_id) ON DELETE CASCADE,
               data_json JSONB NOT NULL,
               created_at TEXT NOT NULL,
               updated_at TEXT NOT NULL
           )"""
    )


def downgrade() -> None:
    op.execute("DROP TABLE identity_user_profiles")
