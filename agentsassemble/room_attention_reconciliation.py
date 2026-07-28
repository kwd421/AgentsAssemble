from __future__ import annotations

from dataclasses import dataclass

from agentsassemble.legacy.meeting.core.events import clean_lobby_text
from agentsassemble.room_attention import attention_lease_is_expired
from agentsassemble.room.repository import RoomRepository, RoomTransaction


_ACTIVE_JOB_STATUSES = frozenset({"pending", "leased"})
_TERMINAL_PARTICIPANT_STATUSES = frozenset({"left", "kicked", "exported"})


@dataclass(frozen=True)
class AttentionReconciliationReport:
    rooms_checked: int
    records_checked: int
    repairs: tuple[dict[str, str], ...]
    truncated_room_ids: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "rooms_checked": self.rooms_checked,
            "records_checked": self.records_checked,
            "repair_count": len(self.repairs),
            "repairs": [dict(repair) for repair in self.repairs],
            "truncated": bool(self.truncated_room_ids),
            "truncated_room_ids": list(self.truncated_room_ids),
        }


@dataclass(frozen=True)
class _RoomAttentionSnapshot:
    sessions: tuple[dict[str, object], ...]
    jobs: tuple[dict[str, object], ...]
    leases: tuple[dict[str, object], ...]
    active_lease_ids_by_job: dict[str, str]
    session_references_complete: bool
    records_checked: int
    truncated: bool


class RoomAttentionReconciler:
    """Repair bounded, durable attention inconsistencies at server startup."""

    def __init__(self, repository: RoomRepository, *, max_records_per_room: int = 500) -> None:
        self.repository = repository
        self.max_records_per_room = min(1000, max(1, int(max_records_per_room)))

    def reconcile(self) -> AttentionReconciliationReport:
        repairs: list[dict[str, str]] = []
        truncated_room_ids: list[str] = []
        records_checked = 0
        rooms = self.repository.list_rooms(include_archived=True)
        for room in rooms:
            room_id = clean_lobby_text(room.get("room_id"), limit=128)
            if not room_id:
                continue
            room_repairs, room_checked, truncated = self._reconcile_room(room_id)
            repairs.extend(room_repairs)
            records_checked += room_checked
            if truncated:
                truncated_room_ids.append(room_id)
        return AttentionReconciliationReport(
            rooms_checked=len(rooms),
            records_checked=records_checked,
            repairs=tuple(repairs),
            truncated_room_ids=tuple(truncated_room_ids),
        )

    def _reconcile_room(
        self,
        room_id: str,
    ) -> tuple[list[dict[str, str]], int, bool]:
        snapshot = _load_room_snapshot(
            self.repository,
            room_id,
            limit=self.max_records_per_room,
        )
        repairs: list[dict[str, str]] = []
        with self.repository.transaction(room_id) as transaction:
            referenced_job_ids, referenced_lease_ids = _reconcile_session_references(
                transaction,
                room_id,
                snapshot,
                repairs,
            )
            _reconcile_active_leases(
                transaction,
                room_id,
                snapshot,
                referenced_job_ids=referenced_job_ids,
                referenced_lease_ids=referenced_lease_ids,
                repairs=repairs,
            )
            _reconcile_active_jobs(
                transaction,
                room_id,
                snapshot,
                referenced_job_ids=referenced_job_ids,
                repairs=repairs,
            )
            if repairs:
                transaction.append_event(
                    "attention_reconciled",
                    repair_count=len(repairs),
                    repair_codes=sorted({repair["code"] for repair in repairs}),
                )
        return repairs, snapshot.records_checked, snapshot.truncated


def _load_room_snapshot(
    repository: RoomRepository,
    room_id: str,
    *,
    limit: int,
) -> _RoomAttentionSnapshot:
    session_rows = repository.sessions(room_id)
    pending_jobs = repository.attention_jobs(
        room_id,
        mode="active",
        status="pending",
        limit=limit + 1,
    )
    leased_jobs = repository.attention_jobs(
        room_id,
        mode="active",
        status="leased",
        limit=limit + 1,
    )
    active_leases = repository.attention_leases(
        room_id,
        status="active",
        limit=limit + 1,
    )
    truncated = any(
        len(records) > limit
        for records in (session_rows, pending_jobs, leased_jobs, active_leases)
    )
    sessions = tuple(session_rows[:limit])
    jobs = tuple(_dedupe_records([*pending_jobs[:limit], *leased_jobs[:limit]], key="job_id"))
    leases = tuple(active_leases[:limit])
    active_lease_ids_by_job = {
        clean_lobby_text(lease.get("job_id"), limit=128): clean_lobby_text(
            lease.get("lease_id"),
            limit=128,
        )
        for lease in leases
        if clean_lobby_text(lease.get("job_id"), limit=128)
    }
    return _RoomAttentionSnapshot(
        sessions=sessions,
        jobs=jobs,
        leases=leases,
        active_lease_ids_by_job=active_lease_ids_by_job,
        session_references_complete=len(session_rows) <= limit,
        records_checked=len(sessions) + len(jobs) + len(leases),
        truncated=truncated,
    )


