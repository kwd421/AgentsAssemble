"""General command parser registrations."""
from __future__ import annotations

import argparse

from agentsassemble.application.cli.common import (
    LIVE_AGENT_CONNECTION_KIND_CHOICES,
    _hide_subparser_from_help,
    parse_codex_timeout,
    parse_nonnegative_float,
    parse_nonnegative_int,
    parse_positive_int,
)
from agentsassemble.release_health import DEFAULT_RELEASE_HEALTH_TIMEOUT_SECONDS
from agentsassemble.application.room_repository_factory import (
    DEFAULT_POSTGRES_DSN_ENV,
    ROOM_REPOSITORY_BACKENDS,
)


def register_core_parsers(subparsers: argparse._SubParsersAction) -> None:
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
        choices=["debate"],
        default=None,
        help="Run the moderated turn-based council demo.",
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
    gui.add_argument(
        "--room-repository-backend",
        choices=sorted(ROOM_REPOSITORY_BACKENDS),
        default="sqlite",
        help="Canonical room storage backend. PostgreSQL must be migrated explicitly before startup.",
    )
    gui.add_argument(
        "--room-postgres-dsn-env",
        default=DEFAULT_POSTGRES_DSN_ENV,
        help="Environment variable containing the PostgreSQL DSN; the DSN is never accepted on argv.",
    )
    gui.add_argument(
        "--attention-shadow-mode",
        choices=("off", "sample", "full"),
        default="off",
        help="Persist no, deterministic 1/16 sampled, or all non-ambient attention diagnostics.",
    )
    gui.add_argument("--public-url", default="", help="Public HTTP(S) base URL used for external /join?token= invite links.")
    gui.add_argument("--host-token", default="", help="Runtime host token for public invite management endpoints.")
    gui.add_argument(
        "--unsafe-expose-control-plane",
        action="store_true",
        help="Allow a direct non-loopback bind that exposes the unauthenticated local control plane.",
    )
    gui.add_argument(
        "--start-public-tunnel",
        "--public-tunnel",
        action="store_true",
        dest="start_public_tunnel",
        help="Start a Cloudflare quick tunnel when cloudflared is installed.",
    )
    gui.add_argument("--live-agent-config", default="", help="Explicit resident group config to autostart after the GUI binds.")
    gui.add_argument("--live-agent-group-id", default="", help="Optional group id for GUI startup autostart.")
    gui.add_argument("--live-agent-auto-restart", action="store_true", help="Enable auto restart for the startup autostart group.")
    gui.add_argument("--live-agent-max-restarts", type=parse_nonnegative_int, default=0)
    gui.add_argument("--live-agent-restart-backoff-seconds", type=parse_nonnegative_float, default=5.0)
    gui.add_argument("--live-agent-stale-restart-after-seconds", type=parse_nonnegative_float, default=0.0)

    frontend_info = subparsers.add_parser(
        "frontend-info",
        help="Print read-only launch guidance for the opt-in React/Vite frontend.",
    )
    frontend_info.add_argument("--backend", default="http://127.0.0.1:8765", help="Backend GUI URL used by the Vite proxy.")
    frontend_info.add_argument("--port", type=parse_positive_int, default=5173, help="React/Vite dev server port.")
    frontend_info.add_argument("--json", action="store_true", dest="as_json", help="Print machine-readable launch guidance.")

    lobby = subparsers.add_parser("lobby", help="Work with lobby records without starting providers.")
    lobby_subparsers = lobby.add_subparsers(dest="lobby_command", required=True)
    lobby_promote = lobby_subparsers.add_parser(
        "promote",
        help="Promote explicit lobby event text into official meeting context.",
    )
    lobby_promote.add_argument("--output-root", default=".agentsassemble")
    lobby_promote.add_argument("--meeting-id", required=True)
    lobby_promote.add_argument(
        "--lobby-event-id",
        action="append",
        default=[],
        dest="lobby_event_ids",
        required=True,
        help="Lobby event id to promote; repeat for multiple events.",
    )
    lobby_promote.add_argument("--reason", default="", help="Optional short operator reason.")
    lobby_promote.add_argument("--json", action="store_true", dest="as_json")

    release_health = subparsers.add_parser(
        "release-health",
        help="List or run the local v0.1 release-health verification queue.",
    )
    release_health.add_argument("--json", "--as-json", action="store_true", dest="as_json", help="Print JSON output.")
    release_health_subparsers = release_health.add_subparsers(dest="release_health_command")
    release_health_list = release_health_subparsers.add_parser("list", help="List release-health checks.")
    release_health_list.add_argument(
        "--json",
        "--as-json",
        action="store_true",
        dest="as_json",
        default=argparse.SUPPRESS,
        help="Print JSON output.",
    )
    release_health_run = release_health_subparsers.add_parser("run", help="Run selected release-health checks locally.")
    release_health_run.add_argument("--check", action="append", default=[], help="Run only this check id; repeat for multiple checks.")
    release_health_run.add_argument("--skip", action="append", default=[], help="Skip this check id; repeat for multiple checks.")
    release_health_run.add_argument(
        "--timeout",
        type=parse_nonnegative_float,
        default=DEFAULT_RELEASE_HEALTH_TIMEOUT_SECONDS,
        help="Per-check timeout in seconds.",
    )
    release_health_run.add_argument(
        "--json",
        "--as-json",
        action="store_true",
        dest="as_json",
        default=argparse.SUPPRESS,
        help="Print JSON output.",
    )
    release_health_run.add_argument(
        "--save-report",
        action="store_true",
        help="Write the latest local report for read-only GUI status projection.",
    )
    release_health_run.add_argument(
        "--output-root",
        default=".agentsassemble",
        help="Output root used with --save-report.",
    )

    bridge = subparsers.add_parser("claude-bridge", help=argparse.SUPPRESS)
    _hide_subparser_from_help(subparsers, "claude-bridge")
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

    api_call = subparsers.add_parser(
        "api-call",
        help="One-shot OpenAI-compatible model call (the API-provider lane). Prompt on stdin, reply on stdout.",
    )
    api_call.add_argument("--provider", required=True, help="Catalog provider id (e.g. nvidia, openrouter, lmstudio).")
    api_call.add_argument("--model", required=True, help="Model id within the provider.")
    api_call.add_argument("--system", default="", help="Optional system prompt.")
    api_call.add_argument("--output-root", default=".agentsassemble", help="Output root for the identity store (usage accounting).")
    api_call.add_argument("--meeting-id", default="", help="Meeting/room id for usage attribution.")
    api_call.add_argument("--participant-id", default="", help="Participant id for usage attribution.")
    api_call.add_argument("--user-id", default="", help="User id for usage attribution.")
    api_call.add_argument(
        "--key-source",
        default="",
        choices=["", "byok", "free", "subscription", "local"],
        help="Override cost_owner by where the key came from (default: catalog).",
    )
    api_call.add_argument("--timeout", type=int, default=60, help="Seconds to wait for the model.")
    api_call.add_argument("--catalog", action="store_true", help="Print the safe model catalog (JSON) and exit.")

    memory_capsule = subparsers.add_parser("memory-capsule", help="Inspect importable memory/profile capsules.")
    memory_capsule_subparsers = memory_capsule.add_subparsers(dest="memory_capsule_command", required=True)
    memory_capsule_gate = memory_capsule_subparsers.add_parser(
        "gate",
        help="Validate a memory/profile capsule before it can influence a meeting.",
    )
    memory_capsule_gate.add_argument("--path", required=True, help="Memory capsule directory path.")
    memory_capsule_gate.add_argument("--json", action="store_true", dest="as_json", help="Print a machine-readable gate report.")

    mcp = subparsers.add_parser("mcp", help=argparse.SUPPRESS)
    _hide_subparser_from_help(subparsers, "mcp")
    mcp.add_argument("--legacy-internal", action="store_true", help=argparse.SUPPRESS)
    mcp_subparsers = mcp.add_subparsers(dest="mcp_command", required=True)
    mcp_serve = mcp_subparsers.add_parser(
        "serve",
        help="Serve participant or archive MCP tools over stdio.",
    )
    mcp_serve.add_argument("--profile", choices=["participant", "archive"], required=True)
    mcp_serve.add_argument("--server", default="http://127.0.0.1:8765", help="AgentsAssemble GUI server URL.")
    mcp_serve.add_argument("--agent-id", default="", help="Participant profile agent id.")
    mcp_serve.add_argument("--meeting-id", default="", help="Default meeting id for participant/archive tools.")
    mcp_serve.add_argument("--display-name", default="", help="Participant profile display name.")
    mcp_serve.add_argument("--provider-kind", default="manual", help="Participant profile provider kind.")
    mcp_serve.add_argument(
        "--connection-kind",
        choices=LIVE_AGENT_CONNECTION_KIND_CHOICES,
        default="manual",
        help="Participant profile connection kind.",
    )
    mcp_serve.add_argument("--engagement-mode", default="mentioned", help="Participant profile engagement mode.")
