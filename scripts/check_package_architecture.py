"""Guard the package root against new unowned product modules."""
from __future__ import annotations

import argparse
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

try:
    from scripts.generate_package_map import PackageGraph, load_package_graph
except ModuleNotFoundError as error:
    if error.name != "scripts":
        raise
    from generate_package_map import PackageGraph, load_package_graph


BASELINE_RELATIVE_PATH = Path("docs/product/PACKAGE_ROOT_BASELINE.txt")
CYCLE_BASELINE_RELATIVE_PATH = Path("docs/product/PACKAGE_CYCLE_BASELINE.txt")
CYCLE_REPORT_RELATIVE_PATH = Path("docs/product/PACKAGE_CYCLES.md")
PERMANENT_ROOT_ENTRYPOINTS = frozenset({"__init__.py", "cli.py", "gui.py"})
CURRENT_CORE_PACKAGE_ROOTS = frozenset(
    {
        "admission",
        "application",
        "diagnostics",
        "identity",
        "persistence",
        "providers",
        "room",
        "web",
    }
)
DOMAIN_PACKAGE_ROOTS = frozenset(
    {"admission", "diagnostics", "identity", "providers", "room"}
)


@dataclass(frozen=True)
class CompatibilityShim:
    replacement_import: str
    removal_gate: str
    known_callers: tuple[str, ...]
    introduced_in: str


