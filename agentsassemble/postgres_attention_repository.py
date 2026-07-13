from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from psycopg import Connection
from psycopg.types.json import Jsonb

from agentsassemble.meeting_events import clean_lobby_text
from agentsassemble.room_attention import (
    ATTENTION_JOB_STATUSES,
    ATTENTION_MODES,
    AgentAttentionState,
    AttentionEvaluation,
    AttentionEvaluationConflict,
)
from agentsassemble.room_repository_records import utc_now


def read_attention_state(
    connection: Connection,
    room_id: str,
    participant_id: str,
) -> AgentAttentionState:
    row = connection.execute(
        """SELECT last_observed_seq, last_attention_evaluated_seq,
                  last_provider_sync_seq, last_spoke_seq
           FROM agent_attention_state
           WHERE room_id = %s AND participant_id = %s""",
        (room_id, participant_id),
    ).fetchone()
    if row is None:
        return AgentAttentionState(room_id=room_id, participant_id=participant_id)
    return AgentAttentionState(
        room_id=room_id,
        participant_id=participant_id,
        last_observed_seq=int(row["last_observed_seq"] or 0),
        last_attention_evaluated_seq=int(row["last_attention_evaluated_seq"] or 0),
        last_provider_sync_seq=int(row["last_provider_sync_seq"] or 0),
        last_spoke_seq=int(row["last_spoke_seq"] or 0),
    )


def write_attention_state(
    connection: Connection,
    state: AgentAttentionState,
) -> AgentAttentionState:
    connection.execute(
        """INSERT INTO agent_attention_state(
               room_id, participant_id, last_observed_seq,
               last_attention_evaluated_seq, last_provider_sync_seq,
               last_spoke_seq, updated_at
           ) VALUES(%s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT(room_id, participant_id) DO UPDATE SET
               last_observed_seq = excluded.last_observed_seq,
               last_attention_evaluated_seq = excluded.last_attention_evaluated_seq,
               last_provider_sync_seq = excluded.last_provider_sync_seq,
               last_spoke_seq = excluded.last_spoke_seq,
               updated_at = excluded.updated_at""",
        (
            state.room_id,
            state.participant_id,
            state.last_observed_seq,
            state.last_attention_evaluated_seq,
            state.last_provider_sync_seq,
            state.last_spoke_seq,
            utc_now(),
        ),
    )
    return state


def record_attention_evaluation(
    connection: Connection,
    evaluation: AttentionEvaluation,
    *,
    mode: str,
    status: str,
) -> dict[str, object]:
    clean_mode = clean_lobby_text(mode, limit=32)
    clean_status = clean_lobby_text(status, limit=32)
    if clean_mode not in ATTENTION_MODES:
        raise ValueError(f"Unsupported attention mode: {clean_mode}")
    if clean_status not in ATTENTION_JOB_STATUSES:
        raise ValueError(f"Unsupported attention job status: {clean_status}")
    existing_row = connection.execute(
        """SELECT * FROM attention_jobs
           WHERE room_id = %s AND source_seq = %s AND mode = %s""",
        (evaluation.room_id, evaluation.source_seq, clean_mode),
    ).fetchone()
    if existing_row is not None:
        existing = attention_job_from_row(existing_row)
        if _evaluation_signature(existing) != _evaluation_signature(evaluation):
            raise AttentionEvaluationConflict(
                "attention_evaluation_conflict: source sequence already has a different decision."
            )
        return existing

    now = utc_now()
    job = {
        "room_id": evaluation.room_id,
        "job_id": f"attention-{uuid4().hex[:12]}",
        "source_seq": evaluation.source_seq,
        "source_event_id": evaluation.source_event_id,
        "mode": clean_mode,
        "outcome": evaluation.outcome,
        "selected_participant_id": evaluation.selected_participant_id,
        "eligible_participant_ids": list(evaluation.eligible_participant_ids),
        "reasons": list(evaluation.reasons),
        "status": clean_status,
        "created_at": now,
        "updated_at": now,
    }
    connection.execute(
        """INSERT INTO attention_jobs(
               room_id, job_id, source_seq, source_event_id, mode, outcome,
               selected_participant_id, eligible_participant_ids_json,
               reasons_json, status, created_at, updated_at
           ) VALUES(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (
            job["room_id"],
            job["job_id"],
            job["source_seq"],
            job["source_event_id"],
            job["mode"],
            job["outcome"],
            job["selected_participant_id"],
            Jsonb(job["eligible_participant_ids"]),
            Jsonb(job["reasons"]),
            job["status"],
            job["created_at"],
            job["updated_at"],
        ),
    )
    return job


def read_attention_jobs(
    connection: Connection,
    room_id: str,
    *,
    mode: str = "",
    status: str = "",
    after_seq: int = 0,
    limit: int = 200,
) -> list[dict[str, object]]:
    clauses = ["room_id = %s"]
    parameters: list[object] = [room_id]
    clean_mode = clean_lobby_text(mode, limit=32)
    clean_status = clean_lobby_text(status, limit=32)
    if clean_mode:
        if clean_mode not in ATTENTION_MODES:
            raise ValueError(f"Unsupported attention mode: {clean_mode}")
        clauses.append("mode = %s")
        parameters.append(clean_mode)
    if clean_status:
        if clean_status not in ATTENTION_JOB_STATUSES:
            raise ValueError(f"Unsupported attention job status: {clean_status}")
        clauses.append("status = %s")
        parameters.append(clean_status)
    if after_seq:
        clauses.append("source_seq > %s")
        parameters.append(max(0, int(after_seq)))
    parameters.append(min(1000, max(1, int(limit or 200))))
    rows = connection.execute(
        f"""SELECT * FROM attention_jobs
            WHERE {' AND '.join(clauses)}
            ORDER BY source_seq ASC LIMIT %s""",
        tuple(parameters),
    ).fetchall()
    return [attention_job_from_row(row) for row in rows]


def attention_job_from_row(row: dict[str, object]) -> dict[str, object]:
    return {
        "room_id": str(row["room_id"]),
        "job_id": str(row["job_id"]),
        "source_seq": int(row["source_seq"]),
        "source_event_id": str(row["source_event_id"]),
        "mode": str(row["mode"]),
        "outcome": str(row["outcome"]),
        "selected_participant_id": str(row.get("selected_participant_id") or ""),
        "eligible_participant_ids": _text_list(row.get("eligible_participant_ids_json")),
        "reasons": _text_list(row.get("reasons_json")),
        "status": str(row["status"]),
        "created_at": _iso_text(row["created_at"]),
        "updated_at": _iso_text(row["updated_at"]),
    }


def _evaluation_signature(value: AttentionEvaluation | dict[str, object]) -> tuple[object, ...]:
    if isinstance(value, AttentionEvaluation):
        return (
            value.source_event_id,
            value.source_seq,
            value.outcome,
            value.selected_participant_id,
            tuple(value.eligible_participant_ids),
            tuple(value.reasons),
        )
    return (
        str(value.get("source_event_id") or ""),
        int(value.get("source_seq") or 0),
        str(value.get("outcome") or ""),
        str(value.get("selected_participant_id") or ""),
        tuple(value.get("eligible_participant_ids") or ()),
        tuple(value.get("reasons") or ()),
    )


def _text_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item)]


def _iso_text(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value or "")
