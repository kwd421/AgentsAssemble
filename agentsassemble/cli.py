from __future__ import annotations

import argparse
import json
import sys
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
from agentsassemble.meeting import run_demo_meeting


def parse_codex_timeout(value: str) -> int | None:
    if value.casefold() in {"none", "off", "unlimited", "0"}:
        return None
    timeout = int(value)
    if timeout < 0:
        raise argparse.ArgumentTypeError("timeout must be positive, 0, or none")
    return timeout


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

    live_server = argparse.ArgumentParser(add_help=False)
    live_server.add_argument("--server", default="http://127.0.0.1:8765", help="AgentsAssemble GUI server URL.")

    live_agent = subparsers.add_parser("live-agent", help="Connect an external live agent to a GUI room.")
    live_agent_subparsers = live_agent.add_subparsers(dest="live_agent_command", required=True)

    live_register = live_agent_subparsers.add_parser("register", parents=[live_server], help="Register a live agent.")
    live_register.add_argument("--agent-id", required=True)
    live_register.add_argument("--display-name", default="")
    live_register.add_argument("--provider-kind", default="manual")
    live_register.add_argument("--connection-kind", choices=["codex_resume", "local_cli", "remote_bridge", "manual"], default="manual")
    live_register.add_argument("--session-id", default="")
    live_register.add_argument("--endpoint", default="")
    live_register.add_argument("--meeting-id", default="")
    live_register.add_argument("--engagement-mode", default="mentioned")

    live_heartbeat = live_agent_subparsers.add_parser("heartbeat", parents=[live_server], help="Update live agent presence.")
    live_heartbeat.add_argument("--agent-id", required=True)
    live_heartbeat.add_argument("--status", choices=["online", "working", "offline"], default="online")

    live_say = live_agent_subparsers.add_parser("say", parents=[live_server], help="Post a lobby message as a live agent.")
    live_say.add_argument("--agent-id", required=True)
    live_say.add_argument("message", nargs="+")

    live_room = live_agent_subparsers.add_parser("room", parents=[live_server], help="Read the live room snapshot for an agent.")
    live_room.add_argument("--agent-id", required=True)

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
    if args.command == "sessions":
        return run_sessions_command(args)

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
                payload={"status": args.status},
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
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 1


def _server_url(server: str, path: str) -> str:
    return f"{server.rstrip('/')}{path}"


def _request_json(url: str, *, method: str = "GET", payload: dict[str, object] | None = None) -> dict[str, object]:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"} if payload is not None else {}
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=10) as response:
        loaded = json.loads(response.read().decode("utf-8"))
    return loaded if isinstance(loaded, dict) else {}


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
