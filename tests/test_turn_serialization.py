import tempfile
import unittest
from pathlib import Path

from agentsassemble.gui import _flow_turn_conflict
from agentsassemble.live_agent_runner import (
    LiveAgentRunner,
    ResidentAgentConfig,
    _latest_human_reply_candidate,
)
from agentsassemble.legacy.meeting.core.events import append_lobby_event_to_file

MEETING = "turn-test-room"


def _write_event(root: Path, payload: dict) -> dict:
    return append_lobby_event_to_file(root / "lobby.jsonl", payload, allow_flow_metadata=True)


def _flow_started(root: Path, *, flow_id: str, policy: str) -> dict:
    return _write_event(root, {
        "name": "Play Mode",
        "actor_id": "flow",
        "message": "start",
        "flow_id": flow_id,
        "flow_meeting_id": MEETING,
        "flow_event_type": "started",
        "flow_policy": policy,
    })


def _flow_speech(root: Path, *, flow_id: str, actor_id: str, message: str) -> dict:
    return _write_event(root, {
        "name": actor_id,
        "actor_id": actor_id,
        "message": message,
        "flow_id": flow_id,
        "flow_meeting_id": MEETING,
        "flow_action": "speak",
    })


class FlowTurnConflictTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_turn_conflict_when_other_agent_spoke_after_source(self):
        _flow_started(self.root, flow_id="f1", policy="turn_based_floor")
        source = _flow_speech(self.root, flow_id="f1", actor_id="agent-a", message="먹")
        _flow_speech(self.root, flow_id="f1", actor_id="agent-b", message="먹물")
        conflict = _flow_turn_conflict(
            self.root,
            actor_id="agent-c",
            source_event_id=str(source["id"]),
            flow_id="f1",
            flow_action="speak",
            meeting_id=MEETING,
            message="먹보",
        )
        self.assertEqual(conflict, "turn_conflict")

    def test_no_conflict_when_room_unchanged_since_source(self):
        _flow_started(self.root, flow_id="f1", policy="turn_based_floor")
        source = _flow_speech(self.root, flow_id="f1", actor_id="agent-a", message="먹")
        conflict = _flow_turn_conflict(
            self.root,
            actor_id="agent-b",
            source_event_id=str(source["id"]),
            flow_id="f1",
            flow_action="speak",
            meeting_id=MEETING,
            message="먹물",
        )
        self.assertEqual(conflict, "")

    def test_free_interval_policy_skips_cas_but_keeps_duplicate_guard(self):
        _flow_started(self.root, flow_id="f1", policy="free_interval")
        source = _flow_speech(self.root, flow_id="f1", actor_id="agent-a", message="안녕")
        _flow_speech(self.root, flow_id="f1", actor_id="agent-b", message="반가워")
        allowed = _flow_turn_conflict(
            self.root,
            actor_id="agent-c",
            source_event_id=str(source["id"]),
            flow_id="f1",
            flow_action="speak",
            meeting_id=MEETING,
            message="나도 반가워",
        )
        self.assertEqual(allowed, "")
        duplicate = _flow_turn_conflict(
            self.root,
            actor_id="agent-c",
            source_event_id=str(source["id"]),
            flow_id="f1",
            flow_action="speak",
            meeting_id=MEETING,
            message="반가워",
        )
        self.assertEqual(duplicate, "duplicate_flow_message")

    def test_word_chain_duplicate_word_rejected(self):
        # 끝말잇기 재현: agent-b가 이미 '먹물'을 냈는데 agent-c도 '먹물'
        _flow_started(self.root, flow_id="f1", policy="round_robin")
        source = _flow_speech(self.root, flow_id="f1", actor_id="agent-a", message="치킨무")
        _flow_speech(self.root, flow_id="f1", actor_id="agent-b", message="먹물")
        conflict = _flow_turn_conflict(
            self.root,
            actor_id="agent-c",
            source_event_id=str(source["id"]),
            flow_id="f1",
            flow_action="speak",
            meeting_id=MEETING,
            message="먹물",
        )
        self.assertIn(conflict, {"turn_conflict", "duplicate_flow_message"})

    def test_non_flow_post_is_never_blocked(self):
        conflict = _flow_turn_conflict(
            self.root,
            actor_id="agent-a",
            source_event_id="",
            flow_id="",
            flow_action="",
            meeting_id=MEETING,
            message="자유 발언",
        )
        self.assertEqual(conflict, "")


