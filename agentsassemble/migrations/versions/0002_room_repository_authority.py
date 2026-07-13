"""Record explicit PostgreSQL room authority activation.

Revision ID: 0002_room_repository_authority
Revises: 0001_room_repository
Create Date: 2026-07-14
"""
from __future__ import annotations

from alembic import op


revision = "0002_room_repository_authority"
down_revision = "0001_room_repository"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE room_repository_authority (
               authority_id TEXT PRIMARY KEY,
               activated_at TIMESTAMPTZ NOT NULL,
               source_backend TEXT NOT NULL,
               source_checksum TEXT NOT NULL
           )"""
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS room_repository_authority")