def _reconcile_session_references(
    transaction: RoomTransaction,
    room_id: str,
    snapshot: _RoomAttentionSnapshot,
    repairs: list[dict[str, str]],
) -> tuple[set[str], set[str]]:
    referenced_job_ids: set[str] = set()
    referenced_lease_ids: set[str] = set()
    for session_snapshot in snapshot.sessions:
        session_id = clean_lobby_text(session_snapshot.get("session_id"), limit=128)
        current = transaction.session(session_id)
        if not current:
            continue
        for phase in ("pending", "active"):
            current, job_id, lease_id = _reconcile_session_phase(
                transaction,
                room_id,
                current,
                phase=phase,
                active_lease_ids_by_job=snapshot.active_lease_ids_by_job,
                repairs=repairs,
            )
            if job_id:
                referenced_job_ids.add(job_id)
            if lease_id:
                referenced_lease_ids.add(lease_id)
    return referenced_job_ids, referenced_lease_ids


def _reconcile_session_phase(
    transaction: RoomTransaction,
    room_id: str,
    session: dict[str, object],
    *,
    phase: str,
    active_lease_ids_by_job: dict[str, str],
    repairs: list[dict[str, str]],
) -> tuple[dict[str, object], str, str]:
    session_id = clean_lobby_text(session.get("session_id"), limit=128)
    job_id = clean_lobby_text(session.get(f"{phase}_attention_job_id"), limit=128)
    if not job_id:
        return session, "", ""
    source_event_id = clean_lobby_text(
        session.get(f"{phase}_attention_source_event_id"),
        limit=128,
    )
    lease_id = clean_lobby_text(session.get(f"{phase}_attention_lease_id"), limit=128)
    job = transaction.attention_job(job_id)
    participant_id = clean_lobby_text(session.get("participant_id"), limit=128)
    participant = transaction.participant(participant_id)
    if not job:
        updated = _clear_session_attention(
            transaction,
            session,
            phase=phase,
            source_event_id=source_event_id,
        )
        repairs.append(_repair(room_id, "session_job_missing", session_id=session_id))
        return updated, "", ""
    if job.get("status") not in _ACTIVE_JOB_STATUSES:
        updated = _clear_session_attention(
            transaction,
            session,
            phase=phase,
            source_event_id=source_event_id,
        )
        repairs.append(
            _repair(room_id, "session_job_terminal", job_id=job_id, session_id=session_id)
        )
        return updated, "", ""
    if (
        job.get("selected_participant_id") != participant_id
        or not participant
        or participant.get("status") in _TERMINAL_PARTICIPANT_STATUSES
    ):
        _cancel_job_and_lease(
            transaction,
            job_id=job_id,
            lease_id=lease_id or active_lease_ids_by_job.get(job_id, ""),
        )
        updated = _clear_session_attention(
            transaction,
            session,
            phase=phase,
            source_event_id=source_event_id,
        )
        repairs.append(
            _repair(
                room_id,
                "selected_participant_unavailable",
                job_id=job_id,
                session_id=session_id,
            )
        )
        return updated, "", ""
    if not lease_id:
        return session, job_id, ""
    return _reconcile_session_lease(
        transaction,
        room_id,
        session,
        phase=phase,
        job=job,
        job_id=job_id,
        lease_id=lease_id,
        source_event_id=source_event_id,
        repairs=repairs,
    )


def _reconcile_session_lease(
    transaction: RoomTransaction,
    room_id: str,
    session: dict[str, object],
    *,
    phase: str,
    job: dict[str, object],
    job_id: str,
    lease_id: str,
    source_event_id: str,
    repairs: list[dict[str, str]],
) -> tuple[dict[str, object], str, str]:
    session_id = clean_lobby_text(session.get("session_id"), limit=128)
    participant_id = clean_lobby_text(session.get("participant_id"), limit=128)
    lease = transaction.attention_lease(lease_id)
    if (
        not lease
        or lease.get("job_id") != job_id
        or lease.get("participant_id") != participant_id
    ):
        updated = _clear_session_attention(
            transaction,
            session,
            phase=phase,
            source_event_id=source_event_id,
        )
        repairs.append(
            _repair(room_id, "session_lease_invalid", job_id=job_id, session_id=session_id)
        )
        return updated, "", ""
    if lease.get("status") != "active":
        if job.get("status") == "pending":
            updated = transaction.update_session_fields(
                session_id,
                **{f"{phase}_attention_lease_id": ""},
            )
            repairs.append(
                _repair(
                    room_id,
                    "session_terminal_lease_cleared",
                    job_id=job_id,
                    lease_id=lease_id,
                    session_id=session_id,
                )
            )
            return updated, job_id, ""
        updated = _clear_session_attention(
            transaction,
            session,
            phase=phase,
            source_event_id=source_event_id,
        )
        repairs.append(
            _repair(
                room_id,
                "session_lease_terminal",
                job_id=job_id,
                lease_id=lease_id,
                session_id=session_id,
            )
        )
        return updated, "", ""
    if not attention_lease_is_expired(lease.get("expires_at")):
        return session, job_id, lease_id
    transaction.resolve_attention_lease(lease_id, status="expired")
    updated = transaction.update_session_fields(
        session_id,
        **{f"{phase}_attention_lease_id": ""},
    )
    repairs.append(
        _repair(
            room_id,
            "lease_expired",
            job_id=job_id,
            lease_id=lease_id,
            session_id=session_id,
        )
    )
    return updated, job_id, ""


