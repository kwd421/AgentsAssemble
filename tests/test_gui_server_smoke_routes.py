from tests.gui_server_test_support import (
    ANY,
    HTTPError,
    LiveAgentSmokeFailed,
    Path,
    Request,
    ThreadingHTTPServer,
    _make_handler,
    _readiness_health_operation_details,
    _session_start_operation_details,
    _write_health_resident_meeting,
    _write_lobby_jsonl_event,
    append_lobby_event,
    connect_live_agent,
    json,
    patch,
    read_live_agents,
    read_live_events,
    read_lobby,
    tempfile,
    terminal_sessions_supported,
    threading,
    unittest,
    urlopen,
)


class GuiServerProcessSmokeTests(unittest.TestCase):

    def test_live_agent_smoke_endpoint_runs_credential_free_group(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            old_event = append_lobby_event(root, {"name": "나", "side": "mine", "message": "old chatter"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-smoke",
                    data=json.dumps({"group_id": "gui-smoke", "timeout": 8}).encode("utf-8"),
                    headers={"Content-Type": "application/json", "Host": "127.0.0.1:1"},
                    method="POST",
                )
                with urlopen(request, timeout=12) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=10",
                    timeout=4,
                ) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(payload["status"], "ok")
            self.assertEqual(payload["group_id"], "gui-smoke")
            self.assertNotEqual(payload["source_event_id"], old_event["id"])
            self.assertEqual({reply["source_event_id"] for reply in payload["replies"]}, {payload["source_event_id"]})
            self.assertEqual(
                {reply["message"] for reply in payload["replies"]},
                {"smoke local_cli ok", "smoke live_session ok", "smoke remote_bridge ok"},
            )
            processes = json.loads((root / "live-agent-runs" / "processes.json").read_text(encoding="utf-8"))
            group = next(item for item in processes["groups"] if item["group_id"] == "gui-smoke")
            self.assertEqual(group["status"], "stopped")
            self.assertIn(("smoke.run", "success", "gui-smoke"), [
                (operation["operation"], operation["status"], operation["target_id"])
                for operation in operations["operations"]
            ])
            persisted_text = json.dumps({"processes": processes, "operations": operations}, ensure_ascii=False)
            self.assertNotIn("agentsassemble-smoke-token", persisted_text)
            self.assertNotIn("auth_ref", persisted_text)


    def test_live_agent_session_smoke_endpoint_records_safe_operation(self):
        smoke_result = {
            "status": "ok",
            "meeting_id": "session-smoke-meeting",
            "group_id": "session-smoke",
            "agent_ids": [
                "session-smoke-local-cli",
                "session-smoke-live-session",
                "session-smoke-remote-bridge",
                "session-smoke-self-service",
            ],
            "source_event_id": "probe-secret",
            "rounds_status": "answered",
            "round_count": 1,
            "answered_round_count": 1,
            "completed_round_count": 0,
            "timeout_round_count": 0,
            "skipped_round_count": 0,
            "expected_reply_count": 4,
            "self_service_official_reply_count": 1,
            "self_service_lobby_reply_count": 2,
            "self_service_post_restart_reply_count": 2,
            "self_service_post_recover_reply_count": 2,
            "self_service_soak_reply_count": 2,
            "lobby_probe_count": 2,
            "source_event_ids": ["probe-secret", "probe-secret-2"],
            "reply_count": 8,
            "post_restart_source_event_id": "post-restart-secret",
            "post_restart_source_event_ids": ["post-restart-secret", "post-restart-secret-2"],
            "post_restart_reply_count": 8,
            "post_recover_source_event_id": "post-recover-secret",
            "post_recover_source_event_ids": ["post-recover-secret", "post-recover-secret-2"],
            "post_recover_reply_count": 8,
            "soak_cycle_count": 2,
            "soak_interval_seconds": 0.5,
            "soak_check_statuses": ["ready", "ready"],
            "soak_source_event_ids": ["soak-secret", "soak-secret-2"],
            "soak_reply_count": 8,
            "soak_replies": [
                {"id": "reply-soak-local", "actor_id": "session-smoke-local-cli", "source_event_id": "soak-secret"},
                {"id": "reply-soak-session", "actor_id": "session-smoke-live-session", "source_event_id": "soak-secret"},
                {"id": "reply-soak-bridge", "actor_id": "session-smoke-remote-bridge", "source_event_id": "soak-secret"},
                {"id": "reply-soak-self-service", "actor_id": "session-smoke-self-service", "source_event_id": "soak-secret"},
            ],
            "replies": [
                {"id": "reply-local", "actor_id": "session-smoke-local-cli", "source_event_id": "probe-secret"},
                {"id": "reply-session", "actor_id": "session-smoke-live-session", "source_event_id": "probe-secret"},
                {"id": "reply-bridge", "actor_id": "session-smoke-remote-bridge", "source_event_id": "probe-secret"},
                {"id": "reply-self-service", "actor_id": "session-smoke-self-service", "source_event_id": "probe-secret"},
            ],
            "post_restart_replies": [
                {
                    "id": "reply-post-local",
                    "actor_id": "session-smoke-local-cli",
                    "source_event_id": "post-restart-secret",
                },
                {
                    "id": "reply-post-session",
                    "actor_id": "session-smoke-live-session",
                    "source_event_id": "post-restart-secret",
                },
                {
                    "id": "reply-post-bridge",
                    "actor_id": "session-smoke-remote-bridge",
                    "source_event_id": "post-restart-secret",
                },
                {
                    "id": "reply-post-self-service",
                    "actor_id": "session-smoke-self-service",
                    "source_event_id": "post-restart-secret",
                },
            ],
            "post_recover_replies": [
                {
                    "id": "reply-recover-local",
                    "actor_id": "session-smoke-local-cli",
                    "source_event_id": "post-recover-secret",
                },
                {
                    "id": "reply-recover-session",
                    "actor_id": "session-smoke-live-session",
                    "source_event_id": "post-recover-secret",
                },
                {
                    "id": "reply-recover-bridge",
                    "actor_id": "session-smoke-remote-bridge",
                    "source_event_id": "post-recover-secret",
                },
                {
                    "id": "reply-recover-self-service",
                    "actor_id": "session-smoke-self-service",
                    "source_event_id": "post-recover-secret",
                },
            ],
            "start_status": "ready",
            "check_status": "ready",
            "resume_status": "ready",
            "restart_status": "ready",
            "recover_status": "ready",
            "stop_status": "stopped",
            "post_stop_process_status": "stopped",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with patch("agentsassemble.gui.run_live_agent_session_smoke", return_value=smoke_result) as session_smoke:
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agent-session-smoke",
                        data=json.dumps(
                            {
                                "group_id": "session-smoke",
                                "meeting_id": "session-smoke-meeting",
                                "timeout": 8,
                                "lobby_probe_count": 2,
                                "soak_cycle_count": 2,
                                "soak_interval_seconds": 0.5,
                            }
                        ).encode("utf-8"),
                        headers={"Content-Type": "application/json", "Host": "127.0.0.1:1"},
                        method="POST",
                    )
                    with urlopen(request, timeout=12) as response:
                        payload = json.loads(response.read().decode("utf-8"))
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20",
                    timeout=4,
                ) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(payload["status"], "ok")
        session_smoke.assert_called_once_with(
            server=f"http://127.0.0.1:{server.server_port}",
            group_id="session-smoke",
            meeting_id="session-smoke-meeting",
            timeout_seconds=8.0,
            lobby_probe_count=2,
            soak_cycle_count=2,
            soak_interval_seconds=0.5,
            request_json=ANY,
            output_root=root,
        )
        session_operations = [operation for operation in operations["operations"] if operation["operation"] == "session.smoke"]
        self.assertEqual(session_operations[-1]["status"], "success")
        self.assertEqual(session_operations[-1]["target_id"], "session-smoke")
        self.assertEqual(session_operations[-1]["details"]["meeting_id"], "session-smoke-meeting")
        self.assertEqual(session_operations[-1]["details"]["rounds_status"], "answered")
        self.assertEqual(session_operations[-1]["details"]["answered_round_count"], 1)
        self.assertEqual(session_operations[-1]["details"]["lobby_probe_count"], 2)
        self.assertEqual(session_operations[-1]["details"]["self_service_official_reply_count"], 1)
        self.assertEqual(session_operations[-1]["details"]["self_service_lobby_reply_count"], 2)
        self.assertEqual(session_operations[-1]["details"]["self_service_post_restart_reply_count"], 2)
        self.assertEqual(session_operations[-1]["details"]["self_service_post_recover_reply_count"], 2)
        self.assertEqual(session_operations[-1]["details"]["self_service_soak_reply_count"], 2)
        self.assertEqual(session_operations[-1]["details"]["reply_count"], 8)
        self.assertEqual(session_operations[-1]["details"]["post_restart_reply_count"], 8)
        self.assertEqual(session_operations[-1]["details"]["post_recover_reply_count"], 8)
        self.assertEqual(session_operations[-1]["details"]["soak_cycle_count"], 2)
        self.assertEqual(session_operations[-1]["details"]["soak_reply_count"], 8)
        self.assertEqual(session_operations[-1]["details"]["soak_check_statuses"], ["ready", "ready"])
        self.assertEqual(session_operations[-1]["details"]["resume_status"], "ready")
        self.assertEqual(session_operations[-1]["details"]["recover_status"], "ready")
        self.assertEqual(session_operations[-1]["details"]["post_stop_process_status"], "stopped")
        operation_blob = json.dumps(session_operations, ensure_ascii=False)
        self.assertNotIn("probe-secret", operation_blob)
        self.assertNotIn("probe-secret-2", operation_blob)
        self.assertNotIn("post-restart-secret", operation_blob)
        self.assertNotIn("post-restart-secret-2", operation_blob)
        self.assertNotIn("post-recover-secret", operation_blob)
        self.assertNotIn("post-recover-secret-2", operation_blob)
        self.assertNotIn("soak-secret", operation_blob)
        self.assertNotIn("soak-secret-2", operation_blob)
        self.assertNotIn("reply-local", operation_blob)
        self.assertNotIn("reply-session", operation_blob)
        self.assertNotIn("reply-bridge", operation_blob)
        self.assertNotIn("reply-self-service", operation_blob)
        self.assertNotIn("reply-post-local", operation_blob)
        self.assertNotIn("reply-post-session", operation_blob)
        self.assertNotIn("reply-post-bridge", operation_blob)
        self.assertNotIn("reply-post-self-service", operation_blob)
        self.assertNotIn("reply-recover-local", operation_blob)
        self.assertNotIn("reply-recover-session", operation_blob)
        self.assertNotIn("reply-recover-bridge", operation_blob)
        self.assertNotIn("reply-recover-self-service", operation_blob)
        self.assertNotIn("reply-soak-local", operation_blob)
        self.assertNotIn("reply-soak-session", operation_blob)
        self.assertNotIn("reply-soak-bridge", operation_blob)
        self.assertNotIn("reply-soak-self-service", operation_blob)


    def test_live_agent_session_smoke_endpoint_records_safe_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with patch(
                    "agentsassemble.gui.run_live_agent_session_smoke",
                    side_effect=LiveAgentSmokeFailed("secret /private/live-agents.json env:SECRET_TOKEN"),
                ):
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agent-session-smoke",
                        data=json.dumps({"group_id": "session-smoke", "meeting_id": "session-smoke-meeting"}).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with self.assertRaises(HTTPError) as raised:
                        urlopen(request, timeout=12)
                    error_payload = json.loads(raised.exception.read().decode("utf-8"))
                    raised.exception.close()
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20",
                    timeout=4,
                ) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(error_payload["error"], "Session smoke could not be run.")
        session_operations = [operation for operation in operations["operations"] if operation["operation"] == "session.smoke"]
        self.assertEqual(session_operations[-1]["status"], "failed")
        operation_blob = json.dumps({"error": error_payload, "operations": session_operations}, ensure_ascii=False)
        self.assertNotIn("SECRET_TOKEN", operation_blob)
        self.assertNotIn("/private/live-agents.json", operation_blob)


    def test_live_agent_session_smoke_endpoint_rejects_negative_soak_payload_before_run(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with patch("agentsassemble.gui.run_live_agent_session_smoke") as session_smoke:
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agent-session-smoke",
                        data=json.dumps({"soak_cycle_count": -1, "soak_interval_seconds": -0.5}).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with self.assertRaises(HTTPError) as raised:
                        urlopen(request, timeout=12)
                    error_payload = json.loads(raised.exception.read().decode("utf-8"))
                    raised.exception.close()
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20",
                    timeout=4,
                ) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        session_smoke.assert_not_called()
        self.assertEqual(error_payload["error"], "Session smoke could not be run.")
        session_operations = [operation for operation in operations["operations"] if operation["operation"] == "session.smoke"]
        self.assertEqual(session_operations[-1]["status"], "failed")


    def test_live_agent_session_smoke_endpoint_runs_credential_free_session(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-session-smoke",
                    data=json.dumps({"group_id": "session-smoke-api", "meeting_id": "session-smoke-api", "timeout": 12}).encode("utf-8"),
                    headers={"Content-Type": "application/json", "Host": "127.0.0.1:1"},
                    method="POST",
                )
                with urlopen(request, timeout=60) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-health", timeout=4) as response:
                    health = json.loads(response.read().decode("utf-8"))
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=40",
                    timeout=4,
                ) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(payload["status"], "ok")
            self.assertEqual(payload["rounds_status"], "answered")
            self.assertEqual(payload["round_count"], 1)
            self.assertEqual(payload["answered_round_count"], 1)
            expected_agent_count = len(payload["agent_ids"])
            self.assertEqual(payload["finalization_status"], "finalized")
            self.assertEqual(payload["finalization_official_event_count"], expected_agent_count)
            self.assertEqual(payload["return_packet_event_count"], expected_agent_count)
            self.assertEqual(payload["artifact_status"], "present")
            self.assertEqual(
                payload["artifact_paths"],
                ["agenda.md", "transcript.md", "decision.md", "return_packets/"],
            )
            self.assertEqual(payload["terminal_session_supported"], terminal_sessions_supported())
            self.assertEqual(payload["terminal_session_included"], payload["terminal_session_supported"])
            self.assertEqual(payload["terminal_session_status"], "covered" if payload["terminal_session_included"] else "skipped")
            if payload["terminal_session_included"]:
                self.assertIn("session-smoke-api-terminal-session", payload["agent_ids"])
            else:
                self.assertEqual(payload["terminal_session_reason"], "pty_unavailable")
            self.assertEqual(payload["expected_reply_count"], expected_agent_count)
            self.assertEqual(payload["self_service_official_reply_count"], 1)
            self.assertEqual(payload["self_service_lobby_reply_count"], 1)
            self.assertEqual(payload["self_service_post_restart_reply_count"], 1)
            self.assertEqual(payload["self_service_post_recover_reply_count"], 1)
            self.assertEqual(payload["self_service_soak_reply_count"], 0)
            self.assertEqual(payload["reply_count"], expected_agent_count)
            self.assertEqual(payload["post_restart_reply_count"], expected_agent_count)
            self.assertEqual(payload["post_recover_reply_count"], expected_agent_count)
            self.assertEqual(payload["recover_status"], "ready")
            self.assertNotEqual(payload["post_restart_source_event_id"], payload["source_event_id"])
            self.assertNotEqual(payload["post_recover_source_event_id"], payload["post_restart_source_event_id"])
            self.assertEqual({reply["actor_id"] for reply in payload["replies"]}, set(payload["agent_ids"]))
            self.assertEqual({reply["actor_id"] for reply in payload["post_restart_replies"]}, set(payload["agent_ids"]))
            self.assertEqual({reply["actor_id"] for reply in payload["post_recover_replies"]}, set(payload["agent_ids"]))
            self.assertEqual({reply["source_event_id"] for reply in payload["post_restart_replies"]}, {payload["post_restart_source_event_id"]})
            self.assertEqual({reply["source_event_id"] for reply in payload["post_recover_replies"]}, {payload["post_recover_source_event_id"]})
            self.assertFalse(any("message" in reply for reply in payload["replies"]))
            self.assertFalse(any("message" in reply for reply in payload["post_restart_replies"]))
            self.assertFalse(any("message" in reply for reply in payload["post_recover_replies"]))
            meeting_dir = root / "meetings" / payload["meeting_id"]
            meeting = json.loads((meeting_dir / "live_state.json").read_text(encoding="utf-8"))
            self.assertTrue(meeting["diagnostic"])
            self.assertEqual(meeting["diagnostic_kind"], "session_smoke")
            live_events = read_live_events(meeting_dir, limit=None)
            official_replies = [event for event in live_events if event.get("kind") == "message" and event.get("official_record") is True]
            self.assertEqual(len(official_replies), expected_agent_count)
            lobby_replies = [event for event in read_lobby(root) if event.get("actor_id") in payload["agent_ids"]]
            self.assertEqual(len(lobby_replies), expected_agent_count * 3)
            self.assertEqual(
                len([event for event in lobby_replies if event.get("source_event_id") == payload["source_event_id"]]),
                expected_agent_count,
            )
            self.assertEqual(
                len([event for event in lobby_replies if event.get("source_event_id") == payload["post_restart_source_event_id"]]),
                expected_agent_count,
            )
            self.assertEqual(
                len([event for event in lobby_replies if event.get("source_event_id") == payload["post_recover_source_event_id"]]),
                expected_agent_count,
            )
            agents = read_live_agents(root)
            self.assertEqual({agent["diagnostic"] for agent in agents if agent["agent_id"] in payload["agent_ids"]}, {True})
            processes = json.loads((root / "live-agent-runs" / "processes.json").read_text(encoding="utf-8"))
            group = next(item for item in processes["groups"] if item["group_id"] == "session-smoke-api")
            self.assertTrue(group["diagnostic"])
            self.assertEqual(group["status"], "stopped")
            self.assertEqual(health["status"], "ok")
            self.assertEqual(health["agents"]["total"], 0)
            self.assertEqual(health["processes"]["total"], 0)
            session_operations = [operation for operation in operations["operations"] if operation["operation"] == "session.smoke"]
            self.assertEqual(session_operations[-1]["details"]["finalization_status"], "finalized")
            self.assertEqual(session_operations[-1]["details"]["finalization_official_event_count"], expected_agent_count)
            self.assertEqual(session_operations[-1]["details"]["return_packet_event_count"], expected_agent_count)
            self.assertEqual(session_operations[-1]["details"]["artifact_status"], "present")
            operation_blob = json.dumps(operations["operations"], ensure_ascii=False)
            self.assertNotIn("session smoke local_cli ok", operation_blob)
            self.assertNotIn("session smoke live_session ok", operation_blob)
            self.assertNotIn("session smoke terminal_session ok", operation_blob)
            self.assertNotIn("session smoke remote_bridge ok", operation_blob)
            self.assertNotIn("session smoke self_service ok", operation_blob)
            self.assertNotIn("agentsassemble-smoke-token", operation_blob)
            self.assertNotIn("auth_ref", operation_blob)
            self.assertNotIn("config_path", operation_blob)
            self.assertNotIn("endpoint", operation_blob)
            self.assertNotIn("log_path", operation_blob)
            self.assertNotIn("command", operation_blob)


    def test_live_agent_official_round_smoke_endpoint_runs_credential_free_round(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-official-round-smoke",
                    data=json.dumps({"group_id": "round-smoke", "timeout": 8}).encode("utf-8"),
                    headers={"Content-Type": "application/json", "Host": "127.0.0.1:1"},
                    method="POST",
                )
                with urlopen(request, timeout=45) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20",
                    timeout=4,
                ) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(payload["status"], "ok")
            self.assertEqual(payload["group_id"], "round-smoke")
            self.assertEqual(payload["round_id"], "official_round_smoke")
            self.assertEqual(payload["turn_count"], 3)
            self.assertEqual(payload["answered_count"], 3)
            self.assertEqual(payload["timeout_count"], 0)
            self.assertEqual(payload["skipped_count"], 0)
            self.assertEqual(len(payload["request_event_ids"]), 3)
            self.assertEqual(len(payload["reply_event_ids"]), 3)
            meeting_dir = root / "meetings" / payload["meeting_id"]
            live_events = read_live_events(meeting_dir, limit=None)
            requests = [event for event in live_events if event.get("kind") == "live_agent_turn_request"]
            replies = [event for event in live_events if event.get("kind") == "message" and event.get("official_record") is True]
            self.assertEqual(len(requests), 3)
            self.assertEqual(len(replies), 3)
            self.assertEqual({reply["source_event_id"] for reply in replies}, {request["id"] for request in requests})
            self.assertEqual(read_lobby(root), [])
            processes = json.loads((root / "live-agent-runs" / "processes.json").read_text(encoding="utf-8"))
            group = next(item for item in processes["groups"] if item["group_id"] == "round-smoke")
            self.assertEqual(group["status"], "stopped")
            self.assertIn(("smoke.official_round", "success", "round-smoke"), [
                (operation["operation"], operation["status"], operation["target_id"])
                for operation in operations["operations"]
            ])
            operation_blob = json.dumps(operations["operations"], ensure_ascii=False)
            self.assertNotIn("smoke local_cli ok", operation_blob)
            self.assertNotIn("smoke live_session ok", operation_blob)
            self.assertNotIn("smoke remote_bridge ok", operation_blob)
            self.assertNotIn("agentsassemble-smoke-token", operation_blob)
            self.assertNotIn("auth_ref", operation_blob)
            self.assertNotIn("config_path", operation_blob)
            self.assertNotIn("endpoint", operation_blob)
            self.assertNotIn("log_path", operation_blob)
            self.assertNotIn("command", operation_blob)


    def test_live_agent_readiness_endpoint_uses_pre_smoke_health_and_runs_smoke(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                payloads = []
                for _ in range(2):
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agent-readiness",
                        data=json.dumps({"group_id": "doctor-smoke", "timeout": 8}).encode("utf-8"),
                        headers={"Content-Type": "application/json", "Host": "127.0.0.1:1"},
                        method="POST",
                    )
                    with urlopen(request, timeout=12) as response:
                        payloads.append(json.loads(response.read().decode("utf-8")))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-health", timeout=4) as response:
                    health_after_smoke = json.loads(response.read().decode("utf-8"))
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20",
                    timeout=4,
                ) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            for payload in payloads:
                self.assertEqual(payload["status"], "ready")
                self.assertEqual(payload["health"]["status"], "ok")
                self.assertEqual(payload["smoke"]["status"], "ok")
                self.assertEqual(payload["smoke"]["group_id"], "doctor-smoke")
                self.assertEqual(
                    {check["id"]: check["status"] for check in payload["checks"]},
                    {"health": "ok", "smoke": "ok"},
                )
            self.assertEqual(health_after_smoke["status"], "ok")
            processes = json.loads((root / "live-agent-runs" / "processes.json").read_text(encoding="utf-8"))
            group = next(item for item in processes["groups"] if item["group_id"] == "doctor-smoke")
            self.assertEqual(group["status"], "stopped")
            self.assertTrue(group["diagnostic"])
            agents = json.loads((root / "live_agents.json").read_text(encoding="utf-8"))["agents"]
            self.assertEqual({agent["diagnostic"] for agent in agents if agent["agent_id"].startswith("doctor-smoke-")}, {True})
            readiness_operations = [
                operation for operation in operations["operations"] if operation["operation"] == "readiness.check"
            ]
            self.assertEqual([operation["status"] for operation in readiness_operations], ["success", "success"])
            self.assertEqual({operation["target_id"] for operation in readiness_operations}, {"doctor-smoke"})


    def test_live_agent_readiness_endpoint_records_degraded_operation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            (root / "live_agents.json").write_text(
                json.dumps({"agents": [{"agent_id": "offline-agent", "status": "offline"}]}),
                encoding="utf-8",
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-readiness",
                    data=json.dumps({"group_id": "doctor-smoke", "timeout": 8}).encode("utf-8"),
                    headers={"Content-Type": "application/json", "Host": "127.0.0.1:1"},
                    method="POST",
                )
                with urlopen(request, timeout=12) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20",
                    timeout=4,
                ) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(payload["status"], "degraded")
            readiness_operations = [
                operation for operation in operations["operations"] if operation["operation"] == "readiness.check"
            ]
            self.assertEqual(readiness_operations[-1]["status"], "degraded")
            self.assertEqual(readiness_operations[-1]["details"]["result_status"], "degraded")


    def test_live_agent_readiness_operation_records_safe_health_attention(self):
        class FakeSupervisor:
            def snapshot_groups(self):
                return [
                    {
                        "group_id": "orphan-group",
                        "status": "unknown",
                        "recent_events": [{"event_type": "recovered_unknown", "status": "unknown"}],
                    }
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=FakeSupervisor()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with patch(
                    "agentsassemble.gui.run_live_agent_smoke",
                    return_value={"status": "ok", "group_id": "doctor-smoke", "replies": []},
                ):
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agent-readiness",
                        data=json.dumps({"group_id": "doctor-smoke", "timeout": 8}).encode("utf-8"),
                        headers={"Content-Type": "application/json", "Host": "127.0.0.1:1"},
                        method="POST",
                    )
                    with urlopen(request, timeout=12) as response:
                        payload = json.loads(response.read().decode("utf-8"))
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20",
                    timeout=4,
                ) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(payload["status"], "degraded")
        readiness_operations = [
            operation for operation in operations["operations"] if operation["operation"] == "readiness.check"
        ]
        details = readiness_operations[-1]["details"]
        self.assertEqual(details["health_status"], "degraded")
        self.assertEqual(details["health_process_attention"], ["orphan-group"])
        self.assertEqual(
            details["health_process_reasons"],
            ["orphan-group recovered_unknown orphan running record marked unknown"],
        )
        details_blob = json.dumps(details, ensure_ascii=False)
        self.assertNotIn("recent_events", details_blob)
        self.assertNotIn("live-agents.json", details_blob)


    def test_live_agent_readiness_operation_omits_suspicious_health_attention(self):
        class FakeSupervisor:
            def snapshot_groups(self):
                return [
                    {
                        "group_id": "/private/live-agents.json",
                        "status": "unknown",
                        "recent_events": [{"event_type": "recovered_unknown", "status": "unknown"}],
                    }
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            (root / "live_agents.json").write_text(
                json.dumps({"agents": [{"agent_id": "env:SECRET_TOKEN", "status": "offline"}]}),
                encoding="utf-8",
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=FakeSupervisor()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with patch(
                    "agentsassemble.gui.run_live_agent_smoke",
                    return_value={"status": "ok", "group_id": "doctor-smoke", "replies": []},
                ):
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agent-readiness",
                        data=json.dumps({"group_id": "doctor-smoke", "timeout": 8}).encode("utf-8"),
                        headers={"Content-Type": "application/json", "Host": "127.0.0.1:1"},
                        method="POST",
                    )
                    with urlopen(request, timeout=12) as response:
                        json.loads(response.read().decode("utf-8"))
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20",
                    timeout=4,
                ) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        readiness_operations = [
            operation for operation in operations["operations"] if operation["operation"] == "readiness.check"
        ]
        details_blob = json.dumps(readiness_operations[-1]["details"], ensure_ascii=False)
        self.assertNotIn("live-agents.json", details_blob)
        self.assertNotIn("SECRET_TOKEN", details_blob)
        self.assertNotIn("/private", details_blob)
        self.assertNotIn("env:", details_blob)


    def test_readiness_health_operation_details_filters_sensitive_values(self):
        details = _readiness_health_operation_details(
            {
                "status": "degraded",
                "processes": {
                    "attention": ["orphan-group", "/private/live-agents.json", "env:SECRET_TOKEN"],
                    "reasons": {
                        "orphan-group": {
                            "event_type": "recovered_unknown",
                            "reason": "orphan running record marked unknown",
                        },
                        "/private/live-agents.json": {
                            "event_type": "recovered_unknown",
                            "reason": "env:SECRET_TOKEN",
                        },
                    },
                },
            }
        )

        self.assertEqual(details["health_status"], "degraded")
        self.assertEqual(details["health_process_attention"], ["orphan-group"])
        self.assertEqual(
            details["health_process_reasons"],
            ["orphan-group recovered_unknown orphan running record marked unknown"],
        )
        details_blob = json.dumps(details, ensure_ascii=False)
        self.assertNotIn("live-agents.json", details_blob)
        self.assertNotIn("SECRET_TOKEN", details_blob)
        self.assertNotIn("/private", details_blob)
        self.assertNotIn("env:", details_blob)


    def test_readiness_health_operation_details_preserves_long_session_causes(self):
        details = _readiness_health_operation_details(
            {
                "status": "degraded",
                "observations": {
                    "lobby_behind_count": 1,
                    "live_behind_count": 2,
                    "error_count": 3,
                    "attention": [
                        "resident-m1:resident-main:agent-a:lobby_cursor_behind",
                        "env:SECRET_TOKEN",
                    ],
                },
                "shared_memory": {
                    "ready_sessions": 2,
                    "with_memory": 1,
                    "attention": [
                        "resident-m1:resident-main:memory_unavailable",
                        "/private/shared-memory.json",
                    ],
                },
                "session_runs": {
                    "active": 1,
                    "retrying": 1,
                    "attention": [
                        "resident-m1:resident-main:run-a:ready:no_current_readiness",
                        "resident-m1:/private/live-agents.json:run-b:degraded:retrying",
                    ],
                },
                "session_run_monitor": {
                    "last_result_count": 1,
                    "attention": ["failed:RuntimeError", "failed:/private/monitor.json"],
                },
            }
        )

        self.assertEqual(details["health_status"], "degraded")
        self.assertEqual(
            details["health_observation_attention"],
            ["resident-m1:resident-main:agent-a:lobby_cursor_behind"],
        )
        self.assertEqual(details["health_observation_lobby_behind_count"], 1)
        self.assertEqual(details["health_observation_live_behind_count"], 2)
        self.assertEqual(details["health_observation_error_count"], 3)
        self.assertEqual(details["health_shared_memory_attention"], ["resident-m1:resident-main:memory_unavailable"])
        self.assertEqual(details["health_shared_memory_ready_sessions"], 2)
        self.assertEqual(details["health_shared_memory_with_memory"], 1)
        self.assertEqual(
            details["health_session_run_attention"],
            ["resident-m1:resident-main:run-a:ready:no_current_readiness"],
        )
        self.assertEqual(details["health_session_run_active"], 1)
        self.assertEqual(details["health_session_run_retrying"], 1)
        self.assertEqual(details["health_session_run_monitor_attention"], ["failed:RuntimeError"])
        self.assertEqual(details["health_session_run_monitor_last_result_count"], 1)
        details_blob = json.dumps(details, ensure_ascii=False)
        self.assertNotIn("SECRET_TOKEN", details_blob)
        self.assertNotIn("/private", details_blob)
        self.assertNotIn("shared-memory.json", details_blob)
        self.assertNotIn("env:", details_blob)


    def test_live_agent_readiness_operation_records_observation_health_cause(self):
        class FakeSupervisor:
            def snapshot_groups(self):
                return [
                    {
                        "group_id": "resident-main",
                        "status": "running",
                        "meeting_id": "resident-m1",
                        "agents": [{"agent_id": "agent-a"}],
                    }
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            _write_health_resident_meeting(root, agent_ids=["agent-a"])
            _write_lobby_jsonl_event(
                root,
                event_id="latest-lobby-event",
                actor_id="human",
                created_at="2026-05-21T10:00:00+00:00",
            )
            connect_live_agent(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "meeting_id": "resident-m1",
                    "last_observed_event_id": "older-lobby-event",
                },
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=FakeSupervisor()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with patch(
                    "agentsassemble.gui.run_live_agent_smoke",
                    return_value={"status": "ok", "group_id": "doctor-smoke", "replies": []},
                ):
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agent-readiness",
                        data=json.dumps({"group_id": "doctor-smoke", "timeout": 8}).encode("utf-8"),
                        headers={"Content-Type": "application/json", "Host": "127.0.0.1:1"},
                        method="POST",
                    )
                    with urlopen(request, timeout=12) as response:
                        payload = json.loads(response.read().decode("utf-8"))
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20",
                    timeout=4,
                ) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(payload["status"], "degraded")
        readiness_operations = [
            operation for operation in operations["operations"] if operation["operation"] == "readiness.check"
        ]
        details = readiness_operations[-1]["details"]
        self.assertEqual(details["health_status"], "degraded")
        self.assertEqual(
            details["health_observation_attention"],
            ["resident-m1:resident-main:agent-a:lobby_cursor_behind"],
        )
        self.assertEqual(details["health_observation_lobby_behind_count"], 1)
        details_blob = json.dumps(details, ensure_ascii=False)
        self.assertNotIn("stale event text", details_blob)
        self.assertNotIn("latest-lobby-event", details_blob)


    def test_session_start_operation_details_drops_unrecognized_ensure_reason(self):
        details = _session_start_operation_details(
            {
                "status": "ready",
                "meeting_id": "resident-m1",
                "group_id": "resident-main",
                "action": "restart",
                "ensure_reason": "session drift from old-session to /private/new-session env:SECRET_TOKEN",
                "connection": {"expected": 1, "connected": 1},
                "process": {"status": "running"},
            }
        )

        self.assertEqual(details["ensure_action"], "restart")
        self.assertNotIn("ensure_reason", details)
        details_blob = json.dumps(details, ensure_ascii=False)
        self.assertNotIn("old-session", details_blob)
        self.assertNotIn("new-session", details_blob)
        self.assertNotIn("SECRET_TOKEN", details_blob)
        self.assertNotIn("/private", details_blob)
        self.assertNotIn("env:", details_blob)


    def test_live_agent_readiness_endpoint_runs_opt_in_targeted_probes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            smoke_result = {"status": "ok", "group_id": "doctor-smoke", "source_event_id": "base-secret", "replies": []}
            probe_results = [
                {
                    "status": "ok",
                    "agent_id": "agent-a",
                    "source_event_id": "probe-source-a",
                    "reply_event_id": "reply-a",
                    "reply": {"message": "secret readiness reply"},
                },
                {
                    "status": "timeout",
                    "agent_id": "agent-b",
                    "source_event_id": "probe-source-b",
                },
                ValueError("Live agent agent-missing was not found at /Users/me/private.json env:SECRET_TOKEN."),
            ]
            try:
                with (
                    patch("agentsassemble.gui.run_live_agent_smoke", return_value=smoke_result),
                    patch("agentsassemble.gui.run_live_agent_probe", side_effect=probe_results) as probe,
                ):
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agent-readiness",
                        data=json.dumps(
                            {
                                "group_id": "doctor-smoke",
                                "timeout": 8,
                                "probe_agent_ids": ["agent-a", "agent-b", "agent-missing"],
                            }
                        ).encode("utf-8"),
                        headers={"Content-Type": "application/json", "Host": "127.0.0.1:1"},
                        method="POST",
                    )
                    with urlopen(request, timeout=12) as response:
                        payload = json.loads(response.read().decode("utf-8"))
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20",
                    timeout=4,
                ) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(payload["status"], "failed")
        self.assertEqual(
            {check["id"]: check["status"] for check in payload["checks"]},
            {
                "health": "ok",
                "smoke": "ok",
                "probe:agent-a": "ok",
                "probe:agent-b": "timeout",
                "probe:agent-missing": "failed",
            },
        )
        self.assertEqual(payload["probes"][0], {"status": "ok", "agent_id": "agent-a", "source_event_id": "probe-source-a", "reply_event_id": "reply-a"})
        self.assertEqual(payload["probes"][1], {"status": "timeout", "agent_id": "agent-b", "source_event_id": "probe-source-b"})
        self.assertEqual(
            payload["probes"][2],
            {"status": "failed", "agent_id": "agent-missing", "reason": "probe could not be run"},
        )
        serialized_payload = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("secret readiness reply", serialized_payload)
        self.assertNotIn("SECRET_TOKEN", serialized_payload)
        self.assertNotIn("/Users/me/private.json", serialized_payload)
        self.assertEqual([call.args[:2] for call in probe.call_args_list], [(root, "agent-a"), (root, "agent-b"), (root, "agent-missing")])
        self.assertEqual({call.kwargs["timeout_seconds"] for call in probe.call_args_list}, {8.0})
        readiness_operations = [
            operation for operation in operations["operations"] if operation["operation"] == "readiness.check"
        ]
        self.assertEqual(readiness_operations[-1]["status"], "failed")
        self.assertEqual(readiness_operations[-1]["details"]["result_status"], "failed")
        self.assertEqual(readiness_operations[-1]["details"]["probe_agent_ids"], ["agent-a", "agent-b", "agent-missing"])
        self.assertEqual(readiness_operations[-1]["details"]["probe_statuses"], ["agent-a:ok", "agent-b:timeout", "agent-missing:failed"])
        self.assertNotIn("secret readiness reply", json.dumps(readiness_operations, ensure_ascii=False))


    def test_live_agent_readiness_endpoint_runs_opt_in_official_round_smoke(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            smoke_result = {"status": "ok", "group_id": "doctor-smoke", "source_event_id": "base-secret", "replies": []}
            official_result = {
                "status": "ok",
                "group_id": "doctor-smoke",
                "meeting_id": "official-round-smoke-doctor-smoke",
                "round_id": "official_round_smoke",
                "agent_ids": ["doctor-smoke-local-cli"],
                "role_ids": ["smoke_local_cli"],
                "turn_count": 1,
                "answered_count": 1,
                "timeout_count": 0,
                "skipped_count": 0,
                "stopped": True,
                "timeout_seconds": 8.0,
                "statuses": ["answered"],
                "request_event_ids": ["request-secret"],
                "reply_event_ids": ["reply-secret"],
                "started_group": {"config_path": "/Users/me/private-live-agents.json"},
                "reply": {"message": "secret official reply"},
            }
            try:
                with (
                    patch("agentsassemble.gui.run_live_agent_smoke", return_value=smoke_result),
                    patch("agentsassemble.gui.run_live_agent_official_round_smoke", return_value=official_result) as official_smoke,
                ):
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agent-readiness",
                        data=json.dumps(
                            {
                                "group_id": "doctor-smoke",
                                "timeout": 8,
                                "official_round_smoke": True,
                            }
                        ).encode("utf-8"),
                        headers={"Content-Type": "application/json", "Host": "127.0.0.1:1"},
                        method="POST",
                    )
                    with urlopen(request, timeout=12) as response:
                        payload = json.loads(response.read().decode("utf-8"))
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20",
                    timeout=4,
                ) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(payload["status"], "ready")
        self.assertEqual(
            {check["id"]: check["status"] for check in payload["checks"]},
            {"health": "ok", "smoke": "ok", "official_round_smoke": "ok"},
        )
        self.assertEqual(
            payload["official_round_smoke"],
            {
                "status": "ok",
                "group_id": "doctor-smoke",
                "meeting_id": "official-round-smoke-doctor-smoke",
                "round_id": "official_round_smoke",
                "agent_ids": ["doctor-smoke-local-cli"],
                "role_ids": ["smoke_local_cli"],
                "turn_count": 1,
                "answered_count": 1,
                "timeout_count": 0,
                "skipped_count": 0,
                "stopped": True,
                "timeout_seconds": 8.0,
                "statuses": ["answered"],
            },
        )
        official_smoke.assert_called_once()
        self.assertEqual(official_smoke.call_args.kwargs["output_root"], root)
        self.assertEqual(official_smoke.call_args.kwargs["group_id"], "doctor-smoke")
        self.assertEqual(official_smoke.call_args.kwargs["timeout_seconds"], 8.0)
        serialized_payload = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("private-live-agents", serialized_payload)
        self.assertNotIn("secret official reply", serialized_payload)
        self.assertNotIn("base-secret", serialized_payload)
        self.assertNotIn("request-secret", serialized_payload)
        self.assertNotIn("reply-secret", serialized_payload)
        readiness_operations = [
            operation for operation in operations["operations"] if operation["operation"] == "readiness.check"
        ]
        self.assertEqual(readiness_operations[-1]["status"], "success")
        self.assertEqual(readiness_operations[-1]["details"]["official_round_smoke"], "ok")
        self.assertEqual(readiness_operations[-1]["details"]["official_round_answered_count"], 1)
        operation_blob = json.dumps(readiness_operations, ensure_ascii=False)
        self.assertNotIn("secret official reply", operation_blob)
        self.assertNotIn("base-secret", operation_blob)
        self.assertNotIn("request-secret", operation_blob)
        self.assertNotIn("reply-secret", operation_blob)


    def test_live_agent_readiness_endpoint_runs_opt_in_session_smoke(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            smoke_result = {"status": "ok", "group_id": "doctor-smoke", "replies": []}
            session_result = {
                "status": "ok",
                "meeting_id": "session-smoke-meeting",
                "group_id": "session-smoke",
                "agent_ids": ["session-smoke-local-cli"],
                "terminal_session_supported": False,
                "terminal_session_included": False,
                "terminal_session_status": "skipped",
                "terminal_session_reason": "pty_unavailable",
                "lobby_probe_count": 1,
                "expected_reply_count": 3,
                "self_service_official_reply_count": 1,
                "self_service_lobby_reply_count": 1,
                "self_service_post_restart_reply_count": 1,
                "self_service_post_recover_reply_count": 1,
                "self_service_soak_reply_count": 2,
                "reply_count": 3,
                "post_restart_reply_count": 3,
                "post_recover_reply_count": 3,
                "soak_cycle_count": 2,
                "soak_reply_count": 6,
                "soak_check_statuses": ["ready", "ready"],
                "rounds_status": "answered",
                "answered_round_count": 1,
                "finalization_status": "finalized",
                "finalization_official_event_count": 4,
                "return_packet_event_count": 4,
                "artifact_status": "present",
                "artifact_paths": ["agenda.md", "transcript.md", "decision.md", "return_packets/"],
                "start_status": "ready",
                "check_status": "ready",
                "resume_status": "ready",
                "restart_status": "ready",
                "recover_status": "ready",
                "stop_status": "stopped",
                "post_stop_process_status": "stopped",
                "source_event_id": "secret-source",
                "post_recover_source_event_id": "secret-recover-source",
                "soak_source_event_ids": ["secret-soak-source"],
                "replies": [{"id": "secret-reply", "message": "secret session reply"}],
                "soak_replies": [{"id": "secret-soak-reply", "message": "secret soak reply"}],
                "started_group": {"config_path": "/Users/me/private-live-agents.json"},
            }
            try:
                with (
                    patch("agentsassemble.gui.run_live_agent_smoke", return_value=smoke_result),
                    patch("agentsassemble.gui.run_live_agent_session_smoke", return_value=session_result) as session_smoke,
                ):
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agent-readiness",
                        data=json.dumps(
                            {
                                "group_id": "doctor-smoke",
                                "timeout": 8,
                                "session_smoke": True,
                                "session_smoke_soak_cycle_count": 2,
                                "session_smoke_soak_interval_seconds": 0.5,
                            }
                        ).encode("utf-8"),
                        headers={"Content-Type": "application/json", "Host": "127.0.0.1:1"},
                        method="POST",
                    )
                    with urlopen(request, timeout=12) as response:
                        payload = json.loads(response.read().decode("utf-8"))
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20",
                    timeout=4,
                ) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(payload["status"], "ready")
        self.assertEqual(
            {check["id"]: check["status"] for check in payload["checks"]},
            {"health": "ok", "smoke": "ok", "session_smoke": "ok"},
        )
        self.assertEqual(
            payload["session_smoke"],
            {
                "status": "ok",
                "meeting_id": "session-smoke-meeting",
                "group_id": "session-smoke",
                "agent_ids": ["session-smoke-local-cli"],
                "terminal_session_supported": False,
                "terminal_session_included": False,
                "terminal_session_status": "skipped",
                "terminal_session_reason": "pty_unavailable",
                "rounds_status": "answered",
                "answered_round_count": 1,
                "finalization_status": "finalized",
                "finalization_official_event_count": 4,
                "return_packet_event_count": 4,
                "artifact_status": "present",
                "artifact_paths": ["agenda.md", "transcript.md", "decision.md", "return_packets/"],
                "lobby_probe_count": 1,
                "expected_reply_count": 3,
                "self_service_official_reply_count": 1,
                "self_service_lobby_reply_count": 1,
                "self_service_post_restart_reply_count": 1,
                "self_service_post_recover_reply_count": 1,
                "self_service_soak_reply_count": 2,
                "reply_count": 3,
                "post_restart_reply_count": 3,
                "post_recover_reply_count": 3,
                "soak_cycle_count": 2,
                "soak_reply_count": 6,
                "soak_check_statuses": ["ready", "ready"],
                "start_status": "ready",
                "check_status": "ready",
                "resume_status": "ready",
                "restart_status": "ready",
                "recover_status": "ready",
                "stop_status": "stopped",
                "post_stop_process_status": "stopped",
            },
        )
        session_smoke.assert_called_once()
        self.assertEqual(session_smoke.call_args.kwargs["output_root"], root)
        self.assertEqual(session_smoke.call_args.kwargs["group_id"], "")
        self.assertEqual(session_smoke.call_args.kwargs["meeting_id"], "")
        self.assertEqual(session_smoke.call_args.kwargs["timeout_seconds"], 8.0)
        self.assertEqual(session_smoke.call_args.kwargs["soak_cycle_count"], 2)
        self.assertEqual(session_smoke.call_args.kwargs["soak_interval_seconds"], 0.5)
        serialized_payload = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("secret-source", serialized_payload)
        self.assertNotIn("secret-recover-source", serialized_payload)
        self.assertNotIn("secret-soak-source", serialized_payload)
        self.assertNotIn("secret session reply", serialized_payload)
        self.assertNotIn("secret soak reply", serialized_payload)
        self.assertNotIn("private-live-agents", serialized_payload)
        readiness_operations = [
            operation for operation in operations["operations"] if operation["operation"] == "readiness.check"
        ]
        self.assertEqual(readiness_operations[-1]["status"], "success")
        self.assertEqual(readiness_operations[-1]["details"]["session_smoke"], "ok")
        self.assertEqual(readiness_operations[-1]["details"]["session_smoke_terminal_session_status"], "skipped")
        self.assertFalse(readiness_operations[-1]["details"]["session_smoke_terminal_session_included"])
        self.assertEqual(readiness_operations[-1]["details"]["session_smoke_finalization_status"], "finalized")
        self.assertEqual(readiness_operations[-1]["details"]["session_smoke_finalization_official_event_count"], 4)
        self.assertEqual(readiness_operations[-1]["details"]["session_smoke_return_packet_event_count"], 4)
        self.assertEqual(readiness_operations[-1]["details"]["session_smoke_artifact_status"], "present")
        self.assertEqual(readiness_operations[-1]["details"]["session_smoke_self_service_official_reply_count"], 1)
        self.assertEqual(readiness_operations[-1]["details"]["session_smoke_self_service_lobby_reply_count"], 1)
        self.assertEqual(readiness_operations[-1]["details"]["session_smoke_self_service_post_recover_reply_count"], 1)
        self.assertEqual(readiness_operations[-1]["details"]["session_smoke_self_service_soak_reply_count"], 2)
        self.assertEqual(readiness_operations[-1]["details"]["session_smoke_reply_count"], 3)
        self.assertEqual(readiness_operations[-1]["details"]["session_smoke_post_recover_reply_count"], 3)
        self.assertEqual(readiness_operations[-1]["details"]["session_smoke_soak_cycle_count"], 2)
        self.assertEqual(readiness_operations[-1]["details"]["session_smoke_soak_reply_count"], 6)
        self.assertEqual(readiness_operations[-1]["details"]["session_smoke_soak_check_statuses"], ["ready", "ready"])
        self.assertEqual(readiness_operations[-1]["details"]["session_smoke_post_stop_process_status"], "stopped")
        readiness_blob = json.dumps(readiness_operations, ensure_ascii=False)
        self.assertNotIn("secret session reply", readiness_blob)
        self.assertNotIn("secret soak reply", readiness_blob)
        self.assertNotIn("secret-soak-source", readiness_blob)


    def test_live_agent_readiness_endpoint_sanitizes_session_smoke_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            root.mkdir()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with (
                    patch("agentsassemble.gui.run_live_agent_smoke", return_value={"status": "ok", "group_id": "doctor-smoke", "replies": []}),
                    patch(
                        "agentsassemble.gui.run_live_agent_session_smoke",
                        side_effect=ValueError("config_path=/Users/me/private-live-agents.json token=SECRET"),
                    ),
                ):
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agent-readiness",
                        data=json.dumps({"group_id": "doctor-smoke", "timeout": 8, "session_smoke": True}).encode("utf-8"),
                        headers={"Content-Type": "application/json", "Host": "127.0.0.1:1"},
                        method="POST",
                    )
                    with urlopen(request, timeout=12) as response:
                        payload = json.loads(response.read().decode("utf-8"))
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20",
                    timeout=4,
                ) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["session_smoke"]["status"], "failed")
        self.assertEqual(payload["session_smoke"]["error"], "session smoke could not be run")
        serialized_payload = json.dumps(payload, ensure_ascii=False)
        serialized_operations = json.dumps(operations, ensure_ascii=False)
        for secret in ("SECRET", "private-live-agents", "/Users/me", "config_path", "token="):
            self.assertNotIn(secret, serialized_payload)
            self.assertNotIn(secret, serialized_operations)
