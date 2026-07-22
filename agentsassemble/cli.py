from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from agentsassemble.providers.bridges.claude_code_bridge import CLAUDE_PRINT_MODE_DISABLED_MESSAGE, serve_bridge
from agentsassemble.application.agent_sessions import (
    CODEX_APP_SERVER_SMOKE_COMMANDS,
    clean_agent_session_provider_kind,
    run_codex_app_server_smoke,
)
from agentsassemble.legacy.live_agent.codex_sessions import (
    DEFAULT_INVITE_CONFIG_PATH,
    DEFAULT_LIVE_AGENT_CONFIG_PATH,
    build_codex_live_agent_config,
    build_codex_live_invite_config,
    list_codex_sessions,
    read_agent_config,
    write_agent_config,
)
# Keep these imports public for callers that historically imported validators
# and choice lists from ``agentsassemble.cli``.
from agentsassemble.application.cli.common import (
    LIVE_AGENT_CONNECTION_KIND_CHOICES,
    LIVE_AGENT_DELEGATE_CONNECTION_KIND_CHOICES,
    _hide_subparser_from_help,
    parse_codex_timeout,
    parse_nonnegative_float,
    parse_nonnegative_int,
    parse_positive_int,
)
from agentsassemble.application.cli.core_commands import (
    frontend_info_payload,
    run_frontend_info_command,
    run_release_health_command,
)
from agentsassemble.application.cli.persona_commands import (
    parse_persona_slot_values as _parse_persona_slot_values,
    run_persona_command,
)
from agentsassemble.application.cli.api_commands import run_api_call_command
from agentsassemble.application.cli.provider_commands import (
    run_providers_command as _run_providers_command,
)
from agentsassemble.application.cli.room_commands import (
    RoomCliRuntime,
    run_room_command as _run_room_command,
)
from agentsassemble.legacy.meeting.cli.commands import (
    run_lobby_command,
    run_memory_capsule_command,
)
from agentsassemble.legacy.room.cli_commands import run_legacy_room_command
from agentsassemble.legacy.live_agent.cli.mcp_commands import run_mcp_command
from agentsassemble.legacy.live_agent.cli.commands import (
    LegacyLiveAgentCliRuntime,
    run_live_agent_command as _run_live_agent_command,
)
from agentsassemble.legacy.live_agent.cli.common import (
    LIVE_AGENT_RESIDENT_CONNECTION_KIND_CHOICES,
    _add_session_auto_restart_args,
    _add_session_finalize_after_rounds_arg,
    _add_session_readiness_wait_args,
    parse_session_smoke_lobby_probe_count,
    parse_session_smoke_soak_cycle_count,
    parse_session_smoke_soak_interval_seconds,
)
from agentsassemble.legacy.live_agent.cli.presence_commands import (
    LegacyPresenceCliRuntime,
    leave_payload as _leave_payload,
    run_legacy_presence_command,
)
from agentsassemble.legacy.live_agent.cli.operations_commands import (
    LegacyOperationsCliRuntime,
    run_legacy_operations_command,
)
from agentsassemble.legacy.live_agent.cli.meeting_commands import (
    LegacyMeetingCliRuntime,
    MAX_LIVE_AGENT_SEQUENCE_TURNS,
    run_legacy_meeting_command,
)
from agentsassemble.legacy.live_agent.cli.discovery_commands import (
    LegacyDiscoveryCliRuntime,
    _format_live_agent_continuity_proof,
    _format_live_agent_continuity_proof_group,
    _live_agent_continuity_proof_group_exit_code,
    run_legacy_discovery_command,
    write_live_agent_discovery_outputs as _owned_write_live_agent_discovery_outputs,
)
from agentsassemble.legacy.live_agent.cli.room_interaction_commands import (
    LegacyRoomInteractionCliRuntime,
    run_legacy_room_interaction_command,
)
from agentsassemble.legacy.live_agent.cli.delegate_commands import (
    LegacyDelegateCliRuntime,
    run_delegate_subprocess as _owned_run_delegate_subprocess,
    run_legacy_delegate_command,
)
from agentsassemble.legacy.live_agent.cli.resident_commands import (
    LegacyResidentCliRuntime,
    run_legacy_resident_command,
    run_legacy_resident_group_command,
)
from agentsassemble.legacy.live_agent.cli.resident_runtime import (
    ApiCatalogCommandRunner as _ApiCatalogCommandRunner,
    resident_workspace_cwd as _owned_resident_workspace_cwd,
    validate_resident_config as _owned_validate_resident_config,
)
from agentsassemble.legacy.live_agent.cli.resident_session_runners import (
    JsonlLiveSessionCommandRunner as _JsonlLiveSessionCommandRunner,
    TerminalLiveSessionCommandRunner as _TerminalLiveSessionCommandRunner,
)
from agentsassemble.legacy.live_agent.cli.resident_process import (
    install_resident_shutdown_signal_handlers as _owned_install_resident_shutdown_signal_handlers,
    process_group_pid as _owned_process_group_pid,
    self_service_exit_error as _owned_self_service_exit_error,
    self_service_process_env as _owned_self_service_process_env,
    self_service_room_command_env as _owned_self_service_room_command_env,
    send_process_stop_signal as _owned_send_process_stop_signal,
    stop_signal as _owned_stop_signal,
    supports_process_groups as _owned_supports_process_groups,
    terminate_process as _owned_terminate_process,
)
from agentsassemble.legacy.live_agent.cli.resident_process_runners import (
    LocalCliCommandRunner as _OwnedLocalCliCommandRunner,
    SelfServiceResidentSupervisor as _OwnedSelfServiceResidentSupervisor,
)
from agentsassemble.legacy.live_agent.cli.resident_runner_factory import (
    command_runner_for_config as _owned_command_runner_for_config,
)
from agentsassemble.legacy.live_agent.cli.resident_ws import (
    config_with_joined_room_session as _owned_config_with_joined_room_session,
    joined_room_session_token as _owned_joined_room_session_token,
    run_ws_group_resident as _owned_run_ws_group_resident,
    run_ws_resident_command as _owned_run_ws_resident_command,
)
from agentsassemble.legacy.live_agent.cli.heartbeat import (
    clean_heartbeat_attention as _owned_clean_heartbeat_attention,
    heartbeat_payload as _owned_heartbeat_payload,
    is_unreplaced_template_placeholder as _owned_is_unreplaced_template_placeholder,
)
from agentsassemble.legacy.live_agent.cli.session_commands import (
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
from agentsassemble.legacy.live_agent.cli.process_commands import (
    LegacyProcessCliRuntime,
    run_legacy_process_command,
)
from agentsassemble.legacy.live_agent.cli.smoke_commands import LegacySmokeCliRuntime, run_legacy_smoke_command
from agentsassemble.legacy.live_agent.cli.command_format import (
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
from agentsassemble.diagnostics.cli import (
    DiagnosticCliRuntime,
    run_diagnostic_command,
)
from agentsassemble.config import load_council_config
from agentsassemble.gui import serve_gui
from agentsassemble.legacy.live_agent.state import (
    _looks_sensitive_presence_error,
)
from agentsassemble.diagnostics.live_cli_smoke import DEFAULT_LIVE_CLI_SMOKE_CONFIG
from agentsassemble.application.room_native_cli_smoke import run_room_native_cli_smoke
from agentsassemble.application.room_repository_factory import RoomRepositoryUnavailable
from agentsassemble.legacy.live_agent.runtime.preflight import preflight_live_agent_config, resident_config_setup_error
from agentsassemble.legacy.live_agent.runtime.processes import clean_live_agent_group_id
from agentsassemble.legacy.meeting.core.events import clean_lobby_text
from agentsassemble.legacy.meeting.support.live_meeting_memory import compact_live_meeting_memory
from agentsassemble.live_agent_runner import (
    LiveAgentRunner,
    ResidentAgentConfig,
    config_from_args,
    load_group_configs,
    official_turn_request_candidate,
    should_reply_to_event,
)
from agentsassemble.legacy.live_agent.runtime.timing import DEFAULT_LIVE_AGENT_POLL_INTERVAL, live_agent_poll_sleep_seconds
from agentsassemble.legacy.live_agent.runtime.smoke import (
    MAX_SESSION_SMOKE_LOBBY_PROBES,
    MAX_SESSION_SMOKE_SOAK_CYCLES,
    MAX_SESSION_SMOKE_SOAK_INTERVAL_SECONDS,
    LiveAgentSmokeFailed,
    run_live_agent_smoke,
)
from agentsassemble.legacy.live_agent.runtime.sessions import session_ensure_action
from agentsassemble.legacy.meeting.core.runner import run_demo_meeting
from agentsassemble.models import ENGAGEMENT_MODE_CHOICES
from agentsassemble.web.cli_errors import CliHttpError
from agentsassemble.diagnostics.provider_health import provider_health_report


MAX_READINESS_PROBE_AGENTS = 10
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

    from agentsassemble.application.cli.core import register_core_parsers
    from agentsassemble.legacy.live_agent.cli.parser import register_live_agent_parsers
    from agentsassemble.application.cli.persona import register_persona_parsers
    from agentsassemble.application.cli.room import register_room_parsers
    from agentsassemble.legacy.live_agent.cli.sessions import register_sessions_parsers

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


def run_providers_command(args: argparse.Namespace) -> int:
    return _run_providers_command(args, runtime=_diagnostic_cli_runtime())


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


def run_live_agent_command(args: argparse.Namespace) -> int:
    return _run_live_agent_command(args, runtime=_legacy_live_agent_cli_runtime())


def _legacy_live_agent_cli_runtime() -> LegacyLiveAgentCliRuntime:
    return LegacyLiveAgentCliRuntime(
        request_json=lambda *call_args, **call_kwargs: _request_json(*call_args, **call_kwargs),
        server_url=_server_url,
        heartbeat_payload=_heartbeat_payload,
        session_command=lambda args: run_legacy_session_command(
            args, runtime=_legacy_session_cli_runtime()
        ),
        process_command=lambda args: run_legacy_process_command(
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
        ),
        smoke_command=lambda args: run_legacy_smoke_command(
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
        ),
        diagnostic_command=lambda args: run_diagnostic_command(
            args, runtime=_diagnostic_cli_runtime()
        ),
        presence_command=lambda args: run_legacy_presence_command(
            args,
            runtime=LegacyPresenceCliRuntime(
                request_json=lambda *call_args, **call_kwargs: _request_json(
                    *call_args, **call_kwargs
                ),
                server_url=_server_url,
            ),
        ),
        operations_command=lambda args: run_legacy_operations_command(
            args,
            runtime=LegacyOperationsCliRuntime(
                request_json=lambda *call_args, **call_kwargs: _request_json(
                    *call_args, **call_kwargs
                ),
                server_url=_server_url,
                monotonic=lambda: time.monotonic(),
                sleep=lambda seconds: time.sleep(seconds),
                is_wait_timeout=_is_live_agent_wait_timeout,
            ),
        ),
        meeting_command=lambda args: run_legacy_meeting_command(
            args,
            runtime=LegacyMeetingCliRuntime(
                request_json=lambda *call_args, **call_kwargs: _request_json(
                    *call_args, **call_kwargs
                ),
                server_url=_server_url,
                operation_http_timeout=_operation_http_timeout,
            ),
        ),
        discovery_command=lambda args: run_legacy_discovery_command(
            args,
            runtime=LegacyDiscoveryCliRuntime(
                request_json=lambda *call_args, **call_kwargs: _request_json(
                    *call_args, **call_kwargs
                ),
                server_url=_server_url,
                monotonic=lambda: time.monotonic(),
                sleep=lambda seconds: time.sleep(seconds),
                is_wait_timeout=_is_live_agent_wait_timeout,
                ensure_session_run=_ensure_live_agent_session_run,
                setup_error_checker=_resident_config_setup_error,
                preflight_config=preflight_live_agent_config,
                write_discovery_outputs=_write_live_agent_discovery_outputs,
            ),
        ),
        room_interaction_command=lambda args: run_legacy_room_interaction_command(
            args,
            runtime=LegacyRoomInteractionCliRuntime(
                request_json=lambda *call_args, **call_kwargs: _request_json(
                    *call_args, **call_kwargs
                ),
                server_url=_server_url,
                monotonic=lambda: time.monotonic(),
                sleep=lambda seconds: time.sleep(seconds),
            ),
        ),
        handlers={
            "delegate": _run_live_agent_delegate,
            "run": _run_live_agent_resident,
            "run-group": _run_live_agent_group,
        },
        runnable_commands=LEGACY_LIVE_AGENT_RUNNABLE_COMMANDS,
    )


def _heartbeat_payload(args: argparse.Namespace) -> dict[str, object]:
    return _owned_heartbeat_payload(args)


def _clean_heartbeat_attention(value: object) -> str:
    return _owned_clean_heartbeat_attention(value)


def _is_unreplaced_template_placeholder(value: object) -> bool:
    return _owned_is_unreplaced_template_placeholder(value)



def _joined_room_session_token(joined: object) -> str:
    return _owned_joined_room_session_token(joined)


def _config_with_joined_room_session(config: ResidentAgentConfig, joined: object) -> ResidentAgentConfig:
    return _owned_config_with_joined_room_session(config, joined)


def _run_ws_resident_command(args: argparse.Namespace, config: ResidentAgentConfig) -> int:
    return _owned_run_ws_resident_command(
        args,
        config,
        command_runner_for_config=lambda *call_args, **call_kwargs: _command_runner_for_config(
            *call_args,
            **call_kwargs,
        ),
        install_shutdown_handlers=lambda callback: _install_resident_shutdown_signal_handlers(callback),
        close_command_runner=lambda runner: _close_command_runner(runner),
    )


def _run_live_agent_resident(args: argparse.Namespace) -> int:
    return run_legacy_resident_command(args, runtime=_legacy_resident_cli_runtime())


def _run_ws_group_resident(config) -> int:
    return _owned_run_ws_group_resident(
        config,
        command_runner_for_config=lambda *call_args, **call_kwargs: _command_runner_for_config(
            *call_args,
            **call_kwargs,
        ),
        close_command_runner=lambda runner: _close_command_runner(runner),
    )


def _run_live_agent_group(args: argparse.Namespace) -> int:
    return run_legacy_resident_group_command(args, runtime=_legacy_resident_cli_runtime())


def _legacy_resident_cli_runtime() -> LegacyResidentCliRuntime:
    return LegacyResidentCliRuntime(
        config_from_args=lambda args: config_from_args(args),
        load_group_configs=lambda *args, **kwargs: load_group_configs(*args, **kwargs),
        setup_error=lambda config: _resident_config_setup_error(config),
        run_ws_resident=lambda args, config: _run_ws_resident_command(args, config),
        supervisor_factory=lambda *args, **kwargs: _SelfServiceResidentSupervisor(*args, **kwargs),
        command_runner_for_config=lambda *args, **kwargs: _command_runner_for_config(*args, **kwargs),
        live_agent_runner_factory=lambda *args, **kwargs: LiveAgentRunner(*args, **kwargs),
        request_json=lambda *args, **kwargs: _request_json(*args, **kwargs),
        sleep=lambda seconds: time.sleep(seconds),
        install_shutdown_handlers=lambda callback: _install_resident_shutdown_signal_handlers(callback),
        close_command_runner=lambda runner: _close_command_runner(runner),
        group_config_errors=lambda configs: _resident_group_config_errors(configs),
        validate_config=lambda config: _validate_resident_config(config),
        run_ws_group_resident=lambda config: _run_ws_group_resident(config),
        should_heartbeat_worker_error=lambda config, error: _should_heartbeat_resident_worker_error(
            config,
            error,
        ),
        heartbeat_worker_error=lambda config, error: _heartbeat_resident_worker_error(config, error),
    )


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


def _write_live_agent_discovery_outputs(
    args: argparse.Namespace,
    *,
    session_bundle: bool,
) -> tuple[Path | None, dict[str, object]]:
    return _owned_write_live_agent_discovery_outputs(
        args,
        session_bundle=session_bundle,
    )


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


def _run_live_agent_delegate(args: argparse.Namespace) -> int:
    return run_legacy_delegate_command(
        args,
        runtime=LegacyDelegateCliRuntime(
            request_json=lambda *call_args, **call_kwargs: _request_json(
                *call_args, **call_kwargs
            ),
            server_url=_server_url,
            run_delegate_command=lambda command, prompt, *, timeout_seconds: _run_delegate_command(
                command,
                prompt,
                timeout_seconds=timeout_seconds,
            ),
        ),
    )


class _SelfServiceResidentSupervisor(_OwnedSelfServiceResidentSupervisor):
    def __init__(
        self,
        config: ResidentAgentConfig,
        *,
        request_json,
        sleep_fn,
        stop_event: threading.Event | None = None,
        isolate_process_group: bool = True,
    ) -> None:
        super().__init__(
            config,
            request_json=request_json,
            sleep_fn=sleep_fn,
            process_env=lambda resident_config: _self_service_process_env(resident_config),
            process_factory=lambda *args, **kwargs: subprocess.Popen(*args, **kwargs),
            terminate_process=lambda process: _terminate_process(process),
            supports_process_groups=lambda: _supports_process_groups(),
            server_url=_server_url,
            monotonic=lambda: time.monotonic(),
            exit_error=_self_service_exit_error,
            stop_event=stop_event,
            isolate_process_group=isolate_process_group,
        )


class _LocalCliCommandRunner(_OwnedLocalCliCommandRunner):
    def __init__(self) -> None:
        super().__init__(
            process_factory=lambda *args, **kwargs: subprocess.Popen(*args, **kwargs),
            terminate_process=lambda process: _terminate_process(process),
            supports_process_groups=lambda: _supports_process_groups(),
        )


def _self_service_process_env(config: ResidentAgentConfig) -> dict[str, str]:
    return _owned_self_service_process_env(
        config,
        environ=os.environ,
        executable=sys.executable,
    )


def _self_service_room_command_env(config: ResidentAgentConfig) -> dict[str, str]:
    return _owned_self_service_room_command_env(config, executable=sys.executable)


def _self_service_exit_error(return_code: int) -> str:
    return _owned_self_service_exit_error(return_code)


def _terminate_process(process: subprocess.Popen) -> None:
    _owned_terminate_process(
        process,
        send_stop_signal=lambda target, stop_signal: _send_process_stop_signal(
            target,
            stop_signal,
            force=False,
        ),
        send_kill_signal=lambda target, stop_signal: _send_process_stop_signal(
            target,
            stop_signal,
            force=True,
        ),
        term_signal=_stop_signal("SIGTERM"),
        kill_signal=_stop_signal("SIGKILL"),
    )


def _send_process_stop_signal(process: subprocess.Popen, stop_signal: int | None, *, force: bool) -> None:
    _owned_send_process_stop_signal(
        process,
        stop_signal,
        force=force,
        process_group_pid=_process_group_pid(process),
    )


def _process_group_pid(process: subprocess.Popen) -> int | None:
    return _owned_process_group_pid(
        process,
        process_groups_supported=_supports_process_groups(),
    )


def _supports_process_groups() -> bool:
    return _owned_supports_process_groups()


def _stop_signal(name: str) -> int | None:
    return _owned_stop_signal(name, signal_module=signal)


def _install_resident_shutdown_signal_handlers(on_shutdown):
    return _owned_install_resident_shutdown_signal_handlers(
        on_shutdown,
        signal_module=signal,
        threading_module=threading,
    )


def _validate_resident_config(config: ResidentAgentConfig) -> None:
    _owned_validate_resident_config(config)


def _command_runner_for_config(config: ResidentAgentConfig, *, output_root: str = ""):
    return _owned_command_runner_for_config(
        config,
        output_root=output_root,
        local_cli_runner_factory=lambda: _LocalCliCommandRunner(),
    )


def _resident_workspace_cwd(config: ResidentAgentConfig) -> Path:
    return _owned_resident_workspace_cwd(config)


def _close_command_runner(command_runner) -> None:
    close = getattr(command_runner, "close", None)
    if close is not None:
        close()


def _run_delegate_command(command: list[str], prompt: str, *, timeout_seconds: int) -> str:
    return _owned_run_delegate_subprocess(command, prompt, timeout_seconds=timeout_seconds)


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
    legacy_result = run_legacy_room_command(args)
    if legacy_result is not None:
        return legacy_result
    return _run_room_command(
        args,
        runtime=RoomCliRuntime(
            request_json=_request_json,
            server_url=_server_url,
            clean_text=clean_lobby_text,
            run_codex_smoke=run_codex_app_server_smoke,
            run_native_smoke=run_room_native_cli_smoke,
            codex_smoke_commands=CODEX_APP_SERVER_SMOKE_COMMANDS,
            default_live_cli_smoke_config=DEFAULT_LIVE_CLI_SMOKE_CONFIG,
        ),
    )


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
