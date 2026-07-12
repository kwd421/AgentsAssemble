"""Live-agent command parser registrations."""
from __future__ import annotations

import argparse

from agentsassemble.cli_parser_common import (
    LIVE_AGENT_CONNECTION_KIND_CHOICES,
    LIVE_AGENT_DELEGATE_CONNECTION_KIND_CHOICES,
    LIVE_AGENT_RESIDENT_CONNECTION_KIND_CHOICES,
    MAX_LIVE_AGENT_ROUND_BATCH,
    _add_session_readiness_wait_args,
    _add_session_auto_restart_args,
    _add_session_finalize_after_rounds_arg,
    _hide_subparser_from_help,
    parse_nonnegative_float,
    parse_nonnegative_int,
    parse_positive_int,
    parse_session_smoke_lobby_probe_count,
    parse_session_smoke_soak_cycle_count,
    parse_session_smoke_soak_interval_seconds,
)
from agentsassemble.live_agent_timing import DEFAULT_LIVE_AGENT_POLL_INTERVAL
from agentsassemble.models import ENGAGEMENT_MODE_CHOICES


def register_live_agent_parsers(subparsers: argparse._SubParsersAction) -> None:
    live_server = argparse.ArgumentParser(add_help=False)
    live_server.add_argument("--server", default="http://127.0.0.1:8765", help="AgentsAssemble GUI server URL.")

    live_agent = subparsers.add_parser("live-agent", help=argparse.SUPPRESS)
    _hide_subparser_from_help(subparsers, "live-agent")
    live_agent.add_argument("--legacy-internal", action="store_true", help=argparse.SUPPRESS)
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
    live_register.add_argument("--join-semantics", default="", help="Override execution structure for comparison runs.")
    live_register.add_argument("--persona-card-id", default="")
    live_register.add_argument("--character-mode", choices=["off", "on", "work_speech_only"], default="")
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
    live_join_brief.add_argument("--poll-interval", type=parse_nonnegative_float, default=DEFAULT_LIVE_AGENT_POLL_INTERVAL)
    live_join_brief.add_argument("--max-chain-depth", type=parse_nonnegative_int, default=1)
    live_join_brief.add_argument("--json", action="store_true", dest="as_json", help="Print a machine-readable join brief.")

    live_lan_invite = live_agent_subparsers.add_parser(
        "lan-invite",
        help="Create or verify LAN invite tokens for future native remote room clients.",
    )
    lan_invite_subparsers = live_lan_invite.add_subparsers(dest="lan_invite_command", required=True)
    lan_invite_create = lan_invite_subparsers.add_parser(
        "create",
        parents=[live_server],
        help="Create a signed LAN invite token without starting a provider.",
    )
    lan_invite_create.add_argument("--meeting-id", required=True)
    lan_invite_create.add_argument("--agent-id", required=True)
    lan_invite_create.add_argument("--display-name", default="")
    lan_invite_create.add_argument("--provider-kind", default="manual")
    lan_invite_create.add_argument("--secret-ref", default="env:AGENTSASSEMBLE_LAN_INVITE_SECRET")
    lan_invite_create.add_argument("--ttl-seconds", type=parse_positive_int, default=600)
    lan_invite_create.add_argument("--json", action="store_true", dest="as_json", help="Print the invite packet as JSON.")
    lan_invite_verify = lan_invite_subparsers.add_parser(
        "verify",
        help="Verify a signed LAN invite token locally without contacting the room.",
    )
    lan_invite_verify.add_argument("--token", required=True)
    lan_invite_verify.add_argument("--secret-ref", default="env:AGENTSASSEMBLE_LAN_INVITE_SECRET")
    lan_invite_verify.add_argument("--expected-meeting-id", default="")
    lan_invite_verify.add_argument("--expected-agent-id", default="")
    lan_invite_verify.add_argument("--json", action="store_true", dest="as_json", help="Print the verification report as JSON.")

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
    live_list.add_argument(
        "--require-host-approved",
        action="store_true",
        help="Exit 1 when any returned live agent is not host-approved for its meeting binding.",
    )

    live_heartbeat = live_agent_subparsers.add_parser("heartbeat", parents=[live_server], help="Update live agent presence.")
    live_heartbeat.add_argument("--agent-id", required=True)
    live_heartbeat.add_argument("--status", choices=["online", "working", "offline", "error"], default="online")
    live_heartbeat.add_argument("--last-error", default=None)
    live_heartbeat.add_argument("--last-attention", default=None)
    live_heartbeat.add_argument("--last-reply-at", default=None)
    live_heartbeat.add_argument("--last-observed-event-id", default=None)
    live_heartbeat.add_argument("--last-observed-live-event-id", default=None)
    live_heartbeat.add_argument("--last-observed-dm-event-id", default=None)
    live_heartbeat.add_argument("--json", action="store_true", dest="as_json", help="Print the raw heartbeat response.")

    live_leave = live_agent_subparsers.add_parser("leave", parents=[live_server], help="Mark an external live agent offline before exiting.")
    live_leave.add_argument("--agent-id", required=True)
    live_leave.add_argument("--last-observed-event-id", default=None)
    live_leave.add_argument("--last-observed-live-event-id", default=None)
    live_leave.add_argument("--last-observed-dm-event-id", default=None)
    live_leave.add_argument("--json", action="store_true", dest="as_json", help="Print the safe leave heartbeat response.")

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

    live_call_preset = live_agent_subparsers.add_parser(
        "call-preset",
        parents=[live_server],
        help="Run a Play Mode official turn preset without starting provider processes.",
    )
    live_call_preset.add_argument("--meeting-id", required=True)
    live_call_preset.add_argument("--preset", required=True, dest="preset_id")
    live_call_preset.add_argument("--role", action="append", default=[], dest="role_ids", help="Limit to a role id; repeat to set order.")
    live_call_preset.add_argument("--timeout", type=parse_nonnegative_float, default=30.0, help="Default seconds to wait per turn.")
    live_call_preset.add_argument("--stop-on-timeout", action="store_true", help="Skip remaining roles after the first timeout.")
    live_call_preset.add_argument("--json", action="store_true", dest="as_json", help="Print the raw preset result payload.")

    live_flow = live_agent_subparsers.add_parser(
        "flow",
        parents=[live_server],
        help="Run a time-boxed Play Mode event-driven conversation loop for approved resident agents.",
    )
    live_flow.add_argument("--meeting-id", required=True)
    live_flow.add_argument("--topic", required=True)
    live_flow.add_argument("--duration-seconds", type=parse_nonnegative_float, default=180.0)
    live_flow.add_argument("--tick-interval", type=parse_nonnegative_float, default=2.0)
    live_flow.add_argument("--cooldown", type=parse_nonnegative_float, default=8.0)
    live_flow.add_argument("--max-agent-turns", type=parse_nonnegative_int, default=0, help="Maximum speaking turns per agent; 0 means unlimited.")
    live_flow.add_argument("--max-total-turns", type=parse_nonnegative_int, default=0, help="Maximum total speaking turns; 0 means unlimited.")
    live_flow.add_argument("--max-silence-seconds", type=parse_nonnegative_float, default=20.0)
    live_flow.add_argument(
        "--resource-report",
        default="",
        help="Optional JSON path for CPU/RSS samples captured while the flow runs.",
    )
    live_flow.add_argument(
        "--resource-sample-interval",
        type=parse_nonnegative_float,
        default=5.0,
        help="Seconds between resource samples when --resource-report is set.",
    )
    live_flow.add_argument(
        "--runtime-mode",
        default="",
        help="Optional comparison label written into --resource-report.",
    )
    live_flow.add_argument("--json", action="store_true", dest="as_json", help="Print the raw flow result payload.")

    live_room_benchmark = live_agent_subparsers.add_parser(
        "room-benchmark",
        help="Measure local room event append/read latency without starting provider processes.",
    )
    live_room_benchmark.add_argument("--output-root", default="", help="Parent directory for a temporary benchmark run.")
    live_room_benchmark.add_argument("--events", type=parse_positive_int, default=500)
    live_room_benchmark.add_argument("--read-window", type=parse_positive_int, default=80)
    live_room_benchmark.add_argument("--warmup-events", type=parse_nonnegative_int, default=20)
    live_room_benchmark.add_argument("--agent-count", type=parse_positive_int, default=5)
    live_room_benchmark.add_argument("--sse-samples", type=parse_nonnegative_int, default=0)
    live_room_benchmark.add_argument("--keep-output", action="store_true", help="Keep the benchmark run directory for inspection.")
    live_room_benchmark.add_argument("--json", action="store_true", dest="as_json", help="Print machine-readable benchmark results.")

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
    live_finalize_meeting.add_argument(
        "--close-pending",
        action="store_true",
        help="Cancel pending official turn requests before finalizing.",
    )
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
    live_say.add_argument("--flow-id", default="", help="Flow id from a wait-next/read-since event when replying from a tool loop.")
    live_say.add_argument("--flow-meeting-id", default="", help="Flow meeting id from a wait-next/read-since event when replying from a tool loop.")
    live_say.add_argument("--json", action="store_true", dest="as_json", help="Print the raw lobby post response.")
    live_say.add_argument("message", nargs="+")

    live_dm_reply = live_agent_subparsers.add_parser("dm-reply", parents=[live_server], help="Post a private DM reply as a live agent.")
    live_dm_reply.add_argument("--agent-id", required=True)
    live_dm_reply.add_argument("--source-event-id", required=True)
    live_dm_reply.add_argument("--json", action="store_true", dest="as_json", help="Print the raw direct DM reply response.")
    live_dm_reply.add_argument("message", nargs="+")

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

    live_read_since = live_agent_subparsers.add_parser(
        "read-since",
        parents=[live_server],
        help="Read lobby and official room events after this agent's stored cursors.",
    )
    live_read_since.add_argument("--agent-id", required=True)
    live_read_since.add_argument("--after-event-id", default="")
    live_read_since.add_argument("--after-live-event-id", default="")
    live_read_since.add_argument("--after-dm-event-id", default="")
    live_read_since.add_argument("--json", action="store_true", dest="as_json", help="Print the raw room diff payload.")

    live_wait_room_event = live_agent_subparsers.add_parser(
        "wait-room-event",
        parents=[live_server],
        help="Wait for the next non-self lobby event visible to a live agent.",
    )
    live_wait_room_event.add_argument("--agent-id", required=True)
    live_wait_room_event.add_argument("--after-event-id", default="")
    live_wait_room_event.add_argument("--max-chain-depth", type=parse_nonnegative_int, default=1)
    live_wait_room_event.add_argument("--timeout", type=parse_nonnegative_float, default=30.0)
    live_wait_room_event.add_argument("--poll-interval", type=parse_nonnegative_float, default=DEFAULT_LIVE_AGENT_POLL_INTERVAL)
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
    live_wait_turn_request.add_argument("--poll-interval", type=parse_nonnegative_float, default=DEFAULT_LIVE_AGENT_POLL_INTERVAL)
    live_wait_turn_request.add_argument("--json", action="store_true", dest="as_json", help="Print the raw wait result.")

    live_wait_next = live_agent_subparsers.add_parser(
        "wait-next",
        parents=[live_server],
        help="Wait for the next actionable lobby event or official turn request visible to a live agent.",
    )
    live_wait_next.add_argument("--agent-id", required=True)
    live_wait_next.add_argument("--after-event-id", default="")
    live_wait_next.add_argument("--after-live-event-id", default="")
    live_wait_next.add_argument("--after-dm-event-id", default="")
    live_wait_next.add_argument("--max-chain-depth", type=parse_nonnegative_int, default=1)
    live_wait_next.add_argument("--timeout", type=parse_nonnegative_float, default=30.0)
    live_wait_next.add_argument("--poll-interval", type=parse_nonnegative_float, default=DEFAULT_LIVE_AGENT_POLL_INTERVAL)
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

    live_local_resources = live_agent_subparsers.add_parser(
        "local-resources",
        parents=[live_server],
        help="Read a sanitized local resource snapshot without controlling processes.",
    )
    live_local_resources.add_argument("--json", action="store_true", dest="as_json", help="Print the raw JSON resource payload.")
    live_local_resources.add_argument(
        "--fail-on-degraded",
        action="store_true",
        help="Exit 1 when local resource status is not ok.",
    )

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
    live_real_session_smoke.add_argument(
        "--official-round-smoke",
        action="store_true",
        help="Also request one moderator-called official round after the initial redacted probe.",
    )
    live_real_session_smoke.add_argument(
        "--restart-smoke",
        action="store_true",
        help="Also restart the resident session and run a second redacted probe before stopping.",
    )
    live_real_session_smoke.add_argument("--json", action="store_true", dest="as_json", help="Print a machine-readable real session smoke result.")

    live_continuity_proof = live_agent_subparsers.add_parser(
        "continuity-proof",
        help="Run an explicitly approved two-turn provider-owned resume continuity proof.",
    )
    live_continuity_proof.add_argument("--agent-id", default="continuity-proof")
    live_continuity_proof.add_argument("--display-name", default="")
    live_continuity_proof.add_argument("--provider-kind", required=True)
    live_continuity_proof.add_argument("--connection-kind", choices=LIVE_AGENT_RESIDENT_CONNECTION_KIND_CHOICES, default="live_session")
    live_continuity_proof.add_argument("--session-id", default="")
    live_continuity_proof.add_argument("--timeout", type=int, default=180)
    live_continuity_proof.add_argument(
        "--approve-real-providers",
        action="store_true",
        help="Allow this one proof command to call a real provider CLI.",
    )
    live_continuity_proof.add_argument("--json", action="store_true", dest="as_json")
    live_continuity_proof.add_argument("--command", dest="resident_command", nargs=argparse.REMAINDER, default=[])

    live_continuity_proof_group = live_agent_subparsers.add_parser(
        "continuity-proof-group",
        help="Run explicit continuity proofs for supported agents in a resident group config.",
    )
    live_continuity_proof_group.add_argument("--config", required=True, help="Path to a live-agent group config.")
    live_continuity_proof_group.add_argument("--server", default="", help="Optional server override for config loading.")
    live_continuity_proof_group.add_argument(
        "--approve-real-providers",
        action="store_true",
        help="Allow this one group proof to call real provider CLIs for supported agents.",
    )
    live_continuity_proof_group.add_argument("--json", action="store_true", dest="as_json")

    live_official_round_smoke = live_agent_subparsers.add_parser(
        "official-round-smoke",
        parents=[live_server],
        help="Run credential-free fake agents through a moderator-called official round.",
    )
    live_official_round_smoke.add_argument("--group-id", default="", help="Optional supervised process group id for the smoke run.")
    live_official_round_smoke.add_argument("--timeout", type=parse_nonnegative_float, default=12.0, help="Seconds to wait per fake official turn.")
    live_official_round_smoke.add_argument("--json", action="store_true", dest="as_json", help="Print a machine-readable smoke result.")

    live_persona_smoke = live_agent_subparsers.add_parser(
        "persona-smoke",
        help="Run a local fake-provider Character Mode smoke without starting real provider CLIs.",
    )
    live_persona_smoke.add_argument("--card", required=True, help="Persona card JSON path to smoke.")
    live_persona_smoke.add_argument("--output-root", default=".agentsassemble", help="Output root for the smoke meeting.")
    live_persona_smoke.add_argument("--meeting-id", default="", help="Optional smoke meeting id.")
    live_persona_smoke.add_argument("--character-mode", choices=["on", "work_speech_only"], default="on")
    live_persona_smoke.add_argument("--context", default="", help="Room context text used for persona lore activation.")
    live_persona_smoke.add_argument("--json", action="store_true", dest="as_json", help="Print a machine-readable smoke result.")

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
    live_run.add_argument(
        "--model",
        dest="model_id",
        default="",
        help="Model id (api_call lane: a catalog model within --provider-kind).",
    )
    live_run.add_argument(
        "--key-source",
        default="",
        choices=["", "byok", "free", "subscription", "local"],
        help="api_call lane: override cost_owner by where the key came from (default: catalog).",
    )
    live_run.add_argument(
        "--output-root",
        default="",
        help="api_call lane: identity-store root for best-effort usage accounting (local-first: match the server's).",
    )
    live_run.add_argument("--engagement-mode", default="always")
    live_run.add_argument(
        "--transport",
        choices=["http", "ws"],
        default="http",
        help="http (poll runner, default) or ws (governed WebSocket resident; reuses the provider brain + runner prompt).",
    )
    live_run.add_argument("--session-token", default="", help="ws transport: a room session token for this agent.")
    live_run.add_argument("--invite-token", default="", help="ws transport: an invite token to join for a session (alternative to --session-token).")
    live_run.add_argument("--join-semantics", default="", help="Override execution structure for comparison runs.")
    live_run.add_argument(
        "--codex-sandbox",
        choices=["read-only", "workspace-write"],
        default="read-only",
        help="(legacy) codex sandbox; superseded by --permission-option.",
    )
    live_run.add_argument(
        "--permission-option",
        default="",
        help="Provider's own permission/sandbox value passed through as-is (codex --sandbox, claude/grok --permission-mode, agy --sandbox/--dangerously-skip-permissions).",
    )
    live_run.add_argument(
        "--reply-char-limit",
        type=parse_nonnegative_int,
        default=0,
        help="Approx character cap for room messages (0 = no limit, default: narrate freely). Suggested menu: 100/250/400/700/1000.",
    )
    live_run.add_argument(
        "--stream-thinking",
        action="store_true",
        help="Stream the agent's reasoning/progress to the operator as it works (codex --json today). Default off.",
    )
    live_run.add_argument(
        "--fast-mode",
        action="store_true",
        help="Per-agent fast toggle (codex --enable fast_mode, claude /fast). A deliberate user setting, not auto. Default off.",
    )
    live_run.add_argument("--timeout", type=int, default=120)
    live_run.add_argument(
        "--official-turn-timeout",
        type=parse_nonnegative_int,
        default=0,
        help="Optional provider command timeout for official turns only; 0 reuses --timeout.",
    )
    live_run.add_argument("--poll-interval", type=parse_nonnegative_float, default=DEFAULT_LIVE_AGENT_POLL_INTERVAL)
    live_run.add_argument("--heartbeat-interval", type=parse_nonnegative_float, default=30.0)
    live_run.add_argument("--cooldown", type=parse_nonnegative_float, default=5.0)
    live_run.add_argument("--max-chain-depth", type=parse_nonnegative_int, default=1)
    live_run.add_argument("--max-ticks", type=parse_nonnegative_int, default=0)
    live_run.add_argument("--persona-id", default="", help="Optional imported persona card id for Play Mode prompts.")
    live_run.add_argument("--persona-card-id", default="", help="Alias for --persona-id.")
    live_run.add_argument("--persona-path", default="", help="Optional persona card JSON path for Play Mode prompts.")
    live_run.add_argument("--character-mode", choices=["off", "on", "work_speech_only"], default="")
    live_run.add_argument("--first-message-index", type=int, default=0)
    live_run.add_argument("--terminal-idle-timeout", type=parse_nonnegative_float, default=0.35)
    live_run.add_argument("--command", dest="resident_command", nargs=argparse.REMAINDER, default=[])

    live_group = live_agent_subparsers.add_parser("run-group", help="Run multiple resident local CLI live agents.")
    live_group.add_argument("--config", required=True)
    live_group.add_argument("--server", default=None)
    live_group.add_argument("--max-ticks", type=parse_nonnegative_int, default=None)
    live_group.add_argument(
        "--launch-stagger-seconds",
        type=parse_nonnegative_float,
        default=2.0,
        help="Delay between starting each resident so concurrent boots/connects don't pile up (0 = all at once).",
    )
    live_group.add_argument("--agent-manifest", default="", help=argparse.SUPPRESS)

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
