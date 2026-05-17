from __future__ import annotations

import argparse
import json
import math
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from agentsassemble.bridges.claude_code_bridge import serve_bridge
from agentsassemble.codex_sessions import (
    DEFAULT_INVITE_CONFIG_PATH,
    build_codex_live_invite_config,
    list_codex_sessions,
    read_agent_config,
    write_agent_config,
)
from agentsassemble.config import load_council_config
from agentsassemble.gui import serve_gui
from agentsassemble.live_agent_preflight import preflight_live_agent_config
from agentsassemble.live_agent_runner import (
    LiveAgentRunner,
    RemoteBridgeResidentCommandRunner,
    ResidentAgentConfig,
    SUPPORTED_RESIDENT_CONNECTION_KINDS,
    config_from_args,
    load_group_configs,
    resident_connection_kind_error,
)
from agentsassemble.live_agent_smoke import LiveAgentSmokeFailed, run_live_agent_smoke
from agentsassemble.live_session_transport import JsonlLiveSession
from agentsassemble.meeting import run_demo_meeting
from agentsassemble.provider_health import provider_health_report


LIVE_AGENT_CONNECTION_KIND_CHOICES = ["codex_resume", "local_cli", "live_session", "remote_bridge", "manual"]
LIVE_AGENT_DELEGATE_CONNECTION_KIND_CHOICES = ["codex_resume", "local_cli", "remote_bridge", "manual"]
LIVE_AGENT_RESIDENT_CONNECTION_KIND_CHOICES = list(SUPPORTED_RESIDENT_CONNECTION_KINDS)


def parse_codex_timeout(value: str) -> int | None:
    if value.casefold() in {"none", "off", "unlimited", "0"}:
        return None
    timeout = int(value)
    if timeout < 0:
        raise argparse.ArgumentTypeError("timeout must be positive, 0, or none")
    return timeout


def parse_nonnegative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("value must be non-negative")
    return parsed