# Root modules that have moved remain explicit, temporary compatibility
# boundaries. Historical presence in the root baseline does not exempt a moved
# module from recording its replacement, callers, and removal gate here.
ROOT_COMPATIBILITY_SHIMS: dict[str, CompatibilityShim] = {
    "cleanup_report.py": CompatibilityShim(
        replacement_import="agentsassemble.diagnostics.cleanup",
        removal_gate=(
            "No direct imports use agentsassemble.cleanup_report for one "
            "compatibility window."
        ),
        known_callers=("tests/test_diagnostics_package.py",),
        introduced_in="Milestone 6.8 shared cleanup diagnostics move",
    ),
    "antigravity_resident.py": CompatibilityShim(
        replacement_import="agentsassemble.providers.antigravity_resident",
        removal_gate=(
            "No direct imports use agentsassemble.antigravity_resident for one "
            "compatibility window."
        ),
        known_callers=("tests/test_provider_package.py",),
        introduced_in="Milestone 5.23 Antigravity resident adapter move",
    ),
    "bridge_protocol.py": CompatibilityShim(
        replacement_import="agentsassemble.providers.bridge_protocol",
        removal_gate=(
            "No direct imports use agentsassemble.bridge_protocol for one "
            "compatibility window."
        ),
        known_callers=("tests/test_provider_package.py",),
        introduced_in="Milestone 5.5 provider bridge protocol move",
    ),
    "bridge_report_tracker.py": CompatibilityShim(
        replacement_import="agentsassemble.providers.bridge_report_tracker",
        removal_gate=(
            "No direct imports use agentsassemble.bridge_report_tracker for one "
            "compatibility window."
        ),
        known_callers=("tests/test_provider_package.py",),
        introduced_in="Milestone 5.5 provider bridge protocol move",
    ),
    "bridge_stop_confirmation.py": CompatibilityShim(
        replacement_import="agentsassemble.room.bridge_stop_confirmation",
        removal_gate=(
            "No direct imports use agentsassemble.bridge_stop_confirmation for "
            "one compatibility window."
        ),
        known_callers=("tests/test_room_package.py",),
        introduced_in="Milestone 6.6 room bridge-stop confirmation move",
    ),
    "claude_transcript.py": CompatibilityShim(
        replacement_import="agentsassemble.providers.claude_transcript",
        removal_gate=(
            "No direct imports use agentsassemble.claude_transcript for one "
            "compatibility window."
        ),
        known_callers=("tests/test_provider_package.py",),
        introduced_in="Milestone 5.18 Claude transcript parser move",
    ),
    "claude_resident.py": CompatibilityShim(
        replacement_import="agentsassemble.providers.claude_resident",
        removal_gate=(
            "No direct imports use agentsassemble.claude_resident for one "
            "compatibility window."
        ),
        known_callers=("tests/test_provider_package.py",),
        introduced_in="Milestone 5.19 Claude resident TUI move",
    ),
    "codex_session_ids.py": CompatibilityShim(
        replacement_import="agentsassemble.providers.codex_session_ids",
        removal_gate=(
            "No direct imports use agentsassemble.codex_session_ids for one "
            "compatibility window."
        ),
        known_callers=("tests/test_provider_package.py",),
        introduced_in="Milestone 5.17 Codex provider parser move",
    ),
    "codex_app_server_live_runtime.py": CompatibilityShim(
        replacement_import="agentsassemble.providers.codex_app_server_live",
        removal_gate=(
            "No direct imports use agentsassemble.codex_app_server_live_runtime "
            "for one compatibility window."
        ),
        known_callers=("tests/test_provider_package.py",),
        introduced_in="Milestone 5.21 Codex app-server live wrapper move",
    ),
    "codex_app_server_runtime.py": CompatibilityShim(
        replacement_import="agentsassemble.providers.codex_app_server",
        removal_gate=(
            "No direct imports use agentsassemble.codex_app_server_runtime for "
            "one compatibility window."
        ),
        known_callers=("tests/test_provider_package.py",),
        introduced_in="Milestone 5.28 Codex app-server runtime move",
    ),
    "codex_resident.py": CompatibilityShim(
        replacement_import="agentsassemble.providers.codex_resident",
        removal_gate=(
            "No direct imports use agentsassemble.codex_resident for one "
            "compatibility window."
        ),
        known_callers=("tests/test_provider_package.py",),
        introduced_in="Milestone 5.22 Codex resident adapter move",
    ),
    "codex_stream.py": CompatibilityShim(
        replacement_import="agentsassemble.providers.codex_stream",
        removal_gate=(
            "No direct imports use agentsassemble.codex_stream for one "
            "compatibility window."
        ),
        known_callers=("tests/test_provider_package.py",),
        introduced_in="Milestone 5.17 Codex provider parser move",
    ),
    "cursor_resident.py": CompatibilityShim(
        replacement_import="agentsassemble.providers.cursor_resident",
        removal_gate=(
            "No direct imports use agentsassemble.cursor_resident for one "
            "compatibility window."
        ),
        known_callers=("tests/test_provider_package.py",),
        introduced_in="Milestone 5.25 Cursor resident adapter move",
    ),
    "deepseek_runtime.py": CompatibilityShim(
        replacement_import="agentsassemble.providers.deepseek",
        removal_gate=(
            "No direct imports use agentsassemble.deepseek_runtime for one "
            "compatibility window."
        ),
        known_callers=("tests/test_provider_package.py",),
        introduced_in="Milestone 5.7 DeepSeek runtime move",
    ),
    "frontend_runtime.py": CompatibilityShim(
        replacement_import="agentsassemble.web.frontend_runtime",
        removal_gate=(
            "No direct imports use agentsassemble.frontend_runtime for one "
            "compatibility window."
        ),
        known_callers=("tests/test_web_transport_package.py",),
        introduced_in="Milestone 4.10 frontend build inspection move",
    ),
    "application_transaction.py": CompatibilityShim(
        replacement_import="agentsassemble.application.transaction",
        removal_gate=(
            "No direct imports use agentsassemble.application_transaction for "
            "one compatibility window."
        ),
        known_callers=("tests/test_application_package.py",),
        introduced_in="Milestone 4.5 application package bootstrap",
    ),
    "room_users.py": CompatibilityShim(
        replacement_import="agentsassemble.application.room_users",
        removal_gate=(
            "No direct imports use agentsassemble.room_users for one "
            "compatibility window."
        ),
        known_callers=("tests/test_application_package.py",),
        introduced_in="Milestone 4.12 application room-user facade move",
    ),
    "session_run_monitor.py": CompatibilityShim(
        replacement_import="agentsassemble.application.session_run_monitor",
        removal_gate=(
            "No direct imports use agentsassemble.session_run_monitor for one "
            "compatibility window."
        ),
        known_callers=("tests/test_application_package.py",),
        introduced_in="Milestone 4.13 application session-run monitor move",
    ),
    "sse_cadence.py": CompatibilityShim(
        replacement_import="agentsassemble.web.sse_cadence",
        removal_gate=(
            "No direct imports use agentsassemble.sse_cadence for one "
            "compatibility window."
        ),
        known_callers=("tests/test_web_transport_package.py",),
        introduced_in="Milestone 4.14 web transport cadence move",
    ),
    "room_websocket.py": CompatibilityShim(
        replacement_import="agentsassemble.web.websocket_codec",
        removal_gate=(
            "No direct imports use agentsassemble.room_websocket for one "
            "compatibility window."
        ),
        known_callers=("tests/test_web_transport_package.py",),
        introduced_in="Milestone 4.18 WebSocket protocol codec move",
    ),
    "ws_room_client.py": CompatibilityShim(
        replacement_import="agentsassemble.web.room_client",
        removal_gate=(
            "No direct imports or monkeypatch targets use "
            "agentsassemble.ws_room_client for one compatibility window."
        ),
        known_callers=("tests/test_web_transport_package.py",),
        introduced_in="Milestone 4.19 canonical room client move",
    ),
    "ws_room_session.py": CompatibilityShim(
        replacement_import="agentsassemble.web.room_session",
        removal_gate=(
            "No direct imports use agentsassemble.ws_room_session for one "
            "compatibility window."
        ),
        known_callers=("tests/test_web_transport_package.py",),
        introduced_in="Milestone 4.20 room WebSocket session move",
    ),
    "stable_entry.py": CompatibilityShim(
        replacement_import="agentsassemble.application.stable_entry",
        removal_gate=(
            "No direct imports use agentsassemble.stable_entry for one "
            "compatibility window."
        ),
        known_callers=("tests/test_application_package.py",),
        introduced_in="Milestone 4.15 stable public-entry service move",
    ),
    "public_invite_runtime.py": CompatibilityShim(
        replacement_import="agentsassemble.application.public_invite_runtime",
        removal_gate=(
            "No direct imports use agentsassemble.public_invite_runtime for one "
            "compatibility window."
        ),
        known_callers=("tests/test_application_package.py",),
        introduced_in="Milestone 4.16 public invite runtime move",
    ),
    "public_tunnel.py": CompatibilityShim(
        replacement_import="agentsassemble.application.public_tunnel",
        removal_gate=(
            "No direct imports or monkeypatch targets use "
            "agentsassemble.public_tunnel for one compatibility window."
        ),
        known_callers=("tests/test_application_package.py",),
        introduced_in="Milestone 4.17 public tunnel manager move",
    ),
    "gui_mafia_http.py": CompatibilityShim(
        replacement_import="agentsassemble.features.mafia.routes",
        removal_gate=(
            "No direct imports use agentsassemble.gui_mafia_http for one "
            "compatibility window."
        ),
        known_callers=("tests/test_feature_routes_package.py",),
        introduced_in="Milestone 4.8 optional feature route packages",
    ),
    "grok_acp_runtime.py": CompatibilityShim(
        replacement_import="agentsassemble.providers.grok_acp",
        removal_gate=(
            "No direct imports use agentsassemble.grok_acp_runtime for one "
            "compatibility window."
        ),
        known_callers=("tests/test_provider_package.py",),
        introduced_in="Milestone 5.10 Grok ACP runtime move",
    ),
    "grok_resident.py": CompatibilityShim(
        replacement_import="agentsassemble.providers.grok_resident",
        removal_gate=(
            "No direct imports use agentsassemble.grok_resident for one "
            "compatibility window."
        ),
        known_callers=("tests/test_provider_package.py",),
        introduced_in="Milestone 5.24 Grok resident adapter move",
    ),
    "hermes_resident.py": CompatibilityShim(
        replacement_import="agentsassemble.providers.hermes_resident",
        removal_gate=(
            "No direct imports use agentsassemble.hermes_resident for one "
            "compatibility window."
        ),
        known_callers=("tests/test_provider_package.py",),
        introduced_in="Milestone 5.26 Hermes resident adapter move",
    ),
    "kiro_resident.py": CompatibilityShim(
        replacement_import="agentsassemble.providers.kiro_resident",
        removal_gate=(
            "No direct imports use agentsassemble.kiro_resident for one "
            "compatibility window."
        ),
        known_callers=("tests/test_provider_package.py",),
        introduced_in="Milestone 5.27 Kiro resident adapter move",
    ),
    "live_cli.py": CompatibilityShim(
        replacement_import="agentsassemble.providers.live_cli",
        removal_gate=(
            "No direct imports use agentsassemble.live_cli for one "
            "compatibility window."
        ),
        known_callers=("tests/test_provider_package.py",),
        introduced_in="Milestone 5.13 POSIX live CLI runtime move",
    ),
    "native_cli_providers.py": CompatibilityShim(
        replacement_import="agentsassemble.providers.launch_specs",
        removal_gate=(
            "No direct imports use agentsassemble.native_cli_providers for one "
            "compatibility window."
        ),
        known_callers=("tests/test_provider_package.py",),
        introduced_in="Milestone 5.29 provider launch-spec move",
    ),
    "provider_capabilities.py": CompatibilityShim(
        replacement_import="agentsassemble.providers.capabilities",
        removal_gate=(
            "No direct imports use agentsassemble.provider_capabilities for one "
            "compatibility window."
        ),
        known_callers=("tests/test_provider_package.py",),
        introduced_in="Milestone 5.30 provider capability move",
    ),
    "room_provider_sync_cursor.py": CompatibilityShim(
        replacement_import="agentsassemble.providers.sync_cursor",
        removal_gate=(
            "No direct imports use agentsassemble.room_provider_sync_cursor "
            "for one compatibility window."
        ),
        known_callers=("tests/test_provider_package.py",),
        introduced_in="Milestone 6.9 provider sync cursor move",
    ),
    "room_agent_bridge.py": CompatibilityShim(
        replacement_import="agentsassemble.providers.agent_bridge",
        removal_gate=(
            "No direct imports use agentsassemble.room_agent_bridge and no "
            "external launch path needs its compatibility module entrypoint "
            "for one compatibility window."
        ),
        known_callers=(
            "tests/test_application_package.py",
            "tests/test_provider_package.py",
        ),
        introduced_in="Milestone 5.31 provider Agent Bridge move",
    ),
    "room_bridge_process.py": CompatibilityShim(
        replacement_import="agentsassemble.providers.bridge_process",
        removal_gate=(
            "No direct imports use agentsassemble.room_bridge_process for one "
            "compatibility window."
        ),
        known_callers=("tests/test_provider_package.py",),
        introduced_in="Milestone 5.32 provider bridge-process move",
    ),
    "room_channels.py": CompatibilityShim(
        replacement_import="agentsassemble.room.channels",
        removal_gate=(
            "No direct imports use agentsassemble.room_channels for one "
            "compatibility window."
        ),
        known_callers=("tests/test_room_package.py",),
        introduced_in="Milestone 6.18 room channel rules move",
    ),
    "room_commands.py": CompatibilityShim(
        replacement_import="agentsassemble.room.commands",
        removal_gate=(
            "No direct imports use agentsassemble.room_commands for one "
            "compatibility window."
        ),
        known_callers=("tests/test_room_package.py",),
        introduced_in="Milestone 6.2 room commands and projection move",
    ),
    "room_command_uow.py": CompatibilityShim(
        replacement_import="agentsassemble.room.command_uow",
        removal_gate=(
            "No direct imports use agentsassemble.room_command_uow for one "
            "compatibility window."
        ),
        known_callers=("tests/test_room_package.py",),
        introduced_in="Milestone 6.4 room command transaction move",
    ),
    "room_context.py": CompatibilityShim(
        replacement_import="agentsassemble.room.context",
        removal_gate=(
            "No direct imports use agentsassemble.room_context for one "
            "compatibility window."
        ),
        known_callers=(
            "tests/test_agent_session_cli.py",
            "tests/test_room_package.py",
        ),
        introduced_in="Milestone 6.10 room context projection move",
    ),
    "room_turn_context.py": CompatibilityShim(
        replacement_import="agentsassemble.room.turn_context",
        removal_gate=(
            "No direct imports use agentsassemble.room_turn_context for one "
            "compatibility window."
        ),
        known_callers=(
            "tests/test_agent_session_room_store.py",
            "tests/test_room_package.py",
        ),
        introduced_in="Milestone 6.11 room turn-context move",
    ),
    "room_turn_coordinator.py": CompatibilityShim(
        replacement_import="agentsassemble.room.turn_coordinator",
        removal_gate=(
            "No direct imports use agentsassemble.room_turn_coordinator for one "
            "compatibility window."
        ),
        known_callers=("tests/test_room_package.py",),
        introduced_in="Milestone 6.12 room turn coordinator move",
    ),
    "room_user_preferences.py": CompatibilityShim(
        replacement_import="agentsassemble.room.user_preferences",
        removal_gate=(
            "No direct imports use agentsassemble.room_user_preferences for one "
            "compatibility window."
        ),
        known_callers=("tests/test_room_package.py",),
        introduced_in="Milestone 6.19 room user preferences record move",
    ),
    "room_errors.py": CompatibilityShim(
        replacement_import="agentsassemble.room.errors",
        removal_gate=(
            "No direct imports use agentsassemble.room_errors for one "
            "compatibility window."
        ),
        known_callers=("tests/test_room_package.py",),
        introduced_in="Milestone 6.1 room contracts move",
    ),
    "room_event_broker.py": CompatibilityShim(
        replacement_import="agentsassemble.room.event_broker",
        removal_gate=(
            "No direct imports use agentsassemble.room_event_broker for one "
            "compatibility window."
        ),
        known_callers=("tests/test_room_package.py",),
        introduced_in="Milestone 6.5 room event broker move",
    ),
    "room_global_settings.py": CompatibilityShim(
        replacement_import="agentsassemble.room.global_settings",
        removal_gate=(
            "No direct imports use agentsassemble.room_global_settings for one "
            "compatibility window."
        ),
        known_callers=("tests/test_room_package.py",),
        introduced_in="Milestone 6.19 room global settings record move",
    ),
    "room_projection.py": CompatibilityShim(
        replacement_import="agentsassemble.room.projection",
        removal_gate=(
            "No direct imports use agentsassemble.room_projection for one "
            "compatibility window."
        ),
        known_callers=("tests/test_room_package.py",),
        introduced_in="Milestone 6.2 room commands and projection move",
    ),
    "room_repository.py": CompatibilityShim(
        replacement_import="agentsassemble.room.repository",
        removal_gate=(
            "No direct imports use agentsassemble.room_repository for one "
            "compatibility window."
        ),
        known_callers=("tests/test_room_package.py",),
        introduced_in="Milestone 6.3 room repository contract move",
    ),
    "room_repository_records.py": CompatibilityShim(
        replacement_import="agentsassemble.room.repository_records",
        removal_gate=(
            "No direct imports use agentsassemble.room_repository_records for "
            "one compatibility window."
        ),
        known_callers=("tests/test_room_package.py",),
        introduced_in="Milestone 6.20 room repository record normalization move",
    ),
    "room_types.py": CompatibilityShim(
        replacement_import="agentsassemble.room.types",
        removal_gate=(
            "No direct imports use agentsassemble.room_types for one "
            "compatibility window."
        ),
        known_callers=("tests/test_room_package.py",),
        introduced_in="Milestone 6.1 room contracts move",
    ),
    "gui_observability_http.py": CompatibilityShim(
        replacement_import="agentsassemble.web.routes.observability",
        removal_gate=(
            "No direct imports or monkeypatch targets use "
            "agentsassemble.gui_observability_http for one compatibility window."
        ),
        known_callers=("tests/test_web_routes_package.py",),
        introduced_in="Milestone 4.9 observability route package",
    ),
    "gui_retired_http.py": CompatibilityShim(
        replacement_import="agentsassemble.web.routes.retired",
        removal_gate=(
            "No direct imports use agentsassemble.gui_retired_http for one "
            "compatibility window, and the v0.2 tombstone audit permits removal."
        ),
        known_callers=("tests/test_web_routes_package.py",),
        introduced_in="Milestone 4.10 retired route package",
    ),
    "gui_side_chat_http.py": CompatibilityShim(
        replacement_import="agentsassemble.features.side_chat.routes",
        removal_gate=(
            "No direct imports or monkeypatch targets use "
            "agentsassemble.gui_side_chat_http for one compatibility window."
        ),
        known_callers=("tests/test_feature_routes_package.py",),
        introduced_in="Milestone 4.8 optional feature route packages",
    ),
    "side_chat.py": CompatibilityShim(
        replacement_import="agentsassemble.features.side_chat.service",
        removal_gate=(
            "No direct imports use agentsassemble.side_chat for one "
            "compatibility window."
        ),
        known_callers=("tests/test_feature_routes_package.py",),
        introduced_in="Milestone 6.13 optional side-chat service move",
    ),
    "mafia_game.py": CompatibilityShim(
        replacement_import="agentsassemble.features.mafia.game",
        removal_gate=(
            "No direct imports use agentsassemble.mafia_game for one "
            "compatibility window."
        ),
        known_callers=(
            "tests/test_feature_routes_package.py",
            "tests/test_mafia_game.py",
        ),
        introduced_in="Milestone 6.23 optional Mafia game service move",
    ),
    "legacy_live_agent_preflight.py": CompatibilityShim(
        replacement_import="agentsassemble.legacy.live_agent.preflight",
        removal_gate=(
            "No direct imports use agentsassemble.legacy_live_agent_preflight "
            "for one compatibility window."
        ),
        known_callers=("tests/test_legacy_package.py",),
        introduced_in="Milestone 6.24 legacy resident preflight service move",
    ),
    "legacy_live_agent_engagement.py": CompatibilityShim(
        replacement_import="agentsassemble.legacy.live_agent.engagement",
        removal_gate=(
            "No direct imports use agentsassemble.legacy_live_agent_engagement "
            "for one compatibility window."
        ),
        known_callers=("tests/test_legacy_package.py",),
        introduced_in="Milestone 6.25 legacy resident engagement service move",
    ),
    "legacy_live_agent_diagnostics.py": CompatibilityShim(
        replacement_import="agentsassemble.legacy.live_agent.diagnostics",
        removal_gate=(
            "No direct imports use agentsassemble.legacy_live_agent_diagnostics "
            "for one compatibility window."
        ),
        known_callers=("tests/test_legacy_package.py",),
        introduced_in="Milestone 6.32 legacy resident diagnostics move",
    ),
    "legacy_live_agent_discovery.py": CompatibilityShim(
        replacement_import="agentsassemble.legacy.live_agent.discovery",
        removal_gate=(
            "No direct imports use agentsassemble.legacy_live_agent_discovery "
            "for one compatibility window."
        ),
        known_callers=("tests/test_legacy_package.py",),
        introduced_in="Milestone 6.39 legacy resident discovery move",
    ),
    "legacy_live_agent_health.py": CompatibilityShim(
        replacement_import="agentsassemble.legacy.live_agent.health",
        removal_gate=(
            "No direct imports use agentsassemble.legacy_live_agent_health for "
            "one compatibility window."
        ),
        known_callers=("tests/test_legacy_package.py",),
        introduced_in="Milestone 6.33 legacy resident health policy move",
    ),
    "legacy_live_agent_health_queries.py": CompatibilityShim(
        replacement_import="agentsassemble.legacy.live_agent.health_queries",
        removal_gate=(
            "No direct imports use "
            "agentsassemble.legacy_live_agent_health_queries for one "
            "compatibility window."
        ),
        known_callers=("tests/test_legacy_package.py",),
        introduced_in="Milestone 6.37 legacy resident health query move",
    ),
    "legacy_live_agent_observation_health.py": CompatibilityShim(
        replacement_import="agentsassemble.legacy.live_agent.observation_health",
        removal_gate=(
            "No direct imports use "
            "agentsassemble.legacy_live_agent_observation_health for one "
            "compatibility window."
        ),
        known_callers=("tests/test_legacy_package.py",),
        introduced_in="Milestone 6.34 legacy resident observation health move",
    ),
    "legacy_live_agent_official_reply.py": CompatibilityShim(
        replacement_import="agentsassemble.legacy.live_agent.official_reply",
        removal_gate=(
            "No direct imports or monkeypatch targets use "
            "agentsassemble.legacy_live_agent_official_reply for one "
            "compatibility window."
        ),
        known_callers=("tests/test_legacy_package.py",),
        introduced_in="Milestone 6.42 legacy resident official reply move",
    ),
    "legacy_live_agent_probe.py": CompatibilityShim(
        replacement_import="agentsassemble.legacy.live_agent.probe",
        removal_gate=(
            "No direct imports use agentsassemble.legacy_live_agent_probe for "
            "one compatibility window."
        ),
        known_callers=("tests/test_legacy_package.py",),
        introduced_in="Milestone 6.26 legacy resident probe service move",
    ),
    "legacy_live_agent_queries.py": CompatibilityShim(
        replacement_import="agentsassemble.legacy.live_agent.queries",
        removal_gate=(
            "No direct imports use agentsassemble.legacy_live_agent_queries "
            "for one compatibility window."
        ),
        known_callers=("tests/test_legacy_package.py",),
        introduced_in="Milestone 6.40 legacy resident query facade move",
    ),
    "legacy_live_agent_speech.py": CompatibilityShim(
        replacement_import="agentsassemble.legacy.live_agent.speech",
        removal_gate=(
            "No direct imports use agentsassemble.legacy_live_agent_speech "
            "for one compatibility window."
        ),
        known_callers=("tests/test_legacy_package.py",),
        introduced_in="Milestone 6.41 legacy resident speech service move",
    ),
    "legacy_live_agent_smoke.py": CompatibilityShim(
        replacement_import="agentsassemble.legacy.live_agent.smoke",
        removal_gate=(
            "No direct imports use agentsassemble.legacy_live_agent_smoke "
            "for one compatibility window."
        ),
        known_callers=("tests/test_legacy_package.py",),
        introduced_in="Milestone 6.43 legacy resident smoke facade move",
    ),
    "legacy_lobby_commands.py": CompatibilityShim(
        replacement_import="agentsassemble.legacy.meeting.lobby_commands",
        removal_gate=(
            "No direct imports use agentsassemble.legacy_lobby_commands for "
            "one compatibility window."
        ),
        known_callers=("tests/test_legacy_meeting_package.py",),
        introduced_in="Milestone 6.61 legacy meeting service package",
    ),
    "legacy_meeting_lifecycle.py": CompatibilityShim(
        replacement_import="agentsassemble.legacy.meeting.lifecycle",
        removal_gate=(
            "No direct imports use agentsassemble.legacy_meeting_lifecycle for "
            "one compatibility window."
        ),
        known_callers=("tests/test_legacy_meeting_package.py",),
        introduced_in="Milestone 6.61 legacy meeting service package",
    ),
    "legacy_meeting_operation_projection.py": CompatibilityShim(
        replacement_import="agentsassemble.legacy.meeting.operation_projection",
        removal_gate=(
            "No direct imports use "
            "agentsassemble.legacy_meeting_operation_projection for one "
            "compatibility window."
        ),
        known_callers=("tests/test_legacy_meeting_package.py",),
        introduced_in="Milestone 6.61 legacy meeting service package",
    ),
    "legacy_meeting_queries.py": CompatibilityShim(
        replacement_import="agentsassemble.legacy.meeting.queries",
        removal_gate=(
            "No direct imports use agentsassemble.legacy_meeting_queries for "
            "one compatibility window."
        ),
        known_callers=("tests/test_legacy_meeting_package.py",),
        introduced_in="Milestone 6.61 legacy meeting service package",
    ),
    "legacy_meeting_records.py": CompatibilityShim(
        replacement_import="agentsassemble.legacy.meeting.records",
        removal_gate=(
            "No direct imports use agentsassemble.legacy_meeting_records for "
            "one compatibility window."
        ),
        known_callers=("tests/test_legacy_meeting_package.py",),
        introduced_in="Milestone 6.61 legacy meeting service package",
    ),
    "legacy_official_rounds.py": CompatibilityShim(
        replacement_import="agentsassemble.legacy.meeting.official_rounds",
        removal_gate=(
            "No direct imports use agentsassemble.legacy_official_rounds for "
            "one compatibility window."
        ),
        known_callers=("tests/test_legacy_meeting_package.py",),
        introduced_in="Milestone 6.61 legacy meeting service package",
    ),
    "legacy_official_turns.py": CompatibilityShim(
        replacement_import="agentsassemble.legacy.meeting.official_turns",
        removal_gate=(
            "No direct imports use agentsassemble.legacy_official_turns for "
            "one compatibility window."
        ),
        known_callers=("tests/test_legacy_meeting_package.py",),
        introduced_in="Milestone 6.61 legacy meeting service package",
    ),
    "legacy_review_checkpoint.py": CompatibilityShim(
        replacement_import="agentsassemble.legacy.meeting.review_checkpoint",
        removal_gate=(
            "No direct imports use agentsassemble.legacy_review_checkpoint for "
            "one compatibility window."
        ),
        known_callers=("tests/test_legacy_meeting_package.py",),
        introduced_in="Milestone 6.61 legacy meeting service package",
    ),
    "legacy_turn_scheduler.py": CompatibilityShim(
        replacement_import="agentsassemble.legacy.meeting.turn_scheduler",
        removal_gate=(
            "No direct imports use agentsassemble.legacy_turn_scheduler for "
            "one compatibility window."
        ),
        known_callers=("tests/test_legacy_meeting_package.py",),
        introduced_in="Milestone 6.61 legacy meeting service package",
    ),
    "gui_legacy_live_agent_engagement_http.py": CompatibilityShim(
        replacement_import="agentsassemble.legacy.live_agent.http.engagement",
        removal_gate=(
            "No direct imports use "
            "agentsassemble.gui_legacy_live_agent_engagement_http for one "
            "compatibility window."
        ),
        known_callers=("tests/test_gui_legacy_live_agent_engagement_http.py",),
        introduced_in="Milestone 6.44 legacy resident engagement HTTP move",
    ),
    "gui_legacy_live_agent_room_session_http.py": CompatibilityShim(
        replacement_import="agentsassemble.legacy.live_agent.http.room_session",
        removal_gate=(
            "No direct imports use "
            "agentsassemble.gui_legacy_live_agent_room_session_http for one "
            "compatibility window."
        ),
        known_callers=("tests/test_gui_legacy_live_agent_room_session_http.py",),
        introduced_in="Milestone 6.45 legacy resident room-session HTTP move",
    ),
    "gui_legacy_live_agent_join_brief_http.py": CompatibilityShim(
        replacement_import="agentsassemble.legacy.live_agent.http.join_brief",
        removal_gate=(
            "No direct imports use "
            "agentsassemble.gui_legacy_live_agent_join_brief_http for one "
            "compatibility window."
        ),
        known_callers=("tests/test_gui_legacy_live_agent_join_brief_http.py",),
        introduced_in="Milestone 6.46 legacy resident join-brief HTTP move",
    ),
    "gui_legacy_live_agent_speech_http.py": CompatibilityShim(
        replacement_import="agentsassemble.legacy.live_agent.http.speech",
        removal_gate=(
            "No direct imports use "
            "agentsassemble.gui_legacy_live_agent_speech_http for one "
            "compatibility window."
        ),
        known_callers=("tests/test_gui_legacy_live_agent_speech_http.py",),
        introduced_in="Milestone 6.47 legacy resident speech HTTP move",
    ),
    "gui_legacy_live_agent_official_reply_http.py": CompatibilityShim(
        replacement_import="agentsassemble.legacy.live_agent.http.official_reply",
        removal_gate=(
            "No direct imports use "
            "agentsassemble.gui_legacy_live_agent_official_reply_http for one "
            "compatibility window."
        ),
        known_callers=(
            "tests/test_gui_legacy_live_agent_official_reply_http.py",
        ),
        introduced_in="Milestone 6.48 legacy resident official-reply HTTP move",
    ),
    "gui_legacy_live_agent_probe_http.py": CompatibilityShim(
        replacement_import="agentsassemble.legacy.live_agent.http.probe",
        removal_gate=(
            "No direct imports use agentsassemble.gui_legacy_live_agent_probe_http "
            "for one compatibility window."
        ),
        known_callers=("tests/test_gui_legacy_live_agent_probe_http.py",),
        introduced_in="Milestone 6.49 legacy resident probe HTTP move",
    ),
    "gui_legacy_live_agent_self_managed_http.py": CompatibilityShim(
        replacement_import="agentsassemble.legacy.live_agent.http.self_managed",
        removal_gate=(
            "No direct imports use "
            "agentsassemble.gui_legacy_live_agent_self_managed_http for one "
            "compatibility window."
        ),
        known_callers=(
            "tests/test_gui_legacy_live_agent_self_managed_http.py",
        ),
        introduced_in="Milestone 6.50 legacy self-managed resident HTTP move",
    ),
    "gui_legacy_live_agent_presence_http.py": CompatibilityShim(
        replacement_import="agentsassemble.legacy.live_agent.http.presence",
        removal_gate=(
            "No direct imports use "
            "agentsassemble.gui_legacy_live_agent_presence_http for one "
            "compatibility window."
        ),
        known_callers=("tests/test_gui_legacy_live_agent_presence_http.py",),
        introduced_in="Milestone 6.51 legacy resident presence HTTP move",
    ),
    "gui_legacy_live_agent_process_http.py": CompatibilityShim(
        replacement_import="agentsassemble.legacy.live_agent.http.process",
        removal_gate=(
            "No direct imports use "
            "agentsassemble.gui_legacy_live_agent_process_http for one "
            "compatibility window."
        ),
        known_callers=("tests/test_gui_legacy_live_agent_process_http.py",),
        introduced_in="Milestone 6.52 legacy resident process HTTP move",
    ),
    "gui_legacy_live_agent_discovery_http.py": CompatibilityShim(
        replacement_import="agentsassemble.legacy.live_agent.http.discovery",
        removal_gate=(
            "No direct imports use "
            "agentsassemble.gui_legacy_live_agent_discovery_http for one "
            "compatibility window."
        ),
        known_callers=("tests/test_gui_legacy_live_agent_discovery_http.py",),
        introduced_in="Milestone 6.53 legacy resident discovery HTTP move",
    ),
    "gui_legacy_live_agent_preflight_http.py": CompatibilityShim(
        replacement_import="agentsassemble.legacy.live_agent.http.preflight",
        removal_gate=(
            "No direct imports use "
            "agentsassemble.gui_legacy_live_agent_preflight_http for one "
            "compatibility window."
        ),
        known_callers=("tests/test_gui_legacy_live_agent_preflight_http.py",),
        introduced_in="Milestone 6.54 legacy resident preflight HTTP move",
    ),
    "gui_legacy_live_agent_readiness_http.py": CompatibilityShim(
        replacement_import="agentsassemble.legacy.live_agent.http.readiness",
        removal_gate=(
            "No direct imports use "
            "agentsassemble.gui_legacy_live_agent_readiness_http for one "
            "compatibility window."
        ),
        known_callers=("tests/test_gui_legacy_live_agent_readiness_http.py",),
        introduced_in="Milestone 6.55 legacy resident readiness HTTP move",
    ),
    "gui_legacy_live_agent_session_run_http.py": CompatibilityShim(
        replacement_import="agentsassemble.legacy.live_agent.http.session_run",
        removal_gate=(
            "No direct imports use "
            "agentsassemble.gui_legacy_live_agent_session_run_http for one "
            "compatibility window."
        ),
        known_callers=("tests/test_gui_legacy_live_agent_session_run_http.py",),
        introduced_in="Milestone 6.56 legacy resident session-run HTTP move",
    ),
    "gui_legacy_live_agent_session_http.py": CompatibilityShim(
        replacement_import="agentsassemble.legacy.live_agent.http.session",
        removal_gate=(
            "No direct imports use "
            "agentsassemble.gui_legacy_live_agent_session_http for one "
            "compatibility window."
        ),
        known_callers=("tests/test_gui_legacy_live_agent_session_http.py",),
        introduced_in="Milestone 6.57 legacy resident session HTTP move",
    ),
    "gui_legacy_live_agent_read_http.py": CompatibilityShim(
        replacement_import="agentsassemble.legacy.live_agent.http.read",
        removal_gate=(
            "No direct imports use "
            "agentsassemble.gui_legacy_live_agent_read_http for one "
            "compatibility window."
        ),
        known_callers=("tests/test_gui_legacy_live_agent_read_http.py",),
        introduced_in="Milestone 6.58 legacy resident read HTTP move",
    ),
    "gui_legacy_live_agent_smoke_http.py": CompatibilityShim(
        replacement_import="agentsassemble.legacy.live_agent.http.smoke",
        removal_gate=(
            "No direct imports use "
            "agentsassemble.gui_legacy_live_agent_smoke_http for one "
            "compatibility window."
        ),
        known_callers=("tests/test_gui_legacy_live_agent_smoke_http.py",),
        introduced_in="Milestone 6.59 legacy resident smoke HTTP move",
    ),
    "legacy_live_agent_presence.py": CompatibilityShim(
        replacement_import="agentsassemble.legacy.live_agent.presence",
        removal_gate=(
            "No direct imports use agentsassemble.legacy_live_agent_presence "
            "for one compatibility window."
        ),
        known_callers=("tests/test_legacy_package.py",),
        introduced_in="Milestone 6.27 legacy resident presence family move",
    ),
    "legacy_live_agent_presence_projection.py": CompatibilityShim(
        replacement_import="agentsassemble.legacy.live_agent.presence_projection",
        removal_gate=(
            "No direct imports use "
            "agentsassemble.legacy_live_agent_presence_projection for one "
            "compatibility window."
        ),
        known_callers=("tests/test_legacy_package.py",),
        introduced_in="Milestone 6.27 legacy resident presence family move",
    ),
    "legacy_live_agent_process_control.py": CompatibilityShim(
        replacement_import="agentsassemble.legacy.live_agent.process_control",
        removal_gate=(
            "No direct imports use "
            "agentsassemble.legacy_live_agent_process_control for one "
            "compatibility window."
        ),
        known_callers=("tests/test_legacy_package.py",),
        introduced_in="Milestone 6.28 legacy resident process mutation move",
    ),
    "legacy_live_agent_process_service.py": CompatibilityShim(
        replacement_import="agentsassemble.legacy.live_agent.process_service",
        removal_gate=(
            "No direct imports use "
            "agentsassemble.legacy_live_agent_process_service for one "
            "compatibility window."
        ),
        known_callers=("tests/test_legacy_package.py",),
        introduced_in="Milestone 6.28 legacy resident process mutation move",
    ),
    "legacy_live_agent_process_projection.py": CompatibilityShim(
        replacement_import="agentsassemble.legacy.live_agent.process_projection",
        removal_gate=(
            "No direct imports use "
            "agentsassemble.legacy_live_agent_process_projection for one "
            "compatibility window."
        ),
        known_callers=("tests/test_legacy_package.py",),
        introduced_in="Milestone 6.31 legacy resident process projection move",
    ),
    "legacy_live_agent_roster_queries.py": CompatibilityShim(
        replacement_import="agentsassemble.legacy.live_agent.roster_queries",
        removal_gate=(
            "No direct imports use "
            "agentsassemble.legacy_live_agent_roster_queries for one "
            "compatibility window."
        ),
        known_callers=("tests/test_legacy_package.py",),
        introduced_in="Milestone 6.36 legacy resident roster query move",
    ),
    "legacy_live_agent_readiness.py": CompatibilityShim(
        replacement_import="agentsassemble.legacy.live_agent.readiness",
        removal_gate=(
            "No direct imports use agentsassemble.legacy_live_agent_readiness "
            "for one compatibility window."
        ),
        known_callers=("tests/test_legacy_package.py",),
        introduced_in="Milestone 6.38 legacy resident readiness family move",
    ),
    "legacy_live_agent_readiness_projection.py": CompatibilityShim(
        replacement_import="agentsassemble.legacy.live_agent.readiness_projection",
        removal_gate=(
            "No direct imports use "
            "agentsassemble.legacy_live_agent_readiness_projection for one "
            "compatibility window."
        ),
        known_callers=("tests/test_legacy_package.py",),
        introduced_in="Milestone 6.38 legacy resident readiness family move",
    ),
    "legacy_live_agent_session_control.py": CompatibilityShim(
        replacement_import="agentsassemble.legacy.live_agent.session_control",
        removal_gate=(
            "No direct imports use "
            "agentsassemble.legacy_live_agent_session_control for one "
            "compatibility window."
        ),
        known_callers=("tests/test_legacy_package.py",),
        introduced_in="Milestone 6.29 legacy resident session policy move",
    ),
    "legacy_live_agent_session_projection.py": CompatibilityShim(
        replacement_import="agentsassemble.legacy.live_agent.session_projection",
        removal_gate=(
            "No direct imports use "
            "agentsassemble.legacy_live_agent_session_projection for one "
            "compatibility window."
        ),
        known_callers=("tests/test_legacy_package.py",),
        introduced_in="Milestone 6.29 legacy resident session policy move",
    ),
    "legacy_live_agent_session_service.py": CompatibilityShim(
        replacement_import="agentsassemble.legacy.live_agent.session_service",
        removal_gate=(
            "No direct imports use "
            "agentsassemble.legacy_live_agent_session_service for one "
            "compatibility window."
        ),
        known_callers=("tests/test_legacy_package.py",),
        introduced_in="Milestone 6.30 legacy resident session service move",
    ),
    "legacy_live_agent_session_run_service.py": CompatibilityShim(
        replacement_import="agentsassemble.legacy.live_agent.session_run_service",
        removal_gate=(
            "No direct imports use "
            "agentsassemble.legacy_live_agent_session_run_service for one "
            "compatibility window."
        ),
        known_callers=("tests/test_legacy_package.py",),
        introduced_in="Milestone 6.30 legacy resident session service move",
    ),
    "legacy_live_agent_session_run_health.py": CompatibilityShim(
        replacement_import="agentsassemble.legacy.live_agent.session_run_health",
        removal_gate=(
            "No direct imports use "
            "agentsassemble.legacy_live_agent_session_run_health for one "
            "compatibility window."
        ),
        known_callers=("tests/test_legacy_package.py",),
        introduced_in="Milestone 6.35 legacy resident session-run health move",
    ),
    "room_friends.py": CompatibilityShim(
        replacement_import="agentsassemble.features.social.friends",
        removal_gate=(
            "No direct imports use agentsassemble.room_friends for one "
            "compatibility window."
        ),
        known_callers=("tests/test_feature_routes_package.py",),
        introduced_in="Milestone 6.14 optional room-friends service move",
    ),
    "room_friend_dms.py": CompatibilityShim(
        replacement_import="agentsassemble.features.social.direct_messages",
        removal_gate=(
            "No direct imports use agentsassemble.room_friend_dms for one "
            "compatibility window."
        ),
        known_callers=("tests/test_feature_routes_package.py",),
        introduced_in="Milestone 6.15 optional friend-DM service move",
    ),
    "user_profile.py": CompatibilityShim(
        replacement_import="agentsassemble.features.social.profile",
        removal_gate=(
            "No direct imports use agentsassemble.user_profile for one "
            "compatibility window."
        ),
        known_callers=("tests/test_feature_routes_package.py",),
        introduced_in="Milestone 6.16 optional user-profile service move",
    ),
    "gui_social_http.py": CompatibilityShim(
        replacement_import="agentsassemble.features.social.routes",
        removal_gate=(
            "No direct imports use agentsassemble.gui_social_http for one "
            "compatibility window."
        ),
        known_callers=("tests/test_feature_routes_package.py",),
        introduced_in="Milestone 4.8 optional feature route packages",
    ),
    "live_cli_output.py": CompatibilityShim(
        replacement_import="agentsassemble.providers.live_cli_output",
        removal_gate=(
            "No direct imports use agentsassemble.live_cli_output for one "
            "compatibility window."
        ),
        known_callers=("tests/test_provider_package.py",),
        introduced_in="Milestone 5.11 live CLI output move",
    ),
    "live_cli_transcripts.py": CompatibilityShim(
        replacement_import="agentsassemble.providers.live_cli_transcripts",
        removal_gate=(
            "No direct imports use agentsassemble.live_cli_transcripts for one "
            "compatibility window."
        ),
        known_callers=("tests/test_provider_package.py",),
        introduced_in="Milestone 5.12 live CLI transcript adapter move",
    ),
    "room_admission.py": CompatibilityShim(
        replacement_import="agentsassemble.admission.preflight",
        removal_gate=(
            "No direct imports or monkeypatch targets use "
            "agentsassemble.room_admission for one compatibility window."
        ),
        known_callers=("tests/test_admission_package.py",),
        introduced_in="Milestone 3.1 admission package bootstrap",
    ),
    "room_admission_coordinator.py": CompatibilityShim(
        replacement_import="agentsassemble.admission.coordinator",
        removal_gate=(
            "No direct imports use agentsassemble.room_admission_coordinator for one "
            "compatibility window."
        ),
        known_callers=("tests/test_admission_coordinator_package.py",),
        introduced_in="Milestone 3.5 admission coordinator move",
    ),
    "room_admission_saga.py": CompatibilityShim(
        replacement_import="agentsassemble.admission.saga",
        removal_gate=(
            "No direct imports use agentsassemble.room_admission_saga for one "
            "compatibility window."
        ),
        known_callers=("tests/test_admission_coordinator_package.py",),
        introduced_in="Milestone 3.5 admission coordinator move",
    ),
    "room_admission_workflow_maintenance.py": CompatibilityShim(
        replacement_import="agentsassemble.admission.maintenance",
        removal_gate=(
            "No direct imports use "
            "agentsassemble.room_admission_workflow_maintenance for one "
            "compatibility window."
        ),
        known_callers=("tests/test_admission_maintenance_package.py",),
        introduced_in="Milestone 3.6 admission maintenance move",
    ),
    "room_admission_workflow_maintenance_command.py": CompatibilityShim(
        replacement_import="agentsassemble.admission.maintenance_command",
        removal_gate=(
            "No direct imports use "
            "agentsassemble.room_admission_workflow_maintenance_command for one "
            "compatibility window."
        ),
        known_callers=("tests/test_admission_maintenance_package.py",),
        introduced_in="Milestone 3.6 admission maintenance move",
    ),
    "room_agent_lifecycle.py": CompatibilityShim(
        replacement_import="agentsassemble.room.agent_lifecycle",
        removal_gate=(
            "No direct imports use agentsassemble.room_agent_lifecycle for one "
            "compatibility window."
        ),
        known_callers=("tests/test_room_package.py",),
        introduced_in="Milestone 6.7 room Agent Session lifecycle move",
    ),
    "room_realtime.py": CompatibilityShim(
        replacement_import="agentsassemble.room.realtime",
        removal_gate=(
            "No direct imports use agentsassemble.room_realtime for one "
            "compatibility window."
        ),
        known_callers=("tests/test_room_package.py",),
        introduced_in="Milestone 6.17 room realtime controller move",
    ),
    "room_database.py": CompatibilityShim(
        replacement_import="agentsassemble.persistence.local.room.database",
        removal_gate=(
            "No direct imports or monkeypatch targets use "
            "agentsassemble.room_database for one compatibility window."
        ),
        known_callers=("tests/test_local_room_persistence_package.py",),
        introduced_in="Milestone 2.5 local SQLite room persistence move",
    ),
    "room_invite_repository.py": CompatibilityShim(
        replacement_import=(
            "agentsassemble.persistence.local.admission.repository"
        ),
        removal_gate=(
            "No direct imports or monkeypatch targets use "
            "agentsassemble.room_invite_repository for one compatibility window."
        ),
        known_callers=(
            "tests/test_admission_repository_contracts.py",
            "tests/test_local_admission_persistence_package.py",
        ),
        introduced_in="Milestone 3.2 local admission persistence move",
    ),
    "room_invite_application.py": CompatibilityShim(
        replacement_import="agentsassemble.admission.invite_service",
        removal_gate=(
            "No direct imports use agentsassemble.room_invite_application for one "
            "compatibility window."
        ),
        known_callers=("tests/test_admission_invite_service_package.py",),
        introduced_in="Milestone 3.4 admission invite service move",
    ),
    "room_session_issuer.py": CompatibilityShim(
        replacement_import="agentsassemble.admission.session_issuer",
        removal_gate=(
            "No direct imports use agentsassemble.room_session_issuer for one "
            "compatibility window."
        ),
        known_callers=("tests/test_admission_session_package.py",),
        introduced_in="Milestone 3.3 admission session service move",
    ),
    "room_session_service.py": CompatibilityShim(
        replacement_import="agentsassemble.admission.session_service",
        removal_gate=(
            "No direct imports use agentsassemble.room_session_service for one "
            "compatibility window."
        ),
        known_callers=("tests/test_admission_session_package.py",),
        introduced_in="Milestone 3.3 admission session service move",
    ),
    "room_setting_values.py": CompatibilityShim(
        replacement_import="agentsassemble.room.setting_values",
        removal_gate=(
            "No direct imports use agentsassemble.room_setting_values for one "
            "compatibility window."
        ),
        known_callers=("tests/test_room_package.py",),
        introduced_in="Milestone 6.18 room setting values move",
    ),
    "room_settings_service.py": CompatibilityShim(
        replacement_import="agentsassemble.room.settings_service",
        removal_gate=(
            "No direct imports use agentsassemble.room_settings_service for one "
            "compatibility window."
        ),
        known_callers=("tests/test_room_package.py",),
        introduced_in="Milestone 6.21 room settings service move",
    ),
    "room_speech.py": CompatibilityShim(
        replacement_import="agentsassemble.room.speech",
        removal_gate=(
            "No direct imports use agentsassemble.room_speech for one "
            "compatibility window."
        ),
        known_callers=("tests/test_room_package.py",),
        introduced_in="Milestone 6.22 governed room speech move",
    ),
    "room_store.py": CompatibilityShim(
        replacement_import="agentsassemble.persistence.local.room.repository",
        removal_gate=(
            "No direct imports or monkeypatch targets use agentsassemble.room_store "
            "for one compatibility window."
        ),
        known_callers=("tests/test_local_room_persistence_package.py",),
        introduced_in="Milestone 2.5 local SQLite room persistence move",
    ),
    "sqlite_attention_repository.py": CompatibilityShim(
        replacement_import="agentsassemble.persistence.local.room.attention",
        removal_gate=(
            "No direct imports use agentsassemble.sqlite_attention_repository for "
            "one compatibility window."
        ),
        known_callers=("tests/test_local_room_persistence_package.py",),
        introduced_in="Milestone 2.5 local SQLite room persistence move",
    ),
    "postgres_attention_repository.py": CompatibilityShim(
        replacement_import="agentsassemble.persistence.postgres.room.attention",
        removal_gate=(
            "No direct imports use agentsassemble.postgres_attention_repository "
            "for one compatibility window."
        ),
        known_callers=("tests/test_postgres_room_persistence_package.py",),
        introduced_in="Milestone 2.2 PostgreSQL room persistence move",
    ),
    "postgres_application_database.py": CompatibilityShim(
        replacement_import=(
            "agentsassemble.persistence.postgres.application_database"
        ),
        removal_gate=(
            "No direct imports or monkeypatch targets use "
            "agentsassemble.postgres_application_database for one compatibility "
            "window."
        ),
        known_callers=(
            "tests/test_postgres_application_database.py",
            "tests/test_postgres_cross_authority_transactions.py",
        ),
        introduced_in="Milestone 2.1 PostgreSQL application database move",
    ),
    "postgres_connection_pool.py": CompatibilityShim(
        replacement_import="agentsassemble.persistence.postgres.connection_pool",
        removal_gate=(
            "No direct imports or monkeypatch targets use "
            "agentsassemble.postgres_connection_pool for one compatibility window."
        ),
        known_callers=(
            "tests/test_postgres_application_database.py",
            "tests/test_postgres_connection_pool.py",
        ),
        introduced_in="Milestone 2.1 PostgreSQL connection pool move",
    ),
    "postgres_identity_preferences.py": CompatibilityShim(
        replacement_import=(
            "agentsassemble.persistence.postgres.identity.preferences"
        ),
        removal_gate=(
            "No direct imports use agentsassemble.postgres_identity_preferences "
            "for one compatibility window."
        ),
        known_callers=("tests/test_postgres_identity_persistence_package.py",),
        introduced_in="Milestone 2.3 PostgreSQL identity persistence move",
    ),
    "postgres_identity_repository.py": CompatibilityShim(
        replacement_import="agentsassemble.persistence.postgres.identity.repository",
        removal_gate=(
            "No direct imports use agentsassemble.postgres_identity_repository "
            "for one compatibility window."
        ),
        known_callers=(
            "tests/test_postgres_cross_authority_transactions.py",
            "tests/test_postgres_identity_persistence_package.py",
            "tests/test_postgres_identity_repository.py",
        ),
        introduced_in="Milestone 2.3 PostgreSQL identity persistence move",
    ),
    "postgres_identity_roster.py": CompatibilityShim(
        replacement_import="agentsassemble.persistence.postgres.identity.roster",
        removal_gate=(
            "No direct imports use agentsassemble.postgres_identity_roster for one "
            "compatibility window."
        ),
        known_callers=("tests/test_postgres_identity_persistence_package.py",),
        introduced_in="Milestone 2.3 PostgreSQL identity persistence move",
    ),
    "postgres_identity_usage.py": CompatibilityShim(
        replacement_import="agentsassemble.persistence.postgres.identity.usage",
        removal_gate=(
            "No direct imports use agentsassemble.postgres_identity_usage for one "
            "compatibility window."
        ),
        known_callers=("tests/test_postgres_identity_persistence_package.py",),
        introduced_in="Milestone 2.3 PostgreSQL identity persistence move",
    ),
    "postgres_identity_users.py": CompatibilityShim(
        replacement_import="agentsassemble.persistence.postgres.identity.users",
        removal_gate=(
            "No direct imports use agentsassemble.postgres_identity_users for one "
            "compatibility window."
        ),
        known_callers=("tests/test_postgres_identity_persistence_package.py",),
        introduced_in="Milestone 2.3 PostgreSQL identity persistence move",
    ),
    "postgres_invite_repository.py": CompatibilityShim(
        replacement_import=(
            "agentsassemble.persistence.postgres.admission.repository"
        ),
        removal_gate=(
            "No direct imports use agentsassemble.postgres_invite_repository "
            "for one compatibility window."
        ),
        known_callers=(
            "tests/test_postgres_admission_persistence_package.py",
            "tests/test_postgres_cross_authority_transactions.py",
            "tests/test_postgres_invite_repository.py",
        ),
        introduced_in="Milestone 2.4 PostgreSQL admission persistence move",
    ),
    "process_environment.py": CompatibilityShim(
        replacement_import="agentsassemble.providers.process_environment",
        removal_gate=(
            "No direct imports use agentsassemble.process_environment for one "
            "compatibility window."
        ),
        known_callers=("tests/test_provider_package.py",),
        introduced_in="Milestone 5.6 provider security utilities move",
    ),
    "provider_catalog.py": CompatibilityShim(
        replacement_import="agentsassemble.providers.catalog",
        removal_gate=(
            "No direct imports use agentsassemble.provider_catalog for one "
            "compatibility window."
        ),
        known_callers=("tests/test_provider_package.py",),
        introduced_in="Milestone 5.2 provider catalog move",
    ),
    "provider_auth.py": CompatibilityShim(
        replacement_import="agentsassemble.providers.auth",
        removal_gate=(
            "No direct imports use agentsassemble.provider_auth for one "
            "compatibility window."
        ),
        known_callers=("tests/test_provider_package.py",),
        introduced_in="Milestone 5.16 provider authentication classifier move",
    ),
    "provider_model_verification.py": CompatibilityShim(
        replacement_import="agentsassemble.providers.model_verification",
        removal_gate=(
            "No direct imports use agentsassemble.provider_model_verification "
            "for one compatibility window."
        ),
        known_callers=("tests/test_provider_package.py",),
        introduced_in="Milestone 5.15 provider model verification move",
    ),
    "provider_runtime_config.py": CompatibilityShim(
        replacement_import="agentsassemble.providers.runtime_config",
        removal_gate=(
            "No direct imports use agentsassemble.provider_runtime_config "
            "for one compatibility window."
        ),
        known_callers=("tests/test_provider_package.py",),
        introduced_in="Milestone 5.3 provider runtime config move",
    ),
    "provider_runtime_contracts.py": CompatibilityShim(
        replacement_import="agentsassemble.providers.runtime_contracts",
        removal_gate=(
            "No direct imports use agentsassemble.provider_runtime_contracts "
            "for one compatibility window."
        ),
        known_callers=("tests/test_provider_package.py",),
        introduced_in="Milestone 5.1 provider package bootstrap",
    ),
    "provider_runtime_factory.py": CompatibilityShim(
        replacement_import="agentsassemble.providers.runtime_factory",
        removal_gate=(
            "No direct imports use agentsassemble.provider_runtime_factory "
            "for one compatibility window."
        ),
        known_callers=("tests/test_provider_package.py",),
        introduced_in="Milestone 5.4 provider runtime factory move",
    ),
    "provider_secrets.py": CompatibilityShim(
        replacement_import="agentsassemble.providers.secrets",
        removal_gate=(
            "No direct imports use agentsassemble.provider_secrets for one "
            "compatibility window."
        ),
        known_callers=("tests/test_provider_package.py",),
        introduced_in="Milestone 5.6 provider security utilities move",
    ),
    "provider_sessions.py": CompatibilityShim(
        replacement_import="agentsassemble.providers.sessions",
        removal_gate=(
            "No direct imports use agentsassemble.provider_sessions for one "
            "compatibility window."
        ),
        known_callers=("tests/test_provider_package.py",),
        introduced_in="Milestone 5.14 provider session discovery move",
    ),
    "room_api_provider.py": CompatibilityShim(
        replacement_import="agentsassemble.providers.api",
        removal_gate=(
            "No direct imports use agentsassemble.room_api_provider for one "
            "compatibility window."
        ),
        known_callers=("tests/test_provider_package.py",),
        introduced_in="Milestone 5.20 OpenAI-compatible API adapter move",
    ),
    "windows_conpty.py": CompatibilityShim(
        replacement_import="agentsassemble.providers.windows_conpty",
        removal_gate=(
            "No direct imports use agentsassemble.windows_conpty for one "
            "compatibility window."
        ),
        known_callers=("tests/test_provider_package.py",),
        introduced_in="Milestone 5.8 Windows ConPTY runtime move",
    ),
    "operator_pairing.py": CompatibilityShim(
        replacement_import="agentsassemble.identity.pairing",
        removal_gate=(
            "No direct imports use agentsassemble.operator_pairing for one "
            "compatibility window."
        ),
        known_callers=("tests/test_identity_pairing_package.py",),
        introduced_in="Milestone 3.7 identity pairing package bootstrap",
    ),
    "opencode_runtime.py": CompatibilityShim(
        replacement_import="agentsassemble.providers.opencode",
        removal_gate=(
            "No direct imports use agentsassemble.opencode_runtime for one "
            "compatibility window."
        ),
        known_callers=("tests/test_provider_package.py",),
        introduced_in="Milestone 5.9 OpenCode runtime move",
    ),
    "identity_store.py": CompatibilityShim(
        replacement_import="agentsassemble.persistence.local.identity.repository",
        removal_gate=(
            "Callers use agentsassemble.identity.repository for contracts and "
            "agentsassemble.persistence.local.identity registry, migration, or "
            "repository modules for one compatibility window."
        ),
        known_callers=(
            "tests/test_identity_repository_package.py",
            "tests/test_local_identity_persistence_package.py",
        ),
        introduced_in="Milestone 3.9 local identity persistence move",
    ),
    "identity_room_preferences.py": CompatibilityShim(
        replacement_import=(
            "agentsassemble.persistence.local.identity.preferences"
        ),
        removal_gate=(
            "Callers use agentsassemble.identity.preferences for shared identity "
            "rules and agentsassemble.persistence.local.identity.preferences for "
            "SQLite persistence for one compatibility window."
        ),
        known_callers=("tests/test_local_identity_persistence_package.py",),
        introduced_in="Milestone 3.10 identity room preference split",
    ),
    "identity_repository_factory.py": CompatibilityShim(
        replacement_import="agentsassemble.identity.factory",
        removal_gate=(
            "No direct imports use agentsassemble.identity_repository_factory for "
            "one compatibility window."
        ),
        known_callers=("tests/test_identity_repository_factory.py",),
        introduced_in="Milestone 3.11 identity repository factory move",
    ),
    "gui_request_security.py": CompatibilityShim(
        replacement_import="agentsassemble.web.security",
        removal_gate=(
            "No direct imports use agentsassemble.gui_request_security for one "
            "compatibility window."
        ),
        known_callers=("tests/test_web_transport_package.py",),
        introduced_in="Milestone 4.1 web security package bootstrap",
    ),
    "gui_application.py": CompatibilityShim(
        replacement_import="agentsassemble.application.gui",
        removal_gate=(
            "No direct imports use agentsassemble.gui_application for one "
            "compatibility window."
        ),
        known_callers=("tests/test_application_package.py",),
        introduced_in="Milestone 4.5 application package bootstrap",
    ),
    "gui_attachment_http.py": CompatibilityShim(
        replacement_import="agentsassemble.web.routes.attachments",
        removal_gate=(
            "No direct imports use agentsassemble.gui_attachment_http for one "
            "compatibility window."
        ),
        known_callers=("tests/test_web_routes_package.py",),
        introduced_in="Milestone 4.6 current web route package",
    ),
    "gui_provider_http.py": CompatibilityShim(
        replacement_import="agentsassemble.web.routes.providers",
        removal_gate=(
            "No direct imports or monkeypatch targets use "
            "agentsassemble.gui_provider_http for one compatibility window."
        ),
        known_callers=("tests/test_web_routes_package.py",),
        introduced_in="Milestone 4.6 current web route package",
    ),
    "gui_public_invite_http.py": CompatibilityShim(
        replacement_import="agentsassemble.web.routes.public_invite",
        removal_gate=(
            "No direct imports use agentsassemble.gui_public_invite_http for one "
            "compatibility window."
        ),
        known_callers=("tests/test_web_routes_package.py",),
        introduced_in="Milestone 4.6 current web route package",
    ),
    "gui_response.py": CompatibilityShim(
        replacement_import="agentsassemble.web.response",
        removal_gate=(
            "No direct imports use agentsassemble.gui_response for one "
            "compatibility window."
        ),
        known_callers=("tests/test_web_transport_package.py",),
        introduced_in="Milestone 4.2 web response transport move",
    ),
    "gui_static_transport.py": CompatibilityShim(
        replacement_import="agentsassemble.web.static",
        removal_gate=(
            "No direct imports use agentsassemble.gui_static_transport for one "
            "compatibility window."
        ),
        known_callers=("tests/test_web_transport_package.py",),
        introduced_in="Milestone 4.2 web static transport move",
    ),
    "gui_router.py": CompatibilityShim(
        replacement_import="agentsassemble.web.router",
        removal_gate=(
            "No direct imports use agentsassemble.gui_router for one "
            "compatibility window."
        ),
        known_callers=("tests/test_web_transport_package.py",),
        introduced_in="Milestone 4.3 web router move",
    ),
    "gui_room_agent_http.py": CompatibilityShim(
        replacement_import="agentsassemble.web.routes.agent_sessions",
        removal_gate=(
            "No direct imports use agentsassemble.gui_room_agent_http for one "
            "compatibility window."
        ),
        known_callers=("tests/test_web_routes_package.py",),
        introduced_in="Milestone 4.7 Agent Session route package",
    ),
    "gui_room_invite_http.py": CompatibilityShim(
        replacement_import="agentsassemble.web.routes.room_invite",
        removal_gate=(
            "No direct imports use agentsassemble.gui_room_invite_http for one "
            "compatibility window."
        ),
        known_callers=("tests/test_web_routes_package.py",),
        introduced_in="Milestone 4.6 current web route package",
    ),
    "gui_room_settings_http.py": CompatibilityShim(
        replacement_import="agentsassemble.web.routes.room_settings",
        removal_gate=(
            "No direct imports use agentsassemble.gui_room_settings_http for one "
            "compatibility window."
        ),
        known_callers=("tests/test_web_routes_package.py",),
        introduced_in="Milestone 4.6 current web route package",
    ),
    "gui_ws_http.py": CompatibilityShim(
        replacement_import="agentsassemble.web.websocket",
        removal_gate=(
            "No direct imports use agentsassemble.gui_ws_http for one "
            "compatibility window."
        ),
        known_callers=("tests/test_web_transport_package.py",),
        introduced_in="Milestone 4.4 WebSocket HTTP transport move",
    ),
    "gui_legacy_lobby_http.py": CompatibilityShim(
        replacement_import="agentsassemble.legacy.meeting.http.lobby",
        removal_gate=(
            "No direct imports use agentsassemble.gui_legacy_lobby_http for "
            "one compatibility window."
        ),
        known_callers=("tests/test_legacy_meeting_http_package.py",),
        introduced_in="Milestone 6.60 legacy lobby HTTP move",
    ),
    "gui_legacy_meeting_http.py": CompatibilityShim(
        replacement_import="agentsassemble.legacy.meeting.http.meeting",
        removal_gate=(
            "No direct imports use agentsassemble.gui_legacy_meeting_http for "
            "one compatibility window."
        ),
        known_callers=("tests/test_legacy_meeting_http_package.py",),
        introduced_in="Milestone 6.60 legacy meeting read HTTP move",
    ),
    "gui_legacy_meeting_lifecycle_http.py": CompatibilityShim(
        replacement_import="agentsassemble.legacy.meeting.http.lifecycle",
        removal_gate=(
            "No direct imports use "
            "agentsassemble.gui_legacy_meeting_lifecycle_http for one "
            "compatibility window."
        ),
        known_callers=("tests/test_gui_legacy_meeting_lifecycle_http.py",),
        introduced_in="Milestone 6.60 legacy meeting lifecycle HTTP move",
    ),
    "gui_legacy_official_round_http.py": CompatibilityShim(
        replacement_import="agentsassemble.legacy.meeting.http.official_round",
        removal_gate=(
            "No direct imports use agentsassemble.gui_legacy_official_round_http "
            "for one compatibility window."
        ),
        known_callers=("tests/test_gui_legacy_official_round_http.py",),
        introduced_in="Milestone 6.60 legacy official-round HTTP move",
    ),
    "gui_legacy_official_turn_http.py": CompatibilityShim(
        replacement_import="agentsassemble.legacy.meeting.http.official_turn",
        removal_gate=(
            "No direct imports use agentsassemble.gui_legacy_official_turn_http "
            "for one compatibility window."
        ),
        known_callers=("tests/test_gui_legacy_official_turn_http.py",),
        introduced_in="Milestone 6.60 legacy official-turn HTTP move",
    ),
    "gui_legacy_review_checkpoint_http.py": CompatibilityShim(
        replacement_import="agentsassemble.legacy.meeting.http.review_checkpoint",
        removal_gate=(
            "No direct imports use "
            "agentsassemble.gui_legacy_review_checkpoint_http for one "
            "compatibility window."
        ),
        known_callers=("tests/test_gui_legacy_review_checkpoint_http.py",),
        introduced_in="Milestone 6.60 legacy review-checkpoint HTTP move",
    ),
    "postgres_room_mutations.py": CompatibilityShim(
        replacement_import="agentsassemble.persistence.postgres.room.mutations",
        removal_gate=(
            "No direct imports use agentsassemble.postgres_room_mutations for one "
            "compatibility window."
        ),
        known_callers=("tests/test_postgres_room_persistence_package.py",),
        introduced_in="Milestone 2.2 PostgreSQL room persistence move",
    ),
    "postgres_room_queries.py": CompatibilityShim(
        replacement_import="agentsassemble.persistence.postgres.room.queries",
        removal_gate=(
            "No direct imports use agentsassemble.postgres_room_queries for one "
            "compatibility window."
        ),
        known_callers=("tests/test_postgres_room_persistence_package.py",),
        introduced_in="Milestone 2.2 PostgreSQL room persistence move",
    ),
    "postgres_room_repository.py": CompatibilityShim(
        replacement_import="agentsassemble.persistence.postgres.room.repository",
        removal_gate=(
            "No direct imports or monkeypatch targets use "
            "agentsassemble.postgres_room_repository for one compatibility window."
        ),
        known_callers=(
            "tests/test_postgres_cross_authority_transactions.py",
            "tests/test_postgres_room_persistence_package.py",
            "tests/test_postgres_room_repository.py",
            "tests/test_room_repository_migration.py",
        ),
        introduced_in="Milestone 2.2 PostgreSQL room persistence move",
    ),
    "postgres_room_rows.py": CompatibilityShim(
        replacement_import="agentsassemble.persistence.postgres.room.rows",
        removal_gate=(
            "No direct imports use agentsassemble.postgres_room_rows for one "
            "compatibility window."
        ),
        known_callers=("tests/test_postgres_room_persistence_package.py",),
        introduced_in="Milestone 2.2 PostgreSQL room persistence move",
    ),
    "postgres_room_schema.py": CompatibilityShim(
        replacement_import="agentsassemble.persistence.postgres.schema",
        removal_gate=(
            "No direct imports or monkeypatch targets use "
            "agentsassemble.postgres_room_schema for one compatibility window."
        ),
        known_callers=(
            "tests/test_postgres_application_database.py",
            "tests/test_postgres_cross_authority_transactions.py",
            "tests/test_postgres_identity_repository.py",
            "tests/test_postgres_invite_repository.py",
            "tests/test_postgres_room_persistence_package.py",
            "tests/test_postgres_room_repository.py",
            "tests/test_postgres_room_schema.py",
            "tests/test_room_repository_factory.py",
            "tests/test_room_repository_migration.py",
        ),
        introduced_in="Milestone 2.2 shared PostgreSQL schema move",
    ),
}


