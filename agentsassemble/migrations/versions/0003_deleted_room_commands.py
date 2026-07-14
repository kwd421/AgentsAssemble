"""Store room deletion command state in the tombstone.

Revision ID: 0003_deleted_room_commands
Revises: 0002_room_repository_authority
Create Date: 2026-07-14
"""
from __future__ import annotations

from alembic import op


revision = "0003_deleted_room_commands"
down_revision = "0002_room_repository_authority"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE deleted_rooms ADD COLUMN principal_id TEXT NOT NULL DEFAULT ''")
    op.execute("ALTER TABLE deleted_rooms ADD COLUMN request_id TEXT NOT NULL DEFAULT ''")
    op.execute("ALTER TABLE deleted_rooms ADD COLUMN action TEXT NOT NULL DEFAULT ''")
    op.execute("ALTER TABLE deleted_rooms ADD COLUMN payload_hash TEXT NOT NULL DEFAULT ''")
    op.execute(
        "ALTER TABLE deleted_rooms ADD COLUMN cleanup_status TEXT NOT NULL DEFAULT 'complete'"
    )
    op.execute("ALTER TABLE deleted_rooms ADD COLUMN room_name TEXT NOT NULL DEFAULT ''")
    op.execute(
        "ALTER TABLE deleted_rooms ADD COLUMN result_json JSONB NOT NULL DEFAULT '{}'::jsonb"
    )


def downgrade() -> None:
    for column in (
        "result_json",
        "room_name",
        "cleanup_status",
        "payload_hash",
        "action",
        "request_id",
        "principal_id",
    ):
        op.execute(f"ALTER TABLE deleted_rooms DROP COLUMN {column}")
