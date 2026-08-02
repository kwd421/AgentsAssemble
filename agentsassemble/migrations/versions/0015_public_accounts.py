"""Add public accounts and explicit server-local identity links.

Revision ID: 0015_public_accounts
Revises: 0014_guest_recovery_codes
Create Date: 2026-08-02
"""
from __future__ import annotations

from alembic import op


revision = "0015_public_accounts"
down_revision = "0014_guest_recovery_codes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE identity_accounts (
               account_id TEXT PRIMARY KEY,
               display_name TEXT NOT NULL DEFAULT '',
               email TEXT NOT NULL DEFAULT '',
               avatar_image_url TEXT NOT NULL DEFAULT '',
               created_at TEXT NOT NULL,
               updated_at TEXT NOT NULL
           )"""
    )
    op.execute(
        """CREATE TABLE identity_external_accounts (
               provider TEXT NOT NULL,
               subject_fingerprint TEXT NOT NULL,
               account_id TEXT NOT NULL UNIQUE
                   REFERENCES identity_accounts(account_id) ON DELETE CASCADE,
               connected_at TEXT NOT NULL,
               PRIMARY KEY(provider, subject_fingerprint)
           )"""
    )
    op.execute(
        """CREATE TABLE identity_user_accounts (
               user_id TEXT PRIMARY KEY
                   REFERENCES identity_users(user_id) ON DELETE CASCADE,
               account_id TEXT NOT NULL UNIQUE
                   REFERENCES identity_accounts(account_id) ON DELETE CASCADE,
               linked_at TEXT NOT NULL
           )"""
    )


def downgrade() -> None:
    op.execute("DROP TABLE identity_user_accounts")
    op.execute("DROP TABLE identity_external_accounts")
    op.execute("DROP TABLE identity_accounts")
