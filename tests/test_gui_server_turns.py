from tests.gui_server_test_support import (
    Path,
    Request,
    ThreadingHTTPServer,
    _live_agent_turn_rounds_payload_locked,
    _make_handler,
    append_live_event,
    build_meeting_payload,
    connect_live_agent_payload,
    json,
    live_agent_turn_round_payload,
    live_agent_turn_rounds_payload,
    live_agent_turn_sequence_payload,
    patch,
    read_live_events,
    tempfile,
    threading,
    time,
    unittest,
    urlopen,
    write_live_state,
)


class GuiServerTurnTests(unittest.TestCase):

    def test_live_agent_official_turn_request_and_reply_stay_out_of_lobby(self):
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
            connect_live_agent_payload(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "meeting_id": "m1",
                    "engagement_mode": "moderator_called",
                    "last_error": "previous official turn failed",
                },
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/meetings/m1/live-agent-turns/request",
                    data=json.dumps(
                        {
                            "agent_id": "agent-a",
                            "role_id": "architect",
                            "display_name": "Agent A",
                            "content": "공식 발언 차례",
                            "turn_id": "round_1:0:architect",
                            "turn_index": 0,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=4) as response:
                    requested = json.loads(response.read().decode("utf-8"))
                reply_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/live-agents/agent-a/official-turn",
                    data=json.dumps(
                        {
                            "meeting_id": "m1",
                            "source_event_id": requested["event"]["id"],
                            "content": "공식 답변\nAction item: Preserve resident shared memory.",
                            "role_id": "payload-override",
                            "display_name": "Payload Override",
                            "turn_id": "round_1:0:architect",
                            "turn_index": 0,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(reply_request, timeout=4) as response:
                    replied = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/lobby", timeout=4) as response:
                    lobby = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
                persisted_agent = json.loads((root / "live_agents.json").read_text(encoding="utf-8"))["agents"][0]
            finally:
                server.shutdown()
                server.server_close()

            request_event = requested["event"]
            reply_event = replied["event"]
            self.assertEqual(request_event["kind"], "live_agent_turn_request")
            self.assertEqual(request_event["channel"], "system")
            self.assertFalse(request_event["official_record"])
            self.assertEqual(request_event["target_agent_id"], "agent-a")
            self.assertEqual(reply_event["kind"], "message")
            self.assertEqual(reply_event["channel"], "official")
            self.assertTrue(reply_event["official_record"])
            self.assertEqual(reply_event["source_event_id"], request_event["id"])
            self.assertEqual(reply_event["actor_id"], "agent-a")
            self.assertEqual(reply_event["role_id"], "architect")
            self.assertEqual(reply_event["display_name"], "Agent A")
            self.assertEqual(reply_event["turn_id"], "round_1:0:architect")
            self.assertEqual(reply_event["engagement_mode"], "moderator_called")
            self.assertEqual(replied["agent"]["last_error"], "")
            self.assertEqual(replied["agent"]["last_observed_live_event_id"], request_event["id"])
            self.assertEqual(persisted_agent["last_error"], "")
            self.assertNotIn("private target-B instruction", [event.get("content") for event in replied["live_events"]])
            self.assertEqual(lobby["events"], [])
            self.assertEqual([item["operation"] for item in operations["operations"]], ["official_turn.request", "official_turn.reply"])
            reply_operation = operations["operations"][1]
            self.assertEqual(reply_operation["details"]["shared_memory_official_event_count"], 1)
            self.assertEqual(reply_operation["details"]["shared_memory_action_item_count"], 1)
            transcript = build_meeting_payload(meeting_dir)["artifacts"]["transcript.md"]
            self.assertIn("공식 답변", transcript)
            self.assertIn(f"- Source event id: {request_event['id']}", transcript)
            self.assertNotIn("공식 발언 차례", transcript)
            self.assertNotIn("private target-B instruction", transcript)
            self.assertFalse((meeting_dir / "transcript.md").exists())
            self.assertTrue((meeting_dir / "shared_memory" / "rolling-summary.md").exists())
            self.assertTrue((meeting_dir / "shared_memory" / "action-items.md").exists())
            shared_memory_text = (meeting_dir / "shared_memory" / "action-items.md").read_text(encoding="utf-8")
            self.assertIn("Preserve resident shared memory.", shared_memory_text)
            self.assertNotIn("공식 발언 차례", shared_memory_text)
            operations_text = json.dumps(operations["operations"], ensure_ascii=False)
            self.assertNotIn("Preserve resident shared memory.", operations_text)


    def test_live_agent_official_turn_call_waits_for_verified_reply(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(meeting_dir, {"meeting_id": "m1", "topic": "runtime", "live_status": "running"})
            connect_live_agent_payload(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "meeting_id": "m1",
                    "engagement_mode": "moderator_called",
                },
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            errors = []

            def answer_when_called():
                try:
                    deadline = time.time() + 2
                    request_event = None
                    while time.time() < deadline:
                        request_event = next(
                            (
                                event
                                for event in read_live_events(meeting_dir, limit=None)
                                if event.get("kind") == "live_agent_turn_request"
                                and event.get("target_agent_id") == "agent-a"
                            ),
                            None,
                        )
                        if request_event is not None:
                            break
                        time.sleep(0.01)
                    self.assertIsNotNone(request_event)
                    reply_request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/live-agents/agent-a/official-turn",
                        data=json.dumps(
                            {
                                "meeting_id": "m1",
                                "source_event_id": request_event["id"],
                                "content": "검증된 공식 답변",
                            }
                        ).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urlopen(reply_request, timeout=4) as response:
                        response.read()
                except Exception as error:  # pragma: no cover - failure is asserted below
                    errors.append(error)

            responder = threading.Thread(target=answer_when_called, daemon=True)
            responder.start()
            try:
                call_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/meetings/m1/live-agent-turns/call",
                    data=json.dumps(
                        {
                            "agent_id": "agent-a",
                            "role_id": "architect",
                            "display_name": "Agent A",
                            "content": "공식 발언 차례",
                            "timeout_seconds": 2,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(call_request, timeout=5) as response:
                    called = json.loads(response.read().decode("utf-8"))
                responder.join(timeout=2)
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/lobby", timeout=4) as response:
                    lobby = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            if errors:
                raise errors[0]
            self.assertEqual(called["status"], "answered")
            self.assertEqual(called["request_event"]["kind"], "live_agent_turn_request")
            self.assertEqual(called["reply_event"]["kind"], "message")
            self.assertEqual(called["reply_event"]["actor_id"], "agent-a")
            self.assertEqual(called["reply_event"]["source_event_id"], called["request_event"]["id"])
            self.assertTrue(called["reply_event"]["official_record"])
            self.assertEqual(called["reply_event"]["content"], "검증된 공식 답변")
            self.assertEqual(lobby["events"], [])
            call_operations = [item for item in operations["operations"] if item["operation"] == "official_turn.call"]
            self.assertEqual(len(call_operations), 1)
            self.assertEqual(call_operations[0]["status"], "success")
            self.assertNotIn("공식 발언 차례", json.dumps(call_operations, ensure_ascii=False))
            self.assertNotIn("검증된 공식 답변", json.dumps(call_operations, ensure_ascii=False))
            transcript = build_meeting_payload(meeting_dir)["artifacts"]["transcript.md"]
            self.assertIn("검증된 공식 답변", transcript)
            self.assertIn(f"- Source event id: {called['request_event']['id']}", transcript)
            self.assertNotIn("공식 발언 차례", transcript)
            self.assertFalse((meeting_dir / "transcript.md").exists())


    def test_live_agent_official_turn_call_timeout_does_not_create_reply(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(meeting_dir, {"meeting_id": "m1", "topic": "runtime", "live_status": "running"})
            connect_live_agent_payload(root, {"agent_id": "agent-a", "display_name": "Agent A", "meeting_id": "m1"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                call_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/meetings/m1/live-agent-turns/call",
                    data=json.dumps(
                        {
                            "agent_id": "agent-a",
                            "content": "공식 발언 차례",
                            "timeout_seconds": 0,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(call_request, timeout=4) as response:
                    called = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(called["status"], "timeout")
            self.assertIsNone(called["reply_event"])
            self.assertEqual(called["request_event"]["kind"], "live_agent_turn_request")
            official_replies = [
                event
                for event in read_live_events(meeting_dir, limit=None)
                if event.get("kind") == "message" and event.get("source_event_id") == called["request_event"]["id"]
            ]
            self.assertEqual(official_replies, [])
            call_operations = [item for item in operations["operations"] if item["operation"] == "official_turn.call"]
            self.assertEqual(call_operations[0]["status"], "degraded")


    def test_live_agent_official_turn_sequence_calls_agents_in_order(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(meeting_dir, {"meeting_id": "m1", "topic": "runtime", "live_status": "running"})
            for agent_id, display_name in (("agent-a", "Agent A"), ("agent-b", "Agent B")):
                connect_live_agent_payload(
                    root,
                    {
                        "agent_id": agent_id,
                        "display_name": display_name,
                        "provider_kind": "local_cli",
                        "connection_kind": "local_cli",
                        "meeting_id": "m1",
                        "engagement_mode": "moderator_called",
                    },
                )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            errors = []
            answered_request_ids = set()

            def answer_sequence_requests():
                try:
                    deadline = time.time() + 4
                    while time.time() < deadline and len(answered_request_ids) < 2:
                        for event in read_live_events(meeting_dir, limit=None):
                            if event.get("kind") != "live_agent_turn_request":
                                continue
                            if event["id"] in answered_request_ids:
                                continue
                            agent_id = event["target_agent_id"]
                            reply_request = Request(
                                f"http://127.0.0.1:{server.server_port}/api/live-agents/{agent_id}/official-turn",
                                data=json.dumps(
                                    {
                                        "meeting_id": "m1",
                                        "source_event_id": event["id"],
                                        "content": f"{agent_id} official reply",
                                    }
                                ).encode("utf-8"),
                                headers={"Content-Type": "application/json"},
                                method="POST",
                            )
                            with urlopen(reply_request, timeout=4) as response:
                                response.read()
                            answered_request_ids.add(event["id"])
                        time.sleep(0.01)
                except Exception as error:  # pragma: no cover - failure is asserted below
                    errors.append(error)

            responder = threading.Thread(target=answer_sequence_requests, daemon=True)
            responder.start()
            try:
                sequence_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/meetings/m1/live-agent-turns/sequence",
                    data=json.dumps(
                        {
                            "timeout_seconds": 2,
                            "turns": [
                                {
                                    "agent_id": "agent-a",
                                    "role_id": "architect",
                                    "display_name": "Agent A",
                                    "turn_id": "round_1:0:architect",
                                    "turn_index": 0,
                                    "content": "private prompt for A",
                                },
                                {
                                    "agent_id": "agent-b",
                                    "role_id": "critic",
                                    "display_name": "Agent B",
                                    "turn_id": "round_1:1:critic",
                                    "turn_index": 1,
                                    "content": "private prompt for B",
                                },
                            ],
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(sequence_request, timeout=6) as response:
                    sequence = json.loads(response.read().decode("utf-8"))
                responder.join(timeout=2)
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            if errors:
                raise errors[0]
            self.assertEqual(sequence["status"], "answered")
            self.assertEqual(sequence["answered_count"], 2)
            self.assertEqual(sequence["timeout_count"], 0)
            self.assertEqual(sequence["skipped_count"], 0)
            self.assertEqual([result["agent_id"] for result in sequence["results"]], ["agent-a", "agent-b"])
            self.assertEqual([result["status"] for result in sequence["results"]], ["answered", "answered"])
            self.assertEqual(sequence["results"][0]["reply_event"]["actor_id"], "agent-a")
            self.assertEqual(sequence["results"][1]["reply_event"]["actor_id"], "agent-b")
            self.assertEqual(sequence["results"][1]["request_event"]["turn_index"], 1)
            transcript = build_meeting_payload(meeting_dir)["artifacts"]["transcript.md"]
            self.assertIn("agent-a official reply", transcript)
            self.assertIn("agent-b official reply", transcript)
            self.assertNotIn("private prompt for A", transcript)
            self.assertNotIn("private prompt for B", transcript)
            sequence_operations = [item for item in operations["operations"] if item["operation"] == "official_turn.sequence"]
            self.assertEqual(len(sequence_operations), 1)
            self.assertEqual(sequence_operations[0]["status"], "success")
            operation_names = [item["operation"] for item in operations["operations"]]
            self.assertEqual(operation_names.count("official_turn.reply"), 2)
            self.assertEqual(operation_names.count("official_turn.sequence"), 1)
            operation_blob = json.dumps(operations["operations"], ensure_ascii=False)
            self.assertNotIn("private prompt for A", operation_blob)
            self.assertNotIn("agent-a official reply", operation_blob)


    def test_live_agent_official_turn_sequence_can_continue_after_timeout(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(meeting_dir, {"meeting_id": "m1", "topic": "runtime", "live_status": "running"})
            for agent_id in ("agent-a", "agent-b"):
                connect_live_agent_payload(root, {"agent_id": agent_id, "display_name": agent_id, "meeting_id": "m1"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                sequence_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/meetings/m1/live-agent-turns/sequence",
                    data=json.dumps(
                        {
                            "timeout_seconds": 0,
                            "turns": [
                                {"agent_id": "agent-a", "content": "first prompt"},
                                {"agent_id": "agent-b", "content": "second prompt"},
                            ],
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(sequence_request, timeout=4) as response:
                    sequence = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(sequence["status"], "timeout")
            self.assertEqual(sequence["answered_count"], 0)
            self.assertEqual(sequence["timeout_count"], 2)
            self.assertEqual(sequence["skipped_count"], 0)
            self.assertFalse(sequence["stopped"])
            self.assertEqual([result["agent_id"] for result in sequence["results"]], ["agent-a", "agent-b"])
            request_events = [
                event for event in read_live_events(meeting_dir, limit=None) if event.get("kind") == "live_agent_turn_request"
            ]
            self.assertEqual([event["target_agent_id"] for event in request_events], ["agent-a", "agent-b"])
            sequence_operations = [item for item in operations["operations"] if item["operation"] == "official_turn.sequence"]
            self.assertEqual(sequence_operations[0]["status"], "degraded")


    def test_live_agent_official_turn_sequence_can_stop_after_timeout(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(meeting_dir, {"meeting_id": "m1", "topic": "runtime", "live_status": "running"})
            for agent_id in ("agent-a", "agent-b"):
                connect_live_agent_payload(root, {"agent_id": agent_id, "display_name": agent_id, "meeting_id": "m1"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                sequence_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/meetings/m1/live-agent-turns/sequence",
                    data=json.dumps(
                        {
                            "timeout_seconds": 0,
                            "stop_on_timeout": True,
                            "turns": [
                                {"agent_id": "agent-a", "content": "first prompt"},
                                {"agent_id": "agent-b", "content": "second prompt"},
                            ],
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(sequence_request, timeout=4) as response:
                    sequence = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(sequence["status"], "stopped")
            self.assertEqual(sequence["timeout_count"], 1)
            self.assertEqual(sequence["skipped_count"], 1)
            self.assertTrue(sequence["stopped"])
            self.assertEqual([result["agent_id"] for result in sequence["results"]], ["agent-a", "agent-b"])
            self.assertEqual([result["status"] for result in sequence["results"]], ["timeout", "skipped"])
            request_events = [
                event for event in read_live_events(meeting_dir, limit=None) if event.get("kind") == "live_agent_turn_request"
            ]
            self.assertEqual([event["target_agent_id"] for event in request_events], ["agent-a"])


    def test_live_agent_official_turn_sequence_reports_cancelled_turn(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(meeting_dir, {"meeting_id": "m1", "topic": "runtime", "live_status": "running"})
            connect_live_agent_payload(root, {"agent_id": "agent-a", "display_name": "Agent A", "meeting_id": "m1"})
            errors = []
            result_holder = {}

            def run_sequence():
                try:
                    result_holder["sequence"] = live_agent_turn_sequence_payload(
                        root,
                        "m1",
                        {
                            "timeout_seconds": 2,
                            "turns": [{"agent_id": "agent-a", "content": "cancel me"}],
                        },
                    )
                except Exception as error:  # pragma: no cover - asserted below
                    errors.append(error)

            sequence_thread = threading.Thread(target=run_sequence)
            sequence_thread.start()
            deadline = time.time() + 2
            request_event = None
            while time.time() < deadline and request_event is None:
                for event in read_live_events(meeting_dir, limit=None):
                    if event.get("kind") == "live_agent_turn_request":
                        request_event = event
                        break
                time.sleep(0.01)
            self.assertIsNotNone(request_event)
            append_live_event(
                meeting_dir,
                {
                    "kind": "live_agent_turn_cancelled",
                    "meeting_id": "m1",
                    "target_agent_id": "agent-a",
                    "source_event_id": request_event["id"],
                    "content": "official turn request cancelled",
                    "channel": "system",
                    "official_record": False,
                },
            )
            sequence_thread.join(timeout=3)

            if errors:
                raise errors[0]
            self.assertFalse(sequence_thread.is_alive())
            sequence = result_holder["sequence"]
            self.assertEqual(sequence["status"], "cancelled")
            self.assertEqual(sequence["cancelled_count"], 1)
            self.assertEqual(sequence["answered_count"], 0)
            self.assertEqual(sequence["timeout_count"], 0)
            self.assertEqual(sequence["results"][0]["status"], "cancelled")
            self.assertEqual(sequence["results"][0]["reply_event"]["kind"], "live_agent_turn_cancelled")


    def test_live_agent_official_turn_sequence_validates_all_turns_before_append(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(meeting_dir, {"meeting_id": "m1", "topic": "runtime", "live_status": "running"})
            connect_live_agent_payload(root, {"agent_id": "agent-a", "display_name": "Agent A", "meeting_id": "m1"})

            with self.assertRaises(ValueError):
                live_agent_turn_sequence_payload(
                    root,
                    "m1",
                    {
                        "timeout_seconds": 0,
                        "turns": [
                            {"agent_id": "agent-a", "content": "valid first prompt"},
                            {"agent_id": "agent-b", "content": "invalid second prompt"},
                        ],
                    },
                )

            self.assertEqual(read_live_events(meeting_dir, limit=None), [])


    def test_live_agent_official_turn_round_builds_sequence_from_bindings(self):
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
                    "debate_rounds": [],
                },
            )
            for agent_id, display_name in (("agent-a", "Agent A"), ("agent-b", "Agent B")):
                connect_live_agent_payload(
                    root,
                    {
                        "agent_id": agent_id,
                        "display_name": display_name,
                        "provider_kind": "local_cli",
                        "connection_kind": "local_cli",
                        "meeting_id": "m1",
                        "engagement_mode": "moderator_called",
                    },
                )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            errors = []
            answered_request_ids = set()

            def answer_round_requests():
                try:
                    deadline = time.time() + 4
                    while time.time() < deadline and len(answered_request_ids) < 2:
                        for event in read_live_events(meeting_dir, limit=None):
                            if event.get("kind") != "live_agent_turn_request":
                                continue
                            if event["id"] in answered_request_ids:
                                continue
                            agent_id = event["target_agent_id"]
                            reply_request = Request(
                                f"http://127.0.0.1:{server.server_port}/api/live-agents/{agent_id}/official-turn",
                                data=json.dumps(
                                    {
                                        "meeting_id": "m1",
                                        "source_event_id": event["id"],
                                        "content": f"{agent_id} round reply",
                                    }
                                ).encode("utf-8"),
                                headers={"Content-Type": "application/json"},
                                method="POST",
                            )
                            with urlopen(reply_request, timeout=4) as response:
                                response.read()
                            answered_request_ids.add(event["id"])
                        time.sleep(0.01)
                except Exception as error:  # pragma: no cover - failure is asserted below
                    errors.append(error)

            responder = threading.Thread(target=answer_round_requests, daemon=True)
            responder.start()
            try:
                round_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/meetings/m1/live-agent-turns/round",
                    data=json.dumps(
                        {
                            "round_id": "round_1",
                            "content": "private round instruction",
                            "timeout_seconds": 2,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(round_request, timeout=6) as response:
                    round_result = json.loads(response.read().decode("utf-8"))
                responder.join(timeout=2)
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            if errors:
                raise errors[0]
            self.assertEqual(round_result["status"], "answered")
            self.assertEqual(round_result["round_id"], "round_1")
            self.assertEqual(round_result["role_ids"], ["architect", "critic"])
            self.assertEqual([result["agent_id"] for result in round_result["results"]], ["agent-a", "agent-b"])
            self.assertEqual(round_result["results"][0]["request_event"]["display_name"], "Architect")
            self.assertEqual(round_result["results"][1]["request_event"]["turn_id"], "round_1:1:critic")
            live_state = json.loads((meeting_dir / "live_state.json").read_text(encoding="utf-8"))
            self.assertEqual(
                live_state["debate_rounds"],
                [
                    {
                        "id": "round_1",
                        "status": "answered",
                        "role_ids": ["architect", "critic"],
                        "turn_count": 2,
                        "answered_count": 2,
                        "timeout_count": 0,
                        "skipped_count": 0,
                    }
                ],
            )
            transcript = build_meeting_payload(meeting_dir)["artifacts"]["transcript.md"]
            self.assertIn("agent-a round reply", transcript)
            self.assertIn("agent-b round reply", transcript)
            self.assertNotIn("private round instruction", transcript)
            round_operations = [item for item in operations["operations"] if item["operation"] == "official_turn.round"]
            self.assertEqual(len(round_operations), 1)
            self.assertEqual(round_operations[0]["status"], "success")
            operation_blob = json.dumps(operations["operations"], ensure_ascii=False)
            self.assertNotIn("private round instruction", operation_blob)
            self.assertNotIn("agent-a round reply", operation_blob)


    def test_live_agent_official_turn_round_preserves_existing_debate_round_fields(self):
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
                    "meeting_template": {
                        "rounds": [
                            {
                                "id": "round_1",
                                "instruction": "Template instruction",
                                "turn_control": {"selection": "all_roles"},
                            }
                        ]
                    },
                    "agent_bindings": [{"role_id": "architect", "agent_id": "agent-a"}],
                    "debate_rounds": [
                        {
                            "id": "round_1",
                            "title": "Keep title",
                            "messages": [{"content": "existing transcript detail"}],
                            "status": "draft",
                        }
                    ],
                },
            )
            connect_live_agent_payload(root, {"agent_id": "agent-a", "display_name": "Agent A", "meeting_id": "m1"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            errors = []

            def answer_request():
                try:
                    deadline = time.time() + 4
                    answered = False
                    while time.time() < deadline and not answered:
                        for event in read_live_events(meeting_dir, limit=None):
                            if event.get("kind") != "live_agent_turn_request":
                                continue
                            reply_request = Request(
                                f"http://127.0.0.1:{server.server_port}/api/live-agents/{event['target_agent_id']}/official-turn",
                                data=json.dumps(
                                    {
                                        "meeting_id": "m1",
                                        "source_event_id": event["id"],
                                        "content": "preserve fields reply",
                                    }
                                ).encode("utf-8"),
                                headers={"Content-Type": "application/json"},
                                method="POST",
                            )
                            with urlopen(reply_request, timeout=4) as response:
                                response.read()
                            answered = True
                            break
                        time.sleep(0.01)
                except Exception as error:  # pragma: no cover - failure is asserted below
                    errors.append(error)

            responder = threading.Thread(target=answer_request, daemon=True)
            responder.start()
            try:
                round_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/meetings/m1/live-agent-turns/round",
                    data=json.dumps({"round_id": "round_1", "timeout_seconds": 2}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(round_request, timeout=6) as response:
                    round_result = json.loads(response.read().decode("utf-8"))
                responder.join(timeout=2)
            finally:
                server.shutdown()
                server.server_close()

            if errors:
                raise errors[0]
            self.assertEqual(round_result["status"], "answered")
            live_state = json.loads((meeting_dir / "live_state.json").read_text(encoding="utf-8"))
            self.assertEqual(len(live_state["debate_rounds"]), 1)
            round_record = live_state["debate_rounds"][0]
            self.assertEqual(round_record["id"], "round_1")
            self.assertEqual(round_record["status"], "answered")
            self.assertEqual(round_record["role_ids"], ["architect"])
            self.assertEqual(round_record["turn_count"], 1)
            self.assertEqual(round_record["title"], "Keep title")
            self.assertEqual(round_record["messages"], [{"content": "existing transcript detail"}])


    def test_live_agent_official_turn_rounds_runs_remaining_template_rounds(self):
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
                    "roles": [
                        {"id": "architect", "display_name": "Architect"},
                        {"id": "critic", "display_name": "Critic"},
                    ],
                    "meeting_template": {
                        "rounds": [
                            {"id": "round_1", "instruction": "Done", "turn_control": {"selection": "all_roles"}},
                            {"id": "round_2", "instruction": "Next", "turn_control": {"selection": "selected_roles", "speaker_role_ids": ["critic"]}},
                            {"id": "round_3", "instruction": "Later", "turn_control": {"selection": "all_roles"}},
                        ]
                    },
                    "agent_bindings": [
                        {"role_id": "architect", "agent_id": "agent-a"},
                        {"role_id": "critic", "agent_id": "agent-b"},
                    ],
                    "debate_rounds": [{"id": "round_1", "status": "answered"}],
                },
            )
            for agent_id, display_name in (("agent-a", "Agent A"), ("agent-b", "Agent B")):
                connect_live_agent_payload(root, {"agent_id": agent_id, "display_name": display_name, "meeting_id": "m1"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            errors = []
            answered_request_ids = set()

            def answer_remaining_round_requests():
                try:
                    deadline = time.time() + 4
                    while time.time() < deadline and len(answered_request_ids) < 1:
                        for event in read_live_events(meeting_dir, limit=None):
                            if event.get("kind") != "live_agent_turn_request" or event["id"] in answered_request_ids:
                                continue
                            agent_id = event["target_agent_id"]
                            reply_request = Request(
                                f"http://127.0.0.1:{server.server_port}/api/live-agents/{agent_id}/official-turn",
                                data=json.dumps(
                                    {
                                        "meeting_id": "m1",
                                        "source_event_id": event["id"],
                                        "content": f"{agent_id} remaining reply",
                                    }
                                ).encode("utf-8"),
                                headers={"Content-Type": "application/json"},
                                method="POST",
                            )
                            with urlopen(reply_request, timeout=4) as response:
                                response.read()
                            answered_request_ids.add(event["id"])
                        time.sleep(0.01)
                except Exception as error:  # pragma: no cover - failure is asserted below
                    errors.append(error)

            responder = threading.Thread(target=answer_remaining_round_requests, daemon=True)
            responder.start()
            try:
                rounds_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/meetings/m1/live-agent-turns/rounds",
                    data=json.dumps({"timeout_seconds": 2, "max_rounds": 1}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(rounds_request, timeout=6) as response:
                    rounds_result = json.loads(response.read().decode("utf-8"))
                responder.join(timeout=2)
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            if errors:
                raise errors[0]
            self.assertEqual(rounds_result["status"], "answered")
            self.assertEqual(rounds_result["round_count"], 1)
            self.assertEqual(rounds_result["answered_round_count"], 1)
            self.assertEqual(rounds_result["results"][0]["round_id"], "round_2")
            self.assertEqual(rounds_result["results"][0]["role_ids"], ["critic"])
            live_state = json.loads((meeting_dir / "live_state.json").read_text(encoding="utf-8"))
            self.assertEqual([round_item["id"] for round_item in live_state["debate_rounds"]], ["round_1", "round_2"])
            operations_text = json.dumps(operations["operations"], ensure_ascii=False)
            self.assertIn('"official_turn.rounds"', operations_text)
            self.assertNotIn("remaining reply", operations_text)
            rounds_operations = [item for item in operations["operations"] if item["operation"] == "official_turn.rounds"]
            self.assertEqual(rounds_operations[0]["status"], "success")
            self.assertEqual(rounds_operations[0]["details"]["round_ids"], ["round_2"])
            self.assertEqual(rounds_operations[0]["details"]["statuses"], ["answered"])


    def test_live_agent_official_turn_rounds_can_finalize_after_success(self):
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
                    "meeting_template": {
                        "rounds": [
                            {"id": "round_1", "instruction": "Next", "turn_control": {"selection": "all_roles"}},
                        ]
                    },
                    "agent_bindings": [{"role_id": "architect", "agent_id": "agent-a"}],
                    "debate_rounds": [],
                },
            )
            connect_live_agent_payload(root, {"agent_id": "agent-a", "display_name": "Agent A", "meeting_id": "m1"})
            finalized = {
                "status": "finalized",
                "meeting_id": "m1",
                "official_event_count": 1,
                "artifact_event_id": "artifact-1",
                "shared_memory": {
                    "official_event_count": 1,
                    "last_official_event_id": "reply-1",
                    "decision_count": 0,
                    "open_question_count": 0,
                    "action_item_count": 1,
                },
            }
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            errors = []
            answered_request_ids = set()

            def answer_remaining_round_requests():
                try:
                    deadline = time.time() + 4
                    while time.time() < deadline and len(answered_request_ids) < 1:
                        for event in read_live_events(meeting_dir, limit=None):
                            if event.get("kind") != "live_agent_turn_request" or event["id"] in answered_request_ids:
                                continue
                            reply_request = Request(
                                f"http://127.0.0.1:{server.server_port}/api/live-agents/agent-a/official-turn",
                                data=json.dumps(
                                    {
                                        "meeting_id": "m1",
                                        "source_event_id": event["id"],
                                        "content": "agent-a remaining reply",
                                    }
                                ).encode("utf-8"),
                                headers={"Content-Type": "application/json"},
                                method="POST",
                            )
                            with urlopen(reply_request, timeout=4) as response:
                                response.read()
                            answered_request_ids.add(event["id"])
                        time.sleep(0.01)
                except Exception as error:  # pragma: no cover - failure is asserted below
                    errors.append(error)

            responder = threading.Thread(target=answer_remaining_round_requests, daemon=True)
            responder.start()
            try:
                with patch(
                    "agentsassemble.legacy_official_rounds.finalize_live_agent_meeting",
                    return_value=finalized,
                ) as finalize_meeting:
                    rounds_request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/meetings/m1/live-agent-turns/rounds",
                        data=json.dumps({"timeout_seconds": 2, "max_rounds": 1, "finalize_after_rounds": True}).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urlopen(rounds_request, timeout=6) as response:
                        rounds_result = json.loads(response.read().decode("utf-8"))
                    responder.join(timeout=2)
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            if errors:
                raise errors[0]
            finalize_meeting.assert_called_once_with((root / "meetings" / "m1").resolve())
            self.assertEqual(rounds_result["status"], "answered")
            self.assertEqual(rounds_result["finalization"]["status"], "finalized")
            self.assertEqual(rounds_result["finalization"]["official_event_count"], 1)
            rounds_operations = [item for item in operations["operations"] if item["operation"] == "official_turn.rounds"]
            self.assertEqual(rounds_operations[0]["status"], "success")
            self.assertEqual(rounds_operations[0]["details"]["finalization_status"], "finalized")
            self.assertEqual(rounds_operations[0]["details"]["finalization_official_event_count"], 1)
            self.assertEqual(rounds_operations[0]["details"]["shared_memory_official_event_count"], 1)
            self.assertEqual(rounds_operations[0]["details"]["shared_memory_last_event_id"], "reply-1")
            self.assertEqual(rounds_operations[0]["details"]["shared_memory_action_item_count"], 1)
            operations_text = json.dumps(operations["operations"], ensure_ascii=False)
            self.assertNotIn("remaining reply", operations_text)


    def test_live_agent_official_turn_rounds_skip_finalization_when_template_rounds_remain(self):
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
                    "meeting_template": {
                        "rounds": [
                            {"id": "round_1", "instruction": "First", "turn_control": {"selection": "all_roles"}},
                            {"id": "round_2", "instruction": "Second", "turn_control": {"selection": "all_roles"}},
                        ]
                    },
                    "agent_bindings": [{"role_id": "architect", "agent_id": "agent-a"}],
                    "debate_rounds": [],
                },
            )
            connect_live_agent_payload(root, {"agent_id": "agent-a", "display_name": "Agent A", "meeting_id": "m1"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            errors = []
            answered_request_ids = set()

            def answer_first_request():
                try:
                    deadline = time.time() + 4
                    while time.time() < deadline and not answered_request_ids:
                        for event in read_live_events(meeting_dir, limit=None):
                            if event.get("kind") != "live_agent_turn_request":
                                continue
                            reply_request = Request(
                                f"http://127.0.0.1:{server.server_port}/api/live-agents/agent-a/official-turn",
                                data=json.dumps(
                                    {
                                        "meeting_id": "m1",
                                        "source_event_id": event["id"],
                                        "content": "round one reply",
                                    }
                                ).encode("utf-8"),
                                headers={"Content-Type": "application/json"},
                                method="POST",
                            )
                            with urlopen(reply_request, timeout=4) as response:
                                response.read()
                            answered_request_ids.add(event["id"])
                            break
                        time.sleep(0.01)
                except Exception as error:  # pragma: no cover - failure is asserted below
                    errors.append(error)

            responder = threading.Thread(target=answer_first_request, daemon=True)
            responder.start()
            try:
                with patch(
                    "agentsassemble.legacy_official_rounds.finalize_live_agent_meeting",
                    return_value={"status": "finalized", "meeting_id": "m1", "official_event_count": 1},
                ) as finalize_meeting:
                    rounds_request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/meetings/m1/live-agent-turns/rounds",
                        data=json.dumps({"timeout_seconds": 2, "max_rounds": 1, "finalize_after_rounds": True}).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urlopen(rounds_request, timeout=6) as response:
                        rounds_result = json.loads(response.read().decode("utf-8"))
                    responder.join(timeout=2)
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            if errors:
                raise errors[0]
            finalize_meeting.assert_not_called()
            self.assertEqual(rounds_result["status"], "answered")
            self.assertEqual(rounds_result["finalization"]["status"], "skipped")
            self.assertEqual(rounds_result["finalization"]["reason"], "rounds_still_remaining")
            self.assertFalse((meeting_dir / "meeting.json").exists())
            rounds_operations = [item for item in operations["operations"] if item["operation"] == "official_turn.rounds"]
            self.assertEqual(rounds_operations[0]["status"], "degraded")
            self.assertEqual(rounds_operations[0]["details"]["finalization_status"], "skipped")
            self.assertEqual(rounds_operations[0]["details"]["finalization_reason"], "rounds_still_remaining")


    def test_live_agent_official_turn_rounds_does_not_duplicate_concurrent_remaining_round(self):
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
                    "meeting_template": {
                        "rounds": [
                            {"id": "round_1", "instruction": "First", "turn_control": {"selection": "all_roles"}},
                        ]
                    },
                    "agent_bindings": [{"role_id": "architect", "agent_id": "agent-a"}],
                    "debate_rounds": [],
                },
            )
            connect_live_agent_payload(root, {"agent_id": "agent-a", "display_name": "Agent A", "meeting_id": "m1"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            start_gate = threading.Barrier(3)
            results = []
            errors = []
            answered_request_ids = set()

            def call_remaining_rounds():
                try:
                    start_gate.wait(timeout=2)
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/meetings/m1/live-agent-turns/rounds",
                        data=json.dumps({"timeout_seconds": 1, "max_rounds": 1}).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urlopen(request, timeout=6) as response:
                        results.append(json.loads(response.read().decode("utf-8")))
                except Exception as error:  # pragma: no cover - failure is asserted below
                    errors.append(error)

            def answer_one_request():
                try:
                    first_seen_at = None
                    deadline = time.time() + 5
                    while time.time() < deadline and not answered_request_ids:
                        request_events = [
                            event
                            for event in read_live_events(meeting_dir, limit=None)
                            if event.get("kind") == "live_agent_turn_request"
                        ]
                        if request_events and first_seen_at is None:
                            first_seen_at = time.time()
                        should_answer = request_events and (
                            len(request_events) >= 2 or (first_seen_at is not None and time.time() - first_seen_at >= 0.25)
                        )
                        if should_answer:
                            event = request_events[0]
                            reply_request = Request(
                                f"http://127.0.0.1:{server.server_port}/api/live-agents/{event['target_agent_id']}/official-turn",
                                data=json.dumps(
                                    {
                                        "meeting_id": "m1",
                                        "source_event_id": event["id"],
                                        "content": "concurrent remaining reply",
                                    }
                                ).encode("utf-8"),
                                headers={"Content-Type": "application/json"},
                                method="POST",
                            )
                            with urlopen(reply_request, timeout=4) as response:
                                response.read()
                            answered_request_ids.add(event["id"])
                            break
                        time.sleep(0.01)
                except Exception as error:  # pragma: no cover - failure is asserted below
                    errors.append(error)

            caller_a = threading.Thread(target=call_remaining_rounds, daemon=True)
            caller_b = threading.Thread(target=call_remaining_rounds, daemon=True)
            responder = threading.Thread(target=answer_one_request, daemon=True)
            caller_a.start()
            caller_b.start()
            responder.start()
            try:
                start_gate.wait(timeout=2)
                caller_a.join(timeout=7)
                caller_b.join(timeout=7)
                responder.join(timeout=2)
            finally:
                server.shutdown()
                server.server_close()

            if errors:
                raise errors[0]
            self.assertEqual(len(results), 2)
            request_events = [
                event for event in read_live_events(meeting_dir, limit=None) if event.get("kind") == "live_agent_turn_request"
            ]
            self.assertEqual([event["turn_id"] for event in request_events], ["round_1:0:architect"])
            self.assertEqual(sorted(result["status"] for result in results), ["answered", "complete"])


    def test_live_agent_official_turn_round_skips_already_answered_round_after_remaining_batch(self):
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
                    "meeting_template": {
                        "rounds": [
                            {"id": "round_1", "instruction": "First", "turn_control": {"selection": "all_roles"}},
                        ]
                    },
                    "agent_bindings": [{"role_id": "architect", "agent_id": "agent-a"}],
                    "debate_rounds": [],
                },
            )
            connect_live_agent_payload(root, {"agent_id": "agent-a", "display_name": "Agent A", "meeting_id": "m1"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            errors = []
            answered_request_ids = set()

            def answer_first_request():
                try:
                    deadline = time.time() + 4
                    while time.time() < deadline and not answered_request_ids:
                        for event in read_live_events(meeting_dir, limit=None):
                            if event.get("kind") != "live_agent_turn_request":
                                continue
                            reply_request = Request(
                                f"http://127.0.0.1:{server.server_port}/api/live-agents/{event['target_agent_id']}/official-turn",
                                data=json.dumps(
                                    {
                                        "meeting_id": "m1",
                                        "source_event_id": event["id"],
                                        "content": "first batch reply",
                                    }
                                ).encode("utf-8"),
                                headers={"Content-Type": "application/json"},
                                method="POST",
                            )
                            with urlopen(reply_request, timeout=4) as response:
                                response.read()
                            answered_request_ids.add(event["id"])
                            break
                        time.sleep(0.01)
                except Exception as error:  # pragma: no cover - failure is asserted below
                    errors.append(error)

            responder = threading.Thread(target=answer_first_request, daemon=True)
            responder.start()
            try:
                remaining_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/meetings/m1/live-agent-turns/rounds",
                    data=json.dumps({"timeout_seconds": 2, "max_rounds": 1}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(remaining_request, timeout=6) as response:
                    remaining_result = json.loads(response.read().decode("utf-8"))
                responder.join(timeout=2)
                duplicate_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/meetings/m1/live-agent-turns/round",
                    data=json.dumps({"round_id": "round_1", "timeout_seconds": 0}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(duplicate_request, timeout=4) as response:
                    duplicate_result = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            if errors:
                raise errors[0]
            self.assertEqual(remaining_result["status"], "answered")
            self.assertEqual(duplicate_result["status"], "complete")
            self.assertEqual(duplicate_result["round_id"], "round_1")
            self.assertEqual(duplicate_result["turn_count"], 0)
            request_events = [
                event for event in read_live_events(meeting_dir, limit=None) if event.get("kind") == "live_agent_turn_request"
            ]
            self.assertEqual([event["turn_id"] for event in request_events], ["round_1:0:architect"])


    def test_live_agent_official_turn_rounds_respect_live_progress_when_meeting_json_exists(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            base_meeting = {
                "meeting_id": "m1",
                "topic": "runtime",
                "live_status": "running",
                "roles": [{"id": "architect", "display_name": "Architect"}],
                "meeting_template": {
                    "rounds": [
                        {"id": "round_1", "instruction": "First", "turn_control": {"selection": "all_roles"}},
                    ]
                },
                "agent_bindings": [{"role_id": "architect", "agent_id": "agent-a"}],
                "debate_rounds": [],
            }
            (meeting_dir / "meeting.json").write_text(json.dumps(base_meeting), encoding="utf-8")
            write_live_state(
                meeting_dir,
                {
                    **base_meeting,
                    "debate_rounds": [
                        {
                            "id": "round_1",
                            "status": "answered",
                            "role_ids": ["architect"],
                            "turn_count": 1,
                            "answered_count": 1,
                        }
                    ],
                },
            )
            connect_live_agent_payload(root, {"agent_id": "agent-a", "display_name": "Agent A", "meeting_id": "m1"})

            rounds_result = live_agent_turn_rounds_payload(root, "m1", {"timeout_seconds": 0, "max_rounds": 1})
            round_result = live_agent_turn_round_payload(root, "m1", {"round_id": "round_1", "timeout_seconds": 0})

            self.assertEqual(rounds_result["status"], "complete")
            self.assertEqual(rounds_result["round_count"], 0)
            self.assertEqual(round_result["status"], "complete")
            self.assertEqual(read_live_events(meeting_dir, limit=None), [])


    def test_live_agent_official_turn_rounds_treat_inner_complete_as_success(self):
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
                    "meeting_template": {
                        "rounds": [
                            {"id": "round_1", "instruction": "First", "turn_control": {"selection": "all_roles"}},
                        ]
                    },
                    "agent_bindings": [{"role_id": "architect", "agent_id": "agent-a"}],
                    "debate_rounds": [{"id": "round_1", "status": "answered"}],
                },
            )
            connect_live_agent_payload(root, {"agent_id": "agent-a", "display_name": "Agent A", "meeting_id": "m1"})

            result = _live_agent_turn_rounds_payload_locked(
                root,
                "m1",
                ["round_1"],
                timeout_seconds=0,
                stop_on_timeout=False,
                max_rounds=1,
            )

            self.assertEqual(result["status"], "complete")
            self.assertEqual(result["round_count"], 1)
            self.assertEqual(result["answered_round_count"], 0)
            self.assertEqual(result["completed_round_count"], 1)
            self.assertEqual(result["results"][0]["status"], "complete")


    def test_meeting_payload_merges_live_round_progress_when_meeting_json_exists(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            base_meeting = {
                "meeting_id": "m1",
                "topic": "runtime",
                "live_status": "complete",
                "roles": [{"id": "architect", "display_name": "Architect"}],
                "meeting_template": {
                    "rounds": [
                        {"id": "round_1", "instruction": "First", "turn_control": {"selection": "all_roles"}},
                    ]
                },
                "agent_bindings": [{"role_id": "architect", "agent_id": "agent-a"}],
                "debate_rounds": [{"id": "round_1", "title": "Keep title", "status": "draft"}],
            }
            (meeting_dir / "meeting.json").write_text(json.dumps(base_meeting), encoding="utf-8")
            write_live_state(
                meeting_dir,
                {
                    **base_meeting,
                    "live_status": "running",
                    "debate_rounds": [
                        {
                            "id": "round_1",
                            "status": "answered",
                            "role_ids": ["architect"],
                            "turn_count": 1,
                            "answered_count": 1,
                        }
                    ],
                },
            )

            payload = build_meeting_payload(meeting_dir)

            self.assertEqual(payload["meeting"]["roles"], [{"id": "architect", "display_name": "Architect"}])
            self.assertEqual(payload["meeting"]["meeting_template"], base_meeting["meeting_template"])
            self.assertEqual(payload["meeting"]["live_status"], "complete")
            self.assertEqual(
                payload["meeting"]["debate_rounds"],
                [
                    {
                        "id": "round_1",
                        "title": "Keep title",
                        "status": "answered",
                        "role_ids": ["architect"],
                        "turn_count": 1,
                        "answered_count": 1,
                    }
                ],
            )


    def test_meeting_payload_ignores_invalid_live_progress_when_meeting_json_exists(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            meeting_dir = Path(temp_dir) / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            (meeting_dir / "meeting.json").write_text(
                json.dumps(
                    {
                        "meeting_id": "m1",
                        "topic": "final",
                        "roles": [],
                        "meeting_template": {"rounds": []},
                        "debate_rounds": [],
                        "live_status": "complete",
                    }
                ),
                encoding="utf-8",
            )
            (meeting_dir / "live_state.json").write_text("{", encoding="utf-8")

            payload = build_meeting_payload(meeting_dir)

            self.assertEqual(payload["meeting"]["meeting_id"], "m1")
            self.assertEqual(payload["meeting"]["live_status"], "complete")


    def test_live_agent_official_turn_rounds_marks_inner_stopped_round_as_degraded(self):
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
                    "roles": [
                        {"id": "architect", "display_name": "Architect"},
                        {"id": "critic", "display_name": "Critic"},
                    ],
                    "meeting_template": {
                        "rounds": [
                            {"id": "round_1", "instruction": "First", "turn_control": {"selection": "all_roles"}},
                        ]
                    },
                    "agent_bindings": [
                        {"role_id": "architect", "agent_id": "agent-a"},
                        {"role_id": "critic", "agent_id": "agent-b"},
                    ],
                    "debate_rounds": [],
                },
            )
            for agent_id in ("agent-a", "agent-b"):
                connect_live_agent_payload(root, {"agent_id": agent_id, "display_name": agent_id, "meeting_id": "m1"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                rounds_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/meetings/m1/live-agent-turns/rounds",
                    data=json.dumps({"timeout_seconds": 0, "stop_on_timeout": True, "max_rounds": 1}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(rounds_request, timeout=4) as response:
                    rounds_result = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(rounds_result["status"], "stopped")
            self.assertEqual(rounds_result["round_count"], 1)
            self.assertEqual(rounds_result["answered_round_count"], 0)
            self.assertEqual(rounds_result["results"][0]["status"], "stopped")
            self.assertEqual(rounds_result["results"][0]["timeout_count"], 1)
            self.assertEqual(rounds_result["results"][0]["skipped_count"], 1)
            request_events = [event for event in read_live_events(meeting_dir, limit=None) if event.get("kind") == "live_agent_turn_request"]
            self.assertEqual([event["turn_id"] for event in request_events], ["round_1:0:architect"])
            rounds_operations = [item for item in operations["operations"] if item["operation"] == "official_turn.rounds"]
            self.assertEqual(rounds_operations[0]["status"], "degraded")


    def test_live_agent_official_turn_rounds_stops_remaining_after_timeout(self):
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
                    "meeting_template": {
                        "rounds": [
                            {"id": "round_1", "instruction": "First", "turn_control": {"selection": "all_roles"}},
                            {"id": "round_2", "instruction": "Second", "turn_control": {"selection": "all_roles"}},
                        ]
                    },
                    "agent_bindings": [{"role_id": "architect", "agent_id": "agent-a"}],
                    "debate_rounds": [],
                },
            )
            connect_live_agent_payload(root, {"agent_id": "agent-a", "display_name": "Agent A", "meeting_id": "m1"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                rounds_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/meetings/m1/live-agent-turns/rounds",
                    data=json.dumps({"timeout_seconds": 0, "stop_on_timeout": True, "max_rounds": 2}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(rounds_request, timeout=4) as response:
                    rounds_result = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/live-agent-operations?limit=20", timeout=4) as response:
                    operations = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(rounds_result["status"], "stopped")
            self.assertEqual(rounds_result["timeout_round_count"], 1)
            self.assertEqual(rounds_result["skipped_round_count"], 1)
            self.assertEqual([result["round_id"] for result in rounds_result["results"]], ["round_1", "round_2"])
            self.assertEqual([result["status"] for result in rounds_result["results"]], ["timeout", "skipped"])
            request_events = [event for event in read_live_events(meeting_dir, limit=None) if event.get("kind") == "live_agent_turn_request"]
            self.assertEqual([event["turn_id"] for event in request_events], ["round_1:0:architect"])
            rounds_operations = [item for item in operations["operations"] if item["operation"] == "official_turn.rounds"]
            self.assertEqual(rounds_operations[0]["status"], "degraded")
