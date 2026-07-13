from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass

from agentsassemble.live_agent_smoke import LiveAgentSmokeFailed
from agentsassemble.meeting_events import clean_lobby_text


SMOKE_COMMANDS = {"smoke", "session-smoke", "real-session-smoke", "official-round-smoke"}


@dataclass(frozen=True)
class LegacySmokeCliRuntime:
    request_json: Callable[..., dict[str, object]]
    server_url: Callable[[str, str], str]
    operation_http_timeout: Callable[..., float]
    session_smoke_http_timeout: Callable[..., float]
    real_session_smoke_http_timeout: Callable[[float], float]
    format_session_smoke: Callable[[dict[str, object]], str]
    format_real_session_smoke: Callable[[dict[str, object]], str]


def run_legacy_smoke_command(args: argparse.Namespace, *, runtime: LegacySmokeCliRuntime) -> int | None:
    command = str(getattr(args, "live_agent_command", ""))
    if command not in SMOKE_COMMANDS:
        return None
    if command == "smoke":
        return _smoke(args, runtime)
    if command == "session-smoke":
        return _session_smoke(args, runtime)
    if command == "real-session-smoke":
        return _real_session_smoke(args, runtime)
    return _official_round_smoke(args, runtime)


def _smoke(args: argparse.Namespace, runtime: LegacySmokeCliRuntime) -> int:
    try:
        result = runtime.request_json(
            runtime.server_url(args.server, "/api/live-agent-smoke"),
            method="POST",
            payload={"group_id": args.group_id, "timeout": float(args.timeout)},
            timeout_seconds=runtime.operation_http_timeout(float(args.timeout)),
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


def _session_smoke(args: argparse.Namespace, runtime: LegacySmokeCliRuntime) -> int:
    result = runtime.request_json(
        runtime.server_url(args.server, "/api/live-agent-session-smoke"),
        method="POST",
        payload={
            "group_id": str(args.group_id or ""),
            "meeting_id": str(args.meeting_id or ""),
            "timeout": float(args.timeout),
            "lobby_probe_count": int(args.lobby_probe_count),
            "soak_cycle_count": int(args.soak_cycle_count),
            "soak_interval_seconds": float(args.soak_interval_seconds),
        },
        timeout_seconds=runtime.session_smoke_http_timeout(
            float(args.timeout),
            lobby_probe_count=int(args.lobby_probe_count),
            soak_cycle_count=int(args.soak_cycle_count),
            soak_interval_seconds=float(args.soak_interval_seconds),
        ),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2) if args.as_json else runtime.format_session_smoke(result))
    return 0 if result.get("status") == "ok" else 1


def _real_session_smoke(args: argparse.Namespace, runtime: LegacySmokeCliRuntime) -> int:
    if not bool(args.approve_real_providers):
        result = _approval_required_result(args)
    else:
        payload: dict[str, object] = {
            "group_id": str(args.group_id or ""),
            "meeting_id": str(args.meeting_id or ""),
            "timeout": float(args.timeout),
            "live_agent_config_path": str(args.live_agent_config or ""),
            "council_config_path": str(args.council_config or ""),
            "agent_config_path": str(args.agent_config or ""),
            "approve_real_providers": True,
        }
        if bool(args.official_round_smoke):
            payload["official_round_smoke"] = True
        if bool(args.restart_smoke):
            payload["restart_smoke"] = True
        result = runtime.request_json(
            runtime.server_url(args.server, "/api/live-agent-real-session-smoke"),
            method="POST",
            payload=payload,
            timeout_seconds=runtime.real_session_smoke_http_timeout(float(args.timeout)),
        )
    print(json.dumps(result, ensure_ascii=False, indent=2) if args.as_json else runtime.format_real_session_smoke(result))
    return 0 if result.get("status") == "ok" else 1


def _approval_required_result(args: argparse.Namespace) -> dict[str, object]:
    return {
        "status": "approval_required",
        "meeting_id": _safe_id(args.meeting_id),
        "group_id": _safe_id(args.group_id),
        "approval_required": True,
        "approved": False,
        "diagnostic": True,
        "reason": "current_operator_approval_required",
    }


def _safe_id(value: object) -> str:
    text = clean_lobby_text(value, limit=128)
    return "".join(char if char.isalnum() or char in "_.-" else "-" for char in text).strip(".-")


def _official_round_smoke(args: argparse.Namespace, runtime: LegacySmokeCliRuntime) -> int:
    result = runtime.request_json(
        runtime.server_url(args.server, "/api/live-agent-official-round-smoke"),
        method="POST",
        payload={"group_id": args.group_id, "timeout": float(args.timeout)},
        timeout_seconds=runtime.operation_http_timeout(float(args.timeout), windows=4),
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
