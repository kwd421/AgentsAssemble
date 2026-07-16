from __future__ import annotations

from dataclasses import dataclass

from agentsassemble.meeting_events import clean_lobby_text
from agentsassemble.room_attention import AgentAttentionState
from agentsassemble.room.repository import RoomRepository, RoomTransaction


class ProviderSyncCursorParityError(RuntimeError):
    """The canonical provider cursor and its compatibility copy diverged."""

    code = "provider_sync_cursor_mismatch"


@dataclass(frozen=True)
class ProviderSyncCursorReconciliationReport:
    rooms_checked: int
    sessions_checked: int
    repairs: tuple[dict[str, str], ...]
    failures: tuple[dict[str, str], ...]
    truncated_room_ids: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "rooms_checked": self.rooms_checked,
            "sessions_checked": self.sessions_checked,
            "repair_count": len(self.repairs),
            "failure_count": len(self.failures),
            "repairs": [dict(repair) for repair in self.repairs],
            "failures": [dict(failure) for failure in self.failures],
            "truncated": bool(self.truncated_room_ids),
            "truncated_room_ids": list(self.truncated_room_ids),
        }


def compatibility_provider_sync_seq(session: dict[str, object]) -> int:
    raw_sequence = session.get("last_provider_sync_seq")
    if raw_sequence in (None, ""):
        return 0
    if isinstance(raw_sequence, bool):
        raise ProviderSyncCursorParityError(
            "Agent Session provider-sync cursor must be a non-negative integer."
        )
    try:
        sequence = int(raw_sequence)
    except (TypeError, ValueError):
        raise ProviderSyncCursorParityError(
            "Agent Session provider-sync cursor must be a non-negative integer."
        ) from None
    if sequence < 0 or (isinstance(raw_sequence, float) and not raw_sequence.is_integer()):
        raise ProviderSyncCursorParityError(
            "Agent Session provider-sync cursor must be a non-negative integer."
        )
    return sequence


def assert_provider_sync_cursor_parity(
    session: dict[str, object],
    state: AgentAttentionState,
) -> int:
    compatibility_seq = compatibility_provider_sync_seq(session)
    if compatibility_seq != state.last_provider_sync_seq:
        raise ProviderSyncCursorParityError(
            "Canonical provider-sync cursor does not match the Agent Session compatibility cursor."
        )
    return state.last_provider_sync_seq


def canonical_provider_sync_seq(
    repository: RoomRepository,
    room_id: str,
    participant_id: str,
    session: dict[str, object],
) -> int:
    state = repository.attention_state(room_id, participant_id)
    sequence = assert_provider_sync_cursor_parity(session, state)
    event_id = clean_lobby_text(session.get("last_provider_sync_event_id"), limit=128)
    event_sequence = repository.event_sequence(room_id, event_id) if event_id else 0
    if event_sequence != sequence or (sequence == 0 and event_id):
        raise ProviderSyncCursorParityError(
            "Canonical provider-sync cursor does not match its Agent Session event cursor."
        )
    return sequence


def provider_sync_session_fields(
    state: AgentAttentionState,
    *,
    event_id: str,
) -> dict[str, object]:
    clean_event_id = clean_lobby_text(event_id, limit=128)
    if bool(state.last_provider_sync_seq) != bool(clean_event_id):
        raise ProviderSyncCursorParityError(
            "Provider-sync sequence and event cursor must advance together."
        )
    return {
        "last_provider_sync_event_id": clean_event_id,
        "last_provider_sync_seq": state.last_provider_sync_seq,
    }