def parse_nonnegative_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0:
        raise argparse.ArgumentTypeError("value must be a finite non-negative number")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="assemble")
    subparsers = parser.add_subparsers(dest="command", required=True)

    demo = subparsers.add_parser("demo", help="Run the canned v0 council demo.")
    demo.add_argument("--adapter", choices=["mock", "codex", "codex-live"], default="mock")
    demo.add_argument("--output-root", default=".agentsassemble")
    demo.add_argument(
        "--codex-timeout",
        type=parse_codex_timeout,
        default=None,
        help="Seconds per Codex call. Use 'none' or omit for no forced timeout.",
    )
    demo.add_argument("--no-codex-search", action="store_true")
    demo.add_argument("--research-depth", choices=["smoke", "standard", "deep"], default="smoke")
    demo.add_argument("--council-config", default=None, help="Optional JSON file describing the meeting topic and roles.")
    demo.add_argument("--agent-config", default=None, help="Optional JSON file with host-approved providers, permissions, and agent bindings.")
    demo.add_argument(
        "--meeting-mode",
        choices=["debate", "free-chat"],
        default=None,
        help="Run a moderated debate or a non-official free-chat room.",
    )
    demo.add_argument(
        "--moderator",
        choices=["on", "off"],
        default=None,
        help="Enable or disable moderator synthesis for debate mode.",
    )
    demo.add_argument("--follow-up-of", default=None, help="Optional parent meeting id for a follow-up council.")
    demo.add_argument("--follow-up-from", default=None, help="Optional parent meeting directory to reopen as a follow-up council.")
    demo.add_argument("--follow-up-note", default=None, help="Optional note explaining what the follow-up should reopen or continue.")
    demo.add_argument(
        "--research-steering",
        default=None,
        help="Optional user-preferred angle to investigate in extra detail without forcing the conclusion.",
    )

    gui = subparsers.add_parser("gui", help="Run the local browser GUI.")
    gui.add_argument("--host", default="127.0.0.1")
    gui.add_argument("--port", type=int, default=8765)
    gui.add_argument("--output-root", default=".agentsassemble")

    bridge = subparsers.add_parser("claude-bridge", help="Run a friend-owned Claude Code bridge.")
    bridge.add_argument("--host", default="127.0.0.1")
    bridge.add_argument("--port", type=int, default=8777)
    bridge.add_argument("--token", required=True)
    bridge.add_argument("--command", dest="bridge_command", default="claude")

    providers = subparsers.add_parser("providers", help="Inspect provider runtime configs.")
    provider_subparsers = providers.add_subparsers(dest="providers_command", required=True)
    provider_health = provider_subparsers.add_parser(
        "health",
        help="Check provider runtime config without starting a meeting.",
    )
    provider_health.add_argument("--config", required=True, help="Agent runtime config path.")
    provider_health.add_argument(
        "--probe",
        choices=["none", "local", "bridge"],
        default="none",
        dest="probe_mode",
        help="Optional runtime probe mode. 'local' checks loopback OpenAI-compatible /models; 'bridge' checks remote bridge health.",
    )
    provider_health.add_argument(
        "--probe-timeout",
        type=parse_nonnegative_float,
        default=2.0,
        help="Seconds to wait for an opt-in local or bridge provider probe.",
    )
    provider_health.add_argument("--json", action="store_true", dest="as_json", help="Print a machine-readable provider health report.")

    live_server = argparse.ArgumentParser(add_help=False)
    live_server.add_argument("--server", default="http://127.0.0.1:8765", help="AgentsAssemble GUI server URL.")

    live_agent = subparsers.add_parser("live-agent", help="Connect an external live agent to a GUI room.")
    live_agent_subparsers = live_agent.add_subparsers(dest="live_agent_command", required=True)

    live_register = live_agent_subparsers.add_parser("register", parents=[live_server], help="Register a live agent.")
    live_register.add_argument("--agent-id", required=True)
    live_register.add_argument("--display-name", default="")
    live_register.add_argument("--provider-kind", default="manual")
    live_register.add_argument("--connection-kind", choices=LIVE_AGENT_CONNECTION_KIND_CHOICES, default="manual")
    live_register.add_argument("--session-id", default="")
    live_register.add_argument("--endpoint", default="")
    live_register.add_argument("--meeting-id", default="")
    live_register.add_argument("--engagement-mode", default="mentioned")

    live_heartbeat = live_agent_subparsers.add_parser("heartbeat", parents=[live_server], help="Update live agent presence.")
    live_heartbeat.add_argument("--agent-id", required=True)
    live_heartbeat.add_argument("--status", choices=["online", "working", "offline", "error"], default="online")
    live_heartbeat.add_argument("--last-error", default=None)
    live_heartbeat.add_argument("--last-reply-at", default=None)
    live_heartbeat.add_argument("--last-observed-event-id", default=None)

    live_say = live_agent_subparsers.add_parser("say", parents=[live_server], help="Post a lobby message as a live agent.")
    live_say.add_argument("--agent-id", required=True)
    live_say.add_argument("message", nargs="+")

    live_room = live_agent_subparsers.add_parser("room", parents=[live_server], help="Read the live room snapshot for an agent.")
    live_room.add_argument("--agent-id", required=True)

    live_health = live_agent_subparsers.add_parser("health", parents=[live_server], help="Read live-agent room health.")
    live_health.add_argument("--json", action="store_true", dest="as_json", help="Print the raw JSON health payload.")
    live_health.add_argument(
        "--fail-on-degraded",
        action="store_true",
        help="Exit 1 when the health status is not ok.",
    )

    live_preflight = live_agent_subparsers.add_parser(
        "preflight",
        help="Check a resident live-agent config without executing provider commands.",
    )
    live_preflight.add_argument("--config", required=True, help="Resident group config path.")
    live_preflight.add_argument("--server", default=None, help="Optional room server URL override for the config.")
    live_preflight.add_argument("--json", action="store_true", dest="as_json", help="Print a machine-readable preflight report.")

    live_smoke = live_agent_subparsers.add_parser(
        "smoke",
        parents=[live_server],
        help="Run credential-free local smoke against a running GUI room.",
    )
    live_smoke.add_argument("--group-id", default="", help="Optional supervised process group id for the smoke run.")
    live_smoke.add_argument("--timeout", type=parse_nonnegative_float, default=12.0, help="Seconds to wait for fake agent replies.")
    live_smoke.add_argument("--json", action="store_true", dest="as_json", help="Print a machine-readable smoke result.")

    live_doctor = live_agent_subparsers.add_parser(
        "doctor",
        parents=[live_server],
        help="Run health plus credential-free smoke readiness checks against a GUI room.",
    )
    live_doctor.add_argument("--group-id", default="", help="Optional supervised process group id for the smoke check.")
    live_doctor.add_argument("--timeout", type=parse_nonnegative_float, default=12.0, help="Seconds to wait for fake agent replies.")
    live_doctor.add_argument("--json", action="store_true", dest="as_json", help="Print a machine-readable readiness result.")

    live_delegate = live_agent_subparsers.add_parser(
        "delegate",
        parents=[live_server],
        help="Run a local CLI once using the room snapshot, then post its reply.",
    )
    live_delegate.add_argument("--agent-id", required=True)
    live_delegate.add_argument("--display-name", default="")
    live_delegate.add_argument("--provider-kind", default="local_cli")
    live_delegate.add_argument("--connection-kind", choices=LIVE_AGENT_DELEGATE_CONNECTION_KIND_CHOICES, default="local_cli")
    live_delegate.add_argument("--session-id", default="")
    live_delegate.add_argument("--endpoint", default="")
    live_delegate.add_argument("--meeting-id", default="")
    live_delegate.add_argument("--engagement-mode", default="mentioned")
    live_delegate.add_argument("--timeout", type=int, default=120)
    live_delegate.add_argument("--command", dest="delegate_command", nargs=argparse.REMAINDER, required=True)

    live_run = live_agent_subparsers.add_parser("run", parents=[live_server], help="Run a resident local CLI live agent.")
    live_run.add_argument("--agent-id", required=True)
    live_run.add_argument("--display-name", default="")
    live_run.add_argument("--provider-kind", default="local_cli")
    live_run.add_argument("--connection-kind", choices=LIVE_AGENT_RESIDENT_CONNECTION_KIND_CHOICES, default="local_cli")
    live_run.add_argument("--session-id", default="")
    live_run.add_argument("--endpoint", default="")
    live_run.add_argument("--auth-ref", default="")
    live_run.add_argument("--meeting-id", default="")
    live_run.add_argument("--engagement-mode", default="always")
    live_run.add_argument("--timeout", type=int, default=120)
    live_run.add_argument("--poll-interval", type=float, default=2.0)
    live_run.add_argument("--heartbeat-interval", type=float, default=30.0)
    live_run.add_argument("--cooldown", type=float, default=5.0)
    live_run.add_argument("--max-chain-depth", type=int, default=1)
    live_run.add_argument("--max-ticks", type=int, default=0)
    live_run.add_argument("--command", dest="resident_command", nargs=argparse.REMAINDER, default=[])

    live_group = live_agent_subparsers.add_parser("run-group", help="Run multiple resident local CLI live agents.")
    live_group.add_argument("--config", required=True)
    live_group.add_argument("--server", default=None)
    live_group.add_argument("--max-ticks", type=int, default=None)

    live_processes = live_agent_subparsers.add_parser("processes", help="Manage supervised live-agent process groups.")
    live_process_subparsers = live_processes.add_subparsers(dest="live_agent_process_command", required=True)

    live_process_list = live_process_subparsers.add_parser("list", parents=[live_server], help="List supervised live-agent process groups.")
    live_process_list.add_argument("--json", action="store_true", dest="as_json", help="Print the raw JSON process payload.")

    live_process_start = live_process_subparsers.add_parser("start", parents=[live_server], help="Start a supervised live-agent run-group.")
    live_process_start.add_argument("--config", required=True, help="Resident group config path.")
    live_process_start.add_argument("--group-id", default="")
    live_process_start.add_argument("--auto-restart", action="store_true")
    live_process_start.add_argument("--max-restarts", type=parse_nonnegative_int, default=0)
    live_process_start.add_argument("--restart-backoff-seconds", type=parse_nonnegative_float, default=5.0)
    live_process_start.add_argument("--json", action="store_true", dest="as_json", help="Print the raw JSON process payload.")

    live_process_stop = live_process_subparsers.add_parser("stop", parents=[live_server], help="Stop a supervised live-agent process group.")
    live_process_stop.add_argument("group_id")
    live_process_stop.add_argument("--json", action="store_true", dest="as_json", help="Print the raw JSON process payload.")

    live_process_restart = live_process_subparsers.add_parser("restart", parents=[live_server], help="Restart a supervised live-agent process group.")
    live_process_restart.add_argument("group_id")
    live_process_restart.add_argument("--json", action="store_true", dest="as_json", help="Print the raw JSON process payload.")

    sessions = subparsers.add_parser("sessions", help="Inspect and invite Codex CLI live sessions.")
    session_subparsers = sessions.add_subparsers(dest="sessions_command", required=True)

    session_list = session_subparsers.add_parser("list", help="List recent Codex CLI sessions.")
    session_list.add_argument("--limit", type=int, default=20)
    session_list.add_argument("--json", action="store_true", dest="as_json")

    session_invite = session_subparsers.add_parser("invite", help="Bind a Codex CLI session to a meeting role.")
    session_invite.add_argument("session_id")
    session_invite.add_argument("--role", required=True, dest="role_id")
    session_invite.add_argument("--output", default=str(DEFAULT_INVITE_CONFIG_PATH))

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
        serve_gui(host=args.host, port=args.port, output_root=Path(args.output_root))
        return 0
    if args.command == "claude-bridge":
        serve_bridge(host=args.host, port=args.port, token=args.token, command=args.bridge_command)
        return 0
    if args.command == "live-agent":
        return run_live_agent_command(args)
    if args.command == "providers":
        return run_providers_command(args)
    if args.command == "sessions":
        return run_sessions_command(args)

    return 1


