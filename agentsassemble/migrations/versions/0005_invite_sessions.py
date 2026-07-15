"""Add hosted room invite and bearer session authority.

Revision ID: 0005_invite_sessions
Revises: 0004_room_global_settings
Create Date: 2026-07-15
"""
from __future__ import annotations

from alembic import op


revision = "0005_invite_sessions"
down_revision = "0004_room_global_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE room_invite_authority (
               authority_id TEXT PRIMARY KEY,
               signing_secret TEXT NOT NULL,
               created_at TIMESTAMPTZ NOT NULL
           )"""
    )
    op.execute(
        """CREATE TABLE room_invites (
               invite_id TEXT PRIMARY KEY,
               room_id TEXT NOT NULL REFERENCES rooms(room_id) ON DELETE CASCADE,
               agent_id TEXT NOT NULL DEFAULT '',
               display_name TEXT NOT NULL DEFAULT '',
               invite_scope TEXT NOT NULL DEFAULT 'room',
               participant_type TEXT NOT NULL DEFAULT 'human',
               client_type TEXT NOT NULL DEFAULT 'browser',
               provider_kind TEXT NOT NULL DEFAULT 'manual',
               created_by_user_id TEXT NOT NULL DEFAULT '',
               join_code_fingerprint TEXT NOT NULL UNIQUE,
               join_nonce TEXT NOT NULL DEFAULT '',
               permission_mode TEXT NOT NULL DEFAULT 'participant',
               max_uses INTEGER NOT NULL DEFAULT 1 CHECK (max_uses >= 0),
               use_count INTEGER NOT NULL DEFAULT 0 CHECK (use_count >= 0),
               expires_at TIMESTAMPTZ NOT NULL,
               created_at TIMESTAMPTZ NOT NULL,
               revoked BOOLEAN NOT NULL DEFAULT FALSE
           )"""
    )
    op.execute("CREATE INDEX idx_room_invites_room ON room_invites(room_id, expires_at)")
    op.execute(
        """CREATE TABLE room_invite_used_nonces (
               nonce_fingerprint TEXT PRIMARY KEY,
               consumed_at TIMESTAMPTZ NOT NULL
           )"""
    )
    op.execute(
        """CREATE TABLE room_access_sessions (
               token_fingerprint TEXT PRIMARY KEY,
               room_id TEXT NOT NULL REFERENCES rooms(room_id) ON DELETE CASCADE,
               participant_id TEXT NOT NULL,
               display_name TEXT NOT NULL DEFAULT '',
               invite_scope TEXT NOT NULL DEFAULT 'room',
               participant_type TEXT NOT NULL DEFAULT 'human',
               client_type TEXT NOT NULL DEFAULT 'browser',
               provider_kind TEXT NOT NULL DEFAULT 'manual',
               owner_id TEXT NOT NULL DEFAULT '',
               connection_kind TEXT NOT NULL DEFAULT '',
               joined_at TIMESTAMPTZ NOT NULL,
               expires_at TIMESTAMPTZ NOT NULL
           )"""
    )
    op.execute(
        """CREATE INDEX idx_room_access_sessions_participant
           ON room_access_sessions(room_id, participant_id)"""
    )
    op.execute(
        """CREATE INDEX idx_room_access_sessions_expiry
           ON room_access_sessions(expires_at)"""
    )


def downgrade() -> None:
    op.execute("DROP TABLE room_access_sessions")
    op.execute("DROP TABLE room_invite_used_nonces")
    op.execute("DROP TABLE room_invites")
    op.execute("DROP TABLE room_invite_authority")
