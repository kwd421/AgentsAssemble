from __future__ import annotations

import argparse
import json
import math
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

from agentsassemble.bridges.claude_code_bridge import serve_bridge
from agentsassemble.codex_resident import CodexResidentCommandRunner
from agentsassemble.codex_sessions import (
    DEFAULT_INVITE_CONFIG_PATH,
    DEFAULT_LIVE_AGENT_CONFIG_PATH,
    build_codex_live_agent_config,
    build_codex_live_invite_config,
    list_codex_sessions,
    read_agent_config,
    write_agent_config,
)
from agentsassemble.config import load_council_config
from agentsassemble.gui import serve_gui
from agentsassemble.live_agents import _looks_sensitive_presence_error
from agentsassemble.live_agent_preflight import preflight_live_agent_config, resident_config_setup_error
from agentsassemble.live_agent_processes import clean_live_agent_group_id
from agentsassemble.live_agent_roster import (
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
    resident_connection_kind_error,
    should_reply_to_event,
)
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
from agentsassemble.provider_health import provider_health_report


LIVE_AGENT_CONNECTION_KIND_CHOICES = [
    "codex_resume",
    "local_cli",
    "live_session",
    "terminal_session",
    "remote_bridge",
    "self_service",
    "manual",
]
LIVE_AGENT_DELEGATE_CONNECTION_KIND_CHOICES = ["codex_resume", "local_cli", "remote_bridge", "manual"]
LIVE_AGENT_RESIDENT_CONNECTION_KIND_CHOICES = list(SUPPORTED_RESIDENT_CONNECTION_KINDS)
MAX_READINESS_PROBE_AGENTS = 10
SESSION_BOUND_PROBE_HTTP_WINDOWS = 25
MAX_LIVE_AGENT_SEQUENCE_TURNS = 12
MAX_LIVE_AGENT_ROUND_BATCH = 8


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


def parse_positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def parse_session_smoke_lobby_probe_count(value: str) -> int:
    parsed = parse_positive_int(value)
    if parsed > MAX_SESSION_SMOKE_LOBBY_PROBES:
        raise argparse.ArgumentTypeError(f"value must be at most {MAX_SESSION_SMOKE_LOBBY_PROBES}")
    return parsed


def parse_session_smoke_soak_cycle_count(value: str) -> int:
    parsed = parse_nonnegative_int(value)
    if parsed > MAX_SESSION_SMOKE_SOAK_CYCLES:
        raise argparse.ArgumentTypeError(f"value must be at most {MAX_SESSION_SMOKE_SOAK_CYCLES}")
    return parsed


def parse_session_smoke_soak_interval_seconds(value: str) -> float:
    parsed = parse_nonnegative_float(value)
    if parsed > MAX_SESSION_SMOKE_SOAK_INTERVAL_SECONDS:
        raise argparse.ArgumentTypeError(f"value must be at most {MAX_SESSION_SMOKE_SOAK_INTERVAL_SECONDS:g}")
    return parsed


def parse_nonnegative_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0:
        raise argparse.ArgumentTypeError("value must be a finite non-negative number")
    return parsed


def _add_session_readiness_wait_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--wait-ready",
        action="store_true",
        help="After the session command returns, poll read-only session readiness until the target is ready.",
    )
    parser.add_argument("--wait-timeout", type=parse_nonnegative_float, default=30.0)
    parser.add_argument("--wait-poll-interval", type=parse_nonnegative_float, default=2.0)


def _add_session_auto_restart_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--auto-restart", action="store_true")
    parser.add_argument("--max-restarts", type=parse_nonnegative_int, default=0)
    parser.add_argument("--restart-backoff-seconds", type=parse_nonnegative_float, default=5.0)
    parser.add_argument("--stale-restart-after-seconds", type=parse_nonnegative_float, default=0.0)