def current_top_level_modules(repository_root: Path) -> frozenset[str]:
    package_root = Path(repository_root) / "agentsassemble"
    return frozenset(path.name for path in package_root.glob("*.py"))


def load_root_baseline(repository_root: Path) -> frozenset[str]:
    path = Path(repository_root) / BASELINE_RELATIVE_PATH
    entries = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line and not line.startswith("#"):
            entries.append(line)
    if entries != sorted(set(entries)):
        raise ValueError("Package root baseline must be unique and sorted.")
    invalid = [entry for entry in entries if Path(entry).name != entry or not entry.endswith(".py")]
    if invalid:
        raise ValueError("Package root baseline contains invalid module paths.")
    return frozenset(entries)


def unexpected_top_level_modules(
    current: Iterable[str],
    baseline: Iterable[str],
) -> tuple[str, ...]:
    allowed = (
        frozenset(baseline)
        | PERMANENT_ROOT_ENTRYPOINTS
        | frozenset(ROOT_COMPATIBILITY_SHIMS)
    )
    return tuple(sorted(frozenset(current) - allowed))


def validate_compatibility_shims() -> None:
    for filename, shim in ROOT_COMPATIBILITY_SHIMS.items():
        if Path(filename).name != filename or not filename.endswith(".py"):
            raise ValueError(f"Invalid compatibility shim filename: {filename!r}")
        if not shim.replacement_import.startswith("agentsassemble."):
            raise ValueError(f"Compatibility shim {filename!r} needs a replacement import.")
        if not shim.removal_gate.strip():
            raise ValueError(f"Compatibility shim {filename!r} needs a removal gate.")
        if not shim.known_callers:
            raise ValueError(f"Compatibility shim {filename!r} needs known callers.")
        if any(not caller.strip() for caller in shim.known_callers):
            raise ValueError(
                f"Compatibility shim {filename!r} has an empty known caller."
            )
        if not shim.introduced_in.strip():
            raise ValueError(
                f"Compatibility shim {filename!r} needs introduction metadata."
            )


