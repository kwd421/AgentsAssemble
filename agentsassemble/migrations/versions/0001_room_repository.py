"""Create canonical room repository schema.

Revision ID: 0001_room_repository
Revises:
Create Date: 2026-07-14
"""
from __future__ import annotations

from alembic import op


revision = "0001_room_repository"
down_revision = None
branch_labels = None
depends_on = None


_SCHEMA_STATEMENTS = (
    """CREATE TABLE rooms (
           room_id TEXT PRIMARY KEY,
           label TEXT NOT NULL,
           status TEXT NOT NULL,
           archived BOOLEAN NOT NULL DEFAULT FALSE,
           updated_at TIMESTAMPTZ NOT NULL,
           data_json JSONB NOT NULL
       )""",
    """CREATE TABLE participants (
           room_id TEXT NOT NULL REFERENCES rooms(room_id) ON DELETE CASCADE,
           participant_id TEXT NOT NULL,
           status TEXT NOT NULL,
           role TEXT NOT NULL DEFAULT '',
           data_json JSONB NOT NULL,
           PRIMARY KEY (room_id, participant_id)
       )""",
    """CREATE TABLE agent_sessions (
           room_id TEXT NOT NULL REFERENCES rooms(room_id) ON DELETE CASCADE,
           session_id TEXT NOT NULL,
           participant_id TEXT NOT NULL DEFAULT '',
           status TEXT NOT NULL,
           runtime_status TEXT NOT NULL DEFAULT '',
           data_json JSONB NOT NULL,
           PRIMARY KEY (room_id, session_id)
       )""",
    """CREATE TABLE room_events (
           room_id TEXT NOT NULL REFERENCES rooms(room_id) ON DELETE CASCADE,
           seq BIGINT NOT NULL,
           event_id TEXT NOT NULL,
           event_type TEXT NOT NULL,
           actor_id TEXT NOT NULL DEFAULT '',
           turn_id TEXT NOT NULL DEFAULT '',
           created_at TIMESTAMPTZ NOT NULL,
           visibility TEXT NOT NULL DEFAULT 'visible',
           payload_json JSONB NOT NULL,
           PRIMARY KEY (room_id, seq),
           UNIQUE (room_id, event_id)
       )""",
    """CREATE TABLE command_results (
           room_id TEXT NOT NULL REFERENCES rooms(room_id) ON DELETE CASCADE,
           principal_id TEXT NOT NULL DEFAULT '',
           request_id TEXT NOT NULL,
           action TEXT NOT NULL DEFAULT '',
           payload_hash TEXT NOT NULL DEFAULT '',
           created_at TIMESTAMPTZ NOT NULL,
           result_json JSONB NOT NULL,
           PRIMARY KEY (room_id, principal_id, request_id)
       )""",
    """CREATE TABLE deleted_rooms (
           room_id TEXT PRIMARY KEY,
           deleted_at TIMESTAMPTZ NOT NULL,
           reason TEXT NOT NULL DEFAULT ''
       )""",
    """CREATE TABLE agent_attention_state (
           room_id TEXT NOT NULL,
           participant_id TEXT NOT NULL,
           last_observed_seq BIGINT NOT NULL DEFAULT 0,
           last_attention_evaluated_seq BIGINT NOT NULL DEFAULT 0,
           last_provider_sync_seq BIGINT NOT NULL DEFAULT 0,
           last_spoke_seq BIGINT NOT NULL DEFAULT 0,
           updated_at TIMESTAMPTZ NOT NULL,
           PRIMARY KEY (room_id, participant_id),
           FOREIGN KEY (room_id, participant_id)
               REFERENCES participants(room_id, participant_id) ON DELETE CASCADE
       )""",
    """CREATE TABLE attention_jobs (
           room_id TEXT NOT NULL REFERENCES rooms(room_id) ON DELETE CASCADE,
           job_id TEXT NOT NULL,
           source_seq BIGINT NOT NULL,
           source_event_id TEXT NOT NULL,
           mode TEXT NOT NULL,
           outcome TEXT NOT NULL,
           selected_participant_id TEXT NOT NULL DEFAULT '',
           eligible_participant_ids_json JSONB NOT NULL DEFAULT '[]'::jsonb,
           reasons_json JSONB NOT NULL DEFAULT '[]'::jsonb,
           status TEXT NOT NULL,
           created_at TIMESTAMPTZ NOT NULL,
           updated_at TIMESTAMPTZ NOT NULL,
           PRIMARY KEY (room_id, job_id),
           UNIQUE (room_id, source_seq, mode)
       )""",
    """CREATE TABLE attention_leases (
           room_id TEXT NOT NULL,
           lease_id TEXT NOT NULL,
           job_id TEXT NOT NULL,
           participant_id TEXT NOT NULL,
           owner_id TEXT NOT NULL DEFAULT '',
           status TEXT NOT NULL,
           acquired_at TIMESTAMPTZ NOT NULL,
           expires_at TIMESTAMPTZ NOT NULL,
           released_at TIMESTAMPTZ,
           PRIMARY KEY (room_id, lease_id),
           FOREIGN KEY (room_id, job_id)
               REFERENCES attention_jobs(room_id, job_id) ON DELETE CASCADE
       )""",
    """CREATE TABLE scheduled_wakeups (
           room_id TEXT NOT NULL REFERENCES rooms(room_id) ON DELETE CASCADE,
           wakeup_id TEXT NOT NULL,
           participant_id TEXT NOT NULL,
           reason TEXT NOT NULL,
           wake_at TIMESTAMPTZ NOT NULL,
           status TEXT NOT NULL,
           payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
           created_at TIMESTAMPTZ NOT NULL,
           updated_at TIMESTAMPTZ NOT NULL,
           PRIMARY KEY (room_id, wakeup_id)
       )""",
    """CREATE TABLE conversation_obligations (
           room_id TEXT NOT NULL REFERENCES rooms(room_id) ON DELETE CASCADE,
           obligation_id TEXT NOT NULL,
           participant_id TEXT NOT NULL,
           source_event_id TEXT NOT NULL DEFAULT '',
           kind TEXT NOT NULL,
           status TEXT NOT NULL,
           due_at TIMESTAMPTZ,
           payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
           created_at TIMESTAMPTZ NOT NULL,
           updated_at TIMESTAMPTZ NOT NULL,
           PRIMARY KEY (room_id, obligation_id)
       )""",
    "CREATE INDEX idx_rooms_updated ON rooms(updated_at DESC)",
    "CREATE INDEX idx_participants_status ON participants(room_id, status)",
    "CREATE INDEX idx_sessions_participant ON agent_sessions(room_id, participant_id)",
    "CREATE INDEX idx_events_type_seq ON room_events(room_id, event_type, seq)",
    "CREATE INDEX idx_events_visibility_seq ON room_events(room_id, visibility, seq)",
    "CREATE INDEX idx_commands_created ON command_results(room_id, created_at DESC)",
    "CREATE INDEX idx_attention_jobs_status ON attention_jobs(room_id, status, source_seq)",
    "CREATE INDEX idx_attention_leases_expiry ON attention_leases(status, expires_at)",
    "CREATE UNIQUE INDEX idx_attention_active_lease ON attention_leases(room_id, job_id) WHERE status = 'active'",
    "CREATE INDEX idx_scheduled_wakeups_due ON scheduled_wakeups(status, wake_at)",
    """CREATE INDEX idx_conversation_obligations_open
       ON conversation_obligations(room_id, participant_id, status)""",
)


def upgrade() -> None:
    for statement in _SCHEMA_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    for table in (
        "conversation_obligations",
        "scheduled_wakeups",
        "attention_leases",
        "attention_jobs",
        "agent_attention_state",
        "command_results",
        "room_events",
        "agent_sessions",
        "participants",
        "rooms",
        "deleted_rooms",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
