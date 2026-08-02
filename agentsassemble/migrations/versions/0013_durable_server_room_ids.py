"""Add immutable server and room identities.

Revision ID: 0013_durable_server_room_ids
Revises: 0012_user_profiles
Create Date: 2026-08-02
"""
from __future__ import annotations

from uuid import uuid4

import sqlalchemy as sa
from alembic import op


revision = "0013_durable_server_room_ids"
down_revision = "0012_user_profiles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE identity_server_metadata (
               key TEXT PRIMARY KEY,
               value TEXT NOT NULL,
               created_at TEXT NOT NULL
           )"""
    )
    op.execute("ALTER TABLE rooms ADD COLUMN room_uid TEXT")
    op.execute("ALTER TABLE identity_room_registry ADD COLUMN room_uid TEXT")

    connection = op.get_bind()
    canonical_rows = connection.execute(
        sa.text("SELECT room_id, data_json FROM rooms ORDER BY room_id")
    ).mappings()
    canonical_uids: dict[str, str] = {}
    for row in canonical_rows:
        room_id = str(row["room_id"])
        room_uid = str(uuid4())
        canonical_uids[room_id] = room_uid
        connection.execute(
            sa.text(
                """UPDATE rooms
                   SET room_uid = :room_uid,
                       data_json = jsonb_set(data_json, '{room_uid}', to_jsonb(CAST(:room_uid AS text)), true)
                   WHERE room_id = :room_id"""
            ),
            {"room_uid": room_uid, "room_id": room_id},
        )
    registry_rows = connection.execute(
        sa.text("SELECT room_id FROM identity_room_registry ORDER BY room_id")
    ).mappings()
    for row in registry_rows:
        room_id = str(row["room_id"])
        connection.execute(
            sa.text(
                "UPDATE identity_room_registry SET room_uid = :room_uid WHERE room_id = :room_id"
            ),
            {"room_uid": canonical_uids.get(room_id, str(uuid4())), "room_id": room_id},
        )

    op.execute("ALTER TABLE rooms ALTER COLUMN room_uid SET NOT NULL")
    op.execute("ALTER TABLE identity_room_registry ALTER COLUMN room_uid SET NOT NULL")
    op.execute("CREATE UNIQUE INDEX idx_rooms_uid ON rooms(room_uid)")
    op.execute(
        "CREATE UNIQUE INDEX idx_identity_rooms_uid ON identity_room_registry(room_uid)"
    )
    connection.execute(
        sa.text(
            """INSERT INTO identity_server_metadata(key, value, created_at)
               VALUES('server_id', :server_id, CURRENT_TIMESTAMP::text)"""
        ),
        {"server_id": str(uuid4())},
    )


def downgrade() -> None:
    op.execute("DROP INDEX idx_identity_rooms_uid")
    op.execute("DROP INDEX idx_rooms_uid")
    op.execute("ALTER TABLE identity_room_registry DROP COLUMN room_uid")
    op.execute("ALTER TABLE rooms DROP COLUMN room_uid")
    op.execute("DROP TABLE identity_server_metadata")