class ProviderSyncCursorReconciler:
    """Establish provider cursor parity before canonical reads switch authority."""

    def __init__(self, repository: RoomRepository, *, max_sessions_per_room: int = 500) -> None:
        self.repository = repository
        self.max_sessions_per_room = min(1000, max(1, int(max_sessions_per_room)))

    def reconcile(self) -> ProviderSyncCursorReconciliationReport:
        rooms = self.repository.list_rooms(include_archived=True)
        repairs: list[dict[str, str]] = []
        failures: list[dict[str, str]] = []
        truncated_room_ids: list[str] = []
        sessions_checked = 0
        for room in rooms:
            room_id = clean_lobby_text(room.get("room_id"), limit=128)
            if not room_id:
                continue
            room_repairs, room_failures, checked, truncated = self._reconcile_room(room_id)
            repairs.extend(room_repairs)
            failures.extend(room_failures)
            sessions_checked += checked
            if truncated:
                truncated_room_ids.append(room_id)
        return ProviderSyncCursorReconciliationReport(
            rooms_checked=len(rooms),
            sessions_checked=sessions_checked,
            repairs=tuple(repairs),
            failures=tuple(failures),
            truncated_room_ids=tuple(truncated_room_ids),
        )

    def _reconcile_room(
        self,
        room_id: str,
    ) -> tuple[list[dict[str, str]], list[dict[str, str]], int, bool]:
        all_sessions = self.repository.sessions(room_id)
        truncated = len(all_sessions) > self.max_sessions_per_room
        sessions = all_sessions[: self.max_sessions_per_room]
        latest_seq = self.repository.latest_event_sequence(room_id)
        repairs: list[dict[str, str]] = []
        failures: list[dict[str, str]] = []
        valid_sessions: list[dict[str, object]] = []
        target_sequences: set[int] = set()
        for session in sessions:
            session_id = clean_lobby_text(session.get("session_id"), limit=128)
            participant_id = clean_lobby_text(session.get("participant_id"), limit=128)
            if not session_id or not participant_id:
                failures.append(
                    _record(
                        room_id,
                        "provider_sync_identity_missing",
                        session_id=session_id,
                        participant_id=participant_id,
                    )
                )
                continue
            valid_sessions.append(session)
            try:
                compatibility_sequence = compatibility_provider_sync_seq(session)
            except ProviderSyncCursorParityError:
                continue
            target_sequence = max(
                compatibility_sequence,
                self.repository.attention_state(room_id, participant_id).last_provider_sync_seq,
            )
            if target_sequence > 0:
                target_sequences.add(target_sequence)
        event_ids_by_seq = {
            sequence: self._event_id_for_seq(room_id, sequence)
            for sequence in target_sequences
        }
        with self.repository.transaction(room_id) as transaction:
            for session_snapshot in valid_sessions:
                repair, failure = self._reconcile_session(
                    transaction,
                    room_id,
                    session_snapshot,
                    latest_seq=latest_seq,
                    event_ids_by_seq=event_ids_by_seq,
                )
                if repair:
                    repairs.append(repair)
                if failure:
                    failures.append(failure)
            if repairs or failures:
                transaction.append_event(
                    "provider_sync_cursor_reconciled",
                    repair_count=len(repairs),
                    failure_count=len(failures),
                    repair_codes=sorted({repair["code"] for repair in repairs}),
                    failure_codes=sorted({failure["code"] for failure in failures}),
                )
        return repairs, failures, len(sessions), truncated

    def _reconcile_session(
        self,
        transaction: RoomTransaction,
        room_id: str,
        session_snapshot: dict[str, object],
        *,
        latest_seq: int,
        event_ids_by_seq: dict[int, str],
    ) -> tuple[dict[str, str], dict[str, str]]:
        session_id = clean_lobby_text(session_snapshot.get("session_id"), limit=128)
        if not session_id:
            return {}, _record(room_id, "provider_sync_identity_missing")
        session = transaction.session(session_id)
        if not session:
            return {}, {}
        participant_id = clean_lobby_text(session.get("participant_id"), limit=128)
        if not participant_id:
            return {}, _record(
                room_id,
                "provider_sync_identity_missing",
                session_id=session_id,
            )
        state = transaction.attention_state(participant_id)
        try:
            compatibility_seq = compatibility_provider_sync_seq(session)
        except ProviderSyncCursorParityError:
            transaction.update_session_fields(
                session_id,
                recovery_required=True,
                last_error="Agent Session provider sync cursor is malformed.",
            )
            return {}, _record(
                room_id,
                "provider_sync_cursor_malformed",
                session_id=session_id,
                participant_id=participant_id,
            )
        canonical_seq = state.last_provider_sync_seq
        target_seq = max(compatibility_seq, canonical_seq)
        expected_event_id = event_ids_by_seq.get(target_seq, "") if target_seq else ""
        current_event_id = clean_lobby_text(session.get("last_provider_sync_event_id"), limit=128)
        field_missing = "last_provider_sync_seq" not in session
        event_id_mismatch = current_event_id != expected_event_id
        if target_seq > latest_seq or (target_seq and not expected_event_id):
            updated = transaction.update_session_fields(
                session_id,
                recovery_required=True,
                last_error="Provider sync cursor is outside the canonical room event stream.",
            )
            del updated
            return {}, _record(
                room_id,
                "provider_sync_cursor_invalid",
                session_id=session_id,
                participant_id=participant_id,
            )
        if compatibility_seq == canonical_seq and not field_missing and not event_id_mismatch:
            return {}, {}

        divergence = compatibility_seq > 0 and canonical_seq > 0 and compatibility_seq != canonical_seq
        if canonical_seq < target_seq:
            state = transaction.advance_attention_state(
                participant_id,
                provider_sync_seq=target_seq,
            )
        code = _repair_code(
            compatibility_seq=compatibility_seq,
            canonical_seq=canonical_seq,
            field_missing=field_missing,
            event_id_mismatch=event_id_mismatch,
        )
        updates: dict[str, object] = provider_sync_session_fields(
            state,
            event_id=expected_event_id,
        )
        if divergence:
            updates.update(
                recovery_required=True,
                last_error="Provider sync cursor divergence was reconciled; recovery context is required.",
            )
        updated = transaction.update_session_fields(session_id, **updates)
        assert_provider_sync_cursor_parity(updated, state)
        return _record(
            room_id,
            code,
            session_id=session_id,
            participant_id=participant_id,
        ), {}

    def _event_id_for_seq(self, room_id: str, sequence: int) -> str:
        events = self.repository.read_events(
            room_id,
            after_seq=max(0, int(sequence) - 1),
            limit=1,
        )
        if not events or int(events[0].get("seq") or 0) != sequence:
            return ""
        return clean_lobby_text(events[0].get("id"), limit=128)


def _repair_code(
    *,
    compatibility_seq: int,
    canonical_seq: int,
    field_missing: bool,
    event_id_mismatch: bool,
) -> str:
    if compatibility_seq and not canonical_seq:
        return "canonical_cursor_initialized"
    if canonical_seq and not compatibility_seq:
        return "compatibility_cursor_restored"
    if compatibility_seq != canonical_seq:
        return "cursor_divergence_reconciled"
    if field_missing:
        return "compatibility_field_restored"
    if event_id_mismatch:
        return "compatibility_event_id_restored"
    return "cursor_parity_restored"


def _record(room_id: str, code: str, **identifiers: str) -> dict[str, str]:
    return {
        "room_id": room_id,
        "code": code,
        **{key: value for key, value in identifiers.items() if value},
    }