def _add_session_finalize_after_rounds_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--finalize-after-rounds",
        action="store_true",
        help="After successful remaining rounds, finalize resident meeting artifacts.",
    )


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
    gui.add_argument("--live-agent-config", default="", help="Explicit resident group config to autostart after the GUI binds.")
    gui.add_argument("--live-agent-group-id", default="", help="Optional group id for GUI startup autostart.")
    gui.add_argument("--live-agent-auto-restart", action="store_true", help="Enable auto restart for the startup autostart group.")
    gui.add_argument("--live-agent-max-restarts", type=parse_nonnegative_int, default=0)
    gui.add_argument("--live-agent-restart-backoff-seconds", type=parse_nonnegative_float, default=5.0)
    gui.add_argument("--live-agent-stale-restart-after-seconds", type=parse_nonnegative_float, default=0.0)

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
        choices=["none", "local", "bridge", "api"],
        default="none",
        dest="probe_mode",
        help=(
            "Optional runtime probe mode. 'local' checks loopback OpenAI-compatible /models; "
            "'bridge' checks remote bridge health; 'api' checks supported provider model-list endpoints."
        ),
    )
    provider_health.add_argument(
        "--probe-timeout",
        type=parse_nonnegative_float,
        default=2.0,
        help="Seconds to wait for an opt-in local, bridge, or API provider probe.",
    )
    provider_health.add_argument("--json", action="store_true", dest="as_json", help="Print a machine-readable provider health report.")

    memory_capsule = subparsers.add_parser("memory-capsule", help="Inspect importable memory/profile capsules.")
    memory_capsule_subparsers = memory_capsule.add_subparsers(dest="memory_capsule_command", required=True)
    memory_capsule_gate = memory_capsule_subparsers.add_parser(
        "gate",
        help="Validate a memory/profile capsule before it can influence a meeting.",
    )
    memory_capsule_gate.add_argument("--path", required=True, help="Memory capsule directory path.")
    memory_capsule_gate.add_argument("--json", action="store_true", dest="as_json", help="Print a machine-readable gate report.")

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
    live_register.add_argument("--json", action="store_true", dest="as_json", help="Print the raw registration response.")

    live_join_brief = live_agent_subparsers.add_parser(
        "join-brief",
        parents=[live_server],
        help="Generate safe startup commands for an external or manual live agent.",
    )
    live_join_brief.add_argument("--agent-id", required=True)
    live_join_brief.add_argument("--display-name", default="")
    live_join_brief.add_argument("--provider-kind", default="manual")
    live_join_brief.add_argument("--connection-kind", choices=LIVE_AGENT_CONNECTION_KIND_CHOICES, default="manual")
    live_join_brief.add_argument("--meeting-id", default="")
    live_join_brief.add_argument("--engagement-mode", choices=ENGAGEMENT_MODE_CHOICES, default="mentioned")
    live_join_brief.add_argument("--timeout", type=parse_nonnegative_float, default=30.0)
    live_join_brief.add_argument("--poll-interval", type=parse_nonnegative_float, default=2.0)
    live_join_brief.add_argument("--max-chain-depth", type=parse_nonnegative_int, default=1)
    live_join_brief.add_argument("--json", action="store_true", dest="as_json", help="Print a machine-readable join brief.")

    live_list = live_agent_subparsers.add_parser("list", parents=[live_server], help="List live agent roster presence.")
    live_list.add_argument("--json", action="store_true", dest="as_json", help="Print the safe roster response.")
    live_list.add_argument("--meeting-id", default="", help="Limit roster rows to one meeting id.")
    live_list.add_argument("--agent-id", action="append", default=[], dest="agent_ids", help="Limit to an agent id; repeat to include more.")
    live_list.add_argument(
        "--status",
        action="append",
        default=[],
        dest="statuses",
        choices=["online", "working", "offline", "error", "stale"],
        help="Limit to a live-agent status; repeat to include more.",
    )
    live_list.add_argument(
        "--require-match",
        action="store_true",
        help="Exit 1 when the filtered roster returns no live agents.",
    )
    live_list.add_argument(
        "--require-all-agents",
        action="store_true",
        help="Exit 1 when any requested --agent-id is missing from the filtered roster.",
    )
    live_list.add_argument(
        "--fail-on-attention",
        action="store_true",
        help="Exit 1 when any returned live agent is not online or working.",
    )

    live_heartbeat = live_agent_subparsers.add_parser("heartbeat", parents=[live_server], help="Update live agent presence.")
    live_heartbeat.add_argument("--agent-id", required=True)
    live_heartbeat.add_argument("--status", choices=["online", "working", "offline", "error"], default="online")
    live_heartbeat.add_argument("--last-error", default=None)
    live_heartbeat.add_argument("--last-reply-at", default=None)
    live_heartbeat.add_argument("--last-observed-event-id", default=None)
    live_heartbeat.add_argument("--last-observed-live-event-id", default=None)
    live_heartbeat.add_argument("--json", action="store_true", dest="as_json", help="Print the raw heartbeat response.")

    live_return_packet = live_agent_subparsers.add_parser(
        "return-packet",
        parents=[live_server],
        help="Read this live agent's targeted return packet.",
    )
    live_return_packet.add_argument("--agent-id", required=True)
    live_return_packet.add_argument("--meeting-id", default="")
    live_return_packet.add_argument("--source-event-id", required=True)
    live_return_packet.add_argument("--json", action="store_true", dest="as_json", help="Print the raw return-packet payload.")

    live_engagement = live_agent_subparsers.add_parser("engagement", parents=[live_server], help="Update a live agent engagement mode.")
    live_engagement.add_argument("--agent-id", required=True)
    live_engagement.add_argument("--mode", choices=ENGAGEMENT_MODE_CHOICES, required=True, dest="engagement_mode")
    live_engagement.add_argument("--json", action="store_true", dest="as_json", help="Print the raw engagement update payload.")

    live_call = live_agent_subparsers.add_parser(
        "call",
        parents=[live_server],
        help="Request an official meeting turn from a moderator-called live agent.",
    )
    live_call.add_argument("--meeting-id", required=True)
    live_call.add_argument("--agent-id", required=True)
    live_call.add_argument("--role-id", default="")
    live_call.add_argument("--display-name", default="")
    live_call.add_argument("--turn-id", default="")
    live_call.add_argument("--turn-index", type=parse_nonnegative_int, default=None)
    live_call.add_argument("--json", action="store_true", dest="as_json", help="Print the raw official turn request payload.")
    live_call.add_argument("--wait", action="store_true", help="Wait for the verified official reply before returning.")
    live_call.add_argument("--timeout", type=parse_nonnegative_float, default=30.0, help="Seconds to wait when --wait is set.")
    live_call.add_argument("message", nargs="+")

    live_call_sequence = live_agent_subparsers.add_parser(
        "call-sequence",
        parents=[live_server],
        help="Request multiple official meeting turns in order and wait for each reply.",
    )
    live_call_sequence.add_argument("--meeting-id", required=True)
    live_call_sequence.add_argument("--turns-json", default="", help="JSON array of official turn request objects.")
    live_call_sequence.add_argument("--turns-file", default="", help="File containing a JSON array of official turn request objects.")
    live_call_sequence.add_argument("--timeout", type=parse_nonnegative_float, default=30.0, help="Default seconds to wait per turn.")
    live_call_sequence.add_argument("--stop-on-timeout", action="store_true", help="Skip remaining turns after the first timeout.")
    live_call_sequence.add_argument("--json", action="store_true", dest="as_json", help="Print the raw sequence result payload.")

    live_call_round = live_agent_subparsers.add_parser(
        "call-round",
        parents=[live_server],
        help="Request an official meeting round from bound live agents.",
    )
    live_call_round.add_argument("--meeting-id", required=True)
    live_call_round.add_argument("--round-id", required=True)
    live_call_round.add_argument("--role", action="append", default=[], dest="role_ids", help="Limit to a role id; repeat to set order.")
    live_call_round.add_argument("--timeout", type=parse_nonnegative_float, default=30.0, help="Default seconds to wait per turn.")
    live_call_round.add_argument("--stop-on-timeout", action="store_true", help="Skip remaining roles after the first timeout.")
    live_call_round.add_argument("--json", action="store_true", dest="as_json", help="Print the raw round result payload.")
    live_call_round.add_argument("instruction", nargs="*", help="Optional round instruction override.")

    live_call_remaining_rounds = live_agent_subparsers.add_parser(
        "call-remaining-rounds",
        parents=[live_server],
        help="Run remaining official meeting template rounds from bound live agents.",
    )
    live_call_remaining_rounds.add_argument("--meeting-id", required=True)
    live_call_remaining_rounds.add_argument("--timeout", type=parse_nonnegative_float, default=30.0, help="Default seconds to wait per turn.")
    live_call_remaining_rounds.add_argument("--max-rounds", type=parse_positive_int, default=MAX_LIVE_AGENT_ROUND_BATCH)
    live_call_remaining_rounds.add_argument("--stop-on-timeout", action="store_true", help="Skip remaining rounds after the first timeout.")
    _add_session_finalize_after_rounds_arg(live_call_remaining_rounds)
    live_call_remaining_rounds.add_argument("--json", action="store_true", dest="as_json", help="Print the raw remaining-round result payload.")

    live_review_checkpoint = live_agent_subparsers.add_parser(
        "review-checkpoint",
        parents=[live_server],
        help="Request a one-shot resident review checkpoint from ready bound live agents.",
    )
    live_review_checkpoint.add_argument("--meeting-id", required=True)
    live_review_checkpoint.add_argument("--group-id", required=True)
    live_review_checkpoint.add_argument(
        "--agent-id",
        action="append",
        default=[],
        dest="agent_ids",
        help="Limit to a bound agent id; repeat to set order.",
    )
    live_review_checkpoint.add_argument("--timeout", type=parse_nonnegative_float, default=30.0)
    live_review_checkpoint.add_argument("--checkpoint-id", default="")
    live_review_checkpoint.add_argument("--json", action="store_true", dest="as_json", help="Print the raw checkpoint result payload.")
    live_review_checkpoint.add_argument("message", nargs="+")

    live_start_meeting = live_agent_subparsers.add_parser(
        "start-meeting",
        parents=[live_server],
        help="Create a visible resident live-agent meeting from council and agent configs.",
    )
    live_start_meeting.add_argument("--meeting-id", default="", help="Optional explicit meeting id.")
    live_start_meeting.add_argument("--council-config", default="", help="Council config path; defaults to the demo council.")
    live_start_meeting.add_argument("--agent-config", default="", help="Agent runtime config with approved resident bindings.")
    live_start_meeting.add_argument("--json", action="store_true", dest="as_json", help="Print the raw meeting start payload.")

    live_finalize_meeting = live_agent_subparsers.add_parser(
        "finalize-meeting",
        parents=[live_server],
        help="Finalize a resident live-agent meeting into durable artifacts.",
    )
    live_finalize_meeting.add_argument("--meeting-id", required=True, help="Resident meeting id to finalize.")
    live_finalize_meeting.add_argument("--force", action="store_true", help="Overwrite existing final artifacts.")
    live_finalize_meeting.add_argument("--json", action="store_true", dest="as_json", help="Print the raw finalization payload.")

    live_start_session = live_agent_subparsers.add_parser(
        "start-session",
        parents=[live_server],
        help="Create a resident meeting and start its supervised live-agent group.",
    )
    live_start_session.add_argument("--meeting-id", default="", help="Optional explicit meeting id.")
    live_start_session.add_argument("--group-id", default="", help="Optional supervised process group id.")
    live_start_session.add_argument("--council-config", default="", help="Council config path; defaults to the demo council.")
    live_start_session.add_argument("--agent-config", default="", help="Agent runtime config with approved resident bindings.")
    live_start_session.add_argument("--live-agent-config", required=True, help="Resident live-agent run-group config path.")
    live_start_session.add_argument("--connect-timeout", type=parse_nonnegative_float, default=5.0, help="Seconds to wait for bound agents to connect.")
    _add_session_readiness_wait_args(live_start_session)
    _add_session_auto_restart_args(live_start_session)
    live_start_session.add_argument(
        "--run-remaining-rounds",
        action="store_true",
        help="After all bound agents connect, run remaining official template rounds.",
    )
    live_start_session.add_argument("--round-timeout", type=parse_nonnegative_float, default=30.0)
    live_start_session.add_argument("--max-rounds", type=parse_positive_int, default=MAX_LIVE_AGENT_ROUND_BATCH)
    live_start_session.add_argument("--stop-on-timeout", action="store_true", help="Skip remaining rounds after the first timeout.")
    _add_session_finalize_after_rounds_arg(live_start_session)
    live_start_session.add_argument(
        "--probe-bound-agents",
        action="store_true",
        help="Before optional remaining rounds, require each bound live agent to answer a lobby probe.",
    )
    live_start_session.add_argument("--probe-timeout", type=parse_nonnegative_float, default=12.0)
    live_start_session.add_argument("--json", action="store_true", dest="as_json", help="Print the raw session start payload.")

    live_resume_session = live_agent_subparsers.add_parser(
        "resume-session",
        parents=[live_server],
        help="Resume an existing resident meeting with a supervised live-agent group.",
    )
    live_resume_session.add_argument("--meeting-id", required=True, help="Existing resident meeting id to resume.")
    live_resume_session.add_argument("--group-id", default="", help="Optional supervised process group id.")
    live_resume_session.add_argument("--live-agent-config", required=True, help="Resident live-agent run-group config path.")
    live_resume_session.add_argument("--connect-timeout", type=parse_nonnegative_float, default=5.0, help="Seconds to wait for bound agents to connect.")
    _add_session_readiness_wait_args(live_resume_session)
    _add_session_auto_restart_args(live_resume_session)
    live_resume_session.add_argument(
        "--run-remaining-rounds",
        action="store_true",
        help="After all bound agents connect, run remaining official template rounds.",
    )
    live_resume_session.add_argument("--round-timeout", type=parse_nonnegative_float, default=30.0)
    live_resume_session.add_argument("--max-rounds", type=parse_positive_int, default=MAX_LIVE_AGENT_ROUND_BATCH)
    live_resume_session.add_argument("--stop-on-timeout", action="store_true", help="Skip remaining rounds after the first timeout.")
    _add_session_finalize_after_rounds_arg(live_resume_session)
    live_resume_session.add_argument(
        "--probe-bound-agents",
        action="store_true",
        help="Before optional remaining rounds, require each bound live agent to answer a lobby probe.",
    )
    live_resume_session.add_argument("--probe-timeout", type=parse_nonnegative_float, default=12.0)
    live_resume_session.add_argument("--json", action="store_true", dest="as_json", help="Print the raw session resume payload.")

    live_restart_session = live_agent_subparsers.add_parser(
        "restart-session",
        parents=[live_server],
        help="Restart an existing resident meeting's supervised live-agent group.",
    )
    live_restart_session.add_argument("--meeting-id", required=True, help="Existing resident meeting id to restart.")
    live_restart_session.add_argument("--group-id", required=True, help="Supervised process group id to restart.")
    live_restart_session.add_argument("--connect-timeout", type=parse_nonnegative_float, default=5.0, help="Seconds to wait for bound agents to reconnect.")
    _add_session_readiness_wait_args(live_restart_session)
    live_restart_session.add_argument(
        "--run-remaining-rounds",
        action="store_true",
        help="After all bound agents reconnect, run remaining official template rounds.",
    )
    live_restart_session.add_argument("--round-timeout", type=parse_nonnegative_float, default=30.0)
    live_restart_session.add_argument("--max-rounds", type=parse_positive_int, default=MAX_LIVE_AGENT_ROUND_BATCH)
    live_restart_session.add_argument("--stop-on-timeout", action="store_true", help="Skip remaining rounds after the first timeout.")
    _add_session_finalize_after_rounds_arg(live_restart_session)
    live_restart_session.add_argument(
        "--probe-bound-agents",
        action="store_true",
        help="Before optional remaining rounds, require each bound live agent to answer a lobby probe.",
    )
    live_restart_session.add_argument("--probe-timeout", type=parse_nonnegative_float, default=12.0)
    live_restart_session.add_argument("--json", action="store_true", dest="as_json", help="Print the raw session restart payload.")

    live_recover_session = live_agent_subparsers.add_parser(
        "recover-session",
        parents=[live_server],
        help="Recover an existing resident meeting's historical supervised live-agent group.",
    )
    live_recover_session.add_argument("--meeting-id", required=True, help="Existing resident meeting id to recover.")
    live_recover_session.add_argument("--group-id", required=True, help="Supervised process group id to recover.")
    live_recover_session.add_argument("--connect-timeout", type=parse_nonnegative_float, default=5.0, help="Seconds to wait for recovered agents to reconnect.")
    _add_session_readiness_wait_args(live_recover_session)
    live_recover_session.add_argument(
        "--run-remaining-rounds",
        action="store_true",
        help="After all recovered agents reconnect, run remaining official template rounds.",
    )
    live_recover_session.add_argument("--round-timeout", type=parse_nonnegative_float, default=30.0)
    live_recover_session.add_argument("--max-rounds", type=parse_positive_int, default=MAX_LIVE_AGENT_ROUND_BATCH)
    live_recover_session.add_argument("--stop-on-timeout", action="store_true", help="Skip remaining rounds after the first timeout.")
    _add_session_finalize_after_rounds_arg(live_recover_session)
    live_recover_session.add_argument(
        "--probe-bound-agents",
        action="store_true",
        help="Before optional remaining rounds, require each bound live agent to answer a lobby probe.",
    )
    live_recover_session.add_argument("--probe-timeout", type=parse_nonnegative_float, default=12.0)
    live_recover_session.add_argument("--json", action="store_true", dest="as_json", help="Print the raw session recover payload.")

    live_ensure_session = live_agent_subparsers.add_parser(
        "ensure-session",
        parents=[live_server],
        help="Inspect one resident session and start, resume, restart, or recover it as needed.",
    )
    live_ensure_session.add_argument("--meeting-id", default="", help="Resident meeting id to ensure; blank starts a new session.")
    live_ensure_session.add_argument("--group-id", default="", help="Supervised process group id to ensure.")
    live_ensure_session.add_argument("--council-config", default="", help="Council config path for start when the meeting is missing.")
    live_ensure_session.add_argument("--agent-config", default="", help="Agent runtime config for start when the meeting is missing.")
    live_ensure_session.add_argument("--live-agent-config", required=True, help="Resident live-agent run-group config path.")
    live_ensure_session.add_argument("--connect-timeout", type=parse_nonnegative_float, default=5.0, help="Seconds to wait for bound agents to connect.")
    live_ensure_session.add_argument("--wait-timeout", type=parse_nonnegative_float, default=30.0)
    live_ensure_session.add_argument("--wait-poll-interval", type=parse_nonnegative_float, default=2.0)
    _add_session_auto_restart_args(live_ensure_session)
    live_ensure_session.add_argument(
        "--run-remaining-rounds",
        action="store_true",
        help="After the ensured session is ready, run remaining official template rounds.",
    )
    live_ensure_session.add_argument("--round-timeout", type=parse_nonnegative_float, default=30.0)
    live_ensure_session.add_argument("--max-rounds", type=parse_positive_int, default=MAX_LIVE_AGENT_ROUND_BATCH)
    live_ensure_session.add_argument("--stop-on-timeout", action="store_true", help="Skip remaining rounds after the first timeout.")
    _add_session_finalize_after_rounds_arg(live_ensure_session)
    live_ensure_session.add_argument(
        "--probe-bound-agents",
        action="store_true",
        help="Before optional remaining rounds, require each bound live agent to answer a lobby probe.",
    )
    live_ensure_session.add_argument("--probe-timeout", type=parse_nonnegative_float, default=12.0)
    live_ensure_session.add_argument("--json", action="store_true", dest="as_json", help="Print the raw ensured session payload.")

    live_check_session = live_agent_subparsers.add_parser(
        "check-session",
        parents=[live_server],
        help="Check an existing resident meeting's supervised live-agent group without mutating it.",
    )
    live_check_session.add_argument("--meeting-id", required=True, help="Existing resident meeting id to check.")
    live_check_session.add_argument("--group-id", required=True, help="Supervised process group id to check.")
    live_check_session.add_argument(
        "--fail-on-degraded",
        action="store_true",
        help="Exit 1 when the checked session is not ready.",
    )
    live_check_session.add_argument("--json", action="store_true", dest="as_json", help="Print the raw session check payload.")

    live_session_readiness = live_agent_subparsers.add_parser(
        "session-readiness",
        parents=[live_server],
        help="Read one resident session readiness snapshot without recording an operation.",
    )
    live_session_readiness.add_argument("--meeting-id", required=True, help="Existing resident meeting id to inspect.")
    live_session_readiness.add_argument("--group-id", required=True, help="Supervised process group id to inspect.")
    live_session_readiness.add_argument(
        "--fail-on-degraded",
        action="store_true",
        help="Exit 1 when the targeted session is not ready.",
    )
    live_session_readiness.add_argument("--json", action="store_true", dest="as_json", help="Print the raw session readiness payload.")

    live_stop_session = live_agent_subparsers.add_parser(
        "stop-session",
        parents=[live_server],
        help="Stop an existing resident meeting's supervised live-agent group.",
    )
    live_stop_session.add_argument("--meeting-id", required=True, help="Existing resident meeting id to stop.")
    live_stop_session.add_argument("--group-id", required=True, help="Supervised process group id to stop.")
    live_stop_session.add_argument("--json", action="store_true", dest="as_json", help="Print the raw session stop payload.")

    live_say = live_agent_subparsers.add_parser("say", parents=[live_server], help="Post a lobby message as a live agent.")
    live_say.add_argument("--agent-id", required=True)
    live_say.add_argument("--source-event-id", default="")
    live_say.add_argument("--auto-chain-depth", type=parse_nonnegative_int, default=None)
    live_say.add_argument("--json", action="store_true", dest="as_json", help="Print the raw lobby post response.")
    live_say.add_argument("message", nargs="+")

    live_answer_turn = live_agent_subparsers.add_parser(
        "official-reply",
        aliases=["answer-turn"],
        parents=[live_server],
        help="Post an official turn reply as a live agent.",
    )
    live_answer_turn.add_argument("--agent-id", required=True)
    live_answer_turn.add_argument("--meeting-id", required=True)
    live_answer_turn.add_argument("--source-event-id", required=True)
    live_answer_turn.add_argument("--json", action="store_true", dest="as_json", help="Print the raw official turn reply response.")
    live_answer_turn.add_argument("message", nargs="+")

    live_room = live_agent_subparsers.add_parser("room", parents=[live_server], help="Read the live room snapshot for an agent.")
    live_room.add_argument("--agent-id", required=True)

    live_wait_room_event = live_agent_subparsers.add_parser(
        "wait-room-event",
        parents=[live_server],
        help="Wait for the next non-self lobby event visible to a live agent.",
    )
    live_wait_room_event.add_argument("--agent-id", required=True)
    live_wait_room_event.add_argument("--after-event-id", default="")
    live_wait_room_event.add_argument("--max-chain-depth", type=parse_nonnegative_int, default=1)
    live_wait_room_event.add_argument("--timeout", type=parse_nonnegative_float, default=30.0)
    live_wait_room_event.add_argument("--poll-interval", type=parse_nonnegative_float, default=2.0)
    live_wait_room_event.add_argument("--json", action="store_true", dest="as_json", help="Print the raw wait result.")

    live_wait_turn_request = live_agent_subparsers.add_parser(
        "wait-official-turn",
        aliases=["wait-turn-request"],
        parents=[live_server],
        help="Wait for the next targeted official turn request visible to a live agent.",
    )
    live_wait_turn_request.add_argument("--agent-id", required=True)
    live_wait_turn_request.add_argument("--after-event-id", default="")
    live_wait_turn_request.add_argument("--timeout", type=parse_nonnegative_float, default=30.0)
    live_wait_turn_request.add_argument("--poll-interval", type=parse_nonnegative_float, default=2.0)
    live_wait_turn_request.add_argument("--json", action="store_true", dest="as_json", help="Print the raw wait result.")

    live_wait_next = live_agent_subparsers.add_parser(
        "wait-next",
        parents=[live_server],
        help="Wait for the next actionable lobby event or official turn request visible to a live agent.",
    )
    live_wait_next.add_argument("--agent-id", required=True)
    live_wait_next.add_argument("--after-event-id", default="")
    live_wait_next.add_argument("--after-live-event-id", default="")
    live_wait_next.add_argument("--max-chain-depth", type=parse_nonnegative_int, default=1)
    live_wait_next.add_argument("--timeout", type=parse_nonnegative_float, default=30.0)
    live_wait_next.add_argument("--poll-interval", type=parse_nonnegative_float, default=2.0)
    live_wait_next.add_argument("--json", action="store_true", dest="as_json", help="Print the raw wait result.")

    live_health = live_agent_subparsers.add_parser("health", parents=[live_server], help="Read live-agent room health.")
    live_health.add_argument("--json", action="store_true", dest="as_json", help="Print the raw JSON health payload.")
    live_health.add_argument(
        "--fail-on-degraded",
        action="store_true",
        help="Exit 1 when the health status is not ok.",
    )
    live_health.add_argument(
        "--wait-ok",
        action="store_true",
        help="Poll until health reports ok or the timeout is reached.",
    )
    live_health.add_argument(
        "--wait-session-ready",
        action="store_true",
        help="Poll until the named meeting/group session reports ready in health.",
    )
    live_health.add_argument("--meeting-id", default="", help="Meeting id for --wait-session-ready.")
    live_health.add_argument("--group-id", default="", help="Process group id for --wait-session-ready.")
    live_health.add_argument("--timeout", type=parse_nonnegative_float, default=30.0)
    live_health.add_argument("--poll-interval", type=parse_nonnegative_float, default=2.0)

    live_preflight = live_agent_subparsers.add_parser(
        "preflight",
        help="Check a resident live-agent config without executing provider commands.",
    )
    live_preflight.add_argument("--config", required=True, help="Resident group config path.")
    live_preflight.add_argument("--server", default=None, help="Optional room server URL override for the config.")
    live_preflight.add_argument("--json", action="store_true", dest="as_json", help="Print a machine-readable preflight report.")

    live_discover = live_agent_subparsers.add_parser(
        "discover",
        parents=[live_server],
        help="Discover installed local agent CLIs and build a resident run-group config draft.",
    )
    live_discover.add_argument(
        "--output",
        default=".agentsassemble/live-agents.discovered.local.json",
        help="Path to write the discovered resident config.",
    )
    live_discover.add_argument("--meeting-id", default="")
    live_discover.add_argument("--engagement-mode", default="mentioned")
    live_discover.add_argument(
        "--session-bundle",
        action="store_true",
        help="Also write council and agent configs plus an ensure-session next command for the discovered residents.",
    )
    live_discover.add_argument("--session-council-output", default="")
    live_discover.add_argument("--session-agent-output", default="")
    live_discover.add_argument(
        "--include-legacy-gemini",
        action="store_true",
        help="Include a detected legacy Gemini CLI entry in the generated config.",
    )
    live_discover.add_argument("--json", action="store_true", dest="as_json", help="Print the machine-readable discovery report.")

    live_auto_join = live_agent_subparsers.add_parser(
        "auto-join",
        parents=[live_server],
        help="Discover local CLIs, write a session bundle, and ensure the resident session.",
    )
    live_auto_join.add_argument(
        "--output",
        default=".agentsassemble/live-agents.discovered.local.json",
        help="Path to write the discovered resident config.",
    )
    live_auto_join.add_argument("--meeting-id", default="", help="Resident meeting id to ensure; blank starts a new session.")
    live_auto_join.add_argument("--engagement-mode", default="mentioned")
    live_auto_join.add_argument("--session-council-output", default="")
    live_auto_join.add_argument("--session-agent-output", default="")
    live_auto_join.add_argument(
        "--include-legacy-gemini",
        action="store_true",
        help="Include a detected legacy Gemini CLI entry in the generated config.",
    )
    live_auto_join.add_argument(
        "--approve-real-providers",
        action="store_true",
        help="Allow auto-join to start discovered real provider CLIs after discovery and preflight evidence.",
    )
    live_auto_join.add_argument(
        "--approve-agent",
        action="append",
        default=[],
        dest="approve_agents",
        help="Allow only a specific discovered live-agent id to auto-join; repeat for multiple agents.",
    )
    live_auto_join.add_argument(
        "--approve-command",
        action="append",
        default=[],
        dest="approve_commands",
        help="Allow only a specific discovered CLI command to auto-join; repeat for multiple commands.",
    )
    live_auto_join.add_argument("--connect-timeout", type=parse_nonnegative_float, default=5.0)
    live_auto_join.add_argument("--wait-timeout", type=parse_nonnegative_float, default=30.0)
    live_auto_join.add_argument("--wait-poll-interval", type=parse_nonnegative_float, default=2.0)
    _add_session_auto_restart_args(live_auto_join)
    live_auto_join.add_argument(
        "--run-remaining-rounds",
        action="store_true",
        help="After the ensured session is ready, run remaining official template rounds.",
    )
    live_auto_join.add_argument("--round-timeout", type=parse_nonnegative_float, default=30.0)
    live_auto_join.add_argument("--max-rounds", type=parse_positive_int, default=MAX_LIVE_AGENT_ROUND_BATCH)
    live_auto_join.add_argument("--stop-on-timeout", action="store_true")
    _add_session_finalize_after_rounds_arg(live_auto_join)
    live_auto_join.add_argument(
        "--probe-bound-agents",
        action="store_true",
        help="Before optional remaining rounds, require each bound live agent to answer a lobby probe.",
    )
    live_auto_join.add_argument("--probe-timeout", type=parse_nonnegative_float, default=12.0)
    live_auto_join.add_argument("--json", action="store_true", dest="as_json", help="Print the machine-readable auto-join result.")

    live_smoke = live_agent_subparsers.add_parser(
        "smoke",
        parents=[live_server],
        help="Run credential-free local smoke against a running GUI room.",
    )
    live_smoke.add_argument("--group-id", default="", help="Optional supervised process group id for the smoke run.")
    live_smoke.add_argument("--timeout", type=parse_nonnegative_float, default=12.0, help="Seconds to wait for fake agent replies.")
    live_smoke.add_argument("--json", action="store_true", dest="as_json", help="Print a machine-readable smoke result.")

    live_session_smoke = live_agent_subparsers.add_parser(
        "session-smoke",
        parents=[live_server],
        help="Run a credential-free resident session start/reply/check/resume/restart/recover/stop smoke.",
    )
    live_session_smoke.add_argument("--group-id", default="", help="Optional supervised process group id for the smoke run.")
    live_session_smoke.add_argument("--meeting-id", default="", help="Optional resident meeting id for the smoke run.")
    live_session_smoke.add_argument("--timeout", type=parse_nonnegative_float, default=12.0, help="Seconds to wait for fake session readiness and replies.")
    live_session_smoke.add_argument(
        "--lobby-probes",
        type=parse_session_smoke_lobby_probe_count,
        default=1,
        dest="lobby_probe_count",
        help="Human lobby probes to verify before restart, after restart, and after recover, 1-5.",
    )
    live_session_smoke.add_argument(
        "--soak-cycles",
        type=parse_session_smoke_soak_cycle_count,
        default=0,
        dest="soak_cycle_count",
        help="Extra same-session check-and-reply soak cycles after recover, 0-5.",
    )
    live_session_smoke.add_argument(
        "--soak-interval",
        type=parse_session_smoke_soak_interval_seconds,
        default=0.0,
        dest="soak_interval_seconds",
        help="Seconds to wait before each soak cycle, 0-60.",
    )
    live_session_smoke.add_argument("--json", action="store_true", dest="as_json", help="Print a machine-readable session smoke result.")

    live_real_session_smoke = live_agent_subparsers.add_parser(
        "real-session-smoke",
        parents=[live_server],
        help="Run an explicitly approved diagnostic start/probe/stop smoke for a real resident config.",
    )
    live_real_session_smoke.add_argument("--group-id", default="", help="Optional supervised process group id for the smoke run.")
    live_real_session_smoke.add_argument("--meeting-id", default="", help="Optional resident meeting id for the smoke run.")
    live_real_session_smoke.add_argument("--live-agent-config", required=True, help="Resident live-agent run-group config path.")
    live_real_session_smoke.add_argument("--council-config", required=True, help="Council config path for bound resident roles.")
    live_real_session_smoke.add_argument("--agent-config", required=True, help="Agent runtime config with approved resident bindings.")
    live_real_session_smoke.add_argument(
        "--timeout",
        type=parse_nonnegative_float,
        default=12.0,
        help="Seconds to wait for real session readiness and probes.",
    )
    live_real_session_smoke.add_argument(
        "--approve-real-providers",
        action="store_true",
        help="Allow this one smoke command to start real provider resident CLIs.",
    )
    live_real_session_smoke.add_argument("--json", action="store_true", dest="as_json", help="Print a machine-readable real session smoke result.")

    live_official_round_smoke = live_agent_subparsers.add_parser(
        "official-round-smoke",
        parents=[live_server],
        help="Run credential-free fake agents through a moderator-called official round.",
    )
    live_official_round_smoke.add_argument("--group-id", default="", help="Optional supervised process group id for the smoke run.")
    live_official_round_smoke.add_argument("--timeout", type=parse_nonnegative_float, default=12.0, help="Seconds to wait per fake official turn.")
    live_official_round_smoke.add_argument("--json", action="store_true", dest="as_json", help="Print a machine-readable smoke result.")

    live_doctor = live_agent_subparsers.add_parser(
        "doctor",
        parents=[live_server],
        help="Run health plus credential-free smoke readiness checks against a GUI room.",
    )
    live_doctor.add_argument("--group-id", default="", help="Optional supervised process group id for the smoke check.")
    live_doctor.add_argument("--timeout", type=parse_nonnegative_float, default=12.0, help="Seconds to wait for fake agent replies.")
    live_doctor.add_argument(
        "--probe-agent",
        action="append",
        default=[],
        dest="probe_agent_ids",
        help="Opt-in resident agent id to probe after credential-free smoke passes; may be repeated.",
    )
    live_doctor.add_argument(
        "--probe-group",
        action="append",
        default=[],
        dest="probe_group_ids",
        help="Opt-in supervised process group id whose launch-time manifest agents should be probed; may be repeated.",
    )
    live_doctor.add_argument(
        "--official-round-smoke",
        action="store_true",
        help="Also run the credential-free moderator-called official round smoke inside readiness.",
    )
    live_doctor.add_argument(
        "--session-smoke",
        action="store_true",
        help="Also run the full credential-free resident session smoke inside readiness.",
    )
    live_doctor.add_argument(
        "--session-smoke-soak-cycles",
        type=parse_session_smoke_soak_cycle_count,
        default=0,
        help="Extra same-session session-smoke soak cycles when --session-smoke is enabled, 0-5.",
    )
    live_doctor.add_argument(
        "--session-smoke-soak-interval",
        type=parse_session_smoke_soak_interval_seconds,
        default=0.0,
        help="Seconds to wait before each session-smoke soak cycle, 0-60.",
    )
    live_doctor.add_argument("--json", action="store_true", dest="as_json", help="Print a machine-readable readiness result.")

    live_probe = live_agent_subparsers.add_parser(
        "probe",
        parents=[live_server],
        help="Ask one already-running live agent to reply through the room.",
    )
    live_probe.add_argument("--agent-id", required=True)
    live_probe.add_argument("--timeout", type=parse_nonnegative_float, default=12.0, help="Seconds to wait for the agent reply.")
    live_probe.add_argument("--json", action="store_true", dest="as_json", help="Print a machine-readable probe result.")

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
    live_run.add_argument("--poll-interval", type=parse_nonnegative_float, default=2.0)
    live_run.add_argument("--heartbeat-interval", type=parse_nonnegative_float, default=30.0)
    live_run.add_argument("--cooldown", type=parse_nonnegative_float, default=5.0)
    live_run.add_argument("--max-chain-depth", type=parse_nonnegative_int, default=1)
    live_run.add_argument("--max-ticks", type=parse_nonnegative_int, default=0)
    live_run.add_argument("--terminal-idle-timeout", type=parse_nonnegative_float, default=0.35)
    live_run.add_argument("--command", dest="resident_command", nargs=argparse.REMAINDER, default=[])

    live_group = live_agent_subparsers.add_parser("run-group", help="Run multiple resident local CLI live agents.")
    live_group.add_argument("--config", required=True)
    live_group.add_argument("--server", default=None)
    live_group.add_argument("--max-ticks", type=parse_nonnegative_int, default=None)

    live_processes = live_agent_subparsers.add_parser("processes", help="Manage supervised live-agent process groups.")
    live_process_subparsers = live_processes.add_subparsers(dest="live_agent_process_command", required=True)

    live_process_list = live_process_subparsers.add_parser("list", parents=[live_server], help="List supervised live-agent process groups.")
    live_process_list.add_argument("--json", action="store_true", dest="as_json", help="Print the raw JSON process payload.")
    live_process_list.add_argument(
        "--fail-on-attention",
        action="store_true",
        help="Exit 1 when any process group needs operator attention.",
    )

    live_process_events = live_process_subparsers.add_parser(
        "events",
        parents=[live_server],
        help="List recent supervised live-agent process lifecycle events.",
    )
    live_process_events.add_argument("--limit", type=parse_positive_int, default=50)
    live_process_events.add_argument("--scan-limit", type=parse_positive_int, default=None)
    live_process_events.add_argument("--group-id", default="")
    live_process_events.add_argument("--json", action="store_true", dest="as_json", help="Print the raw JSON event payload.")

    live_process_wait_event = live_process_subparsers.add_parser(
        "wait-event",
        parents=[live_server],
        help="Wait for a matching supervised process lifecycle event.",
    )
    live_process_wait_event.add_argument("--event-type", required=True, help="Lifecycle event type to wait for.")
    live_process_wait_event.add_argument("--group-id", default="", help="Optional process group id filter.")
    live_process_wait_event.add_argument("--status", default="", help="Optional event status filter.")
    live_process_wait_event.add_argument("--after-timestamp", default="", help="Ignore events at or before this timestamp.")
    live_process_wait_event.add_argument("--limit", type=parse_positive_int, default=50)
    live_process_wait_event.add_argument("--scan-limit", type=parse_positive_int, default=None)
    live_process_wait_event.add_argument("--timeout", type=parse_nonnegative_float, default=30.0)
    live_process_wait_event.add_argument("--poll-interval", type=parse_nonnegative_float, default=2.0)
    live_process_wait_event.add_argument("--json", action="store_true", dest="as_json", help="Print a machine-readable wait result.")

    live_process_start = live_process_subparsers.add_parser("start", parents=[live_server], help="Start a supervised live-agent run-group.")
    live_process_start.add_argument("--config", required=True, help="Resident group config path.")
    live_process_start.add_argument("--group-id", default="")
    live_process_start.add_argument("--auto-restart", action="store_true")
    live_process_start.add_argument("--max-restarts", type=parse_nonnegative_int, default=0)
    live_process_start.add_argument("--restart-backoff-seconds", type=parse_nonnegative_float, default=5.0)
    live_process_start.add_argument("--stale-restart-after-seconds", type=parse_nonnegative_float, default=0.0)
    live_process_start.add_argument("--json", action="store_true", dest="as_json", help="Print the raw JSON process payload.")

    live_process_stop = live_process_subparsers.add_parser("stop", parents=[live_server], help="Stop a supervised live-agent process group.")
    live_process_stop.add_argument("group_id")
    live_process_stop.add_argument("--json", action="store_true", dest="as_json", help="Print the raw JSON process payload.")

    live_process_stop_running = live_process_subparsers.add_parser(
        "stop-running",
        parents=[live_server],
        help="Stop every currently running or restarting supervised live-agent process group.",
    )
    live_process_stop_running.add_argument("--json", action="store_true", dest="as_json", help="Print the raw JSON process payload.")

    live_process_restart = live_process_subparsers.add_parser("restart", parents=[live_server], help="Restart a supervised live-agent process group.")
    live_process_restart.add_argument("group_id")
    live_process_restart.add_argument("--json", action="store_true", dest="as_json", help="Print the raw JSON process payload.")

    live_process_recover = live_process_subparsers.add_parser("recover", parents=[live_server], help="Recover a historical live-agent process group.")
    live_process_recover.add_argument("group_id")
    live_process_recover.add_argument("--json", action="store_true", dest="as_json", help="Print the raw JSON process payload.")

    live_process_wait = live_process_subparsers.add_parser("wait", parents=[live_server], help="Wait for a process group to become ready.")
    live_process_wait.add_argument("group_id")
    live_process_wait.add_argument("--timeout", type=parse_nonnegative_float, default=30.0)
    live_process_wait.add_argument("--poll-interval", type=parse_nonnegative_float, default=2.0)
    live_process_wait.add_argument("--json", action="store_true", dest="as_json", help="Print the raw JSON wait result.")

    live_operations = live_agent_subparsers.add_parser("operations", help="Inspect live-agent control operation history.")
    live_operations_subparsers = live_operations.add_subparsers(dest="live_agent_operations_command", required=True)
    live_operations_list = live_operations_subparsers.add_parser(
        "list",
        parents=[live_server],
        help="List recent live-agent control operations.",
    )
    live_operations_list.add_argument("--limit", type=parse_positive_int, default=50)
    live_operations_list.add_argument("--operation", default="", help="Optional operation name filter.")
    live_operations_list.add_argument("--target-id", default="", help="Optional operation target id filter.")
    live_operations_list.add_argument("--status", default="", help="Optional operation status filter.")
    live_operations_list.add_argument("--scan-limit", type=parse_positive_int, default=None, help="Maximum recent operations to scan before returning matches.")
    live_operations_list.add_argument("--json", action="store_true", dest="as_json", help="Print the raw JSON operation payload.")
    live_operations_list.add_argument(
        "--fail-on-attention",
        action="store_true",
        help="Exit 1 when any returned live-agent operation is not successful.",
    )
    live_operations_wait = live_operations_subparsers.add_parser(
        "wait",
        parents=[live_server],
        help="Wait for a matching live-agent control operation to appear.",
    )
    live_operations_wait.add_argument("--operation", required=True, help="Operation name to wait for.")
    live_operations_wait.add_argument("--target-id", default="", help="Optional operation target id filter.")
    live_operations_wait.add_argument("--status", default="", help="Optional operation status filter.")
    live_operations_wait.add_argument("--after-id", default="", help="Ignore operations up to and including this operation id.")
    live_operations_wait.add_argument("--limit", type=parse_positive_int, default=50)
    live_operations_wait.add_argument("--scan-limit", type=parse_positive_int, default=None, help="Maximum recent operations to scan before waiting on the returned tail.")
    live_operations_wait.add_argument("--timeout", type=parse_nonnegative_float, default=30.0)
    live_operations_wait.add_argument("--poll-interval", type=parse_nonnegative_float, default=2.0)
    live_operations_wait.add_argument("--json", action="store_true", dest="as_json", help="Print a machine-readable wait result.")

    live_session_runs = live_agent_subparsers.add_parser("session-runs", help="Inspect durable live-agent session-run state.")
    live_session_runs_subparsers = live_session_runs.add_subparsers(dest="live_agent_session_runs_command", required=True)
    live_session_runs_list = live_session_runs_subparsers.add_parser(
        "list",
        parents=[live_server],
        help="List durable live-agent session runs.",
    )
    live_session_runs_list.add_argument("--limit", type=parse_positive_int, default=50)
    live_session_runs_list.add_argument("--run-id", default="", help="Filter durable session runs by exact run id.")
    live_session_runs_list.add_argument("--meeting-id", default="", help="Filter durable session runs by meeting id.")
    live_session_runs_list.add_argument("--group-id", default="", help="Filter durable session runs by group id.")
    live_session_runs_list.add_argument(
        "--include-readiness",
        action="store_true",
        help="Request the current read-only readiness overlay for each session run.",
    )
    live_session_runs_list.add_argument("--json", action="store_true", dest="as_json", help="Print the raw JSON session-run payload.")
    live_session_runs_list.add_argument(
        "--fail-on-attention",
        action="store_true",
        help="Exit 1 when any returned session run needs operator attention.",
    )
    live_session_runs_retry_now = live_session_runs_subparsers.add_parser(
        "retry-now",
        parents=[live_server],
        help="Schedule an active durable live-agent session run for immediate retry.",
    )
    live_session_runs_retry_now.add_argument("--run-id", default="", help="Durable session-run id to retry now.")
    live_session_runs_retry_now.add_argument("--meeting-id", default="", help="Meeting id for the latest matching durable session run.")
    live_session_runs_retry_now.add_argument("--group-id", default="", help="Group id for the latest matching durable session run.")
    live_session_runs_retry_now.add_argument(
        "--approve-real-providers",
        action="store_true",
        help="Allow this retry-now action to relaunch real provider residents when their saved config requires approval.",
    )
    live_session_runs_retry_now.add_argument("--json", action="store_true", dest="as_json", help="Print the raw JSON retry payload.")
    live_session_runs_pause = live_session_runs_subparsers.add_parser(
        "pause",
        parents=[live_server],
        help="Pause an active durable live-agent session run without stopping its process group.",
    )
    live_session_runs_pause.add_argument("--run-id", default="", help="Durable session-run id to pause.")
    live_session_runs_pause.add_argument("--meeting-id", default="", help="Meeting id for the latest matching durable session run.")
    live_session_runs_pause.add_argument("--group-id", default="", help="Group id for the latest matching durable session run.")
    live_session_runs_pause.add_argument("--json", action="store_true", dest="as_json", help="Print the raw JSON pause payload.")
    live_session_runs_resume = live_session_runs_subparsers.add_parser(
        "resume",
        parents=[live_server],
        help="Resume a paused durable live-agent session run.",
    )
    live_session_runs_resume.add_argument("--run-id", default="", help="Durable session-run id to resume.")
    live_session_runs_resume.add_argument("--meeting-id", default="", help="Meeting id for the latest matching durable session run.")
    live_session_runs_resume.add_argument("--group-id", default="", help="Group id for the latest matching durable session run.")
    live_session_runs_resume.add_argument("--json", action="store_true", dest="as_json", help="Print the raw JSON resume payload.")
    live_session_runs_stop = live_session_runs_subparsers.add_parser(
        "stop",
        parents=[live_server],
        help="Stop a durable live-agent session run without stopping its process group.",
    )
    live_session_runs_stop.add_argument("--run-id", default="", help="Durable session-run id to stop.")
    live_session_runs_stop.add_argument("--meeting-id", default="", help="Meeting id for the latest matching durable session run.")
    live_session_runs_stop.add_argument("--group-id", default="", help="Group id for the latest matching durable session run.")
    live_session_runs_stop.add_argument("--json", action="store_true", dest="as_json", help="Print the raw JSON stop payload.")
    live_session_runs_wait = live_session_runs_subparsers.add_parser(
        "wait",
        parents=[live_server],
        help="Wait for a durable live-agent session run to reach a status.",
    )
    live_session_runs_wait.add_argument("--run-id", default="", help="Durable session-run id to wait for.")
    live_session_runs_wait.add_argument("--meeting-id", default="", help="Meeting id for the latest matching durable session run.")
    live_session_runs_wait.add_argument("--group-id", default="", help="Group id for the latest matching durable session run.")
    live_session_runs_wait.add_argument("--status", required=True, help="Session-run status to observe, such as ready, failed, or stopped.")
    live_session_runs_wait.add_argument("--limit", type=parse_positive_int, default=50)
    live_session_runs_wait.add_argument("--timeout", type=parse_nonnegative_float, default=30.0)
    live_session_runs_wait.add_argument("--poll-interval", type=parse_nonnegative_float, default=2.0)
    live_session_runs_wait.add_argument("--json", action="store_true", dest="as_json", help="Print a machine-readable wait result.")

    sessions = subparsers.add_parser("sessions", help="Inspect and invite Codex CLI live sessions.")
    session_subparsers = sessions.add_subparsers(dest="sessions_command", required=True)

    session_list = session_subparsers.add_parser("list", help="List recent Codex CLI sessions.")
    session_list.add_argument("--limit", type=int, default=20)
    session_list.add_argument("--json", action="store_true", dest="as_json")

    session_invite = session_subparsers.add_parser("invite", help="Bind a Codex CLI session to a meeting role.")
    session_invite.add_argument("session_id")
    session_invite.add_argument("--role", required=True, dest="role_id")
    session_invite.add_argument("--server", default="", help="Room server URL; when set, record the invite through the GUI control plane.")
    session_invite.add_argument("--meeting-id", default="", help="Meeting id for server-side role validation.")
    session_invite.add_argument("--output", default=str(DEFAULT_INVITE_CONFIG_PATH))
    session_invite.add_argument("--json", action="store_true", dest="as_json")

    session_live_agent_config = session_subparsers.add_parser(
        "live-agent-config",
        help="Build a resident live-agent run-group config from a Codex invite config.",
    )
    session_live_agent_config.add_argument("--input", default=str(DEFAULT_INVITE_CONFIG_PATH), dest="input_path")
    session_live_agent_config.add_argument("--output", default=str(DEFAULT_LIVE_AGENT_CONFIG_PATH))
    session_live_agent_config.add_argument("--server", default="http://127.0.0.1:8765")
    session_live_agent_config.add_argument("--meeting-id", default="")
    session_live_agent_config.add_argument("--engagement-mode", default="always")
    session_live_agent_config.add_argument("--json", action="store_true", dest="as_json")

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
        serve_gui(
            host=args.host,
            port=args.port,
            output_root=Path(args.output_root),
            live_agent_config=Path(args.live_agent_config) if args.live_agent_config else None,
            live_agent_group_id=args.live_agent_group_id,
            live_agent_auto_restart=args.live_agent_auto_restart,
            live_agent_max_restarts=args.live_agent_max_restarts,
            live_agent_restart_backoff_seconds=args.live_agent_restart_backoff_seconds,
            live_agent_stale_restart_after_seconds=args.live_agent_stale_restart_after_seconds,
        )
        return 0
    if args.command == "claude-bridge":
        serve_bridge(host=args.host, port=args.port, token=args.token, command=args.bridge_command)
        return 0
    if args.command == "live-agent":
        return run_live_agent_command(args)
    if args.command == "providers":
        return run_providers_command(args)
    if args.command == "memory-capsule":
        return run_memory_capsule_command(args)
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
            if args.as_json:
                print(json.dumps(response, ensure_ascii=False, indent=2))
            else:
                print(f"Registered {agent.get('agent_id') or args.agent_id}")
            return 0
        if args.live_agent_command == "join-brief":
            return _run_live_agent_join_brief(args)
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
        if args.live_agent_command == "call-remaining-rounds":
            return _run_live_agent_call_remaining_rounds(args)
        if args.live_agent_command == "review-checkpoint":
            return _run_live_agent_review_checkpoint(args)
        if args.live_agent_command == "start-meeting":
            return _run_live_agent_start_meeting(args)
        if args.live_agent_command == "finalize-meeting":
            return _run_live_agent_finalize_meeting(args)
        if args.live_agent_command == "start-session":
            return _run_live_agent_start_session(args)
        if args.live_agent_command == "resume-session":
            return _run_live_agent_resume_session(args)
        if args.live_agent_command == "restart-session":
            return _run_live_agent_restart_session(args)
        if args.live_agent_command == "recover-session":
            return _run_live_agent_recover_session(args)
        if args.live_agent_command == "ensure-session":
            return _run_live_agent_ensure_session(args)
        if args.live_agent_command == "check-session":
            return _run_live_agent_check_session(args)
        if args.live_agent_command == "session-readiness":
            return _run_live_agent_session_readiness(args)
        if args.live_agent_command == "stop-session":
            return _run_live_agent_stop_session(args)
        if args.live_agent_command == "say":
            agent_id = urllib.parse.quote(args.agent_id, safe="")
            payload = {"message": " ".join(args.message), "kind": "message"}
            if args.source_event_id:
                payload["source_event_id"] = args.source_event_id
            if args.auto_chain_depth is not None:
                payload["auto_chain_depth"] = args.auto_chain_depth
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
        if args.live_agent_command in {"official-reply", "answer-turn"}:
            return _run_live_agent_answer_turn(args)
        if args.live_agent_command == "room":
            agent_id = urllib.parse.quote(args.agent_id, safe="")
            response = _request_json(_server_url(args.server, f"/api/live-agents/{agent_id}/room"))
            print(json.dumps(response, ensure_ascii=False, indent=2))
            return 0
        if args.live_agent_command == "wait-room-event":
            return _run_live_agent_wait_room_event(args)
        if args.live_agent_command in {"wait-official-turn", "wait-turn-request"}:
            return _run_live_agent_wait_turn_request(args)
        if args.live_agent_command == "wait-next":
            return _run_live_agent_wait_next(args)
        if args.live_agent_command == "health":
            return _run_live_agent_health(args)
        if args.live_agent_command == "preflight":
            return _run_live_agent_preflight(args)
        if args.live_agent_command == "discover":
            return _run_live_agent_discover(args)
        if args.live_agent_command == "auto-join":
            return _run_live_agent_auto_join(args)
        if args.live_agent_command == "smoke":
            return _run_live_agent_smoke(args)
        if args.live_agent_command == "session-smoke":
            return _run_live_agent_session_smoke(args)
        if args.live_agent_command == "real-session-smoke":
            return _run_live_agent_real_session_smoke(args)
        if args.live_agent_command == "official-round-smoke":
            return _run_live_agent_official_round_smoke(args)
        if args.live_agent_command == "doctor":
            return _run_live_agent_doctor(args)
        if args.live_agent_command == "probe":
            return _run_live_agent_probe(args)
        if args.live_agent_command == "processes":
            return _run_live_agent_processes(args)
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
        "last_reply_at": getattr(args, "last_reply_at", None),
        "last_observed_event_id": getattr(args, "last_observed_event_id", None),
        "last_observed_live_event_id": getattr(args, "last_observed_live_event_id", None),
    }
    for key, value in optional_fields.items():
        if value is not None and not _is_unreplaced_template_placeholder(value):
            payload[key] = value
    return payload