def dependency_direction_violations(graph: PackageGraph) -> tuple[str, ...]:
    violations = []
    for source_name in sorted(graph.modules):
        source_root = _migrated_package_root(graph.modules[source_name].relative_path)
        if not source_root:
            continue
        for imported_name in graph.imports_by_module[source_name]:
            imported_domain = graph.domains[imported_name]
            imported_classification = graph.classifications[imported_name]
            if (
                source_root in CURRENT_CORE_PACKAGE_ROOTS
                and imported_classification == "legacy"
            ):
                violations.append(
                    f"{source_name} imports legacy module {imported_name}"
                )
            if source_root in DOMAIN_PACKAGE_ROOTS and imported_domain == "web":
                violations.append(
                    f"{source_name} imports web module {imported_name}"
                )
            if source_root == "web" and _is_concrete_persistence_module(
                imported_name,
                graph.modules[imported_name].relative_path,
            ):
                violations.append(
                    f"{source_name} imports concrete persistence module {imported_name}"
                )
    return tuple(sorted(set(violations)))


def import_cycles(
    imports_by_module: Mapping[str, tuple[str, ...]],
) -> tuple[tuple[str, ...], ...]:
    index = 0
    stack: list[str] = []
    on_stack: set[str] = set()
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    cycles: list[tuple[str, ...]] = []

    def visit(module_name: str) -> None:
        nonlocal index
        indices[module_name] = index
        lowlinks[module_name] = index
        index += 1
        stack.append(module_name)
        on_stack.add(module_name)
        for imported_name in imports_by_module.get(module_name, ()):
            if imported_name not in indices:
                visit(imported_name)
                lowlinks[module_name] = min(
                    lowlinks[module_name],
                    lowlinks[imported_name],
                )
            elif imported_name in on_stack:
                lowlinks[module_name] = min(
                    lowlinks[module_name],
                    indices[imported_name],
                )
        if lowlinks[module_name] != indices[module_name]:
            return
        component = []
        while True:
            member = stack.pop()
            on_stack.remove(member)
            component.append(member)
            if member == module_name:
                break
        if len(component) > 1 or module_name in imports_by_module.get(module_name, ()):
            cycles.append(tuple(sorted(component)))

    for module_name in sorted(imports_by_module):
        if module_name not in indices:
            visit(module_name)
    return tuple(sorted(cycles, key=lambda cycle: (len(cycle), cycle)))


