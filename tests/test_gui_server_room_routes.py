from unittest.mock import Mock

from tests.gui_server_test_support import (
    HTTPError,
    HTTPStatus,
    Path,
    Request,
    RoomStore,
    ThreadingHTTPServer,
    UTC,
    _dispatch_room_route,
    _live_agent_lobby_flow_metadata,
    _make_handler,
    _read_sse_frame,
    base64,
    configure_room_users_store,
    connect_live_agent,
    create_room_invite,
    datetime,
    join_room_with_invite,
    json,
    patch,
    read_lobby,
    reset_room_invite_state,
    reset_room_users_state,
    room_sse_frames_after_cursor,
    set_room_member_muted,
    set_runtime_host_token,
    set_runtime_public_url,
    tempfile,
    threading,
    time,
    timedelta,
    unittest,
    urlopen,
    user_for_participant,
)
from agentsassemble.web.router import GuiDeps
from agentsassemble.identity.repository import device_auth_key
from agentsassemble.legacy.admission_projection import LiveAgentLegacyAdmissionProjection
from agentsassemble.identity.pairing import OperatorPairingService
from agentsassemble.persistence.local.identity.repository import IdentityStore
from agentsassemble.room_admission import RoomAdmissionService
from agentsassemble.room.realtime import RoomRealtimeController
from agentsassemble.room.errors import RoomCommandRejected
from agentsassemble.room.write_budget import RoomWriteBudgetPolicy
from agentsassemble.admission.coordinator import RoomAdmissionCoordinator
from agentsassemble.admission.invite import verify_session_token
from agentsassemble.admission.invite import compatibility_public_invite_runtime
from agentsassemble.admission.invite_service import InviteApplicationService
from agentsassemble.persistence.local.admission.repository import (
    MemoryInviteSessionRepository,
)
from agentsassemble.admission.session_service import RoomSessionService


def _invite_route_dependencies(root: Path) -> GuiDeps:
    rooms = RoomStore(root)
    identities = IdentityStore(root / "identity.db")
    repository = MemoryInviteSessionRepository()
    invites = InviteApplicationService(repository)
    sessions = RoomSessionService(
        repository,
        token_prefix="aas1",
        ttl_seconds=3600,
        token_key=invites.signing_secret,
    )
    deps = GuiDeps(
        output_root=root,
        room_repository=rooms,
        identity_backend=identities,
        invite_application=invites,
        room_sessions=sessions,
        admission_preflight_service=RoomAdmissionService(
            identities=identities,
            rooms=rooms,
            invite_inspector=invites.inspect,
        ),
        admission_coordinator=RoomAdmissionCoordinator(
            invites=invites,
            sessions=sessions,
            identities=identities,
            rooms=rooms,
        ),
        operator_pairing_service=OperatorPairingService(
            identities=identities,
            rooms=rooms,
            sessions=sessions,
        ),
        public_invite_runtime=compatibility_public_invite_runtime(),
        legacy_admission_projection=LiveAgentLegacyAdmissionProjection(root),
    )
    deps.room_command_handler = lambda _identity, command: _room_lifecycle_command(
        deps,
        command,
    )
    deps.room_runtime_command_handler = (
        lambda identity, command, _server_url: deps.handle_room_command(
            identity,
            command,
        )
    )
    return deps


def _room_lifecycle_command(
    deps: GuiDeps,
    command: dict[str, object],
) -> dict[str, object]:
    payload = dict(command.get("payload") or {})
    room_id = str(payload.get("room_id") or "")
    action = str(command.get("action") or "")
    if action == "participant.export":
        participant_id = str(payload.get("participant_id") or "")
        participant = deps.rooms.set_participant_status(
            room_id,
            participant_id,
            "exported",
        )
        return {
            "op": "ack",
            "request_id": str(command.get("request_id") or ""),
            "accepted": True,
            "action": action,
            "result": {"participant": participant},
        }
    status = "archived" if payload.get("archived") else "active"
    deps.rooms.set_room_status(room_id, status)
    return {
        "op": "ack",
        "request_id": str(command.get("request_id") or ""),
        "accepted": True,
        "action": "room.archive",
        "result": {"room_id": room_id, "status": status},
    }


def _seed_canonical_agent_session(
    root: Path,
    *,
    room_id: str = "session-room",
    agent_id: str = "agent-1",
    session_id: str = "session-1",
    display_name: str = "Agent One",
    provider_kind: str = "codex_live_session",
) -> RoomStore:
    store = RoomStore(root)
    store.create_room(room_id)
    store.upsert_participant(
        room_id,
        {
            "participant_id": agent_id,
            "display_name": display_name,
            "role": "agent",
            "participant_type": "agent",
            "status": "joined",
            "session_id": session_id,
            "provider_kind": provider_kind,
        },
    )
    store.upsert_session(
        room_id,
        {
            "session_id": session_id,
            "participant_id": agent_id,
            "display_name": display_name,
            "status": "attached",
            "provider_kind": provider_kind,
        },
    )
    return store


class _LegacyFacadeSessionVerifier:
    """Make legacy invite-token state an explicit dependency in facade tests."""

    def verify(self, token: str) -> dict[str, object] | None:
        return verify_session_token(token)


def _legacy_facade_route_dependencies(root: Path) -> GuiDeps:
    return GuiDeps(
        output_root=root,
        room_repository=RoomStore(root),
        identity_backend=IdentityStore(root / "identity.db"),
        public_invite_runtime=compatibility_public_invite_runtime(),
        room_sessions=_LegacyFacadeSessionVerifier(),  # type: ignore[arg-type]
    )


