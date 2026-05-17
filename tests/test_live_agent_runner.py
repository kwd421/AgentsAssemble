import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from agentsassemble.live_agent_runner import (
    LiveAgentRunner,
    RemoteBridgeResidentCommandRunner,
    ResidentAgentConfig,
    event_reply_candidate,
    load_group_configs,
)


class FakeClock:
    def __init__(self):
        self.now = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += timedelta(seconds=seconds)


class FakeRoomClient:
    def __init__(self, rooms, *, register_agent=None):
        self.rooms = list(rooms)
        self.register_agent = register_agent or {"agent_id": "agent-a", "status": "online"}
        self.calls = []

    def __call__(self, url, *, method="GET", payload=None):
        self.calls.append((url, method, payload))
        if url.endswith("/room"):
            return self.rooms.pop(0) if self.rooms else {"lobby_events": []}
        if url.endswith("/lobby"):
            return {"event": {"id": "reply-id"}}
        if url.endswith("/live-agents") and method == "POST":
            return {"agent": dict(self.register_agent)}
        return {"agent": {"agent_id": "agent-a", "status": (payload or {}).get("status", "online")}}


def config(**overrides):
    values = {
        "server": "http://room.local",
        "agent_id": "agent-a",
        "display_name": "Agent A",
        "provider_kind": "local_cli",
        "connection_kind": "local_cli",
        "session_id": "",
        "endpoint": "",
        "auth_ref": "",
        "meeting_id": "",
        "engagement_mode": "always",
        "command": ["fake-agent"],
        "timeout_seconds": 30,
        "poll_interval": 1.0,
        "heartbeat_interval": 10.0,
        "cooldown": 0.0,
        "max_chain_depth": 1,
        "max_ticks": 1,
    }
    values.update(overrides)
    return ResidentAgentConfig(**values)


