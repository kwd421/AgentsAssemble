import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from agentsassemble.codex_resident import CodexResidentCommandRunner
from agentsassemble.grok_resident import GROK_JSON_PARSE_FAILURE, GrokResidentValueError
from agentsassemble.live_agent_runner import (
    LiveAgentRunner,
    RemoteBridgeResidentCommandRunner,
    ResidentAgentConfig,
    event_reply_candidate,
    flow_event_candidate,
    flow_decision_prompt,
    load_group_configs,
    official_turn_prompt,
    official_turn_request_candidate,
)
from agentsassemble.live_session_transport import JsonlLiveSession


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
        if url.endswith("/official-turn"):
            return {"event": {"id": "official-reply-id"}}
        if url.endswith("/lobby"):
            return {"event": {"id": "reply-id"}}
        if url.endswith("/live-agents") and method == "POST":
            return {"agent": dict(self.register_agent)}
        return {"agent": {"agent_id": "agent-a", "status": (payload or {}).get("status", "online")}}


class FinalOfflineFailureClient(FakeRoomClient):
    def __call__(self, url, *, method="GET", payload=None):
        if url.endswith("/heartbeat") and (payload or {}).get("status") == "offline":
            self.calls.append((url, method, payload))
            raise ConnectionError("room server unavailable during shutdown")
        return super().__call__(url, method=method, payload=payload)


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
        self.assertEqual(runner.last_observed_event_id, "evt2")

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

    def test_runner_recovers_when_lobby_cursor_fell_out_of_bounded_room_tail(self):
        clock = FakeClock()
        room = {
            "agent": {"agent_id": "agent-a", "last_observed_event_id": "evicted-cursor"},
            "lobby_events": [
                {"id": "evt-new", "side": "mine", "name": "나", "message": "tail 밖 cursor 이후 새 이벤트"},
            ],
        }
        client = FakeRoomClient([room])
        runner = LiveAgentRunner(
            config(),
            request_json=client,
            command_runner=lambda command, prompt, *, timeout_seconds: "reply after evicted cursor",
            sleep_fn=clock.sleep,
            now_fn=clock,
        )

        self.assertEqual(runner.run(), 1)

        lobby_payloads = [payload for url, method, payload in client.calls if url.endswith("/lobby")]
        self.assertEqual(lobby_payloads[0]["source_event_id"], "evt-new")

    def test_lobby_candidate_uses_bounded_tail_when_cursor_is_absent(self):
        events = [
            {"id": "evt-new", "side": "mine", "name": "나", "message": "tail 안의 새 말"},
        ]

        self.assertEqual(
            event_reply_candidate(events, "agent-a", "Agent A", "evicted-cursor", max_chain_depth=1),
            events[0],
        )

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

    def test_flow_runner_wait_advances_cursor_without_lobby_reply(self):
        clock = FakeClock()
        room = {
            "agent": {"agent_id": "agent-a", "engagement_mode": "flow"},
            "lobby_events": [
                {
                    "id": "flow-start",
                    "name": "Play Mode",
                    "message": "자유토론 시작",
                    "flow_id": "flow-1",
                    "flow_event_type": "started",
                    "flow_topic": "고죠 vs 스쿠나",
                },
                {"id": "evt1", "name": "나", "message": "스쿠나가 이김?", "auto_chain_depth": 0},
            ],
        }
        client = FakeRoomClient([room])
        runner = LiveAgentRunner(
            config(engagement_mode="flow"),
            request_json=client,
            command_runner=lambda command, prompt, *, timeout_seconds: '{"action":"wait","reason":"아직 지켜봄","message":""}',
            sleep_fn=clock.sleep,
            now_fn=clock,
        )

        self.assertEqual(runner.run(), 0)

        self.assertFalse([call for call in client.calls if call[0].endswith("/lobby")])
        self.assertIsNotNone(runner.last_reply_at)
        observed = [
            payload.get("last_observed_event_id")
            for url, method, payload in client.calls
            if url.endswith("/heartbeat") and payload and payload.get("last_observed_event_id")
        ]
        self.assertIn("flow-start", observed)

    def test_flow_runner_posts_visible_message_with_action_metadata(self):
        clock = FakeClock()
        room = {
            "agent": {"agent_id": "agent-a", "engagement_mode": "flow"},
            "lobby_events": [
                {
                    "id": "flow-start",
                    "name": "Play Mode",
                    "message": "자유토론 시작",
                    "flow_id": "flow-1",
                    "flow_event_type": "started",
                    "flow_topic": "고죠 vs 스쿠나",
                    "flow_max_total_turns": 6,
                    "flow_max_agent_turns": 3,
                }
            ],
        }
        client = FakeRoomClient([room])

        def command_runner(command, prompt, *, timeout_seconds):
            self.assertIn("고죠 vs 스쿠나", prompt)
            self.assertIn("Return one JSON object only", prompt)
            return '{"action":"challenge","target_agent_id":"agent-b","reason":"반례 부족","message":"그 전제는 좀 약해. 무량공처 대응부터 봐야 해."}'

        runner = LiveAgentRunner(
            config(engagement_mode="flow"),
            request_json=client,
            command_runner=command_runner,
            sleep_fn=clock.sleep,
            now_fn=clock,
        )

        self.assertEqual(runner.run(), 1)

        lobby_payloads = [payload for url, method, payload in client.calls if url.endswith("/lobby")]
        self.assertEqual(lobby_payloads[0]["message"], "그 전제는 좀 약해. 무량공처 대응부터 봐야 해.")
        self.assertEqual(lobby_payloads[0]["source_event_id"], "flow-start")
        self.assertEqual(lobby_payloads[0]["auto_chain_depth"], 1)
        self.assertEqual(lobby_payloads[0]["flow_id"], "flow-1")
        self.assertEqual(lobby_payloads[0]["flow_action"], "challenge")
        self.assertEqual(lobby_payloads[0]["flow_reason"], "반례 부족")
        self.assertEqual(lobby_payloads[0]["target_agent_id"], "agent-b")

    def test_flow_runner_uses_flow_cooldown_option(self):
        clock = FakeClock()
        room = {
            "agent": {"agent_id": "agent-a", "engagement_mode": "flow"},
            "lobby_events": [
                {
                    "id": "flow-start",
                    "name": "Play Mode",
                    "message": "자유토론 시작",
                    "flow_id": "flow-1",
                    "flow_event_type": "started",
                    "flow_topic": "고죠 vs 스쿠나",
                    "flow_cooldown": 60,
                }
            ],
        }
        client = FakeRoomClient([room])

        def command_runner(command, prompt, *, timeout_seconds):
            raise AssertionError("provider should not be called during flow cooldown")

        runner = LiveAgentRunner(
            config(engagement_mode="flow", cooldown=0),
            request_json=client,
            command_runner=command_runner,
            sleep_fn=clock.sleep,
            now_fn=clock,
        )
        runner.last_reply_at = clock.now

        self.assertEqual(runner.run(), 0)
        self.assertFalse([call for call in client.calls if call[0].endswith("/lobby")])

    def test_flow_runner_reacts_to_silence_nudge(self):
        clock = FakeClock()
        room = {
            "agent": {"agent_id": "agent-a", "engagement_mode": "flow"},
            "lobby_events": [
                {
                    "id": "flow-start",
                    "name": "Play Mode",
                    "message": "자유토론 시작",
                    "flow_id": "flow-1",
                    "flow_event_type": "started",
                    "flow_topic": "고죠 vs 스쿠나",
                },
                {
                    "id": "flow-nudge",
                    "name": "Play Mode",
                    "message": "열린 쟁점을 하나 골라 다음 말을 이어가세요.",
                    "flow_id": "flow-1",
                    "flow_event_type": "nudge",
                    "auto_chain_depth": 1,
                },
            ],
        }
        client = FakeRoomClient([room])
        runner = LiveAgentRunner(
            config(engagement_mode="flow", max_chain_depth=1),
            request_json=client,
            command_runner=lambda command, prompt, *, timeout_seconds: '{"action":"speak","message":"그럼 영역전개 변수부터 보자."}',
            sleep_fn=clock.sleep,
            now_fn=clock,
        )
        runner.last_observed_event_id = "flow-start"

        self.assertEqual(runner.run(), 1)

        lobby_payloads = [payload for url, method, payload in client.calls if url.endswith("/lobby")]
        self.assertEqual(lobby_payloads[0]["source_event_id"], "flow-nudge")
        self.assertEqual(lobby_payloads[0]["auto_chain_depth"], 2)

    def test_flow_runner_does_not_call_provider_after_turn_budget_is_exhausted(self):
        clock = FakeClock()
        room = {
            "agent": {"agent_id": "agent-a", "engagement_mode": "flow"},
            "lobby_events": [
                {
                    "id": "flow-start",
                    "name": "Play Mode",
                    "message": "자유토론 시작",
                    "flow_id": "flow-1",
                    "flow_event_type": "started",
                    "flow_topic": "고죠 vs 스쿠나",
                    "flow_max_total_turns": 10,
                    "flow_max_agent_turns": 1,
                },
                {
                    "id": "agent-a-old",
                    "actor_id": "agent-a",
                    "name": "Agent A",
                    "message": "이미 한 번 말함",
                    "flow_id": "flow-1",
                    "flow_action": "speak",
                },
                {"id": "evt2", "actor_id": "agent-b", "name": "Agent B", "message": "반박해봐"},
            ],
        }
        client = FakeRoomClient([room])

        def command_runner(command, prompt, *, timeout_seconds):
            raise AssertionError("provider should not be called after flow agent turn budget is exhausted")

        runner = LiveAgentRunner(
            config(engagement_mode="flow"),
            request_json=client,
            command_runner=command_runner,
            sleep_fn=clock.sleep,
            now_fn=clock,
        )

        self.assertEqual(runner.run(), 0)
        self.assertFalse([call for call in client.calls if call[0].endswith("/lobby")])

    def test_flow_runner_considers_unmentioned_flow_chatter(self):
        clock = FakeClock()
        provider_called = False
        room = {
            "agent": {"agent_id": "agent-a", "engagement_mode": "flow"},
            "lobby_events": [
                {
                    "id": "flow-start",
                    "name": "Play Mode",
                    "message": "자유토론 시작",
                    "flow_id": "flow-1",
                    "flow_event_type": "started",
                    "flow_topic": "고죠 vs 스쿠나",
                },
                {
                    "id": "agent-b-msg",
                    "actor_id": "agent-b",
                    "name": "Agent B",
                    "message": "내 생각엔 스쿠나가 한 수 위야.",
                    "flow_id": "flow-1",
                    "flow_action": "speak",
                },
            ],
        }
        client = FakeRoomClient([room])

        def command_runner(command, prompt, *, timeout_seconds):
            nonlocal provider_called
            provider_called = True
            self.assertIn("내 생각엔 스쿠나가 한 수 위야.", prompt)
            return '{"action":"wait","reason":"더 볼게","message":""}'

        runner = LiveAgentRunner(
            config(engagement_mode="flow"),
            request_json=client,
            command_runner=command_runner,
            sleep_fn=clock.sleep,
            now_fn=clock,
        )
        runner.last_observed_event_id = "flow-start"

        self.assertEqual(runner.run(), 0)
        self.assertTrue(provider_called)
        self.assertFalse([call for call in client.calls if call[0].endswith("/lobby")])
        observed = [
            payload.get("last_observed_event_id")
            for url, method, payload in client.calls
            if url.endswith("/heartbeat") and payload and payload.get("last_observed_event_id")
        ]
        self.assertIn("agent-b-msg", observed)

    def test_flow_candidate_offers_invisible_idle_tick_when_room_caught_up(self):
        events = [
            {
                "id": "flow-start",
                "name": "Play Mode",
                "message": "자유토론 시작",
                "flow_id": "flow-1",
                "flow_meeting_id": "m1",
                "flow_event_type": "started",
                "flow_topic": "고죠 vs 스쿠나",
            },
            {
                "id": "agent-b-msg",
                "actor_id": "agent-b",
                "name": "Agent B",
                "message": "스쿠나가 템포를 뺏는다니까.",
                "flow_id": "flow-1",
                "flow_meeting_id": "m1",
                "flow_action": "speak",
                "auto_chain_depth": 3,
            },
        ]

        candidate = flow_event_candidate(
            events,
            "agent-a",
            "Agent A",
            "agent-b-msg",
            max_chain_depth=1,
            meeting_id="m1",
        )

        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertEqual(candidate["id"], "agent-b-msg")
        self.assertEqual(candidate["flow_event_type"], "tick")
        self.assertEqual(candidate["auto_chain_depth"], 0)
        self.assertIn("판단", candidate["message"])

    def test_flow_runner_can_speak_on_invisible_idle_tick(self):
        clock = FakeClock()
        room = {
            "agent": {"agent_id": "agent-a", "engagement_mode": "flow"},
            "lobby_events": [
                {
                    "id": "flow-start",
                    "name": "Play Mode",
                    "message": "자유토론 시작",
                    "flow_id": "flow-1",
                    "flow_meeting_id": "m1",
                    "flow_event_type": "started",
                    "flow_topic": "고죠 vs 스쿠나",
                },
                {
                    "id": "agent-b-msg",
                    "actor_id": "agent-b",
                    "name": "Agent B",
                    "message": "스쿠나가 템포 리셋하면 고죠도 흔들리지.",
                    "flow_id": "flow-1",
                    "flow_meeting_id": "m1",
                    "flow_action": "speak",
                    "auto_chain_depth": 3,
                },
            ],
        }
        client = FakeRoomClient([room])

        def command_runner(command, prompt, *, timeout_seconds):
            self.assertIn("방 전체 맥락", prompt)
            self.assertIn("스쿠나가 템포 리셋하면", prompt)
            return '{"action":"challenge","message":"그 리셋도 무한을 뚫을 전제가 있어야 먹히지."}'

        runner = LiveAgentRunner(
            config(engagement_mode="flow", max_chain_depth=1),
            request_json=client,
            command_runner=command_runner,
            sleep_fn=clock.sleep,
            now_fn=clock,
        )
        runner.last_observed_event_id = "agent-b-msg"

        self.assertEqual(runner.run(), 1)

        lobby_payloads = [payload for url, method, payload in client.calls if url.endswith("/lobby")]
        self.assertEqual(lobby_payloads[0]["source_event_id"], "agent-b-msg")
        self.assertEqual(lobby_payloads[0]["auto_chain_depth"], 1)
        self.assertEqual(lobby_payloads[0]["flow_action"], "challenge")

    def test_flow_runner_drops_reply_when_flow_finished_during_provider_call(self):
        clock = FakeClock()
        room = {
            "agent": {"agent_id": "agent-a", "engagement_mode": "flow"},
            "lobby_events": [
                {
                    "id": "flow-start",
                    "name": "Play Mode",
                    "message": "자유토론 시작",
                    "flow_id": "flow-1",
                    "flow_meeting_id": "m1",
                    "flow_event_type": "started",
                    "flow_topic": "고죠 vs 스쿠나",
                }
            ],
        }
        client = FakeRoomClient([room, room])

        def command_runner(command, prompt, *, timeout_seconds):
            room["lobby_events"].append(
                {
                    "id": "flow-finished",
                    "name": "Play Mode",
                    "message": "시간제 자유토론 종료",
                    "flow_id": "flow-1",
                    "flow_meeting_id": "m1",
                    "flow_event_type": "finished",
                }
            )
            return '{"action":"speak","message":"늦게 온 답변"}'

        runner = LiveAgentRunner(
            config(engagement_mode="flow", meeting_id="m1"),
            request_json=client,
            command_runner=command_runner,
            sleep_fn=clock.sleep,
            now_fn=clock,
        )

        self.assertEqual(runner.run(), 0)
        self.assertFalse([call for call in client.calls if call[0].endswith("/lobby")])

    def test_flow_decision_prompt_keeps_play_mode_unofficial(self):
        prompt = flow_decision_prompt(
            config(engagement_mode="flow", meeting_id="m1"),
            {
                "meeting_id": "m1",
                "lobby_events": [
                    {
                        "id": "flow-start",
                        "name": "Play Mode",
                        "message": "자유토론 시작",
                        "flow_id": "flow-1",
                        "flow_event_type": "started",
                        "flow_topic": "고죠 vs 스쿠나",
                    }
                ],
            },
            {"id": "flow-start", "name": "Play Mode", "message": "자유토론 시작"},
        )

        self.assertIn("Play Mode lobby conversation", prompt)
        self.assertIn("not an official meeting record", prompt)
        self.assertIn('"action"', prompt)
        self.assertIn('"message"', prompt)

    def test_flow_decision_prompt_includes_configured_persona_card_context(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            card_path = Path(temp_dir) / "card.json"
            card_path.write_text(
                json.dumps(
                    {
                        "id": "persona-1",
                        "display_name": "Tsukishiro Yanagi",
                        "description": "Keeps the room calm.",
                        "personality": "Precise, dry, and protective.",
                        "scenario": "A late-night Play Mode debate room.",
                        "system_prompt": "Stay in character.",
                        "lorebook": [
                            {
                                "key": "Yanagi",
                                "content": "Yanagi calls out vague claims with quiet pressure.",
                                "always_active": True,
                                "insert_order": 1,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            prompt = flow_decision_prompt(
                config(engagement_mode="flow", meeting_id="m1", persona_path=str(card_path)),
                {
                    "meeting_id": "m1",
                    "lobby_events": [
                        {
                            "id": "flow-start",
                            "name": "Play Mode",
                            "message": "자유토론 시작",
                            "flow_id": "flow-1",
                            "flow_event_type": "started",
                            "flow_topic": "누가 더 수상한가",
                        }
                    ],
                },
                {"id": "flow-start", "name": "Play Mode", "message": "Yanagi, 의견 줘."},
            )

        self.assertIn("Play Mode persona card", prompt)
        self.assertIn("Tsukishiro Yanagi", prompt)
        self.assertIn("Precise, dry, and protective.", prompt)
        self.assertIn("Yanagi calls out vague claims", prompt)

    def test_flow_decision_prompt_loads_persona_id_from_default_persona_store(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            card_path = root / ".agentsassemble" / "personas" / "persona-1" / "card.json"
            card_path.parent.mkdir(parents=True)
            card_path.write_text(
                json.dumps({"id": "persona-1", "display_name": "Persona One", "personality": "Uses short dry replies."}),
                encoding="utf-8",
            )

            with patch("agentsassemble.live_agent_runner.Path.cwd", return_value=root):
                prompt = flow_decision_prompt(
                    config(engagement_mode="flow", meeting_id="m1", persona_id="persona-1"),
                    {
                        "meeting_id": "m1",
                        "lobby_events": [
                            {
                                "id": "flow-start",
                                "name": "Play Mode",
                                "message": "자유토론 시작",
                                "flow_id": "flow-1",
                                "flow_event_type": "started",
                            }
                        ],
                    },
                    {"id": "flow-start", "name": "Play Mode", "message": "한마디 해줘."},
                )

        self.assertIn("Persona One", prompt)
        self.assertIn("Uses short dry replies.", prompt)

    def test_flow_decision_prompt_work_speech_only_excludes_raw_lore_body(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            card_path = Path(temp_dir) / "card.json"
            card_path.write_text(
                json.dumps(
                    {
                        "id": "persona-1",
                        "display_name": "Tsukishiro Yanagi",
                        "description": "RAW_DESCRIPTION_MARKER",
                        "personality": "RAW_PERSONALITY_MARKER",
                        "scenario": "RAW_SCENARIO_MARKER",
                        "lorebook": [
                            {
                                "key": "Yanagi",
                                "content": "RAW_LORE_MARKER",
                                "always_active": True,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            prompt = flow_decision_prompt(
                config(
                    engagement_mode="flow",
                    meeting_id="m1",
                    persona_path=str(card_path),
                    character_mode="work_speech_only",
                ),
                {
                    "meeting_id": "m1",
                    "lobby_events": [{"id": "flow-start", "name": "Play Mode", "message": "Yanagi"}],
                },
                {"id": "flow-start", "name": "Play Mode", "message": "Yanagi"},
            )

        self.assertIn("Character speech style", prompt)
        self.assertIn("Tsukishiro Yanagi", prompt)
        self.assertNotIn("RAW_PERSONALITY_MARKER", prompt)
        self.assertNotIn("RAW_DESCRIPTION_MARKER", prompt)
        self.assertNotIn("RAW_SCENARIO_MARKER", prompt)
        self.assertNotIn("RAW_LORE_MARKER", prompt)

    def test_flow_decision_prompt_uses_configured_first_message_index(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            card_path = Path(temp_dir) / "card.json"
            card_path.write_text(
                json.dumps(
                    {
                        "id": "persona-1",
                        "display_name": "Tsukishiro Yanagi",
                        "first_message": "Default greeting.",
                        "alternate_greetings": ["Alt zero.", "Alt one."],
                    }
                ),
                encoding="utf-8",
            )

            prompt = flow_decision_prompt(
                config(
                    engagement_mode="flow",
                    meeting_id="m1",
                    persona_path=str(card_path),
                    first_message_index=2,
                ),
                {
                    "meeting_id": "m1",
                    "lobby_events": [{"id": "flow-start", "name": "Play Mode", "message": "Yanagi"}],
                },
                {"id": "flow-start", "name": "Play Mode", "message": "Yanagi"},
            )

        self.assertIn("Alt one.", prompt)
        self.assertNotIn("Default greeting.", prompt)

    def test_flow_persona_stateful_runner_does_not_answer_official_turn_request(self):
        clock = FakeClock()
        room = {
            "agent": {"agent_id": "agent-a", "engagement_mode": "flow", "meeting_id": "m1"},
            "live_events": [
                {
                    "id": "turn-1",
                    "kind": "live_agent_turn_request",
                    "target_agent_id": "agent-a",
                    "meeting_id": "m1",
                    "content": "공식 기록으로 답해줘",
                }
            ],
            "lobby_events": [],
        }
        client = FakeRoomClient([room])

        def command_runner(command, prompt, *, timeout_seconds):
            raise AssertionError("stateful persona runner must not answer official turns in flow mode")

        runner = LiveAgentRunner(
            config(
                engagement_mode="flow",
                meeting_id="m1",
                connection_kind="live_session",
                provider_kind="codex_live_session",
                persona_path="/tmp/persona/card.json",
            ),
            request_json=client,
            command_runner=command_runner,
            sleep_fn=clock.sleep,
            now_fn=clock,
        )

        self.assertEqual(runner.run(), 0)
        self.assertFalse([call for call in client.calls if call[0].endswith("/official-turn")])
        self.assertEqual(runner.last_observed_live_event_id, "turn-1")
        online_heartbeats = [
            payload
            for url, method, payload in client.calls
            if url.endswith("/heartbeat") and payload.get("status") == "online"
        ]
        self.assertTrue(online_heartbeats)
        self.assertEqual(online_heartbeats[-1]["last_attention"], "persona_context_blocked_official_turn")
        self.assertEqual(online_heartbeats[-1]["last_observed_live_event_id"], "turn-1")

    def test_flow_persona_off_stateful_runner_can_answer_official_turn_request(self):
        clock = FakeClock()
        room = {
            "agent": {"agent_id": "agent-a", "engagement_mode": "flow", "meeting_id": "m1"},
            "live_events": [
                {
                    "id": "turn-1",
                    "kind": "live_agent_turn_request",
                    "target_agent_id": "agent-a",
                    "meeting_id": "m1",
                    "content": "공식 기록으로 답해줘",
                }
            ],
            "lobby_events": [],
        }
        client = FakeRoomClient([room])

        runner = LiveAgentRunner(
            config(
                engagement_mode="flow",
                meeting_id="m1",
                connection_kind="live_session",
                provider_kind="codex_live_session",
                persona_path="/tmp/persona/card.json",
                character_mode="off",
            ),
            request_json=client,
            command_runner=lambda command, prompt, *, timeout_seconds: "공식 답변입니다.",
            sleep_fn=clock.sleep,
            now_fn=clock,
        )

        self.assertEqual(runner.run(), 1)
        self.assertTrue([call for call in client.calls if call[0].endswith("/official-turn")])

    def test_official_turn_can_use_dedicated_command_timeout(self):
        clock = FakeClock()
        room = {
            "agent": {"agent_id": "agent-a", "engagement_mode": "moderator_called", "meeting_id": "m1"},
            "lobby_events": [],
            "live_events": [
                {
                    "id": "turn-request-1",
                    "kind": "live_agent_turn_request",
                    "meeting_id": "m1",
                    "target_agent_id": "agent-a",
                    "content": "공식 답변해줘",
                }
            ],
        }
        client = FakeRoomClient([room])
        command_timeouts = []

        def command_runner(command, prompt, *, timeout_seconds):
            del command, prompt
            command_timeouts.append(timeout_seconds)
            return "공식 답변입니다."

        runner = LiveAgentRunner(
            config(
                engagement_mode="moderator_called",
                meeting_id="m1",
                timeout_seconds=5,
                official_turn_timeout_seconds=17,
            ),
            request_json=client,
            command_runner=command_runner,
            sleep_fn=clock.sleep,
            now_fn=clock,
        )

        self.assertEqual(runner.run(), 1)
        self.assertEqual(command_timeouts, [17])

    def test_official_turn_timeout_defaults_to_general_command_timeout(self):
        clock = FakeClock()
        room = {
            "agent": {"agent_id": "agent-a", "engagement_mode": "moderator_called", "meeting_id": "m1"},
            "lobby_events": [],
            "live_events": [
                {
                    "id": "turn-request-1",
                    "kind": "live_agent_turn_request",
                    "meeting_id": "m1",
                    "target_agent_id": "agent-a",
                    "content": "공식 답변해줘",
                }
            ],
        }
        client = FakeRoomClient([room])
        command_timeouts = []

        def command_runner(command, prompt, *, timeout_seconds):
            del command, prompt
            command_timeouts.append(timeout_seconds)
            return "공식 답변입니다."

        runner = LiveAgentRunner(
            config(engagement_mode="moderator_called", meeting_id="m1", timeout_seconds=5),
            request_json=client,
            command_runner=command_runner,
            sleep_fn=clock.sleep,
            now_fn=clock,
        )

        self.assertEqual(runner.run(), 1)
        self.assertEqual(command_timeouts, [5])

    def test_lobby_reply_keeps_general_command_timeout_when_official_timeout_is_set(self):
        clock = FakeClock()
        room = {"lobby_events": [{"id": "evt1", "name": "나", "message": "로비는 빠르게 답해줘"}]}
        client = FakeRoomClient([room])
        command_timeouts = []

        def command_runner(command, prompt, *, timeout_seconds):
            del command, prompt
            command_timeouts.append(timeout_seconds)
            return "로비 답변입니다."

        runner = LiveAgentRunner(
            config(timeout_seconds=5, official_turn_timeout_seconds=17),
            request_json=client,
            command_runner=command_runner,
            sleep_fn=clock.sleep,
            now_fn=clock,
        )

        self.assertEqual(runner.run(), 1)
        self.assertEqual(command_timeouts, [5])

    def test_flow_lobby_decision_keeps_general_command_timeout_when_official_timeout_is_set(self):
        clock = FakeClock()
        room = {
            "meeting_id": "m1",
            "lobby_events": [
                {
                    "id": "flow-start",
                    "kind": "system",
                    "message": "flow started",
                    "flow_id": "flow-1",
                    "flow_meeting_id": "m1",
                    "flow_event_type": "started",
                },
                {"id": "evt1", "name": "나", "message": "자연스럽게 이어가줘", "flow_meeting_id": "m1"},
            ],
            "live_events": [],
        }
        client = FakeRoomClient([room])
        command_timeouts = []

        def command_runner(command, prompt, *, timeout_seconds):
            del command, prompt
            command_timeouts.append(timeout_seconds)
            return '{"action":"speak","message":"좋아, 여기서 이어갈게."}'

        runner = LiveAgentRunner(
            config(engagement_mode="flow", meeting_id="m1", timeout_seconds=5, official_turn_timeout_seconds=17),
            request_json=client,
            command_runner=command_runner,
            sleep_fn=clock.sleep,
            now_fn=clock,
        )

        self.assertEqual(runner.run(), 1)
        self.assertEqual(command_timeouts, [5])

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

    def test_grok_official_turn_failure_records_safe_category_without_reply(self):
        clock = FakeClock()
        room = {
            "agent": {"agent_id": "agent-a", "engagement_mode": "moderator_called", "meeting_id": "m1"},
            "lobby_events": [],
            "live_events": [
                {
                    "id": "turn-request-1",
                    "kind": "live_agent_turn_request",
                    "meeting_id": "m1",
                    "target_agent_id": "agent-a",
                    "content": "공식 답변해줘",
                }
            ],
        }
        client = FakeRoomClient([room])

        def fail_grok(command, prompt, *, timeout_seconds):
            del command, prompt, timeout_seconds
            raise GrokResidentValueError(
                "Grok live session returned invalid JSON stdout.",
                category=GROK_JSON_PARSE_FAILURE,
            )

        runner = LiveAgentRunner(
            config(
                engagement_mode="moderator_called",
                meeting_id="m1",
                provider_kind="grok_live_session",
                connection_kind="live_session",
                command=["grok"],
            ),
            request_json=client,
            command_runner=fail_grok,
            sleep_fn=clock.sleep,
            now_fn=clock,
        )

        self.assertEqual(runner.run(), 0)

        self.assertEqual([call for call in client.calls if call[0].endswith("/official-turn")], [])
        error_payloads = [
            payload
            for url, method, payload in client.calls
            if url.endswith("/heartbeat") and payload["status"] == "error"
        ]
        self.assertEqual(error_payloads[-1]["last_error"], GROK_JSON_PARSE_FAILURE)
        self.assertEqual(error_payloads[-1]["last_observed_live_event_id"], "turn-request-1")
        self.assertEqual(runner.last_error, GROK_JSON_PARSE_FAILURE)

    def test_runner_does_not_mask_command_failure_when_error_heartbeat_fails(self):
        clock = FakeClock()
        calls = []
        room = {"lobby_events": [{"id": "evt1", "name": "나", "message": "provider 실패를 유지해"}]}

        def request_json(url, *, method="GET", payload=None):
            calls.append((url, method, payload))
            if url.endswith("/live-agents") and method == "POST":
                return {"agent": {"agent_id": "agent-a", "status": "online"}}
            if url.endswith("/room"):
                return room
            if url.endswith("/heartbeat"):
                if (payload or {}).get("status") == "error":
                    raise ConnectionError("error heartbeat failed")
                return {"agent": {"agent_id": "agent-a", "status": (payload or {}).get("status", "online")}}
            return {}

        def fail_command(command, prompt, *, timeout_seconds):
            del command, prompt, timeout_seconds
            raise RuntimeError("provider boom")

        runner = LiveAgentRunner(
            config(),
            request_json=request_json,
            command_runner=fail_command,
            sleep_fn=clock.sleep,
            now_fn=clock,
        )

        try:
            replies = runner.run()
        except Exception as error:  # pragma: no cover - assertion path for clearer RED output
            self.fail(f"error heartbeat failure masked the provider command error: {error}")
        self.assertEqual(replies, 0)
        self.assertEqual(runner.last_error, "provider boom")
        self.assertIsNotNone(runner.last_error_at)
        heartbeat_payloads = [payload for url, method, payload in calls if url.endswith("/heartbeat")]
        self.assertEqual(heartbeat_payloads[-1]["status"], "offline")

    def test_runner_retries_command_error_heartbeat_during_failure_backoff(self):
        clock = FakeClock()
        calls = []
        room = {"lobby_events": [{"id": "evt1", "name": "나", "message": "provider 실패 후 대기"}]}
        failed_error_heartbeat = False

        def request_json(url, *, method="GET", payload=None):
            nonlocal failed_error_heartbeat
            calls.append((url, method, payload))
            if url.endswith("/live-agents") and method == "POST":
                return {"agent": {"agent_id": "agent-a", "status": "online"}}
            if url.endswith("/room"):
                return room
            if url.endswith("/heartbeat"):
                if (payload or {}).get("status") == "error" and not failed_error_heartbeat:
                    failed_error_heartbeat = True
                    raise ConnectionError("first error heartbeat failed")
                return {"agent": {"agent_id": "agent-a", "status": (payload or {}).get("status", "online")}}
            return {}

        def fail_command(command, prompt, *, timeout_seconds):
            del command, prompt, timeout_seconds
            raise RuntimeError("provider boom")

        runner = LiveAgentRunner(
            config(max_ticks=2, poll_interval=2.0, heartbeat_interval=1.0, cooldown=5.0),
            request_json=request_json,
            command_runner=fail_command,
            sleep_fn=clock.sleep,
            now_fn=clock,
        )

        try:
            replies = runner.run()
        except Exception as error:  # pragma: no cover - assertion path for clearer RED output
            self.fail(f"error heartbeat failure stopped failure-backoff retry evidence: {error}")
        self.assertEqual(replies, 0)

        error_payloads = [
            payload for url, method, payload in calls if url.endswith("/heartbeat") and payload["status"] == "error"
        ]
        self.assertEqual(len(error_payloads), 2)
        self.assertEqual(error_payloads[-1]["last_error"], "provider boom")
        self.assertEqual(error_payloads[-1]["last_observed_event_id"], "evt1")

    def test_runner_does_not_record_error_when_command_fails_after_stop(self):
        clock = FakeClock()
        stop_event = threading.Event()
        room = {"lobby_events": [{"id": "evt1", "name": "나", "message": "중지 중"}]}
        client = FakeRoomClient([room])

        def command_runner(command, prompt, *, timeout_seconds):
            del command, prompt, timeout_seconds
            stop_event.set()
            raise RuntimeError("closed during shutdown")

        runner = LiveAgentRunner(
            config(),
            request_json=client,
            command_runner=command_runner,
            sleep_fn=clock.sleep,
            now_fn=clock,
            stop_event=stop_event,
        )

        self.assertEqual(runner.run(), 0)

        heartbeat_payloads = [payload for url, method, payload in client.calls if url.endswith("/heartbeat")]
        self.assertNotIn("error", [payload["status"] for payload in heartbeat_payloads])
        self.assertEqual(heartbeat_payloads[-1]["status"], "offline")

    def test_runner_does_not_mask_success_when_final_offline_heartbeat_fails(self):
        clock = FakeClock()
        room = {"lobby_events": [{"id": "evt1", "name": "나", "message": "답하고 종료"}]}
        client = FinalOfflineFailureClient([room])
        runner = LiveAgentRunner(
            config(),
            request_json=client,
            command_runner=lambda command, prompt, *, timeout_seconds: "reply before shutdown",
            sleep_fn=clock.sleep,
            now_fn=clock,
        )

        self.assertEqual(runner.run(), 1)

        heartbeat_statuses = [
            payload["status"] for url, method, payload in client.calls if url.endswith("/heartbeat")
        ]
        self.assertEqual(heartbeat_statuses[-1], "offline")

    def test_runner_does_not_mask_lobby_reply_when_success_heartbeat_fails(self):
        clock = FakeClock()
        calls = []
        room = {"lobby_events": [{"id": "evt1", "name": "나", "message": "답변은 성공했어"}]}

        def request_json(url, *, method="GET", payload=None):
            calls.append((url, method, payload))
            if url.endswith("/live-agents") and method == "POST":
                return {"agent": {"agent_id": "agent-a", "status": "online"}}
            if url.endswith("/room"):
                return room
            if url.endswith("/lobby"):
                return {"event": {"id": "reply-id"}}
            if url.endswith("/heartbeat"):
                if (payload or {}).get("status") == "online" and (payload or {}).get("last_reply_at"):
                    raise ConnectionError("success heartbeat failed")
                return {"agent": {"agent_id": "agent-a", "status": (payload or {}).get("status", "online")}}
            return {}

        runner = LiveAgentRunner(
            config(),
            request_json=request_json,
            command_runner=lambda command, prompt, *, timeout_seconds: "posted reply",
            sleep_fn=clock.sleep,
            now_fn=clock,
        )

        try:
            replies = runner.run()
        except Exception as error:  # pragma: no cover - assertion path for clearer RED output
            self.fail(f"success heartbeat failure masked the posted lobby reply: {error}")
        self.assertEqual(replies, 1)
        self.assertEqual(runner.last_error, "")
        lobby_payloads = [payload for url, method, payload in calls if url.endswith("/lobby")]
        self.assertEqual(lobby_payloads[0]["source_event_id"], "evt1")

    def test_runner_does_not_mask_lobby_reply_when_initial_working_heartbeat_fails(self):
        clock = FakeClock()
        calls = []
        command_calls = []
        timeline = []
        room = {"lobby_events": [{"id": "evt1", "name": "나", "message": "working 증거 실패해도 답해"}]}

        def request_json(url, *, method="GET", payload=None):
            calls.append((url, method, payload))
            if url.endswith("/live-agents") and method == "POST":
                return {"agent": {"agent_id": "agent-a", "status": "online"}}
            if url.endswith("/room"):
                return room
            if url.endswith("/lobby"):
                return {"event": {"id": "reply-id"}}
            if url.endswith("/heartbeat"):
                if (payload or {}).get("status") == "working":
                    timeline.append("working-heartbeat")
                    raise ConnectionError("working heartbeat failed")
                return {"agent": {"agent_id": "agent-a", "status": (payload or {}).get("status", "online")}}
            return {}

        def command_runner(command, prompt, *, timeout_seconds):
            timeline.append("command")
            command_calls.append((command, prompt, timeout_seconds))
            return "posted after working heartbeat failure"

        runner = LiveAgentRunner(
            config(),
            request_json=request_json,
            command_runner=command_runner,
            sleep_fn=clock.sleep,
            now_fn=clock,
        )

        try:
            replies = runner.run()
        except Exception as error:  # pragma: no cover - assertion path for clearer RED output
            self.fail(f"initial working heartbeat failure masked the lobby reply: {error}")
        self.assertEqual(replies, 1)
        self.assertEqual(len(command_calls), 1)
        self.assertLess(timeline.index("working-heartbeat"), timeline.index("command"))
        self.assertEqual(runner.last_error, "")
        self.assertIsNone(runner.last_error_at)
        working_payloads = [
            payload for url, method, payload in calls if url.endswith("/heartbeat") and payload["status"] == "working"
        ]
        self.assertEqual(working_payloads[0]["last_observed_event_id"], "evt1")
        error_payloads = [
            payload for url, method, payload in calls if url.endswith("/heartbeat") and payload["status"] == "error"
        ]
        self.assertEqual(error_payloads, [])
        lobby_payloads = [payload for url, method, payload in calls if url.endswith("/lobby")]
        self.assertEqual(lobby_payloads[0]["source_event_id"], "evt1")

    def test_runner_does_not_mask_official_reply_when_success_heartbeat_fails(self):
        clock = FakeClock()
        calls = []
        room = {
            "agent": {"agent_id": "agent-a", "engagement_mode": "moderator_called", "meeting_id": "m1"},
            "lobby_events": [],
            "live_events": [
                {
                    "id": "turn-request-1",
                    "kind": "live_agent_turn_request",
                    "meeting_id": "m1",
                    "target_agent_id": "agent-a",
                    "content": "공식 답변 성공을 유지해",
                }
            ],
        }

        def request_json(url, *, method="GET", payload=None):
            calls.append((url, method, payload))
            if url.endswith("/live-agents") and method == "POST":
                return {"agent": {"agent_id": "agent-a", "status": "online"}}
            if url.endswith("/room"):
                return room
            if url.endswith("/official-turn"):
                return {"event": {"id": "official-reply-id"}}
            if url.endswith("/heartbeat"):
                if (payload or {}).get("status") == "online" and (payload or {}).get("last_reply_at"):
                    raise ConnectionError("success heartbeat failed")
                return {"agent": {"agent_id": "agent-a", "status": (payload or {}).get("status", "online")}}
            return {}

        runner = LiveAgentRunner(
            config(engagement_mode="moderator_called", meeting_id="m1"),
            request_json=request_json,
            command_runner=lambda command, prompt, *, timeout_seconds: "official reply",
            sleep_fn=clock.sleep,
            now_fn=clock,
        )

        try:
            replies = runner.run()
        except Exception as error:  # pragma: no cover - assertion path for clearer RED output
            self.fail(f"success heartbeat failure masked the posted official reply: {error}")
        self.assertEqual(replies, 1)
        self.assertEqual(runner.last_error, "")
        official_payloads = [payload for url, method, payload in calls if url.endswith("/official-turn")]
        self.assertEqual(official_payloads[0]["source_event_id"], "turn-request-1")

    def test_runner_does_not_mask_official_reply_when_initial_working_heartbeat_fails(self):
        clock = FakeClock()
        calls = []
        command_calls = []
        timeline = []
        room = {
            "agent": {"agent_id": "agent-a", "engagement_mode": "moderator_called", "meeting_id": "m1"},
            "lobby_events": [],
            "live_events": [
                {
                    "id": "turn-request-1",
                    "kind": "live_agent_turn_request",
                    "meeting_id": "m1",
                    "target_agent_id": "agent-a",
                    "content": "working 증거 실패해도 공식 답변해",
                }
            ],
        }

        def request_json(url, *, method="GET", payload=None):
            calls.append((url, method, payload))
            if url.endswith("/live-agents") and method == "POST":
                return {"agent": {"agent_id": "agent-a", "status": "online"}}
            if url.endswith("/room"):
                return room
            if url.endswith("/official-turn"):
                return {"event": {"id": "official-reply-id"}}
            if url.endswith("/heartbeat"):
                if (payload or {}).get("status") == "working":
                    timeline.append("working-heartbeat")
                    raise ConnectionError("working heartbeat failed")
                return {"agent": {"agent_id": "agent-a", "status": (payload or {}).get("status", "online")}}
            return {}

        def command_runner(command, prompt, *, timeout_seconds):
            timeline.append("command")
            command_calls.append((command, prompt, timeout_seconds))
            return "official reply after working heartbeat failure"

        runner = LiveAgentRunner(
            config(engagement_mode="moderator_called", meeting_id="m1"),
            request_json=request_json,
            command_runner=command_runner,
            sleep_fn=clock.sleep,
            now_fn=clock,
        )

        try:
            replies = runner.run()
        except Exception as error:  # pragma: no cover - assertion path for clearer RED output
            self.fail(f"initial working heartbeat failure masked the official reply: {error}")
        self.assertEqual(replies, 1)
        self.assertEqual(len(command_calls), 1)
        self.assertLess(timeline.index("working-heartbeat"), timeline.index("command"))
        self.assertEqual(runner.last_error, "")
        self.assertIsNone(runner.last_error_at)
        working_payloads = [
            payload for url, method, payload in calls if url.endswith("/heartbeat") and payload["status"] == "working"
        ]
        self.assertEqual(working_payloads[0]["last_observed_live_event_id"], "turn-request-1")
        error_payloads = [
            payload for url, method, payload in calls if url.endswith("/heartbeat") and payload["status"] == "error"
        ]
        self.assertEqual(error_payloads, [])
        official_payloads = [payload for url, method, payload in calls if url.endswith("/official-turn")]
        self.assertEqual(official_payloads[0]["source_event_id"], "turn-request-1")

    def test_runner_does_not_mask_command_error_when_final_offline_heartbeat_fails(self):
        clock = FakeClock()
        room = {"lobby_events": [{"id": "evt1", "name": "나", "message": "실패 후 종료"}]}
        client = FinalOfflineFailureClient([room])

        def fail_command(command, prompt, *, timeout_seconds):
            raise RuntimeError("provider boom")

        runner = LiveAgentRunner(
            config(),
            request_json=client,
            command_runner=fail_command,
            sleep_fn=clock.sleep,
            now_fn=clock,
        )

        self.assertEqual(runner.run(), 0)

        heartbeat_payloads = [payload for url, method, payload in client.calls if url.endswith("/heartbeat")]
        self.assertEqual([payload["status"] for payload in heartbeat_payloads[-2:]], ["error", "offline"])
        self.assertEqual(heartbeat_payloads[-2]["last_error"], "provider boom")

    def test_runner_does_not_mask_room_failure_when_final_offline_heartbeat_fails(self):
        clock = FakeClock()
        calls = []

        def request_json(url, *, method="GET", payload=None):
            calls.append((url, method, payload))
            if url.endswith("/live-agents") and method == "POST":
                return {"agent": {"agent_id": "agent-a", "status": "online"}}
            if url.endswith("/heartbeat"):
                if (payload or {}).get("status") == "offline":
                    raise ConnectionError("room server unavailable during shutdown")
                return {"agent": {"agent_id": "agent-a", "status": (payload or {}).get("status", "online")}}
            if url.endswith("/room"):
                raise RuntimeError("room read failed")
            return {}

        runner = LiveAgentRunner(
            config(),
            request_json=request_json,
            command_runner=lambda command, prompt, *, timeout_seconds: "unused",
            sleep_fn=clock.sleep,
            now_fn=clock,
        )

        with self.assertRaisesRegex(RuntimeError, "room read failed"):
            runner.run()

        heartbeat_statuses = [payload["status"] for url, method, payload in calls if url.endswith("/heartbeat")]
        self.assertEqual(heartbeat_statuses[-1], "offline")

    def test_runner_survives_transient_room_failure_after_initial_snapshot(self):
        clock = FakeClock()
        calls = []
        room_reads = 0
        command_calls = []

        def request_json(url, *, method="GET", payload=None):
            nonlocal room_reads
            calls.append((url, method, payload))
            if url.endswith("/live-agents") and method == "POST":
                return {"agent": {"agent_id": "agent-a", "status": "online"}}
            if url.endswith("/heartbeat"):
                return {"agent": {"agent_id": "agent-a", "status": (payload or {}).get("status", "online")}}
            if url.endswith("/room"):
                room_reads += 1
                if room_reads == 1:
                    return {"lobby_events": []}
                if room_reads == 2:
                    raise ConnectionError("transient room read failed")
                return {"lobby_events": [{"id": "evt1", "name": "나", "message": "다시 왔어?"}]}
            if url.endswith("/lobby"):
                return {"event": {"id": "reply-id"}}
            return {}

        def command_runner(command, prompt, *, timeout_seconds):
            del command, timeout_seconds
            command_calls.append(prompt)
            return "Agent A recovered"

        runner = LiveAgentRunner(
            config(max_ticks=3, cooldown=0.0),
            request_json=request_json,
            command_runner=command_runner,
            sleep_fn=clock.sleep,
            now_fn=clock,
        )

        self.assertEqual(runner.run(), 1)

        error_payloads = [
            payload for url, method, payload in calls if url.endswith("/heartbeat") and payload["status"] == "error"
        ]
        lobby_payloads = [payload for url, method, payload in calls if url.endswith("/lobby")]
        self.assertEqual(len(error_payloads), 1)
        self.assertEqual(error_payloads[0]["last_error"], "transient room read failed")
        self.assertEqual(len(command_calls), 1)
        self.assertEqual(lobby_payloads[0]["source_event_id"], "evt1")

    def test_runner_redacts_sensitive_transient_room_failure_error(self):
        clock = FakeClock()
        calls = []
        room_reads = 0

        def request_json(url, *, method="GET", payload=None):
            nonlocal room_reads
            calls.append((url, method, payload))
            if url.endswith("/live-agents") and method == "POST":
                return {"agent": {"agent_id": "agent-a", "status": "online"}}
            if url.endswith("/heartbeat"):
                return {"agent": {"agent_id": "agent-a", "status": (payload or {}).get("status", "online")}}
            if url.endswith("/room"):
                room_reads += 1
                if room_reads == 1:
                    return {"lobby_events": []}
                raise ConnectionError("room read failed for http://room.local/private/live-agents.json token=secret-token")
            return {}

        runner = LiveAgentRunner(
            config(max_ticks=2, cooldown=0.0),
            request_json=request_json,
            command_runner=lambda command, prompt, *, timeout_seconds: "unused",
            sleep_fn=clock.sleep,
            now_fn=clock,
        )

        self.assertEqual(runner.run(), 0)

        error_payloads = [
            payload for url, method, payload in calls if url.endswith("/heartbeat") and payload["status"] == "error"
        ]
        self.assertEqual(error_payloads[-1]["last_error"], "Resident room read error details redacted.")
        self.assertEqual(runner.last_error, "Resident room read error details redacted.")
        self.assertNotIn("secret-token", error_payloads[-1]["last_error"])
        self.assertNotIn("live-agents.json", error_payloads[-1]["last_error"])

    def test_runner_survives_transient_room_failure_when_error_heartbeat_fails(self):
        clock = FakeClock()
        calls = []
        room_reads = 0

        def request_json(url, *, method="GET", payload=None):
            nonlocal room_reads
            calls.append((url, method, payload))
            if url.endswith("/live-agents") and method == "POST":
                return {"agent": {"agent_id": "agent-a", "status": "online"}}
            if url.endswith("/heartbeat"):
                if (payload or {}).get("status") == "error":
                    raise ConnectionError("heartbeat write failed")
                return {"agent": {"agent_id": "agent-a", "status": (payload or {}).get("status", "online")}}
            if url.endswith("/room"):
                room_reads += 1
                if room_reads == 1:
                    return {"lobby_events": []}
                if room_reads == 2:
                    raise ConnectionError("transient room read failed")
                return {"lobby_events": [{"id": "evt1", "name": "나", "message": "아직 살아있어?"}]}
            if url.endswith("/lobby"):
                return {"event": {"id": "reply-id"}}
            return {}

        runner = LiveAgentRunner(
            config(max_ticks=3, cooldown=0.0),
            request_json=request_json,
            command_runner=lambda command, prompt, *, timeout_seconds: "still alive",
            sleep_fn=clock.sleep,
            now_fn=clock,
        )

        self.assertEqual(runner.run(), 1)

        error_heartbeat_attempts = [
            payload for url, method, payload in calls if url.endswith("/heartbeat") and payload["status"] == "error"
        ]
        lobby_payloads = [payload for url, method, payload in calls if url.endswith("/lobby")]
        self.assertEqual(len(error_heartbeat_attempts), 1)
        self.assertEqual(lobby_payloads[0]["source_event_id"], "evt1")

    def test_runner_replies_immediately_after_transient_room_failure_recovery(self):
        clock = FakeClock()
        calls = []
        room_reads = 0

        def request_json(url, *, method="GET", payload=None):
            nonlocal room_reads
            calls.append((url, method, payload))
            if url.endswith("/live-agents") and method == "POST":
                return {"agent": {"agent_id": "agent-a", "status": "online"}}
            if url.endswith("/heartbeat"):
                return {"agent": {"agent_id": "agent-a", "status": (payload or {}).get("status", "online")}}
            if url.endswith("/room"):
                room_reads += 1
                if room_reads == 1:
                    return {"lobby_events": []}
                if room_reads == 2:
                    raise ConnectionError("transient room read failed")
                return {"lobby_events": [{"id": "evt1", "name": "나", "message": "바로 대답해"}]}
            if url.endswith("/lobby"):
                return {"event": {"id": "reply-id"}}
            return {}

        runner = LiveAgentRunner(
            config(max_ticks=3, poll_interval=1.0, cooldown=30.0),
            request_json=request_json,
            command_runner=lambda command, prompt, *, timeout_seconds: "no room backoff",
            sleep_fn=clock.sleep,
            now_fn=clock,
        )

        self.assertEqual(runner.run(), 1)

        lobby_payloads = [payload for url, method, payload in calls if url.endswith("/lobby")]
        self.assertEqual(lobby_payloads[0]["source_event_id"], "evt1")

    def test_runner_clears_transient_room_error_after_room_snapshot_recovers(self):
        clock = FakeClock()
        calls = []
        room_reads = 0

        def request_json(url, *, method="GET", payload=None):
            nonlocal room_reads
            calls.append((url, method, payload))
            if url.endswith("/live-agents") and method == "POST":
                return {"agent": {"agent_id": "agent-a", "status": "online"}}
            if url.endswith("/heartbeat"):
                return {"agent": {"agent_id": "agent-a", "status": (payload or {}).get("status", "online")}}
            if url.endswith("/room"):
                room_reads += 1
                if room_reads == 1:
                    return {"lobby_events": []}
                if room_reads == 2:
                    raise ConnectionError("transient room read failed")
                return {"lobby_events": []}
            return {}

        runner = LiveAgentRunner(
            config(max_ticks=3, heartbeat_interval=60.0),
            request_json=request_json,
            command_runner=lambda command, prompt, *, timeout_seconds: "unused",
            sleep_fn=clock.sleep,
            now_fn=clock,
        )

        self.assertEqual(runner.run(), 0)

        heartbeat_payloads = [payload for url, method, payload in calls if url.endswith("/heartbeat")]
        self.assertIn({"status": "error", "last_error": "transient room read failed"}, heartbeat_payloads)
        self.assertIn({"status": "online", "last_error": ""}, heartbeat_payloads)

    def test_runner_does_not_clear_provider_error_after_room_clear_heartbeat_failed(self):
        clock = FakeClock()
        calls = []
        room_reads = 0
        clear_attempts = 0

        def request_json(url, *, method="GET", payload=None):
            nonlocal room_reads, clear_attempts
            calls.append((url, method, payload))
            if url.endswith("/live-agents") and method == "POST":
                return {"agent": {"agent_id": "agent-a", "status": "online"}}
            if url.endswith("/heartbeat"):
                if (payload or {}).get("status") == "online" and (payload or {}).get("last_error") == "":
                    clear_attempts += 1
                    if clear_attempts == 1:
                        raise ConnectionError("clear heartbeat failed")
                return {"agent": {"agent_id": "agent-a", "status": (payload or {}).get("status", "online")}}
            if url.endswith("/room"):
                room_reads += 1
                if room_reads == 1:
                    return {"lobby_events": []}
                if room_reads == 2:
                    raise ConnectionError("transient room read failed")
                if room_reads == 3:
                    return {"lobby_events": [{"id": "evt1", "name": "나", "message": "provider 실패"}]}
                return {"lobby_events": [{"id": "evt1", "name": "나", "message": "provider 실패"}]}
            return {}

        def fail_command(command, prompt, *, timeout_seconds):
            del command, prompt, timeout_seconds
            raise RuntimeError("provider boom")

        runner = LiveAgentRunner(
            config(max_ticks=4, cooldown=0.0),
            request_json=request_json,
            command_runner=fail_command,
            sleep_fn=clock.sleep,
            now_fn=clock,
        )

        self.assertEqual(runner.run(), 0)

        clear_payloads = [
            payload
            for url, method, payload in calls
            if url.endswith("/heartbeat") and payload["status"] == "online" and payload.get("last_error") == ""
        ]
        self.assertEqual(len(clear_payloads), 1)
        self.assertEqual(runner.last_error, "provider boom")

    def test_runner_does_not_mask_lobby_post_failure_when_final_offline_heartbeat_fails(self):
        clock = FakeClock()
        calls = []
        room = {"lobby_events": [{"id": "evt1", "name": "나", "message": "답변 게시 실패"}]}

        def request_json(url, *, method="GET", payload=None):
            calls.append((url, method, payload))
            if url.endswith("/live-agents") and method == "POST":
                return {"agent": {"agent_id": "agent-a", "status": "online"}}
            if url.endswith("/heartbeat"):
                if (payload or {}).get("status") == "offline":
                    raise ConnectionError("room server unavailable during shutdown")
                return {"agent": {"agent_id": "agent-a", "status": (payload or {}).get("status", "online")}}
            if url.endswith("/room"):
                return room
            if url.endswith("/lobby"):
                raise RuntimeError("lobby post failed")
            return {}

        runner = LiveAgentRunner(
            config(),
            request_json=request_json,
            command_runner=lambda command, prompt, *, timeout_seconds: "reply that cannot post",
            sleep_fn=clock.sleep,
            now_fn=clock,
        )

        with self.assertRaisesRegex(RuntimeError, "lobby post failed"):
            runner.run()

        heartbeat_statuses = [payload["status"] for url, method, payload in calls if url.endswith("/heartbeat")]
        error_heartbeats = [
            payload for url, method, payload in calls if url.endswith("/heartbeat") and payload["status"] == "error"
        ]
        self.assertTrue(error_heartbeats)
        self.assertEqual(error_heartbeats[-1]["last_error"], "lobby post failed")
        self.assertEqual(error_heartbeats[-1]["last_observed_event_id"], "evt1")
        self.assertEqual(heartbeat_statuses[-1], "offline")

    def test_runner_redacts_sensitive_lobby_post_failure_heartbeat(self):
        clock = FakeClock()
        calls = []
        room = {"lobby_events": [{"id": "evt1", "name": "나", "message": "게시 실패를 남겨줘"}]}

        def request_json(url, *, method="GET", payload=None):
            calls.append((url, method, payload))
            if url.endswith("/live-agents") and method == "POST":
                return {"agent": {"agent_id": "agent-a", "status": "online"}}
            if url.endswith("/heartbeat"):
                return {"agent": {"agent_id": "agent-a", "status": (payload or {}).get("status", "online")}}
            if url.endswith("/room"):
                return room
            if url.endswith("/lobby"):
                raise RuntimeError("lobby post failed for /private/live-agents.json token=secret-token")
            return {}

        runner = LiveAgentRunner(
            config(),
            request_json=request_json,
            command_runner=lambda command, prompt, *, timeout_seconds: "reply that cannot post",
            sleep_fn=clock.sleep,
            now_fn=clock,
        )

        with self.assertRaisesRegex(RuntimeError, "lobby post failed"):
            runner.run()

        error_heartbeats = [
            payload for url, method, payload in calls if url.endswith("/heartbeat") and payload["status"] == "error"
        ]
        self.assertEqual(error_heartbeats[-1]["last_error"], "Resident reply post error details redacted.")
        self.assertEqual(runner.last_error, "Resident reply post error details redacted.")
        self.assertNotIn("secret-token", error_heartbeats[-1]["last_error"])
        self.assertNotIn("live-agents.json", error_heartbeats[-1]["last_error"])

    def test_runner_records_official_turn_post_failure_before_raising(self):
        clock = FakeClock()
        calls = []
        room = {
            "agent": {"agent_id": "agent-a", "engagement_mode": "moderator_called", "meeting_id": "m1"},
            "lobby_events": [],
            "live_events": [
                {
                    "id": "turn-request-1",
                    "kind": "live_agent_turn_request",
                    "meeting_id": "m1",
                    "target_agent_id": "agent-a",
                    "content": "공식 답변 게시 실패를 남겨줘",
                }
            ],
        }

        def request_json(url, *, method="GET", payload=None):
            calls.append((url, method, payload))
            if url.endswith("/live-agents") and method == "POST":
                return {"agent": {"agent_id": "agent-a", "status": "online"}}
            if url.endswith("/heartbeat"):
                return {"agent": {"agent_id": "agent-a", "status": (payload or {}).get("status", "online")}}
            if url.endswith("/room"):
                return room
            if url.endswith("/official-turn"):
                raise RuntimeError("official turn post failed")
            return {}

        runner = LiveAgentRunner(
            config(engagement_mode="moderator_called", meeting_id="m1"),
            request_json=request_json,
            command_runner=lambda command, prompt, *, timeout_seconds: "공식 답변",
            sleep_fn=clock.sleep,
            now_fn=clock,
        )

        with self.assertRaisesRegex(RuntimeError, "official turn post failed"):
            runner.run()

        error_heartbeats = [
            payload for url, method, payload in calls if url.endswith("/heartbeat") and payload["status"] == "error"
        ]
        self.assertTrue(error_heartbeats)
        self.assertEqual(error_heartbeats[-1]["last_error"], "official turn post failed")
        self.assertEqual(error_heartbeats[-1]["last_observed_live_event_id"], "turn-request-1")

    def test_runner_redacts_sensitive_official_turn_post_failure_heartbeat(self):
        clock = FakeClock()
        calls = []
        room = {
            "agent": {"agent_id": "agent-a", "engagement_mode": "moderator_called", "meeting_id": "m1"},
            "lobby_events": [],
            "live_events": [
                {
                    "id": "turn-request-1",
                    "kind": "live_agent_turn_request",
                    "meeting_id": "m1",
                    "target_agent_id": "agent-a",
                    "content": "민감한 공식 게시 실패를 남겨줘",
                }
            ],
        }

        def request_json(url, *, method="GET", payload=None):
            calls.append((url, method, payload))
            if url.endswith("/live-agents") and method == "POST":
                return {"agent": {"agent_id": "agent-a", "status": "online"}}
            if url.endswith("/heartbeat"):
                return {"agent": {"agent_id": "agent-a", "status": (payload or {}).get("status", "online")}}
            if url.endswith("/room"):
                return room
            if url.endswith("/official-turn"):
                raise RuntimeError("official turn post failed for /private/live-agents.json token=secret-token")
            return {}

        runner = LiveAgentRunner(
            config(engagement_mode="moderator_called", meeting_id="m1"),
            request_json=request_json,
            command_runner=lambda command, prompt, *, timeout_seconds: "공식 답변",
            sleep_fn=clock.sleep,
            now_fn=clock,
        )

        with self.assertRaisesRegex(RuntimeError, "official turn post failed"):
            runner.run()

        error_heartbeats = [
            payload for url, method, payload in calls if url.endswith("/heartbeat") and payload["status"] == "error"
        ]
        self.assertEqual(error_heartbeats[-1]["last_error"], "Resident reply post error details redacted.")
        self.assertEqual(error_heartbeats[-1]["last_observed_live_event_id"], "turn-request-1")
        self.assertEqual(runner.last_error, "Resident reply post error details redacted.")
        self.assertNotIn("secret-token", error_heartbeats[-1]["last_error"])
        self.assertNotIn("live-agents.json", error_heartbeats[-1]["last_error"])

    def test_runner_does_not_mask_lobby_post_failure_when_error_heartbeat_fails(self):
        clock = FakeClock()
        calls = []
        room = {"lobby_events": [{"id": "evt1", "name": "나", "message": "게시 실패 원인을 유지해"}]}

        def request_json(url, *, method="GET", payload=None):
            calls.append((url, method, payload))
            if url.endswith("/live-agents") and method == "POST":
                return {"agent": {"agent_id": "agent-a", "status": "online"}}
            if url.endswith("/heartbeat"):
                if (payload or {}).get("status") == "error":
                    raise ConnectionError("error heartbeat failed")
                return {"agent": {"agent_id": "agent-a", "status": (payload or {}).get("status", "online")}}
            if url.endswith("/room"):
                return room
            if url.endswith("/lobby"):
                raise RuntimeError("lobby post failed")
            return {}

        runner = LiveAgentRunner(
            config(),
            request_json=request_json,
            command_runner=lambda command, prompt, *, timeout_seconds: "reply that cannot post",
            sleep_fn=clock.sleep,
            now_fn=clock,
        )

        with self.assertRaises(Exception) as raised:
            runner.run()
        self.assertIsInstance(raised.exception, RuntimeError)
        self.assertIn("lobby post failed", str(raised.exception))

        attempted_error_heartbeats = [
            payload for url, method, payload in calls if url.endswith("/heartbeat") and payload["status"] == "error"
        ]
        self.assertEqual(attempted_error_heartbeats[0]["last_observed_event_id"], "evt1")

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

    def test_runner_keeps_working_heartbeat_fresh_during_slow_reply(self):
        clock = FakeClock()
        room = {"lobby_events": [{"id": "evt1", "name": "나", "message": "천천히 답해"}]}
        client = FakeRoomClient([room])

        def slow_command(command, prompt, *, timeout_seconds):
            del command, prompt, timeout_seconds
            time.sleep(0.05)
            return "slow reply"

        runner = LiveAgentRunner(
            config(heartbeat_interval=0.005),
            request_json=client,
            command_runner=slow_command,
            sleep_fn=clock.sleep,
            now_fn=clock,
        )

        self.assertEqual(runner.run(), 1)

        working_heartbeats = [
            payload
            for url, method, payload in client.calls
            if url.endswith("/heartbeat") and payload["status"] == "working"
        ]
        self.assertGreaterEqual(len(working_heartbeats), 2)
        self.assertEqual({payload["last_observed_event_id"] for payload in working_heartbeats}, {"evt1"})

    def test_runner_waits_for_in_flight_working_heartbeat_before_final_status(self):
        clock = FakeClock()
        room = {"lobby_events": [{"id": "evt1", "name": "나", "message": "순서 지켜"}]}
        call_order = []
        call_lock = threading.Lock()
        background_heartbeat_started = threading.Event()
        release_background_heartbeat = threading.Event()
        working_heartbeats = 0

        def request_json(url, *, method="GET", payload=None):
            nonlocal working_heartbeats
            if url.endswith("/room"):
                return room
            if url.endswith("/live-agents") and method == "POST":
                with call_lock:
                    call_order.append("register")
                return {"agent": {"agent_id": "agent-a", "status": "online"}}
            if url.endswith("/heartbeat"):
                status = str((payload or {}).get("status") or "")
                if status == "working":
                    working_heartbeats += 1
                    if working_heartbeats == 2:
                        background_heartbeat_started.set()
                        release_background_heartbeat.wait(timeout=2)
                with call_lock:
                    call_order.append(f"heartbeat:{status}")
                return {"agent": {"agent_id": "agent-a", "status": status}}
            if url.endswith("/lobby"):
                with call_lock:
                    call_order.append("lobby")
                return {"event": {"id": "reply-id"}}
            return {}

        def slow_command(command, prompt, *, timeout_seconds):
            del command, prompt, timeout_seconds
            if not background_heartbeat_started.wait(timeout=1):
                raise AssertionError("background working heartbeat did not start")
            return "ordered reply"

        runner = LiveAgentRunner(
            config(heartbeat_interval=0.01),
            request_json=request_json,
            command_runner=slow_command,
            sleep_fn=clock.sleep,
            now_fn=clock,
        )
        result = {}
        runner_thread = threading.Thread(target=lambda: result.setdefault("replies", runner.run()))

        runner_thread.start()
        try:
            self.assertTrue(background_heartbeat_started.wait(timeout=1))
            time.sleep(1.1)
        finally:
            release_background_heartbeat.set()
            runner_thread.join(timeout=2)

        self.assertFalse(runner_thread.is_alive())
        self.assertEqual(result.get("replies"), 1)
        with call_lock:
            ordered = list(call_order)
        last_working_index = max(index for index, item in enumerate(ordered) if item == "heartbeat:working")
        self.assertLess(last_working_index, ordered.index("lobby"))
        self.assertLess(ordered.index("lobby"), ordered.index("heartbeat:offline"))

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

    def test_watch_mode_survives_transient_cursor_heartbeat_failure(self):
        clock = FakeClock()
        calls = []
        command_calls = []
        room_reads = 0

        def request_json(url, *, method="GET", payload=None):
            nonlocal room_reads
            calls.append((url, method, payload))
            if url.endswith("/live-agents") and method == "POST":
                return {"agent": {"agent_id": "agent-a", "status": "online"}}
            if url.endswith("/room"):
                room_reads += 1
                return {"lobby_events": [{"id": "evt1", "side": "mine", "name": "나", "message": "보고만 있어"}]}
            if url.endswith("/heartbeat"):
                if (payload or {}).get("last_observed_event_id") == "evt1" and (payload or {}).get("status") == "online":
                    raise ConnectionError("transient cursor heartbeat failure")
                return {"agent": {"agent_id": "agent-a", "status": (payload or {}).get("status", "online")}}
            return {}

        runner = LiveAgentRunner(
            config(engagement_mode="watch", max_ticks=2),
            request_json=request_json,
            command_runner=lambda command, prompt, *, timeout_seconds: command_calls.append(prompt) or "reply",
            sleep_fn=clock.sleep,
            now_fn=clock,
        )

        self.assertEqual(runner.run(), 0)

        self.assertEqual(command_calls, [])
        self.assertEqual(room_reads, 2)
        self.assertEqual([call for call in calls if call[0].endswith("/lobby")], [])
        offline_heartbeats = [
            payload
            for url, method, payload in calls
            if url.endswith("/heartbeat") and (payload or {}).get("status") == "offline"
        ]
        self.assertEqual(offline_heartbeats[-1]["last_observed_event_id"], "evt1")

    def test_manual_mode_observes_without_replying_and_advances_cursor(self):
        clock = FakeClock()
        room = {"lobby_events": [{"id": "evt1", "side": "mine", "name": "나", "message": "수동 모드야"}]}
        client = FakeRoomClient([room])
        command_calls = []
        runner = LiveAgentRunner(
            config(engagement_mode="manual"),
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

    def test_moderator_called_mode_answers_targeted_official_turn_request_not_lobby(self):
        clock = FakeClock()
        room = {
            "agent": {"agent_id": "agent-a", "engagement_mode": "moderator_called", "meeting_id": "m1"},
            "lobby_events": [{"id": "lobby1", "side": "mine", "name": "나", "message": "로비에서는 부르지 않음"}],
            "live_events": [
                {"id": "status1", "kind": "status", "content": "회의 시작"},
                {
                    "id": "turn-request-1",
                    "kind": "live_agent_turn_request",
                    "meeting_id": "m1",
                    "target_agent_id": "agent-a",
                    "role_id": "architect",
                    "display_name": "Agent A",
                    "content": "공식 의견을 말해줘.",
                    "turn_id": "round_1:0:architect",
                    "turn_index": 0,
                    "engagement_mode": "moderator_called",
                },
            ],
        }
        client = FakeRoomClient([room])
        prompts = []

        def command_runner(command, prompt, *, timeout_seconds):
            prompts.append(prompt)
            return "공식 답변"

        runner = LiveAgentRunner(
            config(engagement_mode="moderator_called", meeting_id="m1"),
            request_json=client,
            command_runner=command_runner,
            sleep_fn=clock.sleep,
            now_fn=clock,
        )

        self.assertEqual(runner.run(), 1)

        self.assertEqual(len(prompts), 1)
        self.assertIn("official meeting turn", prompts[0])
        self.assertIn("공식 의견을 말해줘.", prompts[0])
        official_payloads = [payload for url, method, payload in client.calls if url.endswith("/official-turn")]
        self.assertEqual(len(official_payloads), 1)
        self.assertEqual(official_payloads[0]["meeting_id"], "m1")
        self.assertEqual(official_payloads[0]["source_event_id"], "turn-request-1")
        self.assertEqual(official_payloads[0]["content"], "공식 답변")
        self.assertEqual(official_payloads[0]["role_id"], "architect")
        self.assertEqual(official_payloads[0]["turn_id"], "round_1:0:architect")
        self.assertEqual(official_payloads[0]["turn_index"], 0)
        self.assertEqual([call for call in client.calls if call[0].endswith("/lobby")], [])
        self.assertEqual(runner.last_observed_event_id, "lobby1")
        self.assertEqual(runner.last_observed_live_event_id, "turn-request-1")

    def test_moderator_called_cursor_keeps_later_same_agent_request_visible_after_late_reply(self):
        clock = FakeClock()
        first_room = {
            "agent": {"agent_id": "agent-a", "engagement_mode": "moderator_called", "meeting_id": "m1"},
            "lobby_events": [],
            "live_events": [
                {
                    "id": "turn-request-1",
                    "kind": "live_agent_turn_request",
                    "meeting_id": "m1",
                    "target_agent_id": "agent-a",
                    "content": "첫 요청",
                }
            ],
        }
        second_room = {
            "agent": {"agent_id": "agent-a", "engagement_mode": "moderator_called", "meeting_id": "m1"},
            "lobby_events": [],
            "live_events": [
                {
                    "id": "turn-request-1",
                    "kind": "live_agent_turn_request",
                    "meeting_id": "m1",
                    "target_agent_id": "agent-a",
                    "content": "첫 요청",
                },
                {
                    "id": "turn-request-2",
                    "kind": "live_agent_turn_request",
                    "meeting_id": "m1",
                    "target_agent_id": "agent-a",
                    "content": "두 번째 요청",
                },
                {
                    "id": "late-reply-1",
                    "kind": "message",
                    "channel": "official",
                    "official_record": True,
                    "actor_id": "agent-a",
                    "source_event_id": "turn-request-1",
                    "content": "늦은 첫 답변",
                },
            ],
        }
        client = FakeRoomClient([first_room, second_room])
        replies = iter(["첫 답변", "둘째 답변"])
        runner = LiveAgentRunner(
            config(engagement_mode="moderator_called", meeting_id="m1", max_ticks=2),
            request_json=client,
            command_runner=lambda command, prompt, *, timeout_seconds: next(replies),
            sleep_fn=clock.sleep,
            now_fn=clock,
        )

        self.assertEqual(runner.run(), 2)

        official_payloads = [payload for url, method, payload in client.calls if url.endswith("/official-turn")]
        self.assertEqual([payload["source_event_id"] for payload in official_payloads], ["turn-request-1", "turn-request-2"])

    def test_moderator_called_recovers_when_live_cursor_fell_out_of_bounded_room_tail(self):
        clock = FakeClock()
        room = {
            "meeting_id": "m1",
            "agent": {
                "agent_id": "agent-a",
                "engagement_mode": "moderator_called",
                "meeting_id": "m1",
                "last_observed_live_event_id": "evicted-live-cursor",
            },
            "lobby_events": [],
            "live_events": [
                {
                    "id": "turn-request-1",
                    "kind": "live_agent_turn_request",
                    "meeting_id": "m1",
                    "target_agent_id": "agent-a",
                    "content": "현재 tail에 남은 공식 요청에 답해줘.",
                }
            ],
        }
        client = FakeRoomClient([room])
        runner = LiveAgentRunner(
            config(engagement_mode="moderator_called", meeting_id="m1"),
            request_json=client,
            command_runner=lambda command, prompt, *, timeout_seconds: "official reply after evicted cursor",
            sleep_fn=clock.sleep,
            now_fn=clock,
        )

        self.assertEqual(runner.run(), 1)

        official_payloads = [payload for url, method, payload in client.calls if url.endswith("/official-turn")]
        self.assertEqual(official_payloads[0]["source_event_id"], "turn-request-1")

    def test_official_turn_candidate_uses_bounded_tail_when_cursor_is_absent_but_keeps_answered_guard(self):
        answered_events = [
            {
                "id": "turn-request-1",
                "kind": "live_agent_turn_request",
                "target_agent_id": "agent-a",
                "content": "이미 답한 요청",
            },
            {
                "id": "reply-1",
                "kind": "message",
                "channel": "official",
                "official_record": True,
                "actor_id": "agent-a",
                "source_event_id": "turn-request-1",
                "content": "이미 있는 답변",
            },
        ]
        pending_events = [
            {
                "id": "turn-request-2",
                "kind": "live_agent_turn_request",
                "target_agent_id": "agent-a",
                "content": "tail 안의 새 공식 요청",
            }
        ]

        self.assertIsNone(official_turn_request_candidate(answered_events, "agent-a", "evicted-live-cursor"))
        self.assertEqual(
            official_turn_request_candidate(pending_events, "agent-a", "evicted-live-cursor"),
            pending_events[0],
        )

    def test_official_turn_candidate_skips_request_without_event_id(self):
        events = [
            {
                "kind": "live_agent_turn_request",
                "target_agent_id": "agent-a",
                "content": "malformed request without id",
            },
            {
                "id": "turn-request-2",
                "kind": "live_agent_turn_request",
                "target_agent_id": "agent-a",
                "content": "valid request",
            },
        ]

        self.assertEqual(official_turn_request_candidate(events, "agent-a", ""), events[1])

    def test_official_turn_candidate_treats_review_checkpoint_reply_as_answered(self):
        events = [
            {
                "id": "review-request-1",
                "kind": "live_agent_turn_request",
                "target_agent_id": "agent-a",
                "content": "리뷰해줘",
                "channel": "review",
                "official_record": False,
                "review_checkpoint_id": "checkpoint-1",
            },
            {
                "id": "review-reply-1",
                "kind": "message",
                "actor_id": "agent-a",
                "source_event_id": "review-request-1",
                "content": "검토 완료",
                "channel": "review",
                "official_record": False,
                "review_checkpoint_id": "checkpoint-1",
            },
        ]

        self.assertIsNone(official_turn_request_candidate(events, "agent-a", ""))

    def test_official_turn_candidate_skips_cancelled_request(self):
        events = [
            {
                "id": "turn-request-1",
                "kind": "live_agent_turn_request",
                "target_agent_id": "agent-a",
                "content": "닫힌 요청",
            },
            {
                "id": "turn-cancel-1",
                "kind": "live_agent_turn_cancelled",
                "target_agent_id": "agent-a",
                "source_event_id": "turn-request-1",
                "content": "official turn request cancelled",
                "channel": "system",
                "official_record": False,
            },
            {
                "id": "turn-request-2",
                "kind": "live_agent_turn_request",
                "target_agent_id": "agent-a",
                "content": "아직 열려 있는 요청",
            },
        ]

        self.assertEqual(official_turn_request_candidate(events, "agent-a", ""), events[2])

    def test_moderator_called_skips_visible_already_answered_request_without_model_call(self):
        clock = FakeClock()
        room = {
            "agent": {"agent_id": "agent-a", "engagement_mode": "moderator_called", "meeting_id": "m1"},
            "lobby_events": [],
            "live_events": [
                {
                    "id": "turn-request-1",
                    "kind": "live_agent_turn_request",
                    "meeting_id": "m1",
                    "target_agent_id": "agent-a",
                    "content": "이미 답한 요청",
                },
                {
                    "id": "reply-1",
                    "kind": "message",
                    "channel": "official",
                    "official_record": True,
                    "actor_id": "agent-a",
                    "source_event_id": "turn-request-1",
                    "content": "이미 있는 답변",
                },
            ],
        }
        client = FakeRoomClient([room])
        command_calls = []
        runner = LiveAgentRunner(
            config(engagement_mode="moderator_called", meeting_id="m1"),
            request_json=client,
            command_runner=lambda command, prompt, *, timeout_seconds: command_calls.append(prompt) or "duplicate reply",
            sleep_fn=clock.sleep,
            now_fn=clock,
        )

        self.assertEqual(runner.run(), 0)

        self.assertEqual(command_calls, [])
        self.assertEqual([call for call in client.calls if call[0].endswith("/official-turn")], [])
        self.assertEqual(runner.last_observed_live_event_id, "reply-1")

    def test_moderator_called_does_not_treat_informal_same_source_message_as_official_reply(self):
        clock = FakeClock()
        room = {
            "agent": {"agent_id": "agent-a", "engagement_mode": "moderator_called", "meeting_id": "m1"},
            "lobby_events": [],
            "live_events": [
                {
                    "id": "turn-request-1",
                    "kind": "live_agent_turn_request",
                    "meeting_id": "m1",
                    "target_agent_id": "agent-a",
                    "content": "공식 답변이 필요해",
                },
                {
                    "id": "informal-1",
                    "kind": "message",
                    "channel": "system",
                    "official_record": False,
                    "actor_id": "agent-a",
                    "source_event_id": "turn-request-1",
                    "content": "비공식 상태 메모",
                },
            ],
        }
        client = FakeRoomClient([room])
        command_calls = []
        runner = LiveAgentRunner(
            config(engagement_mode="moderator_called", meeting_id="m1"),
            request_json=client,
            command_runner=lambda command, prompt, *, timeout_seconds: command_calls.append(prompt) or "공식 답변",
            sleep_fn=clock.sleep,
            now_fn=clock,
        )

        self.assertEqual(runner.run(), 1)

        self.assertEqual(len(command_calls), 1)
        official_payloads = [payload for url, method, payload in client.calls if url.endswith("/official-turn")]
        self.assertEqual(official_payloads[0]["source_event_id"], "turn-request-1")

    def test_moderator_called_treats_legacy_same_source_message_as_answered_official_request(self):
        clock = FakeClock()
        room = {
            "agent": {"agent_id": "agent-a", "engagement_mode": "moderator_called", "meeting_id": "m1"},
            "lobby_events": [],
            "live_events": [
                {
                    "id": "turn-request-1",
                    "kind": "live_agent_turn_request",
                    "meeting_id": "m1",
                    "target_agent_id": "agent-a",
                    "content": "이미 답한 요청",
                },
                {
                    "id": "legacy-reply-1",
                    "kind": "message",
                    "actor_id": "agent-a",
                    "source_event_id": "turn-request-1",
                    "content": "channel metadata가 생기기 전 공식 답변",
                },
            ],
        }
        client = FakeRoomClient([room])
        command_calls = []
        runner = LiveAgentRunner(
            config(engagement_mode="moderator_called", meeting_id="m1"),
            request_json=client,
            command_runner=lambda command, prompt, *, timeout_seconds: command_calls.append(prompt) or "duplicate reply",
            sleep_fn=clock.sleep,
            now_fn=clock,
        )

        self.assertEqual(runner.run(), 0)

        self.assertEqual(command_calls, [])
        self.assertEqual([call for call in client.calls if call[0].endswith("/official-turn")], [])

    def test_moderator_called_mode_ignores_untargeted_official_turn_request(self):
        clock = FakeClock()
        room = {
            "agent": {"agent_id": "agent-a", "engagement_mode": "moderator_called", "meeting_id": "m1"},
            "lobby_events": [{"id": "lobby1", "side": "mine", "name": "나", "message": "로비 잡담"}],
            "live_events": [
                {
                    "id": "turn-request-1",
                    "kind": "live_agent_turn_request",
                    "meeting_id": "m1",
                    "target_agent_id": "agent-b",
                    "content": "다른 에이전트 차례",
                }
            ],
        }
        client = FakeRoomClient([room])
        command_calls = []
        runner = LiveAgentRunner(
            config(engagement_mode="moderator_called", meeting_id="m1"),
            request_json=client,
            command_runner=lambda command, prompt, *, timeout_seconds: command_calls.append(prompt) or "reply",
            sleep_fn=clock.sleep,
            now_fn=clock,
        )

        self.assertEqual(runner.run(), 0)

        self.assertEqual(command_calls, [])
        self.assertEqual([call for call in client.calls if call[0].endswith("/official-turn")], [])
        self.assertEqual([call for call in client.calls if call[0].endswith("/lobby")], [])
        observed = [
            payload["last_observed_live_event_id"]
            for url, method, payload in client.calls
            if url.endswith("/heartbeat") and payload.get("last_observed_live_event_id")
        ]
        self.assertIn("turn-request-1", observed)

    def test_moderator_called_mode_persists_lobby_cursor_when_no_live_turn_is_available(self):
        clock = FakeClock()
        room = {
            "agent": {"agent_id": "agent-a", "engagement_mode": "moderator_called", "meeting_id": "m1"},
            "lobby_events": [{"id": "lobby1", "side": "mine", "name": "나", "message": "나중에 반복하지 마"}],
            "live_events": [],
        }
        client = FakeRoomClient([room])
        runner = LiveAgentRunner(
            config(engagement_mode="moderator_called", meeting_id="m1", heartbeat_interval=999),
            request_json=client,
            command_runner=lambda command, prompt, *, timeout_seconds: "reply",
            sleep_fn=clock.sleep,
            now_fn=clock,
        )

        self.assertEqual(runner.run(), 0)

        heartbeats = [payload for url, method, payload in client.calls if url.endswith("/heartbeat")]
        self.assertEqual(heartbeats[-1]["status"], "offline")
        self.assertEqual(heartbeats[-1]["last_observed_event_id"], "lobby1")

    def test_moderator_called_survives_transient_live_cursor_heartbeat_failure(self):
        clock = FakeClock()
        calls = []
        command_calls = []

        def request_json(url, *, method="GET", payload=None):
            calls.append((url, method, payload))
            if url.endswith("/live-agents") and method == "POST":
                return {"agent": {"agent_id": "agent-a", "status": "online", "engagement_mode": "moderator_called"}}
            if url.endswith("/room"):
                return {
                    "agent": {"agent_id": "agent-a", "engagement_mode": "moderator_called", "meeting_id": "m1"},
                    "lobby_events": [],
                    "live_events": [{"id": "live-info", "kind": "meeting_status", "content": "still running"}],
                }
            if url.endswith("/heartbeat"):
                if (payload or {}).get("last_observed_live_event_id") == "live-info" and (payload or {}).get("status") == "online":
                    raise ConnectionError("transient live cursor heartbeat failure")
                return {"agent": {"agent_id": "agent-a", "status": (payload or {}).get("status", "online")}}
            return {}

        runner = LiveAgentRunner(
            config(engagement_mode="moderator_called", meeting_id="m1"),
            request_json=request_json,
            command_runner=lambda command, prompt, *, timeout_seconds: command_calls.append(prompt) or "reply",
            sleep_fn=clock.sleep,
            now_fn=clock,
        )

        self.assertEqual(runner.run(), 0)

        self.assertEqual(command_calls, [])
        self.assertEqual([call for call in calls if call[0].endswith("/official-turn")], [])
        offline_heartbeats = [
            payload
            for url, method, payload in calls
            if url.endswith("/heartbeat") and (payload or {}).get("status") == "offline"
        ]
        self.assertEqual(offline_heartbeats[-1]["last_observed_live_event_id"], "live-info")

    def test_official_turn_cursor_does_not_poison_lobby_cursor_when_mode_changes(self):
        clock = FakeClock()
        first_room = {
            "agent": {"agent_id": "agent-a", "engagement_mode": "moderator_called", "meeting_id": "m1"},
            "lobby_events": [{"id": "lobby1", "side": "mine", "name": "나", "message": "이건 공식 턴 전 로비"}],
            "live_events": [
                {
                    "id": "turn-request-1",
                    "kind": "live_agent_turn_request",
                    "meeting_id": "m1",
                    "target_agent_id": "agent-a",
                    "content": "공식 의견",
                }
            ],
        }
        second_room = {
            "agent": {"agent_id": "agent-a", "engagement_mode": "always", "meeting_id": "m1"},
            "lobby_events": [
                {"id": "lobby1", "side": "mine", "name": "나", "message": "이건 공식 턴 전 로비"},
                {"id": "lobby2", "side": "mine", "name": "나", "message": "이제 로비로 답해"},
            ],
            "live_events": [
                {"id": "turn-request-1", "kind": "live_agent_turn_request", "target_agent_id": "agent-a", "content": "공식 의견"},
                {"id": "official-reply-id", "kind": "message", "source_event_id": "turn-request-1", "actor_id": "agent-a"},
            ],
        }
        client = FakeRoomClient([first_room, second_room])
        replies = iter(["공식 답변", "로비 답변"])
        runner = LiveAgentRunner(
            config(engagement_mode="moderator_called", meeting_id="m1", max_ticks=2),
            request_json=client,
            command_runner=lambda command, prompt, *, timeout_seconds: next(replies),
            sleep_fn=clock.sleep,
            now_fn=clock,
        )

        self.assertEqual(runner.run(), 2)

        official_payloads = [payload for url, method, payload in client.calls if url.endswith("/official-turn")]
        lobby_payloads = [payload for url, method, payload in client.calls if url.endswith("/lobby")]
        self.assertEqual(official_payloads[0]["source_event_id"], "turn-request-1")
        self.assertEqual(lobby_payloads[0]["source_event_id"], "lobby2")

    def test_official_turn_prompt_uses_thin_room_envelope_without_recent_event_dump(self):
        room = {
            "agent": {
                "agent_id": "agent-a",
                "last_observed_event_id": "lobby-cursor",
                "last_observed_live_event_id": "live-cursor",
            },
            "meeting_id": "m1",
            "live_events": [
                {
                    "id": "target-b",
                    "kind": "live_agent_turn_request",
                    "official_record": False,
                    "target_agent_id": "agent-b",
                    "audience": "agent:agent-b",
                    "display_name": "Agent B",
                    "content": "private target-B instruction",
                },
                {
                    "id": "official",
                    "kind": "message",
                    "official_record": True,
                    "display_name": "Agent B",
                    "content": "public official statement",
                },
                {
                    "id": "target-a",
                    "kind": "live_agent_turn_request",
                    "official_record": False,
                    "target_agent_id": "agent-a",
                    "audience": "agent:agent-a",
                    "display_name": "Agent A",
                    "content": "agent A private request",
                },
            ]
        }

        prompt = official_turn_prompt(config(agent_id="agent-a", display_name="Agent A"), room, room["live_events"][2])

        self.assertIn("agent A private request", prompt)
        self.assertIn("Source event id: target-a", prompt)
        self.assertIn("Meeting id: m1", prompt)
        self.assertIn("Lobby cursor: lobby-cursor", prompt)
        self.assertIn("Official cursor: live-cursor", prompt)
        self.assertIn("minimal room delivery envelope", prompt)
        self.assertNotIn("public official statement", prompt)
        self.assertNotIn("private target-B instruction", prompt)

    def test_official_turn_prompt_includes_compact_shared_meeting_memory(self):
        room = {
            "shared_memory": {
                "official_event_count": 2,
                "last_official_event_id": "reply-2",
                "rolling_summary": [
                    {"event_id": "reply-1", "speaker": "Architect", "summary": "Keep resident agents explicit."}
                ],
                "decisions": [
                    {"event_id": "reply-1", "speaker": "Architect", "text": "Use host-approved live sessions."}
                ],
                "open_questions": [
                    {"event_id": "reply-2", "speaker": "Critic", "text": "Should play chatter be promoted?"}
                ],
                "action_items": [
                    {"event_id": "reply-2", "speaker": "Critic", "text": "Wire shared memory into resident prompts."}
                ],
            },
            "live_events": [
                {
                    "id": "target-a",
                    "kind": "live_agent_turn_request",
                    "official_record": False,
                    "target_agent_id": "agent-a",
                    "audience": "agent:agent-a",
                    "display_name": "Agent A",
                    "content": "Use the accumulated context.",
                }
            ],
        }

        prompt = official_turn_prompt(config(agent_id="agent-a", display_name="Agent A"), room, room["live_events"][0])

        self.assertIn("Shared meeting memory", prompt)
        self.assertIn("Use host-approved live sessions.", prompt)
        self.assertIn("Should play chatter be promoted?", prompt)
        self.assertIn("Wire shared memory into resident prompts.", prompt)
        self.assertIn("Use the accumulated context.", prompt)

    def test_lobby_delegate_prompt_includes_shared_memory_without_turn_requests(self):
        room = {
            "agent": {
                "agent_id": "agent-a",
                "last_observed_event_id": "evt-before",
                "last_observed_live_event_id": "reply-1",
            },
            "shared_memory": {
                "official_event_count": 1,
                "last_official_event_id": "reply-1",
                "rolling_summary": [
                    {"event_id": "reply-1", "speaker": "Architect", "summary": "Official context only."}
                ],
                "action_items": [
                    {"event_id": "reply-1", "speaker": "Architect", "text": "Keep room prompts compact."}
                ],
            },
            "lobby_events": [
                {"id": "evt-before", "name": "나", "message": "이전 로비는 통째로 싣지 마"},
                {"id": "evt-human", "name": "나", "message": "공유기억 보고 있어?"},
            ],
            "live_events": [
                {
                    "id": "secret-request",
                    "kind": "live_agent_turn_request",
                    "official_record": False,
                    "target_agent_id": "other-agent",
                    "content": "private prompt must stay out",
                }
            ],
        }

        from agentsassemble.live_agent_runner import delegate_prompt

        prompt = delegate_prompt(config(agent_id="agent-a", display_name="Agent A"), room, room["lobby_events"][1])

        self.assertIn("Shared meeting memory", prompt)
        self.assertIn("Official context only.", prompt)
        self.assertIn("Keep room prompts compact.", prompt)
        self.assertIn("공유기억 보고 있어?", prompt)
        self.assertIn("Source event id: evt-human", prompt)
        self.assertIn("Lobby cursor: evt-before", prompt)
        self.assertIn("Official cursor: reply-1", prompt)
        self.assertIn("minimal room delivery envelope", prompt)
        self.assertNotIn("이전 로비는 통째로 싣지 마", prompt)
        self.assertNotIn("private prompt must stay out", prompt)

    def test_official_turn_prompt_labels_review_checkpoint_as_review_not_official_record(self):
        room = {
            "live_events": [
                {
                    "id": "checkpoint-request",
                    "kind": "live_agent_turn_request",
                    "channel": "review",
                    "official_record": False,
                    "review_checkpoint_id": "checkpoint-1",
                    "target_agent_id": "agent-a",
                    "display_name": "Agent A",
                    "content": "검토 기준",
                }
            ],
            "lobby_events": [{"id": "lobby-1", "message": "최근 맥락"}],
        }

        prompt = official_turn_prompt(config(agent_id="agent-a", display_name="Agent A"), room, room["live_events"][0])

        self.assertIn("review checkpoint checkpoint-1", prompt)
        self.assertIn("Reply with one concise review message only.", prompt)
        self.assertNotIn("official meeting record", prompt)
        self.assertIn("검토 기준", prompt)
        self.assertIn("Source event id: checkpoint-request", prompt)
        self.assertNotIn("최근 맥락", prompt)

    def test_runner_uses_room_engagement_mode_to_pause_active_agent(self):
        clock = FakeClock()
        room = {
            "agent": {"agent_id": "agent-a", "engagement_mode": "watch"},
            "lobby_events": [{"id": "evt1", "side": "mine", "name": "나", "message": "always config라도 멈춰"}],
        }
        client = FakeRoomClient([room])
        command_calls = []
        runner = LiveAgentRunner(
            config(engagement_mode="always"),
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

    def test_runner_uses_room_engagement_mode_to_resume_replying(self):
        clock = FakeClock()
        room = {
            "agent": {"agent_id": "agent-a", "engagement_mode": "always"},
            "lobby_events": [{"id": "evt1", "side": "mine", "name": "나", "message": "이름 언급 없이도 답해"}],
        }
        client = FakeRoomClient([room])
        command_calls = []
        runner = LiveAgentRunner(
            config(engagement_mode="mentioned"),
            request_json=client,
            command_runner=lambda command, prompt, *, timeout_seconds: command_calls.append(prompt) or "runtime reply",
            sleep_fn=clock.sleep,
            now_fn=clock,
        )

        self.assertEqual(runner.run(), 1)

        self.assertEqual(len(command_calls), 1)
        lobby_payloads = [payload for url, method, payload in client.calls if url.endswith("/lobby")]
        self.assertEqual(lobby_payloads[0]["source_event_id"], "evt1")

    def test_runner_ignores_invalid_or_wrong_agent_runtime_engagement_mode(self):
        clock = FakeClock()
        invalid_room = {
            "agent": {"agent_id": "agent-a", "engagement_mode": "shout_forever"},
            "lobby_events": [{"id": "evt1", "side": "mine", "name": "나", "message": "config fallback"}],
        }
        wrong_agent_room = {
            "agent": {"agent_id": "agent-b", "engagement_mode": "always"},
            "lobby_events": [
                {"id": "reply-id", "actor_id": "agent-a", "name": "Agent A", "message": "first reply"},
                {"id": "evt2", "side": "mine", "name": "나", "message": "wrong agent fallback"},
            ],
        }
        client = FakeRoomClient([invalid_room, wrong_agent_room])
        command_calls = []
        runner = LiveAgentRunner(
            config(engagement_mode="always", max_ticks=2),
            request_json=client,
            command_runner=lambda command, prompt, *, timeout_seconds: command_calls.append(prompt) or "fallback reply",
            sleep_fn=clock.sleep,
            now_fn=clock,
        )

        self.assertEqual(runner.run(), 2)

        self.assertEqual(len(command_calls), 2)

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
        called_with_at = {"id": "at", "side": "mine", "name": "나", "message": "@agent-a 상태 알려줘"}
        unrelated = {"id": "other", "side": "mine", "name": "나", "message": "아무나 답해"}
        prefix_collision = {"id": "prefix", "side": "mine", "name": "나", "message": "agent-aardvark 상태 알려줘"}
        suffix_collision = {"id": "suffix", "side": "mine", "name": "나", "message": "super-agent-a 상태 알려줘"}
        name_collision = {"id": "name-prefix", "side": "mine", "name": "나", "message": "Agent Alpha 상태 알려줘"}

        self.assertEqual(
            event_reply_candidate([called_by_name], "agent-a", "Agent A", "", max_chain_depth=1, engagement_mode="mentioned"),
            called_by_name,
        )
        self.assertEqual(
            event_reply_candidate([called_by_id], "agent-a", "Agent A", "", max_chain_depth=1, engagement_mode="mentioned"),
            called_by_id,
        )
        self.assertEqual(
            event_reply_candidate([called_with_at], "agent-a", "Agent A", "", max_chain_depth=1, engagement_mode="mentioned"),
            called_with_at,
        )
        self.assertIsNone(
            event_reply_candidate([unrelated], "agent-a", "Agent A", "", max_chain_depth=1, engagement_mode="mentioned")
        )
        self.assertIsNone(
            event_reply_candidate([prefix_collision], "agent-a", "Agent A", "", max_chain_depth=1, engagement_mode="mentioned")
        )
        self.assertIsNone(
            event_reply_candidate([suffix_collision], "agent-a", "Agent A", "", max_chain_depth=1, engagement_mode="mentioned")
        )
        self.assertIsNone(
            event_reply_candidate([name_collision], "agent-a", "Agent A", "", max_chain_depth=1, engagement_mode="mentioned")
        )

    def test_mentioned_mode_handles_non_ascii_casefold_and_regex_meta_names(self):
        korean_call = {"id": "korean", "side": "mine", "name": "나", "message": "설정충 지금 가능해?"}
        german_call = {"id": "casefold", "side": "mine", "name": "나", "message": "STRASSE-BOT 확인해줘"}
        regex_call = {"id": "regex", "side": "mine", "name": "나", "message": "agent.1 상태 알려줘"}
        regex_collision = {"id": "regex-collision", "side": "mine", "name": "나", "message": "agentx1 상태 알려줘"}

        self.assertEqual(
            event_reply_candidate([korean_call], "canon", "설정충", "", max_chain_depth=1, engagement_mode="mentioned"),
            korean_call,
        )
        self.assertEqual(
            event_reply_candidate([german_call], "straße-bot", "Straße Bot", "", max_chain_depth=1, engagement_mode="mentioned"),
            german_call,
        )
        self.assertEqual(
            event_reply_candidate([regex_call], "agent.1", "Agent Dot", "", max_chain_depth=1, engagement_mode="mentioned"),
            regex_call,
        )
        self.assertIsNone(
            event_reply_candidate([regex_collision], "agent.1", "Agent Dot", "", max_chain_depth=1, engagement_mode="mentioned")
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

    def test_idle_runner_survives_transient_periodic_heartbeat_failure(self):
        clock = FakeClock()
        calls = []

        def request_json(url, *, method="GET", payload=None):
            calls.append((url, method, payload))
            if url.endswith("/live-agents") and method == "POST":
                return {"agent": {"agent_id": "agent-a", "status": "online"}}
            if url.endswith("/room"):
                return {"lobby_events": []}
            if url.endswith("/heartbeat"):
                if (payload or {}).get("status") == "online" and len(
                    [call for call in calls if call[0].endswith("/heartbeat") and (call[2] or {}).get("status") == "online"]
                ) == 2:
                    raise ConnectionError("transient heartbeat failure")
                return {"agent": {"agent_id": "agent-a", "status": (payload or {}).get("status", "online")}}
            return {}

        runner = LiveAgentRunner(
            config(max_ticks=2, poll_interval=11.0, heartbeat_interval=10.0),
            request_json=request_json,
            command_runner=lambda command, prompt, *, timeout_seconds: "unused",
            sleep_fn=clock.sleep,
            now_fn=clock,
        )

        self.assertEqual(runner.run(), 0)

        heartbeat_statuses = [payload["status"] for url, method, payload in calls if url.endswith("/heartbeat")]
        self.assertEqual(heartbeat_statuses, ["online", "online", "offline"])

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

    def test_group_config_accepts_official_turn_timeout_without_changing_general_timeout(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "live-agents.json"
            path.write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "agent-official-slow",
                                "command": ["fake"],
                                "timeout_seconds": 5,
                                "official_turn_timeout_seconds": 17,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            loaded = load_group_configs(path)

        self.assertEqual(loaded[0].timeout_seconds, 5)
        self.assertEqual(loaded[0].official_turn_timeout_seconds, 17)

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

    def test_group_config_resolves_relative_persona_path_next_to_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            persona_path = root / "personas" / "yanagi" / "card.json"
            persona_path.parent.mkdir(parents=True)
            persona_path.write_text("{}", encoding="utf-8")
            path = root / "live-agents.json"
            path.write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "agent-persona",
                                "command": ["fake"],
                                "persona_path": "personas/yanagi/card.json",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            loaded = load_group_configs(path)

        self.assertEqual(loaded[0].persona_path, str(persona_path))

    def test_group_config_accepts_character_mode_and_persona_card_alias(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "live-agents.json"
            path.write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "agent-persona",
                                "command": ["fake"],
                                "persona_card_id": "yanagi",
                                "character_mode": "work_speech_only",
                                "first_message_index": 2,
                            },
                            {
                                "agent_id": "agent-off",
                                "command": ["fake"],
                                "persona_id": "plain",
                                "character_mode": "off",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            loaded = load_group_configs(path)

        self.assertEqual(loaded[0].persona_id, "yanagi")
        self.assertEqual(loaded[0].character_mode, "work_speech_only")
        self.assertEqual(loaded[0].first_message_index, 2)
        self.assertEqual(loaded[1].character_mode, "off")

    def test_flow_decision_prompt_respects_character_mode_off(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            card_path = Path(temp_dir) / "card.json"
            card_path.write_text(
                json.dumps({"id": "persona-1", "display_name": "Persona One", "personality": "Dry."}),
                encoding="utf-8",
            )

            prompt = flow_decision_prompt(
                config(engagement_mode="flow", meeting_id="m1", persona_path=str(card_path), character_mode="off"),
                {
                    "meeting_id": "m1",
                    "events": [{"id": "flow-start", "name": "Play Mode", "message": "한마디 해줘."}],
                    "flow": {"flow_id": "flow-1", "topic": "test"},
                },
                {"id": "flow-start", "name": "Play Mode", "message": "한마디 해줘."},
            )

        self.assertNotIn("Play Mode persona card", prompt)

    def test_group_config_rejects_non_object_agent_entries(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "live-agents.json"
            path.write_text(
                json.dumps(
                    {
                        "agents": [
                            {"agent_id": "agent-a", "command": ["fake"]},
                            "agent-b",
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "Each live agent entry must be a JSON object."):
                load_group_configs(path)

    def test_group_config_rejects_non_string_command_entries(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "live-agents.json"
            path.write_text(
                json.dumps(
                    {
                        "agents": [
                            {"agent_id": "agent-a", "command": ["python3", None]},
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "Live agent command entries must be strings."):
                load_group_configs(path)

    def test_group_config_rejects_non_finite_timing_values(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "live-agents.json"
            path.write_text(
                json.dumps(
                    {
                        "poll_interval": float("nan"),
                        "agents": [
                            {"agent_id": "agent-a", "command": ["python3"]},
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "Live agent poll_interval must be a finite non-negative number."):
                load_group_configs(path)

    def test_group_config_rejects_negative_agent_timing_values(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "live-agents.json"
            path.write_text(
                json.dumps(
                    {
                        "agents": [
                            {"agent_id": "agent-a", "command": ["python3"], "cooldown": -1},
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "Live agent cooldown must be a finite non-negative number."):
                load_group_configs(path)

    def test_group_config_rejects_invalid_integer_limits(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "live-agents.json"
            path.write_text(
                json.dumps(
                    {
                        "agents": [
                            {"agent_id": "agent-a", "command": ["python3"], "max_chain_depth": 1.5},
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "Live agent max_chain_depth must be a non-negative integer."):
                load_group_configs(path)

    def test_group_config_rejects_negative_max_ticks_override(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "live-agents.json"
            path.write_text(
                json.dumps({"agents": [{"agent_id": "agent-a", "command": ["python3"]}]}),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "Live agent max_ticks must be a non-negative integer."):
                load_group_configs(path, max_ticks_override=-1)

    def test_group_config_rejects_negative_terminal_idle_timeout(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "live-agents.json"
            path.write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "agent-a",
                                "connection_kind": "terminal_session",
                                "command": ["python3"],
                                "terminal_idle_timeout": -0.1,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "Live agent terminal_idle_timeout must be a finite non-negative number."):
                load_group_configs(path)

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

    def test_group_config_preserves_terminal_session_connection_kind(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "live-agents.json"
            path.write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "claude-terminal",
                                "display_name": "Claude Terminal",
                                "provider_kind": "claude_code",
                                "connection_kind": "terminal_session",
                                "command": ["claude"],
                                "terminal_idle_timeout": 0.2,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            loaded = load_group_configs(path)

        self.assertEqual(loaded[0].connection_kind, "terminal_session")
        self.assertEqual(loaded[0].command, ["claude"])
        self.assertEqual(loaded[0].terminal_idle_timeout, 0.2)

    def test_group_config_preserves_self_service_connection_kind(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "live-agents.json"
            path.write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "antigravity-live",
                                "display_name": "Antigravity Live",
                                "provider_kind": "antigravity_cli",
                                "connection_kind": "self_service",
                                "command": ["antigravity"],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            loaded = load_group_configs(path)

        self.assertEqual(loaded[0].provider_kind, "antigravity_cli")
        self.assertEqual(loaded[0].connection_kind, "self_service")
        self.assertEqual(loaded[0].command, ["antigravity"])

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

    def test_group_config_defaults_codex_live_session_command(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "live-agents.json"
            path.write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "codex-live",
                                "provider_kind": "codex_live_session",
                                "connection_kind": "live_session",
                                "session_id": "019e3038-39cc-76a2-a746-5ba8c0f3b408",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            loaded = load_group_configs(path)

        self.assertEqual(loaded[0].provider_kind, "codex_live_session")
        self.assertEqual(loaded[0].connection_kind, "live_session")
        self.assertEqual(loaded[0].command, ["codex"])

    def test_group_config_defaults_cursor_live_session_command(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "live-agents.json"
            path.write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "cursor-live",
                                "provider_kind": "cursor_live_session",
                                "connection_kind": "live_session",
                                "session_id": "cursor-chat-abc123",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            loaded = load_group_configs(path)

        self.assertEqual(loaded[0].provider_kind, "cursor_live_session")
        self.assertEqual(loaded[0].connection_kind, "live_session")
        self.assertEqual(loaded[0].command, ["cursor-agent"])

    def test_group_config_rejects_non_resident_connection_kind_even_with_command(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "live-agents.json"
            path.write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "manual-agent",
                                "connection_kind": "manual",
                                "command": ["python3", "-c", "print('should not run')"],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                ValueError,
                "Resident groups support local_cli, live_session, terminal_session, remote_bridge, and self_service connections.",
            ):
                load_group_configs(path)

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

    def test_codex_resident_command_runner_starts_then_resumes_session(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            calls = []

            class Completed:
                returncode = 0
                stdout = "session id: 019e3038-39cc-76a2-a746-5ba8c0f3b408\n"
                stderr = ""

            def command_runner(command, **kwargs):
                calls.append({"command": command, "kwargs": kwargs})
                output_path = Path(command[command.index("--output-last-message") + 1])
                output_path.write_text(f"reply {len(calls)}", encoding="utf-8")
                return Completed()

            runner = CodexResidentCommandRunner(
                config(
                    provider_kind="codex_live_session",
                    connection_kind="live_session",
                    command=["codex"],
                ),
                command_runner=command_runner,
                cwd=Path(temp_dir),
            )
            try:
                first_reply = runner([], "first prompt", timeout_seconds=45)
                second_reply = runner([], "second prompt", timeout_seconds=45)
            finally:
                runner.close()

        self.assertEqual(first_reply, "reply 1")
        self.assertEqual(second_reply, "reply 2")
        self.assertEqual(runner.session_id, "019e3038-39cc-76a2-a746-5ba8c0f3b408")
        self.assertEqual(calls[0]["command"][:6], ["codex", "exec", "--sandbox", "read-only", "--ignore-rules", "--skip-git-repo-check"])
        self.assertIn("--cd", calls[0]["command"])
        self.assertEqual(calls[0]["command"][-1], "-")
        self.assertEqual(calls[0]["kwargs"]["input"], "first prompt")
        self.assertEqual(calls[0]["kwargs"]["cwd"], str(Path(temp_dir)))
        self.assertEqual(calls[1]["command"][:6], ["codex", "exec", "--sandbox", "read-only", "--ignore-rules", "resume"])
        self.assertIn("--skip-git-repo-check", calls[1]["command"])
        self.assertIn("019e3038-39cc-76a2-a746-5ba8c0f3b408", calls[1]["command"])
        self.assertNotIn("--cd", calls[1]["command"])
        self.assertEqual(calls[1]["kwargs"]["input"], "second prompt")

    def test_codex_resident_command_runner_uses_only_configured_executable_token(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            calls = []

            def fake_run(command, **kwargs):
                calls.append({"command": command, "kwargs": kwargs})
                output_path = Path(command[command.index("--output-last-message") + 1])
                output_path.write_text("reply", encoding="utf-8")
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            runner = CodexResidentCommandRunner(
                config(
                    provider_kind="codex_live_session",
                    connection_kind="live_session",
                    command=["codex", "--dangerously-bypass-approvals-and-sandbox"],
                ),
                command_runner=fake_run,
                cwd=Path(temp_dir),
            )
            try:
                self.assertEqual(runner(["ignored"], "prompt", timeout_seconds=30), "reply")
            finally:
                runner.close()

        self.assertEqual(calls[0]["command"][:5], ["codex", "exec", "--sandbox", "read-only", "--ignore-rules"])
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", calls[0]["command"])

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

    def test_remote_bridge_resident_command_runner_treats_command_failure_as_error(self):
        def requester(url, headers, payload, timeout_seconds):
            return {
                "text": "Claude Code bridge failed with return code 1.",
                "metadata": {
                    "bridge": "friend-mac",
                    "step": "lobby",
                    "returncode": 1,
                    "stderr": "not logged in token=secret-token",
                },
            }

        runner = RemoteBridgeResidentCommandRunner(
            config(
                connection_kind="remote_bridge",
                endpoint="http://friend.local:8777",
                auth_ref="literal:bridge-token",
            ),
            requester=requester,
        )

        with self.assertRaisesRegex(RuntimeError, "Remote bridge command failed with return code 1"):
            runner([], "prompt", timeout_seconds=45)

    def test_runner_records_remote_bridge_command_failure_as_error_heartbeat(self):
        clock = FakeClock()
        client = FakeRoomClient(
            [{"lobby_events": [{"id": "evt1", "name": "나", "message": "원격 브릿지 응답해줘"}]}]
        )

        def requester(url, headers, payload, timeout_seconds):
            return {
                "text": "Claude Code bridge failed with return code 1.",
                "metadata": {
                    "bridge": "friend-mac",
                    "step": "lobby",
                    "returncode": 1,
                    "stderr": "not logged in token=secret-token",
                    "command": "claude -p --token secret-token",
                },
            }

        remote_config = config(
            provider_kind="claude_code",
            connection_kind="remote_bridge",
            endpoint="http://friend.local:8777",
            auth_ref="literal:bridge-token",
        )
        bridge_runner = RemoteBridgeResidentCommandRunner(remote_config, requester=requester)
        runner = LiveAgentRunner(
            remote_config,
            request_json=client,
            command_runner=bridge_runner,
            sleep_fn=clock.sleep,
            now_fn=clock,
        )

        self.assertEqual(runner.run(), 0)

        lobby_payloads = [payload for url, method, payload in client.calls if url.endswith("/lobby")]
        error_heartbeats = [
            payload
            for url, method, payload in client.calls
            if url.endswith("/heartbeat") and payload and payload.get("status") == "error"
        ]
        self.assertEqual(lobby_payloads, [])
        self.assertEqual(error_heartbeats[-1]["last_observed_event_id"], "evt1")
        self.assertIn("Remote bridge command failed with return code 1", error_heartbeats[-1]["last_error"])
        self.assertNotIn("secret-token", error_heartbeats[-1]["last_error"])
        self.assertNotIn("friend.local", error_heartbeats[-1]["last_error"])

    def test_runner_records_subprocess_failure_without_command_args(self):
        clock = FakeClock()
        client = FakeRoomClient(
            [{"lobby_events": [{"id": "evt1", "name": "나", "message": "로컬 CLI 응답해줘"}]}]
        )

        def fail_command(command, prompt, *, timeout_seconds):
            del prompt, timeout_seconds
            raise subprocess.CalledProcessError(
                7,
                [*command, "--token", "secret-token", "--config", "/private/live-agents.json"],
                output="private stdout",
                stderr="private stderr",
            )

        runner = LiveAgentRunner(
            config(command=["fake-agent"]),
            request_json=client,
            command_runner=fail_command,
            sleep_fn=clock.sleep,
            now_fn=clock,
        )

        self.assertEqual(runner.run(), 0)

        error_heartbeats = [
            payload
            for url, method, payload in client.calls
            if url.endswith("/heartbeat") and payload and payload.get("status") == "error"
        ]
        self.assertEqual(error_heartbeats[-1]["last_error"], "Resident command exited with return code 7.")
        self.assertEqual(runner.last_error, "Resident command exited with return code 7.")
        self.assertNotIn("secret-token", error_heartbeats[-1]["last_error"])
        self.assertNotIn("live-agents.json", error_heartbeats[-1]["last_error"])
        self.assertNotIn("private stdout", error_heartbeats[-1]["last_error"])
        self.assertNotIn("private stderr", error_heartbeats[-1]["last_error"])

    def test_runner_records_subprocess_timeout_without_command_args(self):
        clock = FakeClock()
        client = FakeRoomClient(
            [{"lobby_events": [{"id": "evt1", "name": "나", "message": "로컬 CLI 타임아웃?"}]}]
        )

        def timeout_command(command, prompt, *, timeout_seconds):
            del prompt
            raise subprocess.TimeoutExpired(
                [*command, "--token", "secret-token"],
                timeout_seconds,
                output="private stdout",
                stderr="private stderr",
            )

        runner = LiveAgentRunner(
            config(command=["fake-agent"], timeout_seconds=9),
            request_json=client,
            command_runner=timeout_command,
            sleep_fn=clock.sleep,
            now_fn=clock,
        )

        self.assertEqual(runner.run(), 0)

        error_heartbeats = [
            payload
            for url, method, payload in client.calls
            if url.endswith("/heartbeat") and payload and payload.get("status") == "error"
        ]
        self.assertEqual(error_heartbeats[-1]["last_error"], "Resident command timed out after 9 seconds.")
        self.assertEqual(runner.last_error, "Resident command timed out after 9 seconds.")
        self.assertNotIn("secret-token", error_heartbeats[-1]["last_error"])
        self.assertNotIn("private stdout", error_heartbeats[-1]["last_error"])
        self.assertNotIn("private stderr", error_heartbeats[-1]["last_error"])

    def test_runner_records_os_error_without_path(self):
        clock = FakeClock()
        client = FakeRoomClient(
            [{"lobby_events": [{"id": "evt1", "name": "나", "message": "로컬 CLI 실행돼?"}]}]
        )

        def fail_command(command, prompt, *, timeout_seconds):
            del command, prompt, timeout_seconds
            raise FileNotFoundError(2, "No such file or directory", "/private/token/fake-agent")

        runner = LiveAgentRunner(
            config(command=["/private/token/fake-agent"]),
            request_json=client,
            command_runner=fail_command,
            sleep_fn=clock.sleep,
            now_fn=clock,
        )

        self.assertEqual(runner.run(), 0)

        error_heartbeats = [
            payload
            for url, method, payload in client.calls
            if url.endswith("/heartbeat") and payload and payload.get("status") == "error"
        ]
        self.assertEqual(error_heartbeats[-1]["last_error"], "Resident command failed: No such file or directory.")
        self.assertEqual(runner.last_error, "Resident command failed: No such file or directory.")
        self.assertNotIn("/private/token/fake-agent", error_heartbeats[-1]["last_error"])

    def test_runner_redacts_sensitive_generic_command_error(self):
        clock = FakeClock()
        client = FakeRoomClient(
            [{"lobby_events": [{"id": "evt1", "name": "나", "message": "로컬 CLI 에러?"}]}]
        )

        def fail_command(command, prompt, *, timeout_seconds):
            del command, prompt, timeout_seconds
            raise RuntimeError("failed using /private/live-agents.json")

        runner = LiveAgentRunner(
            config(command=["fake-agent"]),
            request_json=client,
            command_runner=fail_command,
            sleep_fn=clock.sleep,
            now_fn=clock,
        )

        self.assertEqual(runner.run(), 0)

        error_heartbeats = [
            payload
            for url, method, payload in client.calls
            if url.endswith("/heartbeat") and payload and payload.get("status") == "error"
        ]
        self.assertEqual(error_heartbeats[-1]["last_error"], "Resident command error details redacted.")
        self.assertEqual(runner.last_error, "Resident command error details redacted.")
        self.assertNotIn("live-agents.json", error_heartbeats[-1]["last_error"])

    def test_runner_records_jsonl_live_session_failure_without_sensitive_stderr(self):
        clock = FakeClock()
        client = FakeRoomClient(
            [{"lobby_events": [{"id": "evt1", "name": "나", "message": "JSONL 세션 응답해줘"}]}]
        )

        class JsonlCommandRunner:
            def __init__(self):
                self.session = None

            def __call__(self, command, prompt, *, timeout_seconds):
                del prompt
                self.session = JsonlLiveSession(command)
                try:
                    return self.session.ask("prompt", timeout_seconds=timeout_seconds)
                except Exception:
                    self.session.close()
                    raise

        script = "\n".join(
            [
                "import sys",
                "print('token=secret-token http://friend.local/private/live-agents.json', file=sys.stderr, flush=True)",
                "sys.exit(7)",
            ]
        )
        runner = LiveAgentRunner(
            config(
                provider_kind="jsonl",
                connection_kind="live_session",
                command=[sys.executable, "-u", "-c", script],
            ),
            request_json=client,
            command_runner=JsonlCommandRunner(),
            sleep_fn=clock.sleep,
            now_fn=clock,
        )

        self.assertEqual(runner.run(), 0)

        error_heartbeats = [
            payload
            for url, method, payload in client.calls
            if url.endswith("/heartbeat") and payload and payload.get("status") == "error"
        ]
        self.assertIn("stderr tail redacted", error_heartbeats[-1]["last_error"])
        self.assertNotIn("secret-token", error_heartbeats[-1]["last_error"])
        self.assertNotIn("friend.local", error_heartbeats[-1]["last_error"])
        self.assertNotIn("live-agents.json", error_heartbeats[-1]["last_error"])

    def test_runner_heartbeats_updated_command_runner_session_id_after_reply(self):
        clock = FakeClock()
        room = {"lobby_events": [{"id": "evt1", "name": "나", "message": "세션 이어서 답해줘"}]}
        client = FakeRoomClient([room])

        class SessionCommandRunner:
            def __init__(self):
                self.session_id = ""

            def __call__(self, command, prompt, *, timeout_seconds):
                del command, prompt, timeout_seconds
                self.session_id = "019e3038-39cc-76a2-a746-5ba8c0f3b408"
                return "Codex resident reply"

        command_runner = SessionCommandRunner()
        runner = LiveAgentRunner(
            config(provider_kind="codex_live_session", connection_kind="live_session", command=["codex"]),
            request_json=client,
            command_runner=command_runner,
            sleep_fn=clock.sleep,
            now_fn=clock,
        )

        self.assertEqual(runner.run(), 1)

        reply_heartbeats = [
            payload
            for url, method, payload in client.calls
            if url.endswith("/heartbeat") and payload and payload.get("last_reply_at")
        ]
        self.assertEqual(reply_heartbeats[-1]["session_id"], "019e3038-39cc-76a2-a746-5ba8c0f3b408")

    def test_runner_restores_command_runner_session_id_from_registration_response(self):
        clock = FakeClock()
        room = {"lobby_events": [{"id": "evt1", "name": "나", "message": "재시작 후 이어서 답해줘"}]}
        client = FakeRoomClient(
            [room],
            register_agent={
                "agent_id": "agent-a",
                "status": "online",
                "session_id": "019e3038-39cc-76a2-a746-5ba8c0f3b408",
            },
        )

        class SessionCommandRunner:
            def __init__(self):
                self.session_id = ""
                self.seen_session_ids = []

            def __call__(self, command, prompt, *, timeout_seconds):
                del command, prompt, timeout_seconds
                self.seen_session_ids.append(self.session_id)
                return "Codex resumed reply"

        command_runner = SessionCommandRunner()
        runner = LiveAgentRunner(
            config(
                provider_kind="codex_live_session",
                connection_kind="live_session",
                session_id="   ",
                command=["codex"],
            ),
            request_json=client,
            command_runner=command_runner,
            sleep_fn=clock.sleep,
            now_fn=clock,
        )

        self.assertEqual(runner.run(), 1)

        self.assertEqual(command_runner.seen_session_ids, ["019e3038-39cc-76a2-a746-5ba8c0f3b408"])
        register_payloads = [
            payload
            for url, method, payload in client.calls
            if url.endswith("/live-agents") and method == "POST"
        ]
        self.assertEqual(register_payloads[0]["session_id"], "")

    def test_runner_restores_codex_resident_session_id_before_first_command(self):
        clock = FakeClock()
        room = {"lobby_events": [{"id": "evt1", "name": "나", "message": "Codex 세션 이어서 답해줘"}]}
        client = FakeRoomClient(
            [room],
            register_agent={
                "agent_id": "agent-a",
                "status": "online",
                "session_id": "019e3038-39cc-76a2-a746-5ba8c0f3b408",
            },
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            calls = []

            class Completed:
                returncode = 0
                stdout = ""
                stderr = ""

            def command_runner(command, **kwargs):
                calls.append({"command": command, "kwargs": kwargs})
                output_path = Path(command[command.index("--output-last-message") + 1])
                output_path.write_text("Codex resumed reply", encoding="utf-8")
                return Completed()

            codex_runner = CodexResidentCommandRunner(
                config(provider_kind="codex_live_session", connection_kind="live_session", command=["codex"]),
                command_runner=command_runner,
                cwd=Path(temp_dir),
            )
            runner = LiveAgentRunner(
                config(provider_kind="codex_live_session", connection_kind="live_session", command=["codex"]),
                request_json=client,
                command_runner=codex_runner,
                sleep_fn=clock.sleep,
                now_fn=clock,
            )
            try:
                replies = runner.run()
            finally:
                codex_runner.close()

        self.assertEqual(replies, 1)
        self.assertEqual(calls[0]["command"][:6], ["codex", "exec", "--sandbox", "read-only", "--ignore-rules", "resume"])
        self.assertIn("--skip-git-repo-check", calls[0]["command"])
        self.assertIn("019e3038-39cc-76a2-a746-5ba8c0f3b408", calls[0]["command"])
        self.assertNotIn("--cd", calls[0]["command"])
        self.assertIn("Codex 세션 이어서 답해줘", calls[0]["kwargs"]["input"])

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
