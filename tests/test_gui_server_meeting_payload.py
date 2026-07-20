from tests.gui_server_test_support import (
    HTTPError,
    Path,
    Request,
    ThreadingHTTPServer,
    _make_handler,
    _stream_snapshot_payload,
    append_live_event,
    build_meeting_payload,
    codex_session_invite_payload,
    codex_sessions_payload,
    connect_live_agent,
    connect_live_agent_payload,
    heartbeat_live_agent,
    json,
    os,
    patch,
    read_live_agents,
    run_demo_meeting,
    tempfile,
    threading,
    unittest,
    urlopen,
    write_live_state,
)


class GuiServerMeetingPayloadTests(unittest.TestCase):

    def test_build_meeting_payload_includes_room_log_for_free_chat(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            result = run_demo_meeting(adapter_name="mock", output_root=Path(temp_dir), meeting_mode="free_chat")

            payload = build_meeting_payload(result.meeting_dir)

            self.assertEqual(payload["meeting"]["meeting_mode"], "free_chat")
            self.assertIn("room-log.md", payload["artifacts"])
            self.assertIn("informal free-chat record", payload["artifacts"]["room-log.md"])
            self.assertEqual(payload["artifacts"].get("decision.md"), "")
            self.assertEqual(payload["artifacts"].get("transcript.md"), "")


    def test_build_meeting_payload_projects_live_transcript_without_writing_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            meeting_dir = Path(temp_dir) / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(meeting_dir, {"meeting_id": "m1", "topic": "runtime", "live_status": "running"})
            append_live_event(
                meeting_dir,
                {
                    "kind": "live_agent_turn_request",
                    "meeting_id": "m1",
                    "target_agent_id": "agent-a",
                    "content": "private request text",
                },
            )
            append_live_event(
                meeting_dir,
                {
                    "kind": "message",
                    "meeting_id": "m1",
                    "actor_id": "agent-a",
                    "display_name": "Agent A",
                    "source_event_id": "request-1",
                    "content": "official live reply",
                },
            )

            payload = build_meeting_payload(meeting_dir)

            self.assertIn("official live reply", payload["artifacts"]["transcript.md"])
            self.assertNotIn("private request text", payload["artifacts"]["transcript.md"])
            self.assertFalse((meeting_dir / "transcript.md").exists())


    def test_build_meeting_payload_projects_shared_memory_without_writing_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            meeting_dir = Path(temp_dir) / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(meeting_dir, {"meeting_id": "m1", "topic": "runtime", "live_status": "running"})
            append_live_event(
                meeting_dir,
                {
                    "kind": "live_agent_turn_request",
                    "meeting_id": "m1",
                    "target_agent_id": "agent-a",
                    "content": "private request text",
                },
            )
            append_live_event(
                meeting_dir,
                {
                    "kind": "message",
                    "meeting_id": "m1",
                    "actor_id": "agent-a",
                    "display_name": "Agent A",
                    "source_event_id": "request-1",
                    "content": "official live reply\nAction: Preserve a shared memory artifact.",
                },
            )

            payload = build_meeting_payload(meeting_dir)

            self.assertIn("official live reply", payload["artifacts"]["shared_memory/rolling-summary.md"])
            self.assertIn("Preserve a shared memory artifact.", payload["artifacts"]["shared_memory/action-items.md"])
            self.assertNotIn("private request text", payload["artifacts"]["shared_memory/rolling-summary.md"])
            self.assertFalse((meeting_dir / "shared_memory" / "rolling-summary.md").exists())
            self.assertFalse((meeting_dir / "shared_memory" / "index.json").exists())


    def test_build_meeting_payload_preserves_existing_transcript(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            meeting_dir = Path(temp_dir) / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            (meeting_dir / "meeting.json").write_text('{"meeting_id":"m1"}\n', encoding="utf-8")
            (meeting_dir / "transcript.md").write_text("# Canonical Transcript\n\nKeep me.\n", encoding="utf-8")
            append_live_event(
                meeting_dir,
                {
                    "kind": "message",
                    "meeting_id": "m1",
                    "actor_id": "agent-a",
                    "display_name": "Agent A",
                    "content": "late official event",
                },
            )

            payload = build_meeting_payload(meeting_dir)

            self.assertEqual(payload["artifacts"]["transcript.md"], "# Canonical Transcript\n\nKeep me.\n")


    def test_build_meeting_payload_preserves_existing_empty_transcript(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            meeting_dir = Path(temp_dir) / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(meeting_dir, {"meeting_id": "m1", "topic": "runtime", "live_status": "running"})
            (meeting_dir / "transcript.md").write_text("", encoding="utf-8")
            append_live_event(
                meeting_dir,
                {
                    "kind": "message",
                    "meeting_id": "m1",
                    "actor_id": "agent-a",
                    "display_name": "Agent A",
                    "content": "official live reply",
                },
            )

            payload = build_meeting_payload(meeting_dir)

            self.assertEqual(payload["artifacts"]["transcript.md"], "")


    def test_meeting_payload_exposes_lifecycle_projection(self):
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
                    "roles": [{"id": "architect", "display_name": "Architect"}],
                    "agent_bindings": [
                        {"role_id": "architect", "agent_id": "agent-a", "permission_profile_id": "meeting"}
                    ],
                    "permission_profiles": {"meeting": {"id": "meeting", "meeting_read": True, "official_turn": True}},
                },
            )

            payload = build_meeting_payload(meeting_dir)

            self.assertEqual(payload["lifecycle"]["state"], "waiting_for_agents")
            self.assertEqual(payload["lifecycle"]["role_hints"][0]["role_id"], "architect")
            serialized = json.dumps(payload["lifecycle"], ensure_ascii=False)
            self.assertNotIn("permission_profile_id", serialized)
            self.assertNotIn(str(root), serialized)


    def test_meeting_lifecycle_endpoint_returns_compact_projection(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(meeting_dir, {"meeting_id": "m1", "topic": "runtime", "live_status": "running"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/meetings/m1/lifecycle", timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(payload["meeting_id"], "m1")
            self.assertEqual(payload["lifecycle"]["state"], "preparing")
            serialized = json.dumps(payload, ensure_ascii=False)
            self.assertNotIn("artifacts", serialized)
            self.assertNotIn("live_events", serialized)
            self.assertNotIn(str(root), serialized)


    def test_meeting_lifecycle_uses_current_bound_live_agent_roster(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            meeting_record = {
                "meeting_id": "m1",
                "topic": "runtime",
                "live_status": "running",
                "roles": [{"id": "architect", "display_name": "Architect"}],
                "agent_bindings": [
                    {
                        "role_id": "architect",
                        "agent_id": "agent-a",
                        "provider_id": "local-cli",
                        "permission_profile_id": "meeting",
                    }
                ],
                "provider_configs": {"local-cli": {"id": "local-cli", "kind": "local_cli"}},
                "permission_profiles": {"meeting": {"id": "meeting", "meeting_read": True, "official_turn": True}},
            }
            (meeting_dir / "meeting.json").write_text(json.dumps(meeting_record, ensure_ascii=False), encoding="utf-8")
            append_live_event(meeting_dir, {"id": "system-1", "kind": "system", "message": "meeting exists"})
            connect_live_agent(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "meeting_id": "m1",
                    "status": "online",
                },
            )

            payload = build_meeting_payload(meeting_dir)
            snapshot = _stream_snapshot_payload(root, "meeting", meeting_id="m1")
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/meetings/m1/lifecycle", timeout=4) as response:
                    endpoint_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            for lifecycle in (
                payload["lifecycle"],
                snapshot["meeting_stream_snapshot"]["lifecycle"],
                endpoint_payload["lifecycle"],
            ):
                self.assertEqual(lifecycle["counts"]["live_agents"], 1)
                self.assertEqual(lifecycle["role_hints"][0]["admission_status"], "bound_to_meeting")
                self.assertEqual(lifecycle["state"], "preparing")


    def test_meeting_lifecycle_does_not_bind_same_agent_from_other_or_blank_meeting(self):
        for registered_meeting_id in ("m2", ""):
            with self.subTest(registered_meeting_id=registered_meeting_id or "blank"):
                with tempfile.TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir)
                    meeting_dir = root / "meetings" / "m1"
                    meeting_dir.mkdir(parents=True)
                    (meeting_dir / "meeting.json").write_text(
                        json.dumps(
                            {
                                "meeting_id": "m1",
                                "topic": "runtime",
                                "live_status": "running",
                                "roles": [{"id": "architect", "display_name": "Architect"}],
                                "agent_bindings": [
                                    {
                                        "role_id": "architect",
                                        "agent_id": "agent-a",
                                        "provider_id": "local-cli",
                                        "permission_profile_id": "meeting",
                                    }
                                ],
                                "provider_configs": {"local-cli": {"id": "local-cli", "kind": "local_cli"}},
                                "permission_profiles": {
                                    "meeting": {"id": "meeting", "meeting_read": True, "official_turn": True}
                                },
                            },
                            ensure_ascii=False,
                        ),
                        encoding="utf-8",
                    )
                    registration = {
                        "agent_id": "agent-a",
                        "display_name": "Agent A",
                        "provider_kind": "local_cli",
                        "connection_kind": "local_cli",
                        "status": "online",
                    }
                    if registered_meeting_id:
                        registration["meeting_id"] = registered_meeting_id
                    connect_live_agent(root, registration)

                    lifecycle = build_meeting_payload(meeting_dir)["lifecycle"]

                    self.assertEqual(lifecycle["counts"]["live_agents"], 0)
                    self.assertEqual(lifecycle["role_hints"][0]["admission_status"], "waiting_for_agent")
                    self.assertEqual(lifecycle["state"], "waiting_for_agents")


    def test_build_meeting_payload_preserves_codex_live_session_binding_metadata(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            meeting_dir = Path(temp_dir) / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            (meeting_dir / "meeting.json").write_text(
                json.dumps(
                    {
                        "meeting_id": "m1",
                        "topic": "테스트",
                        "question": "기존 세션을 이어받나?",
                        "roles": [
                            {
                                "id": "lore_lawyer",
                                "display_name": "설정충",
                                "lens": "Canon Analyst",
                                "research_focus": "canon",
                            }
                        ],
                        "provider_configs": {
                            "codex-live": {
                                "id": "codex-live",
                                "kind": "codex_live_session",
                                "display_name": "Codex CLI Live Session",
                            }
                        },
                        "permission_profiles": {
                            "codex_live_meeting_readonly": {"id": "codex_live_meeting_readonly"}
                        },
                        "agent_bindings": [
                            {
                                "agent_id": "codex-live-lore-lawyer",
                                "role_id": "lore_lawyer",
                                "owner_id": "host",
                                "provider_id": "codex-live",
                                "model_id": "local-codex-session",
                                "permission_profile_id": "codex_live_meeting_readonly",
                                "join_mode": "current_session",
                                "session_id": "019e3038-39cc-76a2-a746-5ba8c0f3b408",
                            }
                        ],
                        "debate_rounds": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            payload = build_meeting_payload(meeting_dir)

            binding = payload["meeting"]["agent_bindings"][0]
            self.assertEqual(binding["join_mode"], "current_session")
            self.assertEqual(binding["session_id"], "019e3038-39cc-76a2-a746-5ba8c0f3b408")
            self.assertEqual(payload["meeting"]["provider_configs"]["codex-live"]["kind"], "codex_live_session")


    def test_codex_sessions_payload_reads_recent_codex_sessions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir) / "codex-home"
            codex_home.mkdir()
            (codex_home / "session_index.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "id": "old",
                                "thread_name": "Old thread",
                                "updated_at": "2026-05-15T00:00:00Z",
                            }
                        ),
                        json.dumps(
                            {
                                "id": "new",
                                "thread_name": "New thread",
                                "updated_at": "2026-05-17T00:00:00Z",
                            }
                        ),
                    ]
                ),
                encoding="utf-8",
            )

            with patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}):
                payload = codex_sessions_payload(limit=1)

            self.assertEqual(payload["sessions"], [{"id": "new", "thread_name": "New thread", "updated_at": "2026-05-17T00:00:00Z"}])


    def test_codex_session_invite_payload_writes_config_for_meeting_roles(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            (meeting_dir / "meeting.json").write_text(
                json.dumps(
                    {
                        "meeting_id": "m1",
                        "roles": [
                            {"id": "lore_lawyer", "display_name": "설정충"},
                            {"id": "fanboard_skeptic", "display_name": "회의론자"},
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            payload = codex_session_invite_payload(
                root,
                session_id="019e02af-c287-7cd1-aab7-c1e059c5ed44",
                role_id="lore_lawyer",
                meeting_id="m1",
            )

            config_path = root / "codex-live-session.local.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            bindings = {binding["role_id"]: binding for binding in config["agent_bindings"]}
            self.assertEqual(payload["config_path"], str(config_path))
            self.assertEqual(payload["binding"]["role_id"], "lore_lawyer")
            self.assertEqual(payload["binding"]["join_mode"], "current_session")
            self.assertEqual(payload["binding"]["session_id"], "019e02af-c287-7cd1-aab7-c1e059c5ed44")
            self.assertEqual(bindings["lore_lawyer"]["session_id"], "019e02af-c287-7cd1-aab7-c1e059c5ed44")
            self.assertEqual(bindings["fanboard_skeptic"]["join_mode"], "fresh")


    def test_codex_session_invite_http_endpoint_writes_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            (meeting_dir / "meeting.json").write_text(
                json.dumps(
                    {
                        "meeting_id": "m1",
                        "roles": [{"id": "lore_lawyer", "display_name": "설정충"}],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/codex-sessions/invite",
                    data=json.dumps(
                        {
                            "meeting_id": "m1",
                            "role_id": "lore_lawyer",
                            "session_id": "019e02af-c287-7cd1-aab7-c1e059c5ed44",
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations",
                    timeout=4,
                ) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(payload["binding"]["role_id"], "lore_lawyer")
            self.assertTrue((root / "codex-live-session.local.json").exists())
            persisted_operations = (root / "live-agent-runs" / "operations.jsonl").read_text(encoding="utf-8")
            invite_operations = [
                operation
                for operation in operations["operations"]
                if operation["operation"] == "codex_session.invite"
            ]
            self.assertEqual(len(invite_operations), 1)
            self.assertEqual(invite_operations[0]["status"], "success")
            self.assertEqual(invite_operations[0]["target_id"], "lore_lawyer")
            self.assertEqual(
                invite_operations[0]["details"],
                {
                    "role_id": "lore_lawyer",
                    "agent_id": "codex-live-lore-lawyer",
                    "join_mode": "current_session",
                    "provider_id": "codex-live",
                },
            )
            operation_blob = json.dumps(invite_operations, ensure_ascii=False)
            self.assertNotIn("019e02af-c287-7cd1-aab7-c1e059c5ed44", operation_blob)
            self.assertNotIn("codex-live-session.local.json", operation_blob)
            self.assertNotIn("019e02af-c287-7cd1-aab7-c1e059c5ed44", persisted_operations)
            self.assertNotIn("codex-live-session.local.json", persisted_operations)


    def test_codex_session_invite_http_failure_records_safe_operation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            (meeting_dir / "meeting.json").write_text(
                json.dumps(
                    {
                        "meeting_id": "m1",
                        "roles": [
                            {"id": "lore_lawyer", "display_name": "설정충"},
                            {"id": "show_me_the_feats", "display_name": "근거충"},
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (root / "codex-live-session.local.json").write_text(
                json.dumps(
                    {
                        "agent_bindings": [
                            {
                                "agent_id": "codex-live-feats",
                                "role_id": "show_me_the_feats",
                                "owner_id": "host",
                                "provider_id": "codex-live",
                                "permission_profile_id": "codex_live_meeting_readonly",
                                "join_mode": "current_session",
                                "session_id": "019e02af-c287-7cd1-aab7-c1e059c5ed44",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/codex-sessions/invite",
                    data=json.dumps(
                        {
                            "meeting_id": "m1",
                            "role_id": "lore_lawyer",
                            "session_id": "019e02af-c287-7cd1-aab7-c1e059c5ed44",
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as caught:
                    urlopen(request, timeout=4)
                error_payload = json.loads(caught.exception.read().decode("utf-8"))
                caught.exception.close()
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations",
                    timeout=4,
                ) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(caught.exception.code, 400)
            self.assertEqual(error_payload["error"], "Codex live session invite failed.")
            self.assertEqual(error_payload["details"], {"role_id": "lore_lawyer"})
            persisted_operations = (root / "live-agent-runs" / "operations.jsonl").read_text(encoding="utf-8")
            invite_operations = [
                operation
                for operation in operations["operations"]
                if operation["operation"] == "codex_session.invite"
            ]
            self.assertEqual(len(invite_operations), 1)
            self.assertEqual(invite_operations[0]["status"], "failed")
            self.assertEqual(invite_operations[0]["target_id"], "lore_lawyer")
            self.assertEqual(invite_operations[0]["details"], {"role_id": "lore_lawyer"})
            response_blob = json.dumps(error_payload, ensure_ascii=False)
            operation_blob = json.dumps(invite_operations, ensure_ascii=False)
            for blob in (response_blob, operation_blob, persisted_operations):
                self.assertNotIn("019e02af-c287-7cd1-aab7-c1e059c5ed44", blob)
                self.assertNotIn("codex-live-session.local.json", blob)


    def test_codex_session_invite_http_failure_omits_unknown_role_detail(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            (meeting_dir / "meeting.json").write_text(
                json.dumps(
                    {
                        "meeting_id": "m1",
                        "roles": [{"id": "lore_lawyer", "display_name": "설정충"}],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/codex-sessions/invite",
                    data=json.dumps(
                        {
                            "meeting_id": "m1",
                            "role_id": "codex-live-session.local.json",
                            "session_id": "019e02af-c287-7cd1-aab7-c1e059c5ed44",
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as caught:
                    urlopen(request, timeout=4)
                error_payload = json.loads(caught.exception.read().decode("utf-8"))
                caught.exception.close()
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations",
                    timeout=4,
                ) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(caught.exception.code, 400)
            self.assertEqual(error_payload["error"], "Codex live session invite failed.")
            self.assertNotIn("details", error_payload)
            persisted_operations = (root / "live-agent-runs" / "operations.jsonl").read_text(encoding="utf-8")
            invite_operations = [
                operation
                for operation in operations["operations"]
                if operation["operation"] == "codex_session.invite"
            ]
            self.assertEqual(len(invite_operations), 1)
            self.assertEqual(invite_operations[0]["status"], "failed")
            self.assertEqual(invite_operations[0]["target_id"], "")
            self.assertEqual(invite_operations[0]["details"], {})
            response_blob = json.dumps(error_payload, ensure_ascii=False)
            operation_blob = json.dumps(invite_operations, ensure_ascii=False)
            for blob in (response_blob, operation_blob, persisted_operations):
                self.assertNotIn("019e02af-c287-7cd1-aab7-c1e059c5ed44", blob)
                self.assertNotIn("codex-live-session.local.json", blob)


    def test_codex_session_join_http_endpoint_binds_pre_round_meeting_and_ensures_session(self):
        class JoinSupervisor:
            def __init__(self, output_root: Path) -> None:
                self.output_root = output_root
                self.started = []
                self.groups = []

            def snapshot_groups(self):
                return list(self.groups)

            def list_groups(self):
                return list(self.groups)

            def start_group(self, **kwargs):
                self.started.append(kwargs)
                config = json.loads(Path(kwargs["config_path"]).read_text(encoding="utf-8"))
                agents = [{"agent_id": agent["agent_id"]} for agent in config["agents"]]
                for agent in config["agents"]:
                    connect_live_agent_payload(
                        self.output_root,
                        {
                            "agent_id": agent["agent_id"],
                            "display_name": agent.get("display_name", agent["agent_id"]),
                            "provider_kind": agent.get("provider_kind", "codex_live_session"),
                            "connection_kind": agent.get("connection_kind", "live_session"),
                            "meeting_id": kwargs["meeting_id"],
                            "session_id": agent.get("session_id", ""),
                        },
                    )
                    heartbeat_live_agent(
                        self.output_root,
                        agent["agent_id"],
                        status="online",
                        metadata={"session_id": agent.get("session_id", "")},
                    )
                group = {
                    "group_id": kwargs["group_id"],
                    "status": "running",
                    "meeting_id": kwargs["meeting_id"],
                    "agents": agents,
                }
                self.groups = [group]
                return group

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(
                meeting_dir,
                {
                    "meeting_id": "m1",
                    "topic": "runtime",
                    "question": "Can Codex join now?",
                    "roles": [
                        {"id": "lore_lawyer", "display_name": "설정충"},
                        {"id": "show_me_the_feats", "display_name": "근거충"},
                    ],
                    "provider_configs": {
                        "mock": {"id": "mock", "kind": "mock", "display_name": "Mock Demo"}
                    },
                    "permission_profiles": {
                        "meeting_read_only": {"id": "meeting_read_only", "meeting_read": True}
                    },
                    "agent_bindings": [
                        {
                            "agent_id": "lore-agent",
                            "role_id": "lore_lawyer",
                            "provider_id": "mock",
                            "permission_profile_id": "meeting_read_only",
                        },
                        {
                            "agent_id": "feats-agent",
                            "role_id": "show_me_the_feats",
                            "provider_id": "mock",
                            "permission_profile_id": "meeting_read_only",
                        },
                    ],
                    "debate_rounds": [],
                    "live_status": "running",
                },
            )
            supervisor = JoinSupervisor(root)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/codex-sessions/join",
                    data=json.dumps(
                        {
                            "meeting_id": "m1",
                            "role_id": "lore_lawyer",
                            "session_id": "019e02af-c287-7cd1-aab7-c1e059c5ed44",
                            "connect_timeout_seconds": 0,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with patch(
                    "agentsassemble.legacy.live_agent.runtime.sessions.preflight_live_agent_config",
                    return_value={"status": "ok", "summary": {"agents": 2}},
                ):
                    with urlopen(request, timeout=4) as response:
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
            self.assertEqual(payload["action"], "resume")
            self.assertEqual(payload["meeting_id"], "m1")
            self.assertEqual(payload["group_id"], "live-agents.codex-session.local")
            self.assertTrue((root / "codex-live-session.local.json").exists())
            self.assertTrue((root / "live-agents.codex-session.local.json").exists())
            self.assertEqual(supervisor.started[0]["meeting_id"], "m1")
            self.assertEqual(supervisor.started[0]["group_id"], "live-agents.codex-session.local")

            live_state = json.loads((meeting_dir / "live_state.json").read_text(encoding="utf-8"))
            bindings = {binding["role_id"]: binding for binding in live_state["agent_bindings"]}
            self.assertEqual(bindings["lore_lawyer"]["join_mode"], "current_session")
            self.assertEqual(bindings["lore_lawyer"]["session_id"], "019e02af-c287-7cd1-aab7-c1e059c5ed44")
            self.assertEqual(bindings["show_me_the_feats"]["join_mode"], "fresh")
            self.assertEqual(live_state["provider_configs"]["codex-live"]["kind"], "codex_live_session")
            self.assertEqual(live_state["permission_profiles"]["codex_live_meeting_readonly"]["official_turn"], True)

            resident_config = json.loads((root / "live-agents.codex-session.local.json").read_text(encoding="utf-8"))
            self.assertEqual([agent["agent_id"] for agent in resident_config["agents"]], ["codex-live-lore-lawyer", "codex-live-show-me-the-feats"])
            self.assertEqual(resident_config["agents"][0]["session_id"], "019e02af-c287-7cd1-aab7-c1e059c5ed44")
            self.assertEqual(resident_config["agents"][0]["meeting_id"], "m1")
            self.assertEqual({agent["engagement_mode"] for agent in resident_config["agents"]}, {"moderator_called"})

            join_operations = [operation for operation in operations["operations"] if operation["operation"] == "codex_session.join"]
            self.assertEqual(len(join_operations), 1)
            self.assertEqual(join_operations[0]["status"], "success")
            self.assertEqual(join_operations[0]["target_id"], "lore_lawyer")
            persisted_operations = (root / "live-agent-runs" / "operations.jsonl").read_text(encoding="utf-8")
            operation_blob = json.dumps(operations, ensure_ascii=False)
            for blob in (operation_blob, persisted_operations):
                self.assertNotIn("019e02af-c287-7cd1-aab7-c1e059c5ed44", blob)
                self.assertNotIn("codex-live-session.local.json", blob)
                self.assertNotIn("live-agents.codex-session.local.json", blob)


    def test_codex_session_join_refuses_meeting_with_official_progress_before_writing(self):
        class JoinSupervisor:
            def __init__(self) -> None:
                self.started = []

            def snapshot_groups(self):
                return []

            def list_groups(self):
                return []

            def start_group(self, **kwargs):
                self.started.append(kwargs)
                raise AssertionError("join should refuse before starting a group")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(
                meeting_dir,
                {
                    "meeting_id": "m1",
                    "topic": "runtime",
                    "roles": [{"id": "lore_lawyer", "display_name": "설정충"}],
                    "debate_rounds": [{"id": "round_1", "status": "answered"}],
                    "live_status": "running",
                },
            )
            supervisor = JoinSupervisor()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/codex-sessions/join",
                    data=json.dumps(
                        {
                            "meeting_id": "m1",
                            "role_id": "lore_lawyer",
                            "session_id": "019e02af-c287-7cd1-aab7-c1e059c5ed44",
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as caught:
                    urlopen(request, timeout=4)
                error_payload = json.loads(caught.exception.read().decode("utf-8"))
                caught.exception.close()
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20",
                    timeout=4,
                ) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(caught.exception.code, 400)
            self.assertEqual(error_payload["error"], "Codex live session join failed.")
            self.assertEqual(error_payload["details"], {"meeting_id": "m1", "role_id": "lore_lawyer"})
            self.assertEqual(supervisor.started, [])
            self.assertFalse((root / "codex-live-session.local.json").exists())
            self.assertFalse((root / "live-agents.codex-session.local.json").exists())
            operation_blob = json.dumps(operations, ensure_ascii=False)
            self.assertNotIn("019e02af-c287-7cd1-aab7-c1e059c5ed44", operation_blob)
            self.assertNotIn("codex-live-session.local.json", operation_blob)


    def test_codex_session_join_restarts_ready_group_when_selected_session_changes(self):
        class ReadyGroupSupervisor:
            def __init__(self, output_root: Path, config_path: Path) -> None:
                self.output_root = output_root
                self.stopped = []
                self.restarted = []
                self.groups = [
                    {
                        "group_id": "live-agents.codex-session.local",
                        "status": "running",
                        "meeting_id": "m1",
                        "config_path": str(config_path),
                        "server": "http://127.0.0.1:8765",
                        "agents": [{"agent_id": "codex-live-lore-lawyer"}],
                    }
                ]

            def snapshot_groups(self):
                return list(self.groups)

            def list_groups(self):
                return list(self.groups)

            def stop_group(self, group_id):
                self.stopped.append(group_id)
                self.groups[0]["status"] = "stopped"

            def restart_group(self, group_id):
                self.restarted.append(group_id)
                config = json.loads(Path(self.groups[0]["config_path"]).read_text(encoding="utf-8"))
                agent = config["agents"][0]
                connect_live_agent_payload(
                    self.output_root,
                    {
                        "agent_id": agent["agent_id"],
                        "display_name": agent.get("display_name", agent["agent_id"]),
                        "provider_kind": agent.get("provider_kind", "codex_live_session"),
                        "connection_kind": agent.get("connection_kind", "live_session"),
                        "meeting_id": "m1",
                        "session_id": agent.get("session_id", ""),
                    },
                )
                heartbeat_live_agent(self.output_root, agent["agent_id"], status="online", metadata={"session_id": agent.get("session_id", "")})
                self.groups[0]["status"] = "running"
                return dict(self.groups[0])

            def start_group(self, **kwargs):
                raise AssertionError("ready session drift should restart, not start")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(
                meeting_dir,
                {
                    "meeting_id": "m1",
                    "topic": "runtime",
                    "roles": [{"id": "lore_lawyer", "display_name": "설정충"}],
                    "provider_configs": {"codex-live": {"id": "codex-live", "kind": "codex_live_session"}},
                    "permission_profiles": {"codex_live_meeting_readonly": {"id": "codex_live_meeting_readonly", "meeting_read": True}},
                    "agent_bindings": [
                        {
                            "agent_id": "codex-live-lore-lawyer",
                            "role_id": "lore_lawyer",
                            "provider_id": "codex-live",
                            "permission_profile_id": "codex_live_meeting_readonly",
                            "join_mode": "current_session",
                            "session_id": "old-session",
                        }
                    ],
                    "debate_rounds": [],
                    "live_status": "running",
                },
            )
            live_agent_config = root / "live-agents.codex-session.local.json"
            live_agent_config.write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "codex-live-lore-lawyer",
                                "provider_kind": "codex_live_session",
                                "connection_kind": "live_session",
                                "meeting_id": "m1",
                                "session_id": "old-session",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            connect_live_agent_payload(
                root,
                {
                    "agent_id": "codex-live-lore-lawyer",
                    "display_name": "Codex Lore",
                    "provider_kind": "codex_live_session",
                    "connection_kind": "live_session",
                    "meeting_id": "m1",
                    "session_id": "old-session",
                },
            )
            heartbeat_live_agent(root, "codex-live-lore-lawyer", status="online", metadata={"session_id": "old-session"})
            supervisor = ReadyGroupSupervisor(root, live_agent_config)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=supervisor))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/codex-sessions/join",
                    data=json.dumps(
                        {
                            "meeting_id": "m1",
                            "role_id": "lore_lawyer",
                            "session_id": "new-session",
                            "connect_timeout_seconds": 0,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with patch(
                    "agentsassemble.legacy.live_agent.runtime.sessions.preflight_live_agent_config",
                    return_value={"status": "ok", "summary": {"agents": 1}},
                ):
                    with urlopen(request, timeout=4) as response:
                        payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(payload["status"], "ready")
            self.assertEqual(payload["action"], "restart")
            self.assertEqual(supervisor.stopped, ["live-agents.codex-session.local"])
            self.assertEqual(supervisor.restarted, ["live-agents.codex-session.local"])
            live_agent = next(agent for agent in read_live_agents(root) if agent["agent_id"] == "codex-live-lore-lawyer")
            self.assertEqual(live_agent["session_id"], "new-session")


    def test_codex_session_join_refuses_existing_official_event_before_writing(self):
        class JoinSupervisor:
            def start_group(self, **kwargs):
                raise AssertionError("join should refuse before starting a group")

            def snapshot_groups(self):
                return []

            def list_groups(self):
                return []

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            original_state = {
                "meeting_id": "m1",
                "topic": "runtime",
                "roles": [{"id": "lore_lawyer", "display_name": "설정충"}],
                "debate_rounds": [],
                "live_status": "running",
            }
            write_live_state(meeting_dir, original_state)
            append_live_event(
                meeting_dir,
                {
                    "kind": "live_agent_turn_request",
                    "meeting_id": "m1",
                    "target_agent_id": "codex-live-lore-lawyer",
                    "content": "official turn already started",
                },
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=JoinSupervisor()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/codex-sessions/join",
                    data=json.dumps(
                        {
                            "meeting_id": "m1",
                            "role_id": "lore_lawyer",
                            "session_id": "019e02af-c287-7cd1-aab7-c1e059c5ed44",
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as caught:
                    urlopen(request, timeout=4)
                error_payload = json.loads(caught.exception.read().decode("utf-8"))
                caught.exception.close()
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(caught.exception.code, 400)
            self.assertEqual(error_payload["error"], "Codex live session join failed.")
            self.assertEqual(json.loads((meeting_dir / "live_state.json").read_text(encoding="utf-8")), original_state)
            self.assertFalse((root / "codex-live-session.local.json").exists())
            self.assertFalse((root / "live-agents.codex-session.local.json").exists())


    def test_codex_session_join_failure_records_safe_operation_without_session_or_config_paths(self):
        class FailingJoinSupervisor:
            def snapshot_groups(self):
                return []

            def list_groups(self):
                return []

            def start_group(self, **kwargs):
                raise ValueError(
                    "failed for 019e02af-c287-7cd1-aab7-c1e059c5ed44 "
                    "codex-live-session.local.json live-agents.codex-session.local.json"
                )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(
                meeting_dir,
                {
                    "meeting_id": "m1",
                    "topic": "runtime",
                    "roles": [{"id": "lore_lawyer", "display_name": "설정충"}],
                    "debate_rounds": [],
                    "live_status": "running",
                },
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=FailingJoinSupervisor()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/codex-sessions/join",
                    data=json.dumps(
                        {
                            "meeting_id": "m1",
                            "role_id": "lore_lawyer",
                            "session_id": "019e02af-c287-7cd1-aab7-c1e059c5ed44",
                            "connect_timeout_seconds": 0,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with patch(
                    "agentsassemble.legacy.live_agent.runtime.sessions.preflight_live_agent_config",
                    return_value={"status": "ok", "summary": {"agents": 1}},
                ):
                    with self.assertRaises(HTTPError) as caught:
                        urlopen(request, timeout=4)
                error_payload = json.loads(caught.exception.read().decode("utf-8"))
                caught.exception.close()
                persisted_operations = (root / "live-agent-runs" / "operations.jsonl").read_text(encoding="utf-8")
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(caught.exception.code, 400)
            self.assertEqual(error_payload["error"], "Codex live session join failed.")
            self.assertEqual(error_payload["details"], {"meeting_id": "m1", "role_id": "lore_lawyer"})
            response_blob = json.dumps(error_payload, ensure_ascii=False)
            for blob in (response_blob, persisted_operations):
                self.assertNotIn("019e02af-c287-7cd1-aab7-c1e059c5ed44", blob)
                self.assertNotIn("codex-live-session.local.json", blob)
                self.assertNotIn("live-agents.codex-session.local.json", blob)
