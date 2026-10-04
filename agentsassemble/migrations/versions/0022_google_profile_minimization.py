"""Remove Google profile claims retained by older account links.

Revision ID: 0022_google_profile_minimization
Revises: 0021_membership_scope_audit
Create Date: 2026-08-20
"""
from __future__ import annotations

from alembic import op


revision = "0022_google_profile_minimization"
down_revision = "0021_membership_scope_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """UPDATE identity_accounts AS account
           SET display_name = '', email = '', avatar_image_url = ''
           FROM identity_external_accounts AS identity
           WHERE identity.account_id = account.account_id
             AND identity.provider = 'google'"""
    )


def downgrade() -> None:
    # Removed personal data cannot and should not be reconstructed.
    pass
