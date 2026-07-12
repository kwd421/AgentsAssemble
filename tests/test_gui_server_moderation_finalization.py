from tests.gui_server_test_support import (
    HTTPError,
    Path,
    Request,
    ThreadingHTTPServer,
    _make_handler,
    append_live_event,
    build_meeting_payload,
    connect_live_agent_payload,
    json,
    live_agent_official_turn_payload,
    live_agent_turn_request_payload,
    read_live_events,
    tempfile,
    threading,
    time,
    unittest,
    urlopen,
    write_live_state,
)


class GuiServerModerationFinalizationTests(unittest.TestCase):

    def test_live_agent_official_turn_round_validates_before_append(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(
                meeting_dir,
                {
                    "meeting_id": "m1",
                    "roles": [
                        {"id": "architect", "display_name": "Architect"},
                        {"id": "critic", "display_name": "Critic"},
                    ],
                    "meeting_template": {
                        "rounds": [
                            {
                                "id": "round_1",
                                "instruction": "Template instruction",
                                "turn_control": {"selection": "all_roles"},
                            }
                        ]
                    },
                    "agent_bindings": [
                        {"role_id": "architect", "agent_id": "agent-a"},
                        {"role_id": "critic", "agent_id": "agent-b"},
                    ],
                },
            )
            connect_live_agent_payload(root, {"agent_id": "agent-a", "display_name": "Agent A", "meeting_id": "m1"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                round_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/meetings/m1/live-agent-turns/round",
                    data=json.dumps({"round_id": "round_1", "content": "private round instruction"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as error:
                    urlopen(round_request, timeout=4)
                error.exception.close()
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(error.exception.code, 400)
            self.assertEqual(read_live_events(meeting_dir, limit=None), [])
            round_operations = [item for item in operations["operations"] if item["operation"] == "official_turn.round"]
            self.assertEqual(round_operations[0]["status"], "failed")
            self.assertNotIn("private round instruction", json.dumps(operations["operations"], ensure_ascii=False))


    def test_live_agent_play_preset_endpoint_enqueues_turn_without_process_start(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(
                meeting_dir,
                {
                    "meeting_id": "m1",
                    "meeting_mode": "free_chat",
                    "roles": [{"id": "architect", "display_name": "Architect"}],
                    "agent_bindings": [{"role_id": "architect", "agent_id": "agent-a"}],
                },
            )
            connect_live_agent_payload(root, {"agent_id": "agent-a", "display_name": "Agent A", "meeting_id": "m1"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                preset_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/meetings/m1/live-agent-turns/preset",
                    data=json.dumps(
                        {
                            "preset_id": "meme_debate_argument",
                            "role_ids": ["architect"],
                            "timeout_seconds": 0,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(preset_request, timeout=4) as response:
                    result = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(result["preset_id"], "meme_debate_argument")
            self.assertEqual(result["turn_count"], 1)
            self.assertEqual(result["status"], "timeout")
            live_events = read_live_events(meeting_dir, limit=None)
            self.assertEqual(live_events[0]["kind"], "live_agent_turn_request")
            self.assertEqual(live_events[0]["target_agent_id"], "agent-a")
            operation = [item for item in operations["operations"] if item["operation"] == "official_turn.preset"][0]
            self.assertEqual(operation["status"], "degraded")
            operations_text = json.dumps(operations["operations"], ensure_ascii=False)
            self.assertNotIn("심판처럼 판정하지 말고", operations_text)
            self.assertFalse((root / "live-agent-runs" / "processes.json").exists())


    def test_live_agent_review_checkpoint_answers_ready_resident_agents_without_official_record(self):
        class ReadySupervisor:
            def snapshot_groups(self):
                return [
                    {
                        "group_id": "resident-main",
                        "status": "running",
                        "meeting_id": "m1",
                        "agents": [{"agent_id": "agent-a"}, {"agent_id": "agent-b"}],
                    }
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(
                meeting_dir,
                {
                    "meeting_id": "m1",
                    "topic": "resident review",
                    "live_status": "running",
                    "roles": [
                        {"id": "architect", "display_name": "Architect"},
                        {"id": "critic", "display_name": "Critic"},
                    ],
                    "agent_bindings": [
                        {"role_id": "architect", "agent_id": "agent-a", "provider_id": "local-cli"},
                        {"role_id": "critic", "agent_id": "agent-b", "provider_id": "local-cli"},
                    ],
                    "provider_configs": {"local-cli": {"kind": "local_cli", "display_name": "Local CLI"}},
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

            responder_done = threading.Event()

            def answer_review_requests():
                answered = set()
                deadline = time.time() + 3.0
                while time.time() < deadline and len(answered) < 2:
                    for event in read_live_events(meeting_dir, limit=None):
                        if event.get("kind") != "live_agent_turn_request":
                            continue
                        if event.get("review_checkpoint_id") != "checkpoint-1":
                            continue
                        event_id = str(event.get("id") or "")
                        agent_id = str(event.get("target_agent_id") or "")
                        if not event_id or not agent_id or event_id in answered:
                            continue
                        live_agent_official_turn_payload(
                            root,
                            agent_id,
                            {
                                "meeting_id": "m1",
                                "source_event_id": event_id,
                                "content": f"secret review reply from {agent_id}",
                            },
                        )
                        answered.add(event_id)
                    time.sleep(0.01)
                responder_done.set()

            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=ReadySupervisor()))
            server_thread = threading.Thread(target=server.serve_forever, daemon=True)
            response_thread = threading.Thread(target=answer_review_requests, daemon=True)
            server_thread.start()
            response_thread.start()
            try:
                checkpoint_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/meetings/m1/review-checkpoints",
                    data=json.dumps(
                        {
                            "group_id": "resident main",
                            "checkpoint_id": "checkpoint-1",
                            "content": "secret prompt for reviewers",
                            "timeout_seconds": 2.0,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(checkpoint_request, timeout=4) as response:
                    checkpoint = json.loads(response.read().decode("utf-8"))
                responder_done.wait(timeout=2)
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(checkpoint["status"], "answered")
            self.assertEqual(checkpoint["checkpoint_id"], "checkpoint-1")
            self.assertEqual(checkpoint["turn_count"], 2)
            self.assertEqual(checkpoint["answered_count"], 2)
            request_events = [
                event
                for event in read_live_events(meeting_dir, limit=None)
                if event.get("kind") == "live_agent_turn_request" and event.get("review_checkpoint_id") == "checkpoint-1"
            ]
            reply_events = [
                event
                for event in read_live_events(meeting_dir, limit=None)
                if event.get("kind") == "message" and event.get("review_checkpoint_id") == "checkpoint-1"
            ]
            self.assertEqual(len(request_events), 2)
            self.assertEqual(len(reply_events), 2)
            for event in request_events + reply_events:
                self.assertEqual(event["channel"], "review")
                self.assertFalse(event["official_record"])
            transcript = build_meeting_payload(meeting_dir)["artifacts"]["transcript.md"]
            self.assertNotIn("secret prompt for reviewers", transcript)
            self.assertNotIn("secret review reply", transcript)
            checkpoint_markdown = meeting_dir / "review_checkpoints" / "checkpoint-1.md"
            checkpoint_json = meeting_dir / "review_checkpoints" / "checkpoint-1.json"
            self.assertTrue(checkpoint_markdown.exists())
            self.assertTrue(checkpoint_json.exists())
            artifact_text = checkpoint_markdown.read_text(encoding="utf-8")
            artifact_json = json.loads(checkpoint_json.read_text(encoding="utf-8"))
            self.assertIn("secret prompt for reviewers", artifact_text)
            self.assertIn("secret review reply from agent-a", artifact_text)
            self.assertEqual(artifact_json["checkpoint_id"], "checkpoint-1")
            self.assertEqual(artifact_json["status"], "answered")
            self.assertEqual(artifact_json["answered_count"], 2)
            self.assertEqual(artifact_json["results"][0]["request"]["content"], "secret prompt for reviewers")
            self.assertIn("secret review reply", artifact_json["results"][0]["reply"]["content"])
            payload = build_meeting_payload(meeting_dir)
            self.assertIn("checkpoint-1.md", payload["review_checkpoints"])
            self.assertIn("secret review reply from agent-a", payload["review_checkpoints"]["checkpoint-1.md"])
            artifact_events = [
                event
                for event in read_live_events(meeting_dir, limit=None)
                if event.get("artifact_kind") == "review_checkpoint"
            ]
            self.assertEqual(len(artifact_events), 1)
            self.assertEqual(artifact_events[0]["channel"], "review")
            self.assertFalse(artifact_events[0]["official_record"])
            self.assertEqual(artifact_events[0]["artifact_path"], "review_checkpoints/checkpoint-1.md")
            self.assertNotIn("secret prompt for reviewers", json.dumps(artifact_events, ensure_ascii=False))
            self.assertNotIn("secret review reply", json.dumps(artifact_events, ensure_ascii=False))
            checkpoint_operations = [item for item in operations["operations"] if item["operation"] == "review.checkpoint"]
            self.assertEqual(checkpoint_operations[-1]["status"], "success")
            self.assertEqual(checkpoint_operations[-1]["details"]["checkpoint_id"], "checkpoint-1")
            self.assertEqual(checkpoint_operations[-1]["details"]["answered_count"], 2)
            operation_blob = json.dumps(checkpoint_operations, ensure_ascii=False)
            self.assertNotIn("secret prompt for reviewers", operation_blob)
            self.assertNotIn("secret review reply", operation_blob)


    def test_live_agent_review_checkpoint_degrades_without_ready_session_and_omits_prompt_content(self):
        class MissingGroupSupervisor:
            def snapshot_groups(self):
                return []

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(
                meeting_dir,
                {
                    "meeting_id": "m1",
                    "topic": "resident review",
                    "live_status": "running",
                    "roles": [{"id": "architect", "display_name": "Architect"}],
                    "agent_bindings": [{"role_id": "architect", "agent_id": "agent-a", "provider_id": "local-cli"}],
                    "provider_configs": {"local-cli": {"kind": "local_cli", "display_name": "Local CLI"}},
                },
            )
            connect_live_agent_payload(root, {"agent_id": "agent-a", "display_name": "Agent A", "meeting_id": "m1"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root, process_supervisor=MissingGroupSupervisor()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                checkpoint_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/meetings/m1/review-checkpoints",
                    data=json.dumps(
                        {
                            "group_id": "resident-main",
                            "checkpoint_id": "checkpoint-1",
                            "content": "secret unavailable review prompt",
                            "timeout_seconds": 0,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(checkpoint_request, timeout=4) as response:
                    checkpoint = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(checkpoint["status"], "degraded")
            self.assertEqual(checkpoint["reason"], "session_not_ready")
            self.assertEqual(read_live_events(meeting_dir, limit=None), [])
            checkpoint_operations = [item for item in operations["operations"] if item["operation"] == "review.checkpoint"]
            self.assertEqual(checkpoint_operations[-1]["status"], "degraded")
            self.assertEqual(checkpoint_operations[-1]["details"]["result_status"], "degraded")
            self.assertEqual(checkpoint_operations[-1]["details"]["reason"], "session_not_ready")
            self.assertNotIn("secret unavailable review prompt", json.dumps(checkpoint_operations, ensure_ascii=False))


    def test_live_agent_review_checkpoint_reply_endpoint_records_review_operation(self):
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
                    "display_name": "Agent A",
                    "content": "secret review prompt",
                    "channel": "review",
                    "official_record": False,
                    "review_checkpoint_id": "checkpoint-1",
                },
            )
            connect_live_agent_payload(root, {"agent_id": "agent-a", "display_name": "Agent A", "meeting_id": "m1"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                reply_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agents/agent-a/official-turn",
                    data=json.dumps(
                        {
                            "meeting_id": "m1",
                            "source_event_id": request_event["id"],
                            "content": "secret review reply",
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(reply_request, timeout=4) as response:
                    replied = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(replied["event"]["channel"], "review")
            self.assertFalse(replied["event"]["official_record"])
            self.assertEqual(replied["event"]["review_checkpoint_id"], "checkpoint-1")
            operation_names = [item["operation"] for item in operations["operations"]]
            self.assertIn("review.reply", operation_names)
            self.assertNotIn("official_turn.reply", operation_names)
            review_operations = [item for item in operations["operations"] if item["operation"] == "review.reply"]
            self.assertEqual(review_operations[-1]["details"]["review_checkpoint_id"], "checkpoint-1")
            operations_text = json.dumps(operations["operations"], ensure_ascii=False)
            self.assertNotIn("secret review prompt", operations_text)
            self.assertNotIn("secret review reply", operations_text)


    def test_live_agent_official_turn_request_rejects_meeting_id_path_traversal(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            (root / "meetings").mkdir(parents=True)
            outside_meeting = Path(temp_dir) / "escape-target"
            outside_meeting.mkdir(parents=True)
            write_live_state(outside_meeting, {"meeting_id": "../../escape-target", "live_status": "running"})
            connect_live_agent_payload(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "meeting_id": "../../escape-target",
                    "engagement_mode": "moderator_called",
                },
            )

            with self.assertRaises(ValueError):
                live_agent_turn_request_payload(root, "../../escape-target", {"agent_id": "agent-a", "content": "escape"})

            self.assertFalse((outside_meeting / "live_events.jsonl").exists())


    def test_live_agent_official_turn_reply_is_idempotent_for_same_request(self):
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
                    "display_name": "Agent A",
                    "content": "공식 발언 차례",
                },
            )
            connect_live_agent_payload(root, {"agent_id": "agent-a", "display_name": "Agent A", "meeting_id": "m1"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                events = []
                for content in ("첫 공식 답변", "중복 공식 답변"):
                    reply_request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agents/agent-a/official-turn",
                        data=json.dumps(
                            {
                                "meeting_id": "m1",
                                "source_event_id": request_event["id"],
                                "content": content,
                            }
                        ).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urlopen(reply_request, timeout=4) as response:
                        events.append(json.loads(response.read().decode("utf-8"))["event"])
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(events[0]["id"], events[1]["id"])
            official_replies = [
                event
                for event in read_live_events(meeting_dir)
                if event.get("kind") == "message" and event.get("source_event_id") == request_event["id"]
            ]
            self.assertEqual(len(official_replies), 1)
            transcript = build_meeting_payload(meeting_dir)["artifacts"]["transcript.md"]
            self.assertEqual(transcript.count("첫 공식 답변"), 1)
            self.assertNotIn("중복 공식 답변", transcript)


    def test_live_agent_finalize_meeting_endpoint_writes_artifacts_and_operation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(
                meeting_dir,
                {
                    "meeting_id": "m1",
                    "question": "Finalize resident meeting?",
                    "display_question": "Finalize resident meeting?",
                    "topic": "runtime",
                    "display_topic": "runtime",
                    "meeting_mode": "debate",
                    "moderator": {"enabled": True},
                    "roles": [{"id": "architect", "display_name": "Architect", "lens": "shape"}],
                    "meeting_template": {
                        "display_name": "Resident live",
                        "rounds": [
                            {
                                "id": "round_1",
                                "title": "Round 1",
                                "instruction": "Answer.",
                                "turn_control": {"selection": "all_roles"},
                            }
                        ],
                    },
                    "research_depth": {"name": "resident_live"},
                    "research_steering": {"prompt": None},
                    "memory_context": {"recent_episodes": [], "agent_memories": {}},
                    "memory_input": {"research_summaries": []},
                    "agent_bindings": [{"role_id": "architect", "agent_id": "agent-a", "provider_id": "local-cli"}],
                    "provider_configs": {"local-cli": {"kind": "local_cli", "display_name": "Local CLI"}},
                    "debate_rounds": [],
                    "moderator_synthesis": {},
                    "decision_gate": {},
                    "artifacts": {"agenda": "agenda.md"},
                    "live_status": "running",
                },
            )
            request_event = append_live_event(
                meeting_dir,
                {
                    "kind": "live_agent_turn_request",
                    "meeting_id": "m1",
                    "target_agent_id": "agent-a",
                    "role_id": "architect",
                    "display_name": "Architect",
                    "content": "private prompt",
                    "turn_id": "round_1:0:architect",
                    "turn_index": 0,
                },
            )
            append_live_event(
                meeting_dir,
                {
                    "kind": "message",
                    "meeting_id": "m1",
                    "actor_id": "agent-a",
                    "target_agent_id": "agent-a",
                    "source_event_id": request_event["id"],
                    "role_id": "architect",
                    "display_name": "Architect",
                    "content": "official answer for final artifacts",
                    "turn_id": "round_1:0:architect",
                    "turn_index": 0,
                },
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                finalize_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/meetings/m1/finalize",
                    data=json.dumps({}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(finalize_request, timeout=4) as response:
                    finalized = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(finalized["status"], "finalized")
            self.assertEqual(finalized["official_event_count"], 1)
            payload = build_meeting_payload(meeting_dir)
            self.assertIn("official answer for final artifacts", payload["artifacts"]["transcript.md"])
            self.assertNotIn("private prompt", payload["artifacts"]["transcript.md"])
            self.assertEqual(payload["meeting"]["live_status"], "complete")
            self.assertTrue((meeting_dir / "decision.md").exists())
            self.assertEqual(operations["operations"][0]["operation"], "meeting.finalize")
            self.assertEqual(operations["operations"][0]["status"], "success")
            self.assertEqual(operations["operations"][0]["details"]["official_event_count"], 1)
            operations_text = json.dumps(operations["operations"], ensure_ascii=False)
            self.assertNotIn("official answer for final artifacts", operations_text)
            self.assertNotIn("private prompt", operations_text)


    def test_live_agent_finalize_meeting_endpoint_closes_pending_turns_without_prompt_leak(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(
                meeting_dir,
                {
                    "meeting_id": "m1",
                    "question": "Finalize resident meeting?",
                    "display_question": "Finalize resident meeting?",
                    "topic": "runtime",
                    "display_topic": "runtime",
                    "meeting_mode": "debate",
                    "moderator": {"enabled": True},
                    "roles": [
                        {"id": "architect", "display_name": "Architect", "lens": "shape"},
                        {"id": "critic", "display_name": "Critic", "lens": "risk"},
                    ],
                    "meeting_template": {
                        "display_name": "Resident live",
                        "rounds": [
                            {
                                "id": "round_1",
                                "title": "Round 1",
                                "instruction": "Answer.",
                                "turn_control": {"selection": "all_roles"},
                            }
                        ],
                    },
                    "research_depth": {"name": "resident_live"},
                    "research_steering": {"prompt": None},
                    "memory_context": {"recent_episodes": [], "agent_memories": {}},
                    "memory_input": {"research_summaries": []},
                    "agent_bindings": [
                        {"role_id": "architect", "agent_id": "agent-a", "provider_id": "local-cli"},
                        {"role_id": "critic", "agent_id": "agent-b", "provider_id": "local-cli"},
                    ],
                    "provider_configs": {"local-cli": {"kind": "local_cli", "display_name": "Local CLI"}},
                    "debate_rounds": [],
                    "moderator_synthesis": {},
                    "decision_gate": {},
                    "artifacts": {"agenda": "agenda.md"},
                    "live_status": "running",
                },
            )
            answered_request = append_live_event(
                meeting_dir,
                {
                    "kind": "live_agent_turn_request",
                    "meeting_id": "m1",
                    "target_agent_id": "agent-a",
                    "role_id": "architect",
                    "display_name": "Architect",
                    "content": "private prompt",
                    "turn_id": "round_1:0:architect",
                    "turn_index": 0,
                },
            )
            append_live_event(
                meeting_dir,
                {
                    "kind": "message",
                    "meeting_id": "m1",
                    "actor_id": "agent-a",
                    "target_agent_id": "agent-a",
                    "source_event_id": answered_request["id"],
                    "role_id": "architect",
                    "display_name": "Architect",
                    "content": "official answer for final artifacts",
                    "turn_id": "round_1:0:architect",
                    "turn_index": 0,
                },
            )
            pending_request = append_live_event(
                meeting_dir,
                {
                    "kind": "live_agent_turn_request",
                    "meeting_id": "m1",
                    "target_agent_id": "agent-b",
                    "role_id": "critic",
                    "display_name": "Critic",
                    "content": "secret pending prompt",
                    "turn_id": "round_1:1:critic",
                    "turn_index": 1,
                },
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                finalize_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/meetings/m1/finalize",
                    data=json.dumps({"close_pending": True}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(finalize_request, timeout=4) as response:
                    finalized = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(finalized["status"], "finalized")
            self.assertEqual(finalized["cancelled_pending_count"], 1)
            self.assertEqual(finalized["cancelled_turn_request_ids"], [pending_request["id"]])
            cancellation_events = [
                event for event in read_live_events(meeting_dir, limit=None) if event.get("kind") == "live_agent_turn_cancelled"
            ]
            self.assertEqual(len(cancellation_events), 1)
            self.assertEqual(cancellation_events[0]["source_event_id"], pending_request["id"])
            self.assertEqual(operations["operations"][0]["operation"], "meeting.finalize")
            self.assertEqual(operations["operations"][0]["status"], "success")
            self.assertEqual(operations["operations"][0]["details"]["cancelled_pending_count"], 1)
            operations_text = json.dumps(operations["operations"], ensure_ascii=False)
            self.assertNotIn("secret pending prompt", operations_text)


    def test_live_agent_finalize_meeting_endpoint_failure_operation_omits_prompt_content(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(
                meeting_dir,
                {
                    "meeting_id": "m1",
                    "question": "Finalize resident meeting?",
                    "display_question": "Finalize resident meeting?",
                    "topic": "runtime",
                    "display_topic": "runtime",
                    "meeting_mode": "debate",
                    "moderator": {"enabled": True},
                    "roles": [{"id": "architect", "display_name": "Architect", "lens": "shape"}],
                    "meeting_template": {
                        "display_name": "Resident live",
                        "rounds": [
                            {
                                "id": "round_1",
                                "title": "Round 1",
                                "instruction": "Answer.",
                                "turn_control": {"selection": "all_roles"},
                            }
                        ],
                    },
                    "research_depth": {"name": "resident_live"},
                    "research_steering": {"prompt": None},
                    "memory_context": {"recent_episodes": [], "agent_memories": {}},
                    "memory_input": {"research_summaries": []},
                    "agent_bindings": [{"role_id": "architect", "agent_id": "agent-a", "provider_id": "local-cli"}],
                    "provider_configs": {"local-cli": {"kind": "local_cli", "display_name": "Local CLI"}},
                    "debate_rounds": [],
                    "moderator_synthesis": {},
                    "decision_gate": {},
                    "artifacts": {"agenda": "agenda.md"},
                    "live_status": "running",
                },
            )
            request_event = append_live_event(
                meeting_dir,
                {
                    "kind": "live_agent_turn_request",
                    "meeting_id": "m1",
                    "target_agent_id": "agent-a",
                    "role_id": "architect",
                    "display_name": "Architect",
                    "content": "secret pending prompt",
                    "turn_id": "round_1:0:architect",
                    "turn_index": 0,
                },
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                finalize_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/meetings/m1/finalize",
                    data=json.dumps({}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as context:
                    urlopen(finalize_request, timeout=4)
                context.exception.read()
                context.exception.close()
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(operations["operations"][0]["operation"], "meeting.finalize")
            self.assertEqual(operations["operations"][0]["status"], "failed")
            self.assertIn(request_event["id"], operations["operations"][0]["error"])
            operations_text = json.dumps(operations["operations"], ensure_ascii=False)
            self.assertNotIn("secret pending prompt", operations_text)


    def test_live_agent_official_turn_reply_ignores_explicit_nonofficial_same_source_message(self):
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
                    "content": "공식 발언 차례",
                },
            )
            nonofficial_event = {
                "id": "nonofficial-same-source",
                "kind": "message",
                "meeting_id": "m1",
                "channel": "system",
                "official_record": False,
                "actor_id": "agent-a",
                "source_event_id": request_event["id"],
                "content": "비공식 상태 메모",
            }
            with (meeting_dir / "live_events.jsonl").open("a", encoding="utf-8") as file:
                file.write(json.dumps(nonofficial_event, ensure_ascii=False, sort_keys=True) + "\n")
            connect_live_agent_payload(root, {"agent_id": "agent-a", "display_name": "Agent A", "meeting_id": "m1"})

            result = live_agent_official_turn_payload(
                root,
                "agent-a",
                {
                    "meeting_id": "m1",
                    "source_event_id": request_event["id"],
                    "content": "정식 공식 답변",
                },
            )

            self.assertNotEqual(result["event"]["id"], nonofficial_event["id"])
            self.assertEqual(result["event"]["content"], "정식 공식 답변")
            self.assertEqual(result["event"]["channel"], "official")
            self.assertTrue(result["event"]["official_record"])
            official_replies = [
                event
                for event in read_live_events(meeting_dir, limit=None)
                if event.get("kind") == "message"
                and event.get("channel") == "official"
                and event.get("official_record") is True
                and event.get("source_event_id") == request_event["id"]
            ]
            self.assertEqual(len(official_replies), 1)


    def test_live_agent_official_turn_reply_rejects_meeting_the_agent_is_not_attached_to(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for meeting_id in ("m1", "m2"):
                meeting_dir = root / "meetings" / meeting_id
                meeting_dir.mkdir(parents=True)
                write_live_state(meeting_dir, {"meeting_id": meeting_id, "topic": "runtime", "live_status": "running"})
            other_request = append_live_event(
                root / "meetings" / "m2",
                {
                    "kind": "live_agent_turn_request",
                    "meeting_id": "m2",
                    "target_agent_id": "agent-a",
                    "content": "다른 회의 요청",
                },
            )
            connect_live_agent_payload(root, {"agent_id": "agent-a", "display_name": "Agent A", "meeting_id": "m1"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                reply_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agents/agent-a/official-turn",
                    data=json.dumps(
                        {
                            "meeting_id": "m2",
                            "source_event_id": other_request["id"],
                            "content": "잘못된 회의 답변",
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as context:
                    urlopen(reply_request, timeout=4)
                error = context.exception
                error.read()
                error.close()
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(error.code, 400)
            official_replies = [
                event
                for event in read_live_events(root / "meetings" / "m2")
                if event.get("kind") == "message" and event.get("source_event_id") == other_request["id"]
            ]
            self.assertEqual(official_replies, [])


    def test_live_agent_official_turn_reply_finds_request_beyond_latest_live_event_tail(self):
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
                    "display_name": "Agent A",
                    "content": "오래된 요청",
                },
            )
            for index in range(201):
                append_live_event(meeting_dir, {"kind": "status", "content": f"중간 상태 {index}"})
            connect_live_agent_payload(root, {"agent_id": "agent-a", "display_name": "Agent A", "meeting_id": "m1"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                reply_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agents/agent-a/official-turn",
                    data=json.dumps(
                        {
                            "meeting_id": "m1",
                            "source_event_id": request_event["id"],
                            "content": "늦은 공식 답변",
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(reply_request, timeout=4) as response:
                    replied = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(replied["event"]["source_event_id"], request_event["id"])
            self.assertEqual(replied["event"]["content"], "늦은 공식 답변")


    def test_live_agent_official_turn_reply_finds_existing_reply_beyond_latest_live_event_tail(self):
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
                    "content": "공식 발언 차례",
                },
            )
            first_reply = append_live_event(
                meeting_dir,
                {
                    "kind": "message",
                    "meeting_id": "m1",
                    "actor_id": "agent-a",
                    "target_agent_id": "agent-a",
                    "source_event_id": request_event["id"],
                    "content": "이미 기록된 공식 답변",
                },
            )
            for index in range(201):
                append_live_event(meeting_dir, {"kind": "status", "content": f"후속 상태 {index}"})
            connect_live_agent_payload(root, {"agent_id": "agent-a", "display_name": "Agent A", "meeting_id": "m1"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                reply_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agents/agent-a/official-turn",
                    data=json.dumps(
                        {
                            "meeting_id": "m1",
                            "source_event_id": request_event["id"],
                            "content": "중복 공식 답변",
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(reply_request, timeout=4) as response:
                    replied = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(replied["event"]["id"], first_reply["id"])
            official_replies = [
                event
                for event in read_live_events(meeting_dir, limit=None)
                if event.get("kind") == "message" and event.get("source_event_id") == request_event["id"]
            ]
            self.assertEqual(len(official_replies), 1)


    def test_live_agent_official_turn_reply_requires_matching_request(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(meeting_dir, {"meeting_id": "m1", "topic": "runtime", "live_status": "running"})
            other_request = append_live_event(
                meeting_dir,
                {
                    "kind": "live_agent_turn_request",
                    "meeting_id": "m1",
                    "target_agent_id": "agent-b",
                    "content": "다른 에이전트 차례",
                },
            )
            connect_live_agent_payload(root, {"agent_id": "agent-a", "display_name": "Agent A", "meeting_id": "m1"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                reply_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agents/agent-a/official-turn",
                    data=json.dumps(
                        {
                            "meeting_id": "m1",
                            "source_event_id": other_request["id"],
                            "content": "잘못된 답변",
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as context:
                    urlopen(reply_request, timeout=4)
                error = context.exception
                error.read()
                error.close()
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(error.code, 400)