def _is_unreplaced_template_placeholder(value: object) -> bool:
    if not isinstance(value, str):
        return False
    return bool(re.fullmatch(r"\{[A-Za-z0-9_]+\}", value.strip()))


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
    _print_join_brief_command("Room snapshot", commands.get("room"))
    _print_join_brief_command("Roster gate", commands.get("roster_gate"))
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


def _run_live_agent_list(args: argparse.Namespace) -> int:
    try:
        payload = _request_json(_server_url(args.server, _live_agent_list_path(args)))
    except (OSError, urllib.error.URLError, ValueError, json.JSONDecodeError) as error:
        raise ValueError(_live_agent_list_fetch_error(error)) from error
    _print_live_agent_list_payload(payload, as_json=args.as_json)
    if args.require_match and _live_agent_list_payload_empty(payload):
        return 1
    if args.require_all_agents and _live_agent_list_missing_required_agents(payload, args.agent_ids):
        return 1
    if args.fail_on_attention and _live_agent_list_payload_needs_attention(payload):
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


def _append_live_agent_roster_seconds(parts: list[str], label: str, value: object) -> None:
    if value in (None, ""):
        return
    seconds = _safe_nonnegative_float(value)
    parts.append(f"{label}={_format_seconds(seconds)}")


def _live_agent_list_payload_needs_attention(payload: dict[str, object]) -> bool:
    agents = payload.get("agents") if isinstance(payload.get("agents"), list) else []
    return any(isinstance(item, dict) and _live_agent_roster_agent_needs_attention(item) for item in agents)


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
        payload={"force": bool(args.force)},
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
    return f"{prefix} {meeting_id}: {official_count} official events"