def load_cycle_baseline(repository_root: Path) -> frozenset[tuple[str, ...]]:
    path = Path(repository_root) / CYCLE_BASELINE_RELATIVE_PATH
    cycles = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        modules = tuple(part.strip() for part in line.split("|") if part.strip())
        if len(modules) < 2 or modules != tuple(sorted(set(modules))):
            raise ValueError("Package cycle baseline must contain sorted unique cycles.")
        cycles.append(modules)
    if cycles != sorted(set(cycles), key=lambda cycle: (len(cycle), cycle)):
        raise ValueError("Package cycle baseline must be unique and sorted.")
    return frozenset(cycles)


def new_import_cycles(
    current_cycles: Iterable[tuple[str, ...]],
    baseline_cycles: Iterable[tuple[str, ...]],
) -> tuple[tuple[str, ...], ...]:
    baseline = frozenset(baseline_cycles)
    return tuple(
        sorted(
            (cycle for cycle in current_cycles if cycle not in baseline),
            key=lambda cycle: (len(cycle), cycle),
        )
    )


def render_cycle_report(
    graph: PackageGraph,
    baseline_cycles: Iterable[tuple[str, ...]],
) -> str:
    cycles = import_cycles(graph.imports_by_module)
    baseline = frozenset(baseline_cycles)
    fingerprint = hashlib.sha256(
        "\n".join("|".join(cycle) for cycle in cycles).encode("utf-8")
    ).hexdigest()[:16]
    lines = [
        "# Package Import Cycles",
        "",
        "Status: generated architecture report",
        "",
        "Generator: `python3 scripts/check_package_architecture.py --write-cycle-report`",
        "",
        f"Source fingerprint: `{fingerprint}`",
        "",
        f"- Current import cycles: {len(cycles)}",
        f"- Grandfathered exact cycles: {sum(cycle in baseline for cycle in cycles)}",
        f"- New cycles: {sum(cycle not in baseline for cycle in cycles)}",
        "",
        "An exact historical cycle may disappear without updating the baseline. Any",
        "changed or newly introduced cycle fails the architecture gate. A cycle that",
        "moves into a target package is therefore not silently grandfathered.",
        "",
        "## Current Cycles",
        "",
    ]
    if not cycles:
        lines.append("- None")
    for cycle in cycles:
        status = "grandfathered" if cycle in baseline else "new"
        lines.append(f"- **{status}**: " + " -> ".join(f"`{name}`" for name in cycle))
    return "\n".join(lines) + "\n"