def run_providers_command(args: argparse.Namespace) -> int:
    try:
        if args.providers_command == "health":
            return _run_provider_health(args)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 1


def run_live_agent_command(args: argparse.Namespace) -> int:
    try:
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
            response = _request_json(_server_url(args.server, "/api/live-agents"), method="POST", payload=payload)
            agent = response.get("agent", {}) if isinstance(response.get("agent"), dict) else {}
            print(f"Registered {agent.get('agent_id') or args.agent_id}")
            return 0
        if args.live_agent_command == "heartbeat":
            agent_id = urllib.parse.quote(args.agent_id, safe="")
            response = _request_json(
                _server_url(args.server, f"/api/live-agents/{agent_id}/heartbeat"),
                method="POST",
                payload=_heartbeat_payload(args),
            )
            agent = response.get("agent", {}) if isinstance(response.get("agent"), dict) else {}
            print(f"{agent.get('agent_id') or args.agent_id}: {agent.get('status') or args.status}")
            return 0
        if args.live_agent_command == "say":
            agent_id = urllib.parse.quote(args.agent_id, safe="")
            response = _request_json(
                _server_url(args.server, f"/api/live-agents/{agent_id}/lobby"),
                method="POST",
                payload={"message": " ".join(args.message), "kind": "message"},
            )
            event = response.get("event", {}) if isinstance(response.get("event"), dict) else {}
            print(f"Posted {event.get('id') or 'lobby message'}")
            return 0
        if args.live_agent_command == "room":
            agent_id = urllib.parse.quote(args.agent_id, safe="")
            response = _request_json(_server_url(args.server, f"/api/live-agents/{agent_id}/room"))
            print(json.dumps(response, ensure_ascii=False, indent=2))
            return 0
        if args.live_agent_command == "health":
            return _run_live_agent_health(args)
        if args.live_agent_command == "preflight":
            return _run_live_agent_preflight(args)
        if args.live_agent_command == "smoke":
            return _run_live_agent_smoke(args)
        if args.live_agent_command == "doctor":
            return _run_live_agent_doctor(args)
        if args.live_agent_command == "processes":
            return _run_live_agent_processes(args)
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
        "last_reply_at": getattr(args, "last_reply_at", None),
        "last_observed_event_id": getattr(args, "last_observed_event_id", None),
    }
    for key, value in optional_fields.items():
        if value is not None:
            payload[key] = value
    return payload


