from __future__ import annotations

import unittest

import agentsassemble.antigravity_resident as compatibility_antigravity_resident
import agentsassemble.bridge_protocol as compatibility_bridge_protocol
import agentsassemble.bridge_report_tracker as compatibility_bridge_tracker
import agentsassemble.claude_resident as compatibility_claude_resident
import agentsassemble.claude_transcript as compatibility_claude_transcript
import agentsassemble.codex_app_server_runtime as compatibility_codex_app_server
import agentsassemble.codex_app_server_live_runtime as compatibility_codex_app_server_live
import agentsassemble.codex_resident as compatibility_codex_resident
import agentsassemble.codex_session_ids as compatibility_codex_session_ids
import agentsassemble.codex_stream as compatibility_codex_stream
import agentsassemble.cursor_resident as compatibility_cursor_resident
import agentsassemble.deepseek_runtime as compatibility_deepseek
import agentsassemble.grok_acp_runtime as compatibility_grok_acp
import agentsassemble.grok_resident as compatibility_grok_resident
import agentsassemble.hermes_resident as compatibility_hermes_resident
import agentsassemble.kiro_resident as compatibility_kiro_resident
import agentsassemble.live_cli as compatibility_live_cli
import agentsassemble.live_cli_output as compatibility_live_cli_output
import agentsassemble.live_cli_transcripts as compatibility_live_cli_transcripts
import agentsassemble.native_cli_providers as compatibility_launch_specs
import agentsassemble.opencode_runtime as compatibility_opencode
import agentsassemble.process_environment as compatibility_process_environment
import agentsassemble.provider_capabilities as compatibility_capabilities
import agentsassemble.provider_auth as compatibility_auth
import agentsassemble.provider_catalog as compatibility_catalog
import agentsassemble.provider_model_verification as compatibility_model_verification
import agentsassemble.provider_runtime_config as compatibility_config
import agentsassemble.provider_runtime_contracts as compatibility_contracts
import agentsassemble.provider_runtime_factory as compatibility_factory
import agentsassemble.provider_secrets as compatibility_secrets
import agentsassemble.provider_sessions as compatibility_sessions
import agentsassemble.room_api_provider as compatibility_api
import agentsassemble.windows_conpty as compatibility_windows_conpty
from agentsassemble.providers import api as owned_api
from agentsassemble.providers import antigravity_resident as owned_antigravity_resident
from agentsassemble.providers import catalog as owned_catalog
from agentsassemble.providers import bridge_protocol as owned_bridge_protocol
from agentsassemble.providers import bridge_report_tracker as owned_bridge_tracker
from agentsassemble.providers import capabilities as owned_capabilities
from agentsassemble.providers import claude_resident as owned_claude_resident
from agentsassemble.providers import claude_transcript as owned_claude_transcript
from agentsassemble.providers import codex_app_server as owned_codex_app_server
from agentsassemble.providers import codex_app_server_live as owned_codex_app_server_live
from agentsassemble.providers import codex_resident as owned_codex_resident
from agentsassemble.providers import codex_session_ids as owned_codex_session_ids
from agentsassemble.providers import codex_stream as owned_codex_stream
from agentsassemble.providers import cursor_resident as owned_cursor_resident
from agentsassemble.providers import deepseek as owned_deepseek
from agentsassemble.providers import grok_acp as owned_grok_acp
from agentsassemble.providers import grok_resident as owned_grok_resident
from agentsassemble.providers import hermes_resident as owned_hermes_resident
from agentsassemble.providers import kiro_resident as owned_kiro_resident
from agentsassemble.providers import launch_specs as owned_launch_specs
from agentsassemble.providers import live_cli as owned_live_cli
from agentsassemble.providers import live_cli_output as owned_live_cli_output
from agentsassemble.providers import live_cli_transcripts as owned_live_cli_transcripts
from agentsassemble.providers import model_verification as owned_model_verification
from agentsassemble.providers import opencode as owned_opencode
from agentsassemble.providers import process_environment as owned_process_environment
from agentsassemble.providers import auth as owned_auth
from agentsassemble.providers import runtime_config as owned_config
from agentsassemble.providers import runtime_contracts as owned_contracts
from agentsassemble.providers import runtime_factory as owned_factory
from agentsassemble.providers import secrets as owned_secrets
from agentsassemble.providers import sessions as owned_sessions
from agentsassemble.providers import turn_input as owned_turn_input
from agentsassemble.providers import windows_conpty as owned_windows_conpty


