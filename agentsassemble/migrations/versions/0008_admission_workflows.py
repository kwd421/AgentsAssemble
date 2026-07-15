"""Persist resumable room admission workflow records.

Revision ID: 0008_admission_workflows
Revises: 0007_unique_room_access_session
Create Date: 2026-07-15
"""
from __future__ import annotations

from alembic import op


revision = "0008_admission_workflows"
down_revision = "0007_unique_room_access_session"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE room_admission_workflows (
               workflow_id TEXT PRIMARY KEY,
               room_id TEXT NOT NULL DEFAULT '',
               status TEXT NOT NULL,
               record_json JSONB NOT NULL,
               created_at TIMESTAMPTZ NOT NULL,
               updated_at TIMESTAMPTZ NOT NULL
           )"""
    )
    op.execute(
        """CREATE INDEX idx_room_admission_workflows_room_status
           ON room_admission_workflows(room_id, status, updated_at)"""
    )


def downgrade() -> None:
    op.execute("DROP TABLE room_admission_workflows")