def _run_live_agent_resident(args: argparse.Namespace) -> int:
    config = config_from_args(args)
    _validate_resident_config(config)
    command_runner = _command_runner_for_config(config)
    runner = LiveAgentRunner(
        config,
        request_json=_request_json,
        command_runner=command_runner,
        sleep_fn=time.sleep,
    )
    replies = 0
    try:
        replies = runner.run()
    finally:
        _close_command_runner(command_runner)
    print(f"Resident agent stopped after posting {replies} replies")
    return 0


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

    def run_agent(config) -> None:
        command_runner = None
        try:
            _validate_resident_config(config)
            command_runner = _command_runner_for_config(config)
            with active_command_runners_lock:
                active_command_runners.append(command_runner)
            runner = LiveAgentRunner(
                config,
                request_json=_request_json,
                command_runner=command_runner,
                sleep_fn=sleep,
                stop_event=stop_event,
            )
            results[config.agent_id] = runner.run()
        except Exception as error:  # pragma: no cover - surfaced through CLI status in integration use
            if stop_event.is_set():
                return
            errors[config.agent_id] = str(error)
            stop_event.set()
            with active_command_runners_lock:
                runners_to_close = list(active_command_runners)
            for active_runner in runners_to_close:
                _close_command_runner(active_runner)
        finally:
            if command_runner is not None:
                _close_command_runner(command_runner)
                with active_command_runners_lock:
                    if command_runner in active_command_runners:
                        active_command_runners.remove(command_runner)

    threads = [threading.Thread(target=run_agent, args=(config,), daemon=True) for config in configs]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    except KeyboardInterrupt:
        stop_event.set()
        with active_command_runners_lock:
            runners_to_close = list(active_command_runners)
        for command_runner in runners_to_close:
            _close_command_runner(command_runner)
        for thread in threads:
            thread.join(timeout=5)
    if errors:
        for agent_id, error in errors.items():
            print(f"{agent_id}: {error}", file=sys.stderr)
        return 2
    total = sum(results.values())
    print(f"Resident group stopped after posting {total} replies")
    return 0


