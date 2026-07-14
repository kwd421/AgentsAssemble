from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from agentsassemble.meeting_events import clean_lobby_text
from agentsassemble.room_attention import (
    ATTENTION_JOB_STATUSES,
    ATTENTION_MODES,
    AgentAttentionState,
    AttentionEvaluation,
    AttentionEvaluationConflict,
    AttentionLeaseConflict,
    attention_lease_is_expired,
)


def read_attention_state(
    connection: sqlite3.Connection,
    room_id: str,
    participant_id: str,
) -> AgentAttentionState:
    row = connection.execute(
        """SELECT last_observed_seq, last_attention_evaluated_seq,
                  last_provider_sync_seq, last_spoke_seq
           FROM agent_attention_state
           WHERE room_id = ? AND participant_id = ?""",
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
    connection: sqlite3.Connection,
    state: AgentAttentionState,
) -> AgentAttentionState:
    connection.execute(
        """INSERT INTO agent_attention_state(
               room_id, participant_id, last_observed_seq,
               last_attention_evaluated_seq, last_provider_sync_seq,
               last_spoke_seq, updated_at
           ) VALUES(?, ?, ?, ?, ?, ?, ?)
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
            _now(),
        ),
    )
    return state


def record_attention_evaluation(
    connection: sqlite3.Connection,
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
           WHERE room_id = ? AND source_seq = ? AND mode = ?""",
        (evaluation.room_id, evaluation.source_seq, clean_mode),
    ).fetchone()
    if existing_row is not None:
        existing = attention_job_from_row(existing_row)
        if _evaluation_signature(existing) != _evaluation_signature(evaluation):
            raise AttentionEvaluationConflict(
                "attention_evaluation_conflict: source sequence already has a different decision."
            )
        return existing

    now = _now()
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
           ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            job["room_id"],
            job["job_id"],
            job["source_seq"],
            job["source_event_id"],
            job["mode"],
            job["outcome"],
            job["selected_participant_id"],
            _json_dumps(job["eligible_participant_ids"]),
            _json_dumps(job["reasons"]),
            job["status"],
            job["created_at"],
            job["updated_at"],
        ),
    )
    return job


def read_attention_jobs(
    connection: sqlite3.Connection,
    room_id: str,
    *,
    mode: str = "",
    status: str = "",
    after_seq: int = 0,
    limit: int = 200,
) -> list[dict[str, object]]:
    clauses = ["room_id = ?"]
    parameters: list[object] = [room_id]
    clean_mode = clean_lobby_text(mode, limit=32)
    clean_status = clean_lobby_text(status, limit=32)
    if clean_mode:
        if clean_mode not in ATTENTION_MODES:
            raise ValueError(f"Unsupported attention mode: {clean_mode}")
        clauses.append("mode = ?")
        parameters.append(clean_mode)
    if clean_status:
        if clean_status not in ATTENTION_JOB_STATUSES:
            raise ValueError(f"Unsupported attention job status: {clean_status}")
        clauses.append("status = ?")
        parameters.append(clean_status)
    if after_seq:
        clauses.append("source_seq > ?")
        parameters.append(max(0, int(after_seq)))
    parameters.append(min(1000, max(1, int(limit or 200))))
    rows = connection.execute(
        f"""SELECT * FROM attention_jobs
            WHERE {' AND '.join(clauses)}
            ORDER BY source_seq ASC LIMIT ?""",
        tuple(parameters),
    ).fetchall()
    return [attention_job_from_row(row) for row in rows]


