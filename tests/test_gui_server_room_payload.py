from tests.gui_server_test_support import (
    HTTPError,
    LIVE_AGENT_ROOM_LOBBY_EVENT_LIMIT,
    Path,
    Request,
    ThreadingHTTPServer,
    _make_handler,
    append_live_event,
    append_lobby_event,
    configure_room_users_store,
    connect_live_agent_payload,
    heartbeat_live_agent,
    json,
    read_lobby,
    tempfile,
    threading,
    unittest,
    urlopen,
    write_live_state,
)


class GuiServerRoomPayloadTests(unittest.TestCase):

    def test_live_agent_engagement_endpoint_updates_policy_without_refreshing_heartbeat(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            registered = connect_live_agent_payload(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "engagement_mode": "always",
                },
            )
            original_last_seen = registered["agent"]["last_seen_at"]
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agents/agent-a/engagement",
                    data=json.dumps({"engagement_mode": "watch"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agents/agent-a/room", timeout=4) as response:
                    room = json.loads(response.read().decode("utf-8"))
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations",
                    timeout=4,
                ) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(payload["agent"]["engagement_mode"], "watch")
        self.assertEqual(payload["agent"]["last_seen_at"], original_last_seen)
        self.assertEqual(room["agent"]["engagement_mode"], "watch")
        self.assertEqual(room["agent"]["last_seen_at"], original_last_seen)
        self.assertEqual(operations["operations"][0]["operation"], "engagement.update")
        self.assertEqual(operations["operations"][0]["status"], "success")
        self.assertEqual(operations["operations"][0]["target_id"], "agent-a")
        self.assertEqual(operations["operations"][0]["details"]["previous_engagement_mode"], "always")
        self.assertEqual(operations["operations"][0]["details"]["engagement_mode"], "watch")


    def test_live_agent_engagement_endpoint_rejects_invalid_policy(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            connect_live_agent_payload(root, {"agent_id": "agent-a", "engagement_mode": "always"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agents/agent-a/engagement",
                    data=json.dumps({"engagement_mode": "shout_forever"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as context:
                    urlopen(request, timeout=4)
                context.exception.read()
                context.exception.close()
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations",
                    timeout=4,
                ) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(context.exception.code, 400)
        self.assertEqual(operations["operations"][0]["operation"], "engagement.update")
        self.assertEqual(operations["operations"][0]["status"], "failed")
        self.assertEqual(operations["operations"][0]["target_id"], "agent-a")
        self.assertIn("Unknown engagement mode", operations["operations"][0]["error"])


    def test_live_agent_room_endpoint_returns_lobby_context(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            connect_live_agent_payload(root, {"agent_id": "claude-code-live", "display_name": "Claude Code Live"})
            append_lobby_event(root, {"name": "나", "side": "mine", "message": "방 상태 보여?"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agents/claude-code-live/room",
                    timeout=4,
                ) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(payload["agent"]["display_name"], "Claude Code Live")
            self.assertEqual(payload["lobby_events"][0]["message"], "방 상태 보여?")


    def test_read_lobby_returns_room_history_buried_past_global_tail(self):
        # A room's initial load must surface ITS last events even when other
        # rooms' chatter dominates the shared log. (Regression: read_lobby used
        # to take a global tail then filter, so older rooms loaded empty.)
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            old_event = append_lobby_event(
                root,
                {"name": "상남자페이블", "side": "other-agent", "message": "옛날 방 발언", "actor_id": "fable-macho", "flow_meeting_id": "fable-room"},
                allow_flow_metadata=True,
            )
            # Bury it under 200 messages from a different room.
            for index in range(200):
                append_lobby_event(
                    root,
                    {"name": "딴방", "side": "other-agent", "message": f"chatter {index}", "actor_id": f"other-{index}", "flow_meeting_id": "other-room"},
                    allow_flow_metadata=True,
                )
            # Global tail no longer contains the old event...
            self.assertNotIn(old_event["id"], {e["id"] for e in read_lobby(root)})
            # ...but a room-scoped read still finds it.
            scoped = read_lobby(root, meeting_id="fable-room")
            self.assertIn(old_event["id"], {e["id"] for e in scoped})
            self.assertTrue(all(e.get("flow_meeting_id") == "fable-room" for e in scoped))


    def test_room_directory_reconciliation_removes_identity_only_ghosts(self):
        from agentsassemble.application.gui_factory import (
            reconcile_canonical_room_directory,
        )
        from agentsassemble.persistence.local.identity.repository import IdentityStore
        from agentsassemble.persistence.local.room.repository import RoomStore

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            identities = IdentityStore(root / "identity.db")
            rooms = RoomStore(root)
            self.addCleanup(rooms.close)
            identities.upsert_room(room_id="ghost-room", label="Ghost")
            rooms.create_room("canonical-room", label="Canonical")

            reconcile_canonical_room_directory(identities, rooms)

            self.assertIsNone(identities.get_room("ghost-room"))
            projected = identities.get_room("canonical-room")
            self.assertIsNotNone(projected)
            self.assertEqual(
                projected["room_uid"],
                rooms.room("canonical-room")["room_uid"],
            )


    def test_live_agent_room_endpoint_keeps_probe_sized_lobby_tail(self):
        expected_room_tail_limit = LIVE_AGENT_ROOM_LOBBY_EVENT_LIMIT
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            connect_live_agent_payload(root, {"agent_id": "agent-a", "display_name": "Agent A"})
            buried_event = append_lobby_event(root, {"name": "나", "side": "mine", "message": "probe buried by busy room"})
            for index in range(expected_room_tail_limit - 1):
                append_lobby_event(
                    root,
                    {
                        "name": "Busy Agent",
                        "side": "other-agent",
                        "message": f"busy chatter {index}",
                        "actor_id": f"busy-{index}",
                    },
                )
            self.assertNotIn(buried_event["id"], {event["id"] for event in read_lobby(root)})

            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agents/agent-a/room", timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(len(payload["lobby_events"]), expected_room_tail_limit)
            self.assertEqual(payload["lobby_events"][0]["id"], buried_event["id"])


    def test_live_agent_room_endpoint_returns_meeting_live_events_for_agent_meeting(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(meeting_dir, {"meeting_id": "m1", "topic": "runtime", "live_status": "running"})
            request_event = append_live_event(
                meeting_dir,
                {
                    "kind": "live_agent_turn_request",
                    "meeting_id": "m1",
                    "target_agent_id": "agent-a",
                    "role_id": "architect",
                    "content": "공식 의견",
                },
            )
            connect_live_agent_payload(root, {"agent_id": "agent-a", "display_name": "Agent A", "meeting_id": "m1"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agents/agent-a/room", timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(payload["meeting_id"], "m1")
            self.assertEqual(payload["live_events"][0]["id"], request_event["id"])
            self.assertEqual(payload["live_events"][0]["target_agent_id"], "agent-a")
            self.assertEqual(payload["live_events"][0]["official_record"], False)


    def test_live_agent_room_endpoint_projects_return_packet_after_event_tail_ages_out(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            (meeting_dir / "meeting.json").write_text(
                json.dumps(
                    {
                        "meeting_id": "m1",
                        "topic": "runtime",
                        "live_status": "complete",
                        "roles": [
                            {"id": "architect", "display_name": "Architect"},
                            {"id": "critic", "display_name": "Critic"},
                        ],
                        "agent_bindings": [
                            {"role_id": "architect", "agent_id": "agent-a"},
                            {"role_id": "critic", "agent_id": "agent-b"},
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            packet_dir = meeting_dir / "return_packets"
            packet_dir.mkdir()
            (packet_dir / "architect.md").write_text("Architect packet body must stay out of the room event.", encoding="utf-8")
            (packet_dir / "architect.json").write_text(json.dumps({"role_id": "architect"}), encoding="utf-8")
            (packet_dir / "critic.md").write_text("Critic packet body must stay private to agent-b.", encoding="utf-8")
            (packet_dir / "critic.json").write_text(json.dumps({"role_id": "critic"}), encoding="utf-8")
            original_packet_event = append_live_event(
                meeting_dir,
                {
                    "kind": "artifact",
                    "artifact_kind": "return_packet",
                    "meeting_id": "m1",
                    "target_agent_id": "agent-a",
                    "audience": "agent:agent-a",
                    "role_id": "architect",
                    "display_name": "Architect",
                    "artifact_path": "return_packets/architect.md",
                    "artifact_json_path": "return_packets/architect.json",
                    "content": "Return packet ready: return_packets/architect.md",
                },
            )
            for index in range(205):
                tail_event = append_live_event(
                    meeting_dir,
                    {
                        "kind": "status",
                        "meeting_id": "m1",
                        "content": f"tail filler {index}",
                    },
                )
            connect_live_agent_payload(root, {"agent_id": "agent-a", "display_name": "Agent A", "meeting_id": "m1"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agents/agent-a/room", timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            return_packet_events = [
                event
                for event in payload["live_events"]
                if event.get("kind") == "artifact" and event.get("artifact_kind") == "return_packet"
            ]
            self.assertEqual(len(return_packet_events), 1)
            packet_event = return_packet_events[0]
            self.assertEqual(packet_event["id"], original_packet_event["id"])
            self.assertEqual(packet_event["target_agent_id"], "agent-a")
            self.assertEqual(packet_event["audience"], "agent:agent-a")
            self.assertEqual(packet_event["role_id"], "architect")
            self.assertEqual(packet_event["artifact_path"], "return_packets/architect.md")
            self.assertEqual(packet_event["artifact_json_path"], "return_packets/architect.json")
            self.assertEqual(packet_event["official_record"], False)
            payload_text = json.dumps(payload, ensure_ascii=False)
            self.assertNotIn("Architect packet body must stay out", payload_text)
            self.assertNotIn("Critic packet body", payload_text)
            self.assertNotIn("return_packets/critic", payload_text)

            heartbeat_live_agent(root, "agent-a", metadata={"last_observed_live_event_id": original_packet_event["id"]})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agents/agent-a/room", timeout=4) as response:
                    acknowledged_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()
            self.assertFalse(
                [
                    event
                    for event in acknowledged_payload["live_events"]
                    if event.get("kind") == "artifact" and event.get("artifact_kind") == "return_packet"
                ]
            )

            heartbeat_live_agent(root, "agent-a", metadata={"last_observed_live_event_id": tail_event["id"]})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agents/agent-a/room", timeout=4) as response:
                    later_cursor_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()
            self.assertFalse(
                [
                    event
                    for event in later_cursor_payload["live_events"]
                    if event.get("kind") == "artifact" and event.get("artifact_kind") == "return_packet"
                ]
            )


    def test_live_agent_room_endpoint_stops_projected_return_packet_after_projected_ack(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            (meeting_dir / "meeting.json").write_text(
                json.dumps(
                    {
                        "meeting_id": "m1",
                        "topic": "runtime",
                        "live_status": "complete",
                        "roles": [{"id": "architect", "display_name": "Architect"}],
                        "agent_bindings": [{"role_id": "architect", "agent_id": "agent-a"}],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            packet_dir = meeting_dir / "return_packets"
            packet_dir.mkdir()
            (packet_dir / "architect.md").write_text("Architect packet body must stay out of the room event.", encoding="utf-8")
            (packet_dir / "architect.json").write_text(json.dumps({"role_id": "architect"}), encoding="utf-8")
            connect_live_agent_payload(root, {"agent_id": "agent-a", "display_name": "Agent A", "meeting_id": "m1"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agents/agent-a/room", timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()
            return_packet_events = [
                event
                for event in payload["live_events"]
                if event.get("kind") == "artifact" and event.get("artifact_kind") == "return_packet"
            ]
            self.assertEqual(len(return_packet_events), 1)
            projected_id = return_packet_events[0]["id"]

            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(
                    (
                        f"http://127.0.0.1:{server.server_port}"
                        f"/api/live-agents/agent-a/return-packet?meeting_id=m1&source_event_id={projected_id}"
                    ),
                    timeout=4,
                ) as response:
                    projected_packet_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()
            self.assertEqual(projected_packet_payload["source_event_id"], projected_id)
            self.assertEqual(projected_packet_payload["artifact_path"], "return_packets/architect.md")
            self.assertEqual(projected_packet_payload["markdown"], "Architect packet body must stay out of the room event.")

            heartbeat_live_agent(root, "agent-a", metadata={"last_observed_live_event_id": projected_id})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agents/agent-a/room", timeout=4) as response:
                    acknowledged_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()
            self.assertFalse(
                [
                    event
                    for event in acknowledged_payload["live_events"]
                    if event.get("kind") == "artifact" and event.get("artifact_kind") == "return_packet"
                ]
            )

            append_live_event(
                meeting_dir,
                {
                    "kind": "artifact",
                    "artifact_kind": "return_packet",
                    "meeting_id": "m1",
                    "target_agent_id": "agent-a",
                    "audience": "agent:agent-a",
                    "role_id": "architect",
                    "display_name": "Architect",
                    "artifact_path": "return_packets/architect.md",
                    "artifact_json_path": "return_packets/architect.json",
                    "content": "Return packet ready: return_packets/architect.md",
                },
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agents/agent-a/room", timeout=4) as response:
                    late_original_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()
            self.assertFalse(
                [
                    event
                    for event in late_original_payload["live_events"]
                    if event.get("kind") == "artifact" and event.get("artifact_kind") == "return_packet"
                ]
            )


    def test_live_agent_return_packet_endpoint_reads_only_targeted_agent_packet(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            (meeting_dir / "meeting.json").write_text(
                json.dumps(
                    {
                        "meeting_id": "m1",
                        "topic": "runtime",
                        "live_status": "complete",
                        "roles": [
                            {"id": "architect", "display_name": "Architect"},
                            {"id": "critic", "display_name": "Critic"},
                        ],
                        "agent_bindings": [
                            {"role_id": "architect", "agent_id": "agent-a"},
                            {"role_id": "critic", "agent_id": "agent-b"},
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            packet_dir = meeting_dir / "return_packets"
            packet_dir.mkdir()
            (packet_dir / "architect.md").write_text("Architect private return packet.", encoding="utf-8")
            (packet_dir / "architect.json").write_text(json.dumps({"role_id": "architect"}), encoding="utf-8")
            (packet_dir / "critic.md").write_text("Critic packet must stay private.", encoding="utf-8")
            (packet_dir / "critic.json").write_text(json.dumps({"role_id": "critic"}), encoding="utf-8")
            packet_event = append_live_event(
                meeting_dir,
                {
                    "kind": "artifact",
                    "artifact_kind": "return_packet",
                    "meeting_id": "m1",
                    "target_agent_id": "agent-a",
                    "audience": "agent:agent-a",
                    "role_id": "architect",
                    "display_name": "Architect",
                    "artifact_path": "return_packets/architect.md",
                    "artifact_json_path": "return_packets/architect.json",
                    "content": "Return packet ready: return_packets/architect.md",
                },
            )
            connect_live_agent_payload(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "meeting_id": "m1",
                },
            )
            connect_live_agent_payload(
                root,
                {
                    "agent_id": "agent-b",
                    "display_name": "Agent B",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "meeting_id": "m1",
                },
            )

            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(
                    (
                        f"http://127.0.0.1:{server.server_port}"
                        f"/api/live-agents/agent-a/return-packet?meeting_id=m1&source_event_id={packet_event['id']}"
                    ),
                    timeout=4,
                ) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                with self.assertRaises(HTTPError) as error_context:
                    urlopen(
                        (
                            f"http://127.0.0.1:{server.server_port}"
                            f"/api/live-agents/agent-b/return-packet?meeting_id=m1&source_event_id={packet_event['id']}"
                        ),
                        timeout=4,
                    )
                error_body = error_context.exception.read().decode("utf-8")
                error_context.exception.close()
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(payload["status"], "ok")
            self.assertEqual(payload["agent_id"], "agent-a")
            self.assertEqual(payload["meeting_id"], "m1")
            self.assertEqual(payload["source_event_id"], packet_event["id"])
            self.assertEqual(payload["artifact_path"], "return_packets/architect.md")
            self.assertEqual(payload["artifact_json_path"], "return_packets/architect.json")
            self.assertEqual(payload["markdown"], "Architect private return packet.")
            self.assertEqual(payload["json"]["role_id"], "architect")
            payload_text = json.dumps(payload, ensure_ascii=False)
            self.assertNotIn("Critic packet", payload_text)
            self.assertNotIn("return_packets/critic", payload_text)
            self.assertEqual(error_context.exception.code, 404)
            self.assertNotIn("Architect private return packet", error_body)
            self.assertNotIn("Critic packet", error_body)


    def test_live_agent_return_packet_endpoint_rejects_cross_meeting_agent_id_reuse(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            (meeting_dir / "meeting.json").write_text(
                json.dumps(
                    {
                        "meeting_id": "m1",
                        "topic": "runtime",
                        "roles": [{"id": "architect", "display_name": "Architect"}],
                        "agent_bindings": [{"role_id": "architect", "agent_id": "agent-a"}],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            packet_dir = meeting_dir / "return_packets"
            packet_dir.mkdir()
            (packet_dir / "architect.md").write_text("Old meeting packet must not cross sessions.", encoding="utf-8")
            (packet_dir / "architect.json").write_text(json.dumps({"role_id": "architect"}), encoding="utf-8")
            packet_event = append_live_event(
                meeting_dir,
                {
                    "kind": "artifact",
                    "artifact_kind": "return_packet",
                    "meeting_id": "m1",
                    "target_agent_id": "agent-a",
                    "audience": "agent:agent-a",
                    "role_id": "architect",
                    "artifact_path": "return_packets/architect.md",
                    "artifact_json_path": "return_packets/architect.json",
                },
            )
            connect_live_agent_payload(root, {"agent_id": "agent-a", "display_name": "Agent A", "meeting_id": "m2"})

            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with self.assertRaises(HTTPError) as error_context:
                    urlopen(
                        (
                            f"http://127.0.0.1:{server.server_port}"
                            f"/api/live-agents/agent-a/return-packet?meeting_id=m1&source_event_id={packet_event['id']}"
                        ),
                        timeout=4,
                    )
                error_body = error_context.exception.read().decode("utf-8")
                error_context.exception.close()
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(error_context.exception.code, 404)
            self.assertNotIn("Old meeting packet", error_body)


    def test_live_agent_room_endpoint_does_not_project_legacy_packet_after_known_live_cursor(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            (meeting_dir / "meeting.json").write_text(
                json.dumps(
                    {
                        "meeting_id": "m1",
                        "topic": "runtime",
                        "live_status": "complete",
                        "roles": [{"id": "architect", "display_name": "Architect"}],
                        "agent_bindings": [{"role_id": "architect", "agent_id": "agent-a"}],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            packet_dir = meeting_dir / "return_packets"
            packet_dir.mkdir()
            (packet_dir / "architect.md").write_text("Architect packet body must stay out of the room event.", encoding="utf-8")
            (packet_dir / "architect.json").write_text(json.dumps({"role_id": "architect"}), encoding="utf-8")
            later_event = append_live_event(
                meeting_dir,
                {
                    "kind": "status",
                    "meeting_id": "m1",
                    "content": "agent has observed this later event",
                },
            )
            connect_live_agent_payload(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "meeting_id": "m1",
                    "last_observed_live_event_id": later_event["id"],
                },
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agents/agent-a/room", timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertFalse(
                [
                    event
                    for event in payload["live_events"]
                    if event.get("kind") == "artifact" and event.get("artifact_kind") == "return_packet"
                ]
            )


    def test_live_agent_room_endpoint_returns_compact_shared_memory_for_agent_meeting(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(
                meeting_dir,
                {
                    "meeting_id": "m1",
                    "topic": "runtime",
                    "live_status": "running",
                    "shared_memory": {
                        "official_event_count": 1,
                        "last_official_event_id": "stale-reply",
                        "rolling_summary": [
                            {
                                "event_id": "stale-reply",
                                "speaker": "Architect",
                                "summary": "Stale embedded resident memory.",
                            }
                        ],
                    },
                },
            )
            shared_dir = meeting_dir / "shared_memory"
            shared_dir.mkdir(parents=True)
            (shared_dir / "index.json").write_text(
                json.dumps(
                    {
                        "official_event_count": 2,
                        "last_official_event_id": "reply-2",
                        "rolling_summary": [
                            {
                                "event_id": "reply-2",
                                "speaker": "Architect",
                                "summary": "Fresh index resident memory.",
                            }
                        ],
                        "action_items": [
                            {
                                "event_id": "reply-2",
                                "speaker": "Architect",
                                "text": "Use shared memory in prompts.",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            append_live_event(
                meeting_dir,
                {
                    "kind": "live_agent_turn_request",
                    "meeting_id": "m1",
                    "target_agent_id": "agent-b",
                    "content": "private target-B instruction",
                },
            )
            connect_live_agent_payload(root, {"agent_id": "agent-a", "display_name": "Agent A", "meeting_id": "m1"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agents/agent-a/room", timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(payload["shared_memory"]["official_event_count"], 2)
            self.assertEqual(payload["shared_memory"]["last_official_event_id"], "reply-2")
            self.assertEqual(payload["shared_memory"]["rolling_summary"][0]["summary"], "Fresh index resident memory.")
            self.assertEqual(payload["shared_memory"]["action_items"][0]["text"], "Use shared memory in prompts.")
            payload_text = json.dumps(payload["shared_memory"], ensure_ascii=False)
            self.assertNotIn("Stale embedded resident memory.", payload_text)
            self.assertNotIn("private target-B instruction", payload_text)


    def test_live_agent_room_endpoint_projects_fresh_shared_memory_when_index_is_stale(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(meeting_dir, {"meeting_id": "m1", "topic": "runtime", "live_status": "running"})
            shared_dir = meeting_dir / "shared_memory"
            shared_dir.mkdir(parents=True)
            (shared_dir / "index.json").write_text(
                json.dumps(
                    {
                        "official_event_count": 1,
                        "last_official_event_id": "stale-reply",
                        "rolling_summary": [
                            {
                                "event_id": "stale-reply",
                                "speaker": "Architect",
                                "summary": "Stale shared memory file.",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            original_index = (shared_dir / "index.json").read_text(encoding="utf-8")
            append_live_event(
                meeting_dir,
                {
                    "kind": "message",
                    "meeting_id": "m1",
                    "channel": "official",
                    "official_record": True,
                    "actor_id": "agent-a",
                    "display_name": "Architect",
                    "content": "Fresh room memory.\nOpen question: Is the resident context current?",
                },
            )
            append_live_event(
                meeting_dir,
                {
                    "kind": "live_agent_turn_request",
                    "meeting_id": "m1",
                    "target_agent_id": "agent-b",
                    "content": "private target-B instruction",
                },
            )
            connect_live_agent_payload(root, {"agent_id": "agent-a", "display_name": "Agent A", "meeting_id": "m1"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agents/agent-a/room", timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            payload_text = json.dumps(payload["shared_memory"], ensure_ascii=False)
            self.assertEqual(payload["shared_memory"]["rolling_summary"][0]["summary"], "Fresh room memory. Open question: Is the resident context current?")
            self.assertEqual(payload["shared_memory"]["open_questions"][0]["text"], "Is the resident context current?")
            self.assertNotIn("Stale shared memory file.", payload_text)
            self.assertNotIn("private target-B instruction", payload_text)
            self.assertEqual((shared_dir / "index.json").read_text(encoding="utf-8"), original_index)


    def test_live_agent_room_endpoint_uses_official_log_when_matching_index_contains_untrusted_text(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(meeting_dir, {"meeting_id": "m1", "topic": "runtime", "live_status": "running"})
            reply = append_live_event(
                meeting_dir,
                {
                    "kind": "message",
                    "meeting_id": "m1",
                    "channel": "official",
                    "official_record": True,
                    "actor_id": "agent-a",
                    "display_name": "Architect",
                    "content": "Endpoint official memory.\nAction: Keep room endpoint authoritative.",
                },
            )
            shared_dir = meeting_dir / "shared_memory"
            shared_dir.mkdir(parents=True)
            (shared_dir / "index.json").write_text(
                json.dumps(
                    {
                        "official_event_count": 1,
                        "last_official_event_id": reply["id"],
                        "rolling_summary": [
                            {
                                "event_id": reply["id"],
                                "speaker": "Architect",
                                "summary": "private provider output leak",
                            }
                        ],
                        "action_items": [
                            {
                                "event_id": reply["id"],
                                "speaker": "Architect",
                                "text": "private prompt leak",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            original_index = (shared_dir / "index.json").read_text(encoding="utf-8")
            connect_live_agent_payload(root, {"agent_id": "agent-a", "display_name": "Agent A", "meeting_id": "m1"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agents/agent-a/room", timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            payload_text = json.dumps(payload["shared_memory"], ensure_ascii=False)
            self.assertEqual(payload["shared_memory"]["last_official_event_id"], reply["id"])
            self.assertIn("Endpoint official memory.", payload_text)
            self.assertIn("Keep room endpoint authoritative.", payload_text)
            self.assertNotIn("private provider output leak", payload_text)
            self.assertNotIn("private prompt leak", payload_text)
            self.assertEqual((shared_dir / "index.json").read_text(encoding="utf-8"), original_index)


    def test_live_agent_room_endpoint_hides_other_agents_targeted_turn_requests(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(meeting_dir, {"meeting_id": "m1", "topic": "runtime", "live_status": "running"})
            append_live_event(
                meeting_dir,
                {
                    "kind": "live_agent_turn_request",
                    "meeting_id": "m1",
                    "target_agent_id": "agent-b",
                    "audience": "agent:agent-b",
                    "content": "private target-B instruction",
                },
            )
            append_live_event(
                meeting_dir,
                {
                    "kind": "message",
                    "meeting_id": "m1",
                    "actor_id": "agent-b",
                    "display_name": "Agent B",
                    "content": "public official statement",
                },
            )
            connect_live_agent_payload(root, {"agent_id": "agent-a", "display_name": "Agent A", "meeting_id": "m1"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agents/agent-a/room", timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            contents = [event.get("content") for event in payload["live_events"]]
            self.assertNotIn("private target-B instruction", contents)
            self.assertIn("public official statement", contents)