def _run_live_agent_start_session(args: argparse.Namespace) -> int:
    _validate_session_auto_restart_args(args)
    payload = _session_start_payload(args)
    timeout_seconds = _session_remaining_rounds_request(
        args,
        payload,
        connect_timeout_seconds=float(args.connect_timeout),
    )
    response = _request_json(
        _server_url(args.server, "/api/live-agent-sessions/start"),
        method="POST",
        payload=payload,
        timeout_seconds=timeout_seconds,
    )
    response = _maybe_wait_for_live_agent_session_ready(args, response)
    if args.as_json:
        print(json.dumps(response, ensure_ascii=False, indent=2))
    else:
        print(_format_live_agent_session_start(response))
    return _session_command_exit_code(response)


def _validate_session_auto_restart_args(args: argparse.Namespace) -> None:
    if args.auto_restart and args.max_restarts <= 0:
        raise ValueError("--auto-restart requires --max-restarts greater than 0.")
    if args.stale_restart_after_seconds > 0 and (not args.auto_restart or args.max_restarts <= 0):
        raise ValueError("--stale-restart-after-seconds requires --auto-restart and --max-restarts greater than 0.")


def _session_start_payload(args: argparse.Namespace) -> dict[str, object]:
    payload: dict[str, object] = {
        "meeting_id": str(args.meeting_id or ""),
        "group_id": str(args.group_id or ""),
        "council_config_path": str(args.council_config or ""),
        "agent_config_path": str(args.agent_config or ""),
        "live_agent_config_path": str(args.live_agent_config or ""),
        "connect_timeout_seconds": float(args.connect_timeout),
        "auto_restart": bool(args.auto_restart),
        "max_restarts": int(args.max_restarts),
        "restart_backoff_seconds": float(args.restart_backoff_seconds),
        "stale_restart_after_seconds": float(args.stale_restart_after_seconds),
    }
    if bool(getattr(args, "approve_real_providers", False)):
        payload["approve_real_providers"] = True
    return payload