def claim_attention_job(
    connection: sqlite3.Connection,
    room_id: str,
    job_id: str,
    *,
    participant_id: str,
    owner_id: str,
    lease_seconds: float,
) -> dict[str, object]:
    clean_job_id = clean_lobby_text(job_id, limit=128)
    clean_participant_id = clean_lobby_text(participant_id, limit=128)
    clean_owner_id = clean_lobby_text(owner_id, limit=128)
    if not clean_job_id or not clean_participant_id or not clean_owner_id:
        raise AttentionLeaseConflict("attention_lease_identity_required")
    job_row = connection.execute(
        "SELECT * FROM attention_jobs WHERE room_id = ? AND job_id = ?",
        (room_id, clean_job_id),
    ).fetchone()
    if job_row is None:
        raise AttentionLeaseConflict("attention_job_not_found")
    job = attention_job_from_row(job_row)
    if job["outcome"] != "selected" or job["selected_participant_id"] != clean_participant_id:
        raise AttentionLeaseConflict("attention_job_participant_mismatch")

    acquired = datetime.now(UTC)
    existing_row = connection.execute(
        """SELECT * FROM attention_leases
           WHERE room_id = ? AND job_id = ? AND status = 'active'""",
        (room_id, clean_job_id),
    ).fetchone()
    if existing_row is not None:
        existing = attention_lease_from_row(existing_row)
        if attention_lease_is_expired(existing["expires_at"], at=acquired):
            released_at = acquired.isoformat()
            connection.execute(
                """UPDATE attention_leases
                   SET status = 'expired', released_at = ?
                   WHERE room_id = ? AND lease_id = ?""",
                (released_at, room_id, existing["lease_id"]),
            )
            connection.execute(
                """UPDATE attention_jobs
                   SET status = 'pending', updated_at = ?
                   WHERE room_id = ? AND job_id = ?""",
                (released_at, room_id, clean_job_id),
            )
            job = {**job, "status": "pending"}
        elif (
            existing["participant_id"] == clean_participant_id
            and existing["owner_id"] == clean_owner_id
        ):
            return existing
        else:
            raise AttentionLeaseConflict("attention_job_already_leased")
    if job["status"] != "pending":
        raise AttentionLeaseConflict(f"attention_job_not_pending:{job['status']}")

    expires = acquired + timedelta(seconds=max(1.0, float(lease_seconds)))
    lease = {
        "room_id": room_id,
        "lease_id": f"lease-{uuid4().hex[:12]}",
        "job_id": clean_job_id,
        "participant_id": clean_participant_id,
        "owner_id": clean_owner_id,
        "status": "active",
        "acquired_at": acquired.isoformat(),
        "expires_at": expires.isoformat(),
        "released_at": "",
    }
    connection.execute(
        """INSERT INTO attention_leases(
               room_id, lease_id, job_id, participant_id, owner_id, status,
               acquired_at, expires_at, released_at
           ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        tuple(lease[column] for column in (
            "room_id",
            "lease_id",
            "job_id",
            "participant_id",
            "owner_id",
            "status",
            "acquired_at",
            "expires_at",
            "released_at",
        )),
    )
    connection.execute(
        "UPDATE attention_jobs SET status = 'leased', updated_at = ? WHERE room_id = ? AND job_id = ?",
        (_now(), room_id, clean_job_id),
    )
    return lease


def resolve_attention_lease(
    connection: sqlite3.Connection,
    room_id: str,
    lease_id: str,
    *,
    status: str,
) -> dict[str, object]:
    clean_lease_id = clean_lobby_text(lease_id, limit=128)
    clean_status = clean_lobby_text(status, limit=32)
    if clean_status not in {"released", "expired", "cancelled"}:
        raise AttentionLeaseConflict("attention_lease_terminal_status_required")
    row = connection.execute(
        "SELECT * FROM attention_leases WHERE room_id = ? AND lease_id = ?",
        (room_id, clean_lease_id),
    ).fetchone()
    if row is None:
        raise AttentionLeaseConflict("attention_lease_not_found")
    lease = attention_lease_from_row(row)
    if lease["status"] == clean_status:
        return lease
    if lease["status"] != "active":
        raise AttentionLeaseConflict(f"attention_lease_not_active:{lease['status']}")
    released_at = _now()
    connection.execute(
        """UPDATE attention_leases
           SET status = ?, released_at = ?
           WHERE room_id = ? AND lease_id = ?""",
        (clean_status, released_at, room_id, clean_lease_id),
    )
    job_status = "completed" if clean_status == "released" else (
        "pending" if clean_status == "expired" else "cancelled"
    )
    connection.execute(
        "UPDATE attention_jobs SET status = ?, updated_at = ? WHERE room_id = ? AND job_id = ?",
        (job_status, released_at, room_id, lease["job_id"]),
    )
    return {**lease, "status": clean_status, "released_at": released_at}


def read_attention_lease(
    connection: sqlite3.Connection,
    room_id: str,
    lease_id: str,
) -> dict[str, object]:
    row = connection.execute(
        "SELECT * FROM attention_leases WHERE room_id = ? AND lease_id = ?",
        (room_id, lease_id),
    ).fetchone()
    return attention_lease_from_row(row) if row is not None else {}


def attention_job_from_row(row: sqlite3.Row) -> dict[str, object]:
    return {
        "room_id": str(row["room_id"]),
        "job_id": str(row["job_id"]),
        "source_seq": int(row["source_seq"]),
        "source_event_id": str(row["source_event_id"]),
        "mode": str(row["mode"]),
        "outcome": str(row["outcome"]),
        "selected_participant_id": str(row["selected_participant_id"] or ""),
        "eligible_participant_ids": _json_text_list(row["eligible_participant_ids_json"]),
        "reasons": _json_text_list(row["reasons_json"]),
        "status": str(row["status"]),
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
    }


def attention_lease_from_row(row: sqlite3.Row) -> dict[str, object]:
    return {
        "room_id": str(row["room_id"]),
        "lease_id": str(row["lease_id"]),
        "job_id": str(row["job_id"]),
        "participant_id": str(row["participant_id"]),
        "owner_id": str(row["owner_id"] or ""),
        "status": str(row["status"]),
        "acquired_at": str(row["acquired_at"]),
        "expires_at": str(row["expires_at"]),
        "released_at": str(row["released_at"] or ""),
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


def _json_text_list(value: object) -> list[str]:
    try:
        parsed = json.loads(str(value or "[]"))
    except (json.JSONDecodeError, TypeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if str(item)]


def _json_dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _now() -> str:
    return datetime.now(UTC).isoformat()
