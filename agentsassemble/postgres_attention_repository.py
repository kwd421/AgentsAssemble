"""Compatibility exports for PostgreSQL room attention persistence.

Replacement: ``agentsassemble.persistence.postgres.room.attention``.
Removal gate: all direct imports use the replacement path for one compatibility
window.
"""
from agentsassemble.persistence.postgres.room.attention import (
    attention_job_from_row,
    attention_lease_from_row,
    cancel_attention_job,
    checkpoint_observed_seq,
    claim_attention_job,
    read_attention_job,
    read_attention_jobs,
    read_attention_lease,
    read_attention_leases,
    read_attention_state,
    record_attention_evaluation,
    resolve_attention_lease,
    write_attention_state,
)

__all__ = [
    "attention_job_from_row",
    "attention_lease_from_row",
    "cancel_attention_job",
    "checkpoint_observed_seq",
    "claim_attention_job",
    "read_attention_job",
    "read_attention_jobs",
    "read_attention_lease",
    "read_attention_leases",
    "read_attention_state",
    "record_attention_evaluation",
    "resolve_attention_lease",
    "write_attention_state",
]
