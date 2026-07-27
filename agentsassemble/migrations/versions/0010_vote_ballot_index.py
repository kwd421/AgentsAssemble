"""Index canonical vote ballots by poll and room sequence.

Revision ID: 0010_vote_ballot_index
Revises: 0009_resumable_operator_pairing
Create Date: 2026-07-28
"""
from __future__ import annotations

from alembic import op


revision = "0010_vote_ballot_index"
down_revision = "0009_resumable_operator_pairing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            """CREATE INDEX CONCURRENTLY idx_events_vote_ballots
               ON room_events(
                   room_id,
                   visibility,
                   event_type,
                   (payload_json->>'vote_id'),
                   seq
               )
               WHERE payload_json->>'message_kind' = 'vote_cast'"""
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY idx_events_vote_ballots")