def _runner_config(**overrides) -> ResidentAgentConfig:
    defaults = dict(
        server="http://room.local",
        agent_id="agent-a",
        display_name="Agent A",
        provider_kind="local_cli",
        connection_kind="local_cli",
        session_id="",
        endpoint="",
        auth_ref="",
        meeting_id="",
        engagement_mode="always",
        command=["echo"],
        timeout_seconds=5,
        poll_interval=0,
        heartbeat_interval=0,
        cooldown=0,
        max_chain_depth=1,
        max_ticks=1,
    )
    defaults.update(overrides)
    return ResidentAgentConfig(**defaults)


class ScriptedRoomClient:
    def __init__(self, rooms):
        self.rooms = list(rooms)
        self.calls = []

    def __call__(self, url, *, method="GET", payload=None):
        self.calls.append((url, method, payload))
        if url.endswith("/room"):
            return self.rooms.pop(0) if self.rooms else {"lobby_events": []}
        if url.endswith("/lobby"):
            return {"event": {"id": "posted-id"}}
        if url.endswith("/live-agents") and method == "POST":
            return {"agent": {"agent_id": "agent-a", "status": "online"}}
        return {"agent": {"agent_id": "agent-a", "status": "online"}}


class HumanPriorityAndPreemptionTests(unittest.TestCase):
    def test_human_event_outranks_earlier_agent_chatter(self):
        events = [
            {"id": "a1", "actor_id": "agent-b", "name": "Agent B", "message": "에이전트 수다", "auto_chain_depth": 1},
            {"id": "h1", "side": "mine", "name": "나", "message": "사람 질문"},
        ]
        chosen = _latest_human_reply_candidate(
            events, "agent-a", "Agent A", "", max_chain_depth=1, engagement_mode="always",
        )
        self.assertEqual(chosen["id"], "h1")

    def test_oldest_human_first(self):
        events = [
            {"id": "h1", "side": "mine", "name": "나", "message": "첫 질문"},
            {"id": "h2", "side": "mine", "name": "나", "message": "둘째 질문"},
        ]
        chosen = _latest_human_reply_candidate(
            events, "agent-a", "Agent A", "", max_chain_depth=1, engagement_mode="always",
        )
        self.assertEqual(chosen["id"], "h1")

    def test_reply_dropped_when_human_interrupts_mid_generation(self):
        base_room = {
            "agent": {"agent_id": "agent-a", "engagement_mode": "always"},
            "lobby_events": [{"id": "h1", "side": "mine", "name": "나", "message": "질문"}],
        }
        interrupted_room = {
            "agent": {"agent_id": "agent-a", "engagement_mode": "always"},
            "lobby_events": [
                {"id": "h1", "side": "mine", "name": "나", "message": "질문"},
                {"id": "h2", "side": "mine", "name": "나", "message": "아 잠깐, 다른 질문"},
            ],
        }
        client = ScriptedRoomClient([base_room, interrupted_room])
        runner = LiveAgentRunner(
            _runner_config(),
            request_json=client,
            command_runner=lambda command, prompt, *, timeout_seconds: "낡은 답변",
            sleep_fn=lambda seconds: None,
        )
        replies = runner.run()
        self.assertEqual(replies, 0)
        lobby_posts = [c for c in client.calls if c[0].endswith("/lobby") and c[1] == "POST"]
        self.assertEqual(lobby_posts, [])  # 게시 전에 폐기됨
        self.assertEqual(runner.last_observed_event_id, "h1")  # 커서는 전진 → 다음 틱에 h2 응답

    def test_turn_conflict_response_treated_as_skip_not_error(self):
        room = {
            "agent": {"agent_id": "agent-a", "engagement_mode": "always"},
            "lobby_events": [{"id": "h1", "side": "mine", "name": "나", "message": "질문"}],
        }

        class ConflictClient(ScriptedRoomClient):
            def __call__(self, url, *, method="GET", payload=None):
                if url.endswith("/lobby") and method == "POST":
                    self.calls.append((url, method, payload))
                    return {"status": "turn_conflict"}
                return super().__call__(url, method=method, payload=payload)

        client = ConflictClient([room, room])
        runner = LiveAgentRunner(
            _runner_config(),
            request_json=client,
            command_runner=lambda command, prompt, *, timeout_seconds: "늦은 답",
            sleep_fn=lambda seconds: None,
        )
        replies = runner.run()
        self.assertEqual(replies, 0)
        self.assertEqual(runner.last_error, "")  # 에러로 취급하지 않음
        self.assertEqual(runner.last_observed_event_id, "h1")


if __name__ == "__main__":
    unittest.main()