def _run_live_agent_resume_session(args: argparse.Namespace) -> int:
    _validate_session_auto_restart_args(args)
    payload = _session_resume_payload(args)
    timeout_seconds = _session_remaining_rounds_request(
        args,
        payload,
        connect_timeout_seconds=float(args.connect_timeout),
    )
    response = _request_json(
        _server_url(args.server, "/api/live-agent-sessions/resume"),
        method="POST",
        payload=payload,
        timeout_seconds=timeout_seconds,
    )
    response = _maybe_wait_for_live_agent_session_ready(args, response)
    if args.as_json:
        print(json.dumps(response, ensure_ascii=False, indent=2))
    else:
        print(_format_live_agent_session_start(response))
    return _session_command_exit_code(response)


def _session_resume_payload(args: argparse.Namespace) -> dict[str, object]:
    return {
        "meeting_id": str(args.meeting_id or ""),
        "group_id": str(args.group_id or ""),
        "live_agent_config_path": str(args.live_agent_config or ""),
        "connect_timeout_seconds": float(args.connect_timeout),
        "auto_restart": bool(args.auto_restart),
        "max_restarts": int(args.max_restarts),
        "restart_backoff_seconds": float(args.restart_backoff_seconds),
        "stale_restart_after_seconds": float(args.stale_restart_after_seconds),
    }


def _session_remaining_rounds_request(
    args: argparse.Namespace,
    payload: dict[str, object],
    *,
    connect_timeout_seconds: float,
) -> float:
    timeout_seconds = connect_timeout_seconds + 6.0
    if getattr(args, "probe_bound_agents"):
        probe_timeout = float(getattr(args, "probe_timeout"))
        payload.update(
            {
                "probe_bound_agents": True,
                "probe_timeout_seconds": probe_timeout,
            }
        )
        timeout_seconds = connect_timeout_seconds + _operation_http_timeout(
            probe_timeout,
            windows=SESSION_BOUND_PROBE_HTTP_WINDOWS,
        )
    max_rounds = max(1, int(getattr(args, "max_rounds")))
    if getattr(args, "run_remaining_rounds"):
        if max_rounds > MAX_LIVE_AGENT_ROUND_BATCH:
            raise ValueError(f"--max-rounds supports at most {MAX_LIVE_AGENT_ROUND_BATCH}.")
        payload.update(
            {
                "run_remaining_rounds": True,
                "round_timeout_seconds": float(getattr(args, "round_timeout")),
                "round_max_rounds": max_rounds,
                "round_stop_on_timeout": bool(getattr(args, "stop_on_timeout")),
            }
        )
        if getattr(args, "finalize_after_rounds", False):
            payload["finalize_after_rounds"] = True
        round_timeout_seconds = _operation_http_timeout(
            float(getattr(args, "round_timeout")),
            windows=max_rounds * MAX_LIVE_AGENT_SEQUENCE_TURNS,
        )
        if getattr(args, "probe_bound_agents"):
            timeout_seconds += round_timeout_seconds
        else:
            timeout_seconds = connect_timeout_seconds + round_timeout_seconds
    return timeout_seconds


def _session_command_exit_code(response: dict[str, object]) -> int:
    if response.get("status") != "ready":
        return 1
    reply_probe = response.get("reply_probe") if isinstance(response.get("reply_probe"), dict) else None
    if reply_probe is not None and reply_probe.get("status") != "ok":
        return 1
    auto_rounds = response.get("auto_rounds") if isinstance(response.get("auto_rounds"), dict) else None
    if auto_rounds is not None and auto_rounds.get("status") not in {"answered", "complete"}:
        return 1
    finalization = response.get("finalization") if isinstance(response.get("finalization"), dict) else None
    if finalization is not None and finalization.get("status") not in {"finalized", "already_finalized"}:
        return 1
    return 0


def _maybe_wait_for_live_agent_session_ready(
    args: argparse.Namespace,
    response: dict[str, object],
) -> dict[str, object]:
    if not getattr(args, "wait_ready", False):
        return response
    meeting_id = str(response.get("meeting_id") or getattr(args, "meeting_id", "") or "").strip()
    group_id = str(response.get("group_id") or getattr(args, "group_id", "") or "").strip()
    if not meeting_id or not group_id:
        raise ValueError("Session readiness wait requires meeting_id and group_id in the session response.")
    initial_response = response
    if response.get("status") == "ready":
        initial_response = {**response, "status": "starting"}
    waited = _wait_for_live_agent_session_ready(
        server=str(args.server),
        meeting_id=meeting_id,
        group_id=group_id,
        timeout_seconds=float(args.wait_timeout),
        poll_interval_seconds=float(args.wait_poll_interval),
        initial_response=initial_response,
    )
    return _attach_session_post_ready_results(waited, response)


def _wait_for_live_agent_session_ready(
    *,
    server: str,
    meeting_id: str,
    group_id: str,
    timeout_seconds: float,
    poll_interval_seconds: float,
    initial_response: dict[str, object],
) -> dict[str, object]:
    poll_interval = max(0.01, poll_interval_seconds)
    deadline = time.monotonic() + timeout_seconds
    attempts = 0
    last_response = initial_response
    while True:
        now = time.monotonic()
        if attempts > 0 and now >= deadline:
            return last_response
        remaining_before_poll = max(0.01, deadline - now)
        attempts += 1
        try:
            response = _request_json(
                _live_agent_session_readiness_url(server, meeting_id, group_id),
                timeout_seconds=remaining_before_poll,
            )
        except (TimeoutError, urllib.error.URLError) as error:
            if not _is_live_agent_wait_timeout(error):
                raise
            return last_response
        last_response = response
        if response.get("status") == "ready":
            return response
        remaining_after_poll = max(0.0, deadline - time.monotonic())
        if remaining_after_poll > 0:
            time.sleep(min(poll_interval, remaining_after_poll))


def _run_live_agent_stop_session(args: argparse.Namespace) -> int:
    response = _request_json(
        _server_url(args.server, "/api/live-agent-sessions/stop"),
        method="POST",
        payload={
            "meeting_id": str(args.meeting_id or ""),
            "group_id": str(args.group_id or ""),
        },
        timeout_seconds=20.0,
    )
    if args.as_json:
        print(json.dumps(response, ensure_ascii=False, indent=2))
    else:
        print(_format_live_agent_session_stop(response))
    return 0 if response.get("status") == "stopped" else 1


def _run_live_agent_restart_session(args: argparse.Namespace) -> int:
    payload = {
        "meeting_id": str(args.meeting_id or ""),
        "group_id": str(args.group_id or ""),
        "connect_timeout_seconds": float(args.connect_timeout),
    }
    timeout_seconds = _session_remaining_rounds_request(
        args,
        payload,
        connect_timeout_seconds=float(args.connect_timeout),
    )
    response = _request_json(
        _server_url(args.server, "/api/live-agent-sessions/restart"),
        method="POST",
        payload=payload,
        timeout_seconds=timeout_seconds,
    )
    response = _maybe_wait_for_live_agent_session_ready(args, response)
    if args.as_json:
        print(json.dumps(response, ensure_ascii=False, indent=2))
    else:
        print(_format_live_agent_session_start(response))
    return _session_command_exit_code(response)


def _run_live_agent_recover_session(args: argparse.Namespace) -> int:
    payload = {
        "meeting_id": str(args.meeting_id or ""),
        "group_id": str(args.group_id or ""),
        "connect_timeout_seconds": float(args.connect_timeout),
    }
    timeout_seconds = _session_remaining_rounds_request(
        args,
        payload,
        connect_timeout_seconds=float(args.connect_timeout),
    )
    response = _request_json(
        _server_url(args.server, "/api/live-agent-sessions/recover"),
        method="POST",
        payload=payload,
        timeout_seconds=timeout_seconds,
    )
    response = _maybe_wait_for_live_agent_session_ready(args, response)
    if args.as_json:
        print(json.dumps(response, ensure_ascii=False, indent=2))
    else:
        print(_format_live_agent_session_start(response))
    return _session_command_exit_code(response)


def _run_live_agent_ensure_session(args: argparse.Namespace) -> int:
    _validate_session_auto_restart_args(args)
    action, response = _ensure_live_agent_session(args)
    if args.as_json:
        print(json.dumps({"action": action, "session": response}, ensure_ascii=False, indent=2))
    else:
        print(f"Ensured via {action}: {_format_live_agent_session_start(response)}")
    return _session_command_exit_code(response)


def _ensure_live_agent_session(args: argparse.Namespace) -> tuple[str, dict[str, object]]:
    initial = _initial_live_agent_session_readiness(args)
    action = session_ensure_action(initial)
    if action == "start" and _server_side_ensure_required_for_blank_meeting(args):
        payload = _session_start_payload(args)
        timeout_seconds = _session_remaining_rounds_request(
            args,
            payload,
            connect_timeout_seconds=float(args.connect_timeout),
        )
        response = _request_json(
            _server_url(str(args.server), "/api/live-agent-sessions/ensure"),
            method="POST",
            payload=payload,
            timeout_seconds=timeout_seconds,
        )
        ensured_action = str(response.get("action") or action)
        if ensured_action != "none":
            response = _wait_for_live_agent_session_ready_after_control(args, response)
        return ensured_action, response
    if action == "none":
        payload = _session_start_payload(args)
        timeout_seconds = _session_remaining_rounds_request(
            args,
            payload,
            connect_timeout_seconds=float(args.connect_timeout),
        )
        response = _request_json(
            _server_url(str(args.server), "/api/live-agent-sessions/ensure"),
            method="POST",
            payload=payload,
            timeout_seconds=timeout_seconds,
        )
        ensured_action = str(response.get("action") or action)
        if ensured_action != "none":
            response = _wait_for_live_agent_session_ready_after_control(args, response)
        return ensured_action, response
    payload = _ensure_live_agent_session_payload(args, action)
    timeout_seconds = _session_remaining_rounds_request(
        args,
        payload,
        connect_timeout_seconds=float(args.connect_timeout),
    )
    response = _request_json(
        _server_url(str(args.server), f"/api/live-agent-sessions/{action}"),
        method="POST",
        payload=payload,
        timeout_seconds=timeout_seconds,
    )
    response = _wait_for_live_agent_session_ready_after_control(args, response)
    return action, response


def _server_side_ensure_required_for_blank_meeting(args: argparse.Namespace) -> bool:
    return not str(getattr(args, "meeting_id", "") or "").strip() and bool(
        str(getattr(args, "group_id", "") or "").strip()
    )


def _wait_for_live_agent_session_ready_after_control(
    args: argparse.Namespace,
    response: dict[str, object],
) -> dict[str, object]:
    meeting_id = str(response.get("meeting_id") or getattr(args, "meeting_id", "") or "").strip()
    group_id = str(response.get("group_id") or getattr(args, "group_id", "") or "").strip()
    if meeting_id and group_id:
        wait_initial_response = response
        if response.get("status") == "ready":
            wait_initial_response = {**response, "status": "starting"}
        waited = _wait_for_live_agent_session_ready(
            server=str(args.server),
            meeting_id=meeting_id,
            group_id=group_id,
            timeout_seconds=float(args.wait_timeout),
            poll_interval_seconds=float(args.wait_poll_interval),
            initial_response=wait_initial_response,
        )
        return _attach_session_post_ready_results(waited, response)
    return response


def _initial_live_agent_session_readiness(args: argparse.Namespace) -> dict[str, object] | None:
    meeting_id = str(args.meeting_id or "").strip()
    group_id = str(args.group_id or "").strip()
    if not meeting_id or not group_id:
        return None
    try:
        return _request_json(
            _live_agent_session_readiness_url(str(args.server), meeting_id, group_id),
            timeout_seconds=10.0,
        )
    except ValueError as error:
        if "was not found" in str(error):
            return None
        raise


def _ensure_live_agent_session_payload(args: argparse.Namespace, action: str) -> dict[str, object]:
    if action == "start":
        return _session_start_payload(args)
    if action == "resume":
        return _session_resume_payload(args)
    return {
        "meeting_id": str(args.meeting_id or ""),
        "group_id": str(args.group_id or ""),
        "connect_timeout_seconds": float(args.connect_timeout),
    }


def _session_post_ready_checks_requested(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "probe_bound_agents", False) or getattr(args, "run_remaining_rounds", False))


def _attach_session_post_ready_results(
    response: dict[str, object],
    source: dict[str, object],
) -> dict[str, object]:
    if not isinstance(response, dict) or not isinstance(source, dict):
        return response
    merged = response
    for key in ("reply_probe", "auto_rounds", "finalization", "session_run"):
        value = source.get(key)
        if isinstance(value, dict):
            if merged is response:
                merged = dict(response)
            merged[key] = value
    return merged


def _run_live_agent_check_session(args: argparse.Namespace) -> int:
    response = _request_json(
        _server_url(args.server, "/api/live-agent-sessions/check"),
        method="POST",
        payload={
            "meeting_id": str(args.meeting_id or ""),
            "group_id": str(args.group_id or ""),
        },
        timeout_seconds=10.0,
    )
    if args.as_json:
        print(json.dumps(response, ensure_ascii=False, indent=2))
    else:
        print(_format_live_agent_session_check(response))
    return 1 if args.fail_on_degraded and response.get("status") != "ready" else 0


def _run_live_agent_session_readiness(args: argparse.Namespace) -> int:
    response = _request_json(
        _live_agent_session_readiness_url(
            str(args.server),
            str(args.meeting_id or ""),
            str(args.group_id or ""),
        ),
        timeout_seconds=10.0,
    )
    if args.as_json:
        print(json.dumps(response, ensure_ascii=False, indent=2))
    else:
        print(_format_live_agent_session_check(response))
    return 1 if args.fail_on_degraded and response.get("status") != "ready" else 0


