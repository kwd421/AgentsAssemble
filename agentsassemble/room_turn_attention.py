from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Protocol
from uuid import uuid4

from agentsassemble.meeting_events import clean_lobby_text
from agentsassemble.providers.launch_specs import NativeCliProviderSpec
from agentsassemble.room_attention import AttentionLeaseConflict, attention_lease_is_expired
from agentsassemble.room.repository import RoomRepository


ProviderLookup = Callable[[str, str], NativeCliProviderSpec]


class AttentionLeaseWriter(Protocol):
    def resolve_attention_lease(
        self,
        lease_id: str,
        *,
        status: str,
    ) -> dict[str, object]: ...


class RoomTurnAttention:
    """Bind durable attention authority to pending and active provider turns."""

    def __init__(
        self,
        repository: RoomRepository,
        *,
        provider_lookup: ProviderLookup,
        owner_id: str = "",
    ) -> None:
        self.repository = repository
        self._provider_lookup = provider_lookup
        self.owner_id = clean_lobby_text(owner_id, limit=128) or f"turn-{uuid4().hex}"

    @staticmethod
    def queue_fields(event_id: str, *, job_id: str, lease_id: str) -> dict[str, object]:
        clean_job_id = clean_lobby_text(job_id, limit=128)
        clean_lease_id = clean_lobby_text(lease_id, limit=128)
        if bool(clean_job_id) != bool(clean_lease_id):
            raise ValueError("An attention job and lease must be queued together.")
        if not clean_job_id:
            return {}
        clean_event_id = clean_lobby_text(event_id, limit=128)
        if not clean_event_id:
            raise ValueError("An attention source event is required.")
        return {
            "pending_attention_job_id": clean_job_id,
            "pending_attention_lease_id": clean_lease_id,
            "pending_attention_source_event_id": clean_event_id,
        }

    def deferred_fields(
        self,
        room_id: str,
        session: dict[str, object],
        deferred_event_ids: Iterable[str],
    ) -> dict[str, object]:
        source_id = self._pending_source_id(session)
        deferred = set(deferred_event_ids)
        keep = bool(source_id and source_id in deferred)
        if source_id and not keep:
            self._resolve_pending_lease(room_id, session, status="cancelled")
        return {
            "pending_attention_job_id": session.get("pending_attention_job_id") if keep else "",
            "pending_attention_lease_id": session.get("pending_attention_lease_id") if keep else "",
            "pending_attention_source_event_id": source_id if keep else "",
        }

    def assignment_fields(
        self,
        room_id: str,
        agent_id: str,
        session: dict[str, object],
        *,
        inflight_event_ids: Iterable[str],
        deferred_event_ids: Iterable[str],
    ) -> dict[str, object]:
        source_id = self._pending_source_id(session)
        inflight = set(inflight_event_ids)
        deferred = set(deferred_event_ids)
        is_inflight = bool(source_id and source_id in inflight)
        is_deferred = bool(source_id and source_id in deferred)
        if source_id and not is_inflight and not is_deferred:
            self._resolve_pending_lease(room_id, session, status="cancelled")
        active_lease_id = (
            self._active_or_claimed_lease(room_id, agent_id, session)
            if is_inflight
            else ""
        )
        return {
            "active_attention_job_id": session.get("pending_attention_job_id") if is_inflight else "",
            "active_attention_lease_id": active_lease_id,
            "active_attention_source_event_id": source_id if is_inflight else "",
            "pending_attention_job_id": session.get("pending_attention_job_id") if is_deferred else "",
            "pending_attention_lease_id": session.get("pending_attention_lease_id") if is_deferred else "",
            "pending_attention_source_event_id": source_id if is_deferred else "",
        }

    @staticmethod
    def delivery_failed_fields(session: dict[str, object]) -> dict[str, object]:
        return {
            "pending_relay_depth": max(
                int(session.get("active_relay_depth") or 0),
                int(session.get("pending_relay_depth") or 0),
            ),
            "pending_attention_job_id": session.get("active_attention_job_id") or "",
            "pending_attention_lease_id": session.get("active_attention_lease_id") or "",
            "pending_attention_source_event_id": session.get("active_attention_source_event_id") or "",
            "active_attention_job_id": "",
            "active_attention_lease_id": "",
            "active_attention_source_event_id": "",
        }

    def cancel_queued(
        self,
        room_id: str,
        agent_id: str,
        *,
        source_event_id: str,
        lease_id: str,
    ) -> None:
        clean_source_event_id = clean_lobby_text(source_event_id, limit=128)
        clean_lease_id = clean_lobby_text(lease_id, limit=128)
        session = self.repository.session(room_id, agent_id)
        lease = self.repository.attention_lease(room_id, clean_lease_id) if clean_lease_id else {}
        with self.repository.transaction(room_id) as transaction:
            if lease.get("status") == "active":
                transaction.resolve_attention_lease(clean_lease_id, status="cancelled")
            if not session:
                return
            pending = [
                event_id
                for event_id in _dedupe_ids(session.get("pending_event_ids"))
                if event_id != clean_source_event_id
            ]
            updates: dict[str, object] = {"pending_event_ids": pending}
            if session.get("pending_attention_lease_id") == clean_lease_id:
                updates.update(self.empty_pending_fields())
            if session.get("active_attention_lease_id") == clean_lease_id:
                updates.update(self.empty_active_fields())
            transaction.update_session_fields(str(session["session_id"]), **updates)

    def prepare_session_reset(
        self,
        room_id: str,
        session: dict[str, object],
        *,
        pending_event_ids: list[str],
        retry: bool,
    ) -> dict[str, object]:
        active_job_id = clean_lobby_text(session.get("active_attention_job_id"), limit=128)
        active_lease_id = clean_lobby_text(session.get("active_attention_lease_id"), limit=128)
        active_source_id = clean_lobby_text(session.get("active_attention_source_event_id"), limit=128)
        pending_job_id = clean_lobby_text(session.get("pending_attention_job_id"), limit=128)
        pending_lease_id = clean_lobby_text(session.get("pending_attention_lease_id"), limit=128)
        pending_source_id = self._pending_source_id(session)
        pending = _dedupe_ids(pending_event_ids)
        if retry:
            return {
                "pending_event_ids": pending,
                "pending_relay_depth": max(
                    int(session.get("active_relay_depth") or 0),
                    int(session.get("pending_relay_depth") or 0),
                ),
                "pending_attention_job_id": active_job_id or pending_job_id,
                "pending_attention_lease_id": active_lease_id or pending_lease_id,
                "pending_attention_source_event_id": active_source_id or pending_source_id,
                **self.empty_active_fields(),
            }

        for lease_id in dict.fromkeys((active_lease_id, pending_lease_id)):
            self._cancel_active_lease(room_id, lease_id)
        cancelled_sources = {source_id for source_id in (active_source_id, pending_source_id) if source_id}
        return {
            "pending_event_ids": [event_id for event_id in pending if event_id not in cancelled_sources],
            "pending_relay_depth": 0,
            **self.empty_pending_fields(),
            **self.empty_active_fields(),
        }

    def reconcile_session(
        self,
        room_id: str,
        session: dict[str, object],
        *,
        pending_event_ids: list[str],
    ) -> dict[str, object]:
        fields = self.prepare_session_reset(
            room_id,
            session,
            pending_event_ids=pending_event_ids,
            retry=True,
        )
        lease_id = clean_lobby_text(fields.get("pending_attention_lease_id"), limit=128)
        if not lease_id:
            return fields
        lease = self.repository.attention_lease(room_id, lease_id)
        if lease.get("status") == "active" and attention_lease_is_expired(
            lease.get("expires_at")
        ):
            with self.repository.transaction(room_id) as transaction:
                transaction.resolve_attention_lease(lease_id, status="expired")
            fields["pending_attention_lease_id"] = ""
            return fields
        if lease.get("status") == "expired":
            fields["pending_attention_lease_id"] = ""
            return fields
        source_id = clean_lobby_text(fields.get("pending_attention_source_event_id"), limit=128)
        fields["pending_event_ids"] = [
            event_id for event_id in list(fields["pending_event_ids"]) if event_id != source_id
        ]
        fields.update(self.empty_pending_fields())
        return fields

    @staticmethod
    def resolve_active(
        transaction: AttentionLeaseWriter,
        session: dict[str, object],
        *,
        status: str,
    ) -> None:
        lease_id = clean_lobby_text(session.get("active_attention_lease_id"), limit=128)
        if lease_id:
            transaction.resolve_attention_lease(lease_id, status=status)

    @staticmethod
    def empty_active_fields() -> dict[str, str]:
        return {
            "active_attention_job_id": "",
            "active_attention_lease_id": "",
            "active_attention_source_event_id": "",
        }

    @staticmethod
    def empty_pending_fields() -> dict[str, str]:
        return {
            "pending_attention_job_id": "",
            "pending_attention_lease_id": "",
            "pending_attention_source_event_id": "",
        }

    @classmethod
    def empty_fields(cls) -> dict[str, str]:
        return {**cls.empty_pending_fields(), **cls.empty_active_fields()}

    @staticmethod
    def _pending_source_id(session: dict[str, object]) -> str:
        return clean_lobby_text(session.get("pending_attention_source_event_id"), limit=128)

    def _resolve_pending_lease(
        self,
        room_id: str,
        session: dict[str, object],
        *,
        status: str,
    ) -> None:
        lease_id = clean_lobby_text(session.get("pending_attention_lease_id"), limit=128)
        if not lease_id:
            return
        with self.repository.transaction(room_id) as transaction:
            transaction.resolve_attention_lease(lease_id, status=status)

    def _cancel_active_lease(self, room_id: str, lease_id: str) -> None:
        if not lease_id:
            return
        lease = self.repository.attention_lease(room_id, lease_id)
        if lease.get("status") == "active":
            with self.repository.transaction(room_id) as transaction:
                transaction.resolve_attention_lease(lease_id, status="cancelled")

    def _active_or_claimed_lease(
        self,
        room_id: str,
        agent_id: str,
        session: dict[str, object],
    ) -> str:
        job_id = clean_lobby_text(session.get("pending_attention_job_id"), limit=128)
        if not job_id:
            raise AttentionLeaseConflict("attention_job_not_found")
        with self.repository.transaction(room_id) as transaction:
            lease = transaction.claim_attention_job(
                job_id,
                participant_id=agent_id,
                owner_id=self.owner_id,
                lease_seconds=max(60.0, self._provider_lookup(room_id, agent_id).turn_timeout_seconds + 30.0),
            )
        return clean_lobby_text(lease.get("lease_id"), limit=128)


def _dedupe_ids(values: object) -> list[str]:
    source = values if isinstance(values, (list, tuple)) else []
    return list(dict.fromkeys(
        value
        for value in (clean_lobby_text(item, limit=128) for item in source)
        if value
    ))
