import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from agentsassemble.meeting_events import append_live_event, write_live_state


ROOT = Path(__file__).resolve().parents[1]


def write_record(meeting_dir: Path, name: str = "live_state.json", **overrides):
    payload = {
        "meeting_id": "m1",
        "topic": "runtime",
        "live_status": "running",
    }
    payload.update(overrides)
    (meeting_dir / name).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return payload


class MeetingLifecycleTests(unittest.TestCase):
    def test_archived_when_no_state_files_exist(self):
        from agentsassemble.meeting_lifecycle import project_meeting_lifecycle

        with tempfile.TemporaryDirectory() as temp_dir:
            meeting_dir = Path(temp_dir) / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)

            payload = project_meeting_lifecycle(meeting_dir)

        self.assertEqual(payload["state"], "archived")

    def test_unknown_when_record_is_malformed(self):
        from agentsassemble.meeting_lifecycle import project_meeting_lifecycle

        with tempfile.TemporaryDirectory() as temp_dir:
            meeting_dir = Path(temp_dir) / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            (meeting_dir / "live_state.json").write_text("{", encoding="utf-8")

            payload = project_meeting_lifecycle(meeting_dir)

        self.assertEqual(payload["state"], "unknown")
        self.assertIn("malformed", payload["attention"])

    def test_preparing_when_running_without_bindings_or_events(self):
        from agentsassemble.meeting_lifecycle import project_meeting_lifecycle

        with tempfile.TemporaryDirectory() as temp_dir:
            meeting_dir = Path(temp_dir) / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(meeting_dir, {"meeting_id": "m1", "topic": "runtime", "live_status": "running"})

            payload = project_meeting_lifecycle(meeting_dir, now=time.time())

        self.assertEqual(payload["state"], "preparing")

    def test_waiting_for_agents_when_bindings_present_without_presence(self):
        from agentsassemble.meeting_lifecycle import project_meeting_lifecycle

        with tempfile.TemporaryDirectory() as temp_dir:
            meeting_dir = Path(temp_dir) / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_record(
                meeting_dir,
                roles=[{"id": "architect", "display_name": "Architect"}],
                agent_bindings=[
                    {"agent_id": "agent-a", "role_id": "architect", "permission_profile_id": "meeting"}
                ],
                permission_profiles={"meeting": {"id": "meeting", "meeting_read": True, "official_turn": True}},
            )

            payload = project_meeting_lifecycle(meeting_dir, now=time.time(), live_agents=[])

        self.assertEqual(payload["state"], "waiting_for_agents")
        self.assertEqual(payload["role_hints"][0]["admission_status"], "waiting_for_agent")

    def test_running_official_turns_when_official_message_is_recorded(self):
        from agentsassemble.meeting_lifecycle import project_meeting_lifecycle

        with tempfile.TemporaryDirectory() as temp_dir:
            meeting_dir = Path(temp_dir) / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_record(meeting_dir)
            append_live_event(
                meeting_dir,
                {
                    "kind": "message",
                    "meeting_id": "m1",
                    "actor_id": "agent-a",
                    "display_name": "Agent A",
                    "content": "official content must not enter lifecycle",
                },
            )

            payload = project_meeting_lifecycle(meeting_dir, now=time.time())

        self.assertEqual(payload["state"], "running_official_turns")
        self.assertNotIn("official content", json.dumps(payload, ensure_ascii=False))

    def test_blocked_by_pending_turns_when_request_is_unanswered(self):
        from agentsassemble.meeting_lifecycle import project_meeting_lifecycle

        with tempfile.TemporaryDirectory() as temp_dir:
            meeting_dir = Path(temp_dir) / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_record(meeting_dir)
            append_live_event(
                meeting_dir,
                {
                    "kind": "live_agent_turn_request",
                    "meeting_id": "m1",
                    "target_agent_id": "agent-a",
                    "content": "private request text must not leak",
                },
            )

            payload = project_meeting_lifecycle(meeting_dir, now=time.time())

        self.assertEqual(payload["state"], "blocked_by_pending_turns")
        self.assertEqual(payload["counts"]["pending_turns"], 1)
        self.assertNotIn("private request text", json.dumps(payload, ensure_ascii=False))

    def test_stopped_when_running_state_is_stale(self):
        from agentsassemble.meeting_lifecycle import STALE_RUNNING_SECONDS, project_meeting_lifecycle

        with tempfile.TemporaryDirectory() as temp_dir:
            meeting_dir = Path(temp_dir) / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_record(meeting_dir)
            old = time.time() - STALE_RUNNING_SECONDS - 30
            os.utime(meeting_dir / "live_state.json", (old, old))

            payload = project_meeting_lifecycle(meeting_dir, now=time.time())

        self.assertEqual(payload["state"], "stopped")
        self.assertEqual(payload["status_source"], "stale_running_inference")

    def test_finalized_when_final_record_is_complete(self):
        from agentsassemble.meeting_lifecycle import project_meeting_lifecycle

        with tempfile.TemporaryDirectory() as temp_dir:
            meeting_dir = Path(temp_dir) / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_record(meeting_dir, name="meeting.json", live_status="complete")

            payload = project_meeting_lifecycle(meeting_dir)

        self.assertEqual(payload["state"], "finalized")

    def test_role_hints_only_emit_safe_permission_flags_and_violation_count(self):
        from agentsassemble.meeting_lifecycle import project_meeting_lifecycle

        with tempfile.TemporaryDirectory() as temp_dir:
            meeting_dir = Path(temp_dir) / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_record(
                meeting_dir,
                roles=[{"id": "architect", "display_name": "Architect"}],
                permission_profiles={
                    "dangerous": {
                        "id": "dangerous",
                        "meeting_read": True,
                        "lobby_chat": True,
                        "official_turn": True,
                        "web_search": True,
                        "tool_use": True,
                        "git_write": True,
                        "secrets": True,
                    }
                },
                agent_bindings=[
                    {
                        "agent_id": "agent-a",
                        "role_id": "architect",
                        "provider_id": "provider-secret",
                        "model_id": "model-secret",
                        "session_id": "019e3038-39cc-76a2-a746-5ba8c0f3b408",
                        "permission_profile_id": "dangerous",
                    }
                ],
            )

            payload = project_meeting_lifecycle(
                meeting_dir,
                now=time.time(),
                live_agents=[
                    {
                        "agent_id": "agent-a",
                        "status": "online",
                        "admission_status": "bound_to_meeting",
                        "host_approved_binding": True,
                    }
                ],
            )

        hint = payload["role_hints"][0]
        self.assertEqual(hint["role_id"], "architect")
        self.assertEqual(hint["display_name"], "Architect")
        self.assertEqual(hint["admission_status"], "bound_to_meeting")
        self.assertEqual(hint["permissions"]["meeting_read"], True)
        self.assertEqual(hint["permissions"]["official_turn"], True)
        self.assertEqual(hint["permissions"]["tool_use"], True)
        self.assertEqual(hint["unsafe_permission_violations"], 2)
        serialized = json.dumps(payload, ensure_ascii=False)
        for forbidden in (
            "provider-secret",
            "model-secret",
            "session_id",
            "permission_profile_id",
            "git_write",
            "secrets",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_payload_does_not_leak_secrets_or_raw_paths(self):
        from agentsassemble.meeting_lifecycle import project_meeting_lifecycle

        with tempfile.TemporaryDirectory() as temp_dir:
            meeting_dir = Path(temp_dir) / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            write_record(
                meeting_dir,
                agents_config=str(ROOT / "configs" / "agents.json"),
                prompt="secret prompt",
                output="secret output",
                command=["provider", "--token", "secret"],
                cwd=str(ROOT),
                roles=[{"id": "architect", "display_name": "Architect"}],
                agent_bindings=[
                    {
                        "agent_id": "agent-a",
                        "role_id": "architect",
                        "provider_id": "p1",
                        "model_id": "m1",
                        "session_id": "session-secret",
                        "permission_profile_id": "meeting",
                    }
                ],
                permission_profiles={"meeting": {"id": "meeting", "meeting_read": True}},
            )

            payload = project_meeting_lifecycle(meeting_dir, now=time.time())

        serialized = json.dumps(payload, ensure_ascii=False)
        for forbidden in (
            str(ROOT),
            str(Path.home()),
            "agents_config",
            "cwd",
            "argv",
            "command",
            "prompt",
            "output",
            "provider_id",
            "model_id",
            "session_id",
            "p1",
            "session-secret",
        ):
            self.assertNotIn(forbidden, serialized)


if __name__ == "__main__":
    unittest.main()