def _live_agent_session_readiness_url(server: str, meeting_id: str, group_id: str) -> str:
    query = urllib.parse.urlencode(
        {
            "meeting_id": meeting_id,
            "group_id": group_id,
        }
    )
    return _server_url(server, f"/api/live-agent-sessions/readiness?{query}")


def _format_live_agent_session_start(response: dict[str, object]) -> str:
    status = str(response.get("status") or "unknown")
    meeting_id = str(response.get("meeting_id") or "unknown")
    group_id = str(response.get("group_id") or "unknown")
    connection = response.get("connection") if isinstance(response.get("connection"), dict) else {}
    expected = connection.get("expected", 0)
    connected = connection.get("connected", 0)
    attention = _live_agent_session_attention(response)
    suffix = f"; attention {', '.join(str(item) for item in attention)}" if attention else ""
    reply_probe = response.get("reply_probe") if isinstance(response.get("reply_probe"), dict) else None
    if reply_probe is not None:
        suffix += (
            f"; probes {reply_probe.get('status') or 'unknown'}: "
            f"{reply_probe.get('ok_count', 0)}/{reply_probe.get('probe_count', 0)} ok"
        )
    auto_rounds = response.get("auto_rounds") if isinstance(response.get("auto_rounds"), dict) else None
    if auto_rounds is not None:
        suffix += (
            f"; rounds {auto_rounds.get('status') or 'unknown'}: "
            f"{auto_rounds.get('round_count', 0)} rounds, "
            f"{auto_rounds.get('answered_round_count', 0)} answered, "
            f"{auto_rounds.get('completed_round_count', 0)} already complete, "
            f"{auto_rounds.get('timeout_round_count', 0)} timed out, "
            f"{auto_rounds.get('skipped_round_count', 0)} skipped"
        )
    finalization = response.get("finalization") if isinstance(response.get("finalization"), dict) else None
    if finalization is not None:
        suffix += (
            f"; finalization {finalization.get('status') or 'unknown'}: "
            f"{finalization.get('official_event_count', 0)} official events"
        )
    return f"Resident session {meeting_id} {status}; group {group_id}; {connected}/{expected} connected{suffix}"


def _live_agent_session_attention(response: dict[str, object]) -> list[object]:
    attention: list[object] = []
    seen = set()
    for section_name in ("connection", "process", "ownership"):
        section = response.get(section_name) if isinstance(response.get(section_name), dict) else {}
        values = section.get("attention") if isinstance(section.get("attention"), list) else []
        for value in values:
            key = str(value)
            if key in seen:
                continue
            seen.add(key)
            attention.append(value)
    return attention


def _format_live_agent_session_stop(response: dict[str, object]) -> str:
    status = str(response.get("status") or "unknown")
    meeting_id = str(response.get("meeting_id") or "unknown")
    group_id = str(response.get("group_id") or "unknown")
    offline = response.get("offline") if isinstance(response.get("offline"), dict) else {}
    expected = offline.get("expected", 0)
    stopped = offline.get("offline", 0)
    attention = offline.get("attention") if isinstance(offline.get("attention"), list) else []
    suffixes = []
    stopped_session_runs = _stopped_session_run_count(response)
    if stopped_session_runs:
        label = "session run" if stopped_session_runs == 1 else "session runs"
        suffixes.append(f"{stopped_session_runs} {label} stopped")
    if attention:
        suffixes.append(f"attention {', '.join(str(item) for item in attention)}")
    suffix = f"; {'; '.join(suffixes)}" if suffixes else ""
    return f"Resident session {meeting_id} {status}; group {group_id}; {stopped}/{expected} offline{suffix}"


def _stopped_session_run_count(response: dict[str, object]) -> int:
    runs = response.get("session_runs") if isinstance(response.get("session_runs"), list) else []
    return sum(1 for item in runs if isinstance(item, dict) and item.get("status") == "stopped")


def _format_live_agent_session_check(response: dict[str, object]) -> str:
    status = str(response.get("status") or "unknown")
    meeting_id = str(response.get("meeting_id") or "unknown")
    group_id = str(response.get("group_id") or "unknown")
    connection = response.get("connection") if isinstance(response.get("connection"), dict) else {}
    process = response.get("process") if isinstance(response.get("process"), dict) else {}
    expected = connection.get("expected", 0)
    connected = connection.get("connected", 0)
    process_status = str(process.get("status") or "unknown")
    attention = _live_agent_session_attention(response)
    suffix = f"; attention {', '.join(str(item) for item in attention)}" if attention else ""
    process_reason = response.get("process_reason") if isinstance(response.get("process_reason"), dict) else {}
    if process_reason:
        suffix += (
            f"; reason {process_reason.get('event_type') or 'unknown'} "
            f"{process_reason.get('reason') or 'unknown'}"
        )
    return f"Resident session {meeting_id} {status}; group {group_id}; {connected}/{expected} connected; process {process_status}{suffix}"


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


def _run_live_agent_resident(args: argparse.Namespace) -> int:
    config = config_from_args(args)
    setup_error = _resident_config_setup_error(config)
    if setup_error:
        raise ValueError(f"{config.agent_id}: {setup_error}")
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
    command_runner = _command_runner_for_config(config)
    runner = LiveAgentRunner(
        config,
        request_json=_request_json,
        command_runner=command_runner,
        sleep_fn=time.sleep,
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
    restore_signal_handlers = lambda: None
    try:
        restore_signal_handlers = _install_resident_shutdown_signal_handlers(shutdown_group)
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    except KeyboardInterrupt:
        shutdown_group()
        for thread in threads:
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
    _validate_session_auto_restart_args(args)
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
        print(f"Auto-joined via {action}: {_format_live_agent_session_start(response)}")
    return _session_command_exit_code(response)


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
    payload = _session_start_payload(args)
    timeout_seconds = _session_remaining_rounds_request(
        args,
        payload,
        connect_timeout_seconds=float(args.connect_timeout),
    )
    response = _request_json(
        _server_url(str(args.server), "/api/live-agent-session-runs/ensure"),
        method="POST",
        payload=payload,
        timeout_seconds=timeout_seconds,
    )
    action = str(response.get("action") or "ensure")
    if action != "none":
        response = _wait_for_live_agent_session_ready_after_control(args, response)
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
    evidence_basis = str(item.get("evidence_basis") or "").strip()
    operator_action = str(item.get("operator_action") or "").strip()
    approval = "approval required" if item.get("requires_approval") else ""
    parts = [command, entry_status, entry_mode, join_semantics, context_durability, evidence_basis, operator_action, approval]
    clean = [part for part in parts if part]
    return "entry " + " ".join(clean) if clean else ""


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
        result = _request_json(
            _server_url(args.server, "/api/live-agent-smoke"),
            method="POST",
            payload={"group_id": args.group_id, "timeout": float(args.timeout)},
            timeout_seconds=_operation_http_timeout(float(args.timeout)),
        )
    except (LiveAgentSmokeFailed, ValueError) as error:
        print(f"smoke failed: {error}", file=sys.stderr)
        return 1
    if args.as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"live-agent smoke ok: {result['group_id']}")
        for reply in result["replies"]:
            print(f"- {reply['actor_id']}: {reply['message']}")
    return 0


def _run_live_agent_session_smoke(args: argparse.Namespace) -> int:
    result = _request_json(
        _server_url(args.server, "/api/live-agent-session-smoke"),
        method="POST",
        payload={
            "group_id": str(args.group_id or ""),
            "meeting_id": str(args.meeting_id or ""),
            "timeout": float(args.timeout),
            "lobby_probe_count": int(args.lobby_probe_count),
            "soak_cycle_count": int(args.soak_cycle_count),
            "soak_interval_seconds": float(args.soak_interval_seconds),
        },
        timeout_seconds=_session_smoke_http_timeout(
            float(args.timeout),
            lobby_probe_count=int(args.lobby_probe_count),
            soak_cycle_count=int(args.soak_cycle_count),
            soak_interval_seconds=float(args.soak_interval_seconds),
        ),
    )
    if args.as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(_format_live_agent_session_smoke(result))
    return 0 if result.get("status") == "ok" else 1


def _run_live_agent_real_session_smoke(args: argparse.Namespace) -> int:
    if not bool(args.approve_real_providers):
        result = _unapproved_real_session_smoke_result(args)
    else:
        result = _request_json(
            _server_url(args.server, "/api/live-agent-real-session-smoke"),
            method="POST",
            payload={
                "group_id": str(args.group_id or ""),
                "meeting_id": str(args.meeting_id or ""),
                "timeout": float(args.timeout),
                "live_agent_config_path": str(args.live_agent_config or ""),
                "council_config_path": str(args.council_config or ""),
                "agent_config_path": str(args.agent_config or ""),
                "approve_real_providers": True,
            },
            timeout_seconds=_real_session_smoke_http_timeout(float(args.timeout)),
        )
    if args.as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(_format_live_agent_real_session_smoke(result))
    return 0 if result.get("status") == "ok" else 1


def _unapproved_real_session_smoke_result(args: argparse.Namespace) -> dict[str, object]:
    return {
        "status": "approval_required",
        "meeting_id": _safe_cli_smoke_id(args.meeting_id),
        "group_id": _safe_cli_smoke_id(args.group_id),
        "approval_required": True,
        "approved": False,
        "diagnostic": True,
        "reason": "current_operator_approval_required",
    }


def _safe_cli_smoke_id(value: object) -> str:
    text = clean_lobby_text(value, limit=128)
    return "".join(char if char.isalnum() or char in "_.-" else "-" for char in text).strip(".-")


def _run_live_agent_official_round_smoke(args: argparse.Namespace) -> int:
    result = _request_json(
        _server_url(args.server, "/api/live-agent-official-round-smoke"),
        method="POST",
        payload={"group_id": args.group_id, "timeout": float(args.timeout)},
        timeout_seconds=_operation_http_timeout(float(args.timeout), windows=4),
    )
    if args.as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(
            f"official round smoke {result.get('status') or 'unknown'}: "
            f"{result.get('group_id') or args.group_id or 'smoke'} "
            f"({result.get('answered_count', 0)} answered, "
            f"{result.get('timeout_count', 0)} timed out, "
            f"{result.get('skipped_count', 0)} skipped)"
        )
    return 0 if result.get("status") == "ok" else 1


def _format_live_agent_real_session_smoke(result: dict[str, object]) -> str:
    return (
        f"real resident session smoke {result.get('status') or 'unknown'}: "
        f"{result.get('meeting_id') or 'real-session-smoke'} "
        f"group {result.get('group_id') or 'real-session-smoke'}; "
        f"start {result.get('start_status') or 'unknown'}; "
        f"probes {result.get('reply_probe_status') or 'unknown'}: "
        f"{result.get('reply_probe_ok_count', 0)}/{result.get('reply_probe_count', 0)} ok; "
        f"stop {result.get('stop_status') or 'unknown'}; "
        f"post-stop {result.get('post_stop_process_status') or 'unknown'}"
    )


def _format_live_agent_session_smoke(result: dict[str, object]) -> str:
    expected_replies = result.get("expected_reply_count", 0)
    lobby_probe_count = max(1, int(result.get("lobby_probe_count") or 1))
    expected_reply_total = int(expected_replies) * lobby_probe_count
    soak_cycle_count = max(0, int(result.get("soak_cycle_count") or 0))
    soak_part = ""
    if soak_cycle_count:
        soak_expected_total = int(expected_replies) * soak_cycle_count
        soak_part = f"soak {result.get('soak_reply_count', 0)}/{soak_expected_total} replies over {soak_cycle_count} cycles; "
    return (
        f"resident session smoke {result.get('status') or 'unknown'}: "
        f"{result.get('meeting_id') or 'session-smoke'} "
        f"group {result.get('group_id') or 'session-smoke'}; "
        f"rounds {result.get('rounds_status') or 'unknown'} "
        f"({result.get('answered_round_count', 0)} answered); "
        f"{lobby_probe_count} lobby probes; "
        f"{result.get('reply_count', 0)}/{expected_reply_total} replies; "
        f"post-restart {result.get('post_restart_reply_count', 0)}/{expected_reply_total} replies; "
        f"post-recover {result.get('post_recover_reply_count', 0)}/{expected_reply_total} replies; "
        f"{soak_part}"
        f"post-stop {result.get('post_stop_process_status') or 'unknown'}; "
        f"start {result.get('start_status') or 'unknown'}, "
        f"check {result.get('check_status') or 'unknown'}, "
        f"resume {result.get('resume_status') or 'unknown'}, "
        f"restart {result.get('restart_status') or 'unknown'}, "
        f"recover {result.get('recover_status') or 'unknown'}, "
        f"stop {result.get('stop_status') or 'unknown'}"
    )


def _run_live_agent_doctor(args: argparse.Namespace) -> int:
    payload = {"group_id": args.group_id, "timeout": float(args.timeout)}
    if args.official_round_smoke:
        payload["official_round_smoke"] = True
    if args.session_smoke:
        payload["session_smoke"] = True
        if int(args.session_smoke_soak_cycles):
            payload["session_smoke_soak_cycle_count"] = int(args.session_smoke_soak_cycles)
        if float(args.session_smoke_soak_interval):
            payload["session_smoke_soak_interval_seconds"] = float(args.session_smoke_soak_interval)
    if args.probe_agent_ids:
        payload["probe_agent_ids"] = list(args.probe_agent_ids)
    if args.probe_group_ids:
        payload["probe_group_ids"] = list(args.probe_group_ids)
    probe_windows = MAX_READINESS_PROBE_AGENTS if args.probe_group_ids else min(len(args.probe_agent_ids), MAX_READINESS_PROBE_AGENTS)
    official_round_windows = 4 if args.official_round_smoke else 0
    timeout_seconds = _operation_http_timeout(float(args.timeout), windows=1 + official_round_windows + probe_windows)
    if args.session_smoke:
        timeout_seconds += _session_smoke_http_timeout(
            float(args.timeout),
            soak_cycle_count=int(args.session_smoke_soak_cycles),
            soak_interval_seconds=float(args.session_smoke_soak_interval),
        )
    payload = _request_json(
        _server_url(args.server, "/api/live-agent-readiness"),
        method="POST",
        payload=payload,
        timeout_seconds=timeout_seconds,
    )
    if args.as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(_format_live_agent_readiness(payload))
    return 0 if payload.get("status") == "ready" else 1


