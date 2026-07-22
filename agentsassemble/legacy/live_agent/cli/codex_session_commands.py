"""Retained Codex session discovery and config commands."""
from __future__ import annotations

import argparse
import json
import shlex
import sys
import urllib.error
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from agentsassemble.config import load_council_config
from agentsassemble.legacy.live_agent.codex_sessions import (
    build_codex_live_agent_config,
    build_codex_live_invite_config,
    list_codex_sessions,
    read_agent_config,
    write_agent_config,
)
from agentsassemble.legacy.live_agent.runtime.processes import clean_live_agent_group_id


@dataclass(frozen=True)
class CodexSessionCliRuntime:
    request_json: Callable[..., dict[str, object]]
    server_url: Callable[[str, str], str]


def run_codex_session_command(
    args: argparse.Namespace,
    *,
    runtime: CodexSessionCliRuntime,
) -> int:
    if not bool(getattr(args, "legacy_internal", False)):
        print(
            "sessions is a legacy/internal Codex discovery path; use assemble room resume for Agent Sessions.",
            file=sys.stderr,
        )
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
        return _run_invite(args, runtime=runtime)
    if args.sessions_command == "live-agent-config":
        return _run_live_agent_config(args)
    return 1


def codex_live_agent_config_next_commands(
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


def _run_invite(args: argparse.Namespace, *, runtime: CodexSessionCliRuntime) -> int:
    try:
        if args.server:
            response = runtime.request_json(
                runtime.server_url(args.server, "/api/codex-sessions/invite"),
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
                print(
                    f"Invited {binding.get('role_id') or args.role_id} "
                    f"as {binding.get('agent_id') or 'Codex live session'}"
                )
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


def _run_live_agent_config(args: argparse.Namespace) -> int:
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
    next_commands = codex_live_agent_config_next_commands(
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