def _resident_group_config_errors(configs: list[ResidentAgentConfig]) -> dict[str, str]:
    errors: dict[str, str] = {}
    for config in configs:
        try:
            _validate_resident_config(config)
            if config.connection_kind == "remote_bridge":
                probe_runner = _command_runner_for_config(config)
                _close_command_runner(probe_runner)
        except Exception as error:
            errors[config.agent_id] = str(error)
    return errors


def _run_live_agent_health(args: argparse.Namespace) -> int:
    payload = _request_json(_server_url(args.server, "/api/live-agent-health"))
    if args.as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(_format_live_agent_health(payload))
    return 1 if args.fail_on_degraded and payload.get("status") != "ok" else 0


def _run_live_agent_preflight(args: argparse.Namespace) -> int:
    report = preflight_live_agent_config(Path(args.config), server_override=args.server)
    if args.as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(_format_live_agent_preflight(report))
    return 0 if report.get("status") == "ok" else 1


def _run_provider_health(args: argparse.Namespace) -> int:
    report = provider_health_report(
        Path(args.config),
        probe_mode=args.probe_mode,
        probe_timeout_seconds=args.probe_timeout,
    )
    if args.as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(_format_provider_health(report))
    return 0 if report.get("status") == "ok" else 1


def _run_live_agent_smoke(args: argparse.Namespace) -> int:
    try:
        result = run_live_agent_smoke(
            server=args.server,
            group_id=args.group_id,
            timeout_seconds=float(args.timeout),
            request_json=_request_json,
        )
    except LiveAgentSmokeFailed as error:
        print(f"smoke failed: {error}", file=sys.stderr)
        return 1
    if args.as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"live-agent smoke ok: {result['group_id']}")
        for reply in result["replies"]:
            print(f"- {reply['actor_id']}: {reply['message']}")
    return 0