def _run_live_agent_probe(args: argparse.Namespace) -> int:
    agent_id = urllib.parse.quote(args.agent_id, safe="")
    payload = _request_json(
        _server_url(args.server, f"/api/live-agents/{agent_id}/probe"),
        method="POST",
        payload={"timeout_seconds": float(args.timeout)},
        timeout_seconds=_probe_http_timeout(float(args.timeout)),
    )
    if args.as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(_format_live_agent_probe(payload))
    return 0 if payload.get("status") == "ok" else 1


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
    official_round_smoke = payload.get("official_round_smoke") if isinstance(payload.get("official_round_smoke"), dict) else {}
    agents = health.get("agents") if isinstance(health.get("agents"), dict) else {}
    processes = health.get("processes") if isinstance(health.get("processes"), dict) else {}
    connections = health.get("connections") if isinstance(health.get("connections"), dict) else {}
    sessions = health.get("sessions") if isinstance(health.get("sessions"), dict) else {}
    agent_attention = agents.get("attention") if isinstance(agents.get("attention"), list) else []
    process_attention = processes.get("attention") if isinstance(processes.get("attention"), list) else []
    connection_attention = connections.get("attention") if isinstance(connections.get("attention"), list) else []
    session_attention = sessions.get("attention") if isinstance(sessions.get("attention"), list) else []
    process_reasons = _process_reason_summary(processes.get("reasons"))
    smoke_suffix = str(smoke.get("group_id") or "").strip()
    smoke_label = f"{smoke.get('status') or 'unknown'} {smoke_suffix}".strip()
    lines = [
        f"readiness: {payload.get('status') or 'unknown'}",
        f"health: {health.get('status') or 'unknown'}",
        f"smoke: {smoke_label}",
        f"agent attention: {_attention_summary(agent_attention)}",
        f"process attention: {_attention_summary(process_attention)}",
        f"connection attention: {_attention_summary(connection_attention)}",
        f"session attention: {_attention_summary(session_attention)}",
    ]
    if process_reasons:
        lines.append(f"process reasons: {process_reasons}")
    probes = payload.get("probes") if isinstance(payload.get("probes"), list) else []
    if probes:
        lines.append(f"probes: {_readiness_probe_summary(probes)}")
    if official_round_smoke:
        lines.append(f"official round smoke: {_official_round_smoke_summary(official_round_smoke)}")
    session_smoke = payload.get("session_smoke") if isinstance(payload.get("session_smoke"), dict) else {}
    if session_smoke:
        lines.append(f"session smoke: {_session_smoke_summary(session_smoke)}")
    probe_groups = payload.get("probe_groups") if isinstance(payload.get("probe_groups"), list) else []
    if probe_groups:
        lines.append(f"probe groups: {_readiness_probe_group_summary(probe_groups)}")
    if payload.get("probe_error"):
        lines.append(f"probe error: {payload.get('probe_error')}")
    if smoke.get("error"):
        lines.append(f"smoke error: {smoke.get('error')}")
    return "\n".join(lines)


