from __future__ import annotations

from dataclasses import dataclass

from agentsassemble.admission.invite_service import InviteApplicationService
from agentsassemble.admission.session_service import RoomSessionService
from agentsassemble.persistence.local.admission.repository import (
    MemoryInviteSessionRepository,
)
from agentsassemble.application.public_invite_runtime import PublicInviteRuntime
from agentsassemble.diagnostics.sensitive_text import redact_persisted_diagnostic_text
from agentsassemble.providers.launch_specs import NativeCliProviderSpec


class FakeBridgeManager:
    def __init__(self) -> None:
        self.starts: list[tuple[str, str]] = []
        self.specs: list[NativeCliProviderSpec] = []
        self.stops: list[tuple[str, str]] = []
        self.running: set[tuple[str, str]] = set()
        self.start_errors = []
        self.stop_errors = []
        self.close_called = False
        self.sensitive_values: dict[tuple[str, str], tuple[str, ...]] = {}
        self._stream_redactors = {}
        self.portal_publications: dict[
            tuple[str, str, str], dict[str, object]
        ] = {}

    def start(self, room_id, session, spec, *, server_url="", ticket_issuer=None):
        del server_url, ticket_issuer
        if self.start_errors:
            raise self.start_errors.pop(0)
        self.starts.append((room_id, str(session["session_id"])))
        self.running.add((room_id, str(session["session_id"])))
        self.specs.append(spec)
        return {
            "bridge_pid": 701,
            "bridge_handle_id": f"handle-{session['session_id']}",
            "resolved_executable": f"/fake/{spec.command[0]}",
        }

    def stop(self, room_id, session_id, *, timeout_seconds=2.0, handle_id=""):
        del timeout_seconds
        self.stops.append((room_id, session_id))
        if self.stop_errors:
            raise self.stop_errors.pop(0)
        self.running.discard((room_id, session_id))
        return {"stopped": bool(handle_id), "alive": False}

    def health(self, room_id, session_id):
        return {"running": (room_id, session_id) in self.running}

    def room_portal_publication(self, room_id, session_id, turn_id, *, handle_id=""):
        if handle_id != f"handle-{session_id}":
            return None
        publication = self.portal_publications.get((room_id, session_id, turn_id))
        return dict(publication) if publication is not None else None

    def set_room_portal_publication(self, room_id, session_id, turn_id, publication):
        self.portal_publications[(room_id, session_id, turn_id)] = dict(publication)

    def redact_diagnostic(self, room_id, session_id, value, *, limit=16_000):
        return redact_persisted_diagnostic_text(
            value,
            limit=limit,
            exact_values=self.sensitive_values.get((room_id, session_id), ()),
        )

    def _stream_redactor(self, room_id, session_id):
        from agentsassemble.diagnostics.sensitive_text import ExactSensitiveTextStreamRedactor

        key = (room_id, session_id)
        values = self.sensitive_values.get(key, ())
        current = self._stream_redactors.get(key)
        if current is None or current[0] != values:
            current = (values, ExactSensitiveTextStreamRedactor(values))
            self._stream_redactors[key] = current
        return current[1]

    def redact_stream_delta(self, room_id, session_id, turn_id, value):
        return self._stream_redactor(room_id, session_id).redact(turn_id, value)

    def flush_stream_delta(self, room_id, session_id, turn_id):
        return self._stream_redactor(room_id, session_id).flush(turn_id)

    def discard_stream_delta(self, room_id, session_id, turn_id):
        self._stream_redactor(room_id, session_id).discard(turn_id)

    def close(self):
        self.close_called = True
        return None


@dataclass(frozen=True)
class MemoryRoomAccessServices:
    repository: MemoryInviteSessionRepository
    public_invite: PublicInviteRuntime
    invites: InviteApplicationService
    sessions: RoomSessionService

    def controller_kwargs(self) -> dict[str, object]:
        return {
            "invite_application": self.invites,
            "room_sessions": self.sessions,
        }


def memory_room_access_services() -> MemoryRoomAccessServices:
    repository = MemoryInviteSessionRepository()
    public_invite = PublicInviteRuntime(environ={})
    invites = InviteApplicationService(
        repository,
        public_url=public_invite.public_url,
    )
    sessions = RoomSessionService(
        repository,
        token_prefix="aas1",
        ttl_seconds=3600,
        token_key=invites.signing_secret,
    )
    return MemoryRoomAccessServices(
        repository=repository,
        public_invite=public_invite,
        invites=invites,
        sessions=sessions,
    )
