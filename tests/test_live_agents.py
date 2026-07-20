import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agentsassemble.legacy.live_agent.runtime.context import live_agent_context_contract
from agentsassemble.live_agents import (
    connect_live_agent,
    heartbeat_live_agent,
    read_live_agents,
    update_live_agent_engagement,
    update_live_agent_options,
)
from agentsassemble.providers.sandbox_launcher import NoSandboxLauncher, sandbox_launcher_for


class LiveAgentPresenceTests(unittest.TestCase):
    def test_context_contract_labels_match_actual_connection_semantics(self):
        cases = [
            ("codex_live_session", "codex_resume", "codex_exec_resume", "provider_managed_resume", "codex_readonly"),
            ("codex_live_session", "live_session", "codex_exec_resume", "provider_managed_resume", "codex_readonly"),
            ("codex", "codex_resume", "codex_exec_resume", "provider_managed_resume", "codex_readonly"),
            ("grok_live_session", "live_session", "grok_session_resume", "provider_managed_resume", "advisory"),
            ("", "", "manual_room_loop", "external_owner_managed", "advisory"),
            ("claude_code", "live_session", "jsonl_live_session", "process_lifetime", "advisory"),
            ("local_cli", "live_session", "jsonl_live_session", "process_lifetime", "advisory"),
            ("remote_http_bridge", "remote_bridge", "remote_bridge_room_loop", "remote_owner_managed", "advisory"),
        ]

        for provider_kind, connection_kind, join_semantics, context_durability, sandbox_enforcement in cases:
            with self.subTest(provider_kind=provider_kind, connection_kind=connection_kind):
                contract = live_agent_context_contract(provider_kind, connection_kind)
                self.assertEqual(contract["join_semantics"], join_semantics)
                self.assertEqual(contract["context_durability"], context_durability)
                self.assertEqual(contract["sandbox_enforcement"], sandbox_enforcement)

    def test_context_contract_separates_runner_residency_from_provider_residency(self):
        codex = live_agent_context_contract("codex_live_session", "live_session")
        self.assertEqual(codex["execution_mode"], "baseline_call_resume")
        self.assertEqual(codex["runner_residency"], "resident_polling_runner")
        self.assertEqual(codex["provider_residency"], "per_turn_exec_resume")
        self.assertIn("exec/resume", codex["execution_summary"])

        terminal = live_agent_context_contract("claude_code", "terminal_session")
        self.assertEqual(terminal["execution_mode"], "persistent")
        self.assertEqual(terminal["runner_residency"], "resident_process")
        self.assertEqual(terminal["provider_residency"], "persistent_provider_channel")

        remote = live_agent_context_contract("remote_http_bridge", "remote_bridge")
        self.assertEqual(remote["execution_mode"], "tool_loop_unverified")
        self.assertFalse(remote["provider_persistent"])
        self.assertIn("not been verified", remote["tool_loop_unverified_reason"])

    def test_connect_live_agent_can_override_join_semantics_for_runtime_comparison(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            agent = connect_live_agent(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "provider_kind": "codex_live_session",
                    "connection_kind": "live_session",
                    "join_semantics": "runtime_managed_room_turn",
                },
                now=datetime(2026, 5, 17, 12, 0, tzinfo=UTC),
            )
            visible = read_live_agents(root, now=datetime(2026, 5, 17, 12, 0, tzinfo=UTC))[0]

        self.assertEqual(agent["join_semantics"], "runtime_managed_room_turn")
        self.assertEqual(agent["execution_mode"], "runtime_managed_room_turn")
        self.assertEqual(visible["join_semantics"], "runtime_managed_room_turn")
        self.assertEqual(visible["execution_mode"], "runtime_managed_room_turn")

    def test_sandbox_launcher_declares_current_enforcement_without_claiming_os_sandbox(self):
        self.assertEqual(NoSandboxLauncher().enforcement, "advisory")
        self.assertEqual(sandbox_launcher_for("codex_live_session", "live_session").enforcement, "codex_readonly")
        self.assertEqual(sandbox_launcher_for("codex", "codex_resume").enforcement, "codex_readonly")
        self.assertEqual(
            sandbox_launcher_for("codex_live_session", "live_session").command(["codex"]),
            ["codex", "exec", "--sandbox", "read-only", "--ignore-user-config", "--ignore-rules"],
        )
        self.assertEqual(sandbox_launcher_for("claude_code", "terminal_session").enforcement, "advisory")
        self.assertNotEqual(sandbox_launcher_for("claude_code", "terminal_session").enforcement, "os_sandboxed")

    def test_connect_live_agent_upserts_sanitized_presence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            now = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)

            agent = connect_live_agent(
                root,
                {
                    "agent_id": " claude-live\n",
                    "display_name": "Claude\nCode",
                    "provider_kind": "claude_code",
                    "connection_kind": "local_cli",
                    "session_id": "claude-session-1",
                    "engagement_mode": "mentioned",
                    "meeting_id": "m1",
                    "workspace_path": "/Users/seinel/Projects/AgentCouncil",
                    "capabilities": ["room_chat", "mentions"],
                },
                now=now,
            )

            self.assertEqual(agent["agent_id"], "claude-live")
            self.assertEqual(agent["display_name"], "Claude Code")
            self.assertEqual(agent["provider_kind"], "claude_code")
            self.assertEqual(agent["connection_kind"], "local_cli")
            self.assertEqual(agent["sandbox_enforcement"], "advisory")
            self.assertEqual(agent["status"], "online")
            self.assertEqual(agent["last_seen_at"], "2026-05-17T12:00:00+00:00")
            self.assertEqual(agent["workspace_path"], "/Users/seinel/Projects/AgentCouncil")
            self.assertEqual(agent["capabilities"], ["room_chat", "mentions"])
            visible = read_live_agents(root, now=now)[0]
            self.assertEqual({key: visible[key] for key in agent}, agent)
            self.assertEqual(visible["heartbeat_age_seconds"], 0)
            self.assertEqual(visible["stale_after_seconds"], 180)

    def test_update_live_agent_options_edits_permission_and_fast_and_leaves_others(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            connect_live_agent(
                root,
                {
                    "agent_id": "codex-live",
                    "display_name": "Codex",
                    "provider_kind": "codex_live_session",
                    "connection_kind": "live_session",
                    "permission_option": "read-only",
                },
            )
            updated = update_live_agent_options(
                root, "codex-live", permission_option="danger-full-access", fast_mode=True
            )
            self.assertEqual(updated["permission_option"], "danger-full-access")
            self.assertIs(updated["fast_mode"], True)

            # Omitting a field (None) leaves it unchanged.
            again = update_live_agent_options(root, "codex-live", fast_mode=False)
            self.assertEqual(again["permission_option"], "danger-full-access")
            self.assertIs(again["fast_mode"], False)

            with self.assertRaisesRegex(ValueError, "was not found"):
                update_live_agent_options(root, "missing", fast_mode=True)

    def test_read_live_agents_marks_quiet_online_agents_stale(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            started = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)
            later = started + timedelta(seconds=181)

            connect_live_agent(root, {"agent_id": "remote-claude", "display_name": "Remote Claude"}, now=started)

            agents = read_live_agents(root, now=later, stale_after_seconds=180)

            self.assertEqual(agents[0]["agent_id"], "remote-claude")
            self.assertEqual(agents[0]["status"], "stale")
            self.assertEqual(agents[0]["heartbeat_age_seconds"], 181)
            self.assertEqual(agents[0]["stale_after_seconds"], 180)

    def test_read_live_agents_reports_boundary_age_that_matches_stale_status(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            started = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)
            later = started + timedelta(seconds=180, microseconds=500000)

            connect_live_agent(root, {"agent_id": "edge-agent", "display_name": "Edge Agent"}, now=started)

            agents = read_live_agents(root, now=later, stale_after_seconds=180)

        self.assertEqual(agents[0]["status"], "stale")
        self.assertEqual(agents[0]["heartbeat_age_seconds"], 181)

    def test_read_live_agents_adds_output_only_freshness_evidence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            started = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)
            later = started + timedelta(seconds=42)

            connect_live_agent(root, {"agent_id": "fresh-agent", "display_name": "Fresh Agent"}, now=started)
            agents = read_live_agents(root, now=later, stale_after_seconds=180)
            persisted = json.loads((root / "live_agents.json").read_text(encoding="utf-8"))

        self.assertEqual(agents[0]["status"], "online")
        self.assertEqual(agents[0]["heartbeat_age_seconds"], 42)
        self.assertEqual(agents[0]["stale_after_seconds"], 180)
        self.assertNotIn("heartbeat_age_seconds", persisted["agents"][0])
        self.assertNotIn("stale_after_seconds", persisted["agents"][0])

    def test_read_live_agents_ignores_persisted_freshness_evidence_without_valid_heartbeat(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "live_agents.json").write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "corrupt-agent",
                                "display_name": "Corrupt Agent",
                                "provider_kind": "manual",
                                "connection_kind": "manual",
                                "status": "online",
                                "last_seen_at": "not-a-timestamp",
                                "heartbeat_age_seconds": 999,
                                "stale_after_seconds": 999,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            agent = read_live_agents(root, stale_after_seconds=180)[0]

        self.assertEqual(agent["agent_id"], "corrupt-agent")
        self.assertEqual(agent["stale_after_seconds"], 180)
        self.assertNotIn("heartbeat_age_seconds", agent)

    def test_heartbeat_removes_persisted_output_only_freshness_evidence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            started = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)
            (root / "live_agents.json").write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "dirty-agent",
                                "display_name": "Dirty Agent",
                                "provider_kind": "manual",
                                "connection_kind": "manual",
                                "status": "online",
                                "engagement_mode": "always",
                                "last_seen_at": "2026-05-17T11:59:00+00:00",
                                "heartbeat_age_seconds": 60,
                                "stale_after_seconds": 180,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            heartbeat_live_agent(root, "dirty-agent", status="online", now=started)
            persisted = json.loads((root / "live_agents.json").read_text(encoding="utf-8"))

        self.assertNotIn("heartbeat_age_seconds", persisted["agents"][0])
        self.assertNotIn("stale_after_seconds", persisted["agents"][0])

    def test_heartbeat_updates_status_without_losing_connection_metadata(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            started = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)
            pinged = started + timedelta(seconds=45)

            connect_live_agent(
                root,
                {
                    "agent_id": "gemini-cli",
                    "display_name": "Gemini CLI",
                    "provider_kind": "gemini",
                    "connection_kind": "local_cli",
                    "session_id": "gemini-session",
                },
                now=started,
            )
            agent = heartbeat_live_agent(root, "gemini-cli", status="working", now=pinged)

            self.assertEqual(agent["display_name"], "Gemini CLI")
            self.assertEqual(agent["provider_kind"], "gemini")
            self.assertEqual(agent["connection_kind"], "local_cli")
            self.assertEqual(agent["session_id"], "gemini-session")
            self.assertEqual(agent["status"], "working")
            self.assertEqual(agent["last_seen_at"], "2026-05-17T12:00:45+00:00")

    def test_connect_and_heartbeat_persist_sanitized_quota_signals(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            connected = connect_live_agent(
                root,
                {
                    "agent_id": "codex-live",
                    "quota_5h": "12 / 50",
                    "quota_1w": "90%",
                    "quota_state": "low",
                    "quota_windows": [
                        {
                            "label": "5-hour Sonnet",
                            "percent": 72.7,
                            "resetsAt": "2026-06-04T12:00:00+00:00",
                            "used": 12,
                            "limit": 50,
                            "remaining": 38,
                            "unit": "messages",
                        },
                        {"label": "ignored", "percent": "bad"},
                    ],
                },
            )
            heartbeat = heartbeat_live_agent(
                root,
                "codex-live",
                metadata={
                    "quota_5h": "13 / 50",
                    "quota_state": "ok",
                    "quota_windows": [{"label": "5-hour", "percent": 26}],
                },
            )
            visible = read_live_agents(root)[0]

        self.assertEqual(connected["quota_5h"], "12 / 50")
        self.assertEqual(connected["quota_1w"], "90%")
        self.assertEqual(connected["quota_state"], "low")
        self.assertEqual(connected["quota_windows"][0]["percent"], 73)
        self.assertEqual(connected["quota_windows"][0]["unit"], "messages")
        self.assertEqual(heartbeat["quota_5h"], "13 / 50")
        self.assertEqual(heartbeat["quota_1w"], "90%")
        self.assertEqual(heartbeat["quota_state"], "ok")
        self.assertEqual(heartbeat["quota_windows"], [{"label": "5-hour", "percent": 26}])
        self.assertEqual(visible["quota_windows"], [{"label": "5-hour", "percent": 26}])

    def test_heartbeat_can_preserve_child_reported_active_status_for_liveness_ping(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            started = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)
            working_at = started + timedelta(seconds=10)
            liveness_at = started + timedelta(seconds=40)

            connect_live_agent(
                root,
                {
                    "agent_id": "self-service",
                    "display_name": "Self Service",
                    "connection_kind": "self_service",
                },
                now=started,
            )
            heartbeat_live_agent(root, "self-service", status="working", now=working_at)
            agent = heartbeat_live_agent(
                root,
                "self-service",
                status="online",
                metadata={"preserve_status": True},
                now=liveness_at,
            )

            self.assertEqual(agent["status"], "working")
            self.assertEqual(agent["last_seen_at"], "2026-05-17T12:00:40+00:00")
            persisted = json.loads((root / "live_agents.json").read_text(encoding="utf-8"))
            self.assertNotIn("preserve_status", persisted["agents"][0])

    def test_heartbeat_can_preserve_child_reported_error_for_liveness_ping(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            started = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)
            errored_at = started + timedelta(seconds=10)
            liveness_at = started + timedelta(seconds=40)

            connect_live_agent(root, {"agent_id": "self-service", "connection_kind": "self_service"}, now=started)
            heartbeat_live_agent(
                root,
                "self-service",
                status="error",
                metadata={"last_error": "command failed"},
                now=errored_at,
            )
            agent = heartbeat_live_agent(
                root,
                "self-service",
                status="online",
                metadata={"preserve_status": True},
                now=liveness_at,
            )

            self.assertEqual(agent["status"], "error")
            self.assertEqual(agent["last_error"], "command failed")
            self.assertEqual(agent["last_seen_at"], "2026-05-17T12:00:40+00:00")

    def test_preserve_status_applies_only_to_self_service_liveness_without_error_clear(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            started = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)

            connect_live_agent(root, {"agent_id": "terminal", "connection_kind": "terminal_session"}, now=started)
            heartbeat_live_agent(root, "terminal", status="working", now=started + timedelta(seconds=5))
            terminal = heartbeat_live_agent(
                root,
                "terminal",
                status="online",
                metadata={"preserve_status": True},
                now=started + timedelta(seconds=10),
            )

            connect_live_agent(root, {"agent_id": "selfer", "connection_kind": "self_service"}, now=started)
            heartbeat_live_agent(
                root,
                "selfer",
                status="error",
                metadata={"last_error": "command failed"},
                now=started + timedelta(seconds=5),
            )
            selfer = heartbeat_live_agent(
                root,
                "selfer",
                status="online",
                metadata={"preserve_status": True, "last_error": ""},
                now=started + timedelta(seconds=10),
            )

            self.assertEqual(terminal["status"], "online")
            self.assertEqual(selfer["status"], "online")
            self.assertEqual(selfer["last_error"], "")

    def test_live_session_online_heartbeat_clears_previous_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            started = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)

            connect_live_agent(
                root,
                {"agent_id": "codex-live", "provider_kind": "codex_live_session", "connection_kind": "live_session"},
                now=started,
            )
            heartbeat_live_agent(
                root,
                "codex-live",
                status="error",
                metadata={"last_error": "command failed"},
                now=started + timedelta(seconds=5),
            )
            agent = heartbeat_live_agent(
                root,
                "codex-live",
                status="online",
                now=started + timedelta(seconds=10),
            )

            self.assertEqual(agent["status"], "online")
            self.assertEqual(agent["last_error"], "")

    def test_heartbeat_can_refresh_session_id_from_runner_metadata(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            started = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)
            pinged = started + timedelta(seconds=45)

            connect_live_agent(
                root,
                {
                    "agent_id": "codex-live",
                    "display_name": "Codex Live",
                    "provider_kind": "codex_live_session",
                    "connection_kind": "live_session",
                },
                now=started,
            )
            agent = heartbeat_live_agent(
                root,
                "codex-live",
                status="online",
                metadata={"session_id": "019e3038-39cc-76a2-a746-5ba8c0f3b408"},
                now=pinged,
            )

            self.assertEqual(agent["session_id"], "019e3038-39cc-76a2-a746-5ba8c0f3b408")
            visible = read_live_agents(root, now=pinged)[0]
            self.assertEqual(visible["session_id"], "019e3038-39cc-76a2-a746-5ba8c0f3b408")

    def test_presence_last_error_redacts_sensitive_external_error_text(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sensitive_error = (
                "remote bridge failed at https://friend.example/agentsassemble/run?token=abc "
                "using env:REMOTE_BRIDGE_TOKEN and /Users/me/private/live-agents.json"
            )

            connected = connect_live_agent(
                root,
                {
                    "agent_id": "friend-bridge",
                    "connection_kind": "remote_bridge",
                    "endpoint": "http://friend.local:8777",
                    "last_error": sensitive_error,
                },
            )
            heartbeat = heartbeat_live_agent(
                root,
                "friend-bridge",
                status="error",
                metadata={"last_error": sensitive_error},
            )
            persisted = (root / "live_agents.json").read_text(encoding="utf-8")
            visible = read_live_agents(root)[0]

        self.assertEqual(connected["last_error"], "Live-agent presence error details redacted.")
        self.assertEqual(heartbeat["last_error"], "Live-agent presence error details redacted.")
        self.assertEqual(visible["last_error"], "Live-agent presence error details redacted.")
        for forbidden in (
            "friend.example",
            "abc",
            "REMOTE_BRIDGE_TOKEN",
            "/Users/me/private",
            "live-agents.json",
        ):
            self.assertNotIn(forbidden, persisted)
            self.assertNotIn(forbidden, json.dumps(visible, ensure_ascii=False))

    def test_presence_last_error_redacts_common_token_and_endpoint_forms(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sensitive_errors = [
                "bridge failed friend.local:8777/agentsassemble/run",
                "bridge failed friend.local/agentsassemble/run",
                "missing env OPENAI_API_KEY",
                "env var REMOTE_BRIDGE_TOKEN missing",
                "$ANTHROPIC_API_KEY missing",
                "config at configs/private.yaml",
                "failed reading configs/private.toml",
                "prompt file prompts/private.txt failed",
                "github_pat_1234567890abcdef leaked",
                "xoxb-1234567890-secret leaked",
                "AKIA1234567890ABCDEF leaked",
                "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJhZ2VudCJ9.signature",
            ]

            for index, sensitive_error in enumerate(sensitive_errors):
                heartbeat = heartbeat_live_agent(
                    root,
                    f"external-agent-{index}",
                    status="error",
                    metadata={"last_error": sensitive_error},
                )
                self.assertEqual(heartbeat["last_error"], "Live-agent presence error details redacted.")
            persisted = (root / "live_agents.json").read_text(encoding="utf-8")

        for forbidden in (
            "friend.local",
            "OPENAI_API_KEY",
            "REMOTE_BRIDGE_TOKEN",
            "ANTHROPIC_API_KEY",
            "configs/private",
            "prompts/private",
            "ghp_",
            "github_pat_",
            "xoxb-",
            "AKIA",
            "eyJhbGci",
        ):
            self.assertNotIn(forbidden, persisted)

    def test_presence_last_error_keeps_safe_operator_labels(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            safe_labels = ["command failed", "oauth failed", "configuration failed", "curl failed"]

            for label in safe_labels:
                agent = heartbeat_live_agent(
                    root,
                    label.replace(" ", "-"),
                    status="error",
                    metadata={"last_error": label},
                )
                self.assertEqual(agent["last_error"], label)

            persisted = json.loads((root / "live_agents.json").read_text(encoding="utf-8"))

        self.assertEqual([agent["last_error"] for agent in persisted["agents"]], safe_labels)

    def test_connect_live_agent_preserves_existing_engagement_mode_on_reregistration(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            connect_live_agent(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "engagement_mode": "always",
                },
            )

            updated = update_live_agent_engagement(root, "agent-a", "watch")
            reregistered = connect_live_agent(root, {"agent_id": "agent-a", "engagement_mode": "always"})
            fresh = connect_live_agent(root, {"agent_id": "agent-b", "engagement_mode": "shout_forever"})

        self.assertEqual(updated["display_name"], "Agent A")
        self.assertEqual(updated["provider_kind"], "local_cli")
        self.assertEqual(updated["connection_kind"], "local_cli")
        self.assertEqual(updated["engagement_mode"], "watch")
        self.assertEqual(reregistered["engagement_mode"], "watch")
        self.assertEqual(fresh["engagement_mode"], "mentioned")

    def test_connect_live_agent_replaces_default_or_invalid_existing_engagement_mode(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            heartbeat_live_agent(root, "agent-a")
            from_default = connect_live_agent(root, {"agent_id": "agent-a", "engagement_mode": "always"})
            (root / "live_agents.json").write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "agent-b",
                                "display_name": "Agent B",
                                "engagement_mode": "shout_forever",
                                "created_at": "2026-05-17T12:00:00+00:00",
                                "updated_at": "2026-05-17T12:00:00+00:00",
                                "last_seen_at": "2026-05-17T12:00:00+00:00",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            from_invalid = connect_live_agent(root, {"agent_id": "agent-b", "engagement_mode": "human_only"})

        self.assertEqual(from_default["engagement_mode"], "always")
        self.assertEqual(from_invalid["engagement_mode"], "human_only")

    def test_heartbeat_does_not_clobber_operator_selected_engagement_mode(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            connect_live_agent(root, {"agent_id": "agent-a", "engagement_mode": "always"})
            update_live_agent_engagement(root, "agent-a", "watch")

            agent = heartbeat_live_agent(root, "agent-a", metadata={"engagement_mode": "always"})

        self.assertEqual(agent["engagement_mode"], "watch")

    def test_update_live_agent_engagement_preserves_heartbeat_freshness(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            started = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)
            changed = started + timedelta(seconds=45)
            connect_live_agent(root, {"agent_id": "agent-a", "engagement_mode": "always"}, now=started)

            agent = update_live_agent_engagement(root, "agent-a", "watch", now=changed)
            visible = read_live_agents(root, now=changed)[0]

        self.assertEqual(agent["engagement_mode"], "watch")
        self.assertEqual(agent["updated_at"], "2026-05-17T12:00:45+00:00")
        self.assertEqual(agent["last_seen_at"], "2026-05-17T12:00:00+00:00")
        self.assertEqual(visible["heartbeat_age_seconds"], 45)

    def test_update_live_agent_engagement_rejects_unknown_agent_or_mode(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            connect_live_agent(root, {"agent_id": "agent-a", "engagement_mode": "always"})

            with self.assertRaisesRegex(ValueError, "Unknown engagement mode"):
                update_live_agent_engagement(root, "agent-a", "shout_forever")
            with self.assertRaisesRegex(ValueError, "Live agent missing-agent was not found"):
                update_live_agent_engagement(root, "missing-agent", "watch")

    def test_update_live_agent_poll_interval_preserves_presence_and_heartbeat(self):
        from agentsassemble.live_agents import update_live_agent_poll_interval

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            started = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)
            changed = started + timedelta(seconds=5)
            pinged = started + timedelta(seconds=7)
            connect_live_agent(root, {"agent_id": "agent-a", "engagement_mode": "always"}, now=started)

            updated = update_live_agent_poll_interval(root, "agent-a", 0, now=changed)
            heartbeat = heartbeat_live_agent(root, "agent-a", status="online", now=pinged)

        self.assertEqual(updated["poll_interval"], 0)
        self.assertEqual(updated["poll_interval_updated_at"], changed.isoformat())
        self.assertEqual(updated["last_seen_at"], started.isoformat())
        self.assertEqual(heartbeat["poll_interval"], 0)
        self.assertEqual(heartbeat["last_seen_at"], pinged.isoformat())

    def test_update_live_agent_poll_interval_rejects_invalid_or_missing_agent(self):
        from agentsassemble.live_agents import update_live_agent_poll_interval

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            connect_live_agent(root, {"agent_id": "agent-a"})

            with self.assertRaisesRegex(ValueError, "finite non-negative"):
                update_live_agent_poll_interval(root, "agent-a", -1)
            with self.assertRaisesRegex(ValueError, "Live agent missing-agent was not found"):
                update_live_agent_poll_interval(root, "missing-agent", 0.25)

    def test_connect_live_agent_rejects_blank_agent_id(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(ValueError):
                connect_live_agent(Path(temp_dir), {"agent_id": "\n "})

    def test_connect_live_agent_preserves_live_session_connection_kind(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            agent = connect_live_agent(
                Path(temp_dir),
                {
                    "agent_id": "jsonl-session",
                    "display_name": "JSONL Session",
                    "connection_kind": "live_session",
                },
            )

        self.assertEqual(agent["connection_kind"], "live_session")

    def test_connect_live_agent_preserves_terminal_session_connection_kind(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            agent = connect_live_agent(
                Path(temp_dir),
                {
                    "agent_id": "claude-terminal",
                    "display_name": "Claude Terminal",
                    "provider_kind": "claude_code",
                    "connection_kind": "terminal_session",
                },
            )

        self.assertEqual(agent["connection_kind"], "terminal_session")

    def test_connect_live_agent_preserves_self_service_connection_kind(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            agent = connect_live_agent(
                Path(temp_dir),
                {
                    "agent_id": "antigravity-live",
                    "display_name": "Antigravity Live",
                    "provider_kind": "antigravity_cli",
                    "connection_kind": "self_service",
                },
            )

        self.assertEqual(agent["connection_kind"], "self_service")

    def test_connect_live_agent_rejects_unsafe_remote_bridge_endpoint(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with self.assertRaisesRegex(ValueError, "Remote bridge endpoint"):
                connect_live_agent(
                    root,
                    {
                        "agent_id": "friend-bridge",
                        "connection_kind": "remote_bridge",
                        "endpoint": "http://bridge-token@friend.local:8777?secret=1",
                    },
                )

            self.assertFalse((root / "live_agents.json").exists())

    def test_connect_live_agent_rejects_blank_remote_bridge_endpoint(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with self.assertRaisesRegex(ValueError, "Remote bridge endpoint"):
                connect_live_agent(
                    root,
                    {
                        "agent_id": "friend-bridge",
                        "connection_kind": "remote_bridge",
                    },
                )

            self.assertFalse((root / "live_agents.json").exists())

    def test_connect_live_agent_rejects_malformed_remote_bridge_endpoint_netloc(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for endpoint in ("http://:8777", "http://friend.local:bad", "http://friend.local:99999"):
                with self.assertRaisesRegex(ValueError, "valid host and port"):
                    connect_live_agent(
                        root,
                        {
                            "agent_id": f"friend-bridge-{endpoint}",
                            "connection_kind": "remote_bridge",
                            "endpoint": endpoint,
                        },
                    )

            self.assertFalse((root / "live_agents.json").exists())

    def test_connect_live_agent_preserves_safe_remote_bridge_endpoint(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            agent = connect_live_agent(
                Path(temp_dir),
                {
                    "agent_id": "friend-bridge",
                    "connection_kind": "remote_bridge",
                    "endpoint": "http://friend.local:8777",
                },
            )

        self.assertEqual(agent["connection_kind"], "remote_bridge")
        self.assertEqual(agent["endpoint"], "http://friend.local:8777")

    def test_connect_live_agent_clears_existing_endpoint_when_reconnecting_as_local(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            connect_live_agent(
                root,
                {
                    "agent_id": "friend-bridge",
                    "connection_kind": "remote_bridge",
                    "endpoint": "http://friend.local:8777",
                },
            )

            agent = connect_live_agent(
                root,
                {
                    "agent_id": "friend-bridge",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "endpoint": "",
                },
            )

        self.assertEqual(agent["connection_kind"], "local_cli")
        self.assertEqual(agent["endpoint"], "")

    def test_connect_live_agent_preserves_diagnostic_flag(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            agent = connect_live_agent(
                Path(temp_dir),
                {
                    "agent_id": "doctor-smoke-local-cli",
                    "display_name": "Smoke Local CLI",
                    "diagnostic": True,
                },
            )

            self.assertTrue(agent["diagnostic"])
            self.assertTrue(read_live_agents(Path(temp_dir))[0]["diagnostic"])