def _reconcile_active_leases(
    transaction: RoomTransaction,
    room_id: str,
    snapshot: _RoomAttentionSnapshot,
    *,
    referenced_job_ids: set[str],
    referenced_lease_ids: set[str],
    repairs: list[dict[str, str]],
) -> None:
    for lease_snapshot in snapshot.leases:
        lease_id = clean_lobby_text(lease_snapshot.get("lease_id"), limit=128)
        lease = transaction.attention_lease(lease_id)
        if not lease or lease.get("status") != "active":
            continue
        job_id = clean_lobby_text(lease.get("job_id"), limit=128)
        participant = transaction.participant(
            clean_lobby_text(lease.get("participant_id"), limit=128)
        )
        participant_unavailable = (
            not participant
            or participant.get("status") in _TERMINAL_PARTICIPANT_STATUSES
        )
        orphaned = (
            participant_unavailable
            or (
                snapshot.session_references_complete
                and job_id not in referenced_job_ids
            )
        )
        if orphaned:
            job = transaction.attention_job(job_id)
            _cancel_job_and_lease(
                transaction,
                job_id=job_id,
                lease_id=lease_id,
            )
            repairs.append(
                _repair(room_id, "orphan_lease_cancelled", job_id=job_id, lease_id=lease_id)
            )
            if job.get("status") in _ACTIVE_JOB_STATUSES:
                repairs.append(_repair(room_id, "orphan_job_cancelled", job_id=job_id))
        elif (
            snapshot.session_references_complete
            and lease_id not in referenced_lease_ids
            and attention_lease_is_expired(lease.get("expires_at"))
        ):
            transaction.resolve_attention_lease(lease_id, status="expired")
            repairs.append(_repair(room_id, "lease_expired", job_id=job_id, lease_id=lease_id))


def _reconcile_active_jobs(
    transaction: RoomTransaction,
    room_id: str,
    snapshot: _RoomAttentionSnapshot,
    *,
    referenced_job_ids: set[str],
    repairs: list[dict[str, str]],
) -> None:
    for job_snapshot in snapshot.jobs:
        job_id = clean_lobby_text(job_snapshot.get("job_id"), limit=128)
        job = transaction.attention_job(job_id)
        if not job or job.get("status") not in _ACTIVE_JOB_STATUSES:
            continue
        participant = transaction.participant(
            clean_lobby_text(job.get("selected_participant_id"), limit=128)
        )
        participant_available = (
            bool(participant)
            and participant.get("status") not in _TERMINAL_PARTICIPANT_STATUSES
        )
        if participant_available and (
            job_id in referenced_job_ids
            or not snapshot.session_references_complete
        ):
            continue
        _cancel_job_and_lease(
            transaction,
            job_id=job_id,
            lease_id=snapshot.active_lease_ids_by_job.get(job_id, ""),
        )
        repairs.append(_repair(room_id, "orphan_job_cancelled", job_id=job_id))


def _clear_session_attention(
    transaction: RoomTransaction,
    session: dict[str, object],
    *,
    phase: str,
    source_event_id: str,
) -> dict[str, object]:
    session_id = clean_lobby_text(session.get("session_id"), limit=128)
    event_field = "pending_event_ids" if phase == "pending" else "inflight_event_ids"
    remaining = [
        event_id
        for event_id in list(session.get(event_field) or [])
        if event_id != source_event_id
    ]
    return transaction.update_session_fields(
        session_id,
        **{
            event_field: remaining,
            f"{phase}_attention_job_id": "",
            f"{phase}_attention_lease_id": "",
            f"{phase}_attention_source_event_id": "",
        },
    )


def _cancel_job_and_lease(
    transaction: RoomTransaction,
    *,
    job_id: str,
    lease_id: str,
) -> None:
    lease = transaction.attention_lease(lease_id) if lease_id else {}
    if lease.get("status") == "active":
        transaction.resolve_attention_lease(lease_id, status="cancelled")
    job = transaction.attention_job(job_id)
    if job.get("status") in _ACTIVE_JOB_STATUSES:
        transaction.cancel_attention_job(job_id)


def _dedupe_records(
    records: list[dict[str, object]],
    *,
    key: str,
) -> list[dict[str, object]]:
    deduped: dict[str, dict[str, object]] = {}
    for record in records:
        record_id = clean_lobby_text(record.get(key), limit=128)
        if record_id:
            deduped[record_id] = record
    return list(deduped.values())


def _repair(room_id: str, code: str, **identifiers: str) -> dict[str, str]:
    return {
        "room_id": room_id,
        "code": code,
        **{key: value for key, value in identifiers.items() if value},
    }
