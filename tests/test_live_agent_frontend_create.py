import json
import tempfile
import unittest
from pathlib import Path
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from agentsassemble.gui import _make_handler
from agentsassemble.persistence.local.identity.registry import (
    identity_store_for_output_root,
    reset_identity_store_registry,
)
from agentsassemble.legacy.live_agent.runtime.frontend_create import (
    ensure_frontend_meeting,
    frontend_live_agent_check_payload,
    frontend_live_agent_create_payload,
    frontend_live_agent_login_payload,
    frontend_live_agent_options_payload,
)
from agentsassemble.legacy.live_agent.runtime.operations import read_live_agent_operations
from agentsassemble.legacy.live_agent.runtime.sessions import resume_live_agent_session_agent
from agentsassemble.legacy.live_agent.state import connect_live_agent, read_live_agents
from agentsassemble.application.room_users import (
    configure_room_users_store,
    reset_state as reset_room_users_state,
)


class FakeSupervisor:
    def __init__(self) -> None:
        self.started: list[dict[str, object]] = []

    def start_group(self, **kwargs):
        self.started.append(kwargs)
        config_path = Path(str(kwargs["config_path"]))
        config = json.loads(config_path.read_text(encoding="utf-8"))
        return {
            "group_id": kwargs.get("group_id") or config_path.stem,
            "status": "running",
            "meeting_id": kwargs.get("meeting_id", ""),
            "config_path": str(config_path),
            "agents": config["agents"],
        }

    def list_groups(self):
        return [
            {
                "group_id": call.get("group_id") or Path(str(call["config_path"])).stem,
                "status": "running",
                "meeting_id": call.get("meeting_id", ""),
                "config_path": str(call["config_path"]),
            }
            for call in self.started
        ]


def write_meeting(output_root: Path, meeting_id: str = "room-a") -> Path:
    meeting_dir = output_root / "meetings" / meeting_id
    meeting_dir.mkdir(parents=True)
    meeting = {
        "meeting_id": meeting_id,
        "question": "Room question",
        "display_question": "Room question",
        "topic": "Room topic",
        "display_topic": "Room topic",
        "roles": [],
        "agent_bindings": [],
        "provider_configs": {},
        "permission_profiles": {},
        "live_status": "running",
    }
    (meeting_dir / "live_state.json").write_text(json.dumps(meeting), encoding="utf-8")
    return meeting_dir