class GuiServerRoomRouteTests(unittest.TestCase):
    def test_room_role_http_route_uses_canonical_command_budget(self):
        reset_room_invite_state()
        reset_room_users_state()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            deps = _invite_route_dependencies(root)
            deps.rooms.create_room("role-room")
            deps.rooms.upsert_participant(
                "role-room",
                {
                    "participant_id": "agent-1",
                    "display_name": "Agent One",
                    "participant_type": "agent",
                    "status": "joined",
                    "role": "agent",
                },
            )
            provider_catalog = Mock()
            provider_catalog.subscribe.return_value = lambda: None
            provider_catalog.snapshot.return_value = {
                "catalog_revision": "test",
                "providers": [],
            }
            controller = RoomRealtimeController(
                root,
                invite_application=deps.invites,
                room_sessions=deps.sessions,
                repository=deps.rooms,
                provider_catalog=provider_catalog,
                write_budget_policy=RoomWriteBudgetPolicy(
                    max_commands_per_window=1,
                    max_payload_bytes_per_window=100_000,
                    max_room_commands_per_window=1,
                    max_room_payload_bytes_per_window=100_000,
                ),
            )
            self.addCleanup(controller.close)
            deps.room_command_handler = controller.handle_command

            first = _dispatch_room_route(
                root,
                path="/api/room-members/role",
                method="POST",
                payload={
                    "meeting_id": "role-room",
                    "participant_id": "agent-1",
                    "role": "reviewer",
                },
                deps=deps,
            )
            second = _dispatch_room_route(
                root,
                path="/api/room-members/role",
                method="POST",
                payload={
                    "meeting_id": "role-room",
                    "participant_id": "agent-1",
                    "role": "implementer",
                },
                deps=deps,
            )

            self.assertEqual(first.sent_json["member"]["role"], "reviewer")
            self.assertEqual(second.sent_error[0], HTTPStatus.CONFLICT)
            self.assertEqual(
                deps.rooms.participant("role-room", "agent-1")["role"],
                "reviewer",
            )

    def test_room_invite_creation_requires_an_existing_canonical_room(self):
        reset_room_invite_state()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            deps = _invite_route_dependencies(root)

            response = _dispatch_room_route(
                root,
                path="/api/room-invite/create",
                method="POST",
                payload={
                    "meeting_id": "missing-room",
                    "display_name": "Guest",
                    "local_dev_preview": True,
                },
                deps=deps,
            )

        self.assertEqual(response.sent_error, (HTTPStatus.NOT_FOUND, "room was not found"))

    def test_room_invite_join_rejects_a_stale_deleted_room_without_crashing(self):
        reset_room_invite_state()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            deps = _invite_route_dependencies(root)
            invite = deps.invites.create(
                room_url="http://127.0.0.1:8765",
                meeting_id="deleted-room",
                display_name="Guest",
            )

            response = _dispatch_room_route(
                root,
                path="/api/room-invite/join",
                method="POST",
                payload={
                    "invite_token": invite["join_code"],
                    "request_id": "86a68d2b-7bc7-49b7-886a-6dff35b20b69",
                },
                deps=deps,
            )

        self.assertEqual(
            response.sent_error,
            (HTTPStatus.GONE, "room was deleted or does not exist"),
        )

    def test_room_invite_join_requires_a_request_id(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            response = _dispatch_room_route(
                Path(temp_dir),
                path="/api/room-invite/join",
                method="POST",
                payload={"invite_token": "present-token"},
                deps=_invite_route_dependencies(Path(temp_dir)),
            )

        self.assertEqual(
            response.sent_error,
            (HTTPStatus.BAD_REQUEST, "request_id is required"),
        )
        self.assertEqual(response.sent_error_code, "request_id_required")

    def test_room_invite_join_rejects_a_noncanonical_request_id(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            response = _dispatch_room_route(
                Path(temp_dir),
                path="/api/room-invite/join",
                method="POST",
                payload={
                    "invite_token": "present-token",
                    "request_id": "B4BD54E0-EC42-4B19-BBC4-0D888A5A32A3",
                },
                deps=_invite_route_dependencies(Path(temp_dir)),
            )

        self.assertEqual(response.sent_error[0], HTTPStatus.BAD_REQUEST)
        self.assertEqual(response.sent_error_code, "request_id_invalid")

    def test_room_invite_join_returns_conflict_for_changed_idempotent_request(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            deps = _invite_route_dependencies(root)
            deps.rooms.create_room("room-a", label="Room A")
            invite = deps.invites.create(
                room_url="http://127.0.0.1:8765",
                meeting_id="room-a",
                display_name="Guest",
                max_uses=2,
            )
            base_payload = {
                "invite_token": invite["join_code"],
                "request_id": "1f44bcc0-8646-492c-8c7f-d422f75723f6",
                "device_token": "known-device-token",
            }
            first = _dispatch_room_route(
                root,
                path="/api/room-invite/join",
                method="POST",
                payload={**base_payload, "display_name": "First Name"},
                deps=deps,
            )
            conflict = _dispatch_room_route(
                root,
                path="/api/room-invite/join",
                method="POST",
                payload={**base_payload, "display_name": "Changed Name"},
                deps=deps,
            )

        self.assertEqual(first.sent_json["status"], "admitted")
        self.assertEqual(conflict.sent_error[0], HTTPStatus.CONFLICT)
        self.assertEqual(conflict.sent_error_code, "idempotency_conflict")

    def test_agent_invite_is_not_consumed_by_browser_join(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            deps = _invite_route_dependencies(root)
            deps.rooms.create_room("agent-room", label="Agent room")
            invite = deps.invites.create(
                room_url="http://127.0.0.1:8765",
                meeting_id="agent-room",
                agent_id="remote-codex",
                display_name="Remote Codex",
                max_uses=1,
                participant_type="agent",
                client_type="agent_bridge",
                provider_kind="codex",
            )

            browser = _dispatch_room_route(
                root,
                path="/api/room-invite/join",
                method="POST",
                payload={
                    "invite_token": invite["join_code"],
                    "request_id": "da74df15-53d2-4700-9716-5732546b210a",
                },
                deps=deps,
            )
            attendee = _dispatch_room_route(
                root,
                path="/api/room-invite/agent-join",
                method="POST",
                payload={
                    "invite_token": invite["join_code"],
                    "request_id": "f7544248-ebc1-4486-8d97-b6ce59d5ca34",
                    "provider_kind": "codex",
                },
                deps=deps,
            )

        self.assertEqual(browser.sent_error[0], HTTPStatus.FORBIDDEN)
        self.assertEqual(browser.sent_error_code, "agent_client_required")
        self.assertEqual(attendee.sent_json["status"], "admitted")
        self.assertEqual(attendee.sent_json["client_type"], "agent_bridge")
        self.assertEqual(attendee.sent_json["participant_type"], "remote")

    def test_legacy_projection_failure_does_not_rollback_agent_join(self):
        def fail_projection(_root: Path, _payload: dict[str, object]) -> object:
            raise RuntimeError("secret path /tmp/private-token must stay hidden")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            deps = _invite_route_dependencies(root)
            projection = LiveAgentLegacyAdmissionProjection(root, connect=fail_projection)
            deps.legacy_admission_projection = projection
            deps.rooms.create_room("agent-room", label="Agent room")
            invite = deps.invites.create(
                room_url="http://127.0.0.1:8765",
                meeting_id="agent-room",
                agent_id="remote-agent",
                display_name="Remote Agent",
                participant_type="agent",
                client_type="agent_bridge",
                provider_kind="codex",
            )

            joined = _dispatch_room_route(
                root,
                path="/api/room-invite/agent-join",
                method="POST",
                payload={
                    "invite_token": invite["join_code"],
                    "request_id": "3576aa31-2798-43e0-8737-0c3da3a806f4",
                    "provider_kind": "codex",
                },
                deps=deps,
            )
        self.assertEqual(joined.sent_json["status"], "admitted")
        diagnostics = projection.diagnostics()
        self.assertEqual(diagnostics["failure_count"], 1)
        self.assertEqual(
            [failure["operation"] for failure in diagnostics["recent_failures"]],
            ["participant_joined"],
        )
        self.assertNotIn("secret", str(diagnostics))
        self.assertNotIn("/tmp", str(diagnostics))

    def test_rooms_endpoint_does_not_list_identity_only_ghost_room(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            deps = _invite_route_dependencies(root)
            deps.identities.upsert_room(
                room_id="db-room",
                label="DB 방",
                origin="frontend_room",
            )
            handler = _dispatch_room_route(root, path="/api/rooms", deps=deps)
            payload = handler.sent_json

            self.assertEqual(payload["rooms"], [])

    def test_rooms_endpoint_uses_injected_identity_backend_not_global_registry(self):
        reset_room_users_state()
        self.addCleanup(reset_room_users_state)
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            deps = _invite_route_dependencies(root)
            deps.identities.upsert_room(
                room_id="injected-room",
                label="Injected",
                origin="frontend_room",
            )
            deps.rooms.create_room("injected-room", label="Injected")
            configure_room_users_store(root / "compatibility-identity.db")
            from agentsassemble.application.room_users import upsert_room

            upsert_room(
                room_id="compatibility-room",
                label="Compatibility",
                origin="frontend_room",
            )

            payload = _dispatch_room_route(
                root,
                path="/api/rooms",
                deps=deps,
            ).sent_json

        self.assertEqual(
            [room["room_id"] for room in payload["rooms"]],
            ["injected-room"],
        )

    def test_guest_room_capability_cannot_read_another_canonical_room(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            deps = _invite_route_dependencies(root)
            deps.public_invite.set_public_url("https://room.example.com")
            deps.public_invite.set_host_token("host-secret")
            deps.rooms.create_room("guest-room", label="Guest Room")
            deps.rooms.create_room("private-room", label="Private Room")
            invite = deps.invites.create(
                room_url="http://127.0.0.1:8765",
                meeting_id="guest-room",
                display_name="Guest",
            )
            joined = _dispatch_room_route(
                root,
                path="/api/room-invite/join",
                method="POST",
                payload={
                    "invite_token": invite["join_code"],
                    "request_id": "b42cce95-8b47-48aa-8690-5c05d38ec708",
                    "device_token": "guest-device-token",
                },
                deps=deps,
            ).sent_json
            headers = {
                "Authorization": f"Bearer {joined['session_token']}",
                "Host": "room.example.com",
                "Origin": "https://room.example.com",
            }

            listed = _dispatch_room_route(
                root,
                path="/api/rooms",
                headers=headers,
                loopback=False,
                deps=deps,
            )
            allowed = _dispatch_room_route(
                root,
                path="/api/room-settings?room_id=guest-room",
                headers=headers,
                loopback=False,
                deps=deps,
            )
            forbidden = [
                _dispatch_room_route(
                    root,
                    path=path,
                    headers=headers,
                    loopback=False,
                    deps=deps,
                )
                for path in (
                    "/api/room-settings?room_id=private-room",
                    "/api/room-members?meeting_id=private-room",
                    "/api/room-channels?meeting_id=private-room",
                    "/api/rooms/state?room_id=private-room",
                )
            ]

        self.assertEqual(
            [room["room_id"] for room in listed.sent_json["rooms"]],
            ["guest-room"],
        )
        self.assertEqual(
            allowed.sent_json["settings"]["room_id"],
            "guest-room",
        )
        self.assertTrue(
            all(
                response.sent_error
                == (
                    HTTPStatus.FORBIDDEN,
                    "session is not authorized for this room",
                )
                for response in forbidden
            )
        )

    def test_room_creation_rolls_back_identity_projection_when_canonical_create_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            deps = _invite_route_dependencies(root)
            failing_rooms = Mock(wraps=deps.rooms)
            failing_rooms.create_room.side_effect = ValueError(
                "canonical create failed",
            )
            deps.room_repository = failing_rooms

            response = _dispatch_room_route(
                root,
                path="/api/rooms",
                method="POST",
                payload={"room_id": "partial-room", "label": "Partial Room"},
                deps=deps,
            )
            identity_room = deps.identities.get_room("partial-room")

        self.assertEqual(
            response.sent_error,
            (HTTPStatus.BAD_REQUEST, "canonical create failed"),
        )
        self.assertIsNone(identity_room)

    def test_archive_failure_restores_the_identity_projection(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            deps = _invite_route_dependencies(root)
            deps.rooms.create_room("archive-room", label="Archive Room")
            deps.identities.upsert_room(
                room_id="archive-room",
                label="Archive Room",
            )
            failing_rooms = Mock(wraps=deps.rooms)
            failing_rooms.set_room_status.side_effect = ValueError(
                "canonical archive failed",
            )
            deps.room_repository = failing_rooms

            response = _dispatch_room_route(
                root,
                path="/api/rooms/archive",
                method="POST",
                payload={"room_id": "archive-room", "archived": True},
                deps=deps,
            )
            identity_archived = bool(
                deps.identities.get_room("archive-room")["archived"]
            )
            canonical_status = deps.rooms.room("archive-room")["status"]

        self.assertEqual(
            response.sent_error,
            (HTTPStatus.BAD_REQUEST, "canonical archive failed"),
        )
        self.assertFalse(identity_archived)
        self.assertEqual(canonical_status, "active")

    def test_room_ensure_route_materializes_server_room_for_local_operator(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            deps = _invite_route_dependencies(root)
            operator = deps.identities.claim_local_operator_credential(
                device_auth_key("local-operator-device"),
                display_name="Operator",
            )

            response = _dispatch_room_route(
                root,
                path="/api/room/ensure",
                method="POST",
                payload={"meeting_id": "new-room", "label": "New Room"},
                deps=deps,
            ).sent_json

            room = next(
                item
                for item in deps.identities.list_rooms()
                if item["room_id"] == "new-room"
            )
            self.assertEqual(response, {"status": "ready", "meeting_id": "new-room"})
            self.assertEqual(room["owner_id"], operator["user_id"])
            self.assertEqual(room["label"], "New Room")
            self.assertEqual(deps.rooms.room("new-room")["room_id"], "new-room")


    def test_agent_session_http_delegates_creation_to_canonical_room_command(self):
        reset_room_invite_state()
        reset_room_users_state()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            deps = _invite_route_dependencies(root)
            deps.rooms.create_room("session-room")
            commands: list[tuple[dict[str, object], dict[str, object]]] = []
            server_urls: list[str] = []

            def handle_command(
                identity: dict[str, object],
                command: dict[str, object],
            ) -> dict[str, object]:
                commands.append((dict(identity), dict(command)))
                return {
                    "op": "ack",
                    "accepted": True,
                    "action": "agent.create",
                    "result": {
                        "status": "created",
                        "agent_session": {"session_id": "server-session"},
                    },
                }

            deps.room_command_handler = handle_command
            deps.room_runtime_command_handler = (
                lambda identity, command, server_url: (
                    server_urls.append(server_url)
                    or handle_command(identity, command)
                )
            )
            response = _dispatch_room_route(
                root,
                path="/api/agent-sessions",
                method="POST",
                payload={
                    "room_id": "session-room",
                    "provider_id": "codex",
                    "catalog_revision": "catalog-1",
                    "owner_id": "attacker-chosen-owner",
                    "created_by": "attacker-chosen-creator",
                },
                deps=deps,
            )

            self.assertEqual(response.sent_json["status"], "created")
            self.assertEqual(len(commands), 1)
            self.assertEqual(server_urls, ["http://127.0.0.1:8765"])
            identity, command = commands[0]
            self.assertEqual(identity["principal_user_id"], "operator-local-user")
            self.assertEqual(command["action"], "agent.create")
            self.assertNotIn("owner_id", command["payload"])
            self.assertNotIn("created_by", command["payload"])
            self.assertEqual(deps.rooms.participants("session-room"), [])

    def test_agent_session_http_preserves_state_when_canonical_resume_rejects(self):
        reset_room_invite_state()
        reset_room_users_state()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            deps = _invite_route_dependencies(root)
            deps.rooms.create_room("session-room")

            def reject_resume(
                _identity: dict[str, object],
                command: dict[str, object],
            ) -> dict[str, object]:
                self.assertEqual(command["action"], "agent.readd")
                raise RoomCommandRejected(
                    "Agent session agent-1 was not found.",
                    code="not_found",
                )

            deps.room_command_handler = reject_resume
            response = _dispatch_room_route(
                root,
                path="/api/agent-sessions/resume",
                method="POST",
                payload={
                    "room_id": "session-room",
                    "agent_id": "agent-1",
                    "session_id": "session-1",
                    "start": True,
                },
                deps=deps,
            )

            self.assertEqual(response.sent_error[0], HTTPStatus.NOT_FOUND)
            self.assertEqual(response.sent_error_code, "not_found")
            self.assertEqual(deps.rooms.participants("session-room"), [])
            self.assertEqual(deps.rooms.sessions("session-room"), [])


    def test_room_session_export_endpoint_persists_exported_state(self):
        reset_room_invite_state()
        reset_room_users_state()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _seed_canonical_agent_session(root)

            exported = _dispatch_room_route(
                root,
                path="/api/room-participants/export",
                method="POST",
                payload={"room_id": "session-room", "participant_id": "agent-1"},
                deps=_invite_route_dependencies(root),
            ).sent_json
            state = _dispatch_room_route(root, path="/api/rooms/state?room_id=session-room").sent_json

            self.assertEqual(exported["status"], "exported")
            self.assertEqual(state["participants"][0]["status"], "exported")
            self.assertEqual(state["active_participants"], [])


    def test_room_participant_leave_requires_matching_session_or_moderator(self):
        reset_room_invite_state()
        reset_room_users_state()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            configure_room_users_store(root / "identity.db")
            set_runtime_public_url("https://room.example.com")
            set_runtime_host_token("host-secret")
            invite = create_room_invite(
                room_url="http://127.0.0.1:8765",
                meeting_id="session-room",
                agent_id="agent-2",
                display_name="Agent Two",
            )
            session = join_room_with_invite(
                invite["invite_token"],
                meeting_id="session-room",
                display_name="Agent Two",
                device_token="agent-two-device-token",
            )
            _seed_canonical_agent_session(root)

            denied = _dispatch_room_route(
                root,
                path="/api/room-participants/leave",
                method="POST",
                payload={"room_id": "session-room", "participant_id": "agent-1"},
                headers={"Authorization": f"Bearer {session['session_token']}"},
                loopback=False,
                deps=_legacy_facade_route_dependencies(root),
            )

            self.assertEqual(denied.sent_error, (HTTPStatus.FORBIDDEN, "participant session token required"))
            self.assertEqual(RoomStore(root).participant("session-room", "agent-1")["status"], "joined")

    def test_room_participant_session_cannot_leave_its_identity_in_another_room(self):
        reset_room_invite_state()
        reset_room_users_state()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            configure_room_users_store(root / "identity.db")
            set_runtime_public_url("https://room.example.com")
            set_runtime_host_token("host-secret")
            invite = create_room_invite(
                room_url="http://127.0.0.1:8765",
                meeting_id="session-room",
                agent_id="shared-agent",
                display_name="Shared Agent",
            )
            session = join_room_with_invite(
                invite["invite_token"],
                meeting_id="session-room",
                display_name="Shared Agent",
                device_token="shared-agent-device-token",
            )
            participant_id = str(session["agent_id"])
            store = RoomStore(root)
            store.ensure_room("other-room")
            store.upsert_participant(
                "other-room",
                {
                    "participant_id": participant_id,
                    "display_name": "Other Room Identity",
                    "participant_type": "human",
                    "status": "joined",
                },
            )

            denied = _dispatch_room_route(
                root,
                path="/api/room-participants/leave",
                method="POST",
                payload={"room_id": "other-room", "participant_id": participant_id},
                headers={"Authorization": f"Bearer {session['session_token']}"},
                loopback=False,
                deps=_legacy_facade_route_dependencies(root),
            )

            self.assertEqual(
                denied.sent_error,
                (HTTPStatus.FORBIDDEN, "participant session token required"),
            )
            self.assertEqual(store.participant("other-room", participant_id)["status"], "joined")


    def test_agent_session_http_resume_start_requires_authorized_runner(self):
        reset_room_invite_state()
        reset_room_users_state()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            denied = _dispatch_room_route(
                root,
                path="/api/agent-sessions/resume",
                method="POST",
                payload={
                    "room_id": "session-room",
                    "agent_id": "agent-1",
                    "session_id": "session-1",
                    "provider_kind": "codex_live_session",
                    "start": True,
                },
                loopback=False,
            )

            self.assertEqual(
                denied.sent_error,
                (HTTPStatus.FORBIDDEN, "Agent Session control requires local operator or host authorization"),
            )
            self.assertEqual(RoomStore(root).participants("session-room"), [])

    def test_agent_session_http_mutations_require_authorization_without_process_start(self):
        reset_room_invite_state()
        reset_room_users_state()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            for path, payload in (
                (
                    "/api/agent-sessions",
                    {"room_id": "session-room", "agent_id": "agent-1", "provider_kind": "codex_live_session"},
                ),
                (
                    "/api/agent-sessions/resume",
                    {"room_id": "session-room", "agent_id": "agent-1", "session_id": "session-1"},
                ),
            ):
                with self.subTest(path=path):
                    denied = _dispatch_room_route(
                        root,
                        path=path,
                        method="POST",
                        payload=payload,
                        loopback=False,
                    )
                    self.assertEqual(
                        denied.sent_error,
                        (HTTPStatus.FORBIDDEN, "Agent Session control requires local operator or host authorization"),
                    )

            self.assertEqual(RoomStore(root).participants("session-room"), [])

    def test_agent_session_http_mutation_accepts_host_credential(self):
        reset_room_invite_state()
        self.addCleanup(reset_room_invite_state)
        reset_room_users_state()
        set_runtime_host_token("host-secret")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            deps = _invite_route_dependencies(root)
            deps.rooms.create_room("session-room")
            deps.room_command_handler = lambda _identity, command: {
                "op": "ack",
                "accepted": True,
                "action": command["action"],
                "result": {"status": "created"},
            }
            created = _dispatch_room_route(
                root,
                path="/api/agent-sessions",
                method="POST",
                payload={"room_id": "session-room", "agent_id": "agent-1", "provider_kind": "codex_live_session"},
                headers={"X-Host-Token": "host-secret"},
                loopback=False,
                deps=deps,
            )

            self.assertIsNone(created.sent_error)
            self.assertEqual(created.sent_json["status"], "created")

    def test_agent_session_http_turn_is_retired_without_running_a_provider(self):
        reset_room_invite_state()
        reset_room_users_state()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = RoomStore(root)
            store.create_room("session-room")
            baseline = list(store.read_events("session-room"))
            turned = _dispatch_room_route(
                root,
                path="/api/agent-sessions/turn",
                method="POST",
                payload={
                    "room_id": "session-room",
                    "agent_id": "agent-1",
                    "instruction": "Answer now.",
                    "runtime_mode": "exec_jsonl_fallback",
                },
            )

            self.assertEqual(turned.sent_error[0], HTTPStatus.GONE)
            self.assertEqual(turned.sent_error_code, "legacy_route_retired")
            self.assertEqual(store.read_events("session-room"), baseline)

    def test_agent_session_http_next_turn_is_retired_without_assigning_a_turn(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = RoomStore(root)
            store.create_room("session-room")
            baseline = list(store.read_events("session-room"))

            response = _dispatch_room_route(
                root,
                path="/api/agent-sessions/next-turn",
                method="POST",
                payload={"room_id": "session-room"},
            )

            self.assertEqual(response.sent_error[0], HTTPStatus.GONE)
            self.assertEqual(response.sent_error_code, "legacy_route_retired")
            self.assertEqual(store.read_events("session-room"), baseline)


    def test_legacy_lobby_message_does_not_start_a_canonical_provider_turn(self):
        reset_room_invite_state()
        reset_room_users_state()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _seed_canonical_agent_session(
                root,
                agent_id="agent-a",
                session_id="agent-a",
                display_name="Agent A",
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/lobby",
                    data=json.dumps(
                        {
                            "name": "나",
                            "side": "mine",
                            "kind": "message",
                            "message": "레거시 호환 경로 메시지",
                            "flow_meeting_id": "session-room",
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                )
                with urlopen(request, timeout=4) as response:
                    posted = json.loads(response.read().decode("utf-8"))

                events = RoomStore(root).read_events("session-room")
                self.assertEqual(posted["event"]["message"], "레거시 호환 경로 메시지")
                self.assertNotIn("turn_queued", [event["type"] for event in events])
                self.assertNotIn("turn_assigned", [event["type"] for event in events])
                self.assertFalse(
                    any(
                        event.get("type") == "message_final"
                        and event.get("content") == "레거시 호환 경로 메시지"
                        for event in events
                    )
                )
            finally:
                server.shutdown()
                server.server_close()


    def test_room_events_stream_route_replays_missed_event_as_sse(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = RoomStore(root)
            store.create_room("session-room")
            cursor = store.read_events("session-room")[-1]["id"]
            store.append_event("session-room", "message_final", content="hello")
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/room-events/stream?room_id=session-room&cursor={cursor}",
                    timeout=4,
                ) as response:
                    frame = _read_sse_frame(response)
                    content_type = response.headers.get_content_type()
                store.append_event("session-room", "system", content="close stream")
                time.sleep(0.25)
                store.append_event("session-room", "system", content="confirm stream close")
                time.sleep(0.25)
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(content_type, "text/event-stream")
            self.assertIn("event: message_final", frame)
            self.assertIn('"content": "hello"', frame)

    def test_room_events_stream_route_emits_heartbeat_while_room_is_idle(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = RoomStore(root)
            store.create_room("idle-room")
            cursor = store.read_events("idle-room")[-1]["id"]
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            started_at = time.monotonic()
            try:
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/room-events/stream?room_id=idle-room&cursor={cursor}",
                    timeout=2.5,
                ) as response:
                    frame = _read_sse_frame(response, timeout=2.0)
                    elapsed = time.monotonic() - started_at
                store.append_event("idle-room", "system", content="close stream")
                time.sleep(0.25)
                store.append_event("idle-room", "system", content="confirm stream close")
                time.sleep(0.25)
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(frame, "event: heartbeat\ndata: {}")
            self.assertGreaterEqual(elapsed, 0.8)
            self.assertLess(elapsed, 2.0)


    def test_roomstore_joined_row_wins_over_old_live_agent_roster_row(self):
        reset_room_invite_state()
        reset_room_users_state()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            connect_live_agent(
                root,
                {
                    "agent_id": "agent-1",
                    "display_name": "Old Live Agent",
                    "meeting_id": "session-room",
                    "provider_kind": "codex_live_session",
                    "connection_kind": "live_session",
                    "status": "online",
                },
            )
            _seed_canonical_agent_session(root, display_name="Canonical Agent")

            members = _dispatch_room_route(root, path="/api/room-members?meeting_id=session-room").sent_json["members"]

            self.assertEqual(len([member for member in members if member["participant_id"] == "agent-1"]), 1)
            member = next(member for member in members if member["participant_id"] == "agent-1")
            self.assertEqual(member["source"], "agent_session")
            self.assertEqual(member["display_name"], "Canonical Agent")
            self.assertEqual(member["status"], "joined")


    def test_roomstore_exported_row_suppresses_old_live_agent_roster_row(self):
        reset_room_invite_state()
        reset_room_users_state()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            connect_live_agent(
                root,
                {
                    "agent_id": "agent-1",
                    "display_name": "Old Live Agent",
                    "meeting_id": "session-room",
                    "provider_kind": "codex_live_session",
                    "connection_kind": "live_session",
                    "status": "online",
                },
            )
            _seed_canonical_agent_session(root)
            _dispatch_room_route(
                root,
                path="/api/room-participants/export",
                method="POST",
                payload={"room_id": "session-room", "participant_id": "agent-1"},
                deps=_invite_route_dependencies(root),
            )

            members = _dispatch_room_route(root, path="/api/room-members?meeting_id=session-room").sent_json["members"]

            self.assertNotIn("agent-1", [member["participant_id"] for member in members])


    def test_roomstore_roster_keeps_mute_metadata_without_changing_status(self):
        reset_room_invite_state()
        reset_room_users_state()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _seed_canonical_agent_session(root)
            set_room_member_muted(root, meeting_id="session-room", participant_id="agent-1", muted=True)

            member = _dispatch_room_route(root, path="/api/room-members?meeting_id=session-room").sent_json["members"][0]

            self.assertEqual(member["source"], "agent_session")
            self.assertEqual(member["status"], "joined")
            self.assertTrue(member["muted"])


    def test_rooms_endpoint_guest_sees_only_own_rooms(self):
        reset_room_invite_state()
        reset_room_users_state()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            configure_room_users_store(root / "identity.db")
            try:
                from agentsassemble.application.room_users import upsert_room

                set_runtime_public_url("https://room.example.com")
                set_runtime_host_token("host-secret")
                invite = create_room_invite(
                    room_url="http://127.0.0.1:8765",
                    meeting_id="guest-room",
                    agent_id="guest",
                    display_name="Guest",
                )
                session = join_room_with_invite(
                    invite["invite_token"],
                    meeting_id="guest-room",
                    display_name="Guest",
                    device_token="guest-device-token-123",
                )
                guest_user = user_for_participant(str(session["agent_id"]))
                upsert_room(
                    room_id="guest-room",
                    owner_id=str(guest_user["user_id"]),
                    label="Guest Room",
                    origin="frontend_room",
                )
                upsert_room(room_id="operator-room", label="Operator Room", origin="frontend_room")
                RoomStore(root).create_room("guest-room", label="Guest Room")
                RoomStore(root).create_room("operator-room", label="Operator Room")
                handler = _dispatch_room_route(
                    root,
                    path="/api/rooms",
                    headers={
                        "Authorization": f"Bearer {session['session_token']}",
                        "Host": "room.example.com",
                        "Origin": "https://room.example.com",
                    },
                    loopback=False,
                    deps=_legacy_facade_route_dependencies(root),
                )
                payload = handler.sent_json
            finally:
                reset_room_invite_state()
                reset_room_users_state()

            self.assertEqual([room["room_id"] for room in payload["rooms"]], ["guest-room"])


    def test_rooms_archive_hides_room_from_default_list(self):
        reset_room_invite_state()
        reset_room_users_state()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            configure_room_users_store(root / "identity.db")
            try:
                from agentsassemble.application.room_users import upsert_room

                set_runtime_host_token("host-secret")
                upsert_room(room_id="archive-room", label="Archive Room", origin="frontend_room")
                deps = _invite_route_dependencies(root)
                deps.rooms.create_room("archive-room", label="Archive Room")
                archive_handler = _dispatch_room_route(
                    root,
                    path="/api/rooms/archive",
                    method="POST",
                    payload={"room_id": "archive-room", "archived": True},
                    headers={"X-Host-Token": "host-secret"},
                    deps=deps,
                )
                self.assertEqual(archive_handler.sent_json["status"], "archived")
                default_payload = _dispatch_room_route(root, path="/api/rooms").sent_json
                archived_payload = _dispatch_room_route(root, path="/api/rooms?include_archived=true").sent_json
            finally:
                reset_room_invite_state()
                reset_room_users_state()

            self.assertNotIn("archive-room", [room["room_id"] for room in default_payload["rooms"]])
            archived_rooms = {room["room_id"]: room for room in archived_payload["rooms"]}
            self.assertTrue(archived_rooms["archive-room"]["archived"])


    def test_live_agent_lobby_flow_metadata_computes_reply_post_latency_from_start_time(self):
        started_at = (datetime.now(UTC) - timedelta(milliseconds=25)).isoformat()

        metadata = _live_agent_lobby_flow_metadata(
            {
                "flow_id": "flow-rain",
                "flow_runtime_mode": "runtime_managed_room_turn",
                "flow_reply_post_started_at": started_at,
            }
        )

        self.assertEqual(metadata["flow_id"], "flow-rain")
        self.assertEqual(metadata["flow_runtime_mode"], "runtime_managed_room_turn")
        self.assertIsInstance(metadata["flow_reply_post_ms"], int)
        self.assertGreaterEqual(metadata["flow_reply_post_ms"], 0)


    def test_attachment_upload_sanitizes_and_downloads_image(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                server_url = f"http://127.0.0.1:{server.server_port}"
                upload = Request(
                    f"{server_url}/api/attachments",
                    data=json.dumps(
                        {
                            "filename": "../yanagi.png",
                            "content_type": "image/png",
                            "data_base64": base64.b64encode(b"fake-png-bytes").decode("ascii"),
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(upload, timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))

                attachment = payload["attachment"]
                self.assertEqual(attachment["filename"], "yanagi.png")
                self.assertEqual(attachment["content_type"], "image/png")
                self.assertEqual(attachment["size"], len(b"fake-png-bytes"))
                self.assertTrue(attachment["is_image"])
                self.assertNotIn("data_base64", attachment)
                self.assertTrue((root / "attachments" / attachment["id"] / "yanagi.png").exists())
                self.assertFalse((root / "yanagi.png").exists())

                with urlopen(f"{server_url}{attachment['url']}", timeout=4) as response:
                    self.assertEqual(response.read(), b"fake-png-bytes")
                    self.assertEqual(response.headers.get_content_type(), "image/png")
                    self.assertIn("inline", response.headers.get("Content-Disposition", ""))
                with urlopen(f"{server_url}{attachment['download_url']}", timeout=4) as response:
                    self.assertEqual(response.read(), b"fake-png-bytes")
                self.assertIn("attachment", response.headers.get("Content-Disposition", ""))
            finally:
                server.shutdown()
                server.server_close()


    def test_attachment_upload_with_room_id_writes_roomstore_media(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            RoomStore(root).create_room("room-a")
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                server_url = f"http://127.0.0.1:{server.server_port}"
                upload = Request(
                    f"{server_url}/api/attachments",
                    data=json.dumps(
                        {
                            "room_id": "room-a",
                            "filename": "diagram.png",
                            "content_type": "image/png",
                            "data_base64": base64.b64encode(b"room-image").decode("ascii"),
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(upload, timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            media = payload["room_media"]
            self.assertNotIn("path", media)
            self.assertEqual(media["content_type"], "image/png")
            self.assertEqual(media["size"], len(b"room-image"))
            self.assertTrue(media["supported"])
            attached = next(
                event
                for event in RoomStore(root).read_events("room-a")
                if event["type"] == "media_attached"
            )
            self.assertEqual(attached["media"]["id"], media["id"])
            attachment = payload["attachment"]
            stored_path = (
                root
                / "attachments"
                / attachment["id"]
                / attachment["filename"]
            )
            self.assertEqual(stored_path.read_bytes(), b"room-image")


    def test_attachment_svg_is_not_served_inline(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                server_url = f"http://127.0.0.1:{server.server_port}"
                upload = Request(
                    f"{server_url}/api/attachments",
                    data=json.dumps(
                        {
                            "filename": "x.svg",
                            "content_type": "image/svg+xml",
                            "data_base64": base64.b64encode(b"<svg xmlns='http://www.w3.org/2000/svg'></svg>").decode("ascii"),
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(upload, timeout=4) as response:
                    attachment = json.loads(response.read().decode("utf-8"))["attachment"]
                with urlopen(f"{server_url}{attachment['url']}", timeout=4) as response:
                    disposition = response.headers.get("Content-Disposition", "")
                    self.assertIn("attachment", disposition)
                    self.assertNotIn("inline", disposition)
                    self.assertEqual(response.headers.get("Content-Security-Policy"), "default-src 'none'; sandbox")
            finally:
                server.shutdown()
                server.server_close()


    def test_lobby_post_preserves_attachment_metadata_without_raw_payload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                server_url = f"http://127.0.0.1:{server.server_port}"
                upload = Request(
                    f"{server_url}/api/attachments",
                    data=json.dumps(
                        {
                            "filename": "notes.txt",
                            "content_type": "text/plain",
                            "data_base64": base64.b64encode(b"room note").decode("ascii"),
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(upload, timeout=4) as response:
                    attachment = json.loads(response.read().decode("utf-8"))["attachment"]

                post = Request(
                    f"{server_url}/api/lobby",
                    data=json.dumps(
                        {
                            "name": "나",
                            "side": "mine",
                            "kind": "message",
                            "message": "파일 확인",
                            "attachments": [
                                {
                                    "id": attachment["id"],
                                    "filename": "../../forged.txt",
                                    "data_base64": "should-not-survive",
                                }
                            ],
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(post, timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            event = payload["event"]
            self.assertEqual(event["attachments"][0]["filename"], "notes.txt")
            self.assertEqual(event["attachments"][0]["download_url"], attachment["download_url"])
            serialized = json.dumps(event, ensure_ascii=False)
            self.assertNotIn("should-not-survive", serialized)
            self.assertNotIn("../../forged", serialized)
            persisted = read_lobby(root, limit=None)
            self.assertEqual(persisted[0]["attachments"][0]["id"], attachment["id"])


    def test_lobby_rejects_unknown_attachment_ids(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                server_url = f"http://127.0.0.1:{server.server_port}"
                request = Request(
                    f"{server_url}/api/lobby",
                    data=json.dumps(
                        {
                            "name": "나",
                            "side": "mine",
                            "kind": "message",
                            "message": "없는 파일",
                            "attachments": [{"id": "missing-attachment"}],
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as context:
                    urlopen(request, timeout=4)
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(context.exception.code, 400)
            context.exception.close()
            self.assertEqual(read_lobby(root, limit=None), [])