def _run_live_agent_doctor(args: argparse.Namespace) -> int:
    payload = _request_json(
        _server_url(args.server, "/api/live-agent-readiness"),
        method="POST",
        payload={"group_id": args.group_id, "timeout": float(args.timeout)},
    )
    if args.as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(_format_live_agent_readiness(payload))
    return 0 if payload.get("status") == "ready" else 1


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


def _format_provider_health(report: dict[str, object]) -> str:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    providers = report.get("providers") if isinstance(report.get("providers"), list) else []
    bindings = report.get("bindings") if isinstance(report.get("bindings"), list) else []
    lines = [
        f"provider health: {report.get('status') or 'unknown'}",
        f"providers: {summary.get('providers', 0)} checked, {summary.get('failed_providers', 0)} failed",
        f"bindings: {summary.get('bindings', 0)} checked, {summary.get('failed_bindings', 0)} failed",
        f"checks failed: {summary.get('checks_failed', 0)}, warnings: {summary.get('warnings', 0)}",
    ]
    for provider in providers:
        if not isinstance(provider, dict) or provider.get("status") == "ok":
            continue
        failed_checks = [
            check
            for check in provider.get("checks", [])
            if isinstance(check, dict) and check.get("status") in {"failed", "warning"}
        ]
        for check in failed_checks:
            lines.append(f"{provider.get('provider_id') or 'unknown'}: {check.get('id')}: {check.get('message')}")
    for binding in bindings:
        if not isinstance(binding, dict) or binding.get("status") == "ok":
            continue
        failed_checks = [
            check
            for check in binding.get("checks", [])
            if isinstance(check, dict) and check.get("status") in {"failed", "warning"}
        ]
        for check in failed_checks:
            lines.append(f"{binding.get('agent_id') or 'unknown'}: {check.get('id')}: {check.get('message')}")
    return "\n".join(lines)


def _format_live_agent_readiness(payload: dict[str, object]) -> str:
    health = payload.get("health") if isinstance(payload.get("health"), dict) else {}
    smoke = payload.get("smoke") if isinstance(payload.get("smoke"), dict) else {}
    agents = health.get("agents") if isinstance(health.get("agents"), dict) else {}
    processes = health.get("processes") if isinstance(health.get("processes"), dict) else {}
    agent_attention = agents.get("attention") if isinstance(agents.get("attention"), list) else []
    process_attention = processes.get("attention") if isinstance(processes.get("attention"), list) else []
    smoke_suffix = str(smoke.get("group_id") or "").strip()
    smoke_label = f"{smoke.get('status') or 'unknown'} {smoke_suffix}".strip()
    lines = [
        f"readiness: {payload.get('status') or 'unknown'}",
        f"health: {health.get('status') or 'unknown'}",
        f"smoke: {smoke_label}",
        f"agent attention: {_attention_summary(agent_attention)}",
        f"process attention: {_attention_summary(process_attention)}",
    ]
    if smoke.get("error"):
        lines.append(f"smoke error: {smoke.get('error')}")
    return "\n".join(lines)


