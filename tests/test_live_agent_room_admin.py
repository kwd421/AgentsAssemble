import json
import tempfile
import unittest
from pathlib import Path

from agentsassemble.live_agent_frontend_create import frontend_live_agent_create_payload
from agentsassemble.live_agent_room_admin import (
    LegacyLiveAgentRoomSessionService,
    delete_live_agent_session_payload,
    expel_live_agent_from_room_payload,
)
from agentsassemble.live_agent_operations import read_live_agent_operations
from agentsassemble.live_agents import connect_live_agent, read_live_agents
from agentsassemble.multi_host_invites import NATIVE_REMOTE_ROOM_CLIENT_KIND
from agentsassemble.room_invite import create_room_invite, join_room_with_invite, reset_state, verify_session_token

from tests.test_live_agent_frontend_create import FakeSupervisor, write_meeting


class AdminSupervisor(FakeSupervisor):
    def __init__(self) -> None:
        super().__init__()
        self.groups: list[dict[str, object]] = []
        self.stopped: list[str] = []
        self.deleted: list[str] = []

    def list_groups(self):
        return list(self.groups)

    def snapshot_groups(self):
        return list(self.groups)

    def stop_group_if_owned(self, group_id: str, *, meeting_id: str, agent_ids: list[str]):
        for index, group in enumerate(self.groups):
            if group.get("group_id") != group_id:
                continue
            manifest_agent_ids = [
                str(agent.get("agent_id") or "")
                for agent in group.get("agents", [])
                if isinstance(agent, dict)
            ]
            if group.get("meeting_id") != meeting_id or manifest_agent_ids != agent_ids:
                raise ValueError("not owned")
            self.stopped.append(group_id)
            stopped = {**group, "status": "stopped"}
            self.groups[index] = stopped
            return stopped
        raise ValueError("not found")

    def delete_group_record_if_owned(self, group_id: str, *, meeting_id: str, agent_ids: list[str]):
        for index, group in enumerate(self.groups):
            if group.get("group_id") != group_id:
                continue
            manifest_agent_ids = [
                str(agent.get("agent_id") or "")
                for agent in group.get("agents", [])
                if isinstance(agent, dict)
            ]
            if group.get("meeting_id") != meeting_id or manifest_agent_ids != agent_ids:
                raise ValueError("not owned")
            self.deleted.append(group_id)
            del self.groups[index]
            return {"status": "deleted", "group_id": group_id}
        return {"status": "not_found", "group_id": group_id}


class LiveAgentRoomAdminTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_state()

    def tearDown(self) -> None:
        reset_state()

    def test_expel_removes_agent_from_room_but_keeps_saved_session_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "aa"
            workspace = Path(temp_dir) / "project"
            workspace.mkdir()
            write_meeting(root)
            created = frontend_live_agent_create_payload(
                root,
                AdminSupervisor(),
                {
                    "meeting_id": "room-a",
                    "provider_id": "cursor",
                    "display_name": "Cursor To Expel",
                    "workspace_path": str(workspace),
                    "start_now": False,
                },
                default_server="http://127.0.0.1:8765",
            )

            result = expel_live_agent_from_room_payload(
                root,
                AdminSupervisor(),
                {
                    "meeting_id": "room-a",
                    "agent_id": created["agent"]["agent_id"],
                    "group_id": created["group_id"],
                },
            )

            self.assertEqual(result["status"], "expelled")
            self.assertTrue(Path(str(created["live_agent_config_path"])).exists())
            meeting = json.loads((root / "meetings" / "room-a" / "live_state.json").read_text(encoding="utf-8"))
            self.assertEqual(meeting["agent_bindings"], [])
            self.assertEqual(meeting["roles"], [])
            self.assertEqual(meeting["provider_configs"], {})
            agents = read_live_agents(root)
            self.assertEqual(agents[0]["agent_id"], created["agent"]["agent_id"])
            self.assertEqual(agents[0]["meeting_id"], "")

    def test_expel_native_remote_invite_participant_revokes_session_without_room_binding(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "aa"
            write_meeting(root)
            invite = create_room_invite(
                room_url="http://192.168.1.10:8765",
                meeting_id="room-a",
                agent_id="remote-runner",
                display_name="Remote Runner",
                max_uses=1,
            )
            joined = join_room_with_invite(
                str(invite["invite_token"]),
                meeting_id="room-a",
                participant_type="agent",
            )
            session_token = str(joined["session_token"])
            connect_live_agent(
                root,
                {
                    "agent_id": joined["agent_id"],
                    "display_name": joined["display_name"],
                    "provider_kind": "manual",
                    "connection_kind": NATIVE_REMOTE_ROOM_CLIENT_KIND,
                    "meeting_id": joined["meeting_id"],
                    "status": "online",
                },
            )

            result = expel_live_agent_from_room_payload(
                root,
                AdminSupervisor(),
                {
                    "meeting_id": "room-a",
                    "agent_id": joined["agent_id"],
                },
            )

            self.assertEqual(result["status"], "expelled")
            self.assertEqual(result["removed"]["binding_count"], 0)
            self.assertEqual(result["revoked_sessions"], 1)
            self.assertIsNone(verify_session_token(session_token))
            self.assertEqual(read_live_agents(root), [])

    def test_delete_removes_room_binding_live_agent_record_config_and_process_record(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "aa"
            workspace = Path(temp_dir) / "project"
            workspace.mkdir()
            write_meeting(root)
            supervisor = AdminSupervisor()
            created = frontend_live_agent_create_payload(
                root,
                supervisor,
                {
                    "meeting_id": "room-a",
                    "provider_id": "grok",
                    "display_name": "Grok To Delete",
                    "workspace_path": str(workspace),
                    "start_now": False,
                },
                default_server="http://127.0.0.1:8765",
            )
            supervisor.groups.append(
                {
                    "group_id": created["group_id"],
                    "status": "stopped",
                    "meeting_id": "room-a",
                    "config_path": created["live_agent_config_path"],
                    "agents": [{"agent_id": created["agent"]["agent_id"]}],
                }
            )

            result = delete_live_agent_session_payload(
                root,
                supervisor,
                {
                    "meeting_id": "room-a",
                    "agent_id": created["agent"]["agent_id"],
                    "group_id": created["group_id"],
                },
            )

            self.assertEqual(result["status"], "deleted")
            self.assertFalse(Path(str(created["live_agent_config_path"])).exists())
            self.assertEqual(read_live_agents(root), [])
            meeting = json.loads((root / "meetings" / "room-a" / "live_state.json").read_text(encoding="utf-8"))
            self.assertEqual(meeting["agent_bindings"], [])
            self.assertEqual(supervisor.deleted, [created["group_id"]])

    def test_delete_session_service_records_success_and_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            calls: list[dict[str, object]] = []

            def delete_command(output_root, supervisor, payload):
                calls.append(payload)
                if payload["agent_id"] == "bad-agent":
                    raise ValueError("not owned")
                return {
                    "status": "deleted",
                    "meeting_id": payload["meeting_id"],
                    "agent_id": payload["agent_id"],
                }

            service = LegacyLiveAgentRoomSessionService(
                root,
                AdminSupervisor(),
                delete_command=delete_command,
            )

            result = service.delete({"meeting_id": "room-a", "agent_id": "agent-a"})
            with self.assertRaisesRegex(ValueError, "not owned"):
                service.delete({"meeting_id": "room-a", "agent_id": "bad-agent"})

            self.assertEqual(result["status"], "deleted")
            self.assertEqual(len(calls), 2)
            operations = read_live_agent_operations(root, operation="frontend_agent.delete_session")
            by_agent = {str(item["target_id"]): item for item in operations}
            self.assertEqual(by_agent["agent-a"]["status"], "success")
            self.assertEqual(by_agent["bad-agent"]["status"], "failed")


if __name__ == "__main__":
    unittest.main()
