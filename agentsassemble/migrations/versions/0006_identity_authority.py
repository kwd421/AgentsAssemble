"""Add hosted identity, credential, membership, and preference authority.

Revision ID: 0006_identity_authority
Revises: 0005_invite_sessions
Create Date: 2026-07-15
"""
from __future__ import annotations

from alembic import op


revision = "0006_identity_authority"
down_revision = "0005_invite_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE identity_users (
               user_id TEXT PRIMARY KEY,
               participant_id TEXT NOT NULL UNIQUE,
               display_name TEXT NOT NULL DEFAULT '',
               avatar_image_url TEXT NOT NULL DEFAULT '',
               participant_type TEXT NOT NULL DEFAULT 'human',
               auth_provider TEXT NOT NULL DEFAULT 'device',
               is_operator BOOLEAN NOT NULL DEFAULT FALSE,
               created_at TEXT NOT NULL DEFAULT '',
               last_seen_at TEXT NOT NULL DEFAULT ''
           )"""
    )
    op.execute(
        """CREATE TABLE identity_credentials (
               auth_key TEXT PRIMARY KEY,
               user_id TEXT NOT NULL REFERENCES identity_users(user_id) ON DELETE CASCADE,
               provider TEXT NOT NULL DEFAULT 'device',
               created_at TEXT NOT NULL DEFAULT '',
               last_used_at TEXT NOT NULL DEFAULT ''
           )"""
    )
    op.execute("CREATE INDEX idx_identity_credentials_user ON identity_credentials(user_id)")
    op.execute(
        """CREATE TABLE identity_operator_pairings (
               pairing_id TEXT PRIMARY KEY,
               token_fingerprint TEXT NOT NULL UNIQUE,
               user_id TEXT NOT NULL REFERENCES identity_users(user_id) ON DELETE CASCADE,
               room_id TEXT NOT NULL,
               target_origin TEXT NOT NULL,
               created_at TEXT NOT NULL,
               expires_at TEXT NOT NULL,
               used_at TEXT NOT NULL DEFAULT '',
               revoked_at TEXT NOT NULL DEFAULT ''
           )"""
    )
    op.execute(
        """CREATE INDEX idx_identity_operator_pairings_expiry
           ON identity_operator_pairings(expires_at)"""
    )
    op.execute(
        """CREATE TABLE identity_memberships (
               meeting_id TEXT NOT NULL,
               participant_id TEXT NOT NULL,
               display_name TEXT NOT NULL DEFAULT '',
               role TEXT NOT NULL DEFAULT 'agent',
               participant_type TEXT NOT NULL DEFAULT 'unknown',
               provider_kind TEXT NOT NULL DEFAULT '',
               connection_kind TEXT NOT NULL DEFAULT '',
               status TEXT NOT NULL DEFAULT '',
               muted BOOLEAN NOT NULL DEFAULT FALSE,
               is_host BOOLEAN NOT NULL DEFAULT FALSE,
               source TEXT NOT NULL DEFAULT 'manual',
               created_at TEXT NOT NULL DEFAULT '',
               updated_at TEXT NOT NULL DEFAULT '',
               last_seen_at TEXT NOT NULL DEFAULT '',
               PRIMARY KEY (meeting_id, participant_id)
           )"""
    )
    op.execute(
        """CREATE TABLE identity_room_registry (
               room_id TEXT PRIMARY KEY,
               owner_id TEXT NOT NULL DEFAULT '',
               label TEXT NOT NULL DEFAULT '',
               created_at TEXT NOT NULL,
               last_active_at TEXT NOT NULL,
               archived BOOLEAN NOT NULL DEFAULT FALSE,
               origin TEXT NOT NULL DEFAULT ''
           )"""
    )
    op.execute(
        """CREATE INDEX idx_identity_rooms_owner_active
           ON identity_room_registry(owner_id, last_active_at)"""
    )
    op.execute(
        """CREATE INDEX idx_identity_rooms_active
           ON identity_room_registry(last_active_at)"""
    )
    op.execute(
        """CREATE TABLE identity_room_user_preferences (
               user_id TEXT NOT NULL REFERENCES identity_users(user_id) ON DELETE CASCADE,
               room_id TEXT NOT NULL,
               data_json JSONB NOT NULL,
               created_at TEXT NOT NULL,
               updated_at TEXT NOT NULL,
               PRIMARY KEY (user_id, room_id)
           )"""
    )
    op.execute(
        """CREATE INDEX idx_identity_room_preferences_room
           ON identity_room_user_preferences(room_id)"""
    )
    op.execute(
        """CREATE TABLE identity_usage_events (
               id BIGSERIAL PRIMARY KEY,
               created_at TEXT NOT NULL DEFAULT '',
               user_id TEXT NOT NULL DEFAULT '',
               participant_id TEXT NOT NULL DEFAULT '',
               meeting_id TEXT NOT NULL DEFAULT '',
               provider TEXT NOT NULL DEFAULT '',
               model TEXT NOT NULL DEFAULT '',
               input_tokens BIGINT NOT NULL DEFAULT 0,
               output_tokens BIGINT NOT NULL DEFAULT 0,
               cost_owner TEXT NOT NULL DEFAULT '',
               estimated BOOLEAN NOT NULL DEFAULT FALSE
           )"""
    )
    op.execute(
        """CREATE INDEX idx_identity_usage_user
           ON identity_usage_events(user_id, created_at)"""
    )
    op.execute(
        """CREATE INDEX idx_identity_usage_meeting
           ON identity_usage_events(meeting_id, created_at)"""
    )


def downgrade() -> None:
    op.execute("DROP TABLE identity_usage_events")
    op.execute("DROP TABLE identity_room_user_preferences")
    op.execute("DROP TABLE identity_room_registry")
    op.execute("DROP TABLE identity_memberships")
    op.execute("DROP TABLE identity_operator_pairings")
    op.execute("DROP TABLE identity_credentials")
    op.execute("DROP TABLE identity_users")
