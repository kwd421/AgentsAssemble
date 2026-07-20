import json
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from agentsassemble.config import (
    agent_bindings_from_config,
    load_agent_runtime_config,
    load_council_config,
    permissions_from_config,
    providers_from_config,
)
from agentsassemble.legacy.live_agent.runtime.meetings import start_live_agent_meeting
from agentsassemble.live_agent_runner import load_group_configs
from agentsassemble.legacy.live_agent.runtime.sessions import (
    check_live_agent_session,
    live_agent_session_readiness_summary,
    recover_live_agent_session,
    resume_live_agent_session,
    restart_live_agent_session,
    start_live_agent_session,
    stop_live_agent_session,
    session_ensure_action,
)
from agentsassemble.legacy.live_agent.state import connect_live_agent, heartbeat_live_agent, read_live_agents
from agentsassemble.legacy.meeting.core.events import write_live_state


class FakeSessionSupervisor:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.started = []

    def start_group(self, **kwargs):
        self.started.append(kwargs)
        return {
            "group_id": kwargs.get("group_id") or "resident-group",
            "status": "running",
            "agents": [
                {"agent_id": "agent-a", "display_name": "Agent A", "provider_kind": "local_cli", "connection_kind": "local_cli"}
            ],
        }


class LiveAgentSessionEnsureActionTests(unittest.TestCase):
    def test_session_ensure_action_uses_one_policy_for_cli_and_api_surfaces(self):
        cases = [
            ("start", None),
            ("none", {"status": "ready", "group": {}, "process": {"status": "unknown"}}),
            ("resume", {"status": "degraded", "group": {}, "process": {"status": "unknown"}}),
            ("resume", {"status": "degraded", "group": {"group_id": "resident-main"}, "process": {"status": "running"}}),
            ("resume", {"status": "degraded", "group": {"group_id": "resident-main"}, "process": {"status": "restarting"}}),
            ("restart", {"status": "degraded", "group": {"group_id": "resident-main"}, "process": {"status": "stopped"}}),
            ("restart", {"status": "degraded", "group": {"group_id": "resident-main"}, "process_status": "stopped"}),
            ("recover", {"status": "degraded", "group": {"group_id": "resident-main"}, "process": {"status": "error"}}),
            ("recover", {"status": "degraded", "group": {"group_id": "resident-main"}, "process": {"status": "unknown"}}),
            ("recover", {"status": "degraded", "group": {"group_id": "resident-main"}}),
        ]

        for expected_action, readiness in cases:
            with self.subTest(expected_action=expected_action, readiness=readiness):
                self.assertEqual(session_ensure_action(readiness), expected_action)


