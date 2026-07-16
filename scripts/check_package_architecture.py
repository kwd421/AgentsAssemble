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
    {"application", "web", "room", "admission", "identity", "providers", "persistence"}
)
DOMAIN_PACKAGE_ROOTS = frozenset({"room", "admission", "identity", "providers"})


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
    "codex_stream.py": CompatibilityShim(
        replacement_import="agentsassemble.providers.codex_stream",
        removal_gate=(
            "No direct imports use agentsassemble.codex_stream for one "
            "compatibility window."
        ),
        known_callers=("tests/test_provider_package.py",),
        introduced_in="Milestone 5.17 Codex provider parser move",
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
    "live_cli.py": CompatibilityShim(
        replacement_import="agentsassemble.providers.live_cli",
        removal_gate=(
            "No direct imports use agentsassemble.live_cli for one "
            "compatibility window."
        ),
        known_callers=("tests/test_provider_package.py",),
        introduced_in="Milestone 5.13 POSIX live CLI runtime move",
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
