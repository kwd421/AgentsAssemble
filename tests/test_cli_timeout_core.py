import unittest
import json
import urllib.error
import tempfile
from pathlib import Path
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from unittest.mock import patch

from agentsassemble.cli import build_parser, frontend_info_payload, main


class CliTimeoutCoreTests(unittest.TestCase):

    def test_codex_timeout_can_be_disabled(self):
        args = build_parser().parse_args(["demo", "--adapter", "codex", "--codex-timeout", "none"])

        self.assertIsNone(args.codex_timeout)

    def test_demo_accepts_codex_live_adapter(self):
        args = build_parser().parse_args(["demo", "--adapter", "codex-live"])

        self.assertEqual(args.adapter, "codex-live")

    def test_demo_accepts_council_config_path(self):
        args = build_parser().parse_args(["demo", "--council-config", "configs/silly-fake-expert.json"])

        self.assertEqual(args.council_config, "configs/silly-fake-expert.json")

    def test_frontend_info_parser_defaults_to_gui_backend(self):
        args = build_parser().parse_args(["frontend-info"])

        self.assertEqual(args.command, "frontend-info")
        self.assertEqual(args.backend, "http://127.0.0.1:8765")
        self.assertEqual(args.port, 5173)
        self.assertFalse(args.as_json)

    def test_frontend_info_prints_json_contract_without_starting_processes(self):
        stdout = StringIO()
        with redirect_stdout(stdout):
            exit_code = main(["frontend-info", "--backend", "http://127.0.0.1:9999", "--port", "5199", "--json"])
        payload = json.loads(stdout.getvalue())

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["frontend_dir"], "frontend")
        self.assertEqual(payload["frontend_dev_port"], 5199)
        self.assertEqual(payload["frontend_dev_proxy_target"], "http://127.0.0.1:9999")
        self.assertEqual(payload["backend_url"], "http://127.0.0.1:9999")
        self.assertNotIn("legacy_console_path", payload)
        self.assertNotIn("legacy_console_url", payload)
        self.assertNotIn("legacy_console_namespace_url", payload)
        self.assertEqual(payload["legacy_console_status"], "retired")
        self.assertEqual(payload["react_app_path"], "/app/")
        self.assertEqual(payload["react_app_url"], "http://127.0.0.1:9999/app/")
        self.assertEqual(payload["react_app_kind"], "react_default")
        self.assertEqual(payload["react_app_label"], "Discord-style React room client (default at /, alias at /app/)")
        self.assertEqual(payload["app_dist_path"], "frontend/dist")
        self.assertIn("app_static_available", payload)
        self.assertIn("app_index_present", payload)
        self.assertIn("app_assets_dir_present", payload)
        self.assertIn("app_referenced_assets_present", payload)
        self.assertIn(payload["app_build_status"], {"available", "missing", "incomplete"})
        self.assertEqual(payload["recommended_ui_url"], "http://127.0.0.1:9999/")
        if payload["app_static_available"]:
            self.assertEqual(payload["recommended_ui_kind"], "react")
            self.assertEqual(payload["recommended_ui_label"], "Discord-style room client")
            self.assertEqual(payload["default_console_kind"], "react")
            self.assertEqual(payload["default_console_label"], "Discord-style room client (default entry point)")
            self.assertEqual(payload["app_build_status"], "available")
        else:
            self.assertEqual(payload["recommended_ui_kind"], "react_build_required")
            self.assertEqual(payload["recommended_ui_label"], "Discord-style room client (build required)")
            self.assertEqual(payload["default_console_kind"], "react_build_required")
            self.assertEqual(payload["default_console_label"], "Discord-style room client (build required)")
        self.assertEqual(payload["parity_matrix_doc"], "docs/product/legacy-react-parity-matrix.md")
        self.assertTrue(payload["is_default_entry_point"])
        self.assertIn("--port 9999", payload["launch_commands"][0])
        self.assertIn("npm run dev", " ".join(payload["launch_commands"]))
        self.assertIn("does not start provider CLIs", " ".join(payload["notes"]))
        self.assertIn("operator-verified", " ".join(payload["notes"]))

    def test_frontend_info_text_mode_presents_react_default(self):
        stdout = StringIO()
        with redirect_stdout(stdout):
            exit_code = main(["frontend-info"])

        self.assertEqual(exit_code, 0)
        output = stdout.getvalue()
        self.assertIn("Discord-style React room client (default at /, alias at /app/): http://127.0.0.1:8765/app/", output)
        self.assertIn("React/Vite opt-in UI: http://127.0.0.1:5173", output)
        self.assertIn("Parity matrix: docs/product/legacy-react-parity-matrix.md", output)
        self.assertNotIn("Legacy vanilla console:", output)
        self.assertNotIn("Legacy fallback", output)
        self.assertNotIn("vanilla fallback", output)
        self.assertIn("Recommended current UI:", output)
        self.assertIn("React build status:", output)
        self.assertIn("default room client at /", output)

    def test_frontend_info_reports_react_app_static_availability_from_dist(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            missing_dist = Path(temp_dir) / "missing"
            missing_payload = frontend_info_payload(
                backend="http://127.0.0.1:9876",
                port=5178,
                frontend_dist_root=missing_dist,
            )

            dist = Path(temp_dir) / "dist"
            assets = dist / "assets"
            assets.mkdir(parents=True)
            (dist / "index.html").write_text(
                '<div id="root"></div><script type="module" src="/assets/app.js"></script>'
                '<link rel="stylesheet" href="/assets/app.css">',
                encoding="utf-8",
            )
            (assets / "app.js").write_text("console.log('react preview');", encoding="utf-8")
            (assets / "app.css").write_text("body{color:white}", encoding="utf-8")
            present_payload = frontend_info_payload(
                backend="http://127.0.0.1:9876",
                port=5178,
                frontend_dist_root=dist,
            )
            incomplete_dist = Path(temp_dir) / "incomplete-dist"
            (incomplete_dist / "assets").mkdir(parents=True)
            (incomplete_dist / "index.html").write_text(
                '<div id="root"></div><script type="module" src="/assets/missing.js"></script>',
                encoding="utf-8",
            )
            incomplete_payload = frontend_info_payload(
                backend="http://127.0.0.1:9876",
                port=5178,
                frontend_dist_root=incomplete_dist,
            )

        self.assertFalse(missing_payload["app_static_available"])
        self.assertEqual(missing_payload["recommended_ui_kind"], "react_build_required")
        self.assertEqual(missing_payload["recommended_ui_url"], "http://127.0.0.1:9876/")
        self.assertEqual(missing_payload["recommended_ui_label"], "Discord-style room client (build required)")
        self.assertEqual(missing_payload["legacy_console_status"], "retired")
        self.assertEqual(missing_payload["app_build_status"], "missing")
        self.assertFalse(missing_payload["app_index_present"])
        self.assertFalse(missing_payload["app_assets_dir_present"])
        self.assertFalse(missing_payload["app_referenced_assets_present"])
        self.assertTrue(missing_payload["is_default_entry_point"])
        self.assertTrue(present_payload["is_default_entry_point"])
        self.assertFalse(incomplete_payload["app_static_available"])
        self.assertTrue(incomplete_payload["app_index_present"])
        self.assertTrue(incomplete_payload["app_assets_dir_present"])
        self.assertFalse(incomplete_payload["app_referenced_assets_present"])
        self.assertEqual(incomplete_payload["app_build_status"], "incomplete")
        self.assertEqual(incomplete_payload["recommended_ui_kind"], "react_build_required")
        self.assertEqual(incomplete_payload["recommended_ui_url"], "http://127.0.0.1:9876/")
        self.assertTrue(present_payload["app_static_available"])
        self.assertTrue(present_payload["app_referenced_assets_present"])
        self.assertEqual(present_payload["app_build_status"], "available")
        self.assertEqual(present_payload["recommended_ui_kind"], "react")
        self.assertEqual(present_payload["recommended_ui_url"], "http://127.0.0.1:9876/")
        self.assertEqual(present_payload["recommended_ui_label"], "Discord-style room client")
        self.assertTrue(present_payload["app_index_present"])
        self.assertTrue(present_payload["app_assets_dir_present"])

    def test_demo_rejects_disabled_free_chat_meeting_mode(self):
        stderr = StringIO()
        with redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
            build_parser().parse_args(["demo", "--meeting-mode", "free-chat", "--moderator", "off"])

        self.assertEqual(raised.exception.code, 2)
        self.assertIn("invalid choice: 'free-chat'", stderr.getvalue())

    def test_demo_accepts_supported_meeting_mode_and_moderator_options(self):
        args = build_parser().parse_args(["demo", "--meeting-mode", "debate", "--moderator", "off"])

        self.assertEqual(args.meeting_mode, "debate")
        self.assertEqual(args.moderator, "off")

    def test_demo_passes_meeting_mode_and_moderator_to_runner(self):
        with patch("agentsassemble.cli.run_demo_meeting") as run_demo:
            exit_code = main(["demo", "--meeting-mode", "debate", "--moderator", "off", "--output-root", "out"])

        self.assertEqual(exit_code, 0)
        run_demo.assert_called_once()
        kwargs = run_demo.call_args.kwargs
        self.assertEqual(kwargs["meeting_mode"], "debate")
        self.assertFalse(kwargs["moderator_enabled"])
        self.assertEqual(kwargs["output_root"], Path("out"))

    def test_demo_accepts_follow_up_metadata(self):
        args = build_parser().parse_args(
            [
                "demo",
                "--follow-up-of",
                "meeting-1",
                "--follow-up-from",
                ".agentsassemble/meetings/meeting-1",
                "--follow-up-note",
                "reopen unresolved caveat",
            ]
        )

        self.assertEqual(args.follow_up_of, "meeting-1")
        self.assertEqual(args.follow_up_from, ".agentsassemble/meetings/meeting-1")
        self.assertEqual(args.follow_up_note, "reopen unresolved caveat")

    def test_deep_codex_defaults_to_no_timeout(self):
        args = build_parser().parse_args(["demo", "--adapter", "codex", "--research-depth", "deep"])

        self.assertIsNone(args.codex_timeout)

    def test_claude_bridge_parses_bridge_command_without_overwriting_subcommand(self):
        args = build_parser().parse_args(["claude-bridge", "--token", "bridge-token", "--command", "claude"])

        self.assertEqual(args.command, "claude-bridge")
        self.assertEqual(args.bridge_command, "claude")

    def test_gui_accepts_live_agent_autostart_options(self):
        with patch("agentsassemble.cli.serve_gui") as serve_gui:
            exit_code = main(
                [
                    "gui",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "0",
                    "--output-root",
                    "out",
                    "--live-agent-config",
                    "configs/fake-live-agents.json",
                    "--live-agent-group-id",
                    "boot",
                    "--live-agent-auto-restart",
                    "--live-agent-max-restarts",
                    "3",
                    "--live-agent-restart-backoff-seconds",
                    "1.5",
                    "--live-agent-stale-restart-after-seconds",
                    "120",
                    "--attention-shadow-mode",
                    "sample",
                ]
            )

        self.assertEqual(exit_code, 0)
        serve_gui.assert_called_once()
        kwargs = serve_gui.call_args.kwargs
        self.assertEqual(kwargs["host"], "127.0.0.1")
        self.assertEqual(kwargs["port"], 0)
        self.assertEqual(kwargs["output_root"], Path("out"))
        self.assertEqual(kwargs["live_agent_config"], Path("configs/fake-live-agents.json"))
        self.assertEqual(kwargs["live_agent_group_id"], "boot")
        self.assertTrue(kwargs["live_agent_auto_restart"])
        self.assertEqual(kwargs["live_agent_max_restarts"], 3)
        self.assertEqual(kwargs["live_agent_restart_backoff_seconds"], 1.5)
        self.assertEqual(kwargs["live_agent_stale_restart_after_seconds"], 120.0)
        self.assertEqual(kwargs["room_repository_backend"], "sqlite")
        self.assertEqual(kwargs["attention_shadow_mode"], "sample")

    def test_gui_defaults_shadow_attention_recording_off(self):
        with patch("agentsassemble.cli.serve_gui") as serve_gui:
            exit_code = main(["gui"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(serve_gui.call_args.kwargs["attention_shadow_mode"], "off")

    def test_gui_accepts_explicit_postgres_repository_environment(self):
        with patch("agentsassemble.cli.serve_gui") as serve_gui:
            exit_code = main(
                [
                    "gui",
                    "--room-repository-backend",
                    "postgresql",
                    "--room-postgres-dsn-env",
                    "ROOM_DATABASE_SECRET",
                ]
            )

        self.assertEqual(exit_code, 0)
        kwargs = serve_gui.call_args.kwargs
        self.assertEqual(kwargs["room_repository_backend"], "postgresql")
        self.assertEqual(kwargs["room_postgres_dsn_env"], "ROOM_DATABASE_SECRET")

    def test_gui_reports_unavailable_repository_without_traceback(self):
        from agentsassemble.application.room_repository_factory import RoomRepositoryUnavailable

        with patch(
            "agentsassemble.cli.serve_gui",
            side_effect=RoomRepositoryUnavailable("PostgreSQL room authority is not activated."),
        ):
            stderr = StringIO()
            with patch("sys.stderr", stderr):
                exit_code = main(["gui", "--room-repository-backend", "postgresql"])

        self.assertEqual(exit_code, 2)
        self.assertIn("authority is not activated", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_gui_accepts_public_invite_options(self):
        with patch("agentsassemble.cli.serve_gui") as serve_gui:
            exit_code = main(
                [
                    "gui",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "0",
                    "--output-root",
                    "out",
                    "--public-url",
                    "https://shared-room.example.com",
                    "--host-token",
                    "host-secret",
                    "--unsafe-expose-control-plane",
                    "--start-public-tunnel",
                ]
            )

        self.assertEqual(exit_code, 0)
        serve_gui.assert_called_once()
        kwargs = serve_gui.call_args.kwargs
        self.assertEqual(kwargs["public_url"], "https://shared-room.example.com")
        self.assertEqual(kwargs["host_token"], "host-secret")
        self.assertTrue(kwargs["unsafe_expose_control_plane"])
        self.assertTrue(kwargs["start_public_tunnel"])

    def test_gui_reports_non_loopback_policy_error_without_traceback(self):
        with patch("agentsassemble.cli.serve_gui", side_effect=ValueError("Direct non-loopback GUI bind is disabled")):
            stderr = StringIO()
            with patch("sys.stderr", stderr):
                exit_code = main(["gui", "--host", "0.0.0.0"])

        self.assertEqual(exit_code, 2)
        self.assertIn("error: Direct non-loopback GUI bind is disabled", stderr.getvalue())
        self.assertIn("hint: bind to 127.0.0.1", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_sessions_list_outputs_codex_session_index_as_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir) / ".codex"
            codex_home.mkdir()
            (codex_home / "session_index.jsonl").write_text(
                json.dumps(
                    {
                        "id": "019e3038-39cc-76a2-a746-5ba8c0f3b408",
                        "thread_name": "인수인계 받기",
                        "updated_at": "2026-05-16T09:57:44Z",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            stdout = StringIO()

            with patch.dict("os.environ", {"CODEX_HOME": str(codex_home)}), patch("sys.stdout", stdout):
                exit_code = main(["sessions", "--legacy-internal", "list", "--json"])

            self.assertEqual(exit_code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload[0]["id"], "019e3038-39cc-76a2-a746-5ba8c0f3b408")
            self.assertEqual(payload[0]["thread_name"], "인수인계 받기")

    def test_sessions_invite_writes_gitignored_codex_live_agent_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "codex-live-session.local.json"
            stdout = StringIO()

            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "sessions",
                        "--legacy-internal",
                        "invite",
                        "019e3038-39cc-76a2-a746-5ba8c0f3b408",
                        "--role",
                        "lore_lawyer",
                        "--output",
                        str(output),
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertIn(str(output), stdout.getvalue())
            config = json.loads(output.read_text(encoding="utf-8"))
            bindings = {binding["role_id"]: binding for binding in config["agent_bindings"]}
            self.assertEqual(bindings["lore_lawyer"]["join_mode"], "current_session")
            self.assertEqual(bindings["lore_lawyer"]["session_id"], "019e3038-39cc-76a2-a746-5ba8c0f3b408")
            self.assertEqual(bindings["show_me_the_feats"]["join_mode"], "fresh")
            self.assertEqual(bindings["fanboard_skeptic"]["join_mode"], "fresh")

    def test_sessions_invite_can_post_through_room_server(self):
        response = {
            "binding": {
                "role_id": "lore_lawyer",
                "agent_id": "codex-live-lore-lawyer",
                "join_mode": "current_session",
                "provider_id": "codex-live",
            }
        }
        stdout = StringIO()

        with patch("agentsassemble.cli._request_json", return_value=response) as request_json:
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "sessions",
                        "--legacy-internal",
                        "invite",
                        "019e3038-39cc-76a2-a746-5ba8c0f3b408",
                        "--role",
                        "lore_lawyer",
                        "--server",
                        "http://room.local",
                        "--meeting-id",
                        "resident-m1",
                        "--json",
                    ]
                )

        self.assertEqual(exit_code, 0)
        request_json.assert_called_once_with(
            "http://room.local/api/codex-sessions/invite",
            method="POST",
            payload={
                "session_id": "019e3038-39cc-76a2-a746-5ba8c0f3b408",
                "role_id": "lore_lawyer",
                "meeting_id": "resident-m1",
            },
        )
        self.assertEqual(json.loads(stdout.getvalue()), response)

    def test_sessions_invite_server_compact_output_uses_real_response_binding(self):
        response = {"binding": {"role_id": "lore_lawyer", "agent_id": "codex-live-lore-lawyer"}}
        stdout = StringIO()

        with patch("agentsassemble.cli._request_json", return_value=response):
            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "sessions",
                        "--legacy-internal",
                        "invite",
                        "019e3038-39cc-76a2-a746-5ba8c0f3b408",
                        "--role",
                        "lore_lawyer",
                        "--server",
                        "http://room.local",
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertIn("Invited lore_lawyer as codex-live-lore-lawyer", stdout.getvalue())

    def test_sessions_invite_server_transport_error_returns_cli_error(self):
        stderr = StringIO()

        with patch("agentsassemble.cli._request_json", side_effect=urllib.error.URLError("down")):
            with patch("sys.stderr", stderr):
                exit_code = main(
                    [
                        "sessions",
                        "--legacy-internal",
                        "invite",
                        "019e3038-39cc-76a2-a746-5ba8c0f3b408",
                        "--role",
                        "lore_lawyer",
                        "--server",
                        "http://room.local",
                    ]
                )

        self.assertEqual(exit_code, 2)
        self.assertIn("error:", stderr.getvalue())

    def test_sessions_live_agent_config_writes_resident_config_from_invite_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            invite_path = root / "codex-live-session.local.json"
            output_path = root / "live-agents.codex-session.local.json"
            invite_path.write_text(
                json.dumps(
                    {
                        "agent_bindings": [
                            {
                                "agent_id": "codex-live-lore",
                                "role_id": "lore_lawyer",
                                "provider_id": "codex-live",
                                "join_mode": "current_session",
                                "session_id": "019e3038-39cc-76a2-a746-5ba8c0f3b408",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            stdout = StringIO()

            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "sessions",
                        "--legacy-internal",
                        "live-agent-config",
                        "--input",
                        str(invite_path),
                        "--output",
                        str(output_path),
                        "--server",
                        "http://room.local",
                        "--meeting-id",
                        "resident-m1",
                        "--engagement-mode",
                        "moderator_called",
                        "--json",
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertTrue(output_path.exists())
            written = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(written["server"], "http://room.local")
            self.assertEqual(written["agents"][0]["agent_id"], "codex-live-lore")
            self.assertEqual(written["agents"][0]["provider_kind"], "codex_live_session")
            self.assertEqual(written["agents"][0]["connection_kind"], "live_session")
            self.assertEqual(written["agents"][0]["meeting_id"], "resident-m1")
            self.assertEqual(written["agents"][0]["engagement_mode"], "moderator_called")
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["output"], str(output_path))
            self.assertEqual(
                payload["next_commands"]["preflight"],
                [
                    "python3",
                    "-m",
                    "agentsassemble.cli",
                    "live-agent",
                    "--legacy-internal",
                    "preflight",
                    "--config",
                    str(output_path),
                ],
            )
            self.assertEqual(
                payload["next_commands"]["ensure_session"],
                [
                    "python3",
                    "-m",
                    "agentsassemble.cli",
                    "live-agent",
                    "--legacy-internal",
                    "ensure-session",
                    "--server",
                    "http://room.local",
                    "--meeting-id",
                    "resident-m1",
                    "--group-id",
                    "live-agents.codex-session.local",
                    "--agent-config",
                    str(invite_path),
                    "--live-agent-config",
                    str(output_path),
                ],
            )

    def test_sessions_live_agent_config_compact_output_quotes_next_commands(self):
        with tempfile.TemporaryDirectory(prefix="codex path ") as temp_dir:
            root = Path(temp_dir)
            invite_path = root / "codex invite.json"
            output_path = root / "live agents.json"
            invite_path.write_text(
                json.dumps(
                    {
                        "agent_bindings": [
                            {
                                "agent_id": "codex-live-lore",
                                "role_id": "lore_lawyer",
                                "provider_id": "codex-live",
                                "session_id": "019e3038-39cc-76a2-a746-5ba8c0f3b408",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            stdout = StringIO()

            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "sessions",
                        "--legacy-internal",
                        "live-agent-config",
                        "--input",
                        str(invite_path),
                        "--output",
                        str(output_path),
                        "--server",
                        "http://room.local/with space",
                        "--meeting-id",
                        "resident m1",
                    ]
                )

            self.assertEqual(exit_code, 0)
            output = stdout.getvalue()
            self.assertIn(
                "Next preflight: python3 -m agentsassemble.cli live-agent --legacy-internal preflight --config",
                output,
            )
            self.assertIn(f"'{output_path}'", output)
            self.assertIn(f"'{invite_path}'", output)
            self.assertIn("'http://room.local/with space'", output)
            self.assertIn("'resident m1'", output)
            self.assertIn("--group-id live-agents", output)
            written = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(written["agents"][0]["engagement_mode"], "moderator_called")