class LiveAgentSessionReadinessSummaryTests(unittest.TestCase):
    def test_start_session_merges_live_agent_persona_config_into_character_snapshot(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            persona_dir = root / "personas" / "tsukishiro-yanagi"
            persona_dir.mkdir(parents=True)
            (persona_dir / "card.json").write_text(
                json.dumps({"id": "tsukishiro-yanagi", "display_name": "Tsukishiro Yanagi"}, ensure_ascii=False),
                encoding="utf-8",
            )
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            live_agent_config = _write_live_agent_config(
                root,
                ["agent-a"],
                persona_card_id="tsukishiro-yanagi",
                character_mode="on",
            )
            supervisor = FakeSessionSupervisor(root)

            result = start_live_agent_session(
                root,
                supervisor,
                server="http://127.0.0.1:8765",
                council_config_path=council_config,
                agent_config_path=agent_config,
                live_agent_config_path=live_agent_config,
                meeting_id="resident-m1",
                group_id="resident-main",
            )

            meeting = json.loads((root / "meetings" / result["meeting_id"] / "live_state.json").read_text(encoding="utf-8"))
            character_agents = meeting["character_mode"]["agents"]
            self.assertEqual(character_agents[0]["agent_id"], "agent-a")
            self.assertEqual(character_agents[0]["card_id"], "tsukishiro-yanagi")
            self.assertTrue(character_agents[0]["card_hash"].startswith("sha256:"))
            self.assertEqual(meeting["agent_bindings"][0]["persona_card_id"], "tsukishiro-yanagi")

    def test_start_session_snapshots_live_agent_persona_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            persona_dir = root / "external-personas"
            persona_dir.mkdir()
            persona_path = persona_dir / "card.json"
            persona_path.write_text(
                json.dumps({"id": "external-yanagi", "display_name": "External Yanagi"}, ensure_ascii=False),
                encoding="utf-8",
            )
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            live_agent_config = _write_live_agent_config(
                root,
                ["agent-a"],
                persona_path=str(persona_path),
                character_mode="on",
            )
            supervisor = FakeSessionSupervisor(root)

            result = start_live_agent_session(
                root,
                supervisor,
                server="http://127.0.0.1:8765",
                council_config_path=council_config,
                agent_config_path=agent_config,
                live_agent_config_path=live_agent_config,
                meeting_id="resident-m1",
                group_id="resident-main",
            )

            meeting = json.loads((root / "meetings" / result["meeting_id"] / "live_state.json").read_text(encoding="utf-8"))
            character_agent = meeting["character_mode"]["agents"][0]
            self.assertEqual(character_agent["card_id"], "external-yanagi")
            self.assertTrue(character_agent["card_hash"].startswith("sha256:"))
            self.assertEqual(character_agent["source_path"], "external-personas/card.json")
            self.assertEqual(meeting["agent_bindings"][0]["persona_card_id"], "external-yanagi")
            self.assertNotIn("persona_card_path", meeting["agent_bindings"][0])

    def test_start_session_live_agent_persona_overrides_agent_config_snapshot(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            agent_card_dir = root / "personas" / "tsukishiro-yanagi"
            live_card_dir = root / "personas" / "resident-override"
            agent_card_dir.mkdir(parents=True)
            live_card_dir.mkdir(parents=True)
            (agent_card_dir / "card.json").write_text(
                json.dumps({"id": "tsukishiro-yanagi", "display_name": "Tsukishiro Yanagi"}, ensure_ascii=False),
                encoding="utf-8",
            )
            (live_card_dir / "card.json").write_text(
                json.dumps({"id": "resident-override", "display_name": "Resident Override"}, ensure_ascii=False),
                encoding="utf-8",
            )
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config_with_character_mode(root)
            live_agent_config = _write_live_agent_config(
                root,
                ["agent-a"],
                persona_card_id="resident-override",
                character_mode="on",
            )
            supervisor = FakeSessionSupervisor(root)

            result = start_live_agent_session(
                root,
                supervisor,
                server="http://127.0.0.1:8765",
                council_config_path=council_config,
                agent_config_path=agent_config,
                live_agent_config_path=live_agent_config,
                meeting_id="resident-m1",
                group_id="resident-main",
            )

            meeting = json.loads((root / "meetings" / result["meeting_id"] / "live_state.json").read_text(encoding="utf-8"))
            character_agent = meeting["character_mode"]["agents"][0]
            self.assertEqual(character_agent["card_id"], "resident-override")
            self.assertEqual(character_agent["mode"], "on")
            self.assertEqual(meeting["agent_bindings"][0]["persona_card_id"], "resident-override")

    def test_start_session_live_agent_character_mode_off_clears_agent_config_persona(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            agent_card_dir = root / "personas" / "tsukishiro-yanagi"
            agent_card_dir.mkdir(parents=True)
            (agent_card_dir / "card.json").write_text(
                json.dumps({"id": "tsukishiro-yanagi", "display_name": "Tsukishiro Yanagi"}, ensure_ascii=False),
                encoding="utf-8",
            )
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config_with_character_mode(root)
            live_agent_config = _write_live_agent_config(root, ["agent-a"], character_mode="off")
            supervisor = FakeSessionSupervisor(root)

            result = start_live_agent_session(
                root,
                supervisor,
                server="http://127.0.0.1:8765",
                council_config_path=council_config,
                agent_config_path=agent_config,
                live_agent_config_path=live_agent_config,
                meeting_id="resident-m1",
                group_id="resident-main",
            )

            meeting = json.loads((root / "meetings" / result["meeting_id"] / "live_state.json").read_text(encoding="utf-8"))
            character_agent = meeting["character_mode"]["agents"][0]
            self.assertEqual(character_agent["card_id"], "")
            self.assertEqual(character_agent["mode"], "off")
            self.assertNotIn("persona_card_id", meeting["agent_bindings"][0])
            self.assertNotIn("character_mode", meeting["agent_bindings"][0])

    def test_start_session_live_agent_mode_only_override_preserves_agent_config_persona(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            agent_card_dir = root / "personas" / "tsukishiro-yanagi"
            agent_card_dir.mkdir(parents=True)
            (agent_card_dir / "card.json").write_text(
                json.dumps({"id": "tsukishiro-yanagi", "display_name": "Tsukishiro Yanagi"}, ensure_ascii=False),
                encoding="utf-8",
            )
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config_with_character_mode(root)
            live_agent_config = _write_live_agent_config(root, ["agent-a"], character_mode="work_speech_only")
            supervisor = FakeSessionSupervisor(root)

            result = start_live_agent_session(
                root,
                supervisor,
                server="http://127.0.0.1:8765",
                council_config_path=council_config,
                agent_config_path=agent_config,
                live_agent_config_path=live_agent_config,
                meeting_id="resident-m1",
                group_id="resident-main",
            )

            meeting = json.loads((root / "meetings" / result["meeting_id"] / "live_state.json").read_text(encoding="utf-8"))
            character_agent = meeting["character_mode"]["agents"][0]
            self.assertEqual(character_agent["card_id"], "tsukishiro-yanagi")
            self.assertEqual(character_agent["mode"], "work_speech_only")
            self.assertTrue(character_agent["card_hash"].startswith("sha256:"))
            self.assertEqual(meeting["agent_bindings"][0]["persona_card_id"], "tsukishiro-yanagi")

    def test_start_session_sanitizes_live_agent_persona_card_id(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            live_agent_config = _write_live_agent_config(
                root,
                ["agent-a"],
                persona_card_id="../secret/card.json",
                character_mode="on",
            )
            supervisor = FakeSessionSupervisor(root)

            result = start_live_agent_session(
                root,
                supervisor,
                server="http://127.0.0.1:8765",
                council_config_path=council_config,
                agent_config_path=agent_config,
                live_agent_config_path=live_agent_config,
                meeting_id="resident-m1",
                group_id="resident-main",
            )

            meeting = json.loads((root / "meetings" / result["meeting_id"] / "live_state.json").read_text(encoding="utf-8"))
            character_agent = meeting["character_mode"]["agents"][0]
            self.assertEqual(character_agent["card_id"], "secret-card.json")
            self.assertEqual(meeting["agent_bindings"][0]["persona_card_id"], "secret-card.json")
            self.assertNotIn("..", json.dumps(meeting["agent_bindings"], ensure_ascii=False))

    def test_start_session_hides_persona_path_outside_output_root(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            root = temp_root / "room"
            outside = temp_root / "outside"
            root.mkdir()
            outside.mkdir()
            persona_path = outside / "card.json"
            persona_path.write_text(
                json.dumps({"id": "outside-card", "display_name": "Outside Card"}, ensure_ascii=False),
                encoding="utf-8",
            )
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            live_agent_config = _write_live_agent_config(
                root,
                ["agent-a"],
                persona_path=str(root / "../outside/card.json"),
                character_mode="on",
            )
            supervisor = FakeSessionSupervisor(root)

            result = start_live_agent_session(
                root,
                supervisor,
                server="http://127.0.0.1:8765",
                council_config_path=council_config,
                agent_config_path=agent_config,
                live_agent_config_path=live_agent_config,
                meeting_id="resident-m1",
                group_id="resident-main",
            )

            meeting = json.loads((root / "meetings" / result["meeting_id"] / "live_state.json").read_text(encoding="utf-8"))
            character_agent = meeting["character_mode"]["agents"][0]
            self.assertEqual(character_agent["card_id"], "outside-card")
            self.assertTrue(character_agent["card_hash"].startswith("sha256:"))
            self.assertEqual(character_agent["source_path"], "")

    def test_live_agent_meeting_snapshots_character_mode_and_roster_badge(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            persona_dir = root / "personas" / "tsukishiro-yanagi"
            persona_dir.mkdir(parents=True)
            (persona_dir / "card.json").write_text(
                json.dumps(
                    {
                        "id": "tsukishiro-yanagi",
                        "display_name": "Tsukishiro Yanagi",
                        "ignored_features": {"trigger": 1},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config_with_character_mode(root)

            result = start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )

        meeting = result["meeting"]
        character_agents = meeting["character_mode"]["agents"]
        self.assertEqual(character_agents[0]["agent_id"], "agent-a")
        self.assertEqual(character_agents[0]["card_id"], "tsukishiro-yanagi")
        self.assertEqual(character_agents[0]["mode"], "work_speech_only")
        self.assertTrue(character_agents[0]["card_hash"].startswith("sha256:"))
        self.assertEqual(character_agents[0]["ignored_features"], {"trigger": 1})
        self.assertEqual(character_agents[0]["source_path"], "personas/tsukishiro-yanagi/card.json")
        self.assertEqual(character_agents[0]["persona_variables"], {"mood": "dry"})
        self.assertEqual(meeting["agent_bindings"][0]["character_mode"], "work_speech_only")
        roster_agent = next(agent for agent in result["agents"] if agent["agent_id"] == "agent-a")
        self.assertEqual(roster_agent["character_mode"], "work_speech_only")
        self.assertEqual(roster_agent["persona_card_id"], "tsukishiro-yanagi")

    def test_session_summary_degrades_duplicate_active_meeting_groups(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")

            summary = live_agent_session_readiness_summary(
                root,
                [
                    {
                        "group_id": "resident-main",
                        "status": "running",
                        "meeting_id": "resident-m1",
                        "agents": [{"agent_id": "agent-a"}],
                    },
                    {
                        "group_id": "resident-shadow",
                        "status": "restarting",
                        "meeting_id": "resident-m1",
                        "agents": [{"agent_id": "agent-a"}],
                    },
                    {
                        "group_id": "resident-stopped",
                        "status": "stopped",
                        "meeting_id": "resident-m1",
                        "agents": [{"agent_id": "agent-a"}],
                    },
                ],
            )

        self.assertEqual(summary["ready"], 0)
        self.assertEqual(summary["degraded"], 3)
        items_by_group = {item["group_id"]: item for item in summary["items"]}
        self.assertEqual(items_by_group["resident-main"]["ownership_attention"], ["meeting:duplicate_active_group"])
        self.assertEqual(items_by_group["resident-shadow"]["ownership_attention"], ["meeting:duplicate_active_group"])
        self.assertEqual(items_by_group["resident-stopped"]["ownership_attention"], [])

    def test_session_summary_ignores_diagnostic_groups_for_duplicate_ownership(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")

            summary = live_agent_session_readiness_summary(
                root,
                [
                    {
                        "group_id": "resident-main",
                        "status": "running",
                        "meeting_id": "resident-m1",
                        "agents": [{"agent_id": "agent-a"}],
                    },
                    {
                        "group_id": "resident-diagnostic",
                        "status": "running",
                        "meeting_id": "resident-m1",
                        "diagnostic": True,
                        "agents": [{"agent_id": "agent-a"}],
                    },
                ],
            )

        self.assertEqual(summary["ready"], 1)
        self.assertEqual(summary["degraded"], 0)
        self.assertEqual(summary["attention"], [])
        self.assertEqual([item["group_id"] for item in summary["items"]], ["resident-main"])
        self.assertEqual(summary["items"][0]["ownership_attention"], [])

    def test_session_summary_does_not_count_provider_mismatch_presence_as_connected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"], provider_kind="local_cli")
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            connect_live_agent(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Manual Agent A",
                    "provider_kind": "manual",
                    "connection_kind": "manual",
                    "meeting_id": "resident-m1",
                    "status": "online",
                },
            )

            summary = live_agent_session_readiness_summary(
                root,
                [
                    {
                        "group_id": "resident-main",
                        "status": "running",
                        "meeting_id": "resident-m1",
                        "agents": [
                            {
                                "agent_id": "agent-a",
                                "provider_kind": "local_cli",
                                "connection_kind": "local_cli",
                            }
                        ],
                    }
                ],
            )

        self.assertEqual(summary["ready"], 0)
        self.assertEqual(summary["degraded"], 1)
        self.assertEqual(summary["items"][0]["connected"], 0)
        self.assertEqual(summary["items"][0]["connection_attention"], ["agent-a:provider_kind_mismatch"])

    def test_session_summary_degrades_binding_with_missing_provider_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            result = start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            meeting = dict(result["meeting"])
            meeting["agent_bindings"] = [
                {
                    "role_id": "architect",
                    "agent_id": "agent-a",
                    "provider_id": "missing-provider",
                }
            ]
            meeting["provider_configs"] = {}
            write_live_state(root / "meetings" / "resident-m1", meeting)
            heartbeat_live_agent(root, "agent-a", status="online")

            summary = live_agent_session_readiness_summary(
                root,
                [
                    {
                        "group_id": "resident-main",
                        "status": "running",
                        "meeting_id": "resident-m1",
                        "agents": [
                            {
                                "agent_id": "agent-a",
                                "provider_kind": "local_cli",
                                "connection_kind": "local_cli",
                            }
                        ],
                    }
                ],
            )

        self.assertEqual(summary["total"], 1)
        self.assertEqual(summary["ready"], 0)
        self.assertEqual(summary["degraded"], 1)
        self.assertEqual(summary["attention"], ["resident-m1:resident-main:agent-a:binding_provider_missing"])
        self.assertEqual(summary["items"][0]["expected"], 1)
        self.assertEqual(summary["items"][0]["connected"], 0)
        self.assertEqual(summary["items"][0]["connection_attention"], ["agent-a:binding_provider_missing"])
        payload_blob = json.dumps(summary, ensure_ascii=False)
        self.assertNotIn("missing-provider", payload_blob)

    def test_check_session_degrades_duplicate_active_meeting_group(self):
        class DuplicateSupervisor:
            def snapshot_groups(self):
                return [
                    {
                        "group_id": "resident-main",
                        "status": "running",
                        "meeting_id": "resident-m1",
                        "agents": [{"agent_id": "agent-a"}],
                    },
                    {
                        "group_id": "resident-shadow",
                        "status": "running",
                        "meeting_id": "resident-m1",
                        "agents": [{"agent_id": "agent-a"}],
                    },
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")

            session = check_live_agent_session(
                root,
                DuplicateSupervisor(),
                meeting_id="resident-m1",
                group_id="resident-main",
            )

        self.assertEqual(session["status"], "degraded")
        self.assertEqual(session["ownership"]["attention"], ["meeting:duplicate_active_group"])

    def test_check_session_degrades_binding_with_missing_provider_config(self):
        class CheckSupervisor:
            def snapshot_groups(self):
                return [
                    {
                        "group_id": "resident-main",
                        "status": "running",
                        "meeting_id": "resident-m1",
                        "agents": [
                            {
                                "agent_id": "agent-a",
                                "provider_kind": "local_cli",
                                "connection_kind": "local_cli",
                            }
                        ],
                    }
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            result = start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            meeting = dict(result["meeting"])
            meeting["agent_bindings"] = [
                {
                    "role_id": "architect",
                    "agent_id": "agent-a",
                    "provider_id": "missing-provider",
                }
            ]
            meeting["provider_configs"] = {}
            write_live_state(root / "meetings" / "resident-m1", meeting)
            heartbeat_live_agent(root, "agent-a", status="online")

            session = check_live_agent_session(
                root,
                CheckSupervisor(),
                meeting_id="resident-m1",
                group_id="resident-main",
            )

        self.assertEqual(session["status"], "degraded")
        self.assertEqual(session["process"]["attention"], [])
        self.assertEqual(session["connection"]["connected"], 0)
        self.assertEqual(session["connection"]["attention"], ["agent-a:binding_provider_missing"])
        self.assertEqual(session["ownership"]["attention"], [])
        payload_blob = json.dumps(session, ensure_ascii=False)
        self.assertNotIn("missing-provider", payload_blob)


class LiveAgentSessionStartTests(unittest.TestCase):
    def test_director_led_self_service_bundle_matches_manifest_without_real_provider_execution(self):
        project_root = Path(__file__).resolve().parents[1]
        council_config = project_root / "configs" / "director-led-team.example.json"
        agent_config = project_root / "configs" / "agents.director-led-team.example.json"
        live_agent_config = project_root / "configs" / "live-agents.director-led-team.self-service.example.json"

        council = load_council_config(council_config)
        runtime = load_agent_runtime_config(agent_config)
        self.assertIsNotNone(runtime)
        runtime_data = runtime or {}
        providers = providers_from_config(runtime_data)
        permissions = permissions_from_config(runtime_data)
        bindings = agent_bindings_from_config(runtime_data)
        resident_configs = load_group_configs(live_agent_config, server_override="http://127.0.0.1:8765")
        expected_roles = ["director", "engineering_lead", "product_lead", "design_lead", "implementer"]
        expected_agents = [
            "opus-director",
            "xhigh-engineering-lead",
            "xhigh-product-lead",
            "xhigh-design-lead",
            "mini-implementer",
        ]

        self.assertEqual([role.id for role in council.roles], expected_roles)
        self.assertEqual(council.meeting_template_id, "director_led_agent_owned_room_v1")
        self.assertEqual([round_definition.id for round_definition in council.rounds], ["room_entry", "director_confirmation"])
        self.assertFalse(council.moderator.enabled)
        self.assertEqual([binding.role_id for binding in bindings], expected_roles)
        self.assertEqual([binding.agent_id for binding in bindings], expected_agents)
        self.assertEqual(set(permissions), {"meeting_readonly"})
        self.assertTrue(all(provider.kind == "local_cli" for provider in providers.values()))
        expected_provider_commands = {
            "opus-director-slot": ["python3", "-c", "print('director slot placeholder')"],
            "xhigh-engineering-lead-slot": ["python3", "-c", "print('engineering lead slot placeholder')"],
            "xhigh-product-lead-slot": ["python3", "-c", "print('product lead slot placeholder')"],
            "xhigh-design-lead-slot": ["python3", "-c", "print('design lead slot placeholder')"],
            "mini-implementer-slot": ["python3", "-c", "print('implementer slot placeholder')"],
        }
        self.assertEqual(
            {provider_id: provider.command for provider_id, provider in providers.items()},
            expected_provider_commands,
        )
        self.assertTrue(all(binding.permission_profile_id == "meeting_readonly" for binding in bindings))
        self.assertTrue(all(binding.engagement_mode == "watch" for binding in bindings))
        self.assertEqual([config.agent_id for config in resident_configs], expected_agents)
        self.assertTrue(all(config.provider_kind == "local_cli" for config in resident_configs))
        self.assertTrue(all(config.connection_kind == "self_service" for config in resident_configs))
        self.assertTrue(all(config.command and config.command[:2] == ["python3", "scripts/my_self_service_agent.py"] for config in resident_configs))

        class DirectorTeamSupervisor:
            def __init__(self) -> None:
                self.started = []

            def start_group(self, **kwargs):
                self.started.append(kwargs)
                return {
                    "group_id": kwargs.get("group_id") or "director-led-team",
                    "status": "running",
                    "meeting_id": kwargs.get("meeting_id") or "",
                    "agents": [
                        {
                            "agent_id": config.agent_id,
                            "display_name": config.display_name,
                            "provider_kind": config.provider_kind,
                            "connection_kind": config.connection_kind,
                        }
                        for config in resident_configs
                    ],
                }

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            supervisor = DirectorTeamSupervisor()
            session = start_live_agent_session(
                root,
                supervisor,
                server="http://127.0.0.1:8765",
                council_config_path=council_config,
                agent_config_path=agent_config,
                live_agent_config_path=live_agent_config,
                meeting_id="director-led-room",
                group_id="director-led-team",
                connect_timeout_seconds=0,
                preflight_checker=lambda *args, **kwargs: {"status": "ok"},
            )

            meeting_path = root / "meetings" / "director-led-room" / "live_state.json"
            meeting = json.loads(meeting_path.read_text(encoding="utf-8"))

        self.assertEqual(session["meeting_id"], "director-led-room")
        self.assertEqual(session["process"]["ready"], True)
        self.assertEqual(session["process"]["matched"], 5)
        self.assertEqual(session["connection"]["expected"], 5)
        self.assertEqual(session["connection"]["connected"], 0)
        self.assertEqual(len(supervisor.started), 1)
        self.assertEqual(supervisor.started[0]["config_path"], live_agent_config)
        self.assertEqual(supervisor.started[0]["meeting_id"], "director-led-room")
        self.assertEqual([role["id"] for role in meeting["roles"]], expected_roles)
        self.assertEqual([binding["agent_id"] for binding in meeting["agent_bindings"]], expected_agents)
        for provider_id in providers:
            self.assertEqual(meeting["provider_configs"][provider_id]["command"], ["<redacted>"])
            self.assertEqual(meeting["provider_configs"][provider_id]["kind"], "local_cli")

    def test_start_session_refuses_manifest_mismatch_before_writing_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect", "critic"])
            agent_config = _write_agent_config(root, ["agent-a", "agent-b"])
            live_agent_config = _write_live_agent_config(root, ["agent-a"])
            supervisor = FakeSessionSupervisor(root)

            with self.assertRaises(ValueError) as raised:
                start_live_agent_session(
                    root,
                    supervisor,
                    server="http://127.0.0.1:8765",
                    council_config_path=council_config,
                    agent_config_path=agent_config,
                    live_agent_config_path=live_agent_config,
                    meeting_id="resident-m1",
                    group_id="resident-main",
                )

            self.assertIn("does not cover meeting agents", str(raised.exception))
            self.assertEqual(supervisor.started, [])
            self.assertFalse((root / "meetings").exists())
            self.assertFalse((root / "live-agent-runs").exists())

    def test_start_session_refuses_extra_manifest_agent_before_writing_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            live_agent_config = _write_live_agent_config(root, ["agent-a", "agent-extra"])
            supervisor = FakeSessionSupervisor(root)

            with self.assertRaises(ValueError) as raised:
                start_live_agent_session(
                    root,
                    supervisor,
                    server="http://127.0.0.1:8765",
                    council_config_path=council_config,
                    agent_config_path=agent_config,
                    live_agent_config_path=live_agent_config,
                    meeting_id="resident-m1",
                    group_id="resident-main",
                )

            self.assertIn("does not match meeting agents", str(raised.exception))
            self.assertEqual(supervisor.started, [])
            self.assertFalse((root / "meetings").exists())

    def test_start_session_refuses_provider_kind_mismatch_before_writing_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"], provider_kind="claude_code")
            live_agent_config = _write_live_agent_config(root, ["agent-a"], provider_kind="local_cli")
            supervisor = FakeSessionSupervisor(root)

            with self.assertRaises(ValueError) as raised:
                start_live_agent_session(
                    root,
                    supervisor,
                    server="http://127.0.0.1:8765",
                    council_config_path=council_config,
                    agent_config_path=agent_config,
                    live_agent_config_path=live_agent_config,
                    meeting_id="resident-m1",
                    group_id="resident-main",
                )

            self.assertIn("provider_kind", str(raised.exception))
            self.assertEqual(supervisor.started, [])
            self.assertFalse((root / "meetings").exists())

    def test_start_session_refuses_remote_provider_with_local_connection_before_writing_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"], provider_kind="remote_http_bridge")
            live_agent_config = _write_live_agent_config(root, ["agent-a"], provider_kind="remote_http_bridge", connection_kind="local_cli")
            supervisor = FakeSessionSupervisor(root)

            with self.assertRaises(ValueError) as raised:
                start_live_agent_session(
                    root,
                    supervisor,
                    server="http://127.0.0.1:8765",
                    council_config_path=council_config,
                    agent_config_path=agent_config,
                    live_agent_config_path=live_agent_config,
                    meeting_id="resident-m1",
                    group_id="resident-main",
                )

            self.assertIn("connection_kind", str(raised.exception))
            self.assertEqual(supervisor.started, [])
            self.assertFalse((root / "meetings").exists())

    def test_start_session_accepts_codex_live_session_manifest_with_default_command(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"], provider_kind="codex_live_session")
            live_agent_config = root / "live-agents.json"
            live_agent_config.write_text(
                json.dumps(
                    {
                        "server": "http://127.0.0.1:8765",
                        "agents": [
                            {
                                "agent_id": "agent-a",
                                "display_name": "Agent A",
                                "provider_kind": "codex_live_session",
                                "connection_kind": "live_session",
                                "session_id": "019e3038-39cc-76a2-a746-5ba8c0f3b408",
                                "engagement_mode": "moderator_called",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            supervisor = FakeSessionSupervisor(root)

            session = start_live_agent_session(
                root,
                supervisor,
                server="http://127.0.0.1:8765",
                council_config_path=council_config,
                agent_config_path=agent_config,
                live_agent_config_path=live_agent_config,
                meeting_id="resident-m1",
                group_id="resident-main",
                connect_timeout_seconds=0,
                preflight_checker=lambda *args, **kwargs: {"status": "ok"},
            )

        self.assertEqual(session["meeting_id"], "resident-m1")
        self.assertEqual(session["group_id"], "resident-main")
        self.assertEqual(supervisor.started[0]["config_path"], live_agent_config)
        self.assertEqual(supervisor.started[0]["group_id"], "resident-main")

    def test_start_session_accepts_local_provider_with_terminal_session_transport(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"], provider_kind="local_cli")
            live_agent_config = _write_live_agent_config(
                root,
                ["agent-a"],
                provider_kind="local_cli",
                connection_kind="terminal_session",
            )
            supervisor = FakeSessionSupervisor(root)

            session = start_live_agent_session(
                root,
                supervisor,
                server="http://127.0.0.1:8765",
                council_config_path=council_config,
                agent_config_path=agent_config,
                live_agent_config_path=live_agent_config,
                meeting_id="resident-m1",
                group_id="resident-main",
                connect_timeout_seconds=0,
                preflight_checker=lambda *args, **kwargs: {"status": "ok"},
            )

        self.assertEqual(session["meeting_id"], "resident-m1")
        self.assertEqual(supervisor.started[0]["config_path"], live_agent_config)

    def test_start_session_accepts_local_provider_with_self_service_transport(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"], provider_kind="local_cli")
            live_agent_config = _write_live_agent_config(
                root,
                ["agent-a"],
                provider_kind="local_cli",
                connection_kind="self_service",
            )
            supervisor = FakeSessionSupervisor(root)

            session = start_live_agent_session(
                root,
                supervisor,
                server="http://127.0.0.1:8765",
                council_config_path=council_config,
                agent_config_path=agent_config,
                live_agent_config_path=live_agent_config,
                meeting_id="resident-m1",
                group_id="resident-main",
                connect_timeout_seconds=0,
                preflight_checker=lambda *args, **kwargs: {"status": "ok"},
            )

        self.assertEqual(session["meeting_id"], "resident-m1")
        self.assertEqual(supervisor.started[0]["config_path"], live_agent_config)

    def test_start_session_allows_remote_bridge_provider_label_behind_bridge_transport(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"], provider_kind="remote_http_bridge")
            live_agent_config = _write_live_agent_config(
                root,
                ["agent-a"],
                provider_kind="claude_code",
                connection_kind="remote_bridge",
                endpoint="http://bridge.local:8777",
                auth_ref="literal:test-token",
            )

            session = start_live_agent_session(
                root,
                FakeSessionSupervisor(root),
                server="http://127.0.0.1:8765",
                council_config_path=council_config,
                agent_config_path=agent_config,
                live_agent_config_path=live_agent_config,
                meeting_id="resident-m1",
                group_id="resident-main",
                connect_timeout_seconds=0,
            )

            self.assertEqual(session["group"]["status"], "running")
            self.assertEqual(session["connection"]["expected"], 1)

    def test_start_session_example_configs_match_demo_council(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            from agentsassemble.legacy.live_agent.state import heartbeat_live_agent

            root = Path(temp_dir)

            class ExampleSupervisor:
                def start_group(self, **kwargs):
                    configs = load_group_configs(kwargs["config_path"], server_override=kwargs["server"])
                    for config in configs:
                        heartbeat_live_agent(root, config.agent_id, status="online")
                    return {
                        "group_id": kwargs.get("group_id") or "resident-main",
                        "status": "running",
                        "agents": [
                            {
                                "agent_id": config.agent_id,
                                "display_name": config.display_name,
                                "provider_kind": config.provider_kind,
                                "connection_kind": config.connection_kind,
                            }
                            for config in configs
                        ],
                    }

            session = start_live_agent_session(
                root,
                ExampleSupervisor(),
                server="http://127.0.0.1:8765",
                council_config_path=Path("configs/demo-council.json"),
                agent_config_path=Path("configs/agents.start-session.example.json"),
                live_agent_config_path=Path("configs/live-agents.start-session.example.json"),
                meeting_id="resident-example",
                group_id="resident-main",
                connect_timeout_seconds=0,
                preflight_checker=lambda *args, **kwargs: {"status": "ok"},
            )

            self.assertEqual(session["status"], "ready")
            self.assertEqual(session["connection"]["expected"], 3)
            self.assertEqual(session["connection"]["connected"], 3)
            self.assertEqual(session["process"]["matched"], 3)

    def test_start_session_diagnostic_marks_agents_and_process_group(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            live_agent_config = _write_live_agent_config(root, ["agent-a"])

            class DiagnosticSupervisor:
                def __init__(self) -> None:
                    self.started = []

                def start_group(self, **kwargs):
                    self.started.append(kwargs)
                    heartbeat_live_agent(root, "agent-a", status="online")
                    return {
                        "group_id": kwargs.get("group_id") or "resident-main",
                        "status": "running",
                        "diagnostic": kwargs.get("diagnostic"),
                        "agents": [
                            {
                                "agent_id": "agent-a",
                                "display_name": "Agent A",
                                "provider_kind": "local_cli",
                                "connection_kind": "local_cli",
                            }
                        ],
                    }

            supervisor = DiagnosticSupervisor()

            session = start_live_agent_session(
                root,
                supervisor,
                server="http://127.0.0.1:8765",
                council_config_path=council_config,
                agent_config_path=agent_config,
                live_agent_config_path=live_agent_config,
                meeting_id="resident-diagnostic",
                group_id="resident-diagnostic",
                connect_timeout_seconds=0,
                diagnostic=True,
            )

            self.assertEqual(session["status"], "ready")
            self.assertEqual(supervisor.started[0]["meeting_id"], "resident-diagnostic")
            self.assertTrue(supervisor.started[0]["diagnostic"])
            agents = read_live_agents(root)
            self.assertEqual({agent["agent_id"]: agent["diagnostic"] for agent in agents}, {"agent-a": True})

    def test_start_session_omits_unsafe_process_meeting_identity_from_summary(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            live_agent_config = _write_live_agent_config(root, ["agent-a"])

            class UnsafeSummarySupervisor:
                def start_group(self, **kwargs):
                    heartbeat_live_agent(root, "agent-a", status="online")
                    return {
                        "group_id": kwargs.get("group_id") or "resident-main",
                        "status": "running",
                        "meeting_id": "../secret-meeting",
                        "agents": [
                            {
                                "agent_id": "agent-a",
                                "display_name": "Agent A",
                                "provider_kind": "local_cli",
                                "connection_kind": "local_cli",
                            }
                        ],
                    }

            session = start_live_agent_session(
                root,
                UnsafeSummarySupervisor(),
                server="http://127.0.0.1:8765",
                council_config_path=council_config,
                agent_config_path=agent_config,
                live_agent_config_path=live_agent_config,
                meeting_id="resident-m1",
                group_id="resident-main",
                connect_timeout_seconds=0,
            )

            self.assertEqual(session["status"], "starting")
            self.assertIn("group:wrong_meeting", session["process"]["attention"])
            self.assertNotIn("meeting_id", session["group"])
            self.assertNotIn("../secret-meeting", json.dumps(session, ensure_ascii=False))

    def test_start_session_preflight_failure_reports_agent_level_reason(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            live_agent_config = _write_live_agent_config(root, ["agent-a"])
            supervisor = FakeSessionSupervisor(root)

            def failed_preflight(path, *, server_override=None):
                return {
                    "status": "failed",
                    "checks": [{"id": "agent_ids", "status": "ok", "message": "Agent ids are unique."}],
                    "agents": [
                        {
                            "agent_id": "agent-a",
                            "checks": [
                                {"id": "command", "status": "failed", "message": "Command not found: missing-cli"}
                            ],
                        }
                    ],
                }

            with self.assertRaises(ValueError) as raised:
                start_live_agent_session(
                    root,
                    supervisor,
                    server="http://127.0.0.1:8765",
                    council_config_path=council_config,
                    agent_config_path=agent_config,
                    live_agent_config_path=live_agent_config,
                    meeting_id="resident-m1",
                    group_id="resident-main",
                    preflight_checker=failed_preflight,
                )

            self.assertIn("agent-a command: Command not found: missing-cli", str(raised.exception))
            self.assertEqual(supervisor.started, [])
            self.assertFalse((root / "meetings").exists())

    def test_start_session_preflight_failure_redacts_top_level_config_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            live_agent_config = _write_live_agent_config(root, ["agent-a"])
            private_config = root / "private-live-agents.json"
            supervisor = FakeSessionSupervisor(root)

            def failed_preflight(path, *, server_override=None):
                return {
                    "status": "failed",
                    "checks": [
                        {
                            "id": "config_load",
                            "status": "failed",
                            "message": f"No such file or directory: '{private_config}'",
                        }
                    ],
                    "agents": [],
                }

            with self.assertRaises(ValueError) as raised:
                start_live_agent_session(
                    root,
                    supervisor,
                    server="http://127.0.0.1:8765",
                    council_config_path=council_config,
                    agent_config_path=agent_config,
                    live_agent_config_path=live_agent_config,
                    meeting_id="resident-m1",
                    group_id="resident-main",
                    preflight_checker=failed_preflight,
                )

            message = str(raised.exception)
            self.assertIn("config_load", message)
            self.assertIn("details redacted", message)
            self.assertNotIn(str(private_config), message)
            self.assertNotIn("private-live-agents", message)
            self.assertEqual(supervisor.started, [])
            self.assertFalse((root / "meetings").exists())

    def test_start_session_preflight_failure_redacts_agent_command_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            live_agent_config = _write_live_agent_config(root, ["agent-a"])
            private_command = root / "secret-tool"
            supervisor = FakeSessionSupervisor(root)

            def failed_preflight(path, *, server_override=None):
                return {
                    "status": "failed",
                    "checks": [],
                    "agents": [
                        {
                            "agent_id": "agent-a",
                            "checks": [
                                {
                                    "id": "command",
                                    "status": "failed",
                                    "message": f"Command not found: {private_command}",
                                }
                            ],
                        }
                    ],
                }

            with self.assertRaises(ValueError) as raised:
                start_live_agent_session(
                    root,
                    supervisor,
                    server="http://127.0.0.1:8765",
                    council_config_path=council_config,
                    agent_config_path=agent_config,
                    live_agent_config_path=live_agent_config,
                    meeting_id="resident-m1",
                    group_id="resident-main",
                    preflight_checker=failed_preflight,
                )

            message = str(raised.exception)
            self.assertIn("agent-a command", message)
            self.assertIn("details redacted", message)
            self.assertNotIn(str(private_command), message)
            self.assertNotIn("secret-tool", message)
            self.assertEqual(supervisor.started, [])
            self.assertFalse((root / "meetings").exists())

    def test_start_session_returns_sanitized_payload_without_config_paths_or_log_tail(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            live_agent_config = _write_live_agent_config(root, ["agent-a"])

            class SensitiveSupervisor(FakeSessionSupervisor):
                def start_group(self, **kwargs):
                    self.started.append(kwargs)
                    return {
                        "group_id": "resident-main",
                        "status": "running",
                        "config_path": str(live_agent_config),
                        "server": "http://room.local",
                        "log_path": str(root / "live-agent-runs" / "resident-main.log"),
                        "log_tail": "provider secret output",
                        "agents": [
                            {
                                "agent_id": "agent-a",
                                "display_name": "Agent A",
                                "provider_kind": "local_cli",
                                "connection_kind": "local_cli",
                            }
                        ],
                    }

            session = start_live_agent_session(
                root,
                SensitiveSupervisor(root),
                server="http://127.0.0.1:8765",
                council_config_path=council_config,
                agent_config_path=agent_config,
                live_agent_config_path=live_agent_config,
                meeting_id="resident-m1",
                group_id="resident-main",
                connect_timeout_seconds=0,
            )

            serialized = json.dumps(session, ensure_ascii=False)
            self.assertNotIn(str(council_config), serialized)
            self.assertNotIn(str(agent_config), serialized)
            self.assertNotIn(str(live_agent_config), serialized)
            self.assertNotIn("provider secret output", serialized)
            self.assertEqual(session["group"], {"group_id": "resident-main", "status": "running"})
            self.assertEqual(
                session["meeting"],
                {
                    "meeting_id": "resident-m1",
                    "live_status": "running",
                    "role_count": 1,
                    "bound_agent_count": 1,
                },
            )

    def test_start_session_returns_starting_when_agents_are_not_connected_yet(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            live_agent_config = _write_live_agent_config(root, ["agent-a"])
            supervisor = FakeSessionSupervisor(root)

            session = start_live_agent_session(
                root,
                supervisor,
                server="http://127.0.0.1:8765",
                council_config_path=council_config,
                agent_config_path=agent_config,
                live_agent_config_path=live_agent_config,
                meeting_id="resident-m1",
                group_id="resident-main",
                connect_timeout_seconds=0,
            )

            self.assertEqual(session["status"], "starting")
            self.assertEqual(session["connection"]["expected"], 1)
            self.assertEqual(session["connection"]["connected"], 0)
            self.assertEqual(session["connection"]["attention"], ["agent-a:offline"])
            self.assertTrue((root / "meetings" / "resident-m1" / "live_state.json").exists())

    def test_start_session_does_not_count_provider_mismatch_presence_as_connected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"], provider_kind="local_cli")
            live_agent_config = _write_live_agent_config(root, ["agent-a"], provider_kind="local_cli")

            class MismatchSupervisor(FakeSessionSupervisor):
                def start_group(self, **kwargs):
                    self.started.append(kwargs)
                    connect_live_agent(
                        root,
                        {
                            "agent_id": "agent-a",
                            "display_name": "Manual Agent A",
                            "provider_kind": "manual",
                            "connection_kind": "manual",
                            "meeting_id": "resident-m1",
                            "status": "online",
                        },
                    )
                    return {
                        "group_id": "resident-main",
                        "status": "running",
                        "agents": [
                            {
                                "agent_id": "agent-a",
                                "display_name": "Agent A",
                                "provider_kind": "local_cli",
                                "connection_kind": "local_cli",
                            }
                        ],
                    }

            session = start_live_agent_session(
                root,
                MismatchSupervisor(root),
                server="http://127.0.0.1:8765",
                council_config_path=council_config,
                agent_config_path=agent_config,
                live_agent_config_path=live_agent_config,
                meeting_id="resident-m1",
                group_id="resident-main",
                connect_timeout_seconds=0,
            )

            self.assertEqual(session["status"], "starting")
            self.assertEqual(session["connection"]["connected"], 0)
            self.assertEqual(session["connection"]["attention"], ["agent-a:provider_kind_mismatch"])

    def test_start_session_requires_presence_after_process_start(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            live_agent_config = _write_live_agent_config(root, ["agent-a"])

            class FreshStartSupervisor(FakeSessionSupervisor):
                def start_group(self, **kwargs):
                    self.started.append(kwargs)
                    heartbeat_live_agent(
                        root,
                        "agent-a",
                        status="online",
                        now=datetime(2999, 1, 1, 0, 0, tzinfo=UTC),
                    )
                    return {
                        "group_id": "resident-main",
                        "status": "running",
                        "started_at": "2999-01-01T00:01:00+00:00",
                        "agents": [{"agent_id": "agent-a"}],
                    }

            session = start_live_agent_session(
                root,
                FreshStartSupervisor(root),
                server="http://127.0.0.1:8765",
                council_config_path=council_config,
                agent_config_path=agent_config,
                live_agent_config_path=live_agent_config,
                meeting_id="resident-m1",
                group_id="resident-main",
                connect_timeout_seconds=0,
            )

            self.assertEqual(session["status"], "starting")
            self.assertEqual(session["connection"]["connected"], 0)
            self.assertEqual(session["connection"]["attention"], ["agent-a:not_reconnected"])

    def test_start_session_returns_starting_when_group_is_not_running_even_if_agents_connected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            from agentsassemble.legacy.live_agent.state import heartbeat_live_agent

            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            live_agent_config = _write_live_agent_config(root, ["agent-a"])

            class StoppedSupervisor(FakeSessionSupervisor):
                def start_group(self, **kwargs):
                    self.started.append(kwargs)
                    heartbeat_live_agent(root, "agent-a", status="online")
                    return {"group_id": "resident-main", "status": "stopped", "agents": [{"agent_id": "agent-a"}]}

            session = start_live_agent_session(
                root,
                StoppedSupervisor(root),
                server="http://127.0.0.1:8765",
                council_config_path=council_config,
                agent_config_path=agent_config,
                live_agent_config_path=live_agent_config,
                meeting_id="resident-m1",
                group_id="resident-main",
                connect_timeout_seconds=0,
            )

            self.assertEqual(session["status"], "starting")
            self.assertEqual(session["connection"]["connected"], 1)
            self.assertEqual(session["process"]["status"], "stopped")
            self.assertIn("group:stopped", session["process"]["attention"])

    def test_start_session_start_group_failure_exposes_created_meeting_for_recovery(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            live_agent_config = _write_live_agent_config(root, ["agent-a"])

            class FailingSupervisor(FakeSessionSupervisor):
                def start_group(self, **kwargs):
                    self.started.append(kwargs)
                    raise RuntimeError("process launch refused")

            supervisor = FailingSupervisor(root)
            with self.assertRaises(Exception) as raised:
                start_live_agent_session(
                    root,
                    supervisor,
                    server="http://127.0.0.1:8765",
                    council_config_path=council_config,
                    agent_config_path=agent_config,
                    live_agent_config_path=live_agent_config,
                    group_id="resident-main",
                )

            meeting_id = getattr(raised.exception, "meeting_id", "")
            self.assertTrue(meeting_id)
            self.assertTrue((root / "meetings" / meeting_id / "live_state.json").exists())

    def test_start_session_refuses_resident_config_for_another_meeting_before_writing_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            live_agent_config = _write_live_agent_config(root, ["agent-a"], meeting_id="other-meeting")
            supervisor = FakeSessionSupervisor(root)

            with self.assertRaises(ValueError) as raised:
                start_live_agent_session(
                    root,
                    supervisor,
                    server="http://127.0.0.1:8765",
                    council_config_path=council_config,
                    agent_config_path=agent_config,
                    live_agent_config_path=live_agent_config,
                    meeting_id="resident-m1",
                    group_id="resident-main",
                )

            self.assertIn("meeting id does not match", str(raised.exception))
            self.assertEqual(supervisor.started, [])
            self.assertFalse((root / "meetings").exists())

    def test_start_session_refuses_existing_process_group_owned_by_another_meeting(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            live_agent_config = _write_live_agent_config(root, ["agent-a"])

            class OwnedGroupSupervisor(FakeSessionSupervisor):
                def snapshot_groups(self):
                    return [
                        {
                            "group_id": "resident-main",
                            "status": "stopped",
                            "meeting_id": "other-meeting",
                            "agents": [{"agent_id": "agent-a"}],
                        }
                    ]

            supervisor = OwnedGroupSupervisor(root)

            with self.assertRaises(ValueError) as raised:
                start_live_agent_session(
                    root,
                    supervisor,
                    server="http://127.0.0.1:8765",
                    council_config_path=council_config,
                    agent_config_path=agent_config,
                    live_agent_config_path=live_agent_config,
                    meeting_id="resident-m1",
                    group_id="resident-main",
                )

            self.assertIn("belongs to a different meeting", str(raised.exception))
            self.assertEqual(supervisor.started, [])
            self.assertFalse((root / "meetings").exists())

    def test_resume_session_reuses_running_group_for_existing_meeting_without_recreating_it(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            live_agent_config = _write_live_agent_config(root, ["agent-a"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")

            class RunningSupervisor:
                def __init__(self) -> None:
                    self.started = []

                def list_groups(self):
                    return [
                        {
                            "group_id": "resident-main",
                            "status": "running",
                            "agents": [{"agent_id": "agent-a", "provider_kind": "local_cli", "connection_kind": "local_cli"}],
                        }
                    ]

                def start_group(self, **kwargs):
                    self.started.append(kwargs)
                    raise AssertionError("resume must not start a group that is already running")

            supervisor = RunningSupervisor()

            session = resume_live_agent_session(
                root,
                supervisor,
                server="http://127.0.0.1:8765",
                live_agent_config_path=live_agent_config,
                meeting_id="resident-m1",
                group_id="resident-main",
                connect_timeout_seconds=0,
                preflight_checker=lambda *args, **kwargs: {"status": "ok"},
            )

            self.assertEqual(session["status"], "ready")
            self.assertEqual(session["meeting_id"], "resident-m1")
            self.assertEqual(session["group_id"], "resident-main")
            self.assertEqual(session["connection"]["connected"], 1)
            self.assertEqual(supervisor.started, [])
            meeting = json.loads((root / "meetings" / "resident-m1" / "live_state.json").read_text(encoding="utf-8"))
            self.assertEqual(meeting["meeting_id"], "resident-m1")

    def test_resume_session_refuses_binding_with_missing_provider_config(self):
        class NoProcessSupervisor:
            def list_groups(self):
                raise AssertionError("resume must fail before reading process groups")

            def start_group(self, **kwargs):
                raise AssertionError("resume must fail before starting a group")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            live_agent_config = _write_live_agent_config(root, ["agent-a"])
            result = start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            meeting = dict(result["meeting"])
            meeting["agent_bindings"] = [
                {
                    "role_id": "architect",
                    "agent_id": "agent-a",
                    "provider_id": "missing-provider",
                }
            ]
            meeting["provider_configs"] = {}
            write_live_state(root / "meetings" / "resident-m1", meeting)

            with self.assertRaises(ValueError) as raised:
                resume_live_agent_session(
                    root,
                    NoProcessSupervisor(),
                    server="http://127.0.0.1:8765",
                    live_agent_config_path=live_agent_config,
                    meeting_id="resident-m1",
                    group_id="resident-main",
                    preflight_checker=lambda *args, **kwargs: {"status": "ok"},
                )

        self.assertIn("binding_provider_missing", str(raised.exception))
        self.assertNotIn("missing-provider", str(raised.exception))

    def test_resume_session_requires_presence_after_reused_process_start(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            live_agent_config = _write_live_agent_config(root, ["agent-a"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(
                root,
                "agent-a",
                status="online",
                now=datetime(2999, 1, 1, 0, 0, tzinfo=UTC),
            )

            class RunningSupervisor:
                def __init__(self) -> None:
                    self.started = []

                def list_groups(self):
                    return [
                        {
                            "group_id": "resident-main",
                            "status": "running",
                            "started_at": "2999-01-01T00:01:00+00:00",
                            "agents": [{"agent_id": "agent-a", "provider_kind": "local_cli", "connection_kind": "local_cli"}],
                        }
                    ]

                def start_group(self, **kwargs):
                    self.started.append(kwargs)
                    raise AssertionError("resume must reuse the running group")

            supervisor = RunningSupervisor()

            session = resume_live_agent_session(
                root,
                supervisor,
                server="http://127.0.0.1:8765",
                live_agent_config_path=live_agent_config,
                meeting_id="resident-m1",
                group_id="resident-main",
                connect_timeout_seconds=0,
                preflight_checker=lambda *args, **kwargs: {"status": "ok"},
            )

            self.assertEqual(session["status"], "starting")
            self.assertEqual(session["connection"]["connected"], 0)
            self.assertEqual(session["connection"]["attention"], ["agent-a:not_reconnected"])
            self.assertEqual(supervisor.started, [])

    def test_resume_session_reuses_running_group_with_normalized_group_id(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            live_agent_config = _write_live_agent_config(root, ["agent-a"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")

            class NormalizingSupervisor:
                def __init__(self) -> None:
                    self.started = []

                def list_groups(self):
                    return [
                        {
                            "group_id": "resident-main",
                            "status": "running",
                            "agents": [{"agent_id": "agent-a", "provider_kind": "local_cli", "connection_kind": "local_cli"}],
                        }
                    ]

                def start_group(self, **kwargs):
                    self.started.append(kwargs)
                    raise AssertionError("resume must normalize group id before deciding whether to start")

            supervisor = NormalizingSupervisor()

            session = resume_live_agent_session(
                root,
                supervisor,
                server="http://127.0.0.1:8765",
                live_agent_config_path=live_agent_config,
                meeting_id="resident-m1",
                group_id="resident main",
                connect_timeout_seconds=0,
                preflight_checker=lambda *args, **kwargs: {"status": "ok"},
            )

            self.assertEqual(session["status"], "ready")
            self.assertEqual(session["group_id"], "resident-main")
            self.assertEqual(supervisor.started, [])

    def test_resume_session_starts_restarting_group_from_validated_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            live_agent_config = _write_live_agent_config(root, ["agent-a"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")

            class RestartingSupervisor:
                def __init__(self) -> None:
                    self.started = []

                def list_groups(self):
                    return [
                        {
                            "group_id": "resident-main",
                            "status": "restarting",
                            "agents": [{"agent_id": "agent-a", "provider_kind": "local_cli", "connection_kind": "local_cli"}],
                        }
                    ]

                def start_group(self, **kwargs):
                    self.started.append(kwargs)
                    heartbeat_live_agent(root, "agent-a", status="online")
                    return {
                        "group_id": kwargs.get("group_id") or "resident-main",
                        "status": "running",
                        "meeting_id": kwargs.get("meeting_id"),
                        "agents": [{"agent_id": "agent-a", "provider_kind": "local_cli", "connection_kind": "local_cli"}],
                    }

            supervisor = RestartingSupervisor()

            session = resume_live_agent_session(
                root,
                supervisor,
                server="http://127.0.0.1:8765",
                live_agent_config_path=live_agent_config,
                meeting_id="resident-m1",
                group_id="resident-main",
                connect_timeout_seconds=0,
                preflight_checker=lambda *args, **kwargs: {"status": "ok"},
            )

            self.assertEqual(session["status"], "ready")
            self.assertEqual(session["group_id"], "resident-main")
            self.assertEqual(session["process"]["status"], "running")
            self.assertEqual(session["connection"]["connected"], 1)
            self.assertEqual(supervisor.started[0]["config_path"], live_agent_config)
            self.assertEqual(supervisor.started[0]["group_id"], "resident-main")
            self.assertEqual(supervisor.started[0]["meeting_id"], "resident-m1")

    def test_resume_session_starts_missing_group_without_marking_roster_online(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            live_agent_config = _write_live_agent_config(root, ["agent-a"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            (root / "live_agents.json").unlink()

            class MissingSupervisor:
                def __init__(self) -> None:
                    self.started = []

                def list_groups(self):
                    return []

                def start_group(self, **kwargs):
                    self.started.append(kwargs)
                    return {
                        "group_id": kwargs.get("group_id") or "resident-main",
                        "status": "running",
                        "agents": [{"agent_id": "agent-a", "provider_kind": "local_cli", "connection_kind": "local_cli"}],
                    }

            supervisor = MissingSupervisor()

            session = resume_live_agent_session(
                root,
                supervisor,
                server="http://127.0.0.1:8765",
                live_agent_config_path=live_agent_config,
                meeting_id="resident-m1",
                group_id="resident-main",
                connect_timeout_seconds=0,
                preflight_checker=lambda *args, **kwargs: {"status": "ok"},
            )

            self.assertEqual(session["status"], "starting")
            self.assertEqual(session["connection"]["attention"], ["agent-a:offline"])
            self.assertEqual(supervisor.started[0]["config_path"], live_agent_config)
            self.assertEqual(supervisor.started[0]["group_id"], "resident-main")
            self.assertEqual(supervisor.started[0]["meeting_id"], "resident-m1")
            agents = read_live_agents(root)
            self.assertEqual(len(agents), 1)
            self.assertEqual(agents[0]["agent_id"], "agent-a")
            self.assertEqual(agents[0]["meeting_id"], "resident-m1")
            self.assertEqual(agents[0]["status"], "offline")

    def test_resume_session_refuses_running_group_owned_by_another_meeting(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            live_agent_config = _write_live_agent_config(root, ["agent-a"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            (root / "live_agents.json").unlink()

            class RunningOwnedSupervisor:
                def __init__(self) -> None:
                    self.started = []

                def list_groups(self):
                    return [
                        {
                            "group_id": "resident-main",
                            "status": "running",
                            "meeting_id": "other-meeting",
                            "agents": [{"agent_id": "agent-a"}],
                        }
                    ]

                def start_group(self, **kwargs):
                    self.started.append(kwargs)
                    raise AssertionError("resume must not start over a group owned by another meeting")

            supervisor = RunningOwnedSupervisor()

            with self.assertRaises(ValueError) as raised:
                resume_live_agent_session(
                    root,
                    supervisor,
                    server="http://127.0.0.1:8765",
                    live_agent_config_path=live_agent_config,
                    meeting_id="resident-m1",
                    group_id="resident-main",
                    connect_timeout_seconds=0,
                    preflight_checker=lambda *args, **kwargs: {"status": "ok"},
                )

            self.assertIn("belongs to a different meeting", str(raised.exception))
            self.assertEqual(supervisor.started, [])
            self.assertFalse((root / "live_agents.json").exists())

    def test_resume_session_refuses_stopped_group_owned_by_another_meeting_before_roster_or_start(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            live_agent_config = _write_live_agent_config(root, ["agent-a"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            (root / "live_agents.json").unlink()

            class StoppedOwnedSupervisor:
                def __init__(self) -> None:
                    self.started = []

                def list_groups(self):
                    return [
                        {
                            "group_id": "resident-main",
                            "status": "stopped",
                            "meeting_id": "other-meeting",
                            "agents": [{"agent_id": "agent-a"}],
                        }
                    ]

                def start_group(self, **kwargs):
                    self.started.append(kwargs)
                    return {"group_id": "resident-main", "status": "running", "agents": [{"agent_id": "agent-a"}]}

            supervisor = StoppedOwnedSupervisor()

            with self.assertRaises(ValueError) as raised:
                resume_live_agent_session(
                    root,
                    supervisor,
                    server="http://127.0.0.1:8765",
                    live_agent_config_path=live_agent_config,
                    meeting_id="resident-m1",
                    group_id="resident-main",
                    connect_timeout_seconds=0,
                    preflight_checker=lambda *args, **kwargs: {"status": "ok"},
                )

            self.assertIn("belongs to a different meeting", str(raised.exception))
            self.assertEqual(supervisor.started, [])
            self.assertFalse((root / "live_agents.json").exists())

    def test_resume_session_restarts_unknown_group_from_supplied_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            live_agent_config = _write_live_agent_config(root, ["agent-a"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )

            class UnknownSupervisor:
                def __init__(self) -> None:
                    self.started = []
                    self.recovered = []

                def list_groups(self):
                    return [
                        {
                            "group_id": "resident-main",
                            "status": "unknown",
                            "agents": [{"agent_id": "old-agent"}],
                        }
                    ]

                def recover_group(self, group_id):
                    self.recovered.append(group_id)
                    return {"group_id": group_id, "status": "running", "agents": [{"agent_id": "old-agent"}]}

                def start_group(self, **kwargs):
                    self.started.append(kwargs)
                    return {
                        "group_id": kwargs.get("group_id") or "resident-main",
                        "status": "running",
                        "agents": [{"agent_id": "agent-a", "provider_kind": "local_cli", "connection_kind": "local_cli"}],
                    }

            supervisor = UnknownSupervisor()

            session = resume_live_agent_session(
                root,
                supervisor,
                server="http://127.0.0.1:8765",
                live_agent_config_path=live_agent_config,
                meeting_id="resident-m1",
                group_id="resident-main",
                connect_timeout_seconds=0,
                preflight_checker=lambda *args, **kwargs: {"status": "ok"},
            )

            self.assertEqual(session["process"]["matched"], 1)
            self.assertEqual(supervisor.recovered, [])
            self.assertEqual(supervisor.started[0]["config_path"], live_agent_config)

    def test_resume_session_refuses_missing_meeting_before_starting_processes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            live_agent_config = _write_live_agent_config(root, ["agent-a"])
            supervisor = FakeSessionSupervisor(root)

            with self.assertRaises(ValueError) as raised:
                resume_live_agent_session(
                    root,
                    supervisor,
                    server="http://127.0.0.1:8765",
                    live_agent_config_path=live_agent_config,
                    meeting_id="missing-meeting",
                    group_id="resident-main",
                )

            self.assertIn("Meeting missing-meeting was not found", str(raised.exception))
            self.assertEqual(supervisor.started, [])

    def test_resume_session_refuses_manifest_mismatch_against_existing_meeting(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect", "critic"])
            agent_config = _write_agent_config(root, ["agent-a", "agent-b"])
            live_agent_config = _write_live_agent_config(root, ["agent-a"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            supervisor = FakeSessionSupervisor(root)

            with self.assertRaises(ValueError) as raised:
                resume_live_agent_session(
                    root,
                    supervisor,
                    server="http://127.0.0.1:8765",
                    live_agent_config_path=live_agent_config,
                    meeting_id="resident-m1",
                    group_id="resident-main",
                    preflight_checker=lambda *args, **kwargs: {"status": "ok"},
                )

            self.assertIn("does not cover meeting agents", str(raised.exception))
            self.assertEqual(supervisor.started, [])

    def test_stop_session_stops_group_and_marks_bound_agents_offline(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect", "critic"])
            agent_config = _write_agent_config(root, ["agent-a", "agent-b"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")
            heartbeat_live_agent(root, "agent-b", status="working")

            class StopSupervisor:
                def __init__(self) -> None:
                    self.stopped = []

                def stop_group(self, group_id):
                    self.stopped.append(group_id)
                    return {
                        "group_id": group_id,
                        "status": "stopped",
                        "config_path": str(root / "secret-live-agents.json"),
                        "log_tail": "secret provider output",
                        "agents": [{"agent_id": "agent-a"}, {"agent_id": "agent-b"}],
                    }

            supervisor = StopSupervisor()

            session = stop_live_agent_session(
                root,
                supervisor,
                meeting_id="resident-m1",
                group_id="resident main",
            )

            self.assertEqual(session["status"], "stopped")
            self.assertEqual(session["meeting_id"], "resident-m1")
            self.assertEqual(session["group_id"], "resident-main")
            self.assertEqual(supervisor.stopped, ["resident-main"])
            self.assertEqual(session["offline"]["expected"], 2)
            self.assertEqual(session["offline"]["offline"], 2)
            agents = {agent["agent_id"]: agent for agent in read_live_agents(root)}
            self.assertEqual(agents["agent-a"]["status"], "offline")
            self.assertEqual(agents["agent-b"]["status"], "offline")
            serialized = json.dumps(session, ensure_ascii=False)
            self.assertNotIn("secret-live-agents", serialized)
            self.assertNotIn("secret provider output", serialized)
            self.assertTrue((root / "meetings" / "resident-m1" / "live_state.json").exists())

    def test_stop_session_refuses_missing_meeting_before_stopping_group(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            class StopSupervisor:
                def __init__(self) -> None:
                    self.stopped = []

                def stop_group(self, group_id):
                    self.stopped.append(group_id)
                    return {"group_id": group_id, "status": "stopped", "agents": []}

            supervisor = StopSupervisor()

            with self.assertRaises(ValueError) as raised:
                stop_live_agent_session(root, supervisor, meeting_id="missing-meeting", group_id="resident-main")

            self.assertIn("Meeting missing-meeting was not found", str(raised.exception))
            self.assertEqual(supervisor.stopped, [])

    def test_stop_session_requires_explicit_group_id_before_stopping(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )

            class StopSupervisor:
                def __init__(self) -> None:
                    self.stopped = []

                def stop_group(self, group_id):
                    self.stopped.append(group_id)
                    return {"group_id": group_id, "status": "stopped", "agents": [{"agent_id": "agent-a"}]}

            supervisor = StopSupervisor()

            with self.assertRaises(ValueError) as raised:
                stop_live_agent_session(root, supervisor, meeting_id="resident-m1", group_id=" ")

            self.assertIn("Live agent group id is required", str(raised.exception))
            self.assertEqual(supervisor.stopped, [])

    def test_stop_session_refuses_group_manifest_mismatch_before_stop_and_offline(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect", "critic"])
            agent_config = _write_agent_config(root, ["agent-a", "agent-b"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")

            class StopSupervisor:
                def __init__(self) -> None:
                    self.stopped = []

                def list_groups(self):
                    return [
                        {
                            "group_id": "resident-main",
                            "status": "running",
                            "agents": [{"agent_id": "agent-a"}, {"agent_id": "agent-x"}],
                        }
                    ]

                def stop_group(self, group_id):
                    self.stopped.append(group_id)
                    return {"group_id": group_id, "status": "stopped", "agents": [{"agent_id": "agent-a"}]}

            supervisor = StopSupervisor()

            with self.assertRaises(ValueError) as raised:
                stop_live_agent_session(root, supervisor, meeting_id="resident-m1", group_id="resident-main")

            self.assertIn("does not match meeting agents", str(raised.exception))
            self.assertEqual(supervisor.stopped, [])
            agents = {agent["agent_id"]: agent for agent in read_live_agents(root)}
            self.assertEqual(agents["agent-a"]["status"], "online")

    def test_stop_session_refuses_duplicate_manifest_agent_before_stop_and_offline(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")

            class StopSupervisor:
                def __init__(self) -> None:
                    self.stopped = []

                def list_groups(self):
                    return [
                        {
                            "group_id": "resident-main",
                            "status": "running",
                            "agents": [{"agent_id": "agent-a"}, {"agent_id": "agent-a"}],
                        }
                    ]

                def stop_group(self, group_id):
                    self.stopped.append(group_id)
                    return {"group_id": group_id, "status": "stopped", "agents": [{"agent_id": "agent-a"}]}

            supervisor = StopSupervisor()

            with self.assertRaises(ValueError) as raised:
                stop_live_agent_session(root, supervisor, meeting_id="resident-m1", group_id="resident-main")

            self.assertIn("duplicate agent-a", str(raised.exception))
            self.assertEqual(supervisor.stopped, [])
            agents = {agent["agent_id"]: agent for agent in read_live_agents(root)}
            self.assertEqual(agents["agent-a"]["status"], "online")

    def test_stop_session_refuses_group_owned_by_another_meeting_before_stop_and_offline(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")

            class StopSupervisor:
                def __init__(self) -> None:
                    self.stopped = []

                def list_groups(self):
                    return [
                        {
                            "group_id": "resident-main",
                            "status": "running",
                            "meeting_id": "other-meeting",
                            "agents": [{"agent_id": "agent-a"}],
                        }
                    ]

                def stop_group(self, group_id):
                    self.stopped.append(group_id)
                    return {"group_id": group_id, "status": "stopped", "agents": [{"agent_id": "agent-a"}]}

            supervisor = StopSupervisor()

            with self.assertRaises(ValueError) as raised:
                stop_live_agent_session(root, supervisor, meeting_id="resident-m1", group_id="resident-main")

            self.assertIn("belongs to a different meeting", str(raised.exception))
            self.assertEqual(supervisor.stopped, [])
            agents = {agent["agent_id"]: agent for agent in read_live_agents(root)}
            self.assertEqual(agents["agent-a"]["status"], "online")

    def test_stop_session_refuses_unsafe_process_owner_without_echoing_it(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")

            class StopSupervisor:
                def __init__(self) -> None:
                    self.stopped = []

                def list_groups(self):
                    return [
                        {
                            "group_id": "resident-main",
                            "status": "running",
                            "meeting_id": "../secret-meeting",
                            "agents": [{"agent_id": "agent-a"}],
                        }
                    ]

                def stop_group(self, group_id):
                    self.stopped.append(group_id)
                    return {"group_id": group_id, "status": "stopped", "agents": [{"agent_id": "agent-a"}]}

            supervisor = StopSupervisor()

            with self.assertRaises(ValueError) as raised:
                stop_live_agent_session(root, supervisor, meeting_id="resident-m1", group_id="resident-main")

            self.assertIn("belongs to a different meeting", str(raised.exception))
            self.assertNotIn("../secret-meeting", str(raised.exception))
            self.assertEqual(supervisor.stopped, [])
            agents = {agent["agent_id"]: agent for agent in read_live_agents(root)}
            self.assertEqual(agents["agent-a"]["status"], "online")

    def test_stop_session_leaves_wrong_meeting_roster_row_untouched(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            connect_live_agent(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "meeting_id": "other-meeting",
                    "engagement_mode": "moderator_called",
                    "status": "online",
                    "capabilities": ["room_chat", "official_turn"],
                },
            )

            class StopSupervisor:
                def stop_group(self, group_id):
                    return {"group_id": group_id, "status": "stopped", "agents": [{"agent_id": "agent-a"}]}

                def list_groups(self):
                    return [{"group_id": "resident-main", "status": "running", "agents": [{"agent_id": "agent-a"}]}]

            session = stop_live_agent_session(
                root,
                StopSupervisor(),
                meeting_id="resident-m1",
                group_id="resident-main",
            )

            self.assertEqual(session["status"], "stopping")
            self.assertEqual(session["offline"]["expected"], 1)
            self.assertEqual(session["offline"]["offline"], 0)
            self.assertEqual(session["offline"]["attention"], ["agent-a:wrong_meeting"])
            agents = {agent["agent_id"]: agent for agent in read_live_agents(root)}
            self.assertEqual(agents["agent-a"]["meeting_id"], "other-meeting")
            self.assertEqual(agents["agent-a"]["status"], "online")

    def test_stop_session_reports_stopping_when_stop_returns_unknown_status(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")

            class StopSupervisor:
                def stop_group(self, group_id):
                    return {"group_id": group_id, "agents": [{"agent_id": "agent-a"}]}

                def list_groups(self):
                    return [{"group_id": "resident-main", "status": "running", "agents": [{"agent_id": "agent-a"}]}]

            session = stop_live_agent_session(
                root,
                StopSupervisor(),
                meeting_id="resident-m1",
                group_id="resident-main",
            )

            self.assertEqual(session["status"], "stopping")
            self.assertIn("group:unknown", session["process"]["attention"])

    def test_stop_session_reports_process_attention_when_group_remains_running(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")

            class StopSupervisor:
                def stop_group(self, group_id):
                    return {"group_id": group_id, "status": "running", "agents": [{"agent_id": "agent-a"}]}

                def list_groups(self):
                    return [{"group_id": "resident-main", "status": "running", "agents": [{"agent_id": "agent-a"}]}]

            session = stop_live_agent_session(
                root,
                StopSupervisor(),
                meeting_id="resident-m1",
                group_id="resident-main",
            )

            self.assertEqual(session["status"], "stopping")
            self.assertIn("group:running", session["process"]["attention"])

    def test_stop_session_does_not_mark_agents_offline_when_stop_group_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")

            class StopSupervisor:
                def __init__(self) -> None:
                    self.stopped = []

                def list_groups(self):
                    return [{"group_id": "resident-main", "status": "running", "agents": [{"agent_id": "agent-a"}]}]

                def stop_group(self, group_id):
                    self.stopped.append(group_id)
                    raise ValueError("stop failed: /private/live-agents.json")

            supervisor = StopSupervisor()

            with self.assertRaises(ValueError):
                stop_live_agent_session(root, supervisor, meeting_id="resident-m1", group_id="resident-main")

            self.assertEqual(supervisor.stopped, ["resident-main"])
            agents = {agent["agent_id"]: agent for agent in read_live_agents(root)}
            self.assertEqual(agents["agent-a"]["status"], "online")

    def test_restart_session_stops_running_group_and_waits_for_fresh_presence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect", "critic"])
            agent_config = _write_agent_config(root, ["agent-a", "agent-b"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")
            heartbeat_live_agent(root, "agent-b", status="working")

            class RestartSupervisor:
                def __init__(self) -> None:
                    self.stopped = []
                    self.restarted = []

                def snapshot_groups(self):
                    return [
                        {
                            "group_id": "resident-main",
                            "status": "running",
                            "agents": [{"agent_id": "agent-a"}, {"agent_id": "agent-b"}],
                        }
                    ]

                def stop_group(self, group_id):
                    self.stopped.append(group_id)
                    return {"group_id": group_id, "status": "stopped", "agents": [{"agent_id": "agent-a"}, {"agent_id": "agent-b"}]}

                def restart_group(self, group_id):
                    self.restarted.append(group_id)
                    agents = {agent["agent_id"]: agent for agent in read_live_agents(root)}
                    if agents["agent-a"]["status"] != "offline" or agents["agent-b"]["status"] != "offline":
                        raise AssertionError("restart must clear stale presence before starting the group again")
                    heartbeat_live_agent(root, "agent-a", status="online")
                    heartbeat_live_agent(root, "agent-b", status="working")
                    return {"group_id": group_id, "status": "running", "agents": [{"agent_id": "agent-a"}, {"agent_id": "agent-b"}]}

            supervisor = RestartSupervisor()

            session = restart_live_agent_session(
                root,
                supervisor,
                meeting_id="resident-m1",
                group_id="resident main",
                connect_timeout_seconds=0,
            )

            self.assertEqual(session["status"], "ready")
            self.assertEqual(session["meeting_id"], "resident-m1")
            self.assertEqual(session["group_id"], "resident-main")
            self.assertEqual(supervisor.stopped, ["resident-main"])
            self.assertEqual(supervisor.restarted, ["resident-main"])
            self.assertEqual(session["offline"]["offline"], 2)
            self.assertEqual(session["connection"]["connected"], 2)
            self.assertTrue((root / "meetings" / "resident-m1" / "live_state.json").exists())

    def test_restart_session_refuses_binding_with_missing_provider_config_before_mutating_group(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            result = start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            meeting = dict(result["meeting"])
            meeting["agent_bindings"] = [
                {
                    "role_id": "architect",
                    "agent_id": "agent-a",
                    "provider_id": "missing-provider",
                }
            ]
            meeting["provider_configs"] = {}
            write_live_state(root / "meetings" / "resident-m1", meeting)
            heartbeat_live_agent(root, "agent-a", status="online")

            class RestartSupervisor:
                def __init__(self) -> None:
                    self.stopped = []
                    self.restarted = []

                def snapshot_groups(self):
                    return [
                        {
                            "group_id": "resident-main",
                            "status": "running",
                            "meeting_id": "resident-m1",
                            "agents": [
                                {
                                    "agent_id": "agent-a",
                                    "provider_kind": "local_cli",
                                    "connection_kind": "local_cli",
                                }
                            ],
                        }
                    ]

                def stop_group(self, group_id):
                    self.stopped.append(group_id)
                    raise AssertionError("restart must fail before stopping the group")

                def restart_group(self, group_id):
                    self.restarted.append(group_id)
                    raise AssertionError("restart must fail before restarting the group")

            supervisor = RestartSupervisor()

            with self.assertRaises(ValueError) as raised:
                restart_live_agent_session(root, supervisor, meeting_id="resident-m1", group_id="resident-main")

        self.assertIn("binding_provider_missing", str(raised.exception))
        self.assertNotIn("missing-provider", str(raised.exception))
        self.assertEqual(supervisor.stopped, [])
        self.assertEqual(supervisor.restarted, [])

    def test_restart_session_stops_restarting_group_before_clearing_presence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")

            class RestartSupervisor:
                def __init__(self) -> None:
                    self.stopped = []
                    self.restarted = []

                def snapshot_groups(self):
                    return [{"group_id": "resident-main", "status": "restarting", "agents": [{"agent_id": "agent-a"}]}]

                def stop_group(self, group_id):
                    self.stopped.append(group_id)
                    agents = {agent["agent_id"]: agent for agent in read_live_agents(root)}
                    if agents["agent-a"]["status"] != "online":
                        raise AssertionError("pending restart must be stopped before stale presence is cleared")
                    return {"group_id": group_id, "status": "stopped", "agents": [{"agent_id": "agent-a"}]}

                def restart_group(self, group_id):
                    self.restarted.append(group_id)
                    agents = {agent["agent_id"]: agent for agent in read_live_agents(root)}
                    if agents["agent-a"]["status"] != "offline":
                        raise AssertionError("presence should be cleared before starting the group again")
                    heartbeat_live_agent(root, "agent-a", status="online")
                    return {"group_id": group_id, "status": "running", "agents": [{"agent_id": "agent-a"}]}

            supervisor = RestartSupervisor()

            session = restart_live_agent_session(
                root,
                supervisor,
                meeting_id="resident-m1",
                group_id="resident-main",
                connect_timeout_seconds=0,
            )

            self.assertEqual(session["status"], "ready")
            self.assertEqual(supervisor.stopped, ["resident-main"])
            self.assertEqual(supervisor.restarted, ["resident-main"])

    def test_restart_session_revalidates_restarted_manifest_before_ready(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")

            class RestartSupervisor:
                def snapshot_groups(self):
                    return [{"group_id": "resident-main", "status": "stopped", "agents": [{"agent_id": "agent-a"}]}]

                def restart_group(self, group_id):
                    heartbeat_live_agent(root, "agent-a", status="online")
                    return {
                        "group_id": group_id,
                        "status": "running",
                        "agents": [
                            {"agent_id": "agent-a"},
                            {"agent_id": "agent-a"},
                            {"agent_id": "agent-extra"},
                        ],
                    }

            session = restart_live_agent_session(
                root,
                RestartSupervisor(),
                meeting_id="resident-m1",
                group_id="resident-main",
                connect_timeout_seconds=0,
            )

            self.assertEqual(session["status"], "starting")
            self.assertFalse(session["process"]["ready"])
            self.assertIn("agent-a:duplicate_in_group", session["process"]["attention"])
            self.assertIn("agent-extra:extra_in_group", session["process"]["attention"])
            self.assertEqual(session["connection"]["connected"], 1)

    def test_restart_session_refuses_changed_persisted_config_before_stopping_group(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            live_agent_config = _write_live_agent_config(root, ["agent-a", "agent-a"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")

            class RestartSupervisor:
                def __init__(self) -> None:
                    self.stopped = []
                    self.restarted = []

                def snapshot_groups(self):
                    return [
                        {
                            "group_id": "resident-main",
                            "status": "running",
                            "config_path": str(live_agent_config),
                            "server": "http://127.0.0.1:8765",
                            "agents": [{"agent_id": "agent-a"}],
                        }
                    ]

                def stop_group(self, group_id):
                    self.stopped.append(group_id)
                    raise AssertionError("must validate persisted restart config before stopping")

                def restart_group(self, group_id):
                    self.restarted.append(group_id)
                    raise AssertionError("must validate persisted restart config before restarting")

            supervisor = RestartSupervisor()

            with self.assertRaises(ValueError) as raised:
                restart_live_agent_session(root, supervisor, meeting_id="resident-m1", group_id="resident-main")

            self.assertIn("Duplicate agent ids", str(raised.exception))
            self.assertEqual(supervisor.stopped, [])
            self.assertEqual(supervisor.restarted, [])
            agents = {agent["agent_id"]: agent for agent in read_live_agents(root)}
            self.assertEqual(agents["agent-a"]["status"], "online")

    def test_restart_session_refuses_missing_persisted_server_before_stopping_group(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            live_agent_config = _write_live_agent_config(root, ["agent-a"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")

            class RestartSupervisor:
                def __init__(self) -> None:
                    self.stopped = []
                    self.restarted = []

                def snapshot_groups(self):
                    return [
                        {
                            "group_id": "resident-main",
                            "status": "running",
                            "config_path": str(live_agent_config),
                            "server": "",
                            "agents": [{"agent_id": "agent-a"}],
                        }
                    ]

                def stop_group(self, group_id):
                    self.stopped.append(group_id)
                    raise AssertionError("must validate persisted server before stopping")

                def restart_group(self, group_id):
                    self.restarted.append(group_id)
                    raise AssertionError("must validate persisted server before restarting")

            supervisor = RestartSupervisor()

            with self.assertRaises(ValueError) as raised:
                restart_live_agent_session(root, supervisor, meeting_id="resident-m1", group_id="resident-main")

            self.assertIn("has no server to restart", str(raised.exception))
            self.assertEqual(supervisor.stopped, [])
            self.assertEqual(supervisor.restarted, [])
            agents = {agent["agent_id"]: agent for agent in read_live_agents(root)}
            self.assertEqual(agents["agent-a"]["status"], "online")

    def test_restart_session_refuses_missing_persisted_config_before_stopping_group(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")

            class RestartSupervisor:
                def __init__(self) -> None:
                    self.stopped = []
                    self.restarted = []

                def snapshot_groups(self):
                    return [
                        {
                            "group_id": "resident-main",
                            "status": "running",
                            "config_path": "",
                            "server": "http://127.0.0.1:8765",
                            "agents": [{"agent_id": "agent-a"}],
                        }
                    ]

                def stop_group(self, group_id):
                    self.stopped.append(group_id)
                    raise AssertionError("must validate persisted config before stopping")

                def restart_group(self, group_id):
                    self.restarted.append(group_id)
                    raise AssertionError("must validate persisted config before restarting")

            supervisor = RestartSupervisor()

            with self.assertRaises(ValueError) as raised:
                restart_live_agent_session(root, supervisor, meeting_id="resident-m1", group_id="resident-main")

            self.assertIn("has no config to restart", str(raised.exception))
            self.assertEqual(supervisor.stopped, [])
            self.assertEqual(supervisor.restarted, [])
            agents = {agent["agent_id"]: agent for agent in read_live_agents(root)}
            self.assertEqual(agents["agent-a"]["status"], "online")

    def test_restart_session_uses_supervisor_preflight_checker_before_stopping_group(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            live_agent_config = _write_live_agent_config(root, ["agent-a"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")

            class RestartSupervisor:
                def __init__(self) -> None:
                    self.stopped = []
                    self.restarted = []
                    self.preflight_paths = []

                def preflight_checker(self, config_path, *, server_override=None):
                    self.preflight_paths.append((config_path, server_override))
                    return {
                        "status": "failed",
                        "checks": [{"id": "custom", "status": "failed", "message": "custom restart gate"}],
                    }

                def snapshot_groups(self):
                    return [
                        {
                            "group_id": "resident-main",
                            "status": "running",
                            "config_path": str(live_agent_config),
                            "server": "http://127.0.0.1:8765",
                            "agents": [{"agent_id": "agent-a"}],
                        }
                    ]

                def stop_group(self, group_id):
                    self.stopped.append(group_id)
                    raise AssertionError("custom preflight must run before stopping")

                def restart_group(self, group_id):
                    self.restarted.append(group_id)
                    raise AssertionError("custom preflight must run before restarting")

            supervisor = RestartSupervisor()

            with self.assertRaises(ValueError) as raised:
                restart_live_agent_session(root, supervisor, meeting_id="resident-m1", group_id="resident-main")

            self.assertIn("custom restart gate", str(raised.exception))
            self.assertEqual(supervisor.preflight_paths, [(live_agent_config, "http://127.0.0.1:8765")])
            self.assertEqual(supervisor.stopped, [])
            self.assertEqual(supervisor.restarted, [])

    def test_restart_session_rejects_duplicate_persisted_config_after_custom_preflight_ok(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            live_agent_config = _write_live_agent_config(root, ["agent-a", "agent-a"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")

            class RestartSupervisor:
                def __init__(self) -> None:
                    self.stopped = []
                    self.restarted = []

                def preflight_checker(self, config_path, *, server_override=None):
                    return {"status": "ok", "checks": [], "agents": []}

                def snapshot_groups(self):
                    return [
                        {
                            "group_id": "resident-main",
                            "status": "running",
                            "config_path": str(live_agent_config),
                            "server": "http://127.0.0.1:8765",
                            "agents": [{"agent_id": "agent-a"}],
                        }
                    ]

                def stop_group(self, group_id):
                    self.stopped.append(group_id)
                    raise AssertionError("manifest validation must run before stopping")

                def restart_group(self, group_id):
                    self.restarted.append(group_id)
                    raise AssertionError("manifest validation must run before restarting")

            supervisor = RestartSupervisor()

            with self.assertRaises(ValueError) as raised:
                restart_live_agent_session(root, supervisor, meeting_id="resident-m1", group_id="resident-main")

            self.assertIn("duplicate agent-a", str(raised.exception))
            self.assertEqual(supervisor.stopped, [])
            self.assertEqual(supervisor.restarted, [])

    def test_restart_session_uses_read_only_process_snapshot_before_mutation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")

            class RestartSupervisor:
                def __init__(self) -> None:
                    self.snapshot_calls = 0

                def snapshot_groups(self):
                    self.snapshot_calls += 1
                    return [{"group_id": "resident-main", "status": "stopped", "agents": [{"agent_id": "agent-a"}]}]

                def list_groups(self):
                    raise AssertionError("restart prevalidation must not use mutating list_groups when snapshot_groups is available")

                def restart_group(self, group_id):
                    heartbeat_live_agent(root, "agent-a", status="online")
                    return {"group_id": group_id, "status": "running", "agents": [{"agent_id": "agent-a"}]}

            supervisor = RestartSupervisor()

            session = restart_live_agent_session(
                root,
                supervisor,
                meeting_id="resident-m1",
                group_id="resident-main",
                connect_timeout_seconds=0,
            )

            self.assertEqual(session["status"], "ready")
            self.assertEqual(supervisor.snapshot_calls, 1)

    def test_restart_session_reports_starting_without_fresh_presence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")

            class RestartSupervisor:
                def snapshot_groups(self):
                    return [{"group_id": "resident-main", "status": "stopped", "agents": [{"agent_id": "agent-a"}]}]

                def stop_group(self, group_id):
                    raise AssertionError("stopped group should not be stopped before restart")

                def restart_group(self, group_id):
                    return {"group_id": group_id, "status": "running", "agents": [{"agent_id": "agent-a"}]}

            session = restart_live_agent_session(
                root,
                RestartSupervisor(),
                meeting_id="resident-m1",
                group_id="resident-main",
                connect_timeout_seconds=0,
            )

            self.assertEqual(session["status"], "starting")
            self.assertEqual(session["offline"]["offline"], 1)
            self.assertEqual(session["connection"]["attention"], ["agent-a:offline"])
            agents = {agent["agent_id"]: agent for agent in read_live_agents(root)}
            self.assertEqual(agents["agent-a"]["status"], "offline")

    def test_restart_session_refuses_manifest_mismatch_before_restart(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect", "critic"])
            agent_config = _write_agent_config(root, ["agent-a", "agent-b"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")

            class RestartSupervisor:
                def __init__(self) -> None:
                    self.stopped = []
                    self.restarted = []

                def snapshot_groups(self):
                    return [
                        {
                            "group_id": "resident-main",
                            "status": "running",
                            "agents": [{"agent_id": "agent-a"}, {"agent_id": "agent-x"}],
                        }
                    ]

                def stop_group(self, group_id):
                    self.stopped.append(group_id)
                    return {"group_id": group_id, "status": "stopped", "agents": [{"agent_id": "agent-a"}]}

                def restart_group(self, group_id):
                    self.restarted.append(group_id)
                    return {"group_id": group_id, "status": "running", "agents": [{"agent_id": "agent-a"}]}

            supervisor = RestartSupervisor()

            with self.assertRaises(ValueError) as raised:
                restart_live_agent_session(root, supervisor, meeting_id="resident-m1", group_id="resident-main")

            self.assertIn("does not match meeting agents", str(raised.exception))
            self.assertEqual(supervisor.stopped, [])
            self.assertEqual(supervisor.restarted, [])
            agents = {agent["agent_id"]: agent for agent in read_live_agents(root)}
            self.assertEqual(agents["agent-a"]["status"], "online")

    def test_restart_session_refuses_group_owned_by_another_meeting_before_restart(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")

            class RestartSupervisor:
                def __init__(self) -> None:
                    self.stopped = []
                    self.restarted = []

                def snapshot_groups(self):
                    return [
                        {
                            "group_id": "resident-main",
                            "status": "running",
                            "meeting_id": "other-meeting",
                            "agents": [{"agent_id": "agent-a"}],
                        }
                    ]

                def stop_group(self, group_id):
                    self.stopped.append(group_id)
                    return {"group_id": group_id, "status": "stopped", "agents": [{"agent_id": "agent-a"}]}

                def restart_group(self, group_id):
                    self.restarted.append(group_id)
                    return {"group_id": group_id, "status": "running", "agents": [{"agent_id": "agent-a"}]}

            supervisor = RestartSupervisor()

            with self.assertRaises(ValueError) as raised:
                restart_live_agent_session(root, supervisor, meeting_id="resident-m1", group_id="resident-main")

            self.assertIn("belongs to a different meeting", str(raised.exception))
            self.assertEqual(supervisor.stopped, [])
            self.assertEqual(supervisor.restarted, [])
            agents = {agent["agent_id"]: agent for agent in read_live_agents(root)}
            self.assertEqual(agents["agent-a"]["status"], "online")

    def test_restart_session_refuses_duplicate_manifest_agent_before_restart(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")

            class RestartSupervisor:
                def __init__(self) -> None:
                    self.restarted = []

                def snapshot_groups(self):
                    return [
                        {
                            "group_id": "resident-main",
                            "status": "stopped",
                            "agents": [{"agent_id": "agent-a"}, {"agent_id": "agent-a"}],
                        }
                    ]

                def restart_group(self, group_id):
                    self.restarted.append(group_id)
                    return {"group_id": group_id, "status": "running", "agents": [{"agent_id": "agent-a"}]}

            supervisor = RestartSupervisor()

            with self.assertRaises(ValueError) as raised:
                restart_live_agent_session(root, supervisor, meeting_id="resident-m1", group_id="resident-main")

            self.assertIn("duplicate agent-a", str(raised.exception))
            self.assertEqual(supervisor.restarted, [])
            agents = {agent["agent_id"]: agent for agent in read_live_agents(root)}
            self.assertEqual(agents["agent-a"]["status"], "online")

    def test_restart_session_leaves_wrong_meeting_roster_row_untouched(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            connect_live_agent(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "meeting_id": "other-meeting",
                    "engagement_mode": "moderator_called",
                    "status": "online",
                    "capabilities": ["room_chat", "official_turn"],
                },
            )

            class RestartSupervisor:
                def snapshot_groups(self):
                    return [{"group_id": "resident-main", "status": "stopped", "agents": [{"agent_id": "agent-a"}]}]

                def restart_group(self, group_id):
                    return {"group_id": group_id, "status": "running", "agents": [{"agent_id": "agent-a"}]}

            session = restart_live_agent_session(
                root,
                RestartSupervisor(),
                meeting_id="resident-m1",
                group_id="resident-main",
                connect_timeout_seconds=0,
            )

            self.assertEqual(session["status"], "starting")
            self.assertEqual(session["offline"]["attention"], ["agent-a:wrong_meeting"])
            self.assertEqual(session["connection"]["attention"], ["agent-a:wrong_meeting"])
            agents = {agent["agent_id"]: agent for agent in read_live_agents(root)}
            self.assertEqual(agents["agent-a"]["meeting_id"], "other-meeting")
            self.assertEqual(agents["agent-a"]["status"], "online")

    def test_recover_session_waits_for_fresh_presence_after_clearing_stale_roster(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")

            class RecoverSupervisor:
                def __init__(self) -> None:
                    self.recovered = []

                def snapshot_groups(self):
                    return [{"group_id": "resident-main", "status": "unknown", "agents": [{"agent_id": "agent-a"}]}]

                def recover_group(self, group_id):
                    self.recovered.append(group_id)
                    agents = {agent["agent_id"]: agent for agent in read_live_agents(root)}
                    if agents["agent-a"]["status"] != "offline":
                        raise AssertionError("recover must clear stale presence before starting the group again")
                    return {"group_id": group_id, "status": "running", "agents": [{"agent_id": "agent-a"}]}

            supervisor = RecoverSupervisor()

            session = recover_live_agent_session(
                root,
                supervisor,
                meeting_id="resident-m1",
                group_id="resident main",
                connect_timeout_seconds=0,
            )

            self.assertEqual(supervisor.recovered, ["resident-main"])
            self.assertEqual(session["status"], "starting")
            self.assertEqual(session["offline"]["offline"], 1)
            self.assertEqual(session["connection"]["attention"], ["agent-a:offline"])
            agents = {agent["agent_id"]: agent for agent in read_live_agents(root)}
            self.assertEqual(agents["agent-a"]["status"], "offline")

    def test_recover_session_refuses_binding_with_missing_provider_config_before_mutating_group(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            result = start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            meeting = dict(result["meeting"])
            meeting["agent_bindings"] = [
                {
                    "role_id": "architect",
                    "agent_id": "agent-a",
                    "provider_id": "missing-provider",
                }
            ]
            meeting["provider_configs"] = {}
            write_live_state(root / "meetings" / "resident-m1", meeting)
            heartbeat_live_agent(root, "agent-a", status="online")

            class RecoverSupervisor:
                def __init__(self) -> None:
                    self.recovered = []

                def snapshot_groups(self):
                    return [
                        {
                            "group_id": "resident-main",
                            "status": "unknown",
                            "meeting_id": "resident-m1",
                            "agents": [
                                {
                                    "agent_id": "agent-a",
                                    "provider_kind": "local_cli",
                                    "connection_kind": "local_cli",
                                }
                            ],
                        }
                    ]

                def recover_group(self, group_id):
                    self.recovered.append(group_id)
                    raise AssertionError("recover must fail before recovering the group")

            supervisor = RecoverSupervisor()

            with self.assertRaises(ValueError) as raised:
                recover_live_agent_session(root, supervisor, meeting_id="resident-m1", group_id="resident-main")

        self.assertIn("binding_provider_missing", str(raised.exception))
        self.assertNotIn("missing-provider", str(raised.exception))
        self.assertEqual(supervisor.recovered, [])

    def test_recover_session_reports_ready_only_after_recovered_agents_heartbeat(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect", "critic"])
            agent_config = _write_agent_config(root, ["agent-a", "agent-b"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")
            heartbeat_live_agent(root, "agent-b", status="working")

            class RecoverSupervisor:
                def __init__(self) -> None:
                    self.recovered = []

                def snapshot_groups(self):
                    return [
                        {
                            "group_id": "resident-main",
                            "status": "unknown",
                            "agents": [{"agent_id": "agent-a"}, {"agent_id": "agent-b"}],
                        }
                    ]

                def recover_group(self, group_id):
                    self.recovered.append(group_id)
                    agents = {agent["agent_id"]: agent for agent in read_live_agents(root)}
                    if agents["agent-a"]["status"] != "offline" or agents["agent-b"]["status"] != "offline":
                        raise AssertionError("recover must clear stale presence before starting the group again")
                    heartbeat_live_agent(root, "agent-a", status="online")
                    heartbeat_live_agent(root, "agent-b", status="working")
                    return {"group_id": group_id, "status": "running", "agents": [{"agent_id": "agent-a"}, {"agent_id": "agent-b"}]}

            supervisor = RecoverSupervisor()

            session = recover_live_agent_session(
                root,
                supervisor,
                meeting_id="resident-m1",
                group_id="resident-main",
                connect_timeout_seconds=0,
            )

            self.assertEqual(supervisor.recovered, ["resident-main"])
            self.assertEqual(session["status"], "ready")
            self.assertEqual(session["offline"]["offline"], 2)
            self.assertEqual(session["connection"]["connected"], 2)

    def test_recover_session_refuses_manifest_mismatch_before_recovery(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect", "critic"])
            agent_config = _write_agent_config(root, ["agent-a", "agent-b"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")

            class RecoverSupervisor:
                def __init__(self) -> None:
                    self.recovered = []

                def snapshot_groups(self):
                    return [
                        {
                            "group_id": "resident-main",
                            "status": "unknown",
                            "agents": [{"agent_id": "agent-a"}, {"agent_id": "agent-x"}],
                        }
                    ]

                def recover_group(self, group_id):
                    self.recovered.append(group_id)
                    return {"group_id": group_id, "status": "running", "agents": [{"agent_id": "agent-a"}]}

            supervisor = RecoverSupervisor()

            with self.assertRaises(ValueError) as raised:
                recover_live_agent_session(root, supervisor, meeting_id="resident-m1", group_id="resident-main")

            self.assertIn("does not match meeting agents", str(raised.exception))
            self.assertEqual(supervisor.recovered, [])
            agents = {agent["agent_id"]: agent for agent in read_live_agents(root)}
            self.assertEqual(agents["agent-a"]["status"], "online")

    def test_recover_session_refuses_running_group_before_clearing_stale_roster(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")

            class RecoverSupervisor:
                def __init__(self) -> None:
                    self.recovered = []

                def snapshot_groups(self):
                    return [{"group_id": "resident-main", "status": "running", "agents": [{"agent_id": "agent-a"}]}]

                def recover_group(self, group_id):
                    self.recovered.append(group_id)
                    raise AssertionError("running group must be refused before recover")

            supervisor = RecoverSupervisor()

            with self.assertRaises(ValueError) as raised:
                recover_live_agent_session(root, supervisor, meeting_id="resident-m1", group_id="resident-main")

            self.assertIn("already running", str(raised.exception))
            self.assertEqual(supervisor.recovered, [])
            agents = {agent["agent_id"]: agent for agent in read_live_agents(root)}
            self.assertEqual(agents["agent-a"]["status"], "online")

    def test_recover_session_refuses_group_without_server_before_clearing_stale_roster(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")

            class RecoverSupervisor:
                def __init__(self) -> None:
                    self.recovered = []

                def snapshot_groups(self):
                    return [
                        {
                            "group_id": "resident-main",
                            "status": "unknown",
                            "server": "",
                            "config_path": "configs/live-agents.example.json",
                            "agents": [{"agent_id": "agent-a"}],
                        }
                    ]

                def recover_group(self, group_id):
                    self.recovered.append(group_id)
                    raise AssertionError("missing server must be refused before recover")

            supervisor = RecoverSupervisor()

            with self.assertRaises(ValueError) as raised:
                recover_live_agent_session(root, supervisor, meeting_id="resident-m1", group_id="resident-main")

            self.assertIn("has no server to recover", str(raised.exception))
            self.assertEqual(supervisor.recovered, [])
            agents = {agent["agent_id"]: agent for agent in read_live_agents(root)}
            self.assertEqual(agents["agent-a"]["status"], "online")

    def test_recover_session_refuses_missing_config_before_clearing_stale_roster(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")

            class RecoverSupervisor:
                def __init__(self) -> None:
                    self.recovered = []

                def snapshot_groups(self):
                    return [
                        {
                            "group_id": "resident-main",
                            "status": "unknown",
                            "server": "http://127.0.0.1:8765",
                            "config_path": str(root / "missing-live-agents.json"),
                            "agents": [{"agent_id": "agent-a"}],
                        }
                    ]

                def recover_group(self, group_id):
                    self.recovered.append(group_id)
                    raise AssertionError("missing config must be refused before recover")

            supervisor = RecoverSupervisor()

            with self.assertRaises(ValueError) as raised:
                recover_live_agent_session(root, supervisor, meeting_id="resident-m1", group_id="resident-main")

            self.assertIn("has no recoverable config", str(raised.exception))
            self.assertEqual(supervisor.recovered, [])
            agents = {agent["agent_id"]: agent for agent in read_live_agents(root)}
            self.assertEqual(agents["agent-a"]["status"], "online")

    def test_recover_session_refuses_changed_persisted_config_before_clearing_stale_roster(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            live_agent_config = _write_live_agent_config(root, ["agent-a", "agent-a"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")

            class RecoverSupervisor:
                def __init__(self) -> None:
                    self.recovered = []

                def snapshot_groups(self):
                    return [
                        {
                            "group_id": "resident-main",
                            "status": "unknown",
                            "server": "http://127.0.0.1:8765",
                            "config_path": str(live_agent_config),
                            "agents": [{"agent_id": "agent-a"}],
                        }
                    ]

                def recover_group(self, group_id):
                    self.recovered.append(group_id)
                    raise AssertionError("must validate persisted recover config before recovery")

            supervisor = RecoverSupervisor()

            with self.assertRaises(ValueError) as raised:
                recover_live_agent_session(root, supervisor, meeting_id="resident-m1", group_id="resident-main")

            self.assertIn("Duplicate agent ids", str(raised.exception))
            self.assertEqual(supervisor.recovered, [])
            agents = {agent["agent_id"]: agent for agent in read_live_agents(root)}
            self.assertEqual(agents["agent-a"]["status"], "online")

    def test_recover_session_uses_supervisor_preflight_checker_before_clearing_stale_roster(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            live_agent_config = _write_live_agent_config(root, ["agent-a"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")

            class RecoverSupervisor:
                def __init__(self) -> None:
                    self.recovered = []
                    self.preflight_paths = []

                def preflight_checker(self, config_path, *, server_override=None):
                    self.preflight_paths.append((config_path, server_override))
                    return {
                        "status": "failed",
                        "checks": [{"id": "custom", "status": "failed", "message": "custom recover gate"}],
                    }

                def snapshot_groups(self):
                    return [
                        {
                            "group_id": "resident-main",
                            "status": "unknown",
                            "server": "http://127.0.0.1:8765",
                            "config_path": str(live_agent_config),
                            "agents": [{"agent_id": "agent-a"}],
                        }
                    ]

                def recover_group(self, group_id):
                    self.recovered.append(group_id)
                    raise AssertionError("custom preflight must run before recovery")

            supervisor = RecoverSupervisor()

            with self.assertRaises(ValueError) as raised:
                recover_live_agent_session(root, supervisor, meeting_id="resident-m1", group_id="resident-main")

            self.assertIn("custom recover gate", str(raised.exception))
            self.assertEqual(supervisor.preflight_paths, [(live_agent_config, "http://127.0.0.1:8765")])
            self.assertEqual(supervisor.recovered, [])
            agents = {agent["agent_id"]: agent for agent in read_live_agents(root)}
            self.assertEqual(agents["agent-a"]["status"], "online")

    def test_recover_session_rejects_duplicate_persisted_config_after_custom_preflight_ok(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            live_agent_config = _write_live_agent_config(root, ["agent-a", "agent-a"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")

            class RecoverSupervisor:
                def __init__(self) -> None:
                    self.recovered = []

                def preflight_checker(self, config_path, *, server_override=None):
                    return {"status": "ok", "checks": [], "agents": []}

                def snapshot_groups(self):
                    return [
                        {
                            "group_id": "resident-main",
                            "status": "unknown",
                            "server": "http://127.0.0.1:8765",
                            "config_path": str(live_agent_config),
                            "agents": [{"agent_id": "agent-a"}],
                        }
                    ]

                def recover_group(self, group_id):
                    self.recovered.append(group_id)
                    raise AssertionError("manifest validation must run before recovery")

            supervisor = RecoverSupervisor()

            with self.assertRaises(ValueError) as raised:
                recover_live_agent_session(root, supervisor, meeting_id="resident-m1", group_id="resident-main")

            self.assertIn("duplicate agent-a", str(raised.exception))
            self.assertEqual(supervisor.recovered, [])
            agents = {agent["agent_id"]: agent for agent in read_live_agents(root)}
            self.assertEqual(agents["agent-a"]["status"], "online")

    def test_recover_session_leaves_wrong_meeting_roster_row_untouched(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            connect_live_agent(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "meeting_id": "other-meeting",
                    "engagement_mode": "moderator_called",
                    "status": "online",
                    "capabilities": ["room_chat", "official_turn"],
                },
            )

            class RecoverSupervisor:
                def snapshot_groups(self):
                    return [{"group_id": "resident-main", "status": "unknown", "agents": [{"agent_id": "agent-a"}]}]

                def recover_group(self, group_id):
                    return {"group_id": group_id, "status": "running", "agents": [{"agent_id": "agent-a"}]}

            session = recover_live_agent_session(
                root,
                RecoverSupervisor(),
                meeting_id="resident-m1",
                group_id="resident-main",
                connect_timeout_seconds=0,
            )

            self.assertEqual(session["status"], "starting")
            self.assertEqual(session["offline"]["attention"], ["agent-a:wrong_meeting"])
            self.assertEqual(session["connection"]["attention"], ["agent-a:wrong_meeting"])
            agents = {agent["agent_id"]: agent for agent in read_live_agents(root)}
            self.assertEqual(agents["agent-a"]["meeting_id"], "other-meeting")
            self.assertEqual(agents["agent-a"]["status"], "online")

    def test_check_session_reports_ready_without_mutating_runtime_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect", "critic"])
            agent_config = _write_agent_config(root, ["agent-a", "agent-b"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")
            heartbeat_live_agent(root, "agent-b", status="working")
            before_roster = (root / "live_agents.json").read_text(encoding="utf-8")

            class CheckSupervisor:
                def __init__(self) -> None:
                    self.started = []
                    self.stopped = []

                def snapshot_groups(self):
                    return [
                        {
                            "group_id": "resident-main",
                            "status": "running",
                            "config_path": str(root / "secret-live-agents.json"),
                            "log_tail": "secret provider output",
                            "agents": [{"agent_id": "agent-a"}, {"agent_id": "agent-b"}],
                        }
                    ]

                def start_group(self, **kwargs):
                    self.started.append(kwargs)
                    raise AssertionError("check must not start groups")

                def stop_group(self, group_id):
                    self.stopped.append(group_id)
                    raise AssertionError("check must not stop groups")

            supervisor = CheckSupervisor()

            session = check_live_agent_session(
                root,
                supervisor,
                meeting_id="resident-m1",
                group_id="resident main",
            )

            self.assertEqual(session["status"], "ready")
            self.assertEqual(session["meeting_id"], "resident-m1")
            self.assertEqual(session["group_id"], "resident-main")
            self.assertEqual(session["connection"]["connected"], 2)
            self.assertEqual(session["process"]["status"], "running")
            self.assertEqual(session["process"]["attention"], [])
            self.assertEqual(supervisor.started, [])
            self.assertEqual(supervisor.stopped, [])
            self.assertEqual((root / "live_agents.json").read_text(encoding="utf-8"), before_roster)
            serialized = json.dumps(session, ensure_ascii=False)
            self.assertNotIn("secret-live-agents", serialized)
            self.assertNotIn("secret provider output", serialized)

    def test_check_session_requires_presence_after_process_start(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(
                root,
                "agent-a",
                status="working",
                now=datetime(2999, 1, 1, 0, 0, tzinfo=UTC),
            )
            before_roster = (root / "live_agents.json").read_text(encoding="utf-8")

            class CheckSupervisor:
                def snapshot_groups(self):
                    return [
                        {
                            "group_id": "resident-main",
                            "status": "running",
                            "started_at": "2999-01-01T00:01:00+00:00",
                            "agents": [{"agent_id": "agent-a"}],
                        }
                    ]

            session = check_live_agent_session(
                root,
                CheckSupervisor(),
                meeting_id="resident-m1",
                group_id="resident-main",
            )

            self.assertEqual(session["status"], "degraded")
            self.assertEqual(session["connection"]["connected"], 0)
            self.assertEqual(session["connection"]["attention"], ["agent-a:not_reconnected"])
            self.assertEqual((root / "live_agents.json").read_text(encoding="utf-8"), before_roster)

    def test_check_session_does_not_count_provider_mismatch_presence_as_connected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"], provider_kind="local_cli")
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            connect_live_agent(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Manual Agent A",
                    "provider_kind": "manual",
                    "connection_kind": "manual",
                    "meeting_id": "resident-m1",
                    "status": "online",
                },
            )

            class CheckSupervisor:
                def snapshot_groups(self):
                    return [
                        {
                            "group_id": "resident-main",
                            "status": "running",
                            "agents": [
                                {
                                    "agent_id": "agent-a",
                                    "provider_kind": "local_cli",
                                    "connection_kind": "local_cli",
                                }
                            ],
                        }
                    ]

            session = check_live_agent_session(
                root,
                CheckSupervisor(),
                meeting_id="resident-m1",
                group_id="resident-main",
            )

            self.assertEqual(session["status"], "degraded")
            self.assertEqual(session["connection"]["connected"], 0)
            self.assertEqual(session["connection"]["attention"], ["agent-a:provider_kind_mismatch"])

    def test_check_session_uses_read_only_process_snapshot_when_available(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")

            class CheckSupervisor:
                def __init__(self) -> None:
                    self.snapshot_calls = 0

                def snapshot_groups(self):
                    self.snapshot_calls += 1
                    return [{"group_id": "resident-main", "status": "running", "agents": [{"agent_id": "agent-a"}]}]

                def list_groups(self):
                    raise AssertionError("check must not use mutating list_groups when snapshot_groups is available")

            supervisor = CheckSupervisor()

            session = check_live_agent_session(
                root,
                supervisor,
                meeting_id="resident-m1",
                group_id="resident-main",
            )

            self.assertEqual(session["status"], "ready")
            self.assertEqual(supervisor.snapshot_calls, 1)

    def test_check_session_reports_degraded_mismatch_without_reassigning_wrong_meeting_roster(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            connect_live_agent(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "meeting_id": "other-meeting",
                    "engagement_mode": "moderator_called",
                    "status": "online",
                    "capabilities": ["room_chat", "official_turn"],
                },
            )
            before_roster = (root / "live_agents.json").read_text(encoding="utf-8")

            class CheckSupervisor:
                def snapshot_groups(self):
                    return [
                        {
                            "group_id": "resident-main",
                            "status": "running",
                            "agents": [{"agent_id": "agent-a"}, {"agent_id": "agent-x"}],
                        }
                    ]

            session = check_live_agent_session(
                root,
                CheckSupervisor(),
                meeting_id="resident-m1",
                group_id="resident-main",
            )

            self.assertEqual(session["status"], "degraded")
            self.assertEqual(session["connection"]["attention"], ["agent-a:wrong_meeting"])
            self.assertIn("agent-x:extra_in_group", session["process"]["attention"])
            self.assertEqual((root / "live_agents.json").read_text(encoding="utf-8"), before_roster)
            agents = {agent["agent_id"]: agent for agent in read_live_agents(root)}
            self.assertEqual(agents["agent-a"]["meeting_id"], "other-meeting")
            self.assertEqual(agents["agent-a"]["status"], "online")

    def test_check_session_reports_degraded_when_process_group_belongs_to_another_meeting(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")
            before_roster = (root / "live_agents.json").read_text(encoding="utf-8")

            class CheckSupervisor:
                def snapshot_groups(self):
                    return [
                        {
                            "group_id": "resident-main",
                            "status": "running",
                            "meeting_id": "other-meeting",
                            "agents": [{"agent_id": "agent-a"}],
                        }
                    ]

            session = check_live_agent_session(
                root,
                CheckSupervisor(),
                meeting_id="resident-m1",
                group_id="resident-main",
            )

            self.assertEqual(session["status"], "degraded")
            self.assertEqual(session["group"]["meeting_id"], "other-meeting")
            self.assertIn("group:wrong_meeting", session["process"]["attention"])
            self.assertEqual((root / "live_agents.json").read_text(encoding="utf-8"), before_roster)

    def test_check_session_reports_degraded_duplicate_manifest_agent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )
            heartbeat_live_agent(root, "agent-a", status="online")

            class CheckSupervisor:
                def snapshot_groups(self):
                    return [
                        {
                            "group_id": "resident-main",
                            "status": "running",
                            "agents": [{"agent_id": "agent-a"}, {"agent_id": "agent-a"}],
                        }
                    ]

            session = check_live_agent_session(
                root,
                CheckSupervisor(),
                meeting_id="resident-m1",
                group_id="resident-main",
            )

            self.assertEqual(session["status"], "degraded")
            self.assertIn("agent-a:duplicate_in_group", session["process"]["attention"])

    def test_check_session_requires_existing_meeting_and_explicit_group_id(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council_config = _write_council_config(root, ["architect"])
            agent_config = _write_agent_config(root, ["agent-a"])
            start_live_agent_meeting(
                root,
                council_config_path=council_config,
                agent_config_path=agent_config,
                meeting_id="resident-m1",
            )

            class CheckSupervisor:
                def list_groups(self):
                    return []

            with self.assertRaises(ValueError) as raised:
                check_live_agent_session(root, CheckSupervisor(), meeting_id="resident-m1", group_id=" ")
            self.assertIn("Live agent group id is required", str(raised.exception))

            with self.assertRaises(ValueError) as raised:
                check_live_agent_session(root, CheckSupervisor(), meeting_id="missing-meeting", group_id="resident-main")
            self.assertIn("Meeting missing-meeting was not found", str(raised.exception))


def _write_council_config(root: Path, role_ids: list[str]) -> Path:
    path = root / "council.json"
    path.write_text(
        json.dumps(
            {
                "topic": "resident session",
                "question": "Can resident agents start as one session?",
                "roles": [
                    {
                        "id": role_id,
                        "display_name": role_id.title(),
                        "lens": f"{role_id} lens",
                        "research_focus": f"{role_id} focus",
                    }
                    for role_id in role_ids
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def _write_agent_config(root: Path, agent_ids: list[str], *, provider_kind: str = "local_cli") -> Path:
    path = root / "agents.json"
    roles = ["architect", "critic", "tester", "operator"]
    path.write_text(
        json.dumps(
            {
                "providers": [
                    {
                        "id": "local-cli",
                        "kind": provider_kind,
                        "display_name": "Local CLI",
                        "endpoint": "http://bridge.local" if provider_kind == "remote_http_bridge" else None,
                        "command": [sys.executable, "-c", "print('ok')"],
                    }
                ],
                "permission_profiles": [
                    {
                        "id": "meeting_readonly",
                        "meeting_read": True,
                        "lobby_chat": True,
                        "official_turn": True,
                    }
                ],
                "agent_bindings": [
                    {
                        "agent_id": agent_id,
                        "role_id": role_id,
                        "provider_id": "local-cli",
                        "permission_profile_id": "meeting_readonly",
                        "engagement_mode": "always",
                    }
                    for agent_id, role_id in zip(agent_ids, roles, strict=False)
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def _write_agent_config_with_character_mode(root: Path) -> Path:
    path = root / "agents-character.json"
    path.write_text(
        json.dumps(
            {
                "providers": [
                    {
                        "id": "local-cli",
                        "kind": "local_cli",
                        "display_name": "Local CLI",
                        "command": [sys.executable, "-c", "print('ok')"],
                    }
                ],
                "permission_profiles": [
                    {
                        "id": "meeting_readonly",
                        "meeting_read": True,
                        "lobby_chat": True,
                        "official_turn": True,
                    }
                ],
                "agent_bindings": [
                    {
                        "agent_id": "agent-a",
                        "role_id": "architect",
                        "provider_id": "local-cli",
                        "permission_profile_id": "meeting_readonly",
                        "persona_card_id": "tsukishiro-yanagi",
                        "character_mode": "work_speech_only",
                        "first_message_index": 1,
                        "persona_variables": {"mood": "dry", "nested": {"ignored": True}},
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def _write_live_agent_config(
    root: Path,
    agent_ids: list[str],
    *,
    meeting_id: str = "",
    provider_kind: str = "local_cli",
    connection_kind: str = "local_cli",
    endpoint: str = "",
    auth_ref: str = "",
    persona_card_id: str = "",
    persona_path: str = "",
    character_mode: str = "",
) -> Path:
    path = root / "live-agents.json"
    path.write_text(
        json.dumps(
            {
                "server": "http://127.0.0.1:8765",
                "agents": [
                    {
                        "agent_id": agent_id,
                        "display_name": agent_id,
                        "provider_kind": provider_kind,
                        "connection_kind": connection_kind,
                        "endpoint": endpoint,
                        "auth_ref": auth_ref,
                        "meeting_id": meeting_id,
                        "engagement_mode": "moderator_called",
                        "persona_card_id": persona_card_id,
                        "persona_path": persona_path,
                        "character_mode": character_mode,
                        "command": [sys.executable, "-c", "print('ok')"],
                    }
                    for agent_id in agent_ids
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path
