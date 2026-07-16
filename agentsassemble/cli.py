from __future__ import annotations

import argparse
import json
import math
import os
import re
import shlex
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from agentsassemble.bridges.claude_code_bridge import CLAUDE_PRINT_MODE_DISABLED_MESSAGE, serve_bridge
from agentsassemble.agent_sessions import (
    CODEX_APP_SERVER_SMOKE_COMMANDS,
    clean_agent_session_provider_kind,
    run_codex_app_server_smoke,
)
from agentsassemble.providers.cursor_resident import (
    CursorResidentCommandRunner,
    cursor_generic_resident_guard_error,
    cursor_terminal_session_superseded_error,
)
from agentsassemble.hermes_resident import HermesResidentCommandRunner
from agentsassemble.providers.antigravity_resident import AntigravityResidentCommandRunner
from agentsassemble.providers.codex_resident import CodexResidentCommandRunner
from agentsassemble.providers.grok_resident import GrokResidentCommandRunner
from agentsassemble.web.frontend_runtime import frontend_dist_status
from agentsassemble.kiro_resident import KiroResidentCommandRunner
from agentsassemble.codex_sessions import (
    DEFAULT_INVITE_CONFIG_PATH,
    DEFAULT_LIVE_AGENT_CONFIG_PATH,
    build_codex_live_agent_config,
    build_codex_live_invite_config,
    list_codex_sessions,
    read_agent_config,
    write_agent_config,
)
from agentsassemble.providers.claude_resident import claude_code_print_mode_resident_error
from agentsassemble.character_mode import clean_persona_card_id, normalize_character_mode
# Keep these imports public for callers that historically imported validators
# and choice lists from ``agentsassemble.cli``.
from agentsassemble.cli_parser_common import (
    LIVE_AGENT_CONNECTION_KIND_CHOICES,
    LIVE_AGENT_DELEGATE_CONNECTION_KIND_CHOICES,
    LIVE_AGENT_RESIDENT_CONNECTION_KIND_CHOICES,
    MAX_LIVE_AGENT_ROUND_BATCH,
    _add_session_auto_restart_args,
    _add_session_finalize_after_rounds_arg,
    _add_session_readiness_wait_args,
    _hide_subparser_from_help,
    parse_codex_timeout,
    parse_nonnegative_float,
    parse_nonnegative_int,
    parse_positive_int,
    parse_session_smoke_lobby_probe_count,
    parse_session_smoke_soak_cycle_count,
    parse_session_smoke_soak_interval_seconds,
)
from agentsassemble.cli_legacy_live_agent_sessions import (
    LegacySessionCliRuntime,
    SESSION_BOUND_PROBE_HTTP_WINDOWS,
    format_session_start,
    run_legacy_session_command,
    session_command_exit_code,
    session_request_timeout,
    session_start_payload,
    validate_session_auto_restart_args,
    wait_for_session_after_control,
)
from agentsassemble.cli_legacy_live_agent_processes import (
    LegacyProcessCliRuntime,
    run_legacy_process_command,
)
from agentsassemble.cli_legacy_live_agent_smoke import LegacySmokeCliRuntime, run_legacy_smoke_command
from agentsassemble.cli_legacy_live_agent_format import (
    _format_live_agent_probe,
    _format_live_agent_readiness,
    _format_live_agent_real_session_smoke,
    _format_live_agent_session_smoke,
    _format_provider_health,
    _print_live_agent_process_event_wait_result,
    _print_live_agent_process_events_payload,
    _print_live_agent_process_payload,
    _print_live_agent_process_wait_result,
)
from agentsassemble.cli_diagnostics import (
    DiagnosticCliRuntime,
    run_diagnostic_command,
    run_provider_health_command,
)
from agentsassemble.config import load_council_config
from agentsassemble.gui import serve_gui
from agentsassemble.live_agents import (
    PRESENCE_ATTENTION_REDACTED,
    SAFE_PRESENCE_ATTENTION_CODES,
    _looks_sensitive_presence_error,
)
from agentsassemble.live_agent_flow import FlowOptions, LiveAgentFlowClient
from agentsassemble.live_agent_flow_resources import FlowResourceRecorder
from agentsassemble.live_agent_continuity_proof import (
    run_live_agent_continuity_proof,
    run_live_agent_continuity_proof_batch,
)
from agentsassemble.live_cli_smoke import DEFAULT_LIVE_CLI_SMOKE_CONFIG
from agentsassemble.room_native_cli_smoke import run_room_native_cli_smoke
from agentsassemble.room_repository_factory import RoomRepositoryUnavailable
from agentsassemble.live_agent_preflight import preflight_live_agent_config, resident_config_setup_error
from agentsassemble.live_agent_processes import clean_live_agent_group_id
from agentsassemble.lobby_promotion import promote_lobby_events_to_official
from agentsassemble.live_agent_roster import (
    safe_live_agent_roster_agent,
    safe_live_agent_roster_number,
    safe_live_agent_roster_payload,
    safe_live_agent_roster_text,
)
from agentsassemble.meeting_events import clean_lobby_text
from agentsassemble.live_meeting_memory import compact_live_meeting_memory
from agentsassemble.live_agent_discovery import (
    add_session_bundle_outputs,
    apply_discovery_approval_filter,
    build_discovered_live_agent_config,
    build_discovered_session_bundle,
    discovery_has_exact_approval,
    discovered_session_bundle_paths,
    fill_discovery_next_command_output,
    validate_distinct_session_bundle_paths,
)
from agentsassemble.live_agent_join_brief import build_live_agent_join_brief
from agentsassemble.live_agent_runner import (
    LiveAgentRunner,
    RemoteBridgeResidentCommandRunner,
    ResidentAgentConfig,
    SUPPORTED_RESIDENT_CONNECTION_KINDS,
    config_from_args,
    load_group_configs,
    official_turn_request_candidate,
    reply_length_directive,
    resident_connection_kind_error,
    should_reply_to_event,
)
from agentsassemble.live_agent_timing import DEFAULT_LIVE_AGENT_POLL_INTERVAL, live_agent_poll_sleep_seconds
from agentsassemble.live_agent_smoke import (
    MAX_SESSION_SMOKE_LOBBY_PROBES,
    MAX_SESSION_SMOKE_SOAK_CYCLES,
    MAX_SESSION_SMOKE_SOAK_INTERVAL_SECONDS,
    LiveAgentSmokeFailed,
    run_live_agent_smoke,
)
from agentsassemble.live_agent_sessions import session_ensure_action
from agentsassemble.live_session_transport import JsonlLiveSession, TerminalLiveSession
from agentsassemble.meeting import run_demo_meeting
from agentsassemble.memory_capsules import memory_capsule_gate_report
from agentsassemble.models import ENGAGEMENT_MODE_CHOICES
from agentsassemble.cli_http_errors import CliHttpError
from agentsassemble.multi_host_invites import (
    create_lan_invite_packet,
    resolve_lan_invite_secret_ref,
    verify_lan_invite_token,
)
from agentsassemble.persona_cards import (
    PersonaImportReport,
    import_ccv3_persona,
    import_charx_persona,
    import_risum_persona,
    load_persona_card,
    persona_card_from_risu_module,
    read_risum_module,
    render_persona_prompt,
    scan_persona_lore,
)
from agentsassemble.provider_health import provider_health_report
from agentsassemble.release_health import (
    DEFAULT_RELEASE_HEALTH_TIMEOUT_SECONDS,
    ReleaseHealthSelectionError,
    release_health_catalog_payload,
    run_release_health_checks,
    write_latest_release_health_report,
)


MAX_READINESS_PROBE_AGENTS = 10
MAX_LIVE_AGENT_SEQUENCE_TURNS = 12
LEGACY_LIVE_AGENT_RUNNABLE_COMMANDS = {
    "flow",
    "start-session",
    "resume-session",
    "restart-session",
    "recover-session",
    "ensure-session",
    "run-group",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="assemble")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.metavar = "{demo,gui,frontend-info,lobby,release-health,providers,api-call,memory-capsule,persona,room}"

    from agentsassemble.cli_parser_core import register_core_parsers
    from agentsassemble.cli_parser_live_agent import register_live_agent_parsers
    from agentsassemble.cli_parser_persona import register_persona_parsers
    from agentsassemble.cli_parser_room import register_room_parsers
    from agentsassemble.cli_parser_sessions import register_sessions_parsers

    register_core_parsers(subparsers)
    register_live_agent_parsers(subparsers)
    register_persona_parsers(subparsers)
    register_room_parsers(subparsers)
    register_sessions_parsers(subparsers)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "demo":
        run_demo_meeting(
            adapter_name=args.adapter,
            output_root=Path(args.output_root),
            reporter=lambda message: print(message, flush=True),
            codex_timeout_seconds=args.codex_timeout,
            codex_search_enabled=not args.no_codex_search,
            research_depth=args.research_depth,
            research_steering=args.research_steering,
            council_config_path=args.council_config,
            agent_config_path=args.agent_config,
            meeting_mode="free_chat" if args.meeting_mode == "free-chat" else args.meeting_mode,
            moderator_enabled=None if args.moderator is None else args.moderator == "on",
            follow_up_of=args.follow_up_of,
            follow_up_from=args.follow_up_from,
            follow_up_note=args.follow_up_note,
        )
        return 0
    if args.command == "gui":
        try:
            serve_gui(
                host=args.host,
                port=args.port,
                output_root=Path(args.output_root),
                room_repository_backend=args.room_repository_backend,
                room_postgres_dsn_env=args.room_postgres_dsn_env,
                attention_shadow_mode=args.attention_shadow_mode,
                public_url=args.public_url,
                host_token=args.host_token,
                unsafe_expose_control_plane=args.unsafe_expose_control_plane,
                start_public_tunnel=args.start_public_tunnel,
                live_agent_config=Path(args.live_agent_config) if args.live_agent_config else None,
                live_agent_group_id=args.live_agent_group_id,
                live_agent_auto_restart=args.live_agent_auto_restart,
                live_agent_max_restarts=args.live_agent_max_restarts,
                live_agent_restart_backoff_seconds=args.live_agent_restart_backoff_seconds,
                live_agent_stale_restart_after_seconds=args.live_agent_stale_restart_after_seconds,
            )
        except (ValueError, RoomRepositoryUnavailable) as error:
            print(f"error: {error}", file=sys.stderr)
            if "non-loopback GUI bind" in str(error):
                print("hint: bind to 127.0.0.1 and use the authenticated public tunnel", file=sys.stderr)
            return 2
        return 0
    if args.command == "frontend-info":
        return run_frontend_info_command(args)
    if args.command == "lobby":
        return run_lobby_command(args)
    if args.command == "release-health":
        return run_release_health_command(args)
    if args.command == "claude-bridge":
        print(CLAUDE_PRINT_MODE_DISABLED_MESSAGE, file=sys.stderr)
        return 2
    if args.command == "live-agent":
        return run_live_agent_command(args)
    if args.command == "providers":
        return run_providers_command(args)
    if args.command == "api-call":
        return run_api_call_command(args)
    if args.command == "memory-capsule":
        return run_memory_capsule_command(args)
    if args.command == "persona":
        return run_persona_command(args)
    if args.command == "mcp":
        return run_mcp_command(args)
    if args.command == "sessions":
        return run_sessions_command(args)
    if args.command == "room":
        return run_room_command(args)

    return 1


def frontend_info_payload(
    *,
    backend: str = "http://127.0.0.1:8765",
    port: int = 5173,
    frontend_dist_root: Path | None = None,
) -> dict[str, object]:
    backend_url = str(backend or "http://127.0.0.1:8765").rstrip("/") or "http://127.0.0.1:8765"
    frontend_port = int(port)
    frontend_url = f"http://127.0.0.1:{frontend_port}"
    react_app_path = "/app/"
    react_app_url = backend_url + react_app_path
    parity_matrix_doc = "docs/product/legacy-react-parity-matrix.md"
    backend_parts = urllib.parse.urlparse(backend_url)
    backend_host = backend_parts.hostname or "127.0.0.1"
    backend_port = backend_parts.port or 8765
    dist_status = frontend_dist_status(frontend_dist_root)
    recommended_ui_kind = "react" if dist_status.static_available else "react_build_required"
    recommended_ui_url = backend_url + "/"
    recommended_ui_label = (
        "Discord-style room client"
        if dist_status.static_available
        else "Discord-style room client (build required)"
    )
    default_console_kind = "react" if dist_status.static_available else "react_build_required"
    default_console_label = (
        "Discord-style room client (default entry point)"
        if dist_status.static_available
        else "Discord-style room client (build required)"
    )
    return {
        "frontend_dir": "frontend",
        "frontend_url": frontend_url,
        "frontend_dev_port": frontend_port,
        "frontend_dev_proxy_target": backend_url,
        "backend_url": backend_url,
        "legacy_console_status": "retired",
        "default_console_kind": default_console_kind,
        "default_console_label": default_console_label,
        "react_app_path": react_app_path,
        "react_app_url": react_app_url,
        "react_app_kind": "react_default",
        "react_app_label": "Discord-style React room client (default at /, alias at /app/)",
        "recommended_ui_kind": recommended_ui_kind,
        "recommended_ui_url": recommended_ui_url,
        "recommended_ui_label": recommended_ui_label,
        "app_dist_path": "frontend/dist",
        "app_static_available": dist_status.static_available,
        "app_index_present": dist_status.index_present,
        "app_assets_dir_present": dist_status.assets_dir_present,
        "app_referenced_assets_present": dist_status.referenced_assets_present,
        "app_build_status": dist_status.build_status,
        "parity_matrix_doc": parity_matrix_doc,
        "is_default_entry_point": True,
        "launch_commands": [
            f"python3 -m agentsassemble.cli gui --host {backend_host} --port {backend_port} --output-root .agentsassemble",
            "npm --prefix frontend run build",
            "cd frontend && AGENTSASSEMBLE_API_TARGET=" + backend_url + " npm run dev",
        ],
        "notes": [
            "assemble gui serves the Discord-style React room client at / once npm --prefix frontend run build exists.",
            "Until that build exists, / and /app/ return a build-required response instead of serving the retired vanilla console.",
            "The /legacy/ namespace and legacy static routes are retired; rebuild the React client instead of using a fallback UI.",
            "The React/Vite frontend reads existing HTTP/SSE state and does not start provider CLIs.",
            "The Vite proxy should target the same backend URL shown here unless AGENTSASSEMBLE_API_TARGET overrides it.",
            f"Browser parity for the default React surface is operator-verified; see {parity_matrix_doc}.",
        ],
    }


def run_frontend_info_command(args: argparse.Namespace) -> int:
    payload = frontend_info_payload(backend=args.backend, port=args.port)
    if args.as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    print("AgentsAssemble frontend launch info")
    print(f"- {payload['default_console_label']}: {payload['recommended_ui_url']}")
    print(f"- {payload['react_app_label']}: {payload['react_app_url']}")
    print(f"- Recommended current UI: {payload['recommended_ui_url']} ({payload['recommended_ui_label']})")
    print(f"- React/Vite opt-in UI: {payload['frontend_url']}")
    print(f"- Vite API proxy target: {payload['frontend_dev_proxy_target']}")
    print(f"- Built React static available: {payload['app_static_available']} ({payload['app_dist_path']})")
    print(f"- React build status: {payload['app_build_status']}")
    print(f"- Parity matrix: {payload['parity_matrix_doc']}")
    print(f"- Default surface kind: {payload['default_console_kind']}")
    print("- Commands:")
    for command in payload["launch_commands"]:
        print(f"  {command}")
    print("- Note: React/Vite is the default room client at / once built.")
    return 0


def run_lobby_command(args: argparse.Namespace) -> int:
    if args.lobby_command == "promote":
        try:
            payload = promote_lobby_events_to_official(
                Path(args.output_root),
                args.meeting_id,
                list(args.lobby_event_ids),
                reason=args.reason,
            )
        except ValueError as error:
            print(str(error), file=sys.stderr)
            return 2
        if args.as_json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(
                "Promoted "
                f"{len(payload.get('promoted_event_ids') or [])} lobby event(s) into meeting {payload['meeting_id']}."
            )
        return 0
    return 1


def run_release_health_command(args: argparse.Namespace) -> int:
    if getattr(args, "release_health_command", None) in {None, "list"}:
        payload = release_health_catalog_payload()
        if getattr(args, "as_json", False):
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(_format_release_health_catalog(payload))
        return 0
    if args.release_health_command == "run":
        try:
            payload = run_release_health_checks(
                check_ids=getattr(args, "check", []),
                skip_ids=getattr(args, "skip", []),
                timeout_seconds=getattr(args, "timeout", DEFAULT_RELEASE_HEALTH_TIMEOUT_SECONDS),
            )
        except ReleaseHealthSelectionError as error:
            print(str(error), file=sys.stderr)
            return 2
        if getattr(args, "save_report", False):
            write_latest_release_health_report(
                payload,
                output_root=Path(getattr(args, "output_root", ".agentsassemble")),
            )
        if getattr(args, "as_json", False):
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(_format_release_health_run(payload))
        return 0 if payload.get("summary", {}).get("ok") is True else 1
    return 1


def _format_release_health_catalog(payload: dict[str, object]) -> str:
    checks = payload.get("checks") if isinstance(payload.get("checks"), list) else []
    lines = ["AgentsAssemble release-health checks"]
    for item in checks:
        if not isinstance(item, dict):
            continue
        check_id = str(item.get("id") or "")
        label = str(item.get("label") or check_id)
        category = str(item.get("category") or "check")
        kind = str(item.get("kind") or "")
        lines.append(f"- {check_id}: {label} [{category}/{kind}]")
    lines.append("Run: python3 -m agentsassemble.cli release-health run --json")
    return "\n".join(lines)


def _format_release_health_run(payload: dict[str, object]) -> str:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    results = payload.get("results") if isinstance(payload.get("results"), list) else []
    lines = [
        "AgentsAssemble release-health run",
        (
            f"- summary: passed {summary.get('passed', 0)}, failed {summary.get('failed', 0)}, "
            f"skipped {summary.get('skipped', 0)}, total {summary.get('total', 0)}"
        ),
    ]
    for item in results:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "unknown")
        check_id = str(item.get("id") or "check")
        duration = item.get("duration_seconds")
        suffix = f" ({duration}s)" if duration is not None else ""
        if status == "skipped" and item.get("skipped_reason"):
            suffix = f"{suffix} {item['skipped_reason']}"
        lines.append(f"- {status}: {check_id}{suffix}")
    return "\n".join(lines)


def run_persona_command(args: argparse.Namespace) -> int:
    rpack_map_path = Path(args.rpack_map) if getattr(args, "rpack_map", "") else None
    if args.persona_command == "inspect-risum":
        payload = read_risum_module(Path(args.file), rpack_map_path=rpack_map_path)
        card = persona_card_from_risu_module(payload.module, source_name=Path(args.file).name)
        report = PersonaImportReport(
            card=card,
            card_path=Path(""),
            asset_count=len(payload.asset_payloads),
            source_path=str(args.file),
        )
        if args.as_json:
            print(json.dumps(report.to_safe_dict(), ensure_ascii=False, indent=2))
        else:
            print(
                f"{card.display_name}: {len(card.lorebook)} lore entries, "
                f"{len(payload.asset_payloads)} assets, ignored {card.ignored_features}"
            )
        return 0
    if args.persona_command == "import-risum":
        report = import_risum_persona(
            Path(args.file),
            output_root=Path(args.output_root),
            rpack_map_path=rpack_map_path,
        )
        if args.as_json:
            print(json.dumps(report.to_safe_dict(), ensure_ascii=False, indent=2))
        else:
            print(f"Imported {report.card.display_name} persona card: {report.card_path}")
        return 0
    if args.persona_command == "import-ccv3":
        report = import_ccv3_persona(
            Path(args.file),
            output_root=Path(args.output_root),
        )
        if args.as_json:
            print(json.dumps(report.to_safe_dict(), ensure_ascii=False, indent=2))
        else:
            print(f"Imported {report.card.display_name} persona card: {report.card_path}")
        return 0
    if args.persona_command == "import-charx":
        report = import_charx_persona(
            Path(args.file),
            output_root=Path(args.output_root),
        )
        if args.as_json:
            print(json.dumps(report.to_safe_dict(), ensure_ascii=False, indent=2))
        else:
            print(f"Imported {report.card.display_name} persona card: {report.card_path}")
        return 0
    if args.persona_command == "scan":
        card = load_persona_card(Path(args.card))
        scan = scan_persona_lore(card, args.context)
        payload = {
            "persona": card.safe_summary(),
            "active_lore": [
                {
                    "key": entry.key,
                    "comment": entry.comment,
                    "content": entry.content,
                    "insert_order": entry.insert_order,
                }
                for entry in scan.entries
            ],
            "state": scan.state,
            "ignored_features": scan.ignored_features,
        }
        if args.as_json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"{card.display_name}: {len(scan.entries)} active lore entries")
            for entry in scan.entries:
                print(f"- {entry.comment or entry.key or 'lore'}")
        return 0
    if args.persona_command == "render":
        card = load_persona_card(Path(args.card))
        render = render_persona_prompt(
            card,
            recent_messages=args.context,
            user_name=args.user,
            persona=args.persona,
            variables=_parse_persona_slot_values(args.slot),
            mode=args.mode,
            surface=args.surface,
            first_message_index=int(args.first_message_index),
        )
        payload = {
            "persona": card.safe_summary(),
            "mode": render.mode,
            "surface": render.surface,
            "lines": render.lines,
            "active_lore_count": len(render.scan.entries),
            "state": render.scan.state,
        }
        if args.as_json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print("\n".join(render.lines))
        return 0
    return 1


def _parse_persona_slot_values(values: list[str]) -> dict[str, str]:
    slots: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"persona slot must be KEY=VALUE: {value}")
        key, slot_value = value.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError("persona slot key must not be empty")
        slots[key] = slot_value
    return slots


def run_providers_command(args: argparse.Namespace) -> int:
    try:
        if args.providers_command == "health":
            return run_provider_health_command(args, runtime=_diagnostic_cli_runtime())
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 1


def _diagnostic_cli_runtime() -> DiagnosticCliRuntime:
    return DiagnosticCliRuntime(
        request_json=lambda *call_args, **call_kwargs: _request_json(*call_args, **call_kwargs),
        server_url=_server_url,
        operation_http_timeout=_operation_http_timeout,
        session_smoke_http_timeout=_session_smoke_http_timeout,
        probe_http_timeout=_probe_http_timeout,
        provider_health_report=lambda *call_args, **call_kwargs: provider_health_report(*call_args, **call_kwargs),
        format_provider_health=_format_provider_health,
        format_readiness=_format_live_agent_readiness,
        format_probe=_format_live_agent_probe,
    )


def _legacy_session_cli_runtime() -> LegacySessionCliRuntime:
    return LegacySessionCliRuntime(
        request_json=lambda *call_args, **call_kwargs: _request_json(*call_args, **call_kwargs),
        server_url=_server_url,
        operation_http_timeout=_operation_http_timeout,
        monotonic=lambda: time.monotonic(),
        sleep=lambda seconds: time.sleep(seconds),
        is_wait_timeout=_is_live_agent_wait_timeout,
        session_ensure_action=session_ensure_action,
    )


def _is_live_agent_wait_timeout(error: BaseException) -> bool:
    if isinstance(error, TimeoutError):
        return True
    if isinstance(error, urllib.error.URLError):
        return isinstance(getattr(error, "reason", None), TimeoutError)
    return False


def run_api_call_command(args: argparse.Namespace) -> int:
    """API-provider lane: read a prompt on stdin, call an OpenAI-compatible model,
    print the reply on stdout, and record token usage. Designed to be a live-agent
    `command` so the runner's envelope/heartbeat/meta-filter wrap it unchanged."""
    from agentsassemble.providers import api as room_api_provider
    from agentsassemble.providers import catalog as provider_catalog
    from agentsassemble.persistence.local.identity.registry import (
        identity_store_for_output_root,
    )

    if getattr(args, "catalog", False):
        print(json.dumps(provider_catalog.catalog_payload(), ensure_ascii=False, indent=2))
        return 0

    prompt = sys.stdin.read()
    if not prompt.strip():
        print("error: empty prompt on stdin", file=sys.stderr)
        return 2

    store = None
    if args.output_root:
        try:
            store = identity_store_for_output_root(Path(args.output_root))
        except (OSError, ValueError):
            store = None  # usage accounting is best-effort; never block the reply

    try:
        text = room_api_provider.run_api_call(
            args.provider,
            args.model,
            prompt,
            store=store,
            user_id=args.user_id,
            participant_id=args.participant_id,
            meeting_id=args.meeting_id,
            system=args.system,
            key_source=args.key_source,
            timeout=args.timeout,
        )
    except room_api_provider.ApiProviderError as error:
        print(f"error[{error.category}]: {error}", file=sys.stderr)
        return 2
    print(text)
    return 0


def run_memory_capsule_command(args: argparse.Namespace) -> int:
    try:
        if args.memory_capsule_command == "gate":
            report = memory_capsule_gate_report(Path(args.path))
            if args.as_json:
                print(json.dumps(report, ensure_ascii=False, indent=2))
            else:
                print(f"Memory capsule gate: {report['status']}")
                for check in report.get("checks", []):
                    if isinstance(check, dict):
                        print(f"- {check.get('status', 'unknown')}: {check.get('message', '')}")
            return 0 if report.get("status") == "ok" else 1
    except OSError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 1


