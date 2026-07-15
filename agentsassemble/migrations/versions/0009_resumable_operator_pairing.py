"""Persist resumable operator pairing redemption state.

Revision ID: 0009_resumable_operator_pairing
Revises: 0008_admission_workflows
Create Date: 2026-07-15
"""
from __future__ import annotations

from alembic import op


revision = "0009_resumable_operator_pairing"
down_revision = "0008_admission_workflows"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """ALTER TABLE identity_operator_pairings
           ADD COLUMN consumed_auth_key TEXT NOT NULL DEFAULT '',
           ADD COLUMN redemption_status TEXT NOT NULL DEFAULT 'ready',
           ADD COLUMN completed_at TEXT NOT NULL DEFAULT '',
           ADD COLUMN session_fingerprint TEXT NOT NULL DEFAULT '',
           ADD COLUMN failure_code TEXT NOT NULL DEFAULT ''"""
    )


def downgrade() -> None:
    op.execute(
        """ALTER TABLE identity_operator_pairings
           DROP COLUMN failure_code,
           DROP COLUMN session_fingerprint,
           DROP COLUMN completed_at,
           DROP COLUMN redemption_status,
           DROP COLUMN consumed_auth_key"""
    )
