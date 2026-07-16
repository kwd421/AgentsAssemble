"""Durable room-admission workflow across invite, identity, and room stores."""
from __future__ import annotations

import hashlib
import json
import secrets
import threading
from collections.abc import Callable
from datetime import UTC, datetime

from agentsassemble.admission.invite_service import (
    InviteApplicationService,
    PreparedInviteAdmission,
)
from agentsassemble.admission.saga import (
    RoomAdmissionCompensationFailed,
    RoomAdmissionSaga,
)
from agentsassemble.admission.session_service import RoomSessionService
from agentsassemble.application_transaction import ApplicationTransactionBoundary
from agentsassemble.identity.repository import (
    IdentityBackend,
    device_auth_key,
    normalize_participant_type,
)
from agentsassemble.multi_host_invites import NATIVE_REMOTE_ROOM_CLIENT_KIND
from agentsassemble.room_repository import RoomRepository
from agentsassemble.room.text import clean_room_text as clean_lobby_text


class AdmissionIdempotencyConflict(ValueError):
    """A caller reused an admission request id with different inputs."""


class RoomAdmissionCoordinator:
    """Resume-safe mutation owner for browser and Agent Bridge admission.

    The workflow record contains fingerprints and public/canonical metadata,
    never the invite, device credential, or room bearer token. Session tokens
    are deterministically reconstructed from the server signing secret and the
    workflow id, so a lost HTTP response can be retried without storing a raw
    bearer or consuming the invite twice.
    """

    def __init__(
        self,
        *,
        invites: InviteApplicationService,
        sessions: RoomSessionService,
        identities: IdentityBackend,
        rooms: RoomRepository,
        transaction_boundary: ApplicationTransactionBoundary | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._invites = invites
        self._sessions = sessions
        self._identities = identities
        self._rooms = rooms
        self._transaction_boundary = transaction_boundary
        self._now = now or (lambda: datetime.now(UTC))
        self._saga = RoomAdmissionSaga(
            invites=invites,
            sessions=sessions,
            identities=identities,
            now=self._now,
        )
        self._lock = threading.RLock()

    def admit(
        self,
        *,
        invite_token: str,
        request_id: str = "",
        meeting_id: str = "",
        display_name: str = "",
        device_token: str = "",
        participant_type: str = "",
        owner_display_name: str = "",
    ) -> dict[str, object]:
        clean_request_id = clean_lobby_text(request_id, limit=128) or f"legacy-{secrets.token_hex(12)}"
        token_fingerprint = hashlib.sha256(str(invite_token or "").encode("utf-8")).hexdigest()
        auth_key = device_auth_key(device_token)
        payload_hash = _payload_hash(
            {
                "meeting_id": clean_lobby_text(meeting_id, limit=128),
                "display_name": clean_lobby_text(display_name, limit=128),
                "participant_type": clean_lobby_text(participant_type, limit=32),
                "owner_display_name": clean_lobby_text(owner_display_name, limit=64),
            }
        )
        workflow_id = _workflow_id(
            token_fingerprint=token_fingerprint,
            device_auth_key=auth_key,
            request_id=clean_request_id,
        )

        with self._lock:
            workflow = self._invites.admission_workflow(workflow_id)
            if workflow is None:
                prepared = self._invites.prepare_admission(
                    invite_token,
                    meeting_id=meeting_id,
                )
                if isinstance(prepared, dict):
                    return prepared
                room, settings = self._room_context(prepared.meeting_id)
                if not room:
                    return {"status": "rejected", "reason": "room_unavailable"}
                moment = self._now().isoformat()
                workflow = self._invites.create_admission_workflow(
                    workflow_id,
                    {
                        "request_id": clean_request_id,
                        "token_fingerprint": token_fingerprint,
                        "device_auth_key": auth_key,
                        "payload_hash": payload_hash,
                        "status": "started",
                        "resume_phase": "started",
                        **_prepared_record(prepared),
                        **_room_record(room, settings, prepared.meeting_id),
                        "owner_display_name": clean_lobby_text(owner_display_name, limit=64),
                        "created_at": moment,
                        "updated_at": moment,
                    },
                )
            self._validate_retry(
                workflow,
                request_id=clean_request_id,
                token_fingerprint=token_fingerprint,
                device_auth_key=auth_key,
                payload_hash=payload_hash,
            )
            return self._resume(
                workflow,
                invite_token=invite_token,
                display_name=display_name,
                participant_type=participant_type,
            )

    def _resume(
        self,
        workflow: dict[str, object],
        *,
        invite_token: str,
        display_name: str,
        participant_type: str,
    ) -> dict[str, object]:
        workflow_id = str(workflow["workflow_id"])
        try:
            if self._transaction_boundary is None:
                return self._resume_steps(
                    workflow,
                    invite_token=invite_token,
                    display_name=display_name,
                    participant_type=participant_type,
                )
            with self._transaction_boundary.transaction():
                return self._resume_steps(
                    workflow,
                    invite_token=invite_token,
                    display_name=display_name,
                    participant_type=participant_type,
                )
        except AdmissionIdempotencyConflict:
            raise
        except RoomAdmissionCompensationFailed as failure:
            try:
                self._saga.record_failure(failure.workflow_id, failure.cause)
            except Exception as persistence_error:
                failure.cause.add_note(
                    "Admission compensation failure state could not be persisted: "
                    f"{type(persistence_error).__name__}."
                )
            raise failure.cause.with_traceback(failure.cause.__traceback__) from None
        except Exception as error:
            try:
                persisted = self._invites.admission_workflow(workflow_id) or workflow
                self._invites.update_admission_workflow(
                    workflow_id,
                    {
                        **self._phase_updates("failed_retryable"),
                        "resume_phase": clean_lobby_text(persisted.get("status"), limit=64)
                        or "started",
                        "failure_code": type(error).__name__,
                    },
                )
            except Exception as persistence_error:
                error.add_note(
                    "Admission workflow failure state could not be persisted: "
                    f"{type(persistence_error).__name__}."
                )
            raise

    def _resume_steps(
        self,
        workflow: dict[str, object],
        *,
        invite_token: str,
        display_name: str,
        participant_type: str,
    ) -> dict[str, object]:
        workflow_id = str(workflow["workflow_id"])
        if workflow.get("status") == "completed":
            return self._completed_result(workflow)
        if workflow.get("status") == "failed_terminal":
            return {
                "status": "rejected",
                "reason": str(workflow.get("failure_code") or "admission_rejected"),
            }

        prepared = _prepared_from_workflow(workflow, public_url=self._invites.public_url())
        if not prepared.meeting_id:
            refreshed = self._invites.prepare_admission(invite_token)
            if isinstance(refreshed, dict):
                return refreshed
            prepared = refreshed

        room, settings = self._room_context(prepared.meeting_id)
        if not room:
            workflow = self._saga.compensate_room_unavailable(workflow)
            return {
                "status": "rejected",
                "reason": str(workflow.get("failure_code") or "room_unavailable"),
            }

        if not workflow.get("participant_id"):
            workflow = self._resolve_identity(
                workflow,
                prepared,
                display_name=display_name,
                participant_type=participant_type,
            )

        if not workflow.get("invite_consumed"):
            consume_error, workflow = self._invites.consume_for_admission(
                workflow_id,
                prepared,
                updates=self._phase_updates("invite_consumed"),
            )
            if consume_error:
                self._invites.update_admission_workflow(
                    workflow_id,
                    {
                        **self._phase_updates("failed_terminal"),
                        "resume_phase": "invite_consumed",
                        "failure_code": consume_error,
                    },
                )
                return {"status": "rejected", "reason": consume_error}

        session_record = _session_record(workflow)
        session_token, session = self._sessions.ensure_for_request(
            workflow_id,
            session_record,
            joined_at=str(workflow.get("session_joined_at") or ""),
            expires_at=str(workflow.get("session_expires_at") or ""),
        )
        if workflow.get("status") not in {
            "session_issued",
            "membership_committed",
            "completed",
        }:
            workflow = self._invites.update_admission_workflow(
                workflow_id,
                {
                    **self._phase_updates("session_issued"),
                    "session_joined_at": str(session.get("joined_at") or ""),
                    "session_expires_at": str(session.get("expires_at") or ""),
                },
            )

        if workflow.get("status") not in {"membership_committed", "completed"}:
            self._commit_membership(workflow, prepared)
            workflow = self._invites.update_admission_workflow(
                workflow_id,
                self._phase_updates("membership_committed"),
            )

        workflow = self._invites.update_admission_workflow(
            workflow_id,
            {
                **self._phase_updates("completed"),
                **_room_record(room, settings, prepared.meeting_id),
                "failure_code": "",
            },
        )
        return self._result(workflow, prepared, session_token=session_token)

    def _resolve_identity(
        self,
        workflow: dict[str, object],
        prepared: PreparedInviteAdmission,
        *,
        display_name: str,
        participant_type: str,
    ) -> dict[str, object]:
        resolved_type = (
            normalize_participant_type(participant_type, default="")
            or prepared.participant_type
        )
        if prepared.client_type == "agent_bridge":
            resolved_type = "remote"
        auth_key = str(workflow.get("device_auth_key") or "")
        stable_user = None
        if prepared.reusable and auth_key:
            stable_user = self._identities.resolve_credential_user(
                auth_key,
                provider="device",
                display_name=display_name,
                participant_type=resolved_type,
            )
        if stable_user is not None:
            participant_id = clean_lobby_text(stable_user.get("participant_id"), limit=128)
        elif prepared.reusable:
            participant_id = f"{prepared.base_agent_id or 'guest'}-{str(workflow['workflow_id'])[:6]}"
        else:
            participant_id = prepared.base_agent_id
        resolved_name = (
            clean_lobby_text(display_name, limit=128)
            or clean_lobby_text((stable_user or {}).get("display_name"), limit=128)
            or prepared.display_name
            or prepared.base_agent_id
        )
        connection_kind = (
            "native_cli_bridge"
            if prepared.client_type == "agent_bridge"
            else NATIVE_REMOTE_ROOM_CLIENT_KIND
        )
        return self._invites.update_admission_workflow(
            str(workflow["workflow_id"]),
            {
                **self._phase_updates("identity_resolved"),
                "participant_id": participant_id,
                "display_name": resolved_name,
                "participant_type": resolved_type,
                "connection_kind": connection_kind,
                "stable_identity": stable_user is not None,
                "operator": bool(stable_user and stable_user.get("is_operator")),
            },
        )

    def _commit_membership(
        self,
        workflow: dict[str, object],
        prepared: PreparedInviteAdmission,
    ) -> None:
        participant_id = str(workflow.get("participant_id") or "")
        participant_type = str(workflow.get("participant_type") or "human")
        display_name = str(workflow.get("display_name") or participant_id)
        connection_kind = str(workflow.get("connection_kind") or NATIVE_REMOTE_ROOM_CLIENT_KIND)
        role = "human" if participant_type == "human" else "agent"
        self._rooms.upsert_participant(
            prepared.meeting_id,
            {
                "participant_id": participant_id,
                "display_name": display_name,
                "participant_type": participant_type,
                "role": role,
                "provider_kind": prepared.provider_kind,
                "connection_kind": connection_kind,
                "status": "joined",
                "owner_id": prepared.created_by_user_id,
            },
        )
        self._identities.upsert_membership(
            {
                "meeting_id": prepared.meeting_id,
                "participant_id": participant_id,
                "display_name": display_name,
                "role": role,
                "participant_type": participant_type,
                "provider_kind": prepared.provider_kind,
                "connection_kind": connection_kind,
                "status": "online",
                "is_host": False,
                "source": "room_invite",
            }
        )

    def _completed_result(self, workflow: dict[str, object]) -> dict[str, object]:
        session_token = self._sessions.token_for_request(str(workflow["workflow_id"]))
        if self._sessions.verify(session_token) is None:
            return {"status": "rejected", "reason": "admission_session_unavailable"}
        prepared = _prepared_from_workflow(workflow, public_url=self._invites.public_url())
        return self._result(workflow, prepared, session_token=session_token)

    def _result(
        self,
        workflow: dict[str, object],
        prepared: PreparedInviteAdmission,
        *,
        session_token: str,
    ) -> dict[str, object]:
        participant_id = str(workflow.get("participant_id") or "")
        display_name = str(workflow.get("display_name") or participant_id)
        return {
            "status": "admitted",
            "request_id": str(workflow.get("request_id") or ""),
            "session_token": session_token,
            "agent_id": participant_id,
            "display_name": display_name,
            "meeting_id": prepared.meeting_id,
            "invite_scope": prepared.invite_scope,
            "participant_type": str(workflow.get("participant_type") or "human"),
            "client_type": prepared.client_type,
            "provider_kind": prepared.provider_kind,
            "owner_display_name": str(workflow.get("owner_display_name") or ""),
            "owner_id": prepared.created_by_user_id,
            "stable_identity": bool(workflow.get("stable_identity")),
            "operator": bool(workflow.get("operator")),
            "connection_kind": str(workflow.get("connection_kind") or ""),
            "expires_at": str(workflow.get("session_expires_at") or ""),
            "room_label": str(workflow.get("room_label") or prepared.meeting_id),
            "room_topic": str(workflow.get("room_topic") or ""),
            "room_created_at": str(workflow.get("room_created_at") or ""),
            "guide": self._invites.usage_guide(
                prepared,
                participant_id=participant_id,
                display_name=display_name,
                owner_display_name=str(workflow.get("owner_display_name") or ""),
            ),
        }

    def _room_context(
        self,
        room_id: str,
    ) -> tuple[dict[str, object], dict[str, object]]:
        room = self._rooms.room(room_id)
        if not room:
            return {}, {}
        return room, self._rooms.room_settings(room_id)

    def _validate_retry(
        self,
        workflow: dict[str, object],
        *,
        request_id: str,
        token_fingerprint: str,
        device_auth_key: str,
        payload_hash: str,
    ) -> None:
        expected = (
            request_id,
            token_fingerprint,
            device_auth_key,
            payload_hash,
        )
        actual = (
            str(workflow.get("request_id") or ""),
            str(workflow.get("token_fingerprint") or ""),
            str(workflow.get("device_auth_key") or ""),
            str(workflow.get("payload_hash") or ""),
        )
        if actual != expected:
            raise AdmissionIdempotencyConflict(
                "request_id was already used with different admission inputs"
            )

    def _phase_updates(self, status: str) -> dict[str, object]:
        return {
            "status": status,
            "resume_phase": status,
            "updated_at": self._now().isoformat(),
        }


def _workflow_id(*, token_fingerprint: str, device_auth_key: str, request_id: str) -> str:
    material = json.dumps(
        [token_fingerprint, device_auth_key, request_id],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _payload_hash(payload: dict[str, object]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _prepared_record(prepared: PreparedInviteAdmission) -> dict[str, object]:
    return {
        "invite_id": prepared.invite_id,
        "room_id": prepared.meeting_id,
        "base_agent_id": prepared.base_agent_id,
        "invite_display_name": prepared.display_name,
        "invite_scope": prepared.invite_scope,
        "participant_type": prepared.participant_type,
        "client_type": prepared.client_type,
        "provider_kind": prepared.provider_kind,
        "owner_id": prepared.created_by_user_id,
        "reusable": prepared.reusable,
        "max_uses": prepared.max_uses,
        "nonce_fingerprint": prepared.nonce_fingerprint,
    }


def _prepared_from_workflow(
    workflow: dict[str, object],
    *,
    public_url: str,
) -> PreparedInviteAdmission:
    return PreparedInviteAdmission(
        invite_id=str(workflow.get("invite_id") or ""),
        meeting_id=str(workflow.get("room_id") or ""),
        base_agent_id=str(workflow.get("base_agent_id") or ""),
        display_name=str(workflow.get("invite_display_name") or ""),
        invite_scope=str(workflow.get("invite_scope") or "room"),
        participant_type=str(workflow.get("participant_type") or "human"),
        client_type=str(workflow.get("client_type") or "browser"),
        provider_kind=str(workflow.get("provider_kind") or "manual"),
        created_by_user_id=str(workflow.get("owner_id") or ""),
        reusable=bool(workflow.get("reusable")),
        max_uses=int(workflow.get("max_uses", 1) or 0),
        nonce_fingerprint=str(workflow.get("nonce_fingerprint") or ""),
        room_url=public_url,
    )


def _session_record(workflow: dict[str, object]) -> dict[str, object]:
    return {
        "agent_id": str(workflow.get("participant_id") or ""),
        "display_name": str(workflow.get("display_name") or ""),
        "meeting_id": str(workflow.get("room_id") or ""),
        "invite_scope": str(workflow.get("invite_scope") or "room"),
        "participant_type": str(workflow.get("participant_type") or "human"),
        "client_type": str(workflow.get("client_type") or "browser"),
        "provider_kind": str(workflow.get("provider_kind") or "manual"),
        "owner_id": str(workflow.get("owner_id") or ""),
        "connection_kind": str(workflow.get("connection_kind") or ""),
    }


def _room_record(
    room: dict[str, object],
    settings: dict[str, object],
    room_id: str,
) -> dict[str, object]:
    return {
        "room_label": clean_lobby_text(
            settings.get("label") or room.get("label"),
            limit=128,
        )
        or room_id,
        "room_topic": clean_lobby_text(
            settings.get("topic") or room.get("topic"),
            limit=160,
        ),
        "room_created_at": clean_lobby_text(room.get("created_at"), limit=64),
    }