class FrontendLiveAgentCreateTests(unittest.TestCase):
    def tearDown(self):
        reset_room_users_state()
        reset_identity_store_registry()

    def test_options_include_codex_claude_cursor_grok_antigravity_and_local_with_default_workspace(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            payload = frontend_live_agent_options_payload(default_workspace=Path(temp_dir))

        provider_ids = [provider["id"] for provider in payload["providers"]]
        self.assertEqual(provider_ids, ["codex", "claude", "cursor", "grok", "antigravity", "local"])
        self.assertEqual(payload["default_workspace"], temp_dir)
        self.assertEqual(payload["providers"][0]["provider_kind"], "codex_live_session")
        self.assertEqual(payload["providers"][1]["provider_kind"], "claude_code")
        self.assertEqual(payload["providers"][4]["provider_kind"], "antigravity_live_session")
        self.assertFalse(payload["providers"][-1]["startable"])
        self.assertTrue(payload["providers"][0]["login_available"])
        self.assertEqual(payload["providers"][0]["login_label"], "Codex 로그인 열기")
        self.assertFalse(payload["providers"][-1]["login_available"])
        codex = payload["providers"][0]
        self.assertIn("gpt-5.3-codex-spark", [option["id"] for option in codex["model_options"]])
        self.assertNotIn("gpt-5.3-spark", [option["id"] for option in codex["model_options"]])
        self.assertIn("medium", [option["id"] for option in codex["effort_options"]])
        self.assertEqual(
            [option["id"] for option in codex["speed_options"]],
            ["balanced", "fast", "slow"],
        )
        claude = payload["providers"][1]
        self.assertIn("haiku", [option["id"] for option in claude["model_options"]])
        self.assertIn("xhigh", [option["id"] for option in claude["effort_options"]])
        antigravity = payload["providers"][4]
        self.assertIn("Gemini 3.5 Flash (Medium)", [option["id"] for option in antigravity["model_options"]])
        self.assertEqual(antigravity["effort_options"], [])

    def test_create_adds_room_binding_and_writes_per_agent_config_with_workspace(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "aa"
            workspace = Path(temp_dir) / "project"
            workspace.mkdir()
            write_meeting(root)

            result = frontend_live_agent_create_payload(
                root,
                FakeSupervisor(),
                {
                    "meeting_id": "room-a",
                    "provider_id": "cursor",
                    "display_name": "Cursor Planner",
                    "workspace_path": str(workspace),
                    "start_now": False,
                },
                default_server="http://127.0.0.1:8765",
            )

            self.assertEqual(result["status"], "created")
            self.assertEqual(result["agent"]["display_name"], "Cursor Planner")
            self.assertEqual(result["agent"]["provider_kind"], "cursor_live_session")
            config_path = Path(str(result["live_agent_config_path"]))
            config = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(config["agents"][0]["workspace_path"], str(workspace.resolve()))
            self.assertEqual(config["agents"][0]["meeting_id"], "room-a")
            self.assertEqual(config["agents"][0]["provider_kind"], "cursor_live_session")
            self.assertEqual(config.get("transport"), "ws")
            self.assertTrue(str(config["agents"][0].get("invite_token") or "").startswith("aai1."))

            meeting = json.loads((root / "meetings" / "room-a" / "live_state.json").read_text(encoding="utf-8"))
            binding = meeting["agent_bindings"][0]
            self.assertEqual(binding["agent_id"], result["agent"]["agent_id"])
            self.assertEqual(meeting["roles"][0]["display_name"], "Cursor Planner")
            provider = meeting["provider_configs"][binding["provider_id"]]
            self.assertEqual(provider["kind"], "cursor_live_session")
            self.assertEqual(provider["workspace_path"], str(workspace.resolve()))

            live_agents = json.loads((root / "live_agents.json").read_text(encoding="utf-8"))["agents"]
            self.assertEqual(live_agents[0]["agent_id"], result["agent"]["agent_id"])
            self.assertEqual(live_agents[0]["status"], "offline")
            self.assertEqual(live_agents[0]["process_group_id"], result["group_id"])
            self.assertEqual(live_agents[0]["live_agent_config_path"], result["live_agent_config_path"])

    def test_create_persists_reply_char_limit_into_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "aa"
            workspace = Path(temp_dir) / "project"
            workspace.mkdir()
            write_meeting(root)
            result = frontend_live_agent_create_payload(
                root,
                FakeSupervisor(),
                {
                    "meeting_id": "room-a",
                    "provider_id": "cursor",
                    "display_name": "Verbose Agent",
                    "workspace_path": str(workspace),
                    "reply_char_limit": 250,
                    "start_now": False,
                },
                default_server="http://127.0.0.1:8765",
            )
            config = json.loads(Path(str(result["live_agent_config_path"])).read_text(encoding="utf-8"))
            self.assertEqual(config["agents"][0]["reply_char_limit"], 250)

    def test_create_persists_fast_mode_into_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "aa"
            workspace = Path(temp_dir) / "project"
            workspace.mkdir()
            write_meeting(root)
            result = frontend_live_agent_create_payload(
                root,
                FakeSupervisor(),
                {
                    "meeting_id": "room-a",
                    "provider_id": "codex",
                    "display_name": "Fast Codex",
                    "workspace_path": str(workspace),
                    "fast_mode": True,
                    "start_now": False,
                },
                default_server="http://127.0.0.1:8765",
            )
            config = json.loads(Path(str(result["live_agent_config_path"])).read_text(encoding="utf-8"))
            self.assertIs(config["agents"][0]["fast_mode"], True)

    def test_create_omits_fast_mode_when_off(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "aa"
            workspace = Path(temp_dir) / "project"
            workspace.mkdir()
            write_meeting(root)
            result = frontend_live_agent_create_payload(
                root,
                FakeSupervisor(),
                {
                    "meeting_id": "room-a",
                    "provider_id": "codex",
                    "display_name": "Normal Codex",
                    "workspace_path": str(workspace),
                    "start_now": False,
                },
                default_server="http://127.0.0.1:8765",
            )
            config = json.loads(Path(str(result["live_agent_config_path"])).read_text(encoding="utf-8"))
            self.assertNotIn("fast_mode", config["agents"][0])

    def test_create_materializes_missing_meeting_for_localstorage_room(self):
        # A UI room (localStorage) has no server meeting yet; adding an agent
        # must auto-create it instead of failing with "Meeting <id> was not found".
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "aa"
            workspace = Path(temp_dir) / "project"
            workspace.mkdir()
            self.assertFalse((root / "meetings" / "resident-m1").exists())

            result = frontend_live_agent_create_payload(
                root,
                FakeSupervisor(),
                {
                    "meeting_id": "resident-m1",
                    "provider_id": "cursor",
                    "display_name": "Cursor Planner",
                    "workspace_path": str(workspace),
                    "room_label": "상주방 m1",
                    "start_now": False,
                },
                default_server="http://127.0.0.1:8765",
            )

            self.assertEqual(result["status"], "created")
            self.assertEqual(result["meeting_id"], "resident-m1")
            meeting = json.loads((root / "meetings" / "resident-m1" / "live_state.json").read_text(encoding="utf-8"))
            self.assertEqual(meeting["meeting_id"], "resident-m1")
            self.assertEqual(meeting["topic"], "상주방 m1")
            self.assertEqual(meeting["origin"], "frontend_room")
            self.assertEqual(meeting["agent_bindings"][0]["agent_id"], result["agent"]["agent_id"])

    def test_ensure_frontend_meeting_is_idempotent_and_preserves_existing_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "aa"
            configure_room_users_store(root / "identity.db")
            first = ensure_frontend_meeting(root, "work-room", label="작업방")
            self.assertTrue((first / "live_state.json").exists())
            # Mutate the meeting, then re-ensure: must not overwrite existing state.
            state = json.loads((first / "live_state.json").read_text(encoding="utf-8"))
            state["roles"] = [{"id": "keep-me"}]
            (first / "live_state.json").write_text(json.dumps(state), encoding="utf-8")
            second = ensure_frontend_meeting(root, "work-room", label="다른라벨")
            self.assertEqual(first, second)
            reread = json.loads((second / "live_state.json").read_text(encoding="utf-8"))
            self.assertEqual(reread["roles"], [{"id": "keep-me"}])
            self.assertEqual(reread["topic"], "작업방")
            rooms = identity_store_for_output_root(root).list_rooms()
            self.assertEqual([room["room_id"] for room in rooms], ["work-room"])

    def test_ensure_frontend_meeting_writes_room_registry_owner_and_label(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "aa"
            configure_room_users_store(root / "identity.db")
            ensure_frontend_meeting(root, "db-room", label="DB 방", owner_id="owner-1")
            ensure_frontend_meeting(root, "db-room", label="다른 이름", owner_id="owner-1")

            rooms = identity_store_for_output_root(root).list_rooms(include_archived=True)
            self.assertEqual(len(rooms), 1)
            self.assertEqual(rooms[0]["room_id"], "db-room")
            self.assertEqual(rooms[0]["owner_id"], "owner-1")
            self.assertEqual(rooms[0]["label"], "다른 이름")
            self.assertEqual(rooms[0]["origin"], "frontend_room")

    def test_ensure_frontend_meeting_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "aa"
            with self.assertRaises(ValueError):
                ensure_frontend_meeting(root, "../escape")

    def test_created_but_never_started_agent_can_resume_from_saved_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "aa"
            workspace = Path(temp_dir) / "project"
            workspace.mkdir()
            write_meeting(root)
            supervisor = FakeSupervisor()

            created = frontend_live_agent_create_payload(
                root,
                supervisor,
                {
                    "meeting_id": "room-a",
                    "provider_id": "codex",
                    "display_name": "Codex Waiting",
                    "workspace_path": str(workspace),
                    "start_now": False,
                },
                default_server="http://127.0.0.1:8765",
            )

            self.assertEqual(supervisor.started, [])
            session = resume_live_agent_session_agent(
                root,
                supervisor,
                server="http://127.0.0.1:8765",
                live_agent_config_path=Path(str(created["live_agent_config_path"])),
                meeting_id="room-a",
                group_id=str(created["group_id"]),
                agent_id=str(created["agent"]["agent_id"]),
                connect_timeout_seconds=0,
                preflight_checker=lambda path, **kwargs: {"status": "ok", "config_path": str(path)},
            )

            self.assertEqual(session["agent_id"], created["agent"]["agent_id"])
            self.assertEqual(len(supervisor.started), 1)
            started_config = json.loads(Path(str(supervisor.started[0]["config_path"])).read_text(encoding="utf-8"))
            self.assertEqual([agent["agent_id"] for agent in started_config["agents"]], [created["agent"]["agent_id"]])

    def test_read_live_agents_backfills_legacy_frontend_created_session_registration(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "aa"
            config_dir = root / "live-agent-created"
            config_dir.mkdir(parents=True)
            config_path = config_dir / "codex-legacy-created.json"
            config_path.write_text(
                json.dumps(
                    {
                        "server": "http://127.0.0.1:8765",
                        "agents": [
                            {
                                "agent_id": "codex-legacy-created",
                                "display_name": "Legacy Codex",
                                "provider_kind": "codex_live_session",
                                "connection_kind": "live_session",
                                "workspace_path": str(root / "workspace"),
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            connect_live_agent(
                root,
                {
                    "agent_id": "codex-legacy-created",
                    "display_name": "Legacy Codex",
                    "provider_kind": "codex_live_session",
                    "connection_kind": "live_session",
                    "meeting_id": "room-a",
                    "status": "offline",
                },
            )

            agent = read_live_agents(root)[0]

            self.assertEqual(agent["process_group_id"], "agent-codex-legacy-created")
            self.assertEqual(agent["live_agent_config_path"], str(config_path))
            self.assertEqual(agent["workspace_path"], str(root / "workspace"))

    def test_create_persists_selected_model_effort_and_speed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "aa"
            workspace = Path(temp_dir) / "project"
            workspace.mkdir()
            write_meeting(root)

            result = frontend_live_agent_create_payload(
                root,
                FakeSupervisor(),
                {
                    "meeting_id": "room-a",
                    "provider_id": "codex",
                    "display_name": "Codex Tuned",
                    "workspace_path": str(workspace),
                    "model_id": "gpt-5.4-mini",
                    "effort": "high",
                    "speed": "fast",
                    "start_now": False,
                },
                default_server="http://127.0.0.1:8765",
            )

            config = json.loads(Path(str(result["live_agent_config_path"])).read_text(encoding="utf-8"))
            agent_config = config["agents"][0]
            self.assertEqual(agent_config["model_id"], "gpt-5.4-mini")
            self.assertEqual(agent_config["effort"], "high")
            self.assertEqual(agent_config["speed"], "fast")
            self.assertEqual(agent_config["poll_interval"], 0.1)

            meeting = json.loads((root / "meetings" / "room-a" / "live_state.json").read_text(encoding="utf-8"))
            binding = meeting["agent_bindings"][0]
            provider = meeting["provider_configs"][binding["provider_id"]]
            self.assertEqual(binding["model_id"], "gpt-5.4-mini")
            self.assertEqual(binding["effort"], "high")
            self.assertEqual(binding["speed"], "fast")
            self.assertEqual(provider["default_model"], "gpt-5.4-mini")
            self.assertEqual(provider["effort"], "high")
            self.assertEqual(provider["speed"], "fast")

    def test_create_claude_persists_model_effort_speed_and_terminal_command(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "aa"
            workspace = Path(temp_dir) / "project"
            workspace.mkdir()
            write_meeting(root)

            result = frontend_live_agent_create_payload(
                root,
                FakeSupervisor(),
                {
                    "meeting_id": "room-a",
                    "provider_id": "claude",
                    "display_name": "Claude Haiku",
                    "workspace_path": str(workspace),
                    "model_id": "haiku",
                    "effort": "xhigh",
                    "speed": "fast",
                    "start_now": False,
                },
                default_server="http://127.0.0.1:8765",
            )

            config = json.loads(Path(str(result["live_agent_config_path"])).read_text(encoding="utf-8"))
            agent_config = config["agents"][0]
            self.assertEqual(agent_config["provider_kind"], "claude_code")
            self.assertEqual(agent_config["connection_kind"], "terminal_session")
            self.assertEqual(agent_config["command"], ["claude", "--model", "haiku", "--effort", "xhigh"])
            self.assertEqual(agent_config["model_id"], "haiku")
            self.assertEqual(agent_config["effort"], "xhigh")
            self.assertEqual(agent_config["speed"], "fast")
            self.assertEqual(agent_config["workspace_path"], str(workspace.resolve()))

    def test_create_antigravity_persists_model_and_speed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "aa"
            workspace = Path(temp_dir) / "project"
            workspace.mkdir()
            write_meeting(root)

            result = frontend_live_agent_create_payload(
                root,
                FakeSupervisor(),
                {
                    "meeting_id": "room-a",
                    "provider_id": "antigravity",
                    "display_name": "Antigravity Flash",
                    "workspace_path": str(workspace),
                    "model_id": "Gemini 3.5 Flash (Medium)",
                    "speed": "fast",
                    "start_now": False,
                },
                default_server="http://127.0.0.1:8765",
            )

            config = json.loads(Path(str(result["live_agent_config_path"])).read_text(encoding="utf-8"))
            agent_config = config["agents"][0]
            self.assertEqual(agent_config["provider_kind"], "antigravity_live_session")
            self.assertEqual(agent_config["model_id"], "Gemini 3.5 Flash (Medium)")
            self.assertEqual(agent_config["speed"], "fast")

    def test_create_rejects_unsupported_tuning_values(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "aa"
            workspace = Path(temp_dir) / "project"
            workspace.mkdir()
            write_meeting(root)

            with self.assertRaisesRegex(ValueError, "Unsupported model"):
                frontend_live_agent_create_payload(
                    root,
                    FakeSupervisor(),
                    {
                        "meeting_id": "room-a",
                        "provider_id": "codex",
                        "display_name": "Bad Model",
                        "workspace_path": str(workspace),
                        "model_id": "not-a-model",
                        "start_now": False,
                    },
                    default_server="http://127.0.0.1:8765",
                )

            with self.assertRaisesRegex(ValueError, "Unsupported effort"):
                frontend_live_agent_create_payload(
                    root,
                    FakeSupervisor(),
                    {
                        "meeting_id": "room-a",
                        "provider_id": "codex",
                        "display_name": "Bad Effort",
                        "workspace_path": str(workspace),
                        "effort": "maximum",
                        "start_now": False,
                    },
                    default_server="http://127.0.0.1:8765",
                )

            with self.assertRaisesRegex(ValueError, "Unsupported speed"):
                frontend_live_agent_create_payload(
                    root,
                    FakeSupervisor(),
                    {
                        "meeting_id": "room-a",
                        "provider_id": "codex",
                        "display_name": "Bad Speed",
                        "workspace_path": str(workspace),
                        "speed": "instant",
                        "start_now": False,
                    },
                    default_server="http://127.0.0.1:8765",
                )

    def test_check_payload_surfaces_preflight_failure_message(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "aa"
            workspace = Path(temp_dir) / "project"
            workspace.mkdir()
            message = (
                "Antigravity 로그인이 필요합니다. "
                "터미널에서 agy를 실행해 로그인한 뒤 다시 연결 확인을 누르세요."
            )

            result = frontend_live_agent_check_payload(
                root,
                {
                    "meeting_id": "room-a",
                    "provider_id": "antigravity",
                    "display_name": "Antigravity",
                    "workspace_path": str(workspace),
                },
                default_server="http://127.0.0.1:8765",
                preflight_checker=lambda path, **kwargs: {
                    "status": "failed",
                    "agents": [
                        {
                            "checks": [
                                {
                                    "id": "antigravity_auth",
                                    "status": "failed",
                                    "message": message,
                                }
                            ]
                        }
                    ],
                },
            )

            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["message"], message)
            self.assertEqual(
                result["auth_action"],
                {"provider_id": "antigravity", "label": "Antigravity 로그인 열기"},
            )

    def test_login_payload_launches_only_configured_provider_login_command(self):
        calls = []

        result = frontend_live_agent_login_payload(
            {"provider_id": "cursor"},
            command_resolver=lambda command: f"/usr/local/bin/{command}" if command == "cursor-agent" else None,
            command_launcher=lambda command: calls.append(command),
        )

        self.assertEqual(result["status"], "started")
        self.assertEqual(result["provider_id"], "cursor")
        self.assertEqual(calls, [["/usr/local/bin/cursor-agent", "login"]])

    def test_login_payload_rejects_unknown_or_non_login_provider(self):
        with self.assertRaisesRegex(ValueError, "Unknown agent provider"):
            frontend_live_agent_login_payload({"provider_id": "unknown"})

        with self.assertRaisesRegex(ValueError, "does not support local login"):
            frontend_live_agent_login_payload({"provider_id": "local"})

    def test_create_start_now_invokes_supervisor_with_single_agent_group(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "aa"
            workspace = Path(temp_dir) / "project"
            workspace.mkdir()
            write_meeting(root)
            supervisor = FakeSupervisor()

            result = frontend_live_agent_create_payload(
                root,
                supervisor,
                {
                    "meeting_id": "room-a",
                    "provider_id": "grok",
                    "display_name": "Grok Reviewer",
                    "workspace_path": str(workspace),
                    "start_now": True,
                },
                default_server="http://127.0.0.1:8765",
                preflight_checker=lambda path, **kwargs: {"status": "ok", "config_path": str(path)},
            )

            self.assertEqual(result["status"], "starting")
            self.assertEqual(len(supervisor.started), 1)
            started = supervisor.started[0]
            self.assertEqual(started["meeting_id"], "room-a")
            self.assertTrue(str(started["group_id"]).startswith("agent-"))
            started_config = json.loads(Path(str(started["config_path"])).read_text(encoding="utf-8"))
            self.assertEqual([agent["agent_id"] for agent in started_config["agents"]], [result["agent"]["agent_id"]])
            self.assertEqual(result["group"]["status"], "running")

    def test_create_start_now_preflight_failure_does_not_register_agent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "aa"
            workspace = Path(temp_dir) / "project"
            workspace.mkdir()
            write_meeting(root)
            supervisor = FakeSupervisor()

            with self.assertRaisesRegex(ValueError, "command missing"):
                frontend_live_agent_create_payload(
                    root,
                    supervisor,
                    {
                        "meeting_id": "room-a",
                        "provider_id": "grok",
                        "display_name": "Broken Grok",
                        "workspace_path": str(workspace),
                        "start_now": True,
                    },
                    default_server="http://127.0.0.1:8765",
                    preflight_checker=lambda path, **kwargs: {
                        "status": "failed",
                        "agents": [
                            {
                                "checks": [
                                    {
                                        "id": "command",
                                        "status": "failed",
                                        "message": "command missing",
                                    }
                                ]
                            }
                        ],
                    },
                )

            self.assertEqual(supervisor.started, [])
            meeting = json.loads((root / "meetings" / "room-a" / "live_state.json").read_text(encoding="utf-8"))
            self.assertEqual(meeting["agent_bindings"], [])
            self.assertFalse((root / "live_agents.json").exists())

    def test_create_rejects_local_start_until_local_runtime_is_configured(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "aa"
            workspace = Path(temp_dir) / "project"
            workspace.mkdir()
            write_meeting(root)

            with self.assertRaisesRegex(ValueError, "Local runtime"):
                frontend_live_agent_create_payload(
                    root,
                    FakeSupervisor(),
                    {
                        "meeting_id": "room-a",
                        "provider_id": "local",
                        "display_name": "Local Llama",
                        "workspace_path": str(workspace),
                        "start_now": True,
                    },
                    default_server="http://127.0.0.1:8765",
                )

    def test_http_login_endpoint_is_local_operator_only_and_uses_injected_launcher(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "aa"
            launched = []
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                _make_handler(
                    root,
                    process_supervisor=FakeSupervisor(),
                    live_agent_login_launcher=lambda command: launched.append(command),
                    live_agent_login_command_resolver=lambda command: f"/usr/local/bin/{command}",
                ),
            )
            thread = __import__("threading").Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                server_url = f"http://127.0.0.1:{server.server_port}"
                request = Request(
                    f"{server_url}/api/live-agent-create/login",
                    data=json.dumps({"provider_id": "grok"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=4) as response:
                    login = json.loads(response.read().decode("utf-8"))

                self.assertEqual(login["status"], "started")
                self.assertEqual(launched, [["/usr/local/bin/grok", "login"]])
                operation = read_live_agent_operations(root, operation="frontend_agent.login")[0]
                self.assertEqual(operation["status"], "success")
                self.assertEqual(operation["target_id"], "grok")

                unsupported = Request(
                    f"{server_url}/api/live-agent-create/login",
                    data=json.dumps({"provider_id": "unknown"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as raised:
                    urlopen(unsupported, timeout=4)
                self.assertEqual(raised.exception.code, 400)
                raised.exception.close()
                failed_operation = next(
                    item
                    for item in read_live_agent_operations(root, operation="frontend_agent.login")
                    if item["status"] == "failed"
                )
                self.assertEqual(failed_operation["target_id"], "unknown")
                self.assertEqual(launched, [["/usr/local/bin/grok", "login"]])

                blocked = Request(
                    f"{server_url}/api/live-agent-create/login",
                    data=json.dumps({"provider_id": "codex"}).encode("utf-8"),
                    headers={"Content-Type": "application/json", "Host": "evil.example.com"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as raised:
                    urlopen(blocked, timeout=4)
                self.assertEqual(raised.exception.code, 403)
                raised.exception.close()
            finally:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
