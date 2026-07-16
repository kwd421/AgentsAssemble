from __future__ import annotations

import unittest

import agentsassemble.bridge_protocol as compatibility_bridge_protocol
import agentsassemble.bridge_report_tracker as compatibility_bridge_tracker
import agentsassemble.deepseek_runtime as compatibility_deepseek
import agentsassemble.grok_acp_runtime as compatibility_grok_acp
import agentsassemble.live_cli_output as compatibility_live_cli_output
import agentsassemble.live_cli_transcripts as compatibility_live_cli_transcripts
import agentsassemble.opencode_runtime as compatibility_opencode
import agentsassemble.process_environment as compatibility_process_environment
import agentsassemble.provider_catalog as compatibility_catalog
import agentsassemble.provider_runtime_config as compatibility_config
import agentsassemble.provider_runtime_contracts as compatibility_contracts
import agentsassemble.provider_runtime_factory as compatibility_factory
import agentsassemble.provider_secrets as compatibility_secrets
import agentsassemble.windows_conpty as compatibility_windows_conpty
from agentsassemble.providers import catalog as owned_catalog
from agentsassemble.providers import bridge_protocol as owned_bridge_protocol
from agentsassemble.providers import bridge_report_tracker as owned_bridge_tracker
from agentsassemble.providers import deepseek as owned_deepseek
from agentsassemble.providers import grok_acp as owned_grok_acp
from agentsassemble.providers import live_cli_output as owned_live_cli_output
from agentsassemble.providers import live_cli_transcripts as owned_live_cli_transcripts
from agentsassemble.providers import opencode as owned_opencode
from agentsassemble.providers import process_environment as owned_process_environment
from agentsassemble.providers import runtime_config as owned_config
from agentsassemble.providers import runtime_contracts as owned_contracts
from agentsassemble.providers import runtime_factory as owned_factory
from agentsassemble.providers import secrets as owned_secrets
from agentsassemble.providers import windows_conpty as owned_windows_conpty


class ProviderPackageTests(unittest.TestCase):
    def test_bridge_protocol_root_module_exports_owned_types(self) -> None:
        for name in (
            "BridgeProtocolError",
            "BridgeReportRejected",
            "BridgeReportResponse",
            "BridgeReportTimeout",
            "TurnAssignmentEnvelope",
        ):
            with self.subTest(name=name):
                self.assertIs(
                    getattr(compatibility_bridge_protocol, name),
                    getattr(owned_bridge_protocol, name),
                )

    def test_bridge_report_tracker_root_module_exports_owned_type(self) -> None:
        self.assertIs(
            compatibility_bridge_tracker.BridgeReportTracker,
            owned_bridge_tracker.BridgeReportTracker,
        )

    def test_deepseek_runtime_root_module_exports_owned_type(self) -> None:
        self.assertIs(
            compatibility_deepseek.DeepSeekApiRuntime,
            owned_deepseek.DeepSeekApiRuntime,
        )

    def test_grok_acp_runtime_root_module_exports_owned_type(self) -> None:
        self.assertIs(
            compatibility_grok_acp.GrokAcpRuntime,
            owned_grok_acp.GrokAcpRuntime,
        )

    def test_live_cli_output_root_module_exports_owned_functions(self) -> None:
        for name in (
            "extract_live_cli_terminal_message",
            "filter_live_cli_terminal_text",
            "strip_terminal_ansi",
            "terminal_text_contains",
        ):
            with self.subTest(name=name):
                self.assertIs(
                    getattr(compatibility_live_cli_output, name),
                    getattr(owned_live_cli_output, name),
                )

    def test_live_cli_transcripts_root_module_exports_owned_types_and_factory(self) -> None:
        for name in (
            "AntigravityTranscriptMessageSource",
            "ClaudeSessionMessageSource",
            "CodexSessionMessageSource",
            "GrokSessionMessageSource",
            "LiveCliMessageExtractionError",
            "LiveCliMessageSnapshot",
            "LiveCliMessageSource",
            "TerminalCaptureMessageSource",
            "make_live_cli_message_source",
        ):
            with self.subTest(name=name):
                self.assertIs(
                    getattr(compatibility_live_cli_transcripts, name),
                    getattr(owned_live_cli_transcripts, name),
                )

    def test_opencode_runtime_root_module_exports_owned_types(self) -> None:
        self.assertIs(
            compatibility_opencode.OpenCodeRuntime,
            owned_opencode.OpenCodeRuntime,
        )
        self.assertIs(
            compatibility_opencode.OpenCodeServerProcess,
            owned_opencode.OpenCodeServerProcess,
        )

    def test_process_environment_root_module_exports_owned_functions(self) -> None:
        for name in (
            "environment_contains_secret_names",
            "sanitized_child_environment",
            "sanitized_provider_environment",
        ):
            with self.subTest(name=name):
                self.assertIs(
                    getattr(compatibility_process_environment, name),
                    getattr(owned_process_environment, name),
                )

    def test_provider_secrets_root_module_exports_owned_store(self) -> None:
        self.assertIs(
            compatibility_secrets.ProviderSecretStore,
            owned_secrets.ProviderSecretStore,
        )
        self.assertIs(
            compatibility_secrets.PROVIDER_SECRETS,
            owned_secrets.PROVIDER_SECRETS,
        )

    def test_windows_conpty_root_module_exports_owned_type(self) -> None:
        self.assertIs(
            compatibility_windows_conpty.WindowsConPtyRuntime,
            owned_windows_conpty.WindowsConPtyRuntime,
        )

    def test_catalog_root_module_exports_owned_data_and_functions(self) -> None:
        self.assertIs(
            compatibility_catalog.PROVIDER_CATALOG,
            owned_catalog.PROVIDER_CATALOG,
        )
        self.assertIs(
            compatibility_catalog.catalog_payload,
            owned_catalog.catalog_payload,
        )

    def test_runtime_contract_root_module_exports_owned_types(self) -> None:
        self.assertIs(
            compatibility_contracts.AdapterContractError,
            owned_contracts.AdapterContractError,
        )
        self.assertIs(
            compatibility_contracts.ProviderTurnResult,
            owned_contracts.ProviderTurnResult,
        )
        self.assertIs(
            compatibility_contracts.ProviderRuntimeHealth,
            owned_contracts.ProviderRuntimeHealth,
        )
        self.assertIs(
            compatibility_contracts.SUPPORTED_DECLINE_REASONS,
            owned_contracts.SUPPORTED_DECLINE_REASONS,
        )

    def test_runtime_config_root_module_exports_owned_types(self) -> None:
        self.assertIs(
            compatibility_config.ProviderRuntimeConfigError,
            owned_config.ProviderRuntimeConfigError,
        )
        self.assertIs(
            compatibility_config.BridgeConfigError,
            owned_config.BridgeConfigError,
        )
        self.assertIs(
            compatibility_config.ProviderRuntimeProfile,
            owned_config.ProviderRuntimeProfile,
        )
        self.assertIs(
            compatibility_config.ProviderRuntimeConfig,
            owned_config.ProviderRuntimeConfig,
        )
        self.assertIs(
            compatibility_config.CanonicalBridgeLaunchConfig,
            owned_config.CanonicalBridgeLaunchConfig,
        )

    def test_runtime_factory_root_module_exports_owned_factory(self) -> None:
        self.assertIs(
            compatibility_factory.ProviderRuntimeFactoryError,
            owned_factory.ProviderRuntimeFactoryError,
        )
        self.assertIs(
            compatibility_factory.runtime_from_config,
            owned_factory.runtime_from_config,
        )


if __name__ == "__main__":
    unittest.main()
