from __future__ import annotations

import unittest
from pathlib import Path

from scripts.generate_package_map import build_package_map, load_package_graph


ROOT = Path(__file__).resolve().parents[1]


class PackageMapTests(unittest.TestCase):
    def test_committed_package_map_matches_ast_inventory(self) -> None:
        expected = build_package_map(ROOT)
        actual = (ROOT / "docs" / "product" / "PACKAGE_MAP.md").read_text(
            encoding="utf-8"
        )

        self.assertEqual(actual, expected)

    def test_admission_module_collision_is_resolved_by_the_owned_package(self) -> None:
        graph = load_package_graph(ROOT)
        package_map = build_package_map(ROOT)

        self.assertIn("`agentsassemble.admission`", package_map)
        self.assertNotIn("`agentsassemble/admission.py`", package_map)
        self.assertEqual(graph.domains["agentsassemble.admission"], "admission")
        self.assertEqual(
            graph.domains["agentsassemble.admission.preflight"],
            "admission",
        )
        preflight_line = next(
            line
            for line in package_map.splitlines()
            if line.startswith("| `agentsassemble.admission.preflight` |")
        )
        self.assertTrue(preflight_line.endswith("| in-target-package |"))

    def test_identity_pairing_uses_the_owned_package(self) -> None:
        graph = load_package_graph(ROOT)
        package_map = build_package_map(ROOT)

        self.assertEqual(graph.domains["agentsassemble.identity"], "identity")
        self.assertEqual(
            graph.domains["agentsassemble.identity.pairing"],
            "identity",
        )
        pairing_line = next(
            line
            for line in package_map.splitlines()
            if line.startswith("| `agentsassemble.identity.pairing` |")
        )
        self.assertTrue(pairing_line.endswith("| in-target-package |"))

    def test_identity_contract_uses_the_owned_package(self) -> None:
        graph = load_package_graph(ROOT)
        package_map = build_package_map(ROOT)

        self.assertEqual(
            graph.domains["agentsassemble.identity.repository"],
            "identity",
        )
        repository_line = next(
            line
            for line in package_map.splitlines()
            if line.startswith("| `agentsassemble.identity.repository` |")
        )
        self.assertTrue(repository_line.endswith("| in-target-package |"))

    def test_local_identity_modules_use_persistence_ownership(self) -> None:
        graph = load_package_graph(ROOT)
        package_map = build_package_map(ROOT)

        for module_name in (
            "agentsassemble.persistence.local.identity.repository",
            "agentsassemble.persistence.local.identity.registry",
            "agentsassemble.persistence.local.identity.migration",
        ):
            self.assertEqual(graph.domains[module_name], "persistence")
            module_line = next(
                line
                for line in package_map.splitlines()
                if line.startswith(f"| `{module_name}` |")
            )
            self.assertTrue(module_line.endswith("| in-target-package |"))

    def test_nested_persistence_modules_keep_persistence_ownership(self) -> None:
        graph = load_package_graph(ROOT)
        package_map = build_package_map(ROOT)

        self.assertEqual(
            graph.domains[
                "agentsassemble.persistence.postgres.application_database"
            ],
            "persistence",
        )
        self.assertEqual(
            graph.domains["agentsassemble.persistence.postgres.connection_pool"],
            "persistence",
        )
        self.assertIn(
            "`agentsassemble.persistence.postgres.application_database`",
            package_map,
        )
        self.assertIn("`persistence/postgres/` | in-target-package", package_map)

    def test_nested_domain_modules_use_their_package_owner(self) -> None:
        graph = load_package_graph(ROOT)
        package_map = build_package_map(ROOT)

        for module_name in (
            "agentsassemble.room.bridge_stop_confirmation",
            "agentsassemble.room.command_uow",
            "agentsassemble.room.commands",
            "agentsassemble.room.errors",
            "agentsassemble.room.event_broker",
            "agentsassemble.room.projection",
            "agentsassemble.room.repository",
            "agentsassemble.room.text",
            "agentsassemble.room.types",
            "agentsassemble.room.visibility",
        ):
            with self.subTest(module_name=module_name):
                self.assertEqual(graph.domains[module_name], "room")
                module_line = next(
                    line
                    for line in package_map.splitlines()
                    if line.startswith(f"| `{module_name}` |")
                )
                self.assertTrue(module_line.endswith("| in-target-package |"))
        self.assertEqual(
            graph.domains["agentsassemble.web.frontend_runtime"],
            "web",
        )
        self.assertEqual(graph.domains["agentsassemble.frontend_runtime"], "web")
        for compatibility_module in (
            "agentsassemble.bridge_stop_confirmation",
            "agentsassemble.room_command_uow",
            "agentsassemble.room_commands",
            "agentsassemble.room_errors",
            "agentsassemble.room_event_broker",
            "agentsassemble.room_projection",
            "agentsassemble.room_repository",
            "agentsassemble.room_types",
        ):
            compatibility_line = next(
                line
                for line in package_map.splitlines()
                if line.startswith(f"| `{compatibility_module}` |")
            )
            self.assertIn("| compatibility |", compatibility_line)
            self.assertTrue(compatibility_line.endswith("| compatibility-shim |"))
        frontend_line = next(
            line
            for line in package_map.splitlines()
            if line.startswith("| `agentsassemble.web.frontend_runtime` |")
        )
        frontend_shim_line = next(
            line
            for line in package_map.splitlines()
            if line.startswith("| `agentsassemble.frontend_runtime` |")
        )
        self.assertTrue(frontend_line.endswith("| in-target-package |"))
        self.assertIn("| compatibility |", frontend_shim_line)
        self.assertTrue(
            frontend_shim_line.endswith("| compatibility-shim |")
        )

    def test_room_persistence_move_is_not_misclassified_as_policy(self) -> None:
        package_map = build_package_map(ROOT)
        attention_line = next(
            line
            for line in package_map.splitlines()
            if "`agentsassemble.persistence.postgres.room.attention`" in line
        )
        row_shim_line = next(
            line
            for line in package_map.splitlines()
            if "`agentsassemble.postgres_room_rows`" in line
        )

        self.assertTrue(attention_line.endswith("| in-target-package |"))
        self.assertIn("| compatibility |", row_shim_line)
        self.assertTrue(row_shim_line.endswith("| compatibility-shim |"))

    def test_application_and_feature_packages_are_owned_paths(self) -> None:
        graph = load_package_graph(ROOT)
        package_map = build_package_map(ROOT)

        for module_name in (
            "agentsassemble.application.agent_bridge_entrypoint",
            "agentsassemble.application.gui",
            "agentsassemble.application.gui_factory",
        ):
            with self.subTest(module_name=module_name):
                application_line = next(
                    line
                    for line in package_map.splitlines()
                    if line.startswith(f"| `{module_name}` |")
                )
                self.assertEqual(graph.domains[module_name], "application")
                self.assertTrue(
                    application_line.endswith("| in-target-package |")
                )

        for module_name, proposed_package in (
            ("agentsassemble.features.mafia.routes", "features/mafia/"),
            ("agentsassemble.features.side_chat.routes", "features/side_chat/"),
            ("agentsassemble.features.social.routes", "features/social/"),
        ):
            with self.subTest(module_name=module_name):
                self.assertEqual(graph.domains[module_name], "features")
                self.assertEqual(graph.classifications[module_name], "optional")
                module_line = next(
                    line
                    for line in package_map.splitlines()
                    if line.startswith(f"| `{module_name}` |")
                )
                self.assertIn(f"| `{proposed_package}` |", module_line)
                self.assertTrue(module_line.endswith("| in-target-package |"))

    def test_provider_modules_use_the_owned_package(self) -> None:
        graph = load_package_graph(ROOT)
        package_map = build_package_map(ROOT)
        for module_name in (
            "agentsassemble.providers.agent_bridge",
            "agentsassemble.providers.api",
            "agentsassemble.providers.antigravity_resident",
            "agentsassemble.providers.auth",
            "agentsassemble.providers.bridge_protocol",
            "agentsassemble.providers.bridge_process",
            "agentsassemble.providers.bridge_report_tracker",
            "agentsassemble.providers.capabilities",
            "agentsassemble.providers.catalog",
            "agentsassemble.providers.claude_resident",
            "agentsassemble.providers.claude_transcript",
            "agentsassemble.providers.codex_app_server",
            "agentsassemble.providers.codex_app_server_live",
            "agentsassemble.providers.codex_resident",
            "agentsassemble.providers.codex_session_ids",
            "agentsassemble.providers.codex_stream",
            "agentsassemble.providers.cursor_resident",
            "agentsassemble.providers.deepseek",
            "agentsassemble.providers.grok_acp",
            "agentsassemble.providers.grok_resident",
            "agentsassemble.providers.hermes_resident",
            "agentsassemble.providers.kiro_resident",
            "agentsassemble.providers.launch_specs",
            "agentsassemble.providers.live_cli",
            "agentsassemble.providers.live_cli_output",
            "agentsassemble.providers.live_cli_transcripts",
            "agentsassemble.providers.model_verification",
            "agentsassemble.providers.opencode",
            "agentsassemble.providers.process_environment",
            "agentsassemble.providers.resident_config",
            "agentsassemble.providers.runtime_config",
            "agentsassemble.providers.runtime_contracts",
            "agentsassemble.providers.runtime_factory",
            "agentsassemble.providers.secrets",
            "agentsassemble.providers.sessions",
            "agentsassemble.providers.turn_input",
            "agentsassemble.providers.windows_conpty",
        ):
            with self.subTest(module_name=module_name):
                self.assertEqual(graph.domains[module_name], "providers")
                module_line = next(
                    line
                    for line in package_map.splitlines()
                    if line.startswith(f"| `{module_name}` |")
                )
                self.assertTrue(
                    module_line.endswith("| in-target-package |")
                )

        for compatibility_module in (
            "agentsassemble.antigravity_resident",
            "agentsassemble.bridge_protocol",
            "agentsassemble.bridge_report_tracker",
            "agentsassemble.claude_resident",
            "agentsassemble.claude_transcript",
            "agentsassemble.codex_app_server_runtime",
            "agentsassemble.codex_app_server_live_runtime",
            "agentsassemble.codex_resident",
            "agentsassemble.codex_session_ids",
            "agentsassemble.codex_stream",
            "agentsassemble.cursor_resident",
            "agentsassemble.deepseek_runtime",
            "agentsassemble.grok_acp_runtime",
            "agentsassemble.grok_resident",
            "agentsassemble.hermes_resident",
            "agentsassemble.kiro_resident",
            "agentsassemble.native_cli_providers",
            "agentsassemble.provider_capabilities",
            "agentsassemble.live_cli",
            "agentsassemble.live_cli_output",
            "agentsassemble.live_cli_transcripts",
            "agentsassemble.provider_model_verification",
            "agentsassemble.opencode_runtime",
            "agentsassemble.process_environment",
            "agentsassemble.provider_auth",
            "agentsassemble.provider_catalog",
            "agentsassemble.provider_runtime_config",
            "agentsassemble.provider_runtime_contracts",
            "agentsassemble.provider_runtime_factory",
            "agentsassemble.provider_secrets",
            "agentsassemble.provider_sessions",
            "agentsassemble.room_agent_bridge",
            "agentsassemble.room_bridge_process",
            "agentsassemble.room_api_provider",
            "agentsassemble.windows_conpty",
        ):
            with self.subTest(compatibility_module=compatibility_module):
                compatibility_line = next(
                    line
                    for line in package_map.splitlines()
                    if line.startswith(f"| `{compatibility_module}` |")
                )
                self.assertIn("| compatibility |", compatibility_line)
                self.assertTrue(
                    compatibility_line.endswith("| compatibility-shim |")
                )


if __name__ == "__main__":
    unittest.main()