def run_mcp_command(args: argparse.Namespace) -> int:
    if not bool(getattr(args, "legacy_internal", False)):
        print("MCP is a legacy/internal room adapter; use Agent Session room commands instead.", file=sys.stderr)
        return 2
    try:
        if args.mcp_command == "serve":
            from agentsassemble.mcp_server import serve_mcp

            serve_mcp(
                profile=args.profile,
                server=args.server,
                agent_id=args.agent_id,
                meeting_id=args.meeting_id,
                display_name=args.display_name,
                provider_kind=args.provider_kind,
                connection_kind=args.connection_kind,
                engagement_mode=args.engagement_mode,
            )
            return 0
    except (RuntimeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 1


def run_live_agent_command(args: argparse.Namespace) -> int:
    if (
        str(getattr(args, "live_agent_command", "")) in LEGACY_LIVE_AGENT_RUNNABLE_COMMANDS
        and not bool(getattr(args, "legacy_internal", False))
        and os.environ.get("AGENTSASSEMBLE_LEGACY_INTERNAL") != "1"
    ):
        print("live-agent commands are legacy/internal; use Agent Session room commands instead.", file=sys.stderr)
        return 2
    try:
        session_result = run_legacy_session_command(
            args,
            runtime=_legacy_session_cli_runtime(),
        )
        if session_result is not None:
            return session_result
        process_result = run_legacy_process_command(
            args,
            runtime=LegacyProcessCliRuntime(
                request_json=lambda *call_args, **call_kwargs: _request_json(*call_args, **call_kwargs),
                server_url=_server_url,
                monotonic=lambda: time.monotonic(),
                sleep=lambda seconds: time.sleep(seconds),
                is_wait_timeout=_is_live_agent_wait_timeout,
                print_payload=_print_live_agent_process_payload,
                print_events=_print_live_agent_process_events_payload,
                print_wait_result=_print_live_agent_process_wait_result,
                print_event_wait_result=_print_live_agent_process_event_wait_result,
            ),
        )
        if process_result is not None:
            return process_result
        smoke_result = run_legacy_smoke_command(
            args,
            runtime=LegacySmokeCliRuntime(
                request_json=lambda *call_args, **call_kwargs: _request_json(*call_args, **call_kwargs),
                server_url=_server_url,
                operation_http_timeout=_operation_http_timeout,
                session_smoke_http_timeout=_session_smoke_http_timeout,
                real_session_smoke_http_timeout=_real_session_smoke_http_timeout,
                format_session_smoke=_format_live_agent_session_smoke,
                format_real_session_smoke=_format_live_agent_real_session_smoke,
            ),
        )
        if smoke_result is not None:
            return smoke_result
        diagnostic_result = run_diagnostic_command(
            args,
            runtime=_diagnostic_cli_runtime(),
        )
        if diagnostic_result is not None:
            return diagnostic_result
        if args.live_agent_command == "register":
            payload = {
                "agent_id": args.agent_id,
                "display_name": args.display_name,
                "provider_kind": args.provider_kind,
                "connection_kind": args.connection_kind,
                "session_id": args.session_id,
                "endpoint": args.endpoint,
                "meeting_id": args.meeting_id,
                "engagement_mode": args.engagement_mode,
                "capabilities": ["room_chat", "mentions"],
            }
            if args.join_semantics:
                payload["join_semantics"] = args.join_semantics
            persona_card_id = clean_persona_card_id(args.persona_card_id)
            if persona_card_id:
                payload["persona_card_id"] = persona_card_id
            if args.character_mode:
                payload["character_mode"] = args.character_mode
            response = _request_json(_server_url(args.server, "/api/live-agents"), method="POST", payload=payload)
            agent = response.get("agent", {}) if isinstance(response.get("agent"), dict) else {}
            if args.as_json:
                print(json.dumps(response, ensure_ascii=False, indent=2))
            else:
                print(f"Registered {agent.get('agent_id') or args.agent_id}")
            return 0
        if args.live_agent_command == "join-brief":
            return _run_live_agent_join_brief(args)
        if args.live_agent_command == "lan-invite":
            return _run_live_agent_lan_invite(args)
        if args.live_agent_command == "list":
            return _run_live_agent_list(args)
        if args.live_agent_command == "heartbeat":
            agent_id = urllib.parse.quote(args.agent_id, safe="")
            response = _request_json(
                _server_url(args.server, f"/api/live-agents/{agent_id}/heartbeat"),
                method="POST",
                payload=_heartbeat_payload(args),
            )
            agent = response.get("agent", {}) if isinstance(response.get("agent"), dict) else {}
            if args.as_json:
                print(json.dumps(response, ensure_ascii=False, indent=2))
            else:
                print(f"{agent.get('agent_id') or args.agent_id}: {agent.get('status') or args.status}")
            return 0
        if args.live_agent_command == "leave":
            return _run_live_agent_leave(args)
        if args.live_agent_command == "return-packet":
            return _run_live_agent_return_packet(args)
        if args.live_agent_command == "engagement":
            return _run_live_agent_engagement(args)
        if args.live_agent_command == "call":
            return _run_live_agent_call(args)
        if args.live_agent_command == "call-sequence":
            return _run_live_agent_call_sequence(args)
        if args.live_agent_command == "call-round":
            return _run_live_agent_call_round(args)
        if args.live_agent_command == "call-preset":
            return _run_live_agent_call_preset(args)
        if args.live_agent_command == "flow":
            return _run_live_agent_flow(args)
        if args.live_agent_command == "room-benchmark":
            return _run_live_agent_room_benchmark(args)
        if args.live_agent_command == "call-remaining-rounds":
            return _run_live_agent_call_remaining_rounds(args)
        if args.live_agent_command == "review-checkpoint":
            return _run_live_agent_review_checkpoint(args)
        if args.live_agent_command == "start-meeting":
            return _run_live_agent_start_meeting(args)
        if args.live_agent_command == "finalize-meeting":
            return _run_live_agent_finalize_meeting(args)
        if args.live_agent_command == "say":
            agent_id = urllib.parse.quote(args.agent_id, safe="")
            payload = {"message": " ".join(args.message), "kind": "message"}
            if args.source_event_id:
                payload["source_event_id"] = args.source_event_id
            if args.auto_chain_depth is not None:
                payload["auto_chain_depth"] = args.auto_chain_depth
            if args.flow_id:
                payload["flow_id"] = args.flow_id
                payload["flow_action"] = "speak"
                payload["flow_runtime_mode"] = "provider_tool_loop"
            if args.flow_meeting_id:
                payload["flow_meeting_id"] = args.flow_meeting_id
            response = _request_json(
                _server_url(args.server, f"/api/live-agents/{agent_id}/lobby"),
                method="POST",
                payload=payload,
            )
            event = response.get("event", {}) if isinstance(response.get("event"), dict) else {}
            if args.as_json:
                print(json.dumps(response, ensure_ascii=False, indent=2))
            else:
                print(f"Posted {event.get('id') or 'lobby message'}")
            return 0
        if args.live_agent_command == "dm-reply":
            return _run_live_agent_dm_reply(args)
        if args.live_agent_command in {"official-reply", "answer-turn"}:
            return _run_live_agent_answer_turn(args)
        if args.live_agent_command == "room":
            agent_id = urllib.parse.quote(args.agent_id, safe="")
            response = _request_json(_server_url(args.server, f"/api/live-agents/{agent_id}/room"))
            print(json.dumps(response, ensure_ascii=False, indent=2))
            return 0
        if args.live_agent_command == "read-since":
            return _run_live_agent_read_since(args)
        if args.live_agent_command == "wait-room-event":
            return _run_live_agent_wait_room_event(args)
        if args.live_agent_command in {"wait-official-turn", "wait-turn-request"}:
            return _run_live_agent_wait_turn_request(args)
        if args.live_agent_command == "wait-next":
            return _run_live_agent_wait_next(args)
        if args.live_agent_command == "health":
            return _run_live_agent_health(args)
        if args.live_agent_command == "local-resources":
            return _run_live_agent_local_resources(args)
        if args.live_agent_command == "preflight":
            return _run_live_agent_preflight(args)
        if args.live_agent_command == "discover":
            return _run_live_agent_discover(args)
        if args.live_agent_command == "auto-join":
            return _run_live_agent_auto_join(args)
        if args.live_agent_command == "continuity-proof":
            return _run_live_agent_continuity_proof(args)
        if args.live_agent_command == "continuity-proof-group":
            return _run_live_agent_continuity_proof_group(args)
        if args.live_agent_command == "persona-smoke":
            return _run_live_agent_persona_smoke(args)
        if args.live_agent_command == "operations":
            return _run_live_agent_operations(args)
        if args.live_agent_command == "session-runs":
            return _run_live_agent_session_runs(args)
        if args.live_agent_command == "delegate":
            return _run_live_agent_delegate(args)
        if args.live_agent_command == "run":
            return _run_live_agent_resident(args)
        if args.live_agent_command == "run-group":
            return _run_live_agent_group(args)
    except (OSError, subprocess.SubprocessError, urllib.error.URLError, ValueError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 1


def _heartbeat_payload(args: argparse.Namespace) -> dict[str, object]:
    payload = {"status": args.status}
    optional_fields = {
        "last_error": getattr(args, "last_error", None),
        "last_attention": getattr(args, "last_attention", None),
        "last_reply_at": getattr(args, "last_reply_at", None),
        "last_observed_event_id": getattr(args, "last_observed_event_id", None),
        "last_observed_live_event_id": getattr(args, "last_observed_live_event_id", None),
        "last_observed_dm_event_id": getattr(args, "last_observed_dm_event_id", None),
    }
    for key, value in optional_fields.items():
        if value is None or _is_unreplaced_template_placeholder(value):
            continue
        if key == "last_attention":
            attention = _clean_heartbeat_attention(value)
            if attention:
                payload[key] = attention
            continue
        payload[key] = value
    return payload


def _clean_heartbeat_attention(value: object) -> str:
    text = clean_lobby_text(value, limit=128)
    if not text:
        return ""
    if text in SAFE_PRESENCE_ATTENTION_CODES:
        return text
    return PRESENCE_ATTENTION_REDACTED


def _is_unreplaced_template_placeholder(value: object) -> bool:
    if not isinstance(value, str):
        return False
    return bool(re.fullmatch(r"\{[A-Za-z0-9_]+\}", value.strip()))


def _run_live_agent_leave(args: argparse.Namespace) -> int:
    agent_id = urllib.parse.quote(args.agent_id, safe="")
    response = _request_json(
        _server_url(args.server, f"/api/live-agents/{agent_id}/leave"),
        method="POST",
        payload=_leave_payload(args),
    )
    safe_response = _safe_leave_response(response)
    agent = safe_response.get("agent", {}) if isinstance(safe_response.get("agent"), dict) else {}
    if args.as_json:
        print(json.dumps(safe_response, ensure_ascii=False, indent=2))
    else:
        print(f"{agent.get('agent_id') or args.agent_id}: {agent.get('status') or 'offline'}")
    return 0


def _safe_leave_response(response: dict[str, object]) -> dict[str, object]:
    safe: dict[str, object] = {}
    agent = response.get("agent")
    if isinstance(agent, dict):
        safe["agent"] = safe_live_agent_roster_agent(agent)
    agents = response.get("agents")
    if isinstance(agents, list):
        safe["agents"] = safe_live_agent_roster_payload({"agents": agents}).get("agents", [])
    return safe


def _leave_payload(args: argparse.Namespace) -> dict[str, object]:
    payload = {
        "status": "offline",
        "last_error": "",
    }
    for key, arg_name in (
        ("last_observed_event_id", "last_observed_event_id"),
        ("last_observed_live_event_id", "last_observed_live_event_id"),
        ("last_observed_dm_event_id", "last_observed_dm_event_id"),
    ):
        value = getattr(args, arg_name, None)
        if value is not None:
            payload[key] = value
    return payload


def _run_live_agent_join_brief(args: argparse.Namespace) -> int:
    payload = _live_agent_join_brief_payload(args)
    if args.as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        _print_live_agent_join_brief(payload)
    return 0


def _live_agent_join_brief_payload(args: argparse.Namespace) -> dict[str, object]:
    return build_live_agent_join_brief(
        server=args.server,
        agent_id=args.agent_id,
        display_name=args.display_name,
        provider_kind=args.provider_kind,
        connection_kind=args.connection_kind,
        meeting_id=args.meeting_id,
        engagement_mode=args.engagement_mode,
        timeout=args.timeout,
        poll_interval=args.poll_interval,
        max_chain_depth=args.max_chain_depth,
    )


def _print_live_agent_join_brief(payload: dict[str, object]) -> None:
    agent = payload.get("agent") if isinstance(payload.get("agent"), dict) else {}
    commands = payload.get("commands") if isinstance(payload.get("commands"), dict) else {}
    templates = payload.get("templates") if isinstance(payload.get("templates"), dict) else {}
    agent_id = str(agent.get("agent_id") or "agent")
    print(f"Live-agent join brief for {agent_id}")
    _print_join_brief_command("Register", commands.get("register"))
    _print_join_brief_command("Wait loop", commands.get("wait_next"))
    _print_join_brief_command("Read diff", commands.get("read_since"))
    _print_join_brief_command("Room snapshot", commands.get("room"))
    _print_join_brief_command("Roster gate", commands.get("roster_gate"))
    _print_join_brief_command("Leave", commands.get("leave"))
    _print_join_brief_command("Lobby reply template", templates.get("say"))
    _print_join_brief_command("Official reply template", templates.get("official_reply"))
    _print_join_brief_command("Heartbeat template", templates.get("heartbeat"))
    print("Run Register first, then loop Wait and fill one reply template for each returned action.")


def _print_join_brief_command(label: str, value: object) -> None:
    if not isinstance(value, list):
        return
    command = [str(item) for item in value]
    print(f"{label}:")
    print(f"  {shlex.join(command)}")


def _run_live_agent_lan_invite(args: argparse.Namespace) -> int:
    secret = resolve_lan_invite_secret_ref(args.secret_ref)
    if not secret:
        raise ValueError("LAN invite secret is not available.")
    if args.lan_invite_command == "create":
        packet = create_lan_invite_packet(
            room_url=args.server,
            meeting_id=args.meeting_id,
            agent_id=args.agent_id,
            display_name=args.display_name,
            provider_kind=args.provider_kind,
            secret=secret,
            ttl_seconds=args.ttl_seconds,
        )
        if args.as_json:
            print(json.dumps(packet, ensure_ascii=False, indent=2))
        else:
            print(f"LAN invite for {packet.get('meeting_id')}: {packet.get('token')}")
        return 0
    if args.lan_invite_command == "verify":
        report = verify_lan_invite_token(
            args.token,
            secret=secret,
            expected_meeting_id=args.expected_meeting_id,
            expected_agent_id=args.expected_agent_id,
        )
        if args.as_json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print(f"LAN invite verification: {report.get('status')} ({report.get('identity_status')})")
        return 0 if report.get("status") == "ok" else 1
    return 1


def _run_live_agent_list(args: argparse.Namespace) -> int:
    try:
        payload = _request_json(_server_url(args.server, _live_agent_list_path(args)))
    except (OSError, urllib.error.URLError, ValueError, json.JSONDecodeError) as error:
        raise ValueError(_live_agent_list_fetch_error(error)) from error
    safe_payload = _safe_live_agent_list_payload(payload)
    _print_live_agent_list_payload(safe_payload, as_json=args.as_json)
    if args.require_match and _live_agent_list_payload_empty(safe_payload):
        return 1
    if args.require_all_agents and _live_agent_list_missing_required_agents(safe_payload, args.agent_ids):
        return 1
    if args.fail_on_attention and _live_agent_list_payload_needs_attention(safe_payload):
        return 1
    if args.require_host_approved and _live_agent_list_payload_has_unapproved_agents(safe_payload):
        return 1
    return 0


def _live_agent_list_path(args: argparse.Namespace) -> str:
    query: list[tuple[str, str]] = [("safe", "1")]
    meeting_id = str(getattr(args, "meeting_id", "") or "").strip()
    if meeting_id:
        query.append(("meeting_id", meeting_id))
    for agent_id in getattr(args, "agent_ids", []) or []:
        clean_agent_id = str(agent_id or "").strip()
        if clean_agent_id:
            query.append(("agent_id", clean_agent_id))
    for status in getattr(args, "statuses", []) or []:
        clean_status = str(status or "").strip()
        if clean_status:
            query.append(("status", clean_status))
    return f"/api/live-agents?{urllib.parse.urlencode(query)}"


def _live_agent_list_fetch_error(error: Exception) -> str:
    message = clean_lobby_text(error, limit=500)
    if message and not _looks_sensitive_presence_error(message):
        return f"Live-agent roster fetch failed: {message}"
    return "Live-agent roster fetch failed: details redacted."


def _print_live_agent_list_payload(payload: dict[str, object], *, as_json: bool) -> None:
    safe_payload = _safe_live_agent_list_payload(payload)
    if as_json:
        print(json.dumps(safe_payload, ensure_ascii=False, indent=2))
        return
    agents = safe_payload.get("agents") if isinstance(safe_payload.get("agents"), list) else []
    if not agents:
        print("no live agents")
        return
    for item in agents:
        if isinstance(item, dict):
            print(_format_live_agent_roster_agent(item))


def _safe_live_agent_list_payload(payload: dict[str, object]) -> dict[str, object]:
    return safe_live_agent_roster_payload(payload)


def _safe_live_agent_roster_text(value: object, *, limit: int, default: str = "") -> str:
    return safe_live_agent_roster_text(value, limit=limit, default=default)


def _safe_live_agent_roster_number(value: object) -> int | float:
    return safe_live_agent_roster_number(value)


def _format_live_agent_roster_agent(agent: dict[str, object]) -> str:
    agent_id = _safe_live_agent_roster_text(agent.get("agent_id"), limit=64, default="-")
    display_name = _safe_live_agent_roster_text(agent.get("display_name"), limit=128, default="-")
    provider_kind = _safe_live_agent_roster_text(agent.get("provider_kind"), limit=64, default="unknown")
    connection_kind = _safe_live_agent_roster_text(agent.get("connection_kind"), limit=64, default="unknown")
    status = _safe_live_agent_roster_text(agent.get("status"), limit=64, default="unknown")
    parts = [agent_id, display_name, f"{provider_kind}/{connection_kind}", status]
    suffix_parts = []
    _append_live_agent_roster_text(suffix_parts, "meeting", agent.get("meeting_id"))
    _append_live_agent_roster_text(suffix_parts, "join", agent.get("join_semantics"))
    _append_live_agent_roster_text(suffix_parts, "context", agent.get("context_durability"))
    _append_live_agent_roster_text(suffix_parts, "sandbox", agent.get("sandbox_enforcement"))
    _append_live_agent_roster_text(suffix_parts, "admission", agent.get("admission_status"))
    _append_live_agent_roster_bool(suffix_parts, "host_approved", agent.get("host_approved_binding"))
    _append_live_agent_roster_text(suffix_parts, "admission_source", agent.get("admission_evidence_source"))
    _append_live_agent_roster_text(suffix_parts, "binding_role", agent.get("binding_role_id"))
    _append_live_agent_roster_text(suffix_parts, "binding_provider", agent.get("binding_provider_id"))
    _append_live_agent_roster_text(suffix_parts, "binding_kind", agent.get("binding_provider_kind"))
    _append_live_agent_roster_text(suffix_parts, "binding_profile", agent.get("binding_permission_profile_id"))
    _append_live_agent_roster_text(suffix_parts, "binding_join", agent.get("binding_join_mode"))
    _append_live_agent_roster_list(suffix_parts, "binding_conflicts", agent.get("binding_conflicts"))
    _append_live_agent_roster_text(suffix_parts, "engagement", agent.get("engagement_mode"))
    _append_live_agent_roster_seconds(suffix_parts, "heartbeat_age", agent.get("heartbeat_age_seconds"))
    _append_live_agent_roster_seconds(suffix_parts, "stale_after", agent.get("stale_after_seconds"))
    _append_live_agent_roster_text(suffix_parts, "cursor", agent.get("last_observed_event_id"))
    _append_live_agent_roster_text(suffix_parts, "official_cursor", agent.get("last_observed_live_event_id"))
    suffix = f" {' '.join(suffix_parts)}" if suffix_parts else ""
    return f"{' '.join(parts)}{suffix}"


def _append_live_agent_roster_text(parts: list[str], label: str, value: object) -> None:
    text = _safe_live_agent_roster_text(value, limit=128)
    if text:
        parts.append(f"{label}={text}")


def _append_live_agent_roster_bool(parts: list[str], label: str, value: object) -> None:
    if isinstance(value, bool):
        parts.append(f"{label}={'yes' if value else 'no'}")


def _append_live_agent_roster_list(parts: list[str], label: str, value: object) -> None:
    if not isinstance(value, list):
        return
    items = [_safe_live_agent_roster_text(item, limit=64) for item in value]
    text = ",".join(item for item in items if item)
    if text:
        parts.append(f"{label}={text}")


def _append_live_agent_roster_seconds(parts: list[str], label: str, value: object) -> None:
    if value in (None, ""):
        return
    seconds = _safe_nonnegative_float(value)
    parts.append(f"{label}={_format_seconds(seconds)}")


def _live_agent_list_payload_needs_attention(payload: dict[str, object]) -> bool:
    agents = payload.get("agents") if isinstance(payload.get("agents"), list) else []
    return any(isinstance(item, dict) and _live_agent_roster_agent_needs_attention(item) for item in agents)


def _live_agent_list_payload_has_unapproved_agents(payload: dict[str, object]) -> bool:
    agents = payload.get("agents") if isinstance(payload.get("agents"), list) else []
    return any(isinstance(item, dict) and item.get("host_approved_binding") is not True for item in agents)


def _live_agent_list_payload_empty(payload: dict[str, object]) -> bool:
    agents = payload.get("agents") if isinstance(payload.get("agents"), list) else []
    return not any(isinstance(item, dict) for item in agents)


def _live_agent_list_missing_required_agents(payload: dict[str, object], agent_ids: list[str]) -> bool:
    required = {
        clean_lobby_text(agent_id, limit=64)
        for agent_id in agent_ids
        if clean_lobby_text(agent_id, limit=64)
    }
    if not required:
        return False
    agents = payload.get("agents") if isinstance(payload.get("agents"), list) else []
    returned = {
        str(item.get("agent_id") or "")
        for item in agents
        if isinstance(item, dict) and str(item.get("agent_id") or "")
    }
    return not required.issubset(returned)


def _live_agent_roster_agent_needs_attention(agent: dict[str, object]) -> bool:
    status = str(agent.get("status") or "").strip().casefold()
    return status not in {"online", "working"}


def _run_live_agent_engagement(args: argparse.Namespace) -> int:
    agent_id = urllib.parse.quote(args.agent_id, safe="")
    payload = {"engagement_mode": args.engagement_mode}
    response = _request_json(
        _server_url(args.server, f"/api/live-agents/{agent_id}/engagement"),
        method="POST",
        payload=payload,
    )
    if args.as_json:
        print(json.dumps(response, ensure_ascii=False, indent=2))
    else:
        agent = response.get("agent", {}) if isinstance(response.get("agent"), dict) else {}
        print(f"{agent.get('agent_id') or args.agent_id}: {agent.get('engagement_mode') or args.engagement_mode}")
    return 0


def _run_live_agent_return_packet(args: argparse.Namespace) -> int:
    agent_id = urllib.parse.quote(args.agent_id, safe="")
    query_values = {}
    if args.meeting_id:
        query_values["meeting_id"] = args.meeting_id
    query_values["source_event_id"] = args.source_event_id
    query = urllib.parse.urlencode(query_values)
    response = _request_json(_server_url(args.server, f"/api/live-agents/{agent_id}/return-packet?{query}"))
    if args.as_json:
        print(json.dumps(response, ensure_ascii=False, indent=2))
    else:
        print(str(response.get("markdown") or "").strip())
    return 0


def _run_live_agent_call(args: argparse.Namespace) -> int:
    meeting_id = urllib.parse.quote(args.meeting_id, safe="")
    payload = {
        "agent_id": args.agent_id,
        "role_id": args.role_id,
        "display_name": args.display_name,
        "content": " ".join(args.message),
        "turn_id": args.turn_id,
        "turn_index": args.turn_index,
    }
    if args.wait:
        payload["timeout_seconds"] = float(args.timeout)
        response = _request_json(
            _server_url(args.server, f"/api/meetings/{meeting_id}/live-agent-turns/call"),
            method="POST",
            payload=payload,
            timeout_seconds=_operation_http_timeout(float(args.timeout)),
        )
        if args.as_json:
            print(json.dumps(response, ensure_ascii=False, indent=2))
        else:
            request_event = response.get("request_event") if isinstance(response.get("request_event"), dict) else {}
            reply_event = response.get("reply_event") if isinstance(response.get("reply_event"), dict) else {}
            if response.get("status") == "answered":
                print(
                    f"Answered {reply_event.get('actor_id') or args.agent_id} "
                    f"official turn {reply_event.get('id') or 'reply'}"
                )
            else:
                print(
                    f"Timed out waiting for {request_event.get('target_agent_id') or args.agent_id} "
                    f"official turn {request_event.get('id') or 'request'}"
                )
        return 0 if response.get("status") == "answered" else 1
    response = _request_json(
        _server_url(args.server, f"/api/meetings/{meeting_id}/live-agent-turns/request"),
        method="POST",
        payload=payload,
    )
    if args.as_json:
        print(json.dumps(response, ensure_ascii=False, indent=2))
        return 0
    event = response.get("event") if isinstance(response.get("event"), dict) else {}
    print(f"Called {event.get('target_agent_id') or args.agent_id} for official turn {event.get('id') or 'request'}")
    return 0


def _run_live_agent_call_sequence(args: argparse.Namespace) -> int:
    meeting_id = urllib.parse.quote(args.meeting_id, safe="")
    turns = _load_live_agent_sequence_turns(args)
    payload = {
        "turns": turns,
        "timeout_seconds": float(args.timeout),
        "stop_on_timeout": bool(args.stop_on_timeout),
    }
    response = _request_json(
        _server_url(args.server, f"/api/meetings/{meeting_id}/live-agent-turns/sequence"),
        method="POST",
        payload=payload,
        timeout_seconds=_operation_http_timeout(float(args.timeout), windows=max(1, len(turns))),
    )
    if args.as_json:
        print(json.dumps(response, ensure_ascii=False, indent=2))
    else:
        print(
            "Official turn sequence "
            f"{response.get('status') or 'unknown'}: "
            f"{response.get('answered_count', 0)} answered, "
            f"{response.get('timeout_count', 0)} timed out, "
            f"{response.get('skipped_count', 0)} skipped"
        )
        for result in response.get("results", []):
            if not isinstance(result, dict):
                continue
            print(f"- {result.get('agent_id') or 'unknown'}: {_sequence_result_summary(result)}")
    return 0 if response.get("status") == "answered" else 1


def _run_live_agent_call_round(args: argparse.Namespace) -> int:
    meeting_id = urllib.parse.quote(args.meeting_id, safe="")
    payload = {
        "round_id": args.round_id,
        "role_ids": list(args.role_ids or []),
        "timeout_seconds": float(args.timeout),
        "stop_on_timeout": bool(args.stop_on_timeout),
    }
    instruction = " ".join(args.instruction).strip()
    if instruction:
        payload["content"] = instruction
    turn_windows = len(args.role_ids) if args.role_ids else MAX_LIVE_AGENT_SEQUENCE_TURNS
    response = _request_json(
        _server_url(args.server, f"/api/meetings/{meeting_id}/live-agent-turns/round"),
        method="POST",
        payload=payload,
        timeout_seconds=_operation_http_timeout(float(args.timeout), windows=max(1, turn_windows)),
    )
    if args.as_json:
        print(json.dumps(response, ensure_ascii=False, indent=2))
    else:
        print(
            f"Official round {response.get('round_id') or args.round_id} "
            f"{response.get('status') or 'unknown'}: "
            f"{response.get('answered_count', 0)} answered, "
            f"{response.get('timeout_count', 0)} timed out, "
            f"{response.get('skipped_count', 0)} skipped"
        )
        for result in response.get("results", []):
            if not isinstance(result, dict):
                continue
            print(f"- {result.get('agent_id') or 'unknown'}: {_sequence_result_summary(result)}")
    return 0 if response.get("status") in {"answered", "complete"} else 1


def _run_live_agent_call_preset(args: argparse.Namespace) -> int:
    meeting_id = urllib.parse.quote(args.meeting_id, safe="")
    payload = {
        "preset_id": args.preset_id,
        "role_ids": list(args.role_ids or []),
        "timeout_seconds": float(args.timeout),
        "stop_on_timeout": bool(args.stop_on_timeout),
    }
    turn_windows = len(args.role_ids) if args.role_ids else MAX_LIVE_AGENT_SEQUENCE_TURNS
    response = _request_json(
        _server_url(args.server, f"/api/meetings/{meeting_id}/live-agent-turns/preset"),
        method="POST",
        payload=payload,
        timeout_seconds=_operation_http_timeout(float(args.timeout), windows=max(1, turn_windows)),
    )
    if args.as_json:
        print(json.dumps(response, ensure_ascii=False, indent=2))
    else:
        print(
            f"Play preset {response.get('preset_id') or args.preset_id} "
            f"{response.get('status') or 'unknown'}: "
            f"{response.get('answered_count', 0)} answered, "
            f"{response.get('timeout_count', 0)} timed out, "
            f"{response.get('skipped_count', 0)} skipped"
        )
        for result in response.get("results", []):
            if not isinstance(result, dict):
                continue
            print(f"- {result.get('agent_id') or 'unknown'}: {_sequence_result_summary(result)}")
    return 0 if response.get("status") == "answered" else 1


def _run_live_agent_flow(args: argparse.Namespace) -> int:
    print("Play/free flow is disabled; use turn-based Agent Sessions.", file=sys.stderr)
    return 2
    options = FlowOptions(
        duration_seconds=float(args.duration_seconds),
        tick_interval=float(args.tick_interval),
        cooldown=float(args.cooldown),
        max_agent_turns=int(args.max_agent_turns),
        max_total_turns=int(args.max_total_turns),
        max_silence_seconds=float(args.max_silence_seconds),
    )
    client = LiveAgentFlowClient(
        server=args.server,
        request_json=_request_json,
        sleep_fn=time.sleep,
    )
    resource_recorder = None
    if args.resource_report:
        resource_recorder = FlowResourceRecorder(
            server=args.server,
            request_json=_request_json,
            sample_interval_seconds=float(args.resource_sample_interval),
        )
    response = client.run(
        meeting_id=args.meeting_id,
        topic=args.topic,
        options=options,
        sample_fn=resource_recorder.sample if resource_recorder is not None else None,
    )
    resource_report = None
    if resource_recorder is not None:
        resource_recorder.sample(response, force=True)
        resource_report = resource_recorder.write_report(
            args.resource_report,
            meeting_id=args.meeting_id,
            topic=args.topic,
            flow_result=response,
            runtime_mode=args.runtime_mode,
        )
    if args.as_json:
        output = dict(response)
        if resource_report is not None:
            output["resource_report"] = {
                "path": args.resource_report,
                "summary": resource_report.get("summary", {}),
                "sample_count": resource_report.get("sample_count", 0),
            }
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        flow = response.get("flow") if isinstance(response.get("flow"), dict) else {}
        print(
            f"Play flow {flow.get('status') or 'unknown'}: "
            f"{flow.get('meeting_id') or args.meeting_id} · "
            f"{flow.get('total_turns', 0)} turns · "
            f"{flow.get('agent_count', 0)} agents"
        )
        if resource_report is not None:
            summary = resource_report.get("summary") if isinstance(resource_report.get("summary"), dict) else {}
            print(
                "Resource report: "
                f"{args.resource_report} · samples={resource_report.get('sample_count', 0)} · "
                f"peak supervised RSS={summary.get('peak_supervised_rss_mb', 0)} MB · "
                f"peak supervised CPU={summary.get('peak_supervised_cpu_pct', 0)}%"
            )
    return 0 if str((response.get("flow") if isinstance(response.get("flow"), dict) else {}).get("status") or "") in {"finished", "stopped"} else 1


def _run_live_agent_room_benchmark(args: argparse.Namespace) -> int:
    from agentsassemble.room_event_benchmark import RoomEventBenchmarkOptions, run_room_event_benchmark

    output_root = Path(args.output_root) if args.output_root else None
    sse_samples = int(args.sse_samples)
    http_handler_factory = None
    if sse_samples:
        from agentsassemble.gui import _make_handler

        http_handler_factory = _make_handler
    result = run_room_event_benchmark(
        RoomEventBenchmarkOptions(
            output_root=output_root,
            events=int(args.events),
            read_window=int(args.read_window),
            warmup_events=int(args.warmup_events),
            agent_count=int(args.agent_count),
            sse_samples=sse_samples,
            cleanup=not bool(args.keep_output),
        ),
        http_handler_factory=http_handler_factory,
    )
    if args.as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        metrics = result.get("metrics") if isinstance(result.get("metrics"), dict) else {}
        lobby_append = metrics.get("lobby_append_ms") if isinstance(metrics.get("lobby_append_ms"), dict) else {}
        live_append = metrics.get("live_append_ms") if isinstance(metrics.get("live_append_ms"), dict) else {}
        lobby_tail = metrics.get("lobby_tail_read_ms") if isinstance(metrics.get("lobby_tail_read_ms"), dict) else {}
        fairness = metrics.get("flow_speaking_distribution") if isinstance(metrics.get("flow_speaking_distribution"), dict) else {}
        lobby_sse = metrics.get("lobby_sse_append_to_frame_ms") if isinstance(metrics.get("lobby_sse_append_to_frame_ms"), dict) else {}
        print("Room event benchmark:")
        print(f"- lobby append avg/p95: {lobby_append.get('avg_ms', 0)} / {lobby_append.get('p95_ms', 0)} ms")
        print(f"- live append avg/p95: {live_append.get('avg_ms', 0)} / {live_append.get('p95_ms', 0)} ms")
        print(f"- lobby tail read: {lobby_tail.get('avg_ms', 0)} ms")
        if lobby_sse:
            print(
                "- lobby SSE append-to-frame avg/p95: "
                f"{lobby_sse.get('avg_ms', 0)} / {lobby_sse.get('p95_ms', 0)} ms "
                f"(samples={lobby_sse.get('count', 0)}, cadence={lobby_sse.get('polling_cadence_seconds', 0)}s, "
                f"keepalive={lobby_sse.get('keepalive_interval_seconds', 0)}s)"
            )
        print(f"- speaking imbalance: {fairness.get('imbalance_ratio', 0)} ({fairness.get('definition', '')})")
    return 0


def _run_live_agent_call_remaining_rounds(args: argparse.Namespace) -> int:
    meeting_id = urllib.parse.quote(args.meeting_id, safe="")
    max_rounds = max(1, int(args.max_rounds))
    if max_rounds > MAX_LIVE_AGENT_ROUND_BATCH:
        raise ValueError(f"--max-rounds supports at most {MAX_LIVE_AGENT_ROUND_BATCH}.")
    payload = {
        "timeout_seconds": float(args.timeout),
        "stop_on_timeout": bool(args.stop_on_timeout),
        "max_rounds": max_rounds,
    }
    if getattr(args, "finalize_after_rounds", False):
        payload["finalize_after_rounds"] = True
    response = _request_json(
        _server_url(args.server, f"/api/meetings/{meeting_id}/live-agent-turns/rounds"),
        method="POST",
        payload=payload,
        timeout_seconds=_operation_http_timeout(
            float(args.timeout),
            windows=max_rounds * MAX_LIVE_AGENT_SEQUENCE_TURNS,
        ),
    )
    if args.as_json:
        print(json.dumps(response, ensure_ascii=False, indent=2))
    else:
        finalization = response.get("finalization") if isinstance(response.get("finalization"), dict) else None
        finalization_suffix = ""
        if finalization is not None:
            finalization_suffix = (
                f"; finalization {finalization.get('status') or 'unknown'}: "
                f"{finalization.get('official_event_count', 0)} official events"
            )
        print(
            "Official remaining rounds "
            f"{response.get('status') or 'unknown'}: "
            f"{response.get('round_count', 0)} rounds, "
            f"{response.get('answered_round_count', 0)} answered, "
            f"{response.get('completed_round_count', 0)} already complete, "
            f"{response.get('timeout_round_count', 0)} timed out, "
            f"{response.get('skipped_round_count', 0)} skipped"
            f"{finalization_suffix}"
        )
        for result in response.get("results", []):
            if not isinstance(result, dict):
                continue
            print(f"- {result.get('round_id') or 'unknown'}: {result.get('status') or 'unknown'}")
    if response.get("status") not in {"answered", "complete"}:
        return 1
    finalization = response.get("finalization") if isinstance(response.get("finalization"), dict) else None
    if finalization is not None and finalization.get("status") not in {"finalized", "already_finalized"}:
        return 1
    return 0


def _run_live_agent_review_checkpoint(args: argparse.Namespace) -> int:
    meeting_id = urllib.parse.quote(args.meeting_id, safe="")
    payload = {
        "group_id": str(args.group_id or ""),
        "agent_ids": list(args.agent_ids or []),
        "content": " ".join(args.message),
        "checkpoint_id": str(args.checkpoint_id or ""),
        "timeout_seconds": float(args.timeout),
    }
    target_windows = len(args.agent_ids) if args.agent_ids else MAX_LIVE_AGENT_SEQUENCE_TURNS
    response = _request_json(
        _server_url(args.server, f"/api/meetings/{meeting_id}/review-checkpoints"),
        method="POST",
        payload=payload,
        timeout_seconds=_operation_http_timeout(float(args.timeout), windows=max(1, target_windows)),
    )
    if args.as_json:
        print(json.dumps(response, ensure_ascii=False, indent=2))
    else:
        print(
            f"Review checkpoint {response.get('checkpoint_id') or args.checkpoint_id or 'unknown'} "
            f"{response.get('status') or 'unknown'}: "
            f"{response.get('answered_count', 0)}/{response.get('turn_count', 0)} answered, "
            f"{response.get('timeout_count', 0)} timed out, "
            f"{response.get('skipped_count', 0)} skipped"
        )
        reason = str(response.get("reason") or "").strip()
        if reason:
            print(f"reason: {reason}")
        for result in response.get("results", []):
            if not isinstance(result, dict):
                continue
            print(f"- {result.get('agent_id') or 'unknown'}: {_sequence_result_summary(result)}")
    return 0 if response.get("status") == "answered" else 1


def _run_live_agent_start_meeting(args: argparse.Namespace) -> int:
    payload = {
        "meeting_id": str(args.meeting_id or ""),
        "council_config_path": str(args.council_config or ""),
        "agent_config_path": str(args.agent_config or ""),
    }
    response = _request_json(
        _server_url(args.server, "/api/live-agent-meetings/start"),
        method="POST",
        payload=payload,
    )
    if args.as_json:
        print(json.dumps(response, ensure_ascii=False, indent=2))
        return 0
    meeting = response.get("meeting") if isinstance(response.get("meeting"), dict) else {}
    roles = meeting.get("roles") if isinstance(meeting.get("roles"), list) else []
    bindings = meeting.get("agent_bindings") if isinstance(meeting.get("agent_bindings"), list) else []
    meeting_id = str(response.get("meeting_id") or meeting.get("meeting_id") or "unknown")
    print(
        f"Started resident live-agent meeting {meeting_id}: "
        f"{len(roles)} roles, {len(bindings)} bound agents"
    )
    return 0


def _run_live_agent_finalize_meeting(args: argparse.Namespace) -> int:
    meeting_id = urllib.parse.quote(str(args.meeting_id or ""), safe="")
    response = _request_json(
        _server_url(args.server, f"/api/meetings/{meeting_id}/finalize"),
        method="POST",
        payload={"force": bool(args.force), "close_pending": bool(args.close_pending)},
        timeout_seconds=20.0,
    )
    if args.as_json:
        print(json.dumps(response, ensure_ascii=False, indent=2))
    else:
        print(_format_live_agent_finalize_meeting(response))
    return 0 if response.get("status") in {"finalized", "already_finalized"} else 1


def _format_live_agent_finalize_meeting(response: dict[str, object]) -> str:
    status = str(response.get("status") or "unknown")
    meeting_id = str(response.get("meeting_id") or "unknown")
    official_count = response.get("official_event_count", 0)
    prefix = "Already finalized" if status == "already_finalized" else "Finalized"
    try:
        cancelled_count = max(0, int(response.get("cancelled_pending_count", 0)))
    except (TypeError, ValueError):
        cancelled_count = 0
    if cancelled_count:
        suffix = f", {cancelled_count} pending turn{'s' if cancelled_count != 1 else ''} cancelled"
    else:
        suffix = ""
    return f"{prefix} {meeting_id}: {official_count} official events{suffix}"


def _load_live_agent_sequence_turns(args: argparse.Namespace) -> list[dict[str, object]]:
    if bool(args.turns_json) == bool(args.turns_file):
        raise ValueError("Provide exactly one of --turns-json or --turns-file.")
    text = args.turns_json
    if args.turns_file:
        text = Path(args.turns_file).read_text(encoding="utf-8")
    loaded = json.loads(text)
    if not isinstance(loaded, list) or not loaded:
        raise ValueError("Official turn sequence requires a non-empty JSON array.")
    turns = []
    for index, item in enumerate(loaded):
        if not isinstance(item, dict):
            raise ValueError(f"Official turn sequence item {index} must be an object.")
        turns.append(item)
    return turns


def _sequence_result_summary(result: dict[str, object]) -> str:
    status = str(result.get("status") or "unknown")
    reply_event = result.get("reply_event") if isinstance(result.get("reply_event"), dict) else {}
    request_event = result.get("request_event") if isinstance(result.get("request_event"), dict) else {}
    if status == "answered":
        return f"answered {reply_event.get('id') or 'reply'}"
    if status == "timeout":
        return f"timeout {request_event.get('id') or 'request'}"
    if status == "skipped":
        return "skipped"
    return status


def _joined_room_session_token(joined: object) -> str:
    if isinstance(joined, dict):
        return str(joined.get("session_token") or "")
    return str(joined or "")


def _config_with_joined_room_session(config: ResidentAgentConfig, joined: object) -> ResidentAgentConfig:
    if not isinstance(joined, dict):
        return config
    updates: dict[str, str] = {}
    for field, key in (
        ("agent_id", "agent_id"),
        ("display_name", "display_name"),
        ("meeting_id", "meeting_id"),
    ):
        value = str(joined.get(key) or "").strip()
        if value:
            updates[field] = value
    if not updates:
        return config
    from dataclasses import replace

    return replace(config, **updates)


def _run_ws_resident_command(args: argparse.Namespace, config: ResidentAgentConfig) -> int:
    """One-command WS launch: connect the provider agent over the governed
    WebSocket (run_provider_ws_resident) instead of the HTTP poll runner. Reuses
    the provider's command runner as the brain + the runner's prompt envelope."""
    from agentsassemble.room_engagement import resolve_engagement, room_uses_floor
    from agentsassemble.ws_resident import run_provider_ws_resident
    from agentsassemble.ws_room_client import (
        fetch_room_conversation_mode,
        join_room_session,
        meeting_id_from_invite_token,
    )

    invite_token = str(getattr(args, "invite_token", "") or "")
    session_token = str(getattr(args, "session_token", "") or "")
    if not session_token:
        if not invite_token:
            raise ValueError("--transport ws requires --session-token or --invite-token.")
        joined_session = join_room_session(
            config.server,
            invite_token,
            display_name=config.display_name or config.agent_id,
            participant_type="agent",
            device_token=config.agent_id,
        )
        session_token = _joined_room_session_token(joined_session)
        config = _config_with_joined_room_session(config, joined_session)
    # The room's conversation mode (quiet/free/ordered) drives how the agent
    # engages. Resolve the room id: explicit --meeting-id, else read it from the
    # invite token — without it the fetch can't find the room (the bug that made
    # free/ordered silently no-op).
    meeting_id = str(config.meeting_id or "") or meeting_id_from_invite_token(invite_token)
    if meeting_id and not config.meeting_id:
        from dataclasses import replace

        config = replace(config, meeting_id=meeting_id)
    conversation_mode = fetch_room_conversation_mode(config.server, meeting_id)
    effective_engagement = resolve_engagement(conversation_mode, config.engagement_mode)
    use_floor = room_uses_floor(conversation_mode)
    command_runner = _command_runner_for_config(config, output_root=str(getattr(args, "output_root", "") or ""))
    restore_signal_handlers = _install_resident_shutdown_signal_handlers(lambda: _close_command_runner(command_runner))
    try:
        replies = run_provider_ws_resident(
            config.server,
            session_token,
            config,
            command_runner,
            max_replies=int(getattr(config, "max_ticks", 0) or 0),  # 0 = run until killed
            engagement_mode=effective_engagement,
            use_floor=use_floor,
        )
    finally:
        restore_signal_handlers()
        _close_command_runner(command_runner)
    print(f"WS resident agent stopped after posting {replies} replies")
    return 0


def _run_live_agent_resident(args: argparse.Namespace) -> int:
    config = config_from_args(args)
    setup_error = _resident_config_setup_error(config)
    if setup_error:
        raise ValueError(f"{config.agent_id}: {setup_error}")
    if str(getattr(args, "transport", "http") or "http") == "ws":
        return _run_ws_resident_command(args, config)
    if config.connection_kind == "self_service":
        runner = _SelfServiceResidentSupervisor(
            config,
            request_json=_request_json,
            sleep_fn=time.sleep,
        )
        replies = 0
        restore_signal_handlers = lambda: None
        try:
            restore_signal_handlers = _install_resident_shutdown_signal_handlers(runner.close)
            replies = runner.run()
        except KeyboardInterrupt:
            runner.close()
        finally:
            restore_signal_handlers()
        print(f"Self-service resident agent stopped after posting {replies} parent-managed replies")
        return 0
    command_runner = _command_runner_for_config(config, output_root=str(getattr(args, "output_root", "") or ""))
    runner = LiveAgentRunner(
        config,
        request_json=_request_json,
        command_runner=command_runner,
        sleep_fn=time.sleep,
        # Standalone single-agent run owns its own process: advertise pid +
        # relaunch recipe so the room can STOP/RESUME it like a spawned agent.
        self_relaunch=True,
    )
    replies = 0
    restore_signal_handlers = lambda: None
    try:
        restore_signal_handlers = _install_resident_shutdown_signal_handlers(lambda: _close_command_runner(command_runner))
        replies = runner.run()
    except KeyboardInterrupt:
        _close_command_runner(command_runner)
    finally:
        restore_signal_handlers()
        _close_command_runner(command_runner)
    print(f"Resident agent stopped after posting {replies} replies")
    return 0


def _run_ws_group_resident(config) -> int:
    """Run one group member over the governed WebSocket resident loop."""
    from agentsassemble.room_engagement import resolve_engagement, room_uses_floor
    from agentsassemble.ws_resident import run_provider_ws_resident
    from agentsassemble.ws_room_client import (
        fetch_room_conversation_mode,
        join_room_session,
        meeting_id_from_invite_token,
    )

    invite_token = str(getattr(config, "invite_token", "") or "")
    if not invite_token:
        raise ValueError(f"{config.agent_id}: ws transport requires invite_token in group config.")
    joined_session = join_room_session(
        config.server,
        invite_token,
        display_name=config.display_name or config.agent_id,
        participant_type="agent",
        device_token=config.agent_id,
    )
    session_token = _joined_room_session_token(joined_session)
    config = _config_with_joined_room_session(config, joined_session)
    meeting_id = str(config.meeting_id or "") or meeting_id_from_invite_token(invite_token)
    if meeting_id and not config.meeting_id:
        from dataclasses import replace

        config = replace(config, meeting_id=meeting_id)
    conversation_mode = fetch_room_conversation_mode(config.server, meeting_id)
    effective_engagement = resolve_engagement(conversation_mode, config.engagement_mode)
    use_floor = room_uses_floor(conversation_mode)
    command_runner = _command_runner_for_config(config)
    try:
        return run_provider_ws_resident(
            config.server,
            session_token,
            config,
            command_runner,
            max_replies=int(config.max_ticks or 0),
            engagement_mode=effective_engagement,
            use_floor=use_floor,
        )
    finally:
        _close_command_runner(command_runner)


def _run_live_agent_group(args: argparse.Namespace) -> int:
    configs = load_group_configs(Path(args.config), max_ticks_override=args.max_ticks, server_override=args.server)
    config_errors = _resident_group_config_errors(configs)
    if config_errors:
        for agent_id, error in config_errors.items():
            print(f"{agent_id}: {error}", file=sys.stderr)
        return 2
    stop_event = threading.Event()
    results: dict[str, int] = {}
    errors: dict[str, str] = {}
    active_command_runners: list[object] = []
    active_command_runners_lock = threading.Lock()

    def sleep(seconds: float) -> None:
        stop_event.wait(seconds)

    def close_active_command_runners() -> None:
        with active_command_runners_lock:
            runners_to_close = list(active_command_runners)
        for active_runner in runners_to_close:
            _close_command_runner(active_runner)

    def shutdown_group() -> None:
        stop_event.set()
        close_active_command_runners()

    def run_agent(config) -> None:
        command_runner = None
        try:
            _validate_resident_config(config)
            transport = str(getattr(config, "transport", "http") or "http")
            if transport == "ws":
                if config.connection_kind == "self_service":
                    raise ValueError(f"{config.agent_id}: self_service does not support ws transport.")
                results[config.agent_id] = _run_ws_group_resident(config)
                return
            if config.connection_kind == "self_service":
                command_runner = _SelfServiceResidentSupervisor(
                    config,
                    request_json=_request_json,
                    sleep_fn=sleep,
                    stop_event=stop_event,
                    isolate_process_group=False,
                )
            else:
                command_runner = _command_runner_for_config(config)
            with active_command_runners_lock:
                active_command_runners.append(command_runner)
            if config.connection_kind == "self_service":
                results[config.agent_id] = command_runner.run()
            else:
                runner = LiveAgentRunner(
                    config,
                    request_json=_request_json,
                    command_runner=command_runner,
                    sleep_fn=sleep,
                    stop_event=stop_event,
                )
                results[config.agent_id] = runner.run()
        except BaseException as error:  # pragma: no cover - surfaced through CLI status in integration use
            if isinstance(error, KeyboardInterrupt):
                shutdown_group()
                return
            if stop_event.is_set():
                return
            errors[config.agent_id] = str(error)
            if _should_heartbeat_resident_worker_error(config, error):
                _heartbeat_resident_worker_error(config, error)
        finally:
            if command_runner is not None:
                _close_command_runner(command_runner)
                with active_command_runners_lock:
                    if command_runner in active_command_runners:
                        active_command_runners.remove(command_runner)

    threads = [threading.Thread(target=run_agent, args=(config,), daemon=True) for config in configs]
    # Stagger startups so N residents don't all boot their CLI / open their WS
    # connection in the same instant (a thundering herd that starved connects in
    # practice). Only the *launch* is serialized — once started, agents run
    # concurrently, so a slow provider never blocks the others' turns.
    stagger_seconds = max(0.0, float(getattr(args, "launch_stagger_seconds", 0.0) or 0.0))
    started_threads: list[threading.Thread] = []
    restore_signal_handlers = lambda: None
    try:
        restore_signal_handlers = _install_resident_shutdown_signal_handlers(shutdown_group)
        for index, thread in enumerate(threads):
            if index > 0 and stagger_seconds > 0:
                sleep(stagger_seconds)
                if stop_event.is_set():
                    break  # a shutdown arrived during the stagger wait; don't keep launching
            thread.start()
            started_threads.append(thread)
        for thread in started_threads:
            thread.join()
    except KeyboardInterrupt:
        shutdown_group()
        for thread in started_threads:
            thread.join(timeout=5)
    finally:
        restore_signal_handlers()
    if errors:
        for agent_id, error in errors.items():
            print(f"{agent_id}: {error}", file=sys.stderr)
        return 2
    total = sum(results.values())
    summary = ", ".join(f"{config.agent_id}={results.get(config.agent_id, 0)}" for config in configs)
    print(f"Resident group stopped after posting {total} replies ({summary})")
    return 0


def _should_heartbeat_resident_worker_error(config: ResidentAgentConfig, error: BaseException) -> bool:
    return not (config.connection_kind == "self_service" and isinstance(error, subprocess.CalledProcessError))


def _heartbeat_resident_worker_error(config: ResidentAgentConfig, error: BaseException) -> None:
    try:
        _request_json(
            _server_url(config.server, f"/api/live-agents/{urllib.parse.quote(config.agent_id, safe='')}/heartbeat"),
            method="POST",
            payload={"status": "error", "last_error": _resident_worker_error_message(error)},
            timeout_seconds=2.0,
        )
    except Exception:
        return


def _resident_worker_error_message(error: BaseException) -> str:
    message = str(error).strip()
    if message and _looks_sensitive_presence_error(message):
        return "Resident worker error details redacted."
    error_type = type(error).__name__
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", error_type):
        return f"Resident worker failed with {error_type}."
    return "Resident worker failed."


def _resident_group_config_errors(configs: list[ResidentAgentConfig]) -> dict[str, str]:
    errors = _duplicate_resident_agent_id_errors(configs)
    for config in configs:
        if config.agent_id in errors:
            continue
        try:
            setup_error = _resident_config_setup_error(config)
            if setup_error:
                errors[config.agent_id] = setup_error
        except Exception as error:
            errors[config.agent_id] = str(error)
    return errors


def _duplicate_resident_agent_id_errors(configs: list[ResidentAgentConfig]) -> dict[str, str]:
    counts: dict[str, int] = {}
    for config in configs:
        if config.agent_id:
            counts[config.agent_id] = counts.get(config.agent_id, 0) + 1
    return {
        agent_id: "Duplicate agent id in resident group config."
        for agent_id, count in counts.items()
        if count > 1
    }


def _resident_config_setup_error(config: ResidentAgentConfig) -> str:
    _validate_resident_config(config)
    if config.connection_kind == "remote_bridge":
        probe_runner = _command_runner_for_config(config)
        _close_command_runner(probe_runner)
        return ""
    return resident_config_setup_error(config)


def _run_live_agent_health(args: argparse.Namespace) -> int:
    if args.wait_ok and args.wait_session_ready:
        raise ValueError("Use only one of --wait-ok or --wait-session-ready.")
    if args.wait_session_ready and (not str(args.meeting_id or "").strip() or not str(args.group_id or "").strip()):
        raise ValueError("--wait-session-ready requires --meeting-id and --group-id.")
    if args.wait_ok or args.wait_session_ready:
        return _run_live_agent_health_wait(args)
    payload = _request_json(_server_url(args.server, "/api/live-agent-health"))
    _print_live_agent_health_payload(payload, as_json=args.as_json)
    return 1 if args.fail_on_degraded and payload.get("status") != "ok" else 0


def _run_live_agent_health_wait(args: argparse.Namespace) -> int:
    timeout_seconds = float(args.timeout)
    poll_interval = max(0.01, float(args.poll_interval))
    deadline = time.monotonic() + timeout_seconds
    attempts = 0
    last_payload: dict[str, object] | None = None
    while True:
        now = time.monotonic()
        if attempts > 0 and now >= deadline:
            if last_payload is not None:
                _print_live_agent_health_payload(last_payload, as_json=args.as_json)
            return 1
        remaining_before_poll = max(0.01, deadline - now)
        attempts += 1
        try:
            payload = _request_json(
                _server_url(args.server, "/api/live-agent-health"),
                timeout_seconds=remaining_before_poll,
            )
        except (TimeoutError, urllib.error.URLError) as error:
            if not _is_live_agent_wait_timeout(error):
                raise
            if last_payload is not None:
                _print_live_agent_health_payload(last_payload, as_json=args.as_json)
            return 1
        last_payload = payload
        if _live_agent_health_wait_satisfied(payload, args):
            _print_live_agent_health_payload(payload, as_json=args.as_json)
            return 0
        remaining_after_poll = max(0.0, deadline - time.monotonic())
        if remaining_after_poll > 0:
            time.sleep(min(poll_interval, remaining_after_poll))


def _print_live_agent_health_payload(payload: dict[str, object], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(_format_live_agent_health(payload))


def _live_agent_health_wait_satisfied(payload: dict[str, object], args: argparse.Namespace) -> bool:
    if args.wait_session_ready:
        session = _find_live_agent_health_session(payload, args.meeting_id, args.group_id)
        if session is None or str(session.get("status") or "").strip() != "ready":
            return False
        return not args.fail_on_degraded or payload.get("status") == "ok"
    return payload.get("status") == "ok"


def _run_live_agent_local_resources(args: argparse.Namespace) -> int:
    payload = _request_json(_server_url(args.server, "/api/local-resources"))
    _print_live_agent_local_resources_payload(payload, as_json=args.as_json)
    return 1 if args.fail_on_degraded and payload.get("status") != "ok" else 0


def _print_live_agent_local_resources_payload(payload: dict[str, object], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(_format_live_agent_local_resources(payload))


def _format_live_agent_local_resources(payload: dict[str, object]) -> str:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    load_average = payload.get("load_average") if isinstance(payload.get("load_average"), dict) else {}
    processes = payload.get("processes") if isinstance(payload.get("processes"), list) else []
    lines = [
        f"local resources: {payload.get('status') or 'unknown'}",
        (
            f"load: {load_average.get('one', 0)} / {load_average.get('five', 0)} / "
            f"{load_average.get('fifteen', 0)} on {payload.get('cpu_count') or 0} CPUs"
        ),
        (
            f"tracked processes: {summary.get('process_count', 0)}, "
            f"cpu {summary.get('total_cpu_pct', 0)}%, rss {_format_kb_as_mb(summary.get('total_rss_kb'))}"
        ),
    ]
    attention = summary.get("attention") if isinstance(summary.get("attention"), list) else []
    if attention:
        lines.append(f"attention: {_attention_summary(attention)}")
    for process in processes[:8]:
        if not isinstance(process, dict):
            continue
        lines.append(
            (
                f"- {process.get('pid')}: {process.get('comm') or 'unknown'} "
                f"{process.get('role') or 'other'} "
                f"cpu {process.get('cpu_pct', 0)}% rss {_format_kb_as_mb(process.get('rss_kb'))}"
            )
        )
    return "\n".join(lines)


def _format_kb_as_mb(value: object) -> str:
    try:
        kb = float(value or 0)
    except (TypeError, ValueError):
        kb = 0.0
    return f"{kb / 1024:.1f} MB"


def _find_live_agent_health_session(
    payload: dict[str, object],
    meeting_id: str,
    group_id: str,
) -> dict[str, object] | None:
    sessions = payload.get("sessions") if isinstance(payload.get("sessions"), dict) else {}
    items = sessions.get("items") if isinstance(sessions.get("items"), list) else []
    for item in items:
        if not isinstance(item, dict):
            continue
        if str(item.get("meeting_id") or "") == meeting_id and str(item.get("group_id") or "") == group_id:
            return item
    return None


def _run_live_agent_preflight(args: argparse.Namespace) -> int:
    report = preflight_live_agent_config(Path(args.config), server_override=args.server)
    if args.as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(_format_live_agent_preflight(report))
    return 0 if report.get("status") == "ok" else 1


def _write_live_agent_discovery_outputs(
    args: argparse.Namespace,
    *,
    session_bundle: bool,
) -> tuple[Path | None, dict[str, object]]:
    report = build_discovered_live_agent_config(
        server=args.server,
        meeting_id=args.meeting_id,
        engagement_mode=args.engagement_mode,
        include_legacy_gemini=args.include_legacy_gemini,
    )
    if _live_agent_auto_join_has_exact_approval_args(args):
        apply_discovery_approval_filter(
            report,
            approved_agents=getattr(args, "approve_agents", []) or [],
            approved_commands=getattr(args, "approve_commands", []) or [],
        )
    output_path = Path(args.output) if args.output else None
    if report.get("status") == "ok" and output_path is not None:
        session_bundle_paths = None
        if session_bundle:
            session_bundle_paths = discovered_session_bundle_paths(
                output_path,
                council_output=args.session_council_output,
                agent_output=args.session_agent_output,
            )
            validate_distinct_session_bundle_paths(output_path, *session_bundle_paths)
        write_agent_config(output_path, report["config"])
        fill_discovery_next_command_output(report, str(output_path))
        if session_bundle and session_bundle_paths is not None:
            council_output, agent_output = session_bundle_paths
            bundle = build_discovered_session_bundle(report["config"])
            write_agent_config(council_output, bundle["council_config"])
            write_agent_config(agent_output, bundle["agent_config"])
            add_session_bundle_outputs(
                report,
                live_agent_output=str(output_path),
                council_output=str(council_output),
                agent_output=str(agent_output),
                server=args.server,
                meeting_id=args.meeting_id,
                group_id=clean_live_agent_group_id(output_path.stem),
            )
    return output_path, report


def _run_live_agent_discover(args: argparse.Namespace) -> int:
    output_path, report = _write_live_agent_discovery_outputs(args, session_bundle=bool(args.session_bundle))
    if args.as_json:
        print(json.dumps({"output": str(output_path or ""), **report}, ensure_ascii=False, indent=2))
    else:
        print(_format_live_agent_discovery(report, output_path=output_path))
    return 0 if report.get("status") == "ok" else 1


def _run_live_agent_auto_join(args: argparse.Namespace) -> int:
    validate_session_auto_restart_args(args)
    output_path, report = _write_live_agent_discovery_outputs(args, session_bundle=True)
    discovery_payload = {"output": str(output_path or ""), **report}
    if report.get("status") != "ok":
        result = {"status": report.get("status") or "empty", "action": "none", "discovery": discovery_payload, "session": {}}
        if args.as_json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(_format_live_agent_discovery(report, output_path=output_path))
        return 1
    if _live_agent_discovery_requires_approval(report) and not bool(args.approve_real_providers):
        result = {
            "status": "approval_required",
            "action": "none",
            "approval_required": {
                "commands": _live_agent_discovery_approval_commands(report),
            },
            "discovery": discovery_payload,
            "session": {},
        }
        if args.as_json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            commands = ", ".join(result["approval_required"]["commands"]) or "real provider CLI"
            print(f"Auto-join requires --approve-real-providers before starting: {commands}")
        return 1
    session_bundle = report.get("session_bundle") if isinstance(report.get("session_bundle"), dict) else {}
    ensure_args = argparse.Namespace(**vars(args))
    ensure_args.group_id = str(session_bundle.get("group_id") or "")
    ensure_args.council_config = str(session_bundle.get("council_config_path") or "")
    ensure_args.agent_config = str(session_bundle.get("agent_config_path") or "")
    ensure_args.live_agent_config = str(session_bundle.get("live_agent_config_path") or output_path or "")
    ensure_args.probe_bound_agents = _live_agent_auto_join_requires_reply_probe(args, report)
    ensure_args.approve_real_providers = bool(args.approve_real_providers) or discovery_has_exact_approval(report)
    action, response = _ensure_live_agent_session_run(ensure_args)
    result = {
        "status": response.get("status") or "unknown",
        "action": action,
        "discovery": discovery_payload,
        "session": response,
    }
    if args.as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"Auto-joined via {action}: {format_session_start(response)}")
    return session_command_exit_code(response)


def _live_agent_discovery_requires_approval(report: dict[str, object]) -> bool:
    discoveries = report.get("discoveries") if isinstance(report.get("discoveries"), list) else []
    return any(
        isinstance(item, dict)
        and item.get("included")
        and item.get("requires_approval")
        and item.get("approval_status") != "approved"
        for item in discoveries
    )


def _live_agent_auto_join_requires_reply_probe(args: argparse.Namespace, report: dict[str, object]) -> bool:
    return bool(getattr(args, "probe_bound_agents", False)) or discovery_has_exact_approval(report) or (
        bool(getattr(args, "approve_real_providers", False)) and _live_agent_discovery_requires_approval(report)
    )


def _live_agent_auto_join_has_exact_approval_args(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "approve_agents", []) or getattr(args, "approve_commands", []))


def _live_agent_discovery_approval_commands(report: dict[str, object]) -> list[str]:
    discoveries = report.get("discoveries") if isinstance(report.get("discoveries"), list) else []
    commands = []
    for item in discoveries:
        if not isinstance(item, dict) or not item.get("included") or not item.get("requires_approval"):
            continue
        command = str(item.get("command") or "").strip()
        if command:
            commands.append(command)
    return commands[:5]


def _ensure_live_agent_session_run(args: argparse.Namespace) -> tuple[str, dict[str, object]]:
    runtime = _legacy_session_cli_runtime()
    payload = session_start_payload(args)
    timeout_seconds = session_request_timeout(
        args,
        payload,
        runtime=runtime,
    )
    response = _request_json(
        _server_url(str(args.server), "/api/live-agent-session-runs/ensure"),
        method="POST",
        payload=payload,
        timeout_seconds=timeout_seconds,
    )
    action = str(response.get("action") or "ensure")
    if action != "none":
        response = wait_for_session_after_control(args, response, runtime=runtime)
    return action, response


def _format_live_agent_discovery(report: dict[str, object], *, output_path: Path | None) -> str:
    status = str(report.get("status") or "empty")
    lines = [f"discover: {status}"]
    config = report.get("config") if isinstance(report.get("config"), dict) else {}
    agents = config.get("agents") if isinstance(config.get("agents"), list) else []
    if output_path is not None and status == "ok":
        lines.append(f"wrote {output_path}")
    if agents:
        labels = [str(agent.get("agent_id") or "") for agent in agents if isinstance(agent, dict)]
        lines.append("agents " + ", ".join(label for label in labels if label))
    discoveries = report.get("discoveries") if isinstance(report.get("discoveries"), list) else []
    for item in discoveries:
        if not isinstance(item, dict):
            continue
        entry = _format_live_agent_discovery_entry(item)
        if entry:
            lines.append(entry)
    skipped = [
        f"{item.get('command')}:{item.get('reason')}"
        for item in discoveries
        if isinstance(item, dict) and item.get("available") and not item.get("included")
    ]
    if skipped:
        lines.append("skipped " + ", ".join(skipped))
    if status != "ok":
        lines.append("No supported local agent CLIs found.")
    return "\n".join(lines)


def _format_live_agent_discovery_entry(item: dict[str, object]) -> str:
    command = str(item.get("command") or "").strip()
    entry_status = str(item.get("entry_status") or "").strip()
    entry_mode = str(item.get("entry_mode") or item.get("connection_kind") or "").strip()
    join_semantics = str(item.get("join_semantics") or "").strip()
    context_durability = str(item.get("context_durability") or "").strip()
    sandbox_enforcement = str(item.get("sandbox_enforcement") or "").strip()
    evidence_basis = str(item.get("evidence_basis") or "").strip()
    operator_action = str(item.get("operator_action") or "").strip()
    approval = "approval required" if item.get("requires_approval") else ""
    parts = [
        command,
        entry_status,
        entry_mode,
        join_semantics,
        context_durability,
        sandbox_enforcement,
        evidence_basis,
        operator_action,
        approval,
    ]
    clean = [part for part in parts if part]
    return "entry " + " ".join(clean) if clean else ""


def _run_live_agent_continuity_proof(args: argparse.Namespace) -> int:
    config = ResidentAgentConfig(
        server="",
        agent_id=str(args.agent_id or "continuity-proof"),
        display_name=str(args.display_name or args.agent_id or "Continuity Proof"),
        provider_kind=str(args.provider_kind or ""),
        connection_kind=str(args.connection_kind or "live_session"),
        session_id=str(args.session_id or ""),
        endpoint="",
        auth_ref="",
        meeting_id="",
        engagement_mode="always",
        command=list(args.resident_command or []),
        timeout_seconds=int(args.timeout or 180),
        poll_interval=1.0,
        heartbeat_interval=30.0,
        cooldown=0.0,
        max_chain_depth=1,
        max_ticks=1,
    )
    result = run_live_agent_continuity_proof(
        config,
        approve_real_providers=bool(args.approve_real_providers),
        cwd=Path.cwd(),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2) if args.as_json else _format_live_agent_continuity_proof(result))
    return 0 if result.get("status") == "ok" else 1


def _run_live_agent_continuity_proof_group(args: argparse.Namespace) -> int:
    server_override = str(args.server or "") or None
    configs = load_group_configs(Path(args.config), server_override=server_override)
    result = run_live_agent_continuity_proof_batch(
        configs,
        approve_real_providers=bool(args.approve_real_providers),
        setup_error_checker=_resident_config_setup_error,
        cwd=Path.cwd(),
    )
    print(
        json.dumps(result, ensure_ascii=False, indent=2)
        if args.as_json
        else _format_live_agent_continuity_proof_group(result)
    )
    return _live_agent_continuity_proof_group_exit_code(result)


def _format_live_agent_continuity_proof(result: dict[str, object]) -> str:
    recall_state = "yes" if result.get("expected_suffix_recalled") else "no"
    if result.get("recall_match_mode"):
        recall_state = f"{recall_state} ({result.get('recall_match_mode')})"
    return (
        f"continuity proof {result.get('status') or 'unknown'}: "
        f"{result.get('provider_kind') or 'provider'} "
        f"{result.get('method') or 'provider_resume_suffix_recall'}; "
        f"session {'yes' if result.get('session_id_captured') else 'no'}; "
        f"suffix {recall_state}; "
        f"reason {result.get('reason') or 'unknown'}; "
        "limits two-turn provider-owned resume recall only; "
        "does not prove room admission or tool safety"
    )


def _format_live_agent_continuity_proof_group(result: dict[str, object]) -> str:
    status = result.get("status") or "unknown"
    return (
        f"continuity proof group {status}: "
        f"{result.get('ok_count') or 0} ok, "
        f"{result.get('failed_count') or 0} failed, "
        f"{result.get('unsupported_count') or 0} unsupported, "
        f"{result.get('approval_required_count') or 0} approval required; "
        "limits two-turn provider-owned resume recall only"
    )


def _live_agent_continuity_proof_group_exit_code(result: dict[str, object]) -> int:
    return 1 if result.get("status") in {"failed", "approval_required"} else 0


def _run_live_agent_persona_smoke(args: argparse.Namespace) -> int:
    from agentsassemble.live_agent_persona_smoke import run_live_agent_persona_smoke

    result = run_live_agent_persona_smoke(
        output_root=Path(args.output_root),
        card_path=Path(args.card),
        meeting_id=str(args.meeting_id or ""),
        character_mode=str(args.character_mode or "on"),
        context=str(args.context or ""),
    )
    if args.as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        contract = result.get("persona_artifact_contract") if isinstance(result.get("persona_artifact_contract"), dict) else {}
        print(
            f"persona smoke {result.get('status') or 'unknown'}: "
            f"{result.get('meeting_id') or 'persona-smoke'} "
            f"contract {contract.get('status') or 'unknown'}"
        )
    return 0 if result.get("status") == "ok" else 1


def _format_live_agent_preflight(report: dict[str, object]) -> str:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    agents = report.get("agents") if isinstance(report.get("agents"), list) else []
    lines = [
        f"preflight: {report.get('status') or 'unknown'}",
        f"agents: {summary.get('agents', 0)} checked, {summary.get('failed_agents', 0)} failed",
        f"checks failed: {summary.get('checks_failed', 0)}",
    ]
    for agent in agents:
        if not isinstance(agent, dict) or agent.get("status") != "failed":
            continue
        failed_checks = [
            check
            for check in agent.get("checks", [])
            if isinstance(check, dict) and check.get("status") == "failed"
        ]
        for check in failed_checks:
            lines.append(f"{agent.get('agent_id') or 'unknown'}: {check.get('id')}: {check.get('message')}")
    return "\n".join(lines)


def _readiness_probe_summary(probes: list[object]) -> str:
    labels = []
    for probe in probes:
        if not isinstance(probe, dict):
            continue
        agent_id = str(probe.get("agent_id") or "unknown")
        status = str(probe.get("status") or "unknown")
        labels.append(f"{agent_id} {status}")
    return ", ".join(labels) if labels else "none"


def _readiness_probe_group_summary(probe_groups: list[object]) -> str:
    labels = []
    for group in probe_groups:
        if not isinstance(group, dict):
            continue
        group_id = str(group.get("group_id") or "unknown")
        status = str(group.get("status") or "unknown")
        reason = str(group.get("reason") or "")
        label = f"{group_id} {status}"
        if reason:
            label = f"{label} ({reason})"
        labels.append(label)
    return ", ".join(labels) if labels else "none"


def _official_round_smoke_summary(smoke: dict[str, object]) -> str:
    group_id = str(smoke.get("group_id") or "").strip()
    label = f"{smoke.get('status') or 'unknown'} {group_id}".strip()
    return (
        f"{label} ("
        f"{smoke.get('answered_count', 0)} answered, "
        f"{smoke.get('timeout_count', 0)} timed out, "
        f"{smoke.get('skipped_count', 0)} skipped)"
    )


def _session_smoke_summary(smoke: dict[str, object]) -> str:
    group_id = str(smoke.get("group_id") or "").strip()
    label = f"{smoke.get('status') or 'unknown'} {group_id}".strip()
    lobby_probe_count = max(1, int(smoke.get("lobby_probe_count") or 1))
    expected_total = int(smoke.get("expected_reply_count") or 0) * lobby_probe_count
    soak_cycle_count = max(0, int(smoke.get("soak_cycle_count") or 0))
    soak_part = ""
    if soak_cycle_count:
        soak_expected_total = int(smoke.get("expected_reply_count") or 0) * soak_cycle_count
        soak_part = f", soak {smoke.get('soak_reply_count', 0)}/{soak_expected_total} over {soak_cycle_count} cycles"
    post_stop_part = ""
    if smoke.get("post_stop_process_status"):
        post_stop_part = f", post-stop {smoke.get('post_stop_process_status')}"
    return (
        f"{label} ("
        f"{smoke.get('reply_count', 0)}/{expected_total} replies, "
        f"post-restart {smoke.get('post_restart_reply_count', 0)}/{expected_total}, "
        f"post-recover {smoke.get('post_recover_reply_count', 0)}/{expected_total}"
        f"{soak_part}"
        f"{post_stop_part})"
    )


def _format_live_agent_health(payload: dict[str, object]) -> str:
    agents = payload.get("agents") if isinstance(payload.get("agents"), dict) else {}
    admission = payload.get("admission") if isinstance(payload.get("admission"), dict) else {}
    processes = payload.get("processes") if isinstance(payload.get("processes"), dict) else {}
    process_monitor = payload.get("process_monitor") if isinstance(payload.get("process_monitor"), dict) else {}
    connections = payload.get("connections") if isinstance(payload.get("connections"), dict) else {}
    sandbox_enforcement = payload.get("sandbox_enforcement") if isinstance(payload.get("sandbox_enforcement"), dict) else {}
    sessions = payload.get("sessions") if isinstance(payload.get("sessions"), dict) else {}
    observations = payload.get("observations") if isinstance(payload.get("observations"), dict) else {}
    session_runs = payload.get("session_runs") if isinstance(payload.get("session_runs"), dict) else {}
    session_run_monitor = payload.get("session_run_monitor") if isinstance(payload.get("session_run_monitor"), dict) else {}
    agent_counts = agents.get("counts") if isinstance(agents.get("counts"), dict) else {}
    admission_counts = admission.get("counts") if isinstance(admission.get("counts"), dict) else {}
    process_counts = processes.get("counts") if isinstance(processes.get("counts"), dict) else {}
    agent_attention = agents.get("attention") if isinstance(agents.get("attention"), list) else []
    admission_attention = admission.get("attention") if isinstance(admission.get("attention"), list) else []
    process_attention = processes.get("attention") if isinstance(processes.get("attention"), list) else []
    process_reasons = _process_reason_summary(processes.get("reasons"))
    connection_attention = connections.get("attention") if isinstance(connections.get("attention"), list) else []
    sandbox_counts = (
        sandbox_enforcement.get("counts")
        if isinstance(sandbox_enforcement.get("counts"), dict)
        else {}
    )
    sandbox_attention = (
        sandbox_enforcement.get("attention")
        if isinstance(sandbox_enforcement.get("attention"), list)
        else []
    )
    session_attention = sessions.get("attention") if isinstance(sessions.get("attention"), list) else []
    observation_attention = observations.get("attention") if isinstance(observations.get("attention"), list) else []
    session_run_attention = session_runs.get("attention") if isinstance(session_runs.get("attention"), list) else []
    lines = [
        f"status: {payload.get('status') or 'unknown'}",
        (
            f"agents: {agents.get('live', 0)} live / {agents.get('total', 0)} total "
            f"(online {agent_counts.get('online', 0)}, working {agent_counts.get('working', 0)}, "
            f"error {agent_counts.get('error', 0)}, stale {agent_counts.get('stale', 0)}, "
            f"offline {agent_counts.get('offline', 0)})"
        ),
        f"agent attention: {_attention_summary(agent_attention)}",
        (
            f"processes: {process_counts.get('running', 0)} running / {processes.get('total', 0)} total "
            f"(restarting {process_counts.get('restarting', 0)}, error {process_counts.get('error', 0)}, "
            f"unknown {process_counts.get('unknown', 0)}, stopped {process_counts.get('stopped', 0)})"
        ),
        f"process attention: {_attention_summary(process_attention)}",
    ]
    if admission:
        lines.extend(
            [
                (
                    f"admission: {admission.get('host_approved', 0)} host-approved / "
                    f"{admission.get('total', 0)} total "
                    f"(unapproved {admission.get('unapproved', 0)}, "
                    f"bound {admission_counts.get('bound_to_meeting', 0)}, "
                    f"binding conflict {admission_counts.get('binding_conflict', 0)}, "
                    f"meeting lobby {admission_counts.get('meeting_lobby_only', 0)}, "
                    f"missing meeting {admission_counts.get('meeting_missing', 0)}, "
                    f"lobby-only {admission_counts.get('lobby_only', 0)}, "
                    f"unknown {admission_counts.get('unknown', 0)})"
                ),
                f"admission attention: {_attention_summary(admission_attention)}",
            ]
        )
    process_monitor_summary = _process_monitor_summary(process_monitor)
    if process_monitor_summary:
        lines.append(f"process monitor: {process_monitor_summary}")
    if process_reasons:
        lines.append(f"process reasons: {process_reasons}")
    lines.extend(
        [
            f"connections: {connections.get('connected', 0)} connected / {connections.get('expected', 0)} expected",
            f"connection attention: {_attention_summary(connection_attention)}",
            (
                f"sandbox: advisory {sandbox_counts.get('advisory', 0)}, "
                f"codex_readonly {sandbox_counts.get('codex_readonly', 0)}, "
                f"os_sandboxed {sandbox_counts.get('os_sandboxed', 0)}, "
                f"unknown {sandbox_counts.get('unknown', 0)}"
            ),
            f"sandbox attention: {_attention_summary(sandbox_attention)}",
            f"sessions: {sessions.get('ready', 0)} ready / {sessions.get('total', 0)} total",
            f"session attention: {_attention_summary(session_attention)}",
        ]
    )
    if observations:
        lines.extend(
            [
                (
                    f"observations: {observations.get('ready_agent_count', 0)} ready agents, "
                    f"lobby behind {observations.get('lobby_behind_count', 0)}, "
                    f"live behind {observations.get('live_behind_count', 0)}, "
                    f"errors {observations.get('error_count', 0)}"
                ),
                f"observation attention: {_attention_summary(observation_attention)}",
            ]
        )
    if session_runs:
        retry_summary = _session_run_retry_summary(session_runs.get("items"))
        lines.extend(
            [
                (
                    f"session runs: {session_runs.get('active', 0)} active / {session_runs.get('total', 0)} total "
                    f"(ready {session_runs.get('ready', 0)}, retrying {session_runs.get('retrying', 0)})"
                ),
                f"session-run attention: {_attention_summary(session_run_attention)}",
            ]
        )
        if retry_summary:
            lines.append(f"session-run retries: {retry_summary}")
    monitor_summary = _session_run_monitor_summary(session_run_monitor)
    if monitor_summary:
        lines.append(f"session-run monitor: {monitor_summary}")
    return "\n".join(lines)


def _attention_summary(items: list[object]) -> str:
    cleaned = [str(item) for item in items if str(item)]
    return ", ".join(cleaned) if cleaned else "none"


def _session_run_retry_summary(value: object) -> str:
    if not isinstance(value, list):
        return ""
    labels = []
    for item in value:
        if not isinstance(item, dict):
            continue
        parts = []
        run_id = str(item.get("run_id") or "-").strip() or "-"
        failures = _safe_int(item.get("reconcile_failure_count"))
        backoff = _safe_int(item.get("reconcile_backoff_seconds"))
        next_reconcile_at = str(item.get("next_reconcile_at") or "").strip()
        if failures > 0:
            parts.append(f"retry failures {failures}")
        if backoff > 0:
            parts.append(f"retry backoff {backoff}s")
        if re.fullmatch(r"[0-9T:+.\-Z]{1,64}", next_reconcile_at):
            parts.append(f"next retry {next_reconcile_at}")
        if parts:
            labels.append(f"{run_id} {'; '.join(parts)}")
    return ", ".join(labels[:3])


def _process_reason_summary(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    labels = []
    for group_id, reason_payload in value.items():
        clean_group_id = str(group_id or "").strip()
        if not clean_group_id:
            continue
        if isinstance(reason_payload, dict):
            event_type = str(reason_payload.get("event_type") or "").strip()
            reason = str(reason_payload.get("reason") or "").strip()
        else:
            event_type = ""
            reason = str(reason_payload or "").strip()
        if not reason:
            continue
        labels.append(" ".join(part for part in (clean_group_id, event_type, reason) if part))
    return ", ".join(labels)


def _run_live_agent_operations(args: argparse.Namespace) -> int:
    if args.live_agent_operations_command == "list":
        payload = _request_json(_server_url(args.server, _live_agent_operations_path(args, include_filters=True)))
        _print_live_agent_operations_payload(payload, as_json=args.as_json)
        if args.fail_on_attention and _live_agent_operations_payload_needs_attention(payload):
            return 1
        return 0
    if args.live_agent_operations_command == "wait":
        return _run_live_agent_operations_wait(args)
    return 1


def _live_agent_operations_path(args: argparse.Namespace, *, include_filters: bool = False) -> str:
    query: dict[str, object] = {"limit": args.limit}
    if include_filters:
        operation = str(getattr(args, "operation", "") or "").strip()
        target_id = str(getattr(args, "target_id", "") or "").strip()
        status = str(getattr(args, "status", "") or "").strip()
        if operation:
            query["operation"] = operation
        if target_id:
            query["target_id"] = target_id
        if status:
            query["status"] = status
    if getattr(args, "scan_limit", None) is not None:
        query["scan_limit"] = args.scan_limit
    return f"/api/live-agent-operations?{urllib.parse.urlencode(query)}"


def _live_agent_operations_wait_path(args: argparse.Namespace) -> str:
    query: dict[str, object] = {"limit": args.limit}
    scan_limit = getattr(args, "scan_limit", None)
    if scan_limit is not None:
        query["scan_limit"] = scan_limit
        query["scan_tail"] = "1"
    return f"/api/live-agent-operations?{urllib.parse.urlencode(query)}"


def _run_live_agent_session_runs(args: argparse.Namespace) -> int:
    if args.live_agent_session_runs_command == "list":
        payload = _request_json(
            _server_url(
                args.server,
                _live_agent_session_runs_path(
                    args,
                    include_target_filters=True,
                    include_readiness=bool(getattr(args, "include_readiness", False)),
                ),
            )
        )
        _print_live_agent_session_runs_payload(payload, as_json=args.as_json)
        if args.fail_on_attention and _live_agent_session_runs_payload_needs_attention(payload):
            return 1
        return 0
    if args.live_agent_session_runs_command == "retry-now":
        _validate_live_agent_session_runs_retry_now_target(args)
        run_id = str(args.run_id or "").strip()
        path = "/api/live-agent-session-runs/retry-now"
        payload: dict[str, object] = {
            "meeting_id": str(args.meeting_id or "").strip(),
            "group_id": str(args.group_id or "").strip(),
        }
        if run_id:
            path = f"/api/live-agent-session-runs/{urllib.parse.quote(run_id, safe='')}/retry-now"
            payload = {}
        if bool(getattr(args, "approve_real_providers", False)):
            payload["approve_real_providers"] = True
        payload = _request_json(
            _server_url(
                args.server,
                path,
            ),
            method="POST",
            payload=payload,
            timeout_seconds=10.0,
        )
        _print_live_agent_session_runs_retry_now_payload(payload, as_json=args.as_json)
        return 0
    if args.live_agent_session_runs_command in {"pause", "resume", "stop"}:
        command = str(args.live_agent_session_runs_command)
        _validate_live_agent_session_runs_action_target(args, command)
        run_id = str(args.run_id or "").strip()
        path = f"/api/live-agent-session-runs/{command}"
        request_payload: dict[str, object] = {
            "meeting_id": str(args.meeting_id or "").strip(),
            "group_id": str(args.group_id or "").strip(),
        }
        if run_id:
            path = f"/api/live-agent-session-runs/{urllib.parse.quote(run_id, safe='')}/{command}"
            request_payload = {}
        payload = _request_json(
            _server_url(
                args.server,
                path,
            ),
            method="POST",
            payload=request_payload,
            timeout_seconds=10.0,
        )
        _print_live_agent_session_runs_action_payload(payload, as_json=args.as_json, command=command)
        return 0
    if args.live_agent_session_runs_command == "wait":
        return _run_live_agent_session_runs_wait(args)
    return 1


def _live_agent_session_runs_path(
    args: argparse.Namespace,
    *,
    include_target_filters: bool = False,
    include_readiness: bool = False,
) -> str:
    query: dict[str, object] = {"limit": args.limit}
    if include_target_filters:
        run_id = str(getattr(args, "run_id", "") or "").strip()
        if run_id:
            query["run_id"] = run_id
        else:
            meeting_id = str(args.meeting_id or "").strip()
            group_id = str(args.group_id or "").strip()
            if meeting_id:
                query["meeting_id"] = meeting_id
            if group_id:
                query["group_id"] = group_id
    if include_readiness:
        query["include_readiness"] = "1"
    return f"/api/live-agent-session-runs?{urllib.parse.urlencode(query)}"


def _run_live_agent_session_runs_wait(args: argparse.Namespace) -> int:
    _validate_live_agent_session_runs_wait_target(args)
    timeout_seconds = float(args.timeout)
    poll_interval = max(0.01, float(args.poll_interval))
    deadline = time.monotonic() + timeout_seconds
    attempts = 0
    last_payload: dict[str, object] | None = None
    while True:
        now = time.monotonic()
        if attempts > 0 and now >= deadline:
            _print_live_agent_session_runs_wait_result(
                _live_agent_session_runs_wait_result("timeout", args, timeout_seconds, attempts, None, last_payload),
                as_json=args.as_json,
            )
            return 1
        remaining_before_poll = max(0.01, deadline - now)
        attempts += 1
        try:
            payload = _request_json(
                _server_url(
                    args.server,
                    _live_agent_session_runs_path(
                        args,
                        include_target_filters=True,
                        include_readiness=_live_agent_session_runs_wait_requires_readiness(args),
                    ),
                ),
                timeout_seconds=remaining_before_poll,
            )
        except (TimeoutError, urllib.error.URLError) as error:
            if not _is_live_agent_wait_timeout(error):
                raise
            _print_live_agent_session_runs_wait_result(
                _live_agent_session_runs_wait_result(
                    "timeout",
                    args,
                    timeout_seconds,
                    attempts,
                    None,
                    last_payload,
                    error=str(error) or error.__class__.__name__,
                ),
                as_json=args.as_json,
            )
            return 1
        last_payload = payload
        run = _find_live_agent_session_run(
            payload,
            run_id=args.run_id,
            meeting_id=args.meeting_id,
            group_id=args.group_id,
            status=args.status,
        )
        if run is not None:
            _print_live_agent_session_runs_wait_result(
                _live_agent_session_runs_wait_result("observed", args, timeout_seconds, attempts, run, payload),
                as_json=args.as_json,
            )
            return 0
        remaining_after_poll = max(0.0, deadline - time.monotonic())
        if remaining_after_poll > 0:
            time.sleep(min(poll_interval, remaining_after_poll))


def _live_agent_session_runs_wait_result(
    status: str,
    args: argparse.Namespace,
    timeout_seconds: float,
    attempts: int,
    run: dict[str, object] | None,
    payload: dict[str, object] | None,
    *,
    error: str = "",
) -> dict[str, object]:
    result: dict[str, object] = {
        "status": status,
        "run_id": str(run.get("run_id") or "") if isinstance(run, dict) else str(args.run_id or ""),
        "meeting_id": str(args.meeting_id or ""),
        "group_id": str(args.group_id or ""),
        "wanted_status": args.status,
        "run_status": str(run.get("status") or "") if isinstance(run, dict) else "",
        "timeout_seconds": timeout_seconds,
        "attempts": attempts,
        "run": run,
    }
    if status == "timeout":
        result["runs"] = payload.get("runs") if isinstance(payload, dict) and isinstance(payload.get("runs"), list) else []
    if error:
        result["error"] = error
    return result


def _validate_live_agent_session_runs_wait_target(args: argparse.Namespace) -> None:
    if str(args.run_id or "").strip():
        return
    if str(args.meeting_id or "").strip() and str(args.group_id or "").strip():
        return
    raise ValueError("live-agent session-runs wait requires --run-id or both --meeting-id and --group-id.")


def _validate_live_agent_session_runs_retry_now_target(args: argparse.Namespace) -> None:
    _validate_live_agent_session_runs_target(args, "retry-now")


def _validate_live_agent_session_runs_action_target(args: argparse.Namespace, command: str) -> None:
    _validate_live_agent_session_runs_target(args, command)


def _validate_live_agent_session_runs_target(args: argparse.Namespace, command: str) -> None:
    if str(args.run_id or "").strip():
        return
    if str(args.meeting_id or "").strip() and str(args.group_id or "").strip():
        return
    raise ValueError(f"live-agent session-runs {command} requires --run-id or both --meeting-id and --group-id.")


def _live_agent_session_runs_wait_requires_readiness(args: argparse.Namespace) -> bool:
    return str(args.status or "").strip() == "ready"


def _find_live_agent_session_run(
    payload: dict[str, object],
    *,
    run_id: str = "",
    meeting_id: str = "",
    group_id: str = "",
    status: str = "",
) -> dict[str, object] | None:
    runs = payload.get("runs") if isinstance(payload.get("runs"), list) else []
    if run_id:
        for item in runs:
            if not isinstance(item, dict):
                continue
            if str(item.get("run_id") or "") != run_id:
                continue
            if status and str(item.get("status") or "") != status:
                continue
            if not _live_agent_session_run_readiness_allows_status(item, status=status):
                continue
            return item
        return None
    latest = _latest_live_agent_session_run_for_target(runs, meeting_id=meeting_id, group_id=group_id)
    if latest is None:
        return None
    if status and str(latest.get("status") or "") != status:
        return None
    if not _live_agent_session_run_readiness_allows_status(latest, status=status):
        return None
    return latest


def _live_agent_session_run_readiness_allows_status(run: dict[str, object], *, status: str = "") -> bool:
    if str(status or "").strip() != "ready":
        return True
    readiness = run.get("readiness") if isinstance(run.get("readiness"), dict) else {}
    return str(readiness.get("status") or "") == "ready"


def _latest_live_agent_session_run_for_target(
    runs: list[object],
    *,
    meeting_id: str = "",
    group_id: str = "",
) -> dict[str, object] | None:
    for item in reversed(runs):
        if not isinstance(item, dict):
            continue
        if meeting_id and str(item.get("meeting_id") or "") != meeting_id:
            continue
        if group_id and str(item.get("group_id") or "") != group_id:
            continue
        return item
    return None


def _run_live_agent_operations_wait(args: argparse.Namespace) -> int:
    timeout_seconds = float(args.timeout)
    poll_interval = max(0.01, float(args.poll_interval))
    deadline = time.monotonic() + timeout_seconds
    attempts = 0
    last_payload: dict[str, object] | None = None
    after_id_seen = not bool(args.after_id)
    ignored_operation_ids: set[str] = set()
    while True:
        now = time.monotonic()
        if attempts > 0 and now >= deadline:
            _print_live_agent_operations_wait_result(
                _live_agent_operations_wait_result("timeout", args, timeout_seconds, attempts, None, last_payload),
                as_json=args.as_json,
            )
            return 1
        remaining_before_poll = max(0.01, deadline - now)
        attempts += 1
        try:
            payload = _request_json(
                _server_url(args.server, _live_agent_operations_wait_path(args)),
                timeout_seconds=remaining_before_poll,
            )
        except (TimeoutError, urllib.error.URLError) as error:
            if not _is_live_agent_wait_timeout(error):
                raise
            _print_live_agent_operations_wait_result(
                _live_agent_operations_wait_result(
                    "timeout",
                    args,
                    timeout_seconds,
                    attempts,
                    None,
                    last_payload,
                    error=str(error) or error.__class__.__name__,
                ),
                as_json=args.as_json,
            )
            return 1
        last_payload = payload
        after_id_in_payload = bool(args.after_id) and _live_agent_operation_id_present(payload, args.after_id)
        if not after_id_seen and not after_id_in_payload:
            operation = None
        else:
            operation = _find_live_agent_operation(
                payload,
                args.operation,
                args.target_id,
                args.status,
                args.after_id if after_id_in_payload else "",
                ignored_operation_ids=ignored_operation_ids,
            )
        if after_id_in_payload:
            after_id_seen = True
            ignored_operation_ids.update(_live_agent_operation_ids_through(payload, args.after_id))
        if operation is not None:
            _print_live_agent_operations_wait_result(
                _live_agent_operations_wait_result("observed", args, timeout_seconds, attempts, operation, payload),
                as_json=args.as_json,
            )
            return 0
        remaining_after_poll = max(0.0, deadline - time.monotonic())
        if remaining_after_poll > 0:
            time.sleep(min(poll_interval, remaining_after_poll))


def _live_agent_operations_wait_result(
    status: str,
    args: argparse.Namespace,
    timeout_seconds: float,
    attempts: int,
    operation: dict[str, object] | None,
    payload: dict[str, object] | None,
    *,
    error: str = "",
) -> dict[str, object]:
    result: dict[str, object] = {
        "status": status,
        "operation_name": args.operation,
        "target_id": args.target_id,
        "operation_status": args.status,
        "after_id": args.after_id,
        "timeout_seconds": timeout_seconds,
        "attempts": attempts,
        "operation": operation,
    }
    if status == "timeout":
        operations = payload.get("operations") if isinstance(payload, dict) and isinstance(payload.get("operations"), list) else []
        result["operations"] = operations[-max(1, int(args.limit)) :]
        if isinstance(payload, dict):
            result["truncated"] = payload.get("truncated") is True
            if "scan_limit" in payload:
                result["scan_limit"] = payload.get("scan_limit")
            if "scanned_operation_count" in payload:
                result["scanned_operation_count"] = payload.get("scanned_operation_count")
    if error:
        result["error"] = error
    return result


def _find_live_agent_operation(
    payload: dict[str, object],
    operation_name: str,
    target_id: str = "",
    status: str = "",
    after_id: str = "",
    ignored_operation_ids: set[str] | None = None,
) -> dict[str, object] | None:
    operations = payload.get("operations") if isinstance(payload.get("operations"), list) else []
    start_index = 0
    if after_id:
        for index, item in enumerate(operations):
            if isinstance(item, dict) and str(item.get("id") or "") == after_id:
                start_index = index + 1
                break
        else:
            return None
    for item in operations[start_index:]:
        if not isinstance(item, dict):
            continue
        if ignored_operation_ids and str(item.get("id") or "") in ignored_operation_ids:
            continue
        if str(item.get("operation") or "") != operation_name:
            continue
        if target_id and str(item.get("target_id") or "") != target_id:
            continue
        if status and str(item.get("status") or "") != status:
            continue
        return item
    return None


def _live_agent_operation_id_present(payload: dict[str, object], operation_id: str) -> bool:
    operations = payload.get("operations") if isinstance(payload.get("operations"), list) else []
    return any(isinstance(item, dict) and str(item.get("id") or "") == operation_id for item in operations)


def _live_agent_operation_ids_through(payload: dict[str, object], operation_id: str) -> set[str]:
    operations = payload.get("operations") if isinstance(payload.get("operations"), list) else []
    operation_ids: set[str] = set()
    for item in operations:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id") or "")
        if item_id:
            operation_ids.add(item_id)
        if item_id == operation_id:
            break
    return operation_ids


def _print_live_agent_operations_wait_result(result: dict[str, object], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if result.get("status") == "observed":
        operation = result.get("operation") if isinstance(result.get("operation"), dict) else {}
        print(f"Observed live-agent operation: {_format_live_agent_operation(operation)}")
        return
    parts = [str(result.get("operation_name") or "unknown")]
    if result.get("target_id"):
        parts.append(f"target {result.get('target_id')}")
    if result.get("operation_status"):
        parts.append(f"status {result.get('operation_status')}")
    if result.get("after_id"):
        parts.append(f"after {result.get('after_id')}")
    timeout_seconds = _safe_float(result.get("timeout_seconds"))
    print(f"Timed out waiting for live-agent operation {' '.join(parts)} after {timeout_seconds:.1f}s")
    operations = result.get("operations") if isinstance(result.get("operations"), list) else []
    last_operation = next((item for item in reversed(operations) if isinstance(item, dict)), None)
    if last_operation is not None:
        print(f"last operation: {_format_live_agent_operation(last_operation)}")
    scan_notice = _format_live_agent_operation_scan_notice(result)
    if scan_notice:
        print(scan_notice)


def _print_live_agent_operations_payload(payload: dict[str, object], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    operations = payload.get("operations") if isinstance(payload.get("operations"), list) else []
    if not operations:
        print("no live-agent operations")
    else:
        for item in operations:
            if isinstance(item, dict):
                print(_format_live_agent_operation(item))
    scan_notice = _format_live_agent_operation_scan_notice(payload)
    if scan_notice:
        print(scan_notice)


def _print_live_agent_session_runs_payload(payload: dict[str, object], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    runs = payload.get("runs") if isinstance(payload.get("runs"), list) else []
    if not runs:
        print("no live-agent session runs")
        return
    for item in runs:
        if isinstance(item, dict):
            print(_format_live_agent_session_run(item))


def _print_live_agent_session_runs_retry_now_payload(payload: dict[str, object], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    run = payload.get("session_run") if isinstance(payload.get("session_run"), dict) else {}
    suffix = f": {_format_live_agent_session_run(run)}" if run else ""
    status = str(payload.get("status") or "scheduled")
    verb = {"reconciled": "Retried", "skipped": "Skipped"}.get(status, "Scheduled")
    print(f"{verb} live-agent session run retry{suffix}")


def _print_live_agent_session_runs_action_payload(
    payload: dict[str, object],
    *,
    as_json: bool,
    command: str,
) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    run = payload.get("session_run") if isinstance(payload.get("session_run"), dict) else {}
    suffix = f": {_format_live_agent_session_run(run)}" if run else ""
    verb = {"pause": "Paused", "resume": "Resumed", "stop": "Stopped"}.get(command, command.title())
    print(f"{verb} live-agent session run{suffix}")


def _print_live_agent_session_runs_wait_result(result: dict[str, object], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    run_id = str(result.get("run_id") or "").strip()
    meeting_id = str(result.get("meeting_id") or "").strip()
    group_id = str(result.get("group_id") or "").strip()
    target_label = f"session run {run_id}" if run_id else f"session run for {meeting_id or '-'} {group_id or '-'}"
    wanted_status = str(result.get("wanted_status") or "unknown")
    timeout_seconds = _safe_float(result.get("timeout_seconds"))
    run = result.get("run") if isinstance(result.get("run"), dict) else None
    if result.get("status") == "observed":
        suffix = f": {_format_live_agent_session_run(run)}" if run is not None else ""
        print(f"Observed live-agent {target_label} status {wanted_status}{suffix}")
        return
    print(f"Timed out waiting for live-agent {target_label} status {wanted_status} after {timeout_seconds:.1f}s")
    runs = result.get("runs") if isinstance(result.get("runs"), list) else []
    last_run = None
    if run_id:
        last_run = next((item for item in reversed(runs) if isinstance(item, dict) and str(item.get("run_id") or "") == run_id), None)
    if last_run is None and (meeting_id or group_id):
        last_run = _latest_live_agent_session_run_for_target(runs, meeting_id=meeting_id, group_id=group_id)
    if last_run is None:
        last_run = next((item for item in reversed(runs) if isinstance(item, dict)), None)
    if last_run is not None:
        print(f"last run: {_format_live_agent_session_run(last_run)}")


def _format_live_agent_session_run(run: dict[str, object]) -> str:
    run_id = str(run.get("run_id") or "-")
    action = str(run.get("action") or "unknown")
    status = str(run.get("status") or "unknown")
    meeting_id = str(run.get("meeting_id") or "-")
    group_id = str(run.get("group_id") or "-")
    activity = "active" if run.get("active") is True else "inactive"
    phase = str(run.get("phase") or "").strip()
    reconcile_count = _safe_int(run.get("reconcile_count"))
    suffix_parts = []
    if phase:
        suffix_parts.append(f"phase={phase}")
    if reconcile_count:
        suffix_parts.append(f"reconcile_count={reconcile_count}")
    reconcile_failure_count = _safe_int(run.get("reconcile_failure_count"))
    if reconcile_failure_count:
        suffix_parts.append(f"reconcile_failures={reconcile_failure_count}")
    reconcile_backoff_seconds = _safe_int(run.get("reconcile_backoff_seconds"))
    if reconcile_backoff_seconds:
        suffix_parts.append(f"reconcile_backoff={reconcile_backoff_seconds}s")
    next_reconcile_at = str(run.get("next_reconcile_at") or "").strip()
    if next_reconcile_at:
        suffix_parts.append(f"next_reconcile={next_reconcile_at}")
    paused_status = str(run.get("paused_status") or "").strip()
    if paused_status:
        suffix_parts.append(f"paused_from={paused_status}")
    readiness = run.get("readiness") if isinstance(run.get("readiness"), dict) else {}
    readiness_status = str(readiness.get("status") or "").strip()
    if readiness_status:
        suffix_parts.append(f"readiness={readiness_status}")
    readiness_expected = _safe_int(readiness.get("expected"))
    readiness_connected = _safe_int(readiness.get("connected"))
    if readiness_expected > 0:
        suffix_parts.append(f"current_connected={max(0, readiness_connected)}/{readiness_expected}")
    suffix = f" · {' · '.join(suffix_parts)}" if suffix_parts else ""
    return f"{run_id} {action} {status} {meeting_id} {group_id} {activity}{suffix}"


def _format_live_agent_operation(operation: dict[str, object]) -> str:
    timestamp = str(operation.get("timestamp") or "-")
    operation_name = str(operation.get("operation") or "unknown")
    status = str(operation.get("status") or "unknown")
    target_id = str(operation.get("target_id") or "-")
    summary = str(operation.get("summary") or operation.get("error") or "").strip()
    details = _format_live_agent_operation_details(operation.get("details"), operation_name=operation_name)
    suffix_parts = [part for part in (summary, details) if part]
    suffix = f" · {' · '.join(suffix_parts)}" if suffix_parts else ""
    return f"{timestamp} {operation_name} {status} {target_id}{suffix}"


def _format_live_agent_operation_scan_notice(payload: dict[str, object]) -> str:
    if payload.get("truncated") is not True:
        return ""
    scanned = _safe_int(payload.get("scanned_operation_count")) or _safe_int(payload.get("scan_limit"))
    if scanned <= 0:
        return "searched bounded operation history; older matches may exist"
    return f"searched recent {scanned} live-agent operations; older matches may exist"


def _live_agent_operations_payload_needs_attention(payload: dict[str, object]) -> bool:
    operations = payload.get("operations") if isinstance(payload.get("operations"), list) else []
    for item in operations:
        if isinstance(item, dict) and str(item.get("status") or "").strip() != "success":
            return True
    return False


def _live_agent_session_runs_payload_needs_attention(payload: dict[str, object]) -> bool:
    runs = payload.get("runs") if isinstance(payload.get("runs"), list) else []
    for item in runs:
        if isinstance(item, dict) and _live_agent_session_run_needs_attention(item):
            return True
    return False


def _live_agent_session_run_needs_attention(run: dict[str, object]) -> bool:
    status = str(run.get("status") or "").strip()
    if status in {"failed", "error"}:
        return True
    active = run.get("active") is True
    if active and status != "ready":
        return True
    readiness = run.get("readiness") if isinstance(run.get("readiness"), dict) else {}
    readiness_status = str(readiness.get("status") or "").strip()
    return bool(active and readiness_status and readiness_status != "ready")


def _format_live_agent_operation_details(value: object, *, operation_name: str = "") -> str:
    if not isinstance(value, dict):
        return ""
    labels = []
    detail_limit = _live_agent_operation_detail_limit(operation_name)
    for key, raw_detail in _ordered_live_agent_operation_details(value, operation_name=operation_name):
        clean_key = str(key or "").strip()
        clean_value = _format_live_agent_operation_detail_value(raw_detail)
        if clean_key and clean_value:
            labels.append(f"{clean_key}={clean_value}")
        if len(labels) >= detail_limit:
            break
    return "; ".join(labels)


def _ordered_live_agent_operation_details(
    value: dict[str, object],
    *,
    operation_name: str = "",
) -> list[tuple[str, object]]:
    priority = _live_agent_operation_detail_priority(operation_name)
    seen = set()
    ordered: list[tuple[str, object]] = []
    for key in priority:
        if key in value:
            ordered.append((key, value[key]))
            seen.add(key)
    ordered.extend((key, raw_detail) for key, raw_detail in value.items() if key not in seen)
    return ordered


def _live_agent_operation_detail_priority(operation_name: str) -> list[str]:
    if operation_name == "session.smoke":
        return [
            "result_status",
            "finalization_status",
            "finalization_official_event_count",
            "return_packet_event_count",
            "artifact_status",
            "reply_count",
            "post_restart_reply_count",
            "post_recover_reply_count",
            "soak_cycle_count",
            "soak_reply_count",
            "soak_check_statuses",
            "post_stop_process_status",
        ]
    if operation_name == "session.real_smoke":
        return [
            "result_status",
            "start_status",
            "connected_agent_count",
            "expected_agent_count",
            "reply_probe_status",
            "reply_probe_ok_count",
            "reply_probe_count",
            "stop_status",
            "post_stop_process_status",
        ]
    if operation_name == "readiness.check":
        return [
            "result_status",
            "health_process_reasons",
            "health_process_attention",
            "health_observation_attention",
            "health_observation_lobby_behind_count",
            "health_observation_live_behind_count",
            "health_observation_error_count",
            "health_shared_memory_attention",
            "health_session_run_attention",
            "health_session_run_retrying",
            "health_session_run_monitor_attention",
            "health_session_attention",
            "health_connection_attention",
            "health_agent_attention",
            "session_smoke_reply_count",
            "session_smoke_finalization_status",
            "session_smoke_finalization_official_event_count",
            "session_smoke_return_packet_event_count",
            "session_smoke_artifact_status",
            "session_smoke_post_restart_reply_count",
            "session_smoke_post_recover_reply_count",
            "session_smoke_soak_cycle_count",
            "session_smoke_soak_reply_count",
            "session_smoke_soak_check_statuses",
            "session_smoke_post_stop_process_status",
            "probe_statuses",
        ]
    if operation_name in {"session.start", "session.ensure", "session.resume", "session.restart", "session.recover"}:
        return [
            "ensure_action",
            "result_status",
            "connected_agent_count",
            "reply_probe_status",
            "reply_probe_statuses",
            "auto_rounds_status",
            "auto_rounds_reason",
            "finalization_status",
            "finalization_reason",
            "finalization_official_event_count",
            "auto_rounds_answered_round_count",
            "auto_rounds_round_count",
        ]
    if operation_name == "discovery.run":
        return [
            "result_status",
            "approved_count",
            "approved_agent_ids",
            "approved_cli_count",
            "excluded_agent_count",
            "excluded_cli_count",
            "unmatched_approval_count",
            "agents",
            "discovered",
            "approval_required",
        ]
    if operation_name == "official_turn.rounds":
        return [
            "finalization_status",
            "finalization_reason",
            "finalization_official_event_count",
            "round_count",
            "answered_round_count",
            "completed_round_count",
            "timeout_round_count",
            "skipped_round_count",
            "stopped_round_count",
            "statuses",
        ]
    if operation_name == "review.checkpoint":
        return [
            "result_status",
            "checkpoint_id",
            "answered_count",
            "timeout_count",
            "skipped_count",
            "agent_ids",
            "statuses",
            "reply_event_ids",
        ]
    return []


def _live_agent_operation_detail_limit(operation_name: str) -> int:
    if operation_name == "session.real_smoke":
        return 9
    if operation_name == "session.smoke":
        return 8
    if operation_name == "readiness.check":
        return 12
    if operation_name == "session.ensure":
        return 11
    if operation_name in {"session.start", "session.resume", "session.restart", "session.recover"}:
        return 10
    if operation_name == "official_turn.rounds":
        return 8
    if operation_name == "review.checkpoint":
        return 8
    if operation_name == "discovery.run":
        return 10
    return 7


def _format_live_agent_operation_detail_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        items = []
        for item in value[:10]:
            if isinstance(item, bool):
                items.append("true" if item else "false")
            elif isinstance(item, (int, float)) and not isinstance(item, bool):
                items.append(str(item))
            elif isinstance(item, str) and item.strip():
                items.append(item.strip())
        return ",".join(items)
    return ""


def _process_monitor_summary(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    monitor_fields = {"running", "interval_seconds", "last_tick_at", "last_status", "last_group_count", "last_error_type"}
    if not any(field in value for field in monitor_fields):
        return ""
    running = "true" if value.get("running") is True else "false"
    parts = [f"running {running}"]
    interval_seconds = _safe_nonnegative_float(value.get("interval_seconds"))
    if interval_seconds > 0:
        parts.append(f"interval {_format_seconds(interval_seconds)}")
    last_status = str(value.get("last_status") or "").strip()
    if last_status:
        parts.append(f"last {last_status}")
    parts.append(f"groups {_safe_int(value.get('last_group_count'))}")
    last_tick_at = str(value.get("last_tick_at") or "").strip()
    if last_tick_at:
        parts.append(f"last tick {last_tick_at}")
    last_error_type = str(value.get("last_error_type") or "").strip()
    if last_error_type:
        parts.append(f"error {last_error_type}")
    return "; ".join(parts)


def _session_run_monitor_summary(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    monitor_fields = {"running", "interval_seconds", "last_tick_at", "last_status", "last_result_count", "last_error_type"}
    if not any(field in value for field in monitor_fields):
        return ""
    running = "true" if value.get("running") is True else "false"
    parts = [f"running {running}"]
    interval_seconds = _safe_nonnegative_float(value.get("interval_seconds"))
    if interval_seconds > 0:
        parts.append(f"interval {_format_seconds(interval_seconds)}")
    last_status = str(value.get("last_status") or "").strip()
    if last_status:
        parts.append(f"last {last_status}")
    last_result_count = _safe_int(value.get("last_result_count"))
    parts.append(f"results {last_result_count}")
    last_tick_at = str(value.get("last_tick_at") or "").strip()
    if last_tick_at:
        parts.append(f"last tick {last_tick_at}")
    last_error_type = str(value.get("last_error_type") or "").strip()
    if last_error_type:
        parts.append(f"error {last_error_type}")
    return "; ".join(parts)


def _safe_nonnegative_float(value: object) -> float:
    if isinstance(value, bool):
        return 0.0
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) and number >= 0 else 0.0


def _format_seconds(value: float) -> str:
    if value.is_integer():
        return f"{int(value)}s"
    return f"{value:g}s"


def _safe_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _safe_float(value: object) -> float:
    if isinstance(value, bool):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _run_live_agent_delegate(args: argparse.Namespace) -> int:
    payload = {
        "agent_id": args.agent_id,
        "display_name": args.display_name,
        "provider_kind": args.provider_kind,
        "connection_kind": args.connection_kind,
        "session_id": args.session_id,
        "endpoint": args.endpoint,
        "meeting_id": args.meeting_id,
        "engagement_mode": args.engagement_mode,
        "capabilities": ["room_chat", "mentions"],
    }
    _request_json(_server_url(args.server, "/api/live-agents"), method="POST", payload=payload)
    agent_id = urllib.parse.quote(args.agent_id, safe="")
    _request_json(
        _server_url(args.server, f"/api/live-agents/{agent_id}/heartbeat"),
        method="POST",
        payload={"status": "working"},
    )
    room = _request_json(_server_url(args.server, f"/api/live-agents/{agent_id}/room"))
    try:
        reply = _run_delegate_command(args.delegate_command, _delegate_prompt(args, room), timeout_seconds=args.timeout).strip()
        if not reply:
            raise ValueError("Delegate command returned an empty reply.")
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        _heartbeat_delegate_error(args, agent_id, error)
        raise
    lobby_payload = {"message": reply, "kind": "message"}
    source_event = _delegate_source_event(args, room)
    if source_event is not None:
        lobby_payload["source_event_id"] = str(source_event.get("id") or "")
        lobby_payload["auto_chain_depth"] = _delegate_chain_depth(source_event) + 1
    response = _request_json(
        _server_url(args.server, f"/api/live-agents/{agent_id}/lobby"),
        method="POST",
        payload=lobby_payload,
    )
    _request_json(
        _server_url(args.server, f"/api/live-agents/{agent_id}/heartbeat"),
        method="POST",
        payload={"status": "online"},
    )
    event = response.get("event", {}) if isinstance(response.get("event"), dict) else {}
    print(f"Posted {event.get('id') or 'lobby message'}")
    return 0


def _run_live_agent_wait_room_event(args: argparse.Namespace) -> int:
    deadline = time.monotonic() + float(args.timeout)
    last_room: dict[str, object] = {}
    while True:
        agent_id = urllib.parse.quote(args.agent_id, safe="")
        room = _request_json(_server_url(args.server, f"/api/live-agents/{agent_id}/room"))
        last_room = room
        candidate = _wait_room_event_candidate(args, room)
        if candidate is not None:
            payload = _wait_room_event_payload(args, room, candidate)
            if args.as_json:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                print(_format_wait_room_event(payload))
            return 0
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            payload = _wait_room_timeout_payload(args, last_room)
            if args.as_json:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                cursor = payload.get("last_observed_event_id") or "(none)"
                print(f"no new room event after {cursor}")
            return 1
        sleep_interval = live_agent_poll_sleep_seconds(args.poll_interval)
        time.sleep(min(sleep_interval, remaining))


def _wait_room_event_candidate(args: argparse.Namespace, room: dict[str, object]) -> dict[str, object] | None:
    agent = room.get("agent") if isinstance(room.get("agent"), dict) else {}
    events = room.get("lobby_events") if isinstance(room.get("lobby_events"), list) else []
    cursor = str(args.after_event_id or agent.get("last_observed_event_id") or "").strip()
    display_name = str(agent.get("display_name") or "").strip()
    for event in _events_after_id(events, cursor):
        if not isinstance(event, dict):
            continue
        if _wait_room_self_event(args.agent_id, display_name, event):
            continue
        if _delegate_chain_depth(event) > int(args.max_chain_depth):
            continue
        if not str(event.get("message") or "").strip():
            continue
        if not str(event.get("id") or "").strip():
            continue
        return event
    return None


def _events_after_id(events: list[object], event_id: str) -> list[object]:
    if not event_id:
        return events
    for index, event in enumerate(events):
        if isinstance(event, dict) and str(event.get("id") or "") == event_id:
            return events[index + 1 :]
    return events


def _run_live_agent_read_since(args: argparse.Namespace) -> int:
    agent_id = urllib.parse.quote(args.agent_id, safe="")
    room = _request_json(_server_url(args.server, f"/api/live-agents/{agent_id}/room"))
    payload = _live_agent_read_since_payload(args, room)
    if args.as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        lobby_cursor = payload.get("last_observed_event_id") or "(start)"
        live_cursor = payload.get("last_observed_live_event_id") or "(start)"
        dm_cursor = payload.get("last_observed_dm_event_id") or "(start)"
        print(
            "read-since "
            f"lobby {len(payload.get('lobby_events') if isinstance(payload.get('lobby_events'), list) else [])} "
            f"after {lobby_cursor}; "
            f"official {len(payload.get('live_events') if isinstance(payload.get('live_events'), list) else [])} "
            f"after {live_cursor}; "
            f"dm {len(payload.get('dm_events') if isinstance(payload.get('dm_events'), list) else [])} "
            f"after {dm_cursor}"
        )
    return 0


def _live_agent_read_since_payload(args: argparse.Namespace, room: dict[str, object]) -> dict[str, object]:
    agent = room.get("agent") if isinstance(room.get("agent"), dict) else {}
    lobby_cursor = str(args.after_event_id or agent.get("last_observed_event_id") or "").strip()
    live_cursor = str(args.after_live_event_id or agent.get("last_observed_live_event_id") or "").strip()
    dm_cursor = str(args.after_dm_event_id or agent.get("last_observed_dm_event_id") or "").strip()
    lobby_events = [event for event in _events_after_id(_room_event_list(room, "lobby_events"), lobby_cursor) if isinstance(event, dict)]
    live_events = [event for event in _events_after_id(_room_event_list(room, "live_events"), live_cursor) if isinstance(event, dict)]
    dm_events = [event for event in _events_after_id(_room_event_list(room, "dm_events"), dm_cursor) if isinstance(event, dict)]
    next_lobby_cursor = _latest_observed_event_id(lobby_events, lobby_cursor)
    next_live_cursor = _latest_observed_event_id(live_events, live_cursor)
    next_dm_cursor = _latest_observed_event_id(dm_events, dm_cursor)
    meeting_id = str(room.get("meeting_id") or agent.get("meeting_id") or "").strip()
    return {
        "status": "ok",
        "agent_id": args.agent_id,
        "meeting_id": meeting_id,
        "last_observed_event_id": lobby_cursor,
        "last_observed_live_event_id": live_cursor,
        "last_observed_dm_event_id": dm_cursor,
        "next_last_observed_event_id": next_lobby_cursor,
        "next_last_observed_live_event_id": next_live_cursor,
        "next_last_observed_dm_event_id": next_dm_cursor,
        "lobby_events": lobby_events,
        "live_events": live_events,
        "dm_events": dm_events,
        "ack_command": _live_agent_read_since_ack_command(args, next_lobby_cursor, next_live_cursor, next_dm_cursor),
        "room": _wait_room_context(room, meeting_id=meeting_id),
    }


def _room_event_list(room: dict[str, object], key: str) -> list[object]:
    events = room.get(key)
    return events if isinstance(events, list) else []


def _live_agent_read_since_ack_command(args: argparse.Namespace, lobby_cursor: str, live_cursor: str, dm_cursor: str) -> list[str]:
    return [
        "python3",
        "-m",
        "agentsassemble.cli",
        "live-agent",
        "heartbeat",
        "--server",
        str(args.server),
        "--agent-id",
        str(args.agent_id),
        "--status",
        "online",
        "--last-error=",
        f"--last-observed-event-id={lobby_cursor}",
        f"--last-observed-live-event-id={live_cursor}",
        f"--last-observed-dm-event-id={dm_cursor}",
        "--json",
    ]


def _latest_observed_event_id(events: object, fallback: str) -> str:
    if not isinstance(events, list):
        return fallback
    for event in reversed(events):
        if not isinstance(event, dict):
            continue
        event_id = str(event.get("id") or "").strip()
        if event_id:
            return event_id
    return fallback


def _wait_room_self_event(agent_id: str, display_name: str, event: dict[str, object]) -> bool:
    actor_id = str(event.get("actor_id") or "")
    if actor_id:
        return actor_id == agent_id
    return bool(display_name) and str(event.get("name") or "") == display_name


def _wait_room_event_payload(
    args: argparse.Namespace,
    room: dict[str, object],
    event: dict[str, object],
) -> dict[str, object]:
    event_id = str(event.get("id") or "")
    auto_chain_depth = _delegate_chain_depth(event) + 1
    flow_id = str(event.get("flow_id") or "").strip()
    flow_meeting_id = str(event.get("flow_meeting_id") or room.get("meeting_id") or "").strip()
    reply_command = [
        "python3",
        "-m",
        "agentsassemble.cli",
        "live-agent",
        "say",
        "--server",
        str(args.server),
        "--agent-id",
        str(args.agent_id),
        "--source-event-id",
        event_id,
        "--auto-chain-depth",
        str(auto_chain_depth),
    ]
    if flow_id:
        reply_command.extend(["--flow-id", flow_id])
    if flow_meeting_id:
        reply_command.extend(["--flow-meeting-id", flow_meeting_id])
    reply_command.extend(["--", "<reply>"])
    return {
        "status": "event",
        "agent_id": args.agent_id,
        "source_event_id": event_id,
        "auto_chain_depth": auto_chain_depth,
        "event": event,
        "reply_command": reply_command,
        "room": _wait_room_context(room, meeting_id=str(room.get("meeting_id") or "")),
    }


def _wait_room_timeout_payload(args: argparse.Namespace, room: dict[str, object]) -> dict[str, object]:
    agent = room.get("agent") if isinstance(room.get("agent"), dict) else {}
    cursor = str(args.after_event_id or agent.get("last_observed_event_id") or "").strip()
    return {
        "status": "timeout",
        "agent_id": args.agent_id,
        "timeout_seconds": float(args.timeout),
        "last_observed_event_id": _latest_observed_event_id(room.get("lobby_events"), cursor),
    }


def _format_wait_room_event(payload: dict[str, object]) -> str:
    event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
    event_id = str(event.get("id") or "room event")
    name = str(event.get("name") or event.get("actor_id") or "participant")
    message = str(event.get("message") or "").strip()
    return f"{event_id} {name}: {message}"


def _run_live_agent_dm_reply(args: argparse.Namespace) -> int:
    agent_id = urllib.parse.quote(args.agent_id, safe="")
    response = _request_json(
        _server_url(args.server, f"/api/live-agents/{agent_id}/dm-reply"),
        method="POST",
        payload={
            "source_event_id": args.source_event_id,
            "message": " ".join(args.message),
        },
    )
    event = response.get("event", {}) if isinstance(response.get("event"), dict) else {}
    if args.as_json:
        print(json.dumps(response, ensure_ascii=False, indent=2))
    else:
        print(f"Answered DM {event.get('id') or args.source_event_id}")
    return 0


def _run_live_agent_answer_turn(args: argparse.Namespace) -> int:
    agent_id = urllib.parse.quote(args.agent_id, safe="")
    response = _request_json(
        _server_url(args.server, f"/api/live-agents/{agent_id}/official-turn"),
        method="POST",
        payload={
            "meeting_id": args.meeting_id,
            "source_event_id": args.source_event_id,
            "content": " ".join(args.message),
        },
    )
    event = response.get("event", {}) if isinstance(response.get("event"), dict) else {}
    if args.as_json:
        print(json.dumps(response, ensure_ascii=False, indent=2))
    else:
        print(f"Answered official turn {event.get('id') or args.source_event_id}")
    return 0


def _run_live_agent_wait_turn_request(args: argparse.Namespace) -> int:
    deadline = time.monotonic() + float(args.timeout)
    last_room: dict[str, object] = {}
    while True:
        agent_id = urllib.parse.quote(args.agent_id, safe="")
        room = _request_json(_server_url(args.server, f"/api/live-agents/{agent_id}/room"))
        last_room = room
        candidate = _wait_turn_request_candidate(args, room)
        if candidate is not None:
            payload = (
                _wait_persona_blocked_official_turn_payload(args, room, candidate)
                if _wait_agent_persona_blocks_official_turn(room)
                else _wait_turn_request_payload(args, room, candidate)
            )
            if args.as_json:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                print(_format_wait_persona_block(payload) if payload.get("action") == "persona_blocks_official_turn" else _format_wait_turn_request(payload))
            return 0
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            payload = _wait_turn_request_timeout_payload(args, last_room)
            if args.as_json:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                cursor = payload.get("last_observed_live_event_id") or "(none)"
                print(f"no new official turn request after {cursor}")
            return 1
        sleep_interval = live_agent_poll_sleep_seconds(args.poll_interval)
        time.sleep(min(sleep_interval, remaining))


def _wait_turn_request_candidate(args: argparse.Namespace, room: dict[str, object]) -> dict[str, object] | None:
    agent = room.get("agent") if isinstance(room.get("agent"), dict) else {}
    events = room.get("live_events") if isinstance(room.get("live_events"), list) else []
    typed_events = [event for event in events if isinstance(event, dict)]
    requested_cursor = getattr(args, "after_live_event_id", None)
    if requested_cursor is None:
        requested_cursor = getattr(args, "after_event_id", "")
    cursor = str(requested_cursor or agent.get("last_observed_live_event_id") or "").strip()
    return official_turn_request_candidate(typed_events, args.agent_id, cursor)


PERSONA_OFFICIAL_TURN_BLOCK_REASON = "persona_context_blocked_official_turn"


def _wait_agent_persona_blocks_official_turn(room: dict[str, object]) -> bool:
    agent = room.get("agent") if isinstance(room.get("agent"), dict) else {}
    if not agent:
        return False
    mode = str(agent.get("character_mode") or "").strip()
    if mode == "off":
        return False
    has_persona = bool(str(agent.get("persona_card_id") or agent.get("persona_id") or "").strip())
    if not has_persona:
        return False
    return str(agent.get("connection_kind") or "").strip() in {"self_service", "live_session", "terminal_session", "remote_bridge"}


def _wait_turn_request_payload(
    args: argparse.Namespace,
    room: dict[str, object],
    event: dict[str, object],
) -> dict[str, object]:
    event_id = str(event.get("id") or "")
    meeting_id = _wait_turn_request_meeting_id(room, event)
    return {
        "status": "event",
        "agent_id": args.agent_id,
        "meeting_id": meeting_id,
        "source_event_id": event_id,
        "event": event,
        "reply_command": [
            "python3",
            "-m",
            "agentsassemble.cli",
            "live-agent",
            "official-reply",
            "--server",
            str(args.server),
            "--agent-id",
            str(args.agent_id),
            "--meeting-id",
            meeting_id,
            "--source-event-id",
            event_id,
            "--",
            "<reply>",
        ],
        "room": _wait_room_context(room, meeting_id=str(room.get("meeting_id") or meeting_id)),
    }


def _wait_persona_blocked_official_turn_payload(
    args: argparse.Namespace,
    room: dict[str, object],
    event: dict[str, object],
) -> dict[str, object]:
    event_id = str(event.get("id") or "")
    meeting_id = _wait_turn_request_meeting_id(room, event)
    return {
        "status": "event",
        "action": "persona_blocks_official_turn",
        "agent_id": args.agent_id,
        "meeting_id": meeting_id,
        "source_event_id": event_id,
        "reason": PERSONA_OFFICIAL_TURN_BLOCK_REASON,
        "attention": [PERSONA_OFFICIAL_TURN_BLOCK_REASON],
        "event": event,
        "ack_command": [
            "python3",
            "-m",
            "agentsassemble.cli",
            "live-agent",
            "heartbeat",
            "--server",
            str(args.server),
            "--agent-id",
            str(args.agent_id),
            "--status",
            "online",
            "--last-error=",
            f"--last-attention={PERSONA_OFFICIAL_TURN_BLOCK_REASON}",
            f"--last-observed-live-event-id={event_id}",
            "--json",
        ],
        "room": _wait_room_context(room, meeting_id=str(room.get("meeting_id") or meeting_id)),
    }


def _format_wait_persona_block(payload: dict[str, object]) -> str:
    event_id = str(payload.get("source_event_id") or "official turn request")
    return f"persona_blocks_official_turn {event_id}: {PERSONA_OFFICIAL_TURN_BLOCK_REASON}"


def _wait_room_context(room: dict[str, object], *, meeting_id: str) -> dict[str, object]:
    context: dict[str, object] = {
        "meeting_id": meeting_id,
        "lobby_event_count": len(room.get("lobby_events") if isinstance(room.get("lobby_events"), list) else []),
        "live_event_count": len(room.get("live_events") if isinstance(room.get("live_events"), list) else []),
        "dm_event_count": len(room.get("dm_events") if isinstance(room.get("dm_events"), list) else []),
    }
    shared_memory = _wait_shared_memory(room)
    if shared_memory:
        context["shared_memory"] = shared_memory
    return context


def _wait_shared_memory(room: dict[str, object]) -> dict[str, object]:
    memory = room.get("shared_memory")
    if not isinstance(memory, dict):
        return {}
    return compact_live_meeting_memory(memory)


def _wait_turn_request_meeting_id(room: dict[str, object], event: dict[str, object]) -> str:
    agent = room.get("agent") if isinstance(room.get("agent"), dict) else {}
    return str(event.get("meeting_id") or room.get("meeting_id") or agent.get("meeting_id") or "").strip()


def _wait_turn_request_timeout_payload(args: argparse.Namespace, room: dict[str, object]) -> dict[str, object]:
    agent = room.get("agent") if isinstance(room.get("agent"), dict) else {}
    requested_cursor = getattr(args, "after_live_event_id", None)
    if requested_cursor is None:
        requested_cursor = getattr(args, "after_event_id", "")
    cursor = str(requested_cursor or agent.get("last_observed_live_event_id") or "").strip()
    return {
        "status": "timeout",
        "agent_id": args.agent_id,
        "timeout_seconds": float(args.timeout),
        "last_observed_live_event_id": _latest_observed_event_id(room.get("live_events"), cursor),
    }


def _format_wait_turn_request(payload: dict[str, object]) -> str:
    event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
    event_id = str(event.get("id") or "official turn request")
    role_id = str(event.get("role_id") or event.get("target_agent_id") or payload.get("agent_id") or "agent")
    content = str(event.get("content") or "").strip()
    return f"{event_id} {role_id}: {content}"


def _wait_dm_candidate(args: argparse.Namespace, room: dict[str, object]) -> dict[str, object] | None:
    agent = room.get("agent") if isinstance(room.get("agent"), dict) else {}
    events = room.get("dm_events") if isinstance(room.get("dm_events"), list) else []
    cursor = str(args.after_dm_event_id or agent.get("last_observed_dm_event_id") or "").strip()
    for event in _events_after_id(events, cursor):
        if not isinstance(event, dict):
            continue
        if str(event.get("side") or "") != "mine":
            continue
        if str(event.get("target_agent_id") or "").strip() != str(args.agent_id):
            continue
        if not str(event.get("id") or "").strip():
            continue
        if not str(event.get("message") or "").strip():
            continue
        return event
    return None


def _wait_dm_payload(args: argparse.Namespace, room: dict[str, object], event: dict[str, object]) -> dict[str, object]:
    event_id = str(event.get("id") or "")
    return {
        "status": "event",
        "agent_id": args.agent_id,
        "source_event_id": event_id,
        "event": event,
        "reply_command": [
            "python3",
            "-m",
            "agentsassemble.cli",
            "live-agent",
            "dm-reply",
            "--server",
            str(args.server),
            "--agent-id",
            str(args.agent_id),
            "--source-event-id",
            event_id,
            "--",
            "<reply>",
        ],
        "ack_command": [
            "python3",
            "-m",
            "agentsassemble.cli",
            "live-agent",
            "heartbeat",
            "--server",
            str(args.server),
            "--agent-id",
            str(args.agent_id),
            "--status",
            "online",
            "--last-error=",
            f"--last-observed-dm-event-id={event_id}",
            "--json",
        ],
        "room": _wait_room_context(room, meeting_id=str(room.get("meeting_id") or "")),
    }


def _format_wait_dm(payload: dict[str, object]) -> str:
    event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
    event_id = str(event.get("id") or "dm")
    message = str(event.get("message") or "").strip()
    return f"{event_id}: {message}"


def _run_live_agent_wait_next(args: argparse.Namespace) -> int:
    deadline = time.monotonic() + float(args.timeout)
    last_room: dict[str, object] = {}
    while True:
        agent_id = urllib.parse.quote(args.agent_id, safe="")
        room = _request_json(_server_url(args.server, f"/api/live-agents/{agent_id}/room"))
        last_room = room
        dm_candidate = _wait_dm_candidate(args, room)
        if dm_candidate is not None:
            payload = _wait_dm_payload(args, room, dm_candidate)
            payload["action"] = "dm"
            if args.as_json:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                print(f"dm {_format_wait_dm(payload)}")
            return 0
        official_candidate = _wait_turn_request_candidate(args, room)
        if official_candidate is not None:
            if _wait_agent_persona_blocks_official_turn(room):
                payload = _wait_persona_blocked_official_turn_payload(args, room, official_candidate)
            else:
                payload = _wait_turn_request_payload(args, room, official_candidate)
                payload["action"] = "official_turn"
            if args.as_json:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                prefix = payload.get("action")
                if prefix == "persona_blocks_official_turn":
                    print(_format_wait_persona_block(payload))
                else:
                    print(f"official_turn {_format_wait_turn_request(payload)}")
            return 0
        return_packet_candidate = _wait_return_packet_candidate(args, room)
        if return_packet_candidate is not None:
            payload = _wait_return_packet_payload(args, room, return_packet_candidate)
            payload["action"] = "return_packet"
            if args.as_json:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                print(f"return_packet {_format_wait_return_packet(payload)}")
            return 0
        lobby_observation = _wait_next_lobby_observation(args, room)
        if lobby_observation is not None:
            action, lobby_candidate = lobby_observation
            payload = (
                _wait_room_event_payload(args, room, lobby_candidate)
                if action == "lobby"
                else _wait_lobby_observation_payload(args, room, lobby_candidate)
            )
            payload["action"] = action
            if args.as_json:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                print(f"{action} {_format_wait_room_event(payload)}")
            return 0
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            payload = _wait_next_timeout_payload(args, last_room)
            if args.as_json:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                lobby_cursor = payload.get("last_observed_event_id") or "(none)"
                live_cursor = payload.get("last_observed_live_event_id") or "(none)"
                dm_cursor = payload.get("last_observed_dm_event_id") or "(none)"
                print(f"no next action after dm {dm_cursor}, lobby {lobby_cursor}, official {live_cursor}")
            return 1
        sleep_interval = live_agent_poll_sleep_seconds(args.poll_interval)
        time.sleep(min(sleep_interval, remaining))


def _wait_next_timeout_payload(args: argparse.Namespace, room: dict[str, object]) -> dict[str, object]:
    agent = room.get("agent") if isinstance(room.get("agent"), dict) else {}
    lobby_cursor = str(args.after_event_id or agent.get("last_observed_event_id") or "").strip()
    live_cursor = str(args.after_live_event_id or agent.get("last_observed_live_event_id") or "").strip()
    dm_cursor = str(args.after_dm_event_id or agent.get("last_observed_dm_event_id") or "").strip()
    return {
        "status": "timeout",
        "agent_id": args.agent_id,
        "timeout_seconds": float(args.timeout),
        "last_observed_event_id": _latest_observed_event_id(room.get("lobby_events"), lobby_cursor),
        "last_observed_live_event_id": _latest_observed_event_id(room.get("live_events"), live_cursor),
        "last_observed_dm_event_id": _latest_observed_event_id(room.get("dm_events"), dm_cursor),
    }


def _wait_next_lobby_observation(args: argparse.Namespace, room: dict[str, object]) -> tuple[str, dict[str, object]] | None:
    agent = room.get("agent") if isinstance(room.get("agent"), dict) else {}
    events = room.get("lobby_events") if isinstance(room.get("lobby_events"), list) else []
    cursor = str(args.after_event_id or agent.get("last_observed_event_id") or "").strip()
    display_name = str(agent.get("display_name") or "").strip()
    engagement_mode = str(agent.get("engagement_mode") or "always").strip() or "always"
    observed_candidate: dict[str, object] | None = None
    for event in _events_after_id(events, cursor):
        if not isinstance(event, dict):
            continue
        if _wait_room_self_event(args.agent_id, display_name, event):
            continue
        if not str(event.get("id") or "").strip():
            continue
        if not str(event.get("message") or "").strip():
            continue
        if _delegate_chain_depth(event) > int(args.max_chain_depth):
            observed_candidate = event
            continue
        if should_reply_to_event(engagement_mode, event, args.agent_id, display_name):
            return ("lobby", event)
        observed_candidate = event
    if observed_candidate is not None:
        return ("observe_lobby", observed_candidate)
    return None


def _wait_lobby_observation_payload(
    args: argparse.Namespace,
    room: dict[str, object],
    event: dict[str, object],
) -> dict[str, object]:
    event_id = str(event.get("id") or "")
    agent = room.get("agent") if isinstance(room.get("agent"), dict) else {}
    engagement_mode = str(agent.get("engagement_mode") or "always").strip() or "always"
    return {
        "status": "event",
        "agent_id": args.agent_id,
        "source_event_id": event_id,
        "engagement_mode": engagement_mode,
        "event": event,
        "ack_command": [
            "python3",
            "-m",
            "agentsassemble.cli",
            "live-agent",
            "heartbeat",
            "--server",
            str(args.server),
            "--agent-id",
            str(args.agent_id),
            "--status",
            "online",
            "--last-error=",
            f"--last-observed-event-id={event_id}",
            "--json",
        ],
        "room": _wait_room_context(room, meeting_id=str(room.get("meeting_id") or "")),
    }


def _wait_return_packet_candidate(args: argparse.Namespace, room: dict[str, object]) -> dict[str, object] | None:
    agent = room.get("agent") if isinstance(room.get("agent"), dict) else {}
    events = room.get("live_events") if isinstance(room.get("live_events"), list) else []
    cursor = str(args.after_live_event_id or agent.get("last_observed_live_event_id") or "").strip()
    for event in _events_after_id(events, cursor):
        if not isinstance(event, dict):
            continue
        if str(event.get("kind") or "") != "artifact":
            continue
        if str(event.get("artifact_kind") or "") != "return_packet":
            continue
        event_id = str(event.get("id") or "").strip()
        if not event_id:
            continue
        target_agent_id = str(event.get("target_agent_id") or "").strip()
        audience = str(event.get("audience") or "").strip()
        targeted_to_agent = target_agent_id == args.agent_id or audience == f"agent:{args.agent_id}"
        if not targeted_to_agent:
            continue
        if not str(event.get("artifact_path") or event.get("artifact_json_path") or "").strip():
            continue
        return event
    return None


def _wait_return_packet_payload(
    args: argparse.Namespace,
    room: dict[str, object],
    event: dict[str, object],
) -> dict[str, object]:
    event_id = str(event.get("id") or "")
    meeting_id = _wait_turn_request_meeting_id(room, event)
    read_command = [
        "python3",
        "-m",
        "agentsassemble.cli",
        "live-agent",
        "return-packet",
        "--server",
        str(args.server),
        "--agent-id",
        str(args.agent_id),
    ]
    if meeting_id:
        read_command.extend(["--meeting-id", meeting_id])
    read_command.extend(["--source-event-id", event_id, "--json"])
    return {
        "status": "event",
        "agent_id": args.agent_id,
        "meeting_id": meeting_id,
        "source_event_id": event_id,
        "event": event,
        "artifact_path": str(event.get("artifact_path") or ""),
        "artifact_json_path": str(event.get("artifact_json_path") or ""),
        "read_command": read_command,
        "ack_command": [
            "python3",
            "-m",
            "agentsassemble.cli",
            "live-agent",
            "heartbeat",
            "--server",
            str(args.server),
            "--agent-id",
            str(args.agent_id),
            "--status",
            "online",
            "--last-error=",
            "--last-observed-live-event-id=" + event_id,
            "--json",
        ],
        "room": _wait_room_context(room, meeting_id=str(room.get("meeting_id") or meeting_id)),
    }


def _format_wait_return_packet(payload: dict[str, object]) -> str:
    event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
    event_id = str(event.get("id") or "return packet")
    artifact_path = str(payload.get("artifact_path") or payload.get("artifact_json_path") or "").strip()
    return f"{event_id} {artifact_path}".strip()


class _JsonlLiveSessionCommandRunner:
    def __init__(self) -> None:
        self.session: JsonlLiveSession | None = None
        self._lock = threading.Lock()

    def __call__(self, command: list[str], prompt: str, *, timeout_seconds: int) -> str:
        with self._lock:
            if self.session is None:
                self.session = JsonlLiveSession(command)
            session = self.session
        try:
            return session.ask(prompt, timeout_seconds=timeout_seconds)
        except Exception:
            self._close_session(session)
            raise

    def close(self) -> None:
        with self._lock:
            session = self.session
            self.session = None
        if session is not None:
            session.close()

    def _close_session(self, session: JsonlLiveSession) -> None:
        with self._lock:
            if self.session is session:
                self.session = None
        session.close()


class _TerminalLiveSessionCommandRunner:
    def __init__(
        self,
        *,
        idle_timeout_seconds: float,
        cwd: Path | None = None,
        message_extractor=None,
        ready_predicate=None,
        submit_newline: str = "\n",
        submit_settle_seconds: float = 0.0,
        warmup_idle_seconds: float = 0.0,
        stream_config=None,
        permission_mode: str = "",
        fast_mode: bool = False,
    ) -> None:
        self.idle_timeout_seconds = idle_timeout_seconds
        self.cwd = Path(cwd or Path.cwd())
        self.session: TerminalLiveSession | None = None
        self._message_extractor = message_extractor
        self._ready_predicate = ready_predicate
        self._submit_newline = submit_newline
        self._submit_settle_seconds = submit_settle_seconds
        self._warmup_idle_seconds = warmup_idle_seconds
        self._lock = threading.Lock()
        self._permission_mode = str(permission_mode or "").strip()
        # Per-agent fast toggle: send claude's `/fast` slash command once after the
        # TUI boots (a runtime control, not a launch flag).
        self._fast_mode = bool(fast_mode)
        # When set (claude_code + stream_thinking), launch with a known
        # --session-id and tail that transcript to stream tool/reasoning steps.
        self._stream_config = stream_config
        self._stream_session_id = ""
        if stream_config is not None:
            from agentsassemble.providers.claude_transcript import generate_claude_session_id

            self._stream_session_id = generate_claude_session_id()

    def __call__(self, command: list[str], prompt: str, *, timeout_seconds: int) -> str:
        with self._lock:
            if self.session is None:
                launch_command = list(command)
                if self._permission_mode and "--permission-mode" not in launch_command:
                    launch_command = [*launch_command, "--permission-mode", self._permission_mode]
                if self._stream_config is not None and "--session-id" not in launch_command:
                    launch_command = [*launch_command, "--session-id", self._stream_session_id]
                self.session = TerminalLiveSession(
                    launch_command,
                    idle_timeout_seconds=self.idle_timeout_seconds,
                    cwd=self.cwd,
                    message_extractor=self._message_extractor,
                    ready_predicate=self._ready_predicate,
                    submit_newline=self._submit_newline,
                    submit_settle_seconds=self._submit_settle_seconds,
                    warmup_idle_seconds=self._warmup_idle_seconds,
                )
                if self._fast_mode:
                    # Toggle fast once on the fresh session; best-effort.
                    try:
                        self.session.submit_slash_command("/fast")
                    except Exception:
                        pass
            session = self.session
        if self._stream_config is None:
            try:
                return session.ask(prompt, timeout_seconds=timeout_seconds)
            except Exception:
                self._close_session(session)
                raise
        # Streaming turn: tail the transcript for tool/reasoning steps while the
        # PTY produces the final answer (returned below). Best-effort + additive.
        from agentsassemble.providers.claude_transcript import (
            ClaudeTranscriptTailer,
            find_claude_transcript,
            tail_until,
        )
        from agentsassemble.room_thought import post_room_thought

        config = self._stream_config
        session_id = self._stream_session_id
        done = {"value": False}

        def _run_tailer() -> None:
            tailer = ClaudeTranscriptTailer(lambda: find_claude_transcript(session_id))

            def _on_event(event: dict) -> None:
                if event["kind"] == "command":
                    post_room_thought(config, f"🔧 {event['text']}", kind="command")
                elif event["kind"] == "reasoning" and event["text"].strip():
                    post_room_thought(config, event["text"], kind="reasoning")
                # plain assistant text is skipped — the PTY ⏺ extraction is the
                # canonical final answer, so streaming it too would duplicate it.

            tail_until(tailer, lambda: done["value"], _on_event)

        tail_thread = threading.Thread(target=_run_tailer, daemon=True)
        tail_thread.start()
        try:
            return session.ask(prompt, timeout_seconds=timeout_seconds)
        except Exception:
            self._close_session(session)
            raise
        finally:
            done["value"] = True
            tail_thread.join(timeout=2)

    def close(self) -> None:
        with self._lock:
            session = self.session
            self.session = None
        if session is not None:
            session.close()

    def _close_session(self, session: TerminalLiveSession) -> None:
        with self._lock:
            if self.session is session:
                self.session = None
        session.close()


class _SelfServiceResidentSupervisor:
    def __init__(
        self,
        config: ResidentAgentConfig,
        *,
        request_json,
        sleep_fn,
        stop_event: threading.Event | None = None,
        isolate_process_group: bool = True,
    ) -> None:
        self.config = config
        self.request_json = request_json
        self.sleep_fn = sleep_fn
        self.stop_event = stop_event or threading.Event()
        self.isolate_process_group = isolate_process_group
        self.process: subprocess.Popen | None = None
        self.closed = False
        self.last_heartbeat_at = 0.0
        self._lock = threading.Lock()

    def run(self) -> int:
        self._register()
        self._heartbeat("online")
        keep_error_presence = False
        try:
            process = self._start_process()
            return self._supervise(process)
        except subprocess.CalledProcessError as error:
            if not self.stop_event.is_set():
                keep_error_presence = self._heartbeat_safely("error", last_error=_self_service_exit_error(error.returncode))
            raise
        finally:
            self.close()
            if not keep_error_presence:
                self._heartbeat_final_offline()

    def close(self) -> None:
        with self._lock:
            self.closed = True
            process = self.process
        if process is not None:
            _terminate_process(process)

    def _start_process(self) -> subprocess.Popen:
        if not self.config.command:
            raise ValueError("self_service resident requires --command.")
        with self._lock:
            if self.closed:
                raise RuntimeError("Self-service resident supervisor is closed.")
        process = subprocess.Popen(
            self.config.command,
            stdin=subprocess.DEVNULL,
            env=_self_service_process_env(self.config),
            start_new_session=self.isolate_process_group and _supports_process_groups(),
        )
        if self.isolate_process_group and _supports_process_groups():
            process_group_pid = getattr(process, "pid", None)
            if isinstance(process_group_pid, int) and process_group_pid > 0:
                setattr(process, "_agentsassemble_process_group_pid", process_group_pid)
        with self._lock:
            if self.closed:
                should_close = True
            else:
                self.process = process
                should_close = False
        if should_close:
            _terminate_process(process)
            raise RuntimeError("Self-service resident supervisor is closed.")
        return process

    def _supervise(self, process: subprocess.Popen) -> int:
        ticks = 0
        while not self.stop_event.is_set():
            return_code = process.poll()
            if return_code is not None:
                if return_code:
                    raise subprocess.CalledProcessError(return_code, self.config.command)
                return 0
            ticks += 1
            if self.config.max_ticks and ticks >= self.config.max_ticks:
                return 0
            self._heartbeat_if_due()
            self.sleep_fn(self.config.poll_interval)
        return 0

    def _register(self) -> None:
        persona_card_id = clean_persona_card_id(self.config.persona_id)
        if not persona_card_id and self.config.persona_path:
            try:
                persona_card_id = clean_persona_card_id(load_persona_card(Path(self.config.persona_path)).id)
            except (OSError, ValueError, json.JSONDecodeError):
                persona_card_id = ""
        character_mode = normalize_character_mode(
            self.config.character_mode,
            has_card=bool(persona_card_id or self.config.persona_path),
        )
        self.request_json(
            _server_url(self.config.server, "/api/live-agents"),
            method="POST",
            payload={
                "agent_id": self.config.agent_id,
                "display_name": self.config.display_name,
                "provider_kind": self.config.provider_kind,
                "connection_kind": self.config.connection_kind,
                "session_id": self.config.session_id,
                "endpoint": self.config.endpoint,
                "meeting_id": self.config.meeting_id,
                "engagement_mode": self.config.engagement_mode,
                "persona_card_id": persona_card_id,
                "character_mode": character_mode,
                "capabilities": ["room_chat", "mentions", "self_service"],
            },
        )

    def _heartbeat(self, status: str, **metadata: object) -> None:
        payload = {"status": status, **metadata}
        if self.config.session_id:
            payload.setdefault("session_id", self.config.session_id)
        self.request_json(
            _server_url(self.config.server, f"/api/live-agents/{urllib.parse.quote(self.config.agent_id, safe='')}/heartbeat"),
            method="POST",
            payload=payload,
        )
        self.last_heartbeat_at = time.monotonic()

    def _heartbeat_if_due(self) -> None:
        if self.config.heartbeat_interval <= 0:
            return
        if time.monotonic() - self.last_heartbeat_at >= self.config.heartbeat_interval:
            self._heartbeat_safely("online", preserve_status=True)

    def _heartbeat_safely(self, status: str, **metadata: object) -> bool:
        try:
            self._heartbeat(status, **metadata)
        except Exception:
            return False
        return True

    def _heartbeat_final_offline(self) -> None:
        self._heartbeat_safely("offline")


class _LocalCliCommandRunner:
    def __init__(self) -> None:
        self.process: subprocess.Popen | None = None
        self.closed = False
        self._lock = threading.Lock()

    def __call__(self, command: list[str], prompt: str, *, timeout_seconds: int) -> str:
        if not command:
            raise ValueError("Delegate command is required.")
        with self._lock:
            if self.closed:
                raise RuntimeError("Local CLI runner is closed.")
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=_supports_process_groups(),
        )
        if _supports_process_groups():
            process_group_pid = getattr(process, "pid", None)
            if isinstance(process_group_pid, int) and process_group_pid > 0:
                setattr(process, "_agentsassemble_process_group_pid", process_group_pid)
        with self._lock:
            if self.closed:
                should_close = True
            else:
                self.process = process
                should_close = False
        if should_close:
            _terminate_process(process)
            raise RuntimeError("Local CLI runner is closed.")
        try:
            try:
                stdout, stderr = process.communicate(input=prompt, timeout=timeout_seconds)
            except subprocess.TimeoutExpired as error:
                _terminate_process(process)
                stdout, stderr = process.communicate()
                raise subprocess.TimeoutExpired(command, timeout_seconds, output=stdout, stderr=stderr) from error
            if process.returncode:
                raise subprocess.CalledProcessError(process.returncode, command, output=stdout, stderr=stderr)
            return stdout
        except BaseException:
            _terminate_process(process)
            raise
        finally:
            with self._lock:
                if self.process is process:
                    self.process = None

    def close(self) -> None:
        with self._lock:
            self.closed = True
            process = self.process
        if process is not None:
            _terminate_process(process)


class _ApiCatalogCommandRunner:
    """In-process runner for the API-provider lane (connection_kind=api_call).

    Unlike the CLI residents, there's no subprocess: this calls the OpenAI-
    compatible adapter (room_api_provider) directly, so the LiveAgentRunner's
    envelope / heartbeat / meta-filter / turn-CAS wrap the model reply unchanged.
    Token usage is recorded best-effort to the local identity store when
    output_root is set (local-first: the resident shares the server's output_root).
    http_post is injectable for tests."""

    def __init__(self, config: ResidentAgentConfig, *, output_root: str = "", http_post=None) -> None:
        self.config = config
        self.output_root = str(output_root or "")
        self._http_post = http_post

    def _store(self):
        if not self.output_root:
            return None
        try:
            from agentsassemble.persistence.local.identity.registry import (
                identity_store_for_output_root,
            )

            return identity_store_for_output_root(Path(self.output_root))
        except (OSError, ValueError):
            return None  # usage accounting is best-effort; never block the reply

    def __call__(self, command: list[str], prompt: str, *, timeout_seconds: int) -> str:
        from agentsassemble.providers import api as room_api_provider

        try:
            return room_api_provider.run_api_call(
                self.config.provider_kind,
                self.config.model_id,
                prompt,
                store=self._store(),
                participant_id=self.config.agent_id,
                meeting_id=self.config.meeting_id,
                key_source=str(getattr(self.config, "key_source", "") or ""),
                timeout=timeout_seconds,
                http_post=self._http_post,
            )
        except room_api_provider.ApiProviderError as error:
            raise RuntimeError(f"API provider call failed [{error.category}]: {error}") from error

    def close(self) -> None:
        return None


def _self_service_process_env(config: ResidentAgentConfig) -> dict[str, str]:
    env = dict(os.environ)
    command_env = _self_service_room_command_env(config)
    env.update(
        {
            "AGENTSASSEMBLE_SERVER": config.server,
            "AGENTSASSEMBLE_AGENT_ID": config.agent_id,
            "AGENTSASSEMBLE_DISPLAY_NAME": config.display_name,
            "AGENTSASSEMBLE_PROVIDER_KIND": config.provider_kind,
            "AGENTSASSEMBLE_CONNECTION_KIND": config.connection_kind,
            "AGENTSASSEMBLE_MEETING_ID": config.meeting_id,
            "AGENTSASSEMBLE_ENGAGEMENT_MODE": config.engagement_mode,
            "AGENTSASSEMBLE_MAX_CHAIN_DEPTH": str(config.max_chain_depth),
            "AGENTSASSEMBLE_POLL_INTERVAL": str(config.poll_interval),
            "AGENTSASSEMBLE_HEARTBEAT_INTERVAL": str(config.heartbeat_interval),
            "AGENTSASSEMBLE_LEGACY_INTERNAL": "1",
        }
    )
    env.update(command_env)
    return env


def _self_service_room_command_env(config: ResidentAgentConfig) -> dict[str, str]:
    base = [sys.executable, "-m", "agentsassemble.cli", "live-agent", "--legacy-internal"]
    identity = ["--server", config.server, "--agent-id", config.agent_id]
    return {
        "AGENTSASSEMBLE_ROOM_COMMAND": shlex.join([*base, "room", *identity]),
        "AGENTSASSEMBLE_WAIT_NEXT_COMMAND": shlex.join(
            [
                *base,
                "wait-next",
                *identity,
                "--max-chain-depth",
                str(config.max_chain_depth),
                "--poll-interval",
                str(config.poll_interval),
                "--json",
            ]
        ),
        "AGENTSASSEMBLE_WAIT_ROOM_EVENT_COMMAND": shlex.join(
            [
                *base,
                "wait-room-event",
                *identity,
                "--max-chain-depth",
                str(config.max_chain_depth),
                "--poll-interval",
                str(config.poll_interval),
                "--json",
            ]
        ),
        "AGENTSASSEMBLE_WAIT_OFFICIAL_TURN_COMMAND": shlex.join(
            [
                *base,
                "wait-official-turn",
                *identity,
                "--poll-interval",
                str(config.poll_interval),
                "--json",
            ]
        ),
        "AGENTSASSEMBLE_SAY_COMMAND_TEMPLATE": shlex.join(
            [
                *base,
                "say",
                *identity,
                "--source-event-id",
                "{source_event_id}",
                "--auto-chain-depth",
                "{auto_chain_depth}",
                "--",
                "{message}",
            ]
        ),
        "AGENTSASSEMBLE_OFFICIAL_REPLY_COMMAND_TEMPLATE": shlex.join(
            [
                *base,
                "official-reply",
                *identity,
                "--meeting-id",
                "{meeting_id}",
                "--source-event-id",
                "{source_event_id}",
                "--",
                "{message}",
            ]
        ),
        "AGENTSASSEMBLE_DM_REPLY_COMMAND_TEMPLATE": shlex.join(
            [
                *base,
                "dm-reply",
                *identity,
                "--source-event-id",
                "{source_event_id}",
                "--",
                "{message}",
            ]
        ),
        "AGENTSASSEMBLE_HEARTBEAT_COMMAND_TEMPLATE": shlex.join(
            [
                *base,
                "heartbeat",
                *identity,
                "--status",
                "{status}",
                "--last-error={last_error}",
                "--last-attention={last_attention}",
                "--last-reply-at={last_reply_at}",
                "--last-observed-event-id={last_observed_event_id}",
                "--last-observed-live-event-id={last_observed_live_event_id}",
                "--last-observed-dm-event-id={last_observed_dm_event_id}",
                "--json",
            ]
        ),
        "AGENTSASSEMBLE_LEAVE_COMMAND": shlex.join([*base, "leave", *identity, "--json"]),
    }


def _self_service_exit_error(return_code: int) -> str:
    return f"Self-service command exited with return code {return_code}."


def _terminate_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    _send_process_stop_signal(process, _stop_signal("SIGTERM"), force=False)
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        _send_process_stop_signal(process, _stop_signal("SIGKILL"), force=True)
        process.wait(timeout=1)


def _send_process_stop_signal(process: subprocess.Popen, stop_signal: int | None, *, force: bool) -> None:
    process_group_pid = _process_group_pid(process)
    if process_group_pid is not None and stop_signal is not None:
        try:
            os.killpg(process_group_pid, stop_signal)
            return
        except ProcessLookupError:
            return
        except OSError:
            pass
    try:
        if force:
            process.kill()
        else:
            process.terminate()
    except ProcessLookupError:
        return


def _process_group_pid(process: subprocess.Popen) -> int | None:
    if not _supports_process_groups():
        return None
    pgid = getattr(process, "_agentsassemble_process_group_pid", None)
    return pgid if isinstance(pgid, int) and pgid > 0 else None


def _supports_process_groups() -> bool:
    return hasattr(os, "killpg") and hasattr(os, "setsid")


def _stop_signal(name: str) -> int | None:
    value = getattr(signal, name, None)
    return value if isinstance(value, int) else None


def _install_resident_shutdown_signal_handlers(on_shutdown):
    sigterm = _stop_signal("SIGTERM")
    if sigterm is None or threading.current_thread() is not threading.main_thread():
        return lambda: None

    previous_handlers = {}

    def handle_shutdown(signum, frame):
        del signum, frame
        on_shutdown()
        raise KeyboardInterrupt()

    try:
        previous_handlers[sigterm] = signal.signal(sigterm, handle_shutdown)
    except (OSError, RuntimeError, ValueError):
        return lambda: None

    def restore_signal_handlers() -> None:
        for signum, previous_handler in previous_handlers.items():
            try:
                signal.signal(signum, previous_handler)
            except (OSError, RuntimeError, ValueError):
                pass

    return restore_signal_handlers


def _validate_resident_config(config: ResidentAgentConfig) -> None:
    if config.connection_kind not in SUPPORTED_RESIDENT_CONNECTION_KINDS:
        raise ValueError(resident_connection_kind_error())
    if config.connection_kind == "api_call":
        # API-provider lane: --provider-kind is a catalog provider id and --model
        # must exist in that provider's catalog. No --command (the call is in-process).
        from agentsassemble.providers import catalog as provider_catalog

        if not provider_catalog.get_provider(config.provider_kind):
            raise ValueError(
                f"api_call resident requires a known catalog provider as --provider-kind; got {config.provider_kind!r}. "
                f"Known: {', '.join(provider_catalog.list_providers())}."
            )
        if not provider_catalog.get_model(config.provider_kind, config.model_id):
            raise ValueError(
                f"api_call resident requires a known --model for provider {config.provider_kind!r}; got {config.model_id!r}."
            )
        return
    if config.provider_kind == "codex_live_session" and config.connection_kind != "live_session":
        raise ValueError("codex_live_session resident requires live_session connection_kind.")
    if config.provider_kind == "kiro_live_session" and config.connection_kind != "live_session":
        raise ValueError("kiro_live_session resident requires live_session connection_kind.")
    if config.provider_kind == "cursor_live_session" and config.connection_kind != "live_session":
        raise ValueError("cursor_live_session resident requires live_session connection_kind.")
    if config.provider_kind == "grok_live_session" and config.connection_kind != "live_session":
        raise ValueError("grok_live_session resident requires live_session connection_kind.")
    if config.provider_kind == "antigravity_live_session" and config.connection_kind != "live_session":
        raise ValueError("antigravity_live_session resident requires live_session connection_kind.")
    if config.provider_kind == "hermes_live_session" and config.connection_kind != "live_session":
        raise ValueError("hermes_live_session resident requires live_session connection_kind.")
    cursor_superseded_error = cursor_terminal_session_superseded_error(
        config.provider_kind,
        config.connection_kind,
        config.command,
    )
    if cursor_superseded_error:
        raise ValueError(cursor_superseded_error)
    cursor_generic_error = cursor_generic_resident_guard_error(config.provider_kind, config.connection_kind)
    if cursor_generic_error:
        raise ValueError(cursor_generic_error)
    claude_command_error = claude_code_print_mode_resident_error(
        config.provider_kind,
        config.connection_kind,
        config.command,
    )
    if claude_command_error:
        raise ValueError(claude_command_error)
    if config.connection_kind == "remote_bridge":
        if not config.endpoint:
            raise ValueError("Remote bridge resident requires --endpoint.")
        if not config.auth_ref:
            raise ValueError("Remote bridge resident requires --auth-ref.")
        return
    if config.connection_kind in {"local_cli", "live_session", "terminal_session", "self_service", "codex_resume", "manual"} and not config.command:
        raise ValueError(f"{config.connection_kind} resident requires --command.")


def _command_runner_for_config(config: ResidentAgentConfig, *, output_root: str = ""):
    if config.connection_kind == "self_service":
        raise ValueError("self_service residents are supervised directly and do not use prompt-injection command runners.")
    if config.connection_kind == "api_call":
        return _ApiCatalogCommandRunner(config, output_root=output_root)
    cwd = _resident_workspace_cwd(config)
    if config.provider_kind == "codex_live_session" and config.connection_kind == "live_session":
        return CodexResidentCommandRunner(config, cwd=cwd)
    if config.provider_kind == "kiro_live_session" and config.connection_kind == "live_session":
        return KiroResidentCommandRunner(config, cwd=cwd)
    if config.provider_kind == "cursor_live_session" and config.connection_kind == "live_session":
        return CursorResidentCommandRunner(config, cwd=cwd)
    if config.provider_kind == "grok_live_session" and config.connection_kind == "live_session":
        return GrokResidentCommandRunner(config, cwd=cwd)
    if config.provider_kind == "antigravity_live_session" and config.connection_kind == "live_session":
        return AntigravityResidentCommandRunner(config, cwd=cwd)
    if config.provider_kind == "hermes_live_session" and config.connection_kind == "live_session":
        return HermesResidentCommandRunner(config, cwd=cwd)
    if config.provider_kind == "claude_code" and config.connection_kind == "terminal_session":
        # Claude Code's interactive TUI: scrape the PTY, gate completion on the ⏺
        # answer marker, and extract just the reply (see claude_resident). A longer
        # idle floor tolerates mid-answer pauses while it streams.
        from agentsassemble.providers.claude_resident import (
            claude_answer_ready,
            extract_claude_terminal_message,
        )

        return _TerminalLiveSessionCommandRunner(
            idle_timeout_seconds=max(float(config.terminal_idle_timeout or 0.0), 1.0),
            cwd=cwd,
            message_extractor=extract_claude_terminal_message,
            ready_predicate=claude_answer_ready,
            submit_newline="\r",  # Claude Code's TUI submits on Enter (CR), not LF
            submit_settle_seconds=0.4,  # let the typed prompt land in the input box first
            warmup_idle_seconds=1.5,  # wait for the TUI to finish booting before first submit
            # Stream claude's tool/reasoning steps by tailing its transcript JSONL
            # (launched with a known --session-id so it's unambiguous across runs).
            stream_config=config if getattr(config, "stream_thinking", False) else None,
            # claude's own --permission-mode (default/plan/acceptEdits/bypassPermissions).
            permission_mode=str(getattr(config, "permission_option", "") or ""),
            # Per-agent fast toggle → claude's /fast runtime slash command.
            fast_mode=bool(getattr(config, "fast_mode", False)),
        )
    if config.connection_kind == "live_session":
        return _JsonlLiveSessionCommandRunner()
    if config.connection_kind == "terminal_session":
        return _TerminalLiveSessionCommandRunner(idle_timeout_seconds=config.terminal_idle_timeout, cwd=cwd)
    if config.connection_kind == "remote_bridge":
        return RemoteBridgeResidentCommandRunner(config)
    return _LocalCliCommandRunner()


def _resident_workspace_cwd(config: ResidentAgentConfig) -> Path:
    workspace_path = str(getattr(config, "workspace_path", "") or "").strip()
    if not workspace_path:
        return Path.cwd()
    path = Path(workspace_path).expanduser()
    if not path.exists() or not path.is_dir():
        raise ValueError("Workspace folder was not found.")
    return path


def _close_command_runner(command_runner) -> None:
    close = getattr(command_runner, "close", None)
    if close is not None:
        close()


def _delegate_prompt(args: argparse.Namespace, room: dict[str, object]) -> str:
    events = room.get("lobby_events") if isinstance(room.get("lobby_events"), list) else []
    lines = [
        "You are a live AgentsAssemble participant in the room, working through a local CLI bridge with your normal tools available.",
        f"Agent id: {args.agent_id}",
        f"Display name: {args.display_name or args.agent_id}",
        "Judge what the latest message needs, the way you normally would:",
        "- Just conversation -> reply conversationally.",
        "- A task (edit files, run or check something, investigate) -> actually do it with your tools, then report what you did or found.",
        "Do the real work with your tools (not by pasting it into chat). If you lack the access to do something here, say so plainly instead of pretending.",
        reply_length_directive(getattr(args, "reply_char_limit", 0)),
        "Write like a chat: break your message into short lines with a newline after each sentence or distinct thought, not one dense paragraph.",
        "Do not describe this runner, polling, heartbeats, control prompts, or delivery envelopes. No markdown fences.",
        "",
        "Recent lobby events:",
    ]
    for event in events[-12:]:
        if not isinstance(event, dict):
            continue
        name = str(event.get("name") or "participant")
        message = str(event.get("message") or "").strip()
        if message:
            lines.append(f"- {name}: {message}")
    return "\n".join(lines).strip() + "\n"


def _delegate_source_event(args: argparse.Namespace, room: dict[str, object]) -> dict[str, object] | None:
    events = room.get("lobby_events") if isinstance(room.get("lobby_events"), list) else []
    for event in reversed(_delegate_unobserved_events(args, room, events)):
        if not isinstance(event, dict):
            continue
        if not str(event.get("id") or "").strip():
            continue
        if not str(event.get("message") or "").strip():
            continue
        if _delegate_self_event(args, event):
            continue
        return event
    return None


def _delegate_unobserved_events(
    args: argparse.Namespace,
    room: dict[str, object],
    events: list[object],
) -> list[object]:
    agent = room.get("agent") if isinstance(room.get("agent"), dict) else {}
    if str(agent.get("agent_id") or "") != args.agent_id:
        return events
    cursor = str(agent.get("last_observed_event_id") or "").strip()
    if not cursor:
        return events
    for index, event in enumerate(events):
        if isinstance(event, dict) and str(event.get("id") or "") == cursor:
            return events[index + 1 :]
    return events


def _delegate_self_event(args: argparse.Namespace, event: dict[str, object]) -> bool:
    actor_id = str(event.get("actor_id") or "")
    if actor_id and actor_id == args.agent_id:
        return True
    display_name = str(args.display_name or args.agent_id or "")
    return bool(display_name) and str(event.get("name") or "") == display_name


def _delegate_chain_depth(event: dict[str, object]) -> int:
    value = event.get("auto_chain_depth")
    if isinstance(value, int) and not isinstance(value, bool):
        return max(0, value)
    return 0


def _heartbeat_delegate_error(args: argparse.Namespace, quoted_agent_id: str, error: Exception) -> None:
    try:
        _request_json(
            _server_url(args.server, f"/api/live-agents/{quoted_agent_id}/heartbeat"),
            method="POST",
            payload={"status": "error", "last_error": _delegate_error_message(error)},
        )
    except Exception:
        return


def _delegate_error_message(error: Exception) -> str:
    if isinstance(error, subprocess.CalledProcessError):
        return f"Delegate command exited with return code {error.returncode}."
    if isinstance(error, subprocess.TimeoutExpired):
        return f"Delegate command timed out after {error.timeout} seconds."
    if isinstance(error, OSError):
        detail = str(getattr(error, "strerror", "") or "").strip() or error.__class__.__name__
        return f"Delegate command failed: {detail}."
    message = str(error).strip()
    return message or "Delegate command failed."


def _run_delegate_command(command: list[str], prompt: str, *, timeout_seconds: int) -> str:
    if not command:
        raise ValueError("Delegate command is required.")
    completed = subprocess.run(
        command,
        input=prompt,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=True,
    )
    return completed.stdout


def _server_url(server: str, path: str) -> str:
    return f"{server.rstrip('/')}{path}"


def _probe_http_timeout(probe_timeout_seconds: float) -> float:
    return max(10.0, float(probe_timeout_seconds) + 2.0)


def _operation_http_timeout(wait_seconds: float, *, windows: int = 1) -> float:
    return max(10.0, float(wait_seconds) * max(1, int(windows)) + 6.0)


def _session_smoke_http_timeout(
    wait_seconds: float,
    *,
    lobby_probe_count: int = 1,
    soak_cycle_count: int = 0,
    soak_interval_seconds: float = 0.0,
) -> float:
    timeout = max(0.0, float(wait_seconds))
    probes = max(1, int(lobby_probe_count))
    soak_cycles = max(0, int(soak_cycle_count))
    soak_interval = max(0.0, float(soak_interval_seconds))
    return (
        _operation_http_timeout(timeout)
        + _operation_http_timeout(timeout, windows=4)
        + (timeout * probes)
        + 10.0
        + _operation_http_timeout(timeout)
        + _operation_http_timeout(timeout)
        + (timeout * probes)
        + timeout
        + _operation_http_timeout(timeout)
        + (timeout * probes)
        + (soak_cycles * (10.0 + timeout + soak_interval))
        + 20.0
    )


def _real_session_smoke_http_timeout(wait_seconds: float) -> float:
    timeout = max(0.0, float(wait_seconds))
    return _operation_http_timeout(timeout, windows=25) + 22.0


def _request_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, object] | None = None,
    timeout_seconds: float = 10.0,
) -> dict[str, object]:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"} if payload is not None else {}
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            loaded = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        try:
            message, code = _http_error_details(error)
        finally:
            error.close()
        raise CliHttpError(
            message,
            status_code=int(error.code or 0),
            code=code,
        ) from error
    return loaded if isinstance(loaded, dict) else {}


def _http_error_details(error: urllib.error.HTTPError) -> tuple[str, str]:
    body = error.read().decode("utf-8", errors="replace")
    if body:
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = {}
        if isinstance(payload, dict) and payload.get("error"):
            return str(payload["error"]), str(payload.get("code") or "")
        return body.strip(), ""
    return str(error), ""


def run_sessions_command(args: argparse.Namespace) -> int:
    if not bool(getattr(args, "legacy_internal", False)):
        print("sessions is a legacy/internal Codex discovery path; use assemble room resume for Agent Sessions.", file=sys.stderr)
        return 2
    if args.sessions_command == "list":
        sessions = list_codex_sessions(limit=args.limit)
        if args.as_json:
            print(json.dumps(sessions, ensure_ascii=False, indent=2))
        else:
            for index, session in enumerate(sessions, start=1):
                print(f"{index:>2}  {session['updated_at']}  {session['id']}  {session['thread_name']}")
        return 0
    if args.sessions_command == "invite":
        try:
            if args.server:
                response = _request_json(
                    _server_url(args.server, "/api/codex-sessions/invite"),
                    method="POST",
                    payload={
                        "session_id": args.session_id,
                        "role_id": args.role_id,
                        "meeting_id": args.meeting_id,
                    },
                )
                if args.as_json:
                    print(json.dumps(response, ensure_ascii=False, indent=2))
                else:
                    binding = response.get("binding") if isinstance(response.get("binding"), dict) else {}
                    print(f"Invited {binding.get('role_id') or args.role_id} as {binding.get('agent_id') or 'Codex live session'}")
                return 0
            role_ids = [role.id for role in load_council_config().roles]
            output_path = Path(args.output)
            config = build_codex_live_invite_config(
                session_id=args.session_id,
                role_id=args.role_id,
                role_ids=role_ids,
                existing=read_agent_config(output_path),
            )
            write_agent_config(output_path, config)
        except (OSError, urllib.error.URLError, ValueError, json.JSONDecodeError) as error:
            print(f"error: {error}", file=sys.stderr)
            return 2
        print(f"Wrote {output_path}")
        return 0
    if args.sessions_command == "live-agent-config":
        try:
            output_path = Path(args.output)
            config = build_codex_live_agent_config(
                read_agent_config(args.input_path),
                server=args.server,
                meeting_id=args.meeting_id,
                engagement_mode=args.engagement_mode,
            )
            write_agent_config(output_path, config)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            print(f"error: {error}", file=sys.stderr)
            return 2
        next_commands = _codex_live_agent_config_next_commands(
            input_path=str(args.input_path),
            output_path=str(output_path),
            server=str(args.server),
            meeting_id=str(args.meeting_id),
        )
        if args.as_json:
            print(
                json.dumps(
                    {"output": str(output_path), "config": config, "next_commands": next_commands},
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            print(f"Wrote {output_path}")
            print("Next preflight: " + shlex.join(next_commands["preflight"]))
            print("Next ensure-session: " + shlex.join(next_commands["ensure_session"]))
        return 0
    return 1


def run_room_command(args: argparse.Namespace) -> int:
    if args.room_command == "migrate-legacy-messages":
        from agentsassemble.legacy_room_migration import migrate_legacy_messages

        try:
            result = migrate_legacy_messages(Path(args.output_root), apply=bool(args.apply))
        except (OSError, ValueError, sqlite3.Error, json.JSONDecodeError) as error:
            print(f"error: {error}", file=sys.stderr)
            return 2
        if args.as_json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(
                f"{result['status']}: {result['message_count']} message(s) "
                f"from {result['room_count']} room(s)"
            )
            for room in result["rooms"]:
                print(f"- {room['room_id']}: {room['message_count']}")
            if result.get("backup_dir"):
                print(f"backup: {result['backup_dir']}")
        return 0
    if args.room_command == "migrate-postgres":
        from agentsassemble.room_repository_factory import (
            RoomRepositoryConfigurationError,
            RoomRepositorySettings,
        )
        from agentsassemble.room_repository_migration import (
            RoomRepositoryTransferError,
            migrate_sqlite_rooms_to_postgres,
        )

        try:
            settings = RoomRepositorySettings.from_environment(
                backend="postgresql",
                postgres_dsn_env=str(args.postgres_dsn_env),
            )
            if not settings.postgres_dsn:
                raise RoomRepositoryConfigurationError(
                    f"PostgreSQL room migration requires {settings.postgres_dsn_env} to be set."
                )
            result = migrate_sqlite_rooms_to_postgres(
                Path(args.output_root),
                postgres_dsn=settings.postgres_dsn,
                apply=bool(args.apply),
            )
        except (RoomRepositoryConfigurationError, RoomRepositoryTransferError) as error:
            print(f"error: {error}", file=sys.stderr)
            return 2
        if args.as_json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            source_counts = result["source"]["row_counts"]
            print(
                f"PostgreSQL room migration {result['mode']}: {result['status']} · "
                f"rooms={source_counts['rooms']} · events={source_counts['room_events']}"
            )
            print(f"source checksum: {result['source']['checksum']}")
            if result.get("verified"):
                print("target checksum verified")
            elif not result.get("can_apply"):
                print("target is not safe to apply")
        return 0 if result.get("status") in {"ready", "applied"} else 1
    if args.room_command == "migrate-room-settings":
        from agentsassemble.room_settings_migration import (
            LegacyRoomSettingsMigrationError,
            migrate_legacy_room_settings,
        )

        try:
            result = migrate_legacy_room_settings(
                Path(args.output_root),
                apply=bool(args.apply),
            )
        except (LegacyRoomSettingsMigrationError, OSError, ValueError, sqlite3.Error) as error:
            print(f"error: {error}", file=sys.stderr)
            return 2
        if args.as_json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(
                f"Legacy room settings migration {result['mode']}: {result['status']} · "
                f"rooms={result['candidate_room_count']} · changes={result['change_count']} · "
                f"issues={result['issue_count']}"
            )
            if result.get("source_fingerprint"):
                print(f"source fingerprint: {result['source_fingerprint']}")
            for issue in result.get("issues", []):
                print(
                    f"- {issue.get('room_id') or '<file>'} {issue.get('field') or '<record>'}: "
                    f"{issue.get('message')}"
                )
            if result.get("backup_dir"):
                print(f"backup: {result['backup_dir']}")
            elif result.get("status") == "ready":
                print("Run the same command with --apply after reviewing the dry-run plan.")
        return 0 if result.get("status") in {"ready", "applied", "already_applied", "not_needed"} else 1
    if args.room_command == "migrate-room-preferences":
        from agentsassemble.room_preferences_migration import (
            LegacyRoomPreferencesMigrationError,
            migrate_legacy_room_preferences,
        )

        try:
            result = migrate_legacy_room_preferences(
                Path(args.output_root),
                user_id=str(args.user_id),
                apply=bool(args.apply),
            )
        except (LegacyRoomPreferencesMigrationError, OSError, ValueError, sqlite3.Error) as error:
            print(f"error: {error}", file=sys.stderr)
            return 2
        if args.as_json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(
                f"Legacy room preference migration {result['mode']}: {result['status']} · "
                f"user={result['user_id']} · rooms={result['candidate_room_count']} · "
                f"changes={result['change_count']} · issues={result['issue_count']}"
            )
            for issue in result.get("issues", []):
                print(
                    f"- {issue.get('room_id') or '<file>'} {issue.get('field') or '<record>'}: "
                    f"{issue.get('message')}"
                )
            if result.get("backup_dir"):
                print(f"backup: {result['backup_dir']}")
            elif result.get("status") == "ready":
                print("Run the same command with --apply after reviewing the dry-run plan.")
        return 0 if result.get("status") in {"ready", "applied", "already_applied", "not_needed"} else 1
    if args.room_command == "purge-admission-workflows":
        from agentsassemble.admission.maintenance_command import (
            purge_admission_workflows,
        )
        from agentsassemble.admission.repository import InviteRepositoryError
        from agentsassemble.room_repository_factory import (
            RoomRepositoryConfigurationError,
            RoomRepositoryUnavailable,
        )

        try:
            result = purge_admission_workflows(
                output_root=Path(args.output_root),
                repository_backend=str(args.room_repository_backend),
                postgres_dsn_env=str(args.room_postgres_dsn_env),
                updated_before=str(args.before),
                room_id=str(args.room_id or ""),
                apply=bool(args.apply),
            )
        except (
            InviteRepositoryError,
            RoomRepositoryConfigurationError,
            RoomRepositoryUnavailable,
            OSError,
            ValueError,
        ) as error:
            print(f"error: {error}", file=sys.stderr)
            return 2
        if args.as_json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(
                f"Admission workflow purge {result['mode']}: "
                f"selected={result['selected_count']} · purged={result['purged_count']}"
            )
            if not args.apply:
                print("Review the dry-run result, then repeat with --apply to delete it.")
        return 0
    if args.room_command == "list":
        query = urllib.parse.urlencode({"include_archived": "true"} if args.include_archived else {})
        path = "/api/rooms" + (f"?{query}" if query else "")
        payload = _request_json(_server_url(args.server, path))
        if args.as_json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            rooms = payload.get("rooms") if isinstance(payload.get("rooms"), list) else []
            if not rooms:
                print("no rooms")
            for room in rooms:
                print(
                    f"{room.get('room_id')}: {room.get('status') or ('archived' if room.get('archived') else 'active')}"
                )
        return 0
    if args.room_command == "status":
        query = urllib.parse.urlencode({"room_id": args.room_id})
        payload = _request_json(_server_url(args.server, f"/api/rooms/state?{query}"))
        if args.as_json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            room = payload.get("room") if isinstance(payload.get("room"), dict) else {}
            participants = payload.get("active_participants") if isinstance(payload.get("active_participants"), list) else []
            print(f"{room.get('room_id') or args.room_id}: {room.get('status') or 'unknown'}")
            print(f"active participants: {len(participants)}")
        return 0
    if args.room_command == "benchmark":
        from agentsassemble.canonical_room_benchmark import (
            CanonicalRoomBenchmarkOptions,
            run_canonical_room_benchmark,
        )

        result = run_canonical_room_benchmark(
            CanonicalRoomBenchmarkOptions(
                output_root=Path(args.output_root) if args.output_root else None,
                events=int(args.events),
                agent_count=int(args.agent_count),
                read_window=int(args.read_window),
                samples=int(args.samples),
                cleanup=not bool(args.keep_output),
            )
        )
        if args.as_json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            metrics = result.get("metrics") if isinstance(result.get("metrics"), dict) else {}
            latest = metrics.get("latest_window_ms") if isinstance(metrics.get("latest_window_ms"), dict) else {}
            reconnect = metrics.get("reconnect_after_seq_ms") if isinstance(metrics.get("reconnect_after_seq_ms"), dict) else {}
            context = metrics.get("agent_context_ms") if isinstance(metrics.get("agent_context_ms"), dict) else {}
            print(
                f"canonical room benchmark: {result.get('status')} · "
                f"events={result.get('measured_event_count')} · agents={args.agent_count}"
            )
            print(f"- latest window p50/p95: {latest.get('p50_ms')} / {latest.get('p95_ms')} ms")
            print(f"- reconnect p50/p95: {reconnect.get('p50_ms')} / {reconnect.get('p95_ms')} ms")
            print(f"- agent context p50/p95: {context.get('p50_ms')} / {context.get('p95_ms')} ms")
        return 0 if result.get("status") == "ok" else 1
    if args.room_command in {"join", "resume"}:
        payload = {
            "room_id": args.room_id,
            "agent_id": args.agent,
            "session_id": args.session or args.agent,
            "provider_session_id": args.provider_session_id,
            "model": args.model,
            "effort": args.effort,
            "sandbox": args.sandbox,
            "permissions": args.permissions,
            "provider_kind": clean_agent_session_provider_kind(args.provider_kind or args.provider),
            "start": bool(args.start),
            "dry_run": bool(args.dry_run),
        }
        response = _request_json(
            _server_url(args.server, "/api/agent-sessions/resume"),
            method="POST",
            payload=payload,
        )
        if args.as_json:
            print(json.dumps(response, ensure_ascii=False, indent=2))
        else:
            participant = response.get("participant") if isinstance(response.get("participant"), dict) else {}
            process_status = response.get("process_status") or "unknown"
            print(
                f"attached Agent Session {participant.get('participant_id') or args.agent} "
                f"in {args.room_id} · process: {process_status}"
            )
        return 0
    if args.room_command == "attend":
        from agentsassemble.room_attendee import run_attendee_from_cli

        return run_attendee_from_cli(
            provider_id=str(args.provider),
            display_name=str(args.display_name or ""),
            workspace=str(args.workspace or ""),
            model=str(args.model or ""),
            reasoning_effort=str(args.effort or ""),
            service_tier=str(args.service_tier or ""),
            variant=str(args.variant or ""),
            permission_mode=str(args.permission_mode or "meeting_read_only"),
        )
    if args.room_command == "smoke":
        live_cli_providers = [
            clean_lobby_text(provider, limit=128)
            for provider in str(getattr(args, "providers", "") or "").split(",")
            if provider.strip()
        ]
        if live_cli_providers:
            payload = run_room_native_cli_smoke(
                config_path=getattr(args, "config", str(DEFAULT_LIVE_CLI_SMOKE_CONFIG)),
                providers=live_cli_providers,
                approve_real_provider=bool(args.approve_real_provider),
                timeout_seconds=float(getattr(args, "timeout", 120.0) or 120.0),
                latency_samples=int(getattr(args, "latency_samples", 0) or 0),
                agent_conversation=bool(getattr(args, "agent_conversation", False)),
                conversation_seconds=float(getattr(args, "conversation_seconds", 0.0) or 0.0),
                conversation_topic=str(getattr(args, "conversation_topic", "") or ""),
                verify_controls=bool(getattr(args, "verify_controls", False)),
                observe_gui_port=int(getattr(args, "observe_gui_port", 0) or 0),
            )
        elif bool(args.approve_real_provider) and args.room_smoke_command in CODEX_APP_SERVER_SMOKE_COMMANDS:
            payload = run_codex_app_server_smoke(
                args.room_smoke_command,
                approve_real_provider=True,
            )
        else:
            payload = {
                "status": "skipped" if not args.approve_real_provider else "not_run",
                "smoke": args.room_smoke_command or "live-cli",
                "requires_approval": True,
                "approved": bool(args.approve_real_provider),
                "metrics": {
                    "cold_start_ms": None,
                    "warm_turn_ms": [],
                    "time_to_turn_start_ack_ms": [],
                    "time_to_first_notification_ms": [],
                    "time_to_first_agent_delta_ms": [],
                    "time_to_message_final_ms": [],
                    "turn_completed_ms": [],
                    "provider_visible_chars": [],
                    "thread_reused": [],
                    "runtime_reused": [],
                    "runtime_profile_key": [],
                    "rss_kb_start": None,
                    "rss_kb_end": None,
                    "rss_kb_delta": None,
                    "token_usage": [],
                    "context_failures": 0,
                    "errors": [],
                    "distinct_provider_session_id": None,
                },
            }
        if args.as_json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"{payload['smoke']}: {payload['status']} (real provider smoke is opt-in and not run by unit tests)")
        return 0 if payload.get("status") in {"ok", "skipped", "not_run"} else 1
    if args.room_command == "turn":
        payload = {
            "room_id": args.room_id,
            "agent_id": args.agent,
            "session_id": args.session or args.agent,
            "instruction": args.instruction,
            "dry_run": bool(args.dry_run),
        }
        response = _request_json(
            _server_url(args.server, "/api/agent-sessions/turn"),
            method="POST",
            payload=payload,
        )
        if args.as_json:
            print(json.dumps(response, ensure_ascii=False, indent=2))
        else:
            print(f"ran Agent Session turn {response.get('turn_id') or ''} in {args.room_id}: {response.get('turn_status') or response.get('status')}")
        return 0
    if args.room_command == "leave":
        payload = {"room_id": args.room_id, "participant_id": args.agent}
        response = _request_json(
            _server_url(args.server, "/api/room-participants/leave"),
            method="POST",
            payload=payload,
        )
        if args.as_json:
            print(json.dumps(response, ensure_ascii=False, indent=2))
        else:
            print(f"left {args.room_id}: {args.agent}")
        return 0
    return 1


def _codex_live_agent_config_next_commands(
    *,
    input_path: str,
    output_path: str,
    server: str,
    meeting_id: str,
) -> dict[str, list[str]]:
    group_id = clean_live_agent_group_id(Path(output_path).stem)
    ensure_session = [
        "python3",
        "-m",
        "agentsassemble.cli",
        "live-agent",
        "--legacy-internal",
        "ensure-session",
        "--server",
        server,
    ]
    if meeting_id:
        ensure_session.extend(["--meeting-id", meeting_id])
    ensure_session.extend(["--group-id", group_id])
    ensure_session.extend(
        [
            "--agent-config",
            input_path,
            "--live-agent-config",
            output_path,
        ]
    )
    return {
        "preflight": [
            "python3",
            "-m",
            "agentsassemble.cli",
            "live-agent",
            "--legacy-internal",
            "preflight",
            "--config",
            output_path,
        ],
        "ensure_session": ensure_session,
    }


if __name__ == "__main__":
    raise SystemExit(main())