class LiveAgentRunnerTests(unittest.TestCase):
    def test_always_runner_replies_to_new_non_self_event_and_records_chain_metadata(self):
        clock = FakeClock()
        room = {"lobby_events": [{"id": "evt1", "name": "나", "message": "상태 어때?", "auto_chain_depth": 0}]}
        client = FakeRoomClient([room])
        prompts = []

        def command_runner(command, prompt, *, timeout_seconds):
            prompts.append((command, prompt, timeout_seconds))
            return "Agent A online"

        runner = LiveAgentRunner(config(), request_json=client, command_runner=command_runner, sleep_fn=clock.sleep, now_fn=clock)

        self.assertEqual(runner.run(), 1)

        lobby_payloads = [payload for url, method, payload in client.calls if url.endswith("/lobby")]
        self.assertEqual(lobby_payloads[0]["message"], "Agent A online")
        self.assertEqual(lobby_payloads[0]["actor_id"], "agent-a")
        self.assertEqual(lobby_payloads[0]["source_event_id"], "evt1")
        self.assertEqual(lobby_payloads[0]["auto_chain_depth"], 1)
        self.assertIn("AgentsAssemble", prompts[0][1])
        self.assertIn("상태 어때?", prompts[0][1])

    def test_runner_does_not_reply_to_the_same_event_twice(self):
        clock = FakeClock()
        repeated = {"lobby_events": [{"id": "evt1", "name": "나", "message": "한 번만"}]}
        client = FakeRoomClient([repeated, repeated])
        runner = LiveAgentRunner(
            config(max_ticks=2),
            request_json=client,
            command_runner=lambda command, prompt, *, timeout_seconds: "reply",
            sleep_fn=clock.sleep,
            now_fn=clock,
        )

        self.assertEqual(runner.run(), 1)

        lobby_calls = [call for call in client.calls if call[0].endswith("/lobby")]
        self.assertEqual(len(lobby_calls), 1)

    def test_runner_restores_observed_cursor_from_registration_before_replying(self):
        clock = FakeClock()
        room = {
            "lobby_events": [
                {"id": "evt1", "name": "나", "message": "이미 처리한 말"},
                {"id": "evt2", "name": "나", "message": "재시작 후 새 말"},
            ]
        }
        client = FakeRoomClient([room], register_agent={"agent_id": "agent-a", "last_observed_event_id": "evt1"})
        runner = LiveAgentRunner(
            config(),
            request_json=client,
            command_runner=lambda command, prompt, *, timeout_seconds: "reply after restart",
            sleep_fn=clock.sleep,
            now_fn=clock,
        )

        self.assertEqual(runner.run(), 1)

        lobby_payloads = [payload for url, method, payload in client.calls if url.endswith("/lobby")]
        self.assertEqual(lobby_payloads[0]["source_event_id"], "evt2")
        self.assertEqual(runner.last_observed_event_id, "reply-id")

    def test_runner_restores_observed_cursor_from_room_snapshot_before_replying(self):
        clock = FakeClock()
        room = {
            "agent": {"agent_id": "agent-a", "last_observed_event_id": "evt1"},
            "lobby_events": [
                {"id": "evt1", "name": "나", "message": "이미 처리한 말"},
                {"id": "evt2", "name": "나", "message": "room snapshot 뒤 새 말"},
            ],
        }
        client = FakeRoomClient([room])
        runner = LiveAgentRunner(
            config(),
            request_json=client,
            command_runner=lambda command, prompt, *, timeout_seconds: "reply after room recovery",
            sleep_fn=clock.sleep,
            now_fn=clock,
        )

        self.assertEqual(runner.run(), 1)

        lobby_payloads = [payload for url, method, payload in client.calls if url.endswith("/lobby")]
        self.assertEqual(lobby_payloads[0]["source_event_id"], "evt2")

    def test_runner_keeps_local_cursor_when_presence_snapshot_is_stale(self):
        clock = FakeClock()
        room = {
            "agent": {"agent_id": "agent-a", "last_observed_event_id": "evt1"},
            "lobby_events": [
                {"id": "evt1", "name": "나", "message": "오래된 말"},
                {"id": "evt2", "name": "나", "message": "이미 처리한 말"},
                {"id": "evt3", "name": "나", "message": "새 말"},
            ],
        }
        client = FakeRoomClient([room], register_agent={"agent_id": "agent-a", "last_observed_event_id": "evt1"})
        runner = LiveAgentRunner(
            config(),
            request_json=client,
            command_runner=lambda command, prompt, *, timeout_seconds: "reply to new event",
            sleep_fn=clock.sleep,
            now_fn=clock,
        )
        runner.last_observed_event_id = "evt2"

        self.assertEqual(runner.run(), 1)

        lobby_payloads = [payload for url, method, payload in client.calls if url.endswith("/lobby")]
        self.assertEqual(lobby_payloads[0]["source_event_id"], "evt3")

    def test_runner_ignores_cursor_snapshot_without_matching_agent_id(self):
        clock = FakeClock()
        room = {
            "agent": {"last_observed_event_id": "evt1"},
            "lobby_events": [
                {"id": "evt1", "name": "나", "message": "확실하지 않은 cursor는 무시"},
                {"id": "evt2", "name": "나", "message": "두 번째 말"},
            ],
        }
        client = FakeRoomClient([room])
        runner = LiveAgentRunner(
            config(),
            request_json=client,
            command_runner=lambda command, prompt, *, timeout_seconds: "reply without unsafe cursor",
            sleep_fn=clock.sleep,
            now_fn=clock,
        )

        self.assertEqual(runner.run(), 1)

        lobby_payloads = [payload for url, method, payload in client.calls if url.endswith("/lobby")]
        self.assertEqual(lobby_payloads[0]["source_event_id"], "evt1")

    def test_runner_skips_self_events_and_chain_depth_over_limit(self):
        self_event = {"id": "evt-self", "actor_id": "agent-a", "name": "Agent A", "message": "내 말"}
        deep_event = {"id": "evt-deep", "name": "Agent B", "message": "너무 깊음", "auto_chain_depth": 2}

        self.assertIsNone(event_reply_candidate([self_event], "agent-a", "Agent A", "", max_chain_depth=1))
        self.assertIsNone(event_reply_candidate([deep_event], "agent-a", "Agent A", "", max_chain_depth=1))

    def test_runner_records_error_status_when_command_fails(self):
        clock = FakeClock()
        room = {"lobby_events": [{"id": "evt1", "name": "나", "message": "실패해봐"}]}
        client = FakeRoomClient([room])

        def fail_command(command, prompt, *, timeout_seconds):
            raise RuntimeError("boom")

        runner = LiveAgentRunner(config(), request_json=client, command_runner=fail_command, sleep_fn=clock.sleep, now_fn=clock)

        self.assertEqual(runner.run(), 0)

        error_payloads = [payload for url, method, payload in client.calls if url.endswith("/heartbeat") and payload["status"] == "error"]
        self.assertEqual(error_payloads[0]["last_error"], "boom")
        self.assertEqual(error_payloads[0]["last_observed_event_id"], "evt1")

    def test_runner_backs_off_after_command_failure_before_next_reply(self):
        clock = FakeClock()
        first_room = {"lobby_events": [{"id": "evt1", "name": "나", "message": "첫 실패"}]}
        second_room = {
            "lobby_events": [
                {"id": "evt1", "name": "나", "message": "첫 실패"},
                {"id": "evt2", "name": "나", "message": "바로 다시 호출하지 마"},
            ]
        }
        client = FakeRoomClient([first_room, second_room])
        command_calls = []

        def fail_command(command, prompt, *, timeout_seconds):
            command_calls.append(prompt)
            raise RuntimeError("boom")

        runner = LiveAgentRunner(
            config(max_ticks=2, poll_interval=1.0, cooldown=5.0),
            request_json=client,
            command_runner=fail_command,
            sleep_fn=clock.sleep,
            now_fn=clock,
        )

        self.assertEqual(runner.run(), 0)

        self.assertEqual(len(command_calls), 1)
        self.assertEqual([call for call in client.calls if call[0].endswith("/lobby")], [])

    def test_runner_keeps_error_status_on_periodic_heartbeat_during_failure_backoff(self):
        clock = FakeClock()
        room = {"lobby_events": [{"id": "evt1", "name": "나", "message": "실패 후 대기"}]}
        client = FakeRoomClient([room, room])

        def fail_command(command, prompt, *, timeout_seconds):
            raise RuntimeError("boom")

        runner = LiveAgentRunner(
            config(max_ticks=2, poll_interval=2.0, heartbeat_interval=1.0, cooldown=5.0),
            request_json=client,
            command_runner=fail_command,
            sleep_fn=clock.sleep,
            now_fn=clock,
        )

        self.assertEqual(runner.run(), 0)

        heartbeats = [payload for url, method, payload in client.calls if url.endswith("/heartbeat")]
        error_index = next(index for index, payload in enumerate(heartbeats) if payload["status"] == "error")
        self.assertEqual(heartbeats[error_index + 1]["status"], "error")
        self.assertEqual(heartbeats[error_index + 1]["last_error"], "boom")
        self.assertEqual(heartbeats[error_index + 1]["last_observed_event_id"], "evt1")

    def test_watch_mode_observes_without_replying_and_advances_cursor(self):
        clock = FakeClock()
        room = {"lobby_events": [{"id": "evt1", "side": "mine", "name": "나", "message": "보고만 있어"}]}
        client = FakeRoomClient([room])
        command_calls = []
        runner = LiveAgentRunner(
            config(engagement_mode="watch"),
            request_json=client,
            command_runner=lambda command, prompt, *, timeout_seconds: command_calls.append(prompt) or "reply",
            sleep_fn=clock.sleep,
            now_fn=clock,
        )

        self.assertEqual(runner.run(), 0)

        self.assertEqual(command_calls, [])
        self.assertEqual([call for call in client.calls if call[0].endswith("/lobby")], [])
        observed = [
            payload["last_observed_event_id"]
            for url, method, payload in client.calls
            if url.endswith("/heartbeat") and payload.get("last_observed_event_id")
        ]
        self.assertIn("evt1", observed)

    def test_human_only_mode_replies_to_humans_and_ignores_agent_chatter(self):
        human_event = {"id": "human", "side": "mine", "name": "나", "message": "사람 질문"}
        other_human_event = {"id": "other-human", "side": "other", "name": "상대", "message": "상대 질문"}
        agent_event = {"id": "agent", "side": "other-agent", "actor_id": "agent-b", "name": "Agent B", "message": "에이전트 말"}

        self.assertEqual(
            event_reply_candidate([human_event], "agent-a", "Agent A", "", max_chain_depth=1, engagement_mode="human_only"),
            human_event,
        )
        self.assertEqual(
            event_reply_candidate([other_human_event], "agent-a", "Agent A", "", max_chain_depth=1, engagement_mode="human_only"),
            other_human_event,
        )
        self.assertIsNone(
            event_reply_candidate([agent_event], "agent-a", "Agent A", "", max_chain_depth=1, engagement_mode="human_only")
        )

    def test_mentioned_mode_replies_only_when_called_by_display_name_or_agent_id(self):
        called_by_name = {"id": "name", "side": "mine", "name": "나", "message": "Agent A 지금 가능해?"}
        called_by_id = {"id": "id", "side": "mine", "name": "나", "message": "agent-a 상태 알려줘"}
        unrelated = {"id": "other", "side": "mine", "name": "나", "message": "아무나 답해"}

        self.assertEqual(
            event_reply_candidate([called_by_name], "agent-a", "Agent A", "", max_chain_depth=1, engagement_mode="mentioned"),
            called_by_name,
        )
        self.assertEqual(
            event_reply_candidate([called_by_id], "agent-a", "Agent A", "", max_chain_depth=1, engagement_mode="mentioned"),
            called_by_id,
        )
        self.assertIsNone(
            event_reply_candidate([unrelated], "agent-a", "Agent A", "", max_chain_depth=1, engagement_mode="mentioned")
        )

    def test_idle_runner_sends_periodic_heartbeat(self):
        clock = FakeClock()
        client = FakeRoomClient([{"lobby_events": []}, {"lobby_events": []}])
        runner = LiveAgentRunner(
            config(max_ticks=2, poll_interval=11.0, heartbeat_interval=10.0),
            request_json=client,
            command_runner=lambda command, prompt, *, timeout_seconds: "reply",
            sleep_fn=clock.sleep,
            now_fn=clock,
        )

        self.assertEqual(runner.run(), 0)

        online_heartbeats = [
            payload
            for url, method, payload in client.calls
            if url.endswith("/heartbeat") and payload["status"] == "online"
        ]
        self.assertEqual(len(online_heartbeats), 2)

    def test_group_config_preserves_zero_overrides(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "live-agents.json"
            path.write_text(
                json.dumps(
                    {
                        "poll_interval": 2,
                        "heartbeat_interval": 30,
                        "max_chain_depth": 1,
                        "agents": [
                            {
                                "agent_id": "agent-zero",
                                "command": ["fake"],
                                "poll_interval": 0,
                                "heartbeat_interval": 0,
                                "max_chain_depth": 0,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            loaded = load_group_configs(path)

        self.assertEqual(loaded[0].poll_interval, 0)
        self.assertEqual(loaded[0].heartbeat_interval, 0)
        self.assertEqual(loaded[0].max_chain_depth, 0)

    def test_group_config_server_override_applies_to_all_agents(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "live-agents.json"
            path.write_text(
                json.dumps(
                    {
                        "server": "http://config-server.local",
                        "agents": [
                            {"agent_id": "agent-a", "command": ["fake"]},
                            {"agent_id": "agent-b", "server": "http://agent-server.local", "command": ["fake"]},
                        ],
                    }
                ),
                encoding="utf-8",
            )

            loaded = load_group_configs(path, server_override="http://override.local")

        self.assertEqual([config.server for config in loaded], ["http://override.local", "http://override.local"])

    def test_group_config_defaults_to_safe_mentioned_policy_unless_explicit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "live-agents.json"
            path.write_text(
                json.dumps(
                    {
                        "agents": [
                            {"agent_id": "agent-default", "command": ["fake"]},
                            {"agent_id": "agent-always", "engagement_mode": "always", "command": ["fake"]},
                        ],
                    }
                ),
                encoding="utf-8",
            )

            loaded = load_group_configs(path)

        modes = {config.agent_id: config.engagement_mode for config in loaded}
        self.assertEqual(modes["agent-default"], "mentioned")
        self.assertEqual(modes["agent-always"], "always")

    def test_group_config_preserves_live_session_connection_kind(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "live-agents.json"
            path.write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "agent-session",
                                "connection_kind": "live_session",
                                "command": ["python3", "-u", "session.py"],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            loaded = load_group_configs(path)

        self.assertEqual(loaded[0].connection_kind, "live_session")

    def test_group_config_accepts_remote_bridge_without_local_command(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "live-agents.json"
            path.write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "friend-claude",
                                "display_name": "Friend Claude",
                                "provider_kind": "claude_code",
                                "connection_kind": "remote_bridge",
                                "endpoint": "http://friend.local:8777",
                                "auth_ref": "env:BRIDGE_TOKEN",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            loaded = load_group_configs(path)

        self.assertEqual(loaded[0].connection_kind, "remote_bridge")
        self.assertEqual(loaded[0].command, [])
        self.assertEqual(loaded[0].endpoint, "http://friend.local:8777")
        self.assertEqual(loaded[0].auth_ref, "env:BRIDGE_TOKEN")

    def test_remote_bridge_resident_command_runner_calls_bridge_with_runner_prompt(self):
        calls = []

        def requester(url, headers, payload, timeout_seconds):
            calls.append({"url": url, "headers": headers, "payload": payload, "timeout_seconds": timeout_seconds})
            return {
                "text": '{"message":"원격 Claude가 로비 이벤트를 봤습니다.","kind":"message","readiness":"ready"}',
                "metadata": {"bridge": "friend-mac", "command": "claude -p"},
            }

        runner = RemoteBridgeResidentCommandRunner(
            config(
                provider_kind="claude_code",
                connection_kind="remote_bridge",
                endpoint="http://friend.local:8777",
                auth_ref="literal:bridge-token",
                meeting_id="m1",
                session_id="session-1",
            ),
            requester=requester,
        )

        reply = runner([], "Reply to this AgentsAssemble lobby context.", timeout_seconds=45)

        self.assertEqual(reply, "원격 Claude가 로비 이벤트를 봤습니다.")
        self.assertEqual(calls[0]["url"], "http://friend.local:8777/agentsassemble/run")
        self.assertEqual(calls[0]["headers"]["Authorization"], "Bearer bridge-token")
        self.assertEqual(calls[0]["timeout_seconds"], 30)
        self.assertEqual(calls[0]["payload"]["step"], "lobby")
        self.assertEqual(calls[0]["payload"]["prompt"], "Reply to this AgentsAssemble lobby context.")
        self.assertEqual(calls[0]["payload"]["role"]["id"], "agent-a")
        self.assertEqual(calls[0]["payload"]["session_id"], "session-1")
        self.assertFalse(calls[0]["payload"]["permissions"]["filesystem_write"])
        self.assertNotIn("command", calls[0]["payload"])

    def test_remote_bridge_resident_command_runner_sanitizes_auth_failures(self):
        def requester(url, headers, payload, timeout_seconds):
            raise RuntimeError("Bearer bridge-token rejected by http://friend.local:8777")

        runner = RemoteBridgeResidentCommandRunner(
            config(
                connection_kind="remote_bridge",
                endpoint="http://friend.local:8777",
                auth_ref="literal:bridge-token",
            ),
            requester=requester,
        )

        with self.assertRaisesRegex(RuntimeError, "Remote bridge request failed."):
            runner([], "prompt", timeout_seconds=45)

    def test_remote_bridge_resident_command_runner_rejects_redacted_env_auth_value(self):
        with patch.dict("os.environ", {"BRIDGE_TOKEN": "<redacted>"}, clear=False):
            with self.assertRaisesRegex(ValueError, "available auth_ref"):
                RemoteBridgeResidentCommandRunner(
                    config(
                        connection_kind="remote_bridge",
                        endpoint="http://friend.local:8777",
                        auth_ref="env:BRIDGE_TOKEN",
                    )
                )

    def test_remote_bridge_resident_command_runner_rejects_unsafe_endpoint(self):
        with self.assertRaisesRegex(ValueError, "safe endpoint"):
            RemoteBridgeResidentCommandRunner(
                config(
                    connection_kind="remote_bridge",
                    endpoint="http://bridge-token@friend.local:8777?secret=1",
                    auth_ref="literal:bridge-token",
                )
            )
