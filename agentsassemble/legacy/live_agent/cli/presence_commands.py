"""Retained live-agent presence, roster, and LAN invite CLI commands."""
from __future__ import annotations

import argparse
import json
import math
import shlex
import urllib.error
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass

from agentsassemble.admission.lan_invite import (
    create_lan_invite_packet,
    resolve_lan_invite_secret_ref,
    verify_lan_invite_token,
)
from agentsassemble.legacy.live_agent.runtime.join_brief import build_live_agent_join_brief
from agentsassemble.legacy.live_agent.runtime.roster import (
    safe_live_agent_roster_agent,
    safe_live_agent_roster_payload,
    safe_live_agent_roster_text,
)
from agentsassemble.legacy.live_agent.state import _looks_sensitive_presence_error
from agentsassemble.legacy.meeting.core.events import clean_lobby_text


PRESENCE_COMMANDS = {
    "join-brief",
    "lan-invite",
    "list",
    "leave",
    "engagement",
    "return-packet",
}


@dataclass(frozen=True)
class LegacyPresenceCliRuntime:
    request_json: Callable[..., dict[str, object]]
    server_url: Callable[[str, str], str]


def run_legacy_presence_command(
    args: argparse.Namespace,
    *,
    runtime: LegacyPresenceCliRuntime,
) -> int | None:
    command = str(getattr(args, "live_agent_command", ""))
    if command not in PRESENCE_COMMANDS:
        return None
    handlers = {
        "join-brief": _run_join_brief,
        "lan-invite": _run_lan_invite,
        "list": _run_list,
        "leave": _run_leave,
        "engagement": _run_engagement,
        "return-packet": _run_return_packet,
    }
    return handlers[command](args, runtime)


