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
from agentsassemble.gui_router import GuiDeps
from agentsassemble.identity_store import IdentityStore, device_auth_key
from agentsassemble.legacy.admission_projection import LiveAgentLegacyAdmissionProjection
from agentsassemble.operator_pairing import OperatorPairingService
from agentsassemble.room_admission import RoomAdmissionService
from agentsassemble.room_admission_coordinator import RoomAdmissionCoordinator
from agentsassemble.room_invite import verify_session_token
from agentsassemble.room_invite import compatibility_public_invite_runtime
from agentsassemble.room_invite_application import InviteApplicationService
from agentsassemble.room_invite_repository import MemoryInviteSessionRepository
from agentsassemble.room_session_service import RoomSessionService


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
    return GuiDeps(
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

    def test_legacy_projection_failure_does_not_rollback_agent_join_or_leave(self):
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
                path="/api/room-invite/join",
                method="POST",
                payload={
                    "invite_token": invite["join_code"],
                    "request_id": "3576aa31-2798-43e0-8737-0c3da3a806f4",
                },
                deps=deps,
            )
            session_token = str(joined.sent_json["session_token"])
            participant_id = str(joined.sent_json["agent_id"])
            left = _dispatch_room_route(
                root,
                path="/api/room-invite/leave",
                method="POST",
                payload={},
                headers={"Authorization": f"Bearer {session_token}"},
                deps=deps,
            )

        self.assertEqual(joined.sent_json["status"], "admitted")
        self.assertEqual(left.sent_json, {"status": "left", "agent_id": participant_id})
        self.assertIsNone(deps.sessions.verify(session_token))
        diagnostics = projection.diagnostics()
        self.assertEqual(diagnostics["failure_count"], 2)
        self.assertEqual(
            [failure["operation"] for failure in diagnostics["recent_failures"]],
            ["participant_joined", "participant_left"],
        )
        self.assertNotIn("secret", str(diagnostics))
        self.assertNotIn("/tmp", str(diagnostics))

    def test_room_route_split_preserves_historical_service_imports(self):
        from agentsassemble import gui_room_http

        for name in (
            "AgentSessionProcessService",
            "create_agent_session_payload",
            "room_status_payload",
            "create_room_invite",
            "room_members_payload",
            "add_channel",
        ):
            with self.subTest(name=name):
                self.assertTrue(hasattr(gui_room_http, name))

    def test_rooms_endpoint_lists_room_created_by_ensure(self):
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

            rooms = payload["rooms"]
            self.assertIn("db-room", [room["room_id"] for room in rooms])
            room = next(room for room in rooms if room["room_id"] == "db-room")
            self.assertEqual(room["label"], "DB 방")
            self.assertFalse(room["archived"])
            self.assertNotIn("owner_id", room)

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
            configure_room_users_store(root / "compatibility-identity.db")
            from agentsassemble.room_users import upsert_room

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
            state = json.loads((root / "meetings" / "new-room" / "live_state.json").read_text(encoding="utf-8"))
            self.assertEqual(response, {"status": "ready", "meeting_id": "new-room"})
            self.assertEqual(room["owner_id"], operator["user_id"])
            self.assertEqual(room["label"], "New Room")
            self.assertEqual(state["origin"], "frontend_room")


    def test_room_session_resume_endpoint_feeds_room_members_from_canonical_state(self):
        reset_room_invite_state()
        reset_room_users_state()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            resumed = _dispatch_room_route(
                root,
                path="/api/agent-sessions/resume",
                method="POST",
                payload={"room_id": "session-room", "agent_id": "agent-1", "session_id": "session-1"},
            ).sent_json
            members = _dispatch_room_route(root, path="/api/room-members?meeting_id=session-room").sent_json
            left = _dispatch_room_route(
                root,
                path="/api/room-participants/leave",
                method="POST",
                payload={"room_id": "session-room", "participant_id": "agent-1"},
            ).sent_json
            after_leave = _dispatch_room_route(root, path="/api/room-members?meeting_id=session-room").sent_json

            self.assertEqual(resumed["status"], "resumed")
            self.assertEqual(len(members["members"]), 1)
            self.assertEqual(members["members"][0]["participant_id"], "agent-1")
            self.assertEqual(members["members"][0]["source"], "agent_session")
            self.assertEqual(left["status"], "left")
            self.assertEqual(after_leave["members"], [])


    def test_agent_session_create_endpoint_feeds_room_members_from_canonical_state(self):
        reset_room_invite_state()
        reset_room_users_state()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            created = _dispatch_room_route(
                root,
                path="/api/agent-sessions",
                method="POST",
                payload={
                    "room_id": "session-room",
                    "agent_id": "agent-1",
                    "display_name": "Agent One",
                    "provider_kind": "codex_live_session",
                    "model": "gpt-5.3-codex-spark",
                    "runtime_sharing_policy": "isolated_session",
                },
            ).sent_json
            members = _dispatch_room_route(root, path="/api/room-members?meeting_id=session-room").sent_json

            self.assertEqual(created["status"], "created")
            self.assertEqual(members["members"][0]["participant_id"], "agent-1")
            self.assertEqual(members["members"][0]["source"], "agent_session")
            self.assertEqual(members["members"][0]["connection_kind"], "agent_session")
            self.assertEqual(members["members"][0]["execution_mode"], "agent_session_app_server")
            self.assertEqual(members["members"][0]["owner_id"], "operator-local")
            self.assertEqual(members["members"][0]["model_id"], "gpt-5.3-codex-spark")


    def test_room_session_export_endpoint_persists_exported_state(self):
        reset_room_invite_state()
        reset_room_users_state()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _dispatch_room_route(
                root,
                path="/api/agent-sessions/resume",
                method="POST",
                payload={"room_id": "session-room", "agent_id": "agent-1", "session_id": "session-1"},
            )

            exported = _dispatch_room_route(
                root,
                path="/api/room-participants/export",
                method="POST",
                payload={"room_id": "session-room", "participant_id": "agent-1"},
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
            _dispatch_room_route(
                root,
                path="/api/agent-sessions/resume",
                method="POST",
                payload={"room_id": "session-room", "agent_id": "agent-1", "session_id": "session-1"},
            )

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
            created = _dispatch_room_route(
                root,
                path="/api/agent-sessions",
                method="POST",
                payload={"room_id": "session-room", "agent_id": "agent-1", "provider_kind": "codex_live_session"},
                headers={"X-Host-Token": "host-secret"},
                loopback=False,
            )

            self.assertIsNone(created.sent_error)
            self.assertEqual(created.sent_json["status"], "created")


    def test_agent_session_http_resume_start_uses_process_service_runner(self):
        reset_room_invite_state()
        reset_room_users_state()
        calls: list[list[str]] = []
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with patch(
                "agentsassemble.gui_room_http._local_agent_session_command_runner",
                side_effect=lambda command: calls.append(command) or {"returncode": 0},
            ):
                resumed = _dispatch_room_route(
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
                ).sent_json

            self.assertEqual(resumed["process_status"], "resumed")
            self.assertEqual(calls[0][:2], ["codex", "exec"])
            self.assertIn("--ephemeral", calls[0])
            self.assertNotIn("--last", calls[0])


    def test_agent_session_http_turn_requires_authorized_runner(self):
        reset_room_invite_state()
        reset_room_users_state()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _dispatch_room_route(
                root,
                path="/api/agent-sessions/resume",
                method="POST",
                payload={
                    "room_id": "session-room",
                    "agent_id": "agent-1",
                    "session_id": "session-1",
                    "provider_kind": "codex_live_session",
                },
            )
            denied = _dispatch_room_route(
                root,
                path="/api/agent-sessions/turn",
                method="POST",
                payload={
                    "room_id": "session-room",
                    "agent_id": "agent-1",
                    "session_id": "session-1",
                    "instruction": "Answer.",
                },
                loopback=False,
            )

            self.assertEqual(
                denied.sent_error,
                (HTTPStatus.FORBIDDEN, "Agent Session turn requires local operator or host authorization"),
            )
            self.assertNotIn("message_final", [event["type"] for event in RoomStore(root).read_events("session-room")])


    def test_agent_session_http_turn_uses_fake_runner_and_appends_room_events(self):
        reset_room_invite_state()
        reset_room_users_state()
        packets: list[dict[str, object]] = []

        def fake_turn_command_streamer(command, prompt, timeout_seconds):
            packets.append({"command": command, "prompt": prompt, "timeout_seconds": timeout_seconds})
            yield {"type": "message_delta", "content": "Answer "}
            yield {"type": "message_final", "content": "Answer from fake runner"}

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = RoomStore(root)
            store.create_room("session-room")
            cursor = store.read_events("session-room")[-1]["id"]
            store.append_event("session-room", "message_final", actor_id="human-1", content="Question")
            _dispatch_room_route(
                root,
                path="/api/agent-sessions/resume",
                method="POST",
                payload={
                    "room_id": "session-room",
                    "agent_id": "agent-1",
                    "session_id": "session-1",
                    "provider_kind": "codex_live_session",
                },
            )

            with patch(
                "agentsassemble.gui_room_http._local_agent_session_turn_command_streamer",
                side_effect=fake_turn_command_streamer,
            ):
                turned = _dispatch_room_route(
                    root,
                    path="/api/agent-sessions/turn",
                    method="POST",
                    payload={
                        "room_id": "session-room",
                        "agent_id": "agent-1",
                        "session_id": "session-1",
                        "instruction": "Answer now.",
                        "runtime_mode": "exec_jsonl_fallback",
                    },
                ).sent_json

            self.assertEqual(turned["turn_status"], "finished")
            self.assertEqual(packets[0]["command"][-1], "-")
            self.assertIn("[Your turn]\nAnswer now.", packets[0]["prompt"])
            self.assertNotIn('"session_id"', packets[0]["prompt"])
            event_types = [event["type"] for event in RoomStore(root).read_events("session-room")]
            self.assertIn("turn_started", event_types)
            self.assertIn("message_delta", event_types)
            self.assertIn("message_final", event_types)
            self.assertIn("turn_finished", event_types)
            frames = "".join(room_sse_frames_after_cursor(root, "session-room", cursor=cursor))
            self.assertIn("event: turn_started", frames)
            self.assertIn("event: message_final", frames)
            self.assertIn("Answer from fake runner", frames)


    def test_agent_session_http_next_turn_uses_latest_room_message_and_ordered_agent(self):
        reset_room_invite_state()
        reset_room_users_state()
        packets: list[dict[str, object]] = []

        def fake_turn_command_streamer(command, prompt, timeout_seconds):
            packets.append({"command": command, "prompt": prompt, "timeout_seconds": timeout_seconds})
            yield {"type": "message_final", "content": "Ordered reply"}

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = RoomStore(root)
            store.create_room("session-room")
            first_message = store.append_event("session-room", "message_final", actor_id="human-1", content="첫 질문")
            _dispatch_room_route(
                root,
                path="/api/agent-sessions",
                method="POST",
                payload={
                    "room_id": "session-room",
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "provider_kind": "codex_live_session",
                },
            )
            _dispatch_room_route(
                root,
                path="/api/agent-sessions",
                method="POST",
                payload={
                    "room_id": "session-room",
                    "agent_id": "agent-b",
                    "display_name": "Agent B",
                    "provider_kind": "codex_live_session",
                },
            )

            with patch(
                "agentsassemble.gui_room_http._local_agent_session_turn_command_streamer",
                side_effect=fake_turn_command_streamer,
            ):
                first = _dispatch_room_route(
                    root,
                    path="/api/agent-sessions/next-turn",
                    method="POST",
                    payload={
                        "room_id": "session-room",
                        "trigger_event_id": first_message["id"],
                        "runtime_mode": "exec_jsonl_fallback",
                    },
                ).sent_json
                second_message = store.append_event("session-room", "message_final", actor_id="human-1", content="둘째 질문")
                second = _dispatch_room_route(
                    root,
                    path="/api/agent-sessions/next-turn",
                    method="POST",
                    payload={
                        "room_id": "session-room",
                        "trigger_event_id": second_message["id"],
                        "runtime_mode": "exec_jsonl_fallback",
                    },
                ).sent_json

            self.assertEqual(first["participant_id"], "agent-a")
            self.assertEqual(second["participant_id"], "agent-b")
            self.assertIn("[Your turn]\nRespond to the latest room message.", packets[0]["prompt"])
            self.assertNotIn('"session_id"', packets[0]["prompt"])
            self.assertIn("첫 질문", packets[0]["prompt"])
            event_types = [event["type"] for event in RoomStore(root).read_events("session-room")]
            self.assertIn("turn_queued", event_types)
            self.assertIn("turn_assigned", event_types)
            self.assertIn("turn_finished", event_types)


    def test_lobby_message_auto_starts_agent_session_turn_without_manual_call(self):
        reset_room_invite_state()
        reset_room_users_state()

        def fake_turn_adapter(session, packet):
            yield {"type": "message_final", "content": "자동 Agent Session 응답"}

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _dispatch_room_route(
                root,
                path="/api/agent-sessions",
                method="POST",
                payload={
                    "room_id": "session-room",
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "provider_kind": "codex_live_session",
                },
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with patch("agentsassemble.gui._local_agent_session_turn_adapter", side_effect=fake_turn_adapter):
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/lobby",
                        data=json.dumps(
                            {
                                "name": "나",
                                "side": "mine",
                                "kind": "message",
                                "message": "방 메시지에 자동으로 답해줘.",
                                "flow_meeting_id": "session-room",
                            }
                        ).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                    )
                    with urlopen(request, timeout=4) as response:
                        posted = json.loads(response.read().decode("utf-8"))

                    deadline = time.time() + 4
                    events = []
                    while time.time() < deadline:
                        events = RoomStore(root).read_events("session-room")
                        if any(event.get("content") == "자동 Agent Session 응답" for event in events):
                            break
                        time.sleep(0.02)

                self.assertEqual(posted["event"]["message"], "방 메시지에 자동으로 답해줘.")
                self.assertIn("turn_queued", [event["type"] for event in events])
                self.assertIn("turn_assigned", [event["type"] for event in events])
                self.assertTrue(any(event.get("content") == "자동 Agent Session 응답" for event in events))
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
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(content_type, "text/event-stream")
            self.assertIn("event: message_final", frame)
            self.assertIn('"content": "hello"', frame)


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
            _dispatch_room_route(
                root,
                path="/api/agent-sessions/resume",
                method="POST",
                payload={
                    "room_id": "session-room",
                    "agent_id": "agent-1",
                    "session_id": "session-1",
                    "display_name": "Canonical Agent",
                    "provider_kind": "codex_live_session",
                },
            )

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
            _dispatch_room_route(
                root,
                path="/api/agent-sessions/resume",
                method="POST",
                payload={"room_id": "session-room", "agent_id": "agent-1", "session_id": "session-1"},
            )
            _dispatch_room_route(
                root,
                path="/api/room-participants/export",
                method="POST",
                payload={"room_id": "session-room", "participant_id": "agent-1"},
            )

            members = _dispatch_room_route(root, path="/api/room-members?meeting_id=session-room").sent_json["members"]

            self.assertNotIn("agent-1", [member["participant_id"] for member in members])


    def test_roomstore_roster_keeps_mute_metadata_without_changing_status(self):
        reset_room_invite_state()
        reset_room_users_state()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _dispatch_room_route(
                root,
                path="/api/agent-sessions/resume",
                method="POST",
                payload={"room_id": "session-room", "agent_id": "agent-1", "session_id": "session-1"},
            )
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
                from agentsassemble.room_users import upsert_room

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
                from agentsassemble.room_users import upsert_room

                set_runtime_host_token("host-secret")
                upsert_room(room_id="archive-room", label="Archive Room", origin="frontend_room")
                archive_handler = _dispatch_room_route(
                    root,
                    path="/api/rooms/archive",
                    method="POST",
                    payload={"room_id": "archive-room", "archived": True},
                    headers={"X-Host-Token": "host-secret"},
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
            self.assertEqual(Path(media["path"]).read_bytes(), b"room-image")
            self.assertEqual(media["content_type"], "image/png")
            self.assertEqual(media["size"], len(b"room-image"))
            self.assertTrue(media["supported"])
            self.assertIn("media_attached", [event["type"] for event in RoomStore(root).read_events("room-a")])


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
