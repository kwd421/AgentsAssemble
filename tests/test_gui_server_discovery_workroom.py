from tests.gui_server_test_support import (
    Path,
    Request,
    ThreadingHTTPServer,
    _make_handler,
    append_live_agent_operation,
    build_meeting_payload,
    json,
    live_agent_discovery_payload,
    patch,
    run_demo_meeting,
    tempfile,
    threading,
    unittest,
    urlopen,
)


class GuiServerDiscoveryWorkroomTests(unittest.TestCase):

    def test_live_agent_discovery_payload_filters_exact_approved_agents_before_writing_bundle(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            def resolver(command):
                return f"/opt/bin/{command}" if command in {"claude", "codex", "antigravity"} else None

            with (
                patch("agentsassemble.live_agent_discovery.shutil.which", side_effect=resolver),
                patch("agentsassemble.live_agent_discovery.terminal_sessions_supported", return_value=True),
            ):
                report = live_agent_discovery_payload(
                    root,
                    {
                        "server": "http://room.local",
                        "meeting_id": "resident-gui",
                        "write_config": True,
                        "session_bundle": True,
                        "approved_agents": ["codex-live"],
                    },
                    default_server="http://default.local",
                )

            self.assertEqual(report["status"], "ok")
            output_path = Path(report["output"])
            self.assertTrue(output_path.exists())
            written = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual([agent["agent_id"] for agent in written["agents"]], ["codex-live"])
            council = json.loads(Path(report["session_bundle"]["council_config_path"]).read_text(encoding="utf-8"))
            agent_config = json.loads(Path(report["session_bundle"]["agent_config_path"]).read_text(encoding="utf-8"))
            self.assertEqual([role["id"] for role in council["roles"]], ["codex_live"])
            self.assertEqual([binding["agent_id"] for binding in agent_config["agent_bindings"]], ["codex-live"])
            discoveries = {item["command"]: item for item in report["discoveries"]}
            self.assertEqual(discoveries["codex"]["approval_status"], "approved")
            self.assertEqual(discoveries["claude"]["approval_status"], "not_approved")
            self.assertEqual(discoveries["antigravity"]["approval_status"], "not_approved")
            self.assertEqual(discoveries["antigravity"]["entry_status"], "approval_required")
            self.assertFalse(discoveries["antigravity"]["included"])
            self.assertEqual(report["approval_filter"]["approved_agents"], ["codex-live"])


    def test_live_agent_discovery_endpoint_records_exact_approval_operation_evidence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            def resolver(command):
                return f"/opt/bin/{command}" if command in {"claude", "codex", "antigravity"} else None

            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                server_url = f"http://127.0.0.1:{server.server_port}"
                with (
                    patch("agentsassemble.live_agent_discovery.shutil.which", side_effect=resolver),
                    patch("agentsassemble.live_agent_discovery.terminal_sessions_supported", return_value=True),
                ):
                    request = Request(
                        f"{server_url}/api/live-agent-discovery",
                        data=json.dumps(
                            {
                                "meeting_id": "resident-gui",
                                "write_config": True,
                                "session_bundle": True,
                                "approved_agents": ["codex-live", "missing-agent"],
                            }
                        ).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urlopen(request, timeout=4) as response:
                        report = json.loads(response.read().decode("utf-8"))
                with urlopen(f"{server_url}/api/live-agent-operations", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            discovery_operations = [
                operation for operation in operations["operations"] if operation["operation"] == "discovery.run"
            ]
            self.assertEqual(report["approval_filter"]["approved_agents"], ["codex-live"])
            self.assertEqual(len(discovery_operations), 1)
            details = discovery_operations[0]["details"]
            self.assertEqual(details["result_status"], "ok")
            self.assertEqual(details["agents"], 1)
            self.assertEqual(details["approved_count"], 1)
            self.assertEqual(details["approved_agent_ids"], ["codex-live"])
            self.assertEqual(details["excluded_agent_count"], 2)
            self.assertEqual(details["unmatched_approval_count"], 1)
            serialized = json.dumps(discovery_operations[0], ensure_ascii=False)
            self.assertNotIn("/opt/bin", serialized)
            self.assertNotIn("approved_commands", serialized)
            self.assertNotIn("excluded_commands", serialized)


    def test_live_agent_discovery_endpoint_records_cli_approval_counts_without_command_names(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            def resolver(command):
                return f"/opt/bin/{command}" if command in {"claude", "codex", "antigravity"} else None

            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                server_url = f"http://127.0.0.1:{server.server_port}"
                with (
                    patch("agentsassemble.live_agent_discovery.shutil.which", side_effect=resolver),
                    patch("agentsassemble.live_agent_discovery.terminal_sessions_supported", return_value=True),
                ):
                    request = Request(
                        f"{server_url}/api/live-agent-discovery",
                        data=json.dumps(
                            {
                                "meeting_id": "resident-gui",
                                "write_config": True,
                                "session_bundle": True,
                                "approved_commands": ["codex", "missing-cli"],
                            }
                        ).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urlopen(request, timeout=4) as response:
                        report = json.loads(response.read().decode("utf-8"))
                with urlopen(f"{server_url}/api/live-agent-operations", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            discovery_operations = [
                operation for operation in operations["operations"] if operation["operation"] == "discovery.run"
            ]
            self.assertEqual(report["approval_filter"]["approved_commands"], ["codex"])
            self.assertEqual(len(discovery_operations), 1)
            details = discovery_operations[0]["details"]
            self.assertEqual(details["result_status"], "ok")
            self.assertEqual(details["agents"], 1)
            self.assertEqual(details["approved_count"], 1)
            self.assertNotIn("approved_agent_ids", details)
            self.assertEqual(details["approved_cli_count"], 1)
            self.assertEqual(details["excluded_cli_count"], 2)
            self.assertEqual(details["excluded_agent_count"], 2)
            self.assertEqual(details["unmatched_approval_count"], 1)
            serialized_details = json.dumps(details, ensure_ascii=False)
            self.assertNotIn("/opt/bin", serialized_details)
            self.assertNotIn("missing-cli", serialized_details)
            self.assertNotIn("approved_commands", serialized_details)
            self.assertNotIn("excluded_commands", serialized_details)
            approval_details = {
                key: value
                for key, value in details.items()
                if key
                in {
                    "approved_count",
                    "approved_cli_count",
                    "excluded_agent_count",
                    "excluded_cli_count",
                    "unmatched_approval_count",
                }
            }
            serialized_approval_details = json.dumps(approval_details, ensure_ascii=False)
            self.assertNotIn("codex", serialized_approval_details)
            self.assertNotIn("claude", serialized_approval_details)
            self.assertNotIn("antigravity", serialized_approval_details)
            self.assertNotIn("missing-cli", serialized_approval_details)


    def test_build_meeting_payload_contains_tabs_and_artifacts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            result = run_demo_meeting(adapter_name="mock", output_root=Path(temp_dir))

            payload = build_meeting_payload(result.meeting_dir)

            self.assertEqual(payload["meeting"]["meeting_id"], result.meeting_id)
            self.assertIn("agenda.md", payload["artifacts"])
            self.assertIn("transcript.md", payload["artifacts"])
            self.assertIn("decision.md", payload["artifacts"])
            self.assertIn("meeting.json", payload["artifacts"])
            self.assertIn("lore_lawyer/research.md", payload["research"])
            self.assertIn("lore_lawyer", payload["research_json"])
            self.assertIn("evidence_gate", payload["research_json"]["lore_lawyer"])
            self.assertIn("lore_lawyer.md", payload["return_packets"])
            self.assertEqual(payload["tabs"], ["lobby", "live", "board", "archive"])
            self.assertEqual(payload["tab_labels"]["lobby"], "로비")
            self.assertEqual(payload["tab_labels"]["live"], "실황")
            self.assertEqual(payload["tab_labels"]["board"], "작전판")
            self.assertEqual(payload["tab_labels"]["archive"], "아카이브")


    def test_workroom_queue_endpoint_returns_safe_presence_without_artifact_bodies(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            (meeting_dir / "shared_memory").mkdir()
            (meeting_dir / "return_packets").mkdir()
            (meeting_dir / "review_checkpoints").mkdir()
            (meeting_dir / "private_research" / "architect").mkdir(parents=True)
            (meeting_dir / "meeting.json").write_text(
                json.dumps(
                    {
                        "meeting_id": "m1",
                        "topic": "Safe workroom queue",
                        "question": "No raw bodies?",
                        "roles": [],
                        "debate_rounds": [],
                        "moderator_synthesis": {},
                        "live_status": "running",
                    }
                ),
                encoding="utf-8",
            )
            (meeting_dir / "transcript.md").write_text("SECRET_TRANSCRIPT_BODY", encoding="utf-8")
            (meeting_dir / "decision.md").write_text("SECRET_DECISION_BODY", encoding="utf-8")
            (meeting_dir / "shared_memory" / "rolling-summary.md").write_text(
                "SECRET_MEMORY_BODY",
                encoding="utf-8",
            )
            (meeting_dir / "return_packets" / "architect.md").write_text(
                "SECRET_RETURN_PACKET",
                encoding="utf-8",
            )
            (meeting_dir / "review_checkpoints" / "checkpoint-1.md").write_text(
                "SECRET_REVIEW_REPLY",
                encoding="utf-8",
            )
            (meeting_dir / "review_checkpoints" / "checkpoint-1.json").write_text(
                json.dumps({"secret": "SECRET_REVIEW_JSON"}),
                encoding="utf-8",
            )
            (meeting_dir / "task_scope_report.json").write_text(
                json.dumps(
                    {
                        "version": "task_scope_report_v0",
                        "summary": "scope_overlap_evidence",
                        "overlap_count": 2,
                        "candidate_count_total": 5,
                        "overlaps": [
                            {
                                "kind": "file",
                                "token": "agentsassemble/gui.py",
                                "role_ids": [
                                    "SESSION_TOKEN_abc123",
                                    "sk-secret123456",
                                    "auth_ref_prod",
                                ],
                                "display_names": ["Architect", "Critic"],
                                "task_body": "SECRET_TASK_BODY",
                            },
                            {
                                "kind": "file",
                                "token": "SESSION_TOKEN_abc123/foo.py",
                            },
                            {
                                "kind": "file",
                                "token": "auth_ref_prod/config.py",
                            },
                            {
                                "kind": "file",
                                "token": "--api-key/value.py",
                            },
                            {
                                "kind": "file",
                                "token": "/Users/seinel/private.py",
                                "role_ids": ["leaker"],
                                "display_names": ["/Users/seinel/private-name"],
                            },
                        ],
                        "overlaps_truncated": False,
                        "raw_prompt": "SECRET_PROMPT",
                    }
                ),
                encoding="utf-8",
            )
            (meeting_dir / "private_research" / "architect" / "research.md").write_text(
                "SECRET_RESEARCH_BODY",
                encoding="utf-8",
            )

            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/meetings/m1/workroom-queue",
                    timeout=4,
                ) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            serialized = json.dumps(payload, ensure_ascii=False)
            for forbidden in (
                "SECRET_TRANSCRIPT_BODY",
                "SECRET_DECISION_BODY",
                "SECRET_MEMORY_BODY",
                "SECRET_RETURN_PACKET",
                "SECRET_REVIEW_REPLY",
                "SECRET_REVIEW_JSON",
                "SECRET_RESEARCH_BODY",
                "SECRET_TASK_BODY",
                "SECRET_PROMPT",
                "SESSION_TOKEN_abc123",
                "sk-secret123456",
                "auth_ref_prod",
                "--api-key",
                "Architect",
                "Critic",
                "/Users/seinel/private.py",
                "/Users/seinel/private-name",
            ):
                self.assertNotIn(forbidden, serialized)
            self.assertEqual(payload["meeting_id"], "m1")
            self.assertTrue(payload["artifacts"]["transcript.md"]["available"])
            self.assertTrue(payload["artifacts"]["decision.md"]["available"])
            self.assertTrue(payload["artifacts"]["shared_memory/rolling-summary.md"]["available"])
            self.assertFalse(payload["artifacts"]["shared_memory/action-items.md"]["available"])
            self.assertEqual(payload["return_packets"]["count"], 1)
            self.assertEqual(payload["review_checkpoints"]["count"], 1)
            self.assertEqual(payload["task_scope"]["overlap_count"], 2)
            self.assertEqual(payload["task_scope"]["candidate_count_total"], 5)
            self.assertEqual(
                payload["task_scope"]["overlaps"],
                [
                    {
                        "kind": "file",
                        "token": "agentsassemble/gui.py",
                    }
                ],
            )
            self.assertTrue(payload["task_scope"]["overlaps_truncated"])
            self.assertIn("lifecycle", payload)


    def test_workroom_queue_task_scope_rejects_nonfinite_counts_and_keeps_safe_overlap_warning(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            (meeting_dir / "meeting.json").write_text(
                json.dumps(
                    {
                        "meeting_id": "m1",
                        "topic": "topic",
                        "question": "question",
                        "roles": [],
                        "debate_rounds": [],
                        "moderator_synthesis": {},
                        "live_status": "running",
                    }
                ),
                encoding="utf-8",
            )
            (meeting_dir / "task_scope_report.json").write_text(
                json.dumps(
                    {
                        "summary": "scope_overlap_evidence",
                        "overlap_count": float("inf"),
                        "candidate_count_total": float("inf"),
                        "overlaps": [
                            {
                                "kind": "file",
                                "token": "agentsassemble/gui.py",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/meetings/m1/workroom-queue",
                    timeout=4,
                ) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            serialized = json.dumps(payload)
            self.assertNotIn("Infinity", serialized)
            self.assertEqual(payload["task_scope"]["candidate_count_total"], 0)
            self.assertEqual(payload["task_scope"]["overlap_count"], 1)
            self.assertEqual(
                payload["task_scope"]["overlaps"],
                [{"kind": "file", "token": "agentsassemble/gui.py"}],
            )


    def test_live_agent_operations_endpoint_filters_operation_target_and_status(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            append_live_agent_operation(
                root,
                operation="session.start",
                status="success",
                target_id="resident-a",
                summary="matching operation",
            )
            append_live_agent_operation(
                root,
                operation="session.start",
                status="failed",
                target_id="resident-a",
                summary="wrong status",
            )
            append_live_agent_operation(
                root,
                operation="session.resume",
                status="success",
                target_id="resident-a",
                summary="wrong operation",
            )
            append_live_agent_operation(
                root,
                operation="session.start",
                status="success",
                target_id="resident-b",
                summary="wrong target",
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(
                    (
                        f"http://127.0.0.1:{server.server_port}/api/live-agent-operations"
                        "?limit=1&operation=session.start&target_id=resident-a&status=success&scan_limit=10"
                    ),
                    timeout=4,
                ) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual([operation["summary"] for operation in payload["operations"]], ["matching operation"])
        self.assertEqual(payload["operation"], "session.start")
        self.assertEqual(payload["target_id"], "resident-a")
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["scan_limit"], 10)
        self.assertFalse(payload["truncated"])