def initialize_root_baseline(repository_root: Path) -> Path:
    root = Path(repository_root)
    path = root / BASELINE_RELATIVE_PATH
    if path.exists():
        raise FileExistsError(f"Refusing to replace existing package baseline: {path}")
    entries = sorted(current_top_level_modules(root))
    content = "\n".join(
        (
            "# Historical AgentsAssemble package-root baseline captured 2026-07-16.",
            "# Do not add new files here. New root compatibility shims belong in",
            "# ROOT_COMPATIBILITY_SHIMS with a replacement import and removal gate.",
            *entries,
            "",
        )
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def initialize_cycle_baseline(repository_root: Path) -> Path:
    root = Path(repository_root)
    path = root / CYCLE_BASELINE_RELATIVE_PATH
    if path.exists():
        raise FileExistsError(f"Refusing to replace existing cycle baseline: {path}")
    cycles = import_cycles(load_package_graph(root).imports_by_module)
    content = "\n".join(
        (
            "# Exact import cycles grandfathered at the 2026-07-16 architecture gate.",
            "# Cycles may disappear, but changed or new cycles must not be added here.",
            *(" | ".join(cycle) for cycle in cycles),
            "",
        )
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def write_cycle_report(repository_root: Path) -> Path:
    root = Path(repository_root)
    path = root / CYCLE_REPORT_RELATIVE_PATH
    path.write_text(
        render_cycle_report(load_package_graph(root), load_cycle_baseline(root)),
        encoding="utf-8",
    )
    return path


def _migrated_package_root(relative_path: str) -> str:
    parts = Path(relative_path).parts
    if len(parts) < 3 or parts[0] != "agentsassemble":
        return ""
    root = parts[1]
    return root if root in CURRENT_CORE_PACKAGE_ROOTS else ""


def _is_concrete_persistence_module(module_name: str, relative_path: str) -> bool:
    stem = Path(relative_path).stem
    return (
        module_name.startswith("agentsassemble.persistence.postgres")
        or module_name.startswith("agentsassemble.persistence.local")
        or stem.startswith("postgres_")
        or stem.startswith("sqlite_")
        or stem in {"identity_store", "room_store"}
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--initialize-baseline", action="store_true")
    parser.add_argument("--initialize-cycle-baseline", action="store_true")
    parser.add_argument("--write-cycle-report", action="store_true")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    if args.initialize_baseline:
        path = initialize_root_baseline(root)
        print(f"Wrote {path.relative_to(root)}")
        return 0
    if args.initialize_cycle_baseline:
        path = initialize_cycle_baseline(root)
        print(f"Wrote {path.relative_to(root)}")
        return 0
    if args.write_cycle_report:
        path = write_cycle_report(root)
        print(f"Wrote {path.relative_to(root)}")
        return 0
    validate_compatibility_shims()
    unexpected = unexpected_top_level_modules(
        current_top_level_modules(root),
        load_root_baseline(root),
    )
    if unexpected:
        print("Unowned top-level product modules: " + ", ".join(unexpected))
        return 1
    graph = load_package_graph(root)
    violations = dependency_direction_violations(graph)
    if violations:
        print("Dependency direction violations:")
        for violation in violations:
            print(f"- {violation}")
        return 1
    cycles = import_cycles(graph.imports_by_module)
    new_cycles = new_import_cycles(cycles, load_cycle_baseline(root))
    if new_cycles:
        print("New import cycles:")
        for cycle in new_cycles:
            print("- " + " -> ".join(cycle))
        return 1
    expected_report = render_cycle_report(graph, load_cycle_baseline(root))
    report_path = root / CYCLE_REPORT_RELATIVE_PATH
    if not report_path.exists() or report_path.read_text(encoding="utf-8") != expected_report:
        print(f"Package cycle report is stale: {report_path.relative_to(root)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