def _run_leave(args: argparse.Namespace, runtime: LegacyPresenceCliRuntime) -> int:
    agent_id = urllib.parse.quote(args.agent_id, safe="")
    response = runtime.request_json(
        runtime.server_url(args.server, f"/api/live-agents/{agent_id}/leave"),
        method="POST",
        payload=leave_payload(args),
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


def leave_payload(args: argparse.Namespace) -> dict[str, object]:
    payload: dict[str, object] = {"status": "offline", "last_error": ""}
    for key, arg_name in (
        ("last_observed_event_id", "last_observed_event_id"),
        ("last_observed_live_event_id", "last_observed_live_event_id"),
        ("last_observed_dm_event_id", "last_observed_dm_event_id"),
    ):
        value = getattr(args, arg_name, None)
        if value is not None:
            payload[key] = value
    return payload


def _run_join_brief(args: argparse.Namespace, _runtime: LegacyPresenceCliRuntime) -> int:
    payload = build_live_agent_join_brief(
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
    if args.as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        _print_join_brief(payload)
    return 0


def _print_join_brief(payload: dict[str, object]) -> None:
    agent = payload.get("agent") if isinstance(payload.get("agent"), dict) else {}
    commands = payload.get("commands") if isinstance(payload.get("commands"), dict) else {}
    templates = payload.get("templates") if isinstance(payload.get("templates"), dict) else {}
    print(f"Live-agent join brief for {str(agent.get('agent_id') or 'agent')}")
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
    if isinstance(value, list):
        print(f"{label}:")
        print(f"  {shlex.join([str(item) for item in value])}")


def _run_lan_invite(args: argparse.Namespace, _runtime: LegacyPresenceCliRuntime) -> int:
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


def _run_list(args: argparse.Namespace, runtime: LegacyPresenceCliRuntime) -> int:
    try:
        payload = runtime.request_json(runtime.server_url(args.server, _list_path(args)))
    except (OSError, urllib.error.URLError, ValueError, json.JSONDecodeError) as error:
        raise ValueError(_list_fetch_error(error)) from error
    safe_payload = safe_live_agent_roster_payload(payload)
    _print_list_payload(safe_payload, as_json=args.as_json)
    agents = safe_payload.get("agents") if isinstance(safe_payload.get("agents"), list) else []
    if args.require_match and not any(isinstance(item, dict) for item in agents):
        return 1
    if args.require_all_agents and _missing_required_agents(safe_payload, args.agent_ids):
        return 1
    if args.fail_on_attention and any(
        isinstance(item, dict) and _agent_needs_attention(item) for item in agents
    ):
        return 1
    if args.require_host_approved and any(
        isinstance(item, dict) and item.get("host_approved_binding") is not True for item in agents
    ):
        return 1
    return 0


def _list_path(args: argparse.Namespace) -> str:
    query: list[tuple[str, str]] = [("safe", "1")]
    meeting_id = str(getattr(args, "meeting_id", "") or "").strip()
    if meeting_id:
        query.append(("meeting_id", meeting_id))
    for agent_id in getattr(args, "agent_ids", []) or []:
        if clean_agent_id := str(agent_id or "").strip():
            query.append(("agent_id", clean_agent_id))
    for status in getattr(args, "statuses", []) or []:
        if clean_status := str(status or "").strip():
            query.append(("status", clean_status))
    return f"/api/live-agents?{urllib.parse.urlencode(query)}"


def _list_fetch_error(error: Exception) -> str:
    message = clean_lobby_text(error, limit=500)
    if message and not _looks_sensitive_presence_error(message):
        return f"Live-agent roster fetch failed: {message}"
    return "Live-agent roster fetch failed: details redacted."


def _print_list_payload(payload: dict[str, object], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    agents = payload.get("agents") if isinstance(payload.get("agents"), list) else []
    if not agents:
        print("no live agents")
        return
    for item in agents:
        if isinstance(item, dict):
            print(_format_roster_agent(item))


def _format_roster_agent(agent: dict[str, object]) -> str:
    safe_text = safe_live_agent_roster_text
    parts = [
        safe_text(agent.get("agent_id"), limit=64, default="-"),
        safe_text(agent.get("display_name"), limit=128, default="-"),
        f"{safe_text(agent.get('provider_kind'), limit=64, default='unknown')}/"
        f"{safe_text(agent.get('connection_kind'), limit=64, default='unknown')}",
        safe_text(agent.get("status"), limit=64, default="unknown"),
    ]
    suffix: list[str] = []
    for label, field in (
        ("meeting", "meeting_id"),
        ("join", "join_semantics"),
        ("context", "context_durability"),
        ("sandbox", "sandbox_enforcement"),
        ("admission", "admission_status"),
    ):
        if text := safe_text(agent.get(field), limit=128):
            suffix.append(f"{label}={text}")
    approved = agent.get("host_approved_binding")
    if isinstance(approved, bool):
        suffix.append(f"host_approved={'yes' if approved else 'no'}")
    for label, field in (
        ("admission_source", "admission_evidence_source"),
        ("binding_role", "binding_role_id"),
        ("binding_provider", "binding_provider_id"),
        ("binding_kind", "binding_provider_kind"),
        ("binding_profile", "binding_permission_profile_id"),
        ("binding_join", "binding_join_mode"),
    ):
        if text := safe_text(agent.get(field), limit=128):
            suffix.append(f"{label}={text}")
    conflicts = agent.get("binding_conflicts")
    if isinstance(conflicts, list):
        text = ",".join(
            item for item in (safe_text(value, limit=64) for value in conflicts) if item
        )
        if text:
            suffix.append(f"binding_conflicts={text}")
    if engagement := safe_text(agent.get("engagement_mode"), limit=128):
        suffix.append(f"engagement={engagement}")
    for label, field in (
        ("heartbeat_age", "heartbeat_age_seconds"),
        ("stale_after", "stale_after_seconds"),
    ):
        value = agent.get(field)
        if value not in (None, ""):
            suffix.append(f"{label}={_format_seconds(_safe_nonnegative_float(value))}")
    for label, field in (
        ("cursor", "last_observed_event_id"),
        ("official_cursor", "last_observed_live_event_id"),
    ):
        if text := safe_text(agent.get(field), limit=128):
            suffix.append(f"{label}={text}")
    return " ".join(parts + suffix)


def _missing_required_agents(payload: dict[str, object], agent_ids: list[str]) -> bool:
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


def _agent_needs_attention(agent: dict[str, object]) -> bool:
    return str(agent.get("status") or "").strip().casefold() not in {"online", "working"}


def _run_engagement(args: argparse.Namespace, runtime: LegacyPresenceCliRuntime) -> int:
    agent_id = urllib.parse.quote(args.agent_id, safe="")
    response = runtime.request_json(
        runtime.server_url(args.server, f"/api/live-agents/{agent_id}/engagement"),
        method="POST",
        payload={"engagement_mode": args.engagement_mode},
    )
    if args.as_json:
        print(json.dumps(response, ensure_ascii=False, indent=2))
    else:
        agent = response.get("agent", {}) if isinstance(response.get("agent"), dict) else {}
        print(f"{agent.get('agent_id') or args.agent_id}: {agent.get('engagement_mode') or args.engagement_mode}")
    return 0


def _run_return_packet(args: argparse.Namespace, runtime: LegacyPresenceCliRuntime) -> int:
    agent_id = urllib.parse.quote(args.agent_id, safe="")
    query_values = {"source_event_id": args.source_event_id}
    if args.meeting_id:
        query_values["meeting_id"] = args.meeting_id
    query = urllib.parse.urlencode(query_values)
    response = runtime.request_json(
        runtime.server_url(args.server, f"/api/live-agents/{agent_id}/return-packet?{query}")
    )
    if args.as_json:
        print(json.dumps(response, ensure_ascii=False, indent=2))
    else:
        print(str(response.get("markdown") or "").strip())
    return 0


def _safe_nonnegative_float(value: object) -> float:
    if isinstance(value, bool):
        return 0.0
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) and number >= 0 else 0.0


def _format_seconds(value: float) -> str:
    return f"{int(value)}s" if value.is_integer() else f"{value:g}s"