class ProviderPackageTests(unittest.TestCase):
    def test_provider_capability_root_module_exports_owned_discovery(self) -> None:
        for name in (
            "CatalogListener",
            "ProbeRunner",
            "ProviderCapabilityCatalog",
            "ProviderCatalogSelectionError",
            "ValidatedProviderSelection",
            "provider_catalog_payload",
            "provider_catalog_snapshot",
        ):
            self.assertIs(
                getattr(compatibility_capabilities, name),
                getattr(owned_capabilities, name),
            )
        self.assertIs(
            compatibility_capabilities.PROVIDER_CAPABILITIES,
            owned_capabilities.PROVIDER_CAPABILITIES,
        )

    def test_native_cli_provider_root_module_exports_owned_launch_specs(self) -> None:
        for name in (
            "NativeCliProviderDefinition",
            "NativeCliProviderSpec",
            "StoredProviderProfileError",
            "UnsupportedNativeCliProvider",
            "default_native_cli_provider_specs",
            "native_cli_provider_catalog_payload",
            "native_cli_provider_definition",
            "native_cli_provider_spec_from_config",
            "native_cli_provider_spec_from_payload",
            "native_cli_provider_spec_from_stored_session_strict",
            "validate_native_cli_provider_spec",
        ):
            self.assertIs(
                getattr(compatibility_launch_specs, name),
                getattr(owned_launch_specs, name),
            )
        self.assertIs(
            compatibility_launch_specs.PROVIDER_CATALOG,
            owned_launch_specs.PROVIDER_CATALOG,
        )

    def test_codex_app_server_root_module_exports_owned_runtime(self) -> None:
        for name in (
            "CodexAppServerRuntime",
            "CodexAppServerRuntimeManager",
            "ProcessFactory",
            "_app_server_progress_text",
            "_codex_app_server_turn_start_settings",
            "clean_agent_session_provider_kind",
            "clean_provider_session_id",
            "codex_app_server_runtime_command",
            "runtime_profile_key",
            "runtime_profile_settings",
        ):
            self.assertIs(
                getattr(compatibility_codex_app_server, name),
                getattr(owned_codex_app_server, name),
            )
        self.assertEqual(
            compatibility_codex_app_server.CODEX_APP_SERVER_STDERR_TAIL_CHARS,
            owned_codex_app_server.CODEX_APP_SERVER_STDERR_TAIL_CHARS,
        )

    def test_provider_turn_input_preserves_explicit_and_json_fallback(self) -> None:
        self.assertEqual(
            owned_turn_input.agent_turn_prompt({"provider_input": "visible"}),
            "visible",
        )
        fallback = owned_turn_input.agent_turn_prompt(
            {"room_id": "room-a", "current_turn_instruction": "안녕"}
        )
        self.assertIn("one AgentsAssemble room turn", fallback)
        self.assertIn('"current_turn_instruction": "안녕"', fallback)

    def test_kiro_resident_root_module_exports_owned_adapter(self) -> None:
        for name in (
            "KiroResidentCommandRunner",
            "clean_kiro_reply",
            "default_kiro_resident_command",
            "extract_kiro_session_ids",
            "kiro_command_check",
            "kiro_provider_connection_check",
        ):
            self.assertIs(
                getattr(compatibility_kiro_resident, name),
                getattr(owned_kiro_resident, name),
            )

    def test_hermes_resident_root_module_exports_owned_adapter(self) -> None:
        for name in (
            "HermesResidentCommandRunner",
            "HermesResidentRuntimeError",
            "HermesResidentValueError",
            "clean_hermes_session_id",
            "default_hermes_resident_command",
            "hermes_command_check",
            "hermes_error_category",
            "hermes_provider_connection_check",
        ):
            self.assertIs(
                getattr(compatibility_hermes_resident, name),
                getattr(owned_hermes_resident, name),
            )
        self.assertEqual(
            compatibility_hermes_resident.HERMES_MISSING_SESSION_ID,
            owned_hermes_resident.HERMES_MISSING_SESSION_ID,
        )

    def test_cursor_resident_root_module_exports_owned_adapter(self) -> None:
        for name in (
            "CursorResidentCommandRunner",
            "CursorResidentRuntimeError",
            "CursorResidentValueError",
            "clean_cursor_chat_id",
            "cursor_auth_check",
            "cursor_command_check",
            "cursor_error_category",
            "cursor_generic_resident_guard_check",
            "cursor_generic_resident_guard_error",
            "cursor_login_required_message",
            "cursor_provider_connection_check",
            "cursor_terminal_session_superseded_check",
            "cursor_terminal_session_superseded_error",
            "default_cursor_resident_command",
        ):
            self.assertIs(
                getattr(compatibility_cursor_resident, name),
                getattr(owned_cursor_resident, name),
            )
        self.assertEqual(
            compatibility_cursor_resident.CURSOR_GENERIC_RESIDENT_UNSUPPORTED_MESSAGE,
            owned_cursor_resident.CURSOR_GENERIC_RESIDENT_UNSUPPORTED_MESSAGE,
        )

    def test_grok_resident_root_module_exports_owned_adapter(self) -> None:
        for name in (
            "GrokResidentCommandRunner",
            "GrokResidentRuntimeError",
            "GrokResidentValueError",
            "clean_grok_session_id",
            "default_grok_resident_command",
            "grok_auth_check",
            "grok_command_check",
            "grok_error_category",
            "grok_login_required_message",
            "grok_provider_connection_check",
            "parse_grok_stream_line",
        ):
            self.assertIs(
                getattr(compatibility_grok_resident, name),
                getattr(owned_grok_resident, name),
            )
        self.assertEqual(
            compatibility_grok_resident.GROK_JSON_PARSE_FAILURE,
            owned_grok_resident.GROK_JSON_PARSE_FAILURE,
        )

    def test_antigravity_resident_root_module_exports_owned_adapter(self) -> None:
        for name in (
            "AntigravityResidentCommandRunner",
            "AntigravityResidentRuntimeError",
            "AntigravityResidentValueError",
            "antigravity_auth_check",
            "antigravity_command_check",
            "antigravity_error_category",
            "antigravity_provider_connection_check",
            "clean_antigravity_conversation_id",
            "default_antigravity_resident_command",
        ):
            self.assertIs(
                getattr(compatibility_antigravity_resident, name),
                getattr(owned_antigravity_resident, name),
            )
        self.assertEqual(
            compatibility_antigravity_resident.ANTIGRAVITY_BACKEND_ERROR,
            owned_antigravity_resident.ANTIGRAVITY_BACKEND_ERROR,
        )

    def test_codex_resident_root_module_exports_owned_adapter(self) -> None:
        for name in (
            "CODEX_EXEC_SAFETY_FLAGS",
            "CodexResidentCommandRunner",
            "codex_auth_check",
            "codex_exec_prefix",
            "codex_login_required_message",
            "codex_provider_connection_check",
            "default_codex_resident_command",
        ):
            self.assertIs(
                getattr(compatibility_codex_resident, name),
                getattr(owned_codex_resident, name),
            )

    def test_codex_app_server_live_root_module_exports_owned_runtime(self) -> None:
        self.assertIs(
            compatibility_codex_app_server_live.CodexAppServerLiveRuntime,
            owned_codex_app_server_live.CodexAppServerLiveRuntime,
        )
        self.assertIs(
            compatibility_codex_app_server_live._codex_activity,
            owned_codex_app_server_live._codex_activity,
        )

    def test_room_api_provider_root_module_exports_owned_contract(self) -> None:
        for name in (
            "ApiProviderError",
            "ApiReply",
            "ApiUsage",
            "api_error_category",
            "chat_completion",
            "chat_completion_with_fallback",
            "record_api_usage",
            "run_api_call",
        ):
            with self.subTest(name=name):
                self.assertIs(
                    getattr(compatibility_api, name),
                    getattr(owned_api, name),
                )

    def test_claude_resident_root_module_exports_owned_contract(self) -> None:
        for name in (
            "CLAUDE_ANSWER_MARKER",
            "CLAUDE_CODE_PRINT_FLAGS",
            "CLAUDE_CODE_PRINT_MODE_MESSAGE",
            "_strip_envelope_leak",
            "_strip_terminal_ansi",
            "claude_answer_ready",
            "claude_code_print_mode_resident_check",
            "claude_code_print_mode_resident_error",
            "extract_claude_terminal_message",
            "render_terminal_screen",
        ):
            with self.subTest(name=name):
                self.assertIs(
                    getattr(compatibility_claude_resident, name),
                    getattr(owned_claude_resident, name),
                )

    def test_claude_transcript_root_module_exports_owned_contract(self) -> None:
        for name in (
            "ClaudeTranscriptTailer",
            "find_claude_transcript",
            "generate_claude_session_id",
            "parse_claude_transcript_line",
            "tail_until",
        ):
            with self.subTest(name=name):
                self.assertIs(
                    getattr(compatibility_claude_transcript, name),
                    getattr(owned_claude_transcript, name),
                )

    def test_codex_session_id_root_module_exports_owned_contract(self) -> None:
        for name in (
            "CODEX_SESSION_ID_PATTERN",
            "CODEX_SESSION_ID_RE",
            "CODEX_SESSION_LABEL_RE",
            "extract_codex_session_id",
        ):
            with self.subTest(name=name):
                self.assertIs(
                    getattr(compatibility_codex_session_ids, name),
                    getattr(owned_codex_session_ids, name),
                )

    def test_codex_stream_root_module_exports_owned_functions(self) -> None:
        self.assertIs(
            compatibility_codex_stream.parse_codex_stream,
            owned_codex_stream.parse_codex_stream,
        )
        self.assertIs(
            compatibility_codex_stream.parse_codex_stream_line,
            owned_codex_stream.parse_codex_stream_line,
        )

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

    def test_live_cli_root_module_exports_owned_runtime_contract(self) -> None:
        for name in (
            "GENERAL_ROOM_ID",
            "PARENT_AGENT_SESSION_ENV_KEYS",
            "AgentRuntime",
            "ApiRuntime",
            "LiveCliRuntime",
            "LiveCliSession",
            "live_cli_supported",
        ):
            with self.subTest(name=name):
                self.assertIs(
                    getattr(compatibility_live_cli, name),
                    getattr(owned_live_cli, name),
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

    def test_provider_auth_root_module_exports_owned_functions(self) -> None:
        self.assertIs(
            compatibility_auth.provider_auth_error_message,
            owned_auth.provider_auth_error_message,
        )
        self.assertIs(
            compatibility_auth.provider_login_required_message,
            owned_auth.provider_login_required_message,
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

    def test_provider_sessions_root_module_exports_owned_reader(self) -> None:
        self.assertIs(
            compatibility_sessions.list_provider_sessions,
            owned_sessions.list_provider_sessions,
        )

    def test_model_verification_root_module_exports_owned_policy(self) -> None:
        self.assertIs(
            compatibility_model_verification.model_observation_matches,
            owned_model_verification.model_observation_matches,
        )
        self.assertIs(
            compatibility_model_verification.model_verification_status,
            owned_model_verification.model_verification_status,
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
