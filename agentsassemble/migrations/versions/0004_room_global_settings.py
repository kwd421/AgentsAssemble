"""Add canonical room-global settings.

Revision ID: 0004_room_global_settings
Revises: 0003_deleted_room_commands
Create Date: 2026-07-14
"""
from __future__ import annotations

from alembic import op


revision = "0004_room_global_settings"
down_revision = "0003_deleted_room_commands"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE room_settings (
               room_id TEXT PRIMARY KEY REFERENCES rooms(room_id) ON DELETE CASCADE,
               updated_at TIMESTAMPTZ NOT NULL,
               data_json JSONB NOT NULL
           )"""
    )
    op.execute(
        """INSERT INTO room_settings(room_id, updated_at, data_json)
           SELECT room_id, updated_at,
                  jsonb_build_object(
                      'label', label,
                      'topic', '',
                      'appearance', jsonb_build_object(
                          'banner_preset', 'default',
                          'banner_image_url', '',
                          'icon_image_url', '',
                          'icon_label', '',
                          'invite_scope', 'room'
                      ),
                      'conversation_mode', 'ordered',
                      'max_relay_turns', 6,
                      'channels', '[]'::jsonb
                  )
           FROM rooms"""
    )


def downgrade() -> None:
    op.execute("DROP TABLE room_settings")
