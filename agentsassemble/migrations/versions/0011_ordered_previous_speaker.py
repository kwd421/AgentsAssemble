"""Add the ordered-room previous-speaker exclusion setting.

Revision ID: 0011_ordered_previous_speaker
Revises: 0010_vote_ballot_index
Create Date: 2026-07-28
"""
from __future__ import annotations

from alembic import op


revision = "0011_ordered_previous_speaker"
down_revision = "0010_vote_ballot_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """UPDATE room_settings
           SET data_json = jsonb_set(
               data_json,
               '{ordered_exclude_previous_speaker}',
               'true'::jsonb,
               true
           )
           WHERE NOT data_json ? 'ordered_exclude_previous_speaker'"""
    )


def downgrade() -> None:
    op.execute(
        """UPDATE room_settings
           SET data_json = data_json - 'ordered_exclude_previous_speaker'"""
    )