def _format_live_agent_health(payload: dict[str, object]) -> str:
    agents = payload.get("agents") if isinstance(payload.get("agents"), dict) else {}
    processes = payload.get("processes") if isinstance(payload.get("processes"), dict) else {}
    agent_counts = agents.get("counts") if isinstance(agents.get("counts"), dict) else {}
    process_counts = processes.get("counts") if isinstance(processes.get("counts"), dict) else {}
    agent_attention = agents.get("attention") if isinstance(agents.get("attention"), list) else []
    process_attention = processes.get("attention") if isinstance(processes.get("attention"), list) else []
    return "\n".join(
        [
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
    )


def _attention_summary(items: list[object]) -> str:
    cleaned = [str(item) for item in items if str(item)]
    return ", ".join(cleaned) if cleaned else "none"


def _run_live_agent_processes(args: argparse.Namespace) -> int:
    if args.live_agent_process_command == "list":
        payload = _request_json(_server_url(args.server, "/api/live-agent-processes"))
        _print_live_agent_process_payload(payload, as_json=args.as_json)
        return 0
    if args.live_agent_process_command == "start":
        if args.auto_restart and args.max_restarts <= 0:
            raise ValueError("--auto-restart requires --max-restarts greater than 0.")
        payload = {
            "config_path": args.config,
            "server": args.server,
            "auto_restart": args.auto_restart,
            "max_restarts": args.max_restarts,
            "restart_backoff_seconds": args.restart_backoff_seconds,
        }
        if args.group_id:
            payload["group_id"] = args.group_id
        response = _request_json(
            _server_url(args.server, "/api/live-agent-processes/start"),
            method="POST",
            payload=payload,
        )
        _print_live_agent_process_payload(response, as_json=args.as_json, action="start")
        return 0
    if args.live_agent_process_command in {"stop", "restart"}:
        group_id = urllib.parse.quote(args.group_id, safe="")
        response = _request_json(
            _server_url(args.server, f"/api/live-agent-processes/{group_id}/{args.live_agent_process_command}"),
            method="POST",
            payload={},
        )
        _print_live_agent_process_payload(response, as_json=args.as_json, action=args.live_agent_process_command)
        return 0
    return 1


def _print_live_agent_process_payload(payload: dict[str, object], *, as_json: bool, action: str = "list") -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    group = payload.get("group") if isinstance(payload.get("group"), dict) else None
    if group is not None:
        print(_format_live_agent_process_action(group, action))
        return

    groups = payload.get("groups") if isinstance(payload.get("groups"), list) else []
    if not groups:
        print("no live-agent process groups")
        return
    for item in groups:
        if isinstance(item, dict):
            print(_format_live_agent_process_group(item))


def _format_live_agent_process_group(group: dict[str, object]) -> str:
    group_id = str(group.get("group_id") or "unknown")
    status = str(group.get("status") or "unknown")
    pid = group.get("pid")
    pid_text = f"pid {pid}" if pid not in (None, "") else "pid -"
    auto_restart = "auto-restart on" if group.get("auto_restart") else "auto-restart off"
    restart_count = group.get("restart_count", 0)
    max_restarts = group.get("max_restarts", 0)
    config_path = str(group.get("config_path") or "").strip()
    agents = _format_live_agent_process_agents(group.get("agents"))
    last_event = _format_live_agent_process_last_event(group.get("recent_events"))
    suffix_parts = [part for part in (config_path, agents, last_event) if part]
    suffix = f" {'; '.join(suffix_parts)}" if suffix_parts else ""
    return f"{group_id}: {status} ({pid_text}, {auto_restart}, restarts {restart_count}/{max_restarts}){suffix}"


def _format_live_agent_process_action(group: dict[str, object], action: str) -> str:
    group_id = str(group.get("group_id") or "unknown")
    status = str(group.get("status") or "unknown")
    pid = group.get("pid")
    if action == "start":
        return f"Started {group_id} (pid {pid if pid not in (None, '') else '-'})"
    if action == "stop":
        return f"Stopped {group_id} ({status})"
    if action == "restart":
        return f"Restarted {group_id} (pid {pid if pid not in (None, '') else '-'})"
    return _format_live_agent_process_group(group)


def _format_live_agent_process_agents(value: object) -> str:
    if not isinstance(value, list) or not value:
        return ""
    labels = []
    for item in value:
        if not isinstance(item, dict):
            continue
        name = str(item.get("display_name") or item.get("agent_id") or "").strip()
        connection_kind = str(item.get("connection_kind") or "").strip()
        if not name:
            continue
        labels.append(f"{name}/{connection_kind}" if connection_kind else name)
    return f"agents {', '.join(labels)}" if labels else ""


def _format_live_agent_process_last_event(value: object) -> str:
    if not isinstance(value, list) or not value:
        return ""
    for item in reversed(value):
        if not isinstance(item, dict):
            continue
        event_type = str(item.get("event_type") or "").strip()
        if event_type:
            return f"last event {event_type}"
    return ""


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
    reply = _run_delegate_command(args.delegate_command, _delegate_prompt(args, room), timeout_seconds=args.timeout).strip()
    if not reply:
        raise ValueError("Delegate command returned an empty reply.")
    response = _request_json(
        _server_url(args.server, f"/api/live-agents/{agent_id}/lobby"),
        method="POST",
        payload={"message": reply, "kind": "message"},
    )
    _request_json(
        _server_url(args.server, f"/api/live-agents/{agent_id}/heartbeat"),
        method="POST",
        payload={"status": "online"},
    )
    event = response.get("event", {}) if isinstance(response.get("event"), dict) else {}
    print(f"Posted {event.get('id') or 'lobby message'}")
    return 0


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


def _validate_resident_config(config: ResidentAgentConfig) -> None:
    if config.connection_kind not in SUPPORTED_RESIDENT_CONNECTION_KINDS:
        raise ValueError(resident_connection_kind_error())
    if config.connection_kind == "remote_bridge":
        if not config.endpoint:
            raise ValueError("Remote bridge resident requires --endpoint.")
        if not config.auth_ref:
            raise ValueError("Remote bridge resident requires --auth-ref.")
        return
    if config.connection_kind in {"local_cli", "live_session", "codex_resume", "manual"} and not config.command:
        raise ValueError(f"{config.connection_kind} resident requires --command.")


def _command_runner_for_config(config: ResidentAgentConfig):
    if config.connection_kind == "live_session":
        return _JsonlLiveSessionCommandRunner()
    if config.connection_kind == "remote_bridge":
        return RemoteBridgeResidentCommandRunner(config)
    return _LocalCliCommandRunner()


def _close_command_runner(command_runner) -> None:
    close = getattr(command_runner, "close", None)
    if close is not None:
        close()


def _delegate_prompt(args: argparse.Namespace, room: dict[str, object]) -> str:
    events = room.get("lobby_events") if isinstance(room.get("lobby_events"), list) else []
    lines = [
        "You are a live AgentsAssemble participant connected through a local CLI bridge.",
        f"Agent id: {args.agent_id}",
        f"Display name: {args.display_name or args.agent_id}",
        "Reply with one concise lobby message only.",
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


def _request_json(url: str, *, method: str = "GET", payload: dict[str, object] | None = None) -> dict[str, object]:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"} if payload is not None else {}
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            loaded = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raise ValueError(_http_error_message(error)) from error
    return loaded if isinstance(loaded, dict) else {}


def _http_error_message(error: urllib.error.HTTPError) -> str:
    body = error.read().decode("utf-8", errors="replace")
    if body:
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = {}
        if isinstance(payload, dict) and payload.get("error"):
            return str(payload["error"])
        return body.strip()
    return str(error)


def run_sessions_command(args: argparse.Namespace) -> int:
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
            role_ids = [role.id for role in load_council_config().roles]
            output_path = Path(args.output)
            config = build_codex_live_invite_config(
                session_id=args.session_id,
                role_id=args.role_id,
                role_ids=role_ids,
                existing=read_agent_config(output_path),
            )
            write_agent_config(output_path, config)
        except ValueError as error:
            print(f"error: {error}", file=sys.stderr)
            return 2
        print(f"Wrote {output_path}")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