def _format_live_agent_probe(payload: dict[str, object]) -> str:
    lines = [
        f"probe: {payload.get('status') or 'unknown'}",
        f"agent: {payload.get('agent_id') or 'unknown'}",
    ]
    if payload.get("agent_status"):
        lines.append(f"agent status: {payload.get('agent_status')}")
    if payload.get("source_event_id"):
        lines.append(f"source: {payload.get('source_event_id')}")
    if payload.get("reply_event_id"):
        lines.append(f"reply: {payload.get('reply_event_id')}")
    if payload.get("reason"):
        lines.append(f"reason: {payload.get('reason')}")
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
    processes = payload.get("processes") if isinstance(payload.get("processes"), dict) else {}
    process_monitor = payload.get("process_monitor") if isinstance(payload.get("process_monitor"), dict) else {}
    connections = payload.get("connections") if isinstance(payload.get("connections"), dict) else {}
    sessions = payload.get("sessions") if isinstance(payload.get("sessions"), dict) else {}
    observations = payload.get("observations") if isinstance(payload.get("observations"), dict) else {}
    session_runs = payload.get("session_runs") if isinstance(payload.get("session_runs"), dict) else {}
    session_run_monitor = payload.get("session_run_monitor") if isinstance(payload.get("session_run_monitor"), dict) else {}
    agent_counts = agents.get("counts") if isinstance(agents.get("counts"), dict) else {}
    process_counts = processes.get("counts") if isinstance(processes.get("counts"), dict) else {}
    agent_attention = agents.get("attention") if isinstance(agents.get("attention"), list) else []
    process_attention = processes.get("attention") if isinstance(processes.get("attention"), list) else []
    process_reasons = _process_reason_summary(processes.get("reasons"))
    connection_attention = connections.get("attention") if isinstance(connections.get("attention"), list) else []
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
    process_monitor_summary = _process_monitor_summary(process_monitor)
    if process_monitor_summary:
        lines.append(f"process monitor: {process_monitor_summary}")
    if process_reasons:
        lines.append(f"process reasons: {process_reasons}")
    lines.extend(
        [
            f"connections: {connections.get('connected', 0)} connected / {connections.get('expected', 0)} expected",
            f"connection attention: {_attention_summary(connection_attention)}",
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


def _run_live_agent_processes(args: argparse.Namespace) -> int:
    if args.live_agent_process_command == "list":
        payload = _request_json(_server_url(args.server, "/api/live-agent-processes"))
        _print_live_agent_process_payload(payload, as_json=args.as_json)
        if args.fail_on_attention and _live_agent_process_payload_needs_attention(payload):
            return 1
        return 0
    if args.live_agent_process_command == "events":
        params = {"limit": args.limit}
        if args.scan_limit is not None:
            params["scan_limit"] = args.scan_limit
        if args.group_id:
            params["group_id"] = args.group_id
        query = urllib.parse.urlencode(params)
        payload = _request_json(_server_url(args.server, f"/api/live-agent-process-events?{query}"))
        _print_live_agent_process_events_payload(payload, as_json=args.as_json)
        return 0
    if args.live_agent_process_command == "wait-event":
        return _run_live_agent_process_event_wait(args)
    if args.live_agent_process_command == "wait":
        return _run_live_agent_process_wait(args)
    if args.live_agent_process_command == "start":
        if args.auto_restart and args.max_restarts <= 0:
            raise ValueError("--auto-restart requires --max-restarts greater than 0.")
        if args.stale_restart_after_seconds > 0 and (not args.auto_restart or args.max_restarts <= 0):
            raise ValueError("--stale-restart-after-seconds requires --auto-restart and --max-restarts greater than 0.")
        payload = {
            "config_path": args.config,
            "server": args.server,
            "auto_restart": args.auto_restart,
            "max_restarts": args.max_restarts,
            "restart_backoff_seconds": args.restart_backoff_seconds,
        }
        if args.stale_restart_after_seconds > 0:
            payload["stale_restart_after_seconds"] = args.stale_restart_after_seconds
        if args.group_id:
            payload["group_id"] = args.group_id
        response = _request_json(
            _server_url(args.server, "/api/live-agent-processes/start"),
            method="POST",
            payload=payload,
        )
        _print_live_agent_process_payload(response, as_json=args.as_json, action="start")
        return 0
    if args.live_agent_process_command in {"stop", "restart", "recover"}:
        group_id = urllib.parse.quote(args.group_id, safe="")
        response = _request_json(
            _server_url(args.server, f"/api/live-agent-processes/{group_id}/{args.live_agent_process_command}"),
            method="POST",
            payload={},
        )
        _print_live_agent_process_payload(response, as_json=args.as_json, action=args.live_agent_process_command)
        return 0
    if args.live_agent_process_command == "stop-running":
        response = _request_json(
            _server_url(args.server, "/api/live-agent-processes/stop-running"),
            method="POST",
            payload={},
        )
        _print_live_agent_process_payload(response, as_json=args.as_json, action="stop-running")
        return 0
    return 1


def _run_live_agent_process_wait(args: argparse.Namespace) -> int:
    timeout_seconds = float(args.timeout)
    poll_interval = max(0.01, float(args.poll_interval))
    deadline = time.monotonic() + timeout_seconds
    attempts = 0
    last_group: dict[str, object] | None = None
    while True:
        now = time.monotonic()
        if attempts > 0 and now >= deadline:
            _print_live_agent_process_wait_result(
                {
                    "status": "timeout",
                    "group_id": args.group_id,
                    "timeout_seconds": timeout_seconds,
                    "attempts": attempts,
                    "group": last_group,
                },
                as_json=args.as_json,
            )
            return 1
        remaining_before_poll = max(0.01, deadline - now)
        attempts += 1
        try:
            payload = _request_json(
                _server_url(args.server, "/api/live-agent-processes"),
                timeout_seconds=remaining_before_poll,
            )
        except (TimeoutError, urllib.error.URLError) as error:
            if not _is_live_agent_process_wait_timeout(error):
                raise
            _print_live_agent_process_wait_result(
                {
                    "status": "timeout",
                    "group_id": args.group_id,
                    "timeout_seconds": timeout_seconds,
                    "attempts": attempts,
                    "group": last_group,
                    "error": str(error) or error.__class__.__name__,
                },
                as_json=args.as_json,
            )
            return 1
        group = _find_live_agent_process_group(payload, args.group_id)
        last_group = group
        if group is not None and _live_agent_process_group_ready(group):
            _print_live_agent_process_wait_result(
                {
                    "status": "ready",
                    "group_id": args.group_id,
                    "timeout_seconds": timeout_seconds,
                    "attempts": attempts,
                    "group": group,
                },
                as_json=args.as_json,
            )
            return 0
        remaining_after_poll = max(0.0, deadline - time.monotonic())
        if remaining_after_poll > 0:
            time.sleep(min(poll_interval, remaining_after_poll))


def _run_live_agent_process_event_wait(args: argparse.Namespace) -> int:
    timeout_seconds = float(args.timeout)
    poll_interval = max(0.01, float(args.poll_interval))
    deadline = time.monotonic() + timeout_seconds
    attempts = 0
    last_payload: dict[str, object] | None = None
    last_event: dict[str, object] | None = None
    while True:
        now = time.monotonic()
        if attempts > 0 and now >= deadline:
            _print_live_agent_process_event_wait_result(
                _live_agent_process_event_wait_result("timeout", args, timeout_seconds, attempts, last_event, last_payload),
                as_json=args.as_json,
            )
            return 1
        remaining_before_poll = max(0.01, deadline - now)
        attempts += 1
        try:
            payload = _request_json(
                _server_url(args.server, _live_agent_process_event_wait_path(args)),
                timeout_seconds=remaining_before_poll,
            )
        except (TimeoutError, urllib.error.URLError) as error:
            if not _is_live_agent_process_wait_timeout(error):
                raise
            _print_live_agent_process_event_wait_result(
                _live_agent_process_event_wait_result(
                    "timeout",
                    args,
                    timeout_seconds,
                    attempts,
                    last_event,
                    last_payload,
                    error=str(error) or error.__class__.__name__,
                ),
                as_json=args.as_json,
            )
            return 1
        last_payload = payload
        payload_last_event = _last_live_agent_process_event(payload)
        if payload_last_event is not None:
            last_event = payload_last_event
        event = _find_live_agent_process_event(
            payload,
            args.event_type,
            group_id=args.group_id,
            status=args.status,
            after_timestamp=args.after_timestamp,
        )
        if event is not None:
            _print_live_agent_process_event_wait_result(
                _live_agent_process_event_wait_result("observed", args, timeout_seconds, attempts, event, payload),
                as_json=args.as_json,
            )
            return 0
        remaining_after_poll = max(0.0, deadline - time.monotonic())
        if remaining_after_poll > 0:
            time.sleep(min(poll_interval, remaining_after_poll))


def _live_agent_process_event_wait_path(args: argparse.Namespace) -> str:
    params: dict[str, object] = {"limit": args.limit}
    if args.scan_limit is not None:
        params["scan_limit"] = args.scan_limit
    if args.group_id:
        params["group_id"] = args.group_id
    return f"/api/live-agent-process-events?{urllib.parse.urlencode(params)}"


def _find_live_agent_process_event(
    payload: dict[str, object],
    event_type: str,
    *,
    group_id: str = "",
    status: str = "",
    after_timestamp: str = "",
) -> dict[str, object] | None:
    events = payload.get("events") if isinstance(payload.get("events"), list) else []
    for item in events:
        if not isinstance(item, dict):
            continue
        if str(item.get("event_type") or "") != event_type:
            continue
        if group_id and str(item.get("group_id") or "") != clean_live_agent_group_id(group_id):
            continue
        if status and str(item.get("status") or "") != status:
            continue
        timestamp = str(item.get("timestamp") or "")
        if after_timestamp and timestamp <= after_timestamp:
            continue
        return item
    return None


def _last_live_agent_process_event(payload: dict[str, object] | None) -> dict[str, object] | None:
    events = payload.get("events") if isinstance(payload, dict) and isinstance(payload.get("events"), list) else []
    for item in reversed(events):
        if isinstance(item, dict):
            return item
    return None


def _live_agent_process_event_wait_result(
    status: str,
    args: argparse.Namespace,
    timeout_seconds: float,
    attempts: int,
    event: dict[str, object] | None,
    payload: dict[str, object] | None,
    *,
    error: str = "",
) -> dict[str, object]:
    result: dict[str, object] = {
        "status": status,
        "event_type": args.event_type,
        "group_id": args.group_id,
        "event_status": args.status,
        "after_timestamp": args.after_timestamp,
        "timeout_seconds": timeout_seconds,
        "attempts": attempts,
        "event": event,
    }
    if status == "timeout" and isinstance(payload, dict):
        result["truncated"] = payload.get("truncated") is True
        if isinstance(payload.get("events"), list):
            result["events"] = payload.get("events")
    if error:
        result["error"] = error
    return result


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
            "health_session_attention",
            "health_connection_attention",
            "health_agent_attention",
            "session_smoke_reply_count",
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


def _print_live_agent_process_payload(payload: dict[str, object], *, as_json: bool, action: str = "list") -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    if action == "stop-running":
        print(_format_live_agent_process_bulk_stop(payload))
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


def _live_agent_process_payload_needs_attention(payload: dict[str, object]) -> bool:
    groups = payload.get("groups") if isinstance(payload.get("groups"), list) else []
    for item in groups:
        if isinstance(item, dict) and _live_agent_process_group_needs_attention(item):
            return True
    return False


def _live_agent_process_group_needs_attention(group: dict[str, object]) -> bool:
    status = str(group.get("status") or "").strip()
    if status in {"error", "unknown", "restarting"}:
        return True
    connection = group.get("agent_connection") if isinstance(group.get("agent_connection"), dict) else {}
    attention = connection.get("attention") if isinstance(connection, dict) else []
    return isinstance(attention, list) and bool(attention)


def _find_live_agent_process_group(payload: dict[str, object], group_id: str) -> dict[str, object] | None:
    groups = payload.get("groups") if isinstance(payload.get("groups"), list) else []
    for item in groups:
        if isinstance(item, dict) and str(item.get("group_id") or "") == group_id:
            return item
    return None


def _is_live_agent_process_wait_timeout(error: BaseException) -> bool:
    return _is_live_agent_wait_timeout(error)


def _is_live_agent_wait_timeout(error: BaseException) -> bool:
    if isinstance(error, TimeoutError):
        return True
    if isinstance(error, urllib.error.URLError):
        return isinstance(getattr(error, "reason", None), TimeoutError)
    return False


def _live_agent_process_group_ready(group: dict[str, object]) -> bool:
    if str(group.get("status") or "").strip() != "running":
        return False
    if _live_agent_process_group_needs_attention(group):
        return False
    connection = group.get("agent_connection") if isinstance(group.get("agent_connection"), dict) else {}
    expected = _safe_int(connection.get("expected")) if isinstance(connection, dict) else 0
    connected = _safe_int(connection.get("connected")) if isinstance(connection, dict) else 0
    return expected <= 0 or connected >= expected


def _print_live_agent_process_wait_result(result: dict[str, object], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    group_id = str(result.get("group_id") or "unknown")
    timeout_seconds = _safe_float(result.get("timeout_seconds"))
    group = result.get("group") if isinstance(result.get("group"), dict) else None
    if result.get("status") == "ready":
        suffix = f": {_format_live_agent_process_group(group)}" if group is not None else ""
        print(f"Process group {group_id} ready{suffix}")
        return
    if group is None:
        print(f"Process group {group_id} not ready after {timeout_seconds:.1f}s: group not found")
        return
    print(f"Process group {group_id} not ready after {timeout_seconds:.1f}s: {_format_live_agent_process_group(group)}")


def _print_live_agent_process_event_wait_result(result: dict[str, object], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    event = result.get("event") if isinstance(result.get("event"), dict) else None
    if result.get("status") == "observed":
        suffix = f": {_format_live_agent_process_event(event)}" if event is not None else ""
        print(f"Observed live-agent process event{suffix}")
        return
    parts = [str(result.get("event_type") or "unknown")]
    if result.get("group_id"):
        parts.append(f"group {result.get('group_id')}")
    if result.get("event_status"):
        parts.append(f"status {result.get('event_status')}")
    if result.get("after_timestamp"):
        parts.append(f"after {result.get('after_timestamp')}")
    timeout_seconds = _safe_float(result.get("timeout_seconds"))
    print(f"Timed out waiting for live-agent process event {' '.join(parts)} after {timeout_seconds:.1f}s")
    if event is not None:
        print(f"last event: {_format_live_agent_process_event(event)}")
    if result.get("truncated") is True:
        print("searched bounded lifecycle history; older matches may exist")


def _print_live_agent_process_events_payload(payload: dict[str, object], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    events = payload.get("events") if isinstance(payload.get("events"), list) else []
    if not events:
        print("no live-agent process events")
    else:
        for item in events:
            if isinstance(item, dict):
                print(_format_live_agent_process_event(item))
    scan_notice = _format_live_agent_process_event_scan_notice(payload)
    if scan_notice:
        print(scan_notice)


def _format_live_agent_process_event(event: dict[str, object]) -> str:
    timestamp = str(event.get("timestamp") or "-")
    group_id = str(event.get("group_id") or "unknown")
    event_type = str(event.get("event_type") or "unknown")
    status = str(event.get("status") or "unknown")
    parts = [timestamp, group_id, event_type, status]
    pid = event.get("pid")
    if pid not in (None, ""):
        parts.append(f"pid {pid}")
    returncode = event.get("returncode")
    if returncode not in (None, ""):
        parts.append(f"returncode {returncode}")
    parts.append(f"restarts {_safe_int(event.get('restart_count'))}/{_safe_int(event.get('max_restarts'))}")
    next_restart_at = str(event.get("next_restart_at") or "").strip()
    if next_restart_at:
        parts.append(f"next restart {next_restart_at}")
    previous_status = str(event.get("previous_status") or "").strip()
    if previous_status:
        parts.append(f"previous {previous_status}")
    reason = _format_live_agent_process_event_reason(event.get("reason"))
    if reason:
        parts.append(f"reason {reason}")
    offline = _live_agent_process_offline_summary(event.get("offline"))
    if offline:
        parts.append(offline)
    attention = _format_live_agent_process_offline_attention(event.get("offline"))
    if attention:
        parts.append(attention)
    return " ".join(parts)


def _format_live_agent_process_event_scan_notice(payload: dict[str, object]) -> str:
    if payload.get("truncated") is not True:
        return ""
    scanned = _safe_int(payload.get("scanned_event_count")) or _safe_int(payload.get("scan_limit"))
    if scanned <= 0:
        return "searched bounded lifecycle history; older matches may exist"
    return f"searched recent {scanned} lifecycle events; older matches may exist"


def _format_live_agent_process_bulk_stop(payload: dict[str, object]) -> str:
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    stopped_count = _safe_int(result.get("stopped_count"))
    failed_count = _safe_int(result.get("failed_count"))
    skipped_count = _safe_int(result.get("skipped_count"))
    summary = f"Stopped {stopped_count} live-agent process groups"
    details = []
    offline = _live_agent_process_bulk_offline_summary(result.get("stopped"))
    if offline:
        details.append(offline)
    if failed_count:
        details.append(f"failed {failed_count}")
    if skipped_count:
        details.append(f"skipped {skipped_count}")
    return f"{summary} ({', '.join(details)})" if details else summary


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
    connection = _format_live_agent_process_connection(group.get("agent_connection"))
    last_event = _format_live_agent_process_last_event(group.get("recent_events"))
    stale_watchdog = _format_live_agent_process_stale_watchdog(group.get("stale_restart_after_seconds"))
    next_restart = _format_live_agent_process_next_restart(group.get("next_restart_at"))
    suffix_parts = [part for part in (config_path, agents, connection, stale_watchdog, next_restart, last_event) if part]
    suffix = f" {'; '.join(suffix_parts)}" if suffix_parts else ""
    return f"{group_id}: {status} ({pid_text}, {auto_restart}, restarts {restart_count}/{max_restarts}){suffix}"


def _format_live_agent_process_action(group: dict[str, object], action: str) -> str:
    group_id = str(group.get("group_id") or "unknown")
    status = str(group.get("status") or "unknown")
    pid = group.get("pid")
    if action == "start":
        return f"Started {group_id} (pid {pid if pid not in (None, '') else '-'})"
    if action == "stop":
        offline = _live_agent_process_offline_summary(group.get("offline"))
        suffix = f", {offline}" if offline else ""
        return f"Stopped {group_id} ({status}{suffix})"
    if action == "restart":
        return f"Restarted {group_id} (pid {pid if pid not in (None, '') else '-'})"
    if action == "recover":
        previous_status = str(group.get("recovered_from_status") or "unknown")
        return f"Recovered {group_id} from {previous_status} (pid {pid if pid not in (None, '') else '-'})"
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


def _format_live_agent_process_connection(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    expected = _safe_int(value.get("expected"))
    connected = _safe_int(value.get("connected"))
    if expected <= 0 and connected <= 0 and not value.get("attention"):
        return ""
    attention = _format_live_agent_process_connection_attention(value.get("attention"))
    suffix = f"; {attention}" if attention else ""
    return f"agents connected {connected}/{expected}{suffix}"


def _format_live_agent_process_stale_watchdog(value: object) -> str:
    seconds = _safe_float(value)
    if seconds <= 0:
        return ""
    if seconds.is_integer():
        return f"stale watchdog {int(seconds)}s"
    return f"stale watchdog {seconds:.1f}s"


def _format_live_agent_process_next_restart(value: object) -> str:
    timestamp = str(value or "").strip()
    return f"next restart {timestamp}" if timestamp else ""


def _format_live_agent_process_connection_attention(value: object) -> str:
    if not isinstance(value, list):
        return ""
    labels = []
    for item in value:
        if not isinstance(item, dict):
            continue
        agent_id = str(item.get("agent_id") or "").strip()
        status = str(item.get("status") or "").strip()
        if agent_id and status:
            labels.append(f"{status} {agent_id}")
    return ", ".join(labels)


def _live_agent_process_offline_summary(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    expected = _safe_int(value.get("expected"))
    offline = _safe_int(value.get("offline"))
    if expected <= 0:
        return ""
    return f"offline {offline}/{expected}"


def _live_agent_process_bulk_offline_summary(records: object) -> str:
    if not isinstance(records, list):
        return ""
    expected = 0
    offline = 0
    for record in records:
        if not isinstance(record, dict):
            continue
        summary = record.get("offline")
        if not isinstance(summary, dict):
            continue
        expected += _safe_int(summary.get("expected"))
        offline += _safe_int(summary.get("offline"))
    if expected <= 0:
        return ""
    return f"offline {offline}/{expected}"


def _format_live_agent_process_last_event(value: object) -> str:
    if not isinstance(value, list) or not value:
        return ""
    latest = _latest_live_agent_process_event(value)
    if latest is None:
        return ""
    event_type = str(latest.get("event_type") or "").strip()
    offline = _format_live_agent_process_last_offline_event(value, latest_event=latest)
    reason = _format_live_agent_process_last_reason_event(value, latest_event=latest)
    suffix = ", ".join(detail for detail in (offline, reason) if detail)
    suffix = f", {suffix}" if suffix else ""
    return f"last event {event_type}{suffix}"


def _latest_live_agent_process_event(value: list[object]) -> dict[str, object] | None:
    for item in reversed(value):
        if not isinstance(item, dict):
            continue
        if str(item.get("event_type") or "").strip():
            return item
    return None


def _format_live_agent_process_last_offline_event(
    value: list[object],
    *,
    latest_event: dict[str, object],
) -> str:
    for item in reversed(value):
        if not isinstance(item, dict):
            continue
        event_type = str(item.get("event_type") or "").strip()
        if not event_type:
            continue
        offline = _live_agent_process_offline_summary(item.get("offline"))
        if not offline:
            continue
        attention = _format_live_agent_process_offline_attention(item.get("offline"))
        details = ", ".join(detail for detail in (offline, attention) if detail)
        if item is latest_event:
            return details
        return f"last offline {event_type} {details}"
    return ""


def _format_live_agent_process_last_reason_event(
    value: list[object],
    *,
    latest_event: dict[str, object],
) -> str:
    for item in reversed(value):
        if not isinstance(item, dict):
            continue
        reason = _format_live_agent_process_event_reason(item.get("reason"))
        if not reason:
            continue
        if item is latest_event:
            return f"reason {reason}"
        event_type = str(item.get("event_type") or "").strip()
        return f"last reason {event_type} {reason}" if event_type else f"last reason {reason}"
    return ""


def _format_live_agent_process_event_reason(value: object) -> str:
    return str(value or "").strip()


def _format_live_agent_process_offline_attention(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    attention = value.get("attention")
    if not isinstance(attention, list):
        return ""
    labels = []
    for item in attention[:10]:
        if not isinstance(item, dict):
            continue
        agent_id = str(item.get("agent_id") or "").strip()
        status = str(item.get("status") or "").strip()
        if agent_id and status:
            labels.append(f"{status} {agent_id}")
    return ", ".join(labels)


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
        sleep_interval = max(float(args.poll_interval), 0.05)
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
    return {
        "status": "event",
        "agent_id": args.agent_id,
        "source_event_id": event_id,
        "auto_chain_depth": auto_chain_depth,
        "event": event,
        "reply_command": [
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
            "--",
            "<reply>",
        ],
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
            payload = _wait_turn_request_payload(args, room, candidate)
            if args.as_json:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                print(_format_wait_turn_request(payload))
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
        sleep_interval = max(float(args.poll_interval), 0.05)
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


def _wait_room_context(room: dict[str, object], *, meeting_id: str) -> dict[str, object]:
    context: dict[str, object] = {
        "meeting_id": meeting_id,
        "lobby_event_count": len(room.get("lobby_events") if isinstance(room.get("lobby_events"), list) else []),
        "live_event_count": len(room.get("live_events") if isinstance(room.get("live_events"), list) else []),
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


def _run_live_agent_wait_next(args: argparse.Namespace) -> int:
    deadline = time.monotonic() + float(args.timeout)
    last_room: dict[str, object] = {}
    while True:
        agent_id = urllib.parse.quote(args.agent_id, safe="")
        room = _request_json(_server_url(args.server, f"/api/live-agents/{agent_id}/room"))
        last_room = room
        official_candidate = _wait_turn_request_candidate(args, room)
        if official_candidate is not None:
            payload = _wait_turn_request_payload(args, room, official_candidate)
            payload["action"] = "official_turn"
            if args.as_json:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
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
                print(f"no next action after lobby {lobby_cursor}, official {live_cursor}")
            return 1
        sleep_interval = max(float(args.poll_interval), 0.05)
        time.sleep(min(sleep_interval, remaining))


def _wait_next_timeout_payload(args: argparse.Namespace, room: dict[str, object]) -> dict[str, object]:
    agent = room.get("agent") if isinstance(room.get("agent"), dict) else {}
    lobby_cursor = str(args.after_event_id or agent.get("last_observed_event_id") or "").strip()
    live_cursor = str(args.after_live_event_id or agent.get("last_observed_live_event_id") or "").strip()
    return {
        "status": "timeout",
        "agent_id": args.agent_id,
        "timeout_seconds": float(args.timeout),
        "last_observed_event_id": _latest_observed_event_id(room.get("lobby_events"), lobby_cursor),
        "last_observed_live_event_id": _latest_observed_event_id(room.get("live_events"), live_cursor),
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
    def __init__(self, *, idle_timeout_seconds: float) -> None:
        self.idle_timeout_seconds = idle_timeout_seconds
        self.session: TerminalLiveSession | None = None
        self._lock = threading.Lock()

    def __call__(self, command: list[str], prompt: str, *, timeout_seconds: int) -> str:
        with self._lock:
            if self.session is None:
                self.session = TerminalLiveSession(command, idle_timeout_seconds=self.idle_timeout_seconds)
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
        }
    )
    env.update(command_env)
    return env


def _self_service_room_command_env(config: ResidentAgentConfig) -> dict[str, str]:
    base = [sys.executable, "-m", "agentsassemble.cli", "live-agent"]
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
        "AGENTSASSEMBLE_HEARTBEAT_COMMAND_TEMPLATE": shlex.join(
            [
                *base,
                "heartbeat",
                *identity,
                "--status",
                "{status}",
                "--last-error={last_error}",
                "--last-reply-at={last_reply_at}",
                "--last-observed-event-id={last_observed_event_id}",
                "--last-observed-live-event-id={last_observed_live_event_id}",
                "--json",
            ]
        ),
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
    if config.provider_kind == "codex_live_session" and config.connection_kind != "live_session":
        raise ValueError("codex_live_session resident requires live_session connection_kind.")
    if config.connection_kind == "remote_bridge":
        if not config.endpoint:
            raise ValueError("Remote bridge resident requires --endpoint.")
        if not config.auth_ref:
            raise ValueError("Remote bridge resident requires --auth-ref.")
        return
    if config.connection_kind in {"local_cli", "live_session", "terminal_session", "self_service", "codex_resume", "manual"} and not config.command:
        raise ValueError(f"{config.connection_kind} resident requires --command.")


def _command_runner_for_config(config: ResidentAgentConfig):
    if config.connection_kind == "self_service":
        raise ValueError("self_service residents are supervised directly and do not use prompt-injection command runners.")
    if config.provider_kind == "codex_live_session" and config.connection_kind == "live_session":
        return CodexResidentCommandRunner(config)
    if config.connection_kind == "live_session":
        return _JsonlLiveSessionCommandRunner()
    if config.connection_kind == "terminal_session":
        return _TerminalLiveSessionCommandRunner(idle_timeout_seconds=config.terminal_idle_timeout)
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
            message = _http_error_message(error)
        finally:
            error.close()
        raise ValueError(message) from error
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
            "preflight",
            "--config",
            output_path,
        ],
        "ensure_session": ensure_session,
    }


if __name__ == "__main__":
    raise SystemExit(main())
