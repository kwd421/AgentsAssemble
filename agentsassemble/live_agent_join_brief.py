from __future__ import annotations

import math

from agentsassemble.live_agent_context import live_agent_context_contract
from agentsassemble.meeting_events import clean_lobby_text


def build_live_agent_join_brief(
    *,
    server: object = "",
    agent_id: object,
    display_name: object = "",
    provider_kind: object = "manual",
    connection_kind: object = "manual",
    meeting_id: object = "",
    engagement_mode: object = "mentioned",
    timeout: object = 30.0,
    poll_interval: object = 2.0,
    max_chain_depth: object = 1,
) -> dict[str, object]:
    normalized_server = _clean_scalar_text(server, limit=256, field_name="server") or "http://127.0.0.1:8765"
    normalized_agent_id = _clean_scalar_text(agent_id, limit=64, field_name="agent_id")
    if not normalized_agent_id:
        raise ValueError("agent_id is required.")
    normalized_display_name = _clean_scalar_text(display_name, limit=128, field_name="display_name") or normalized_agent_id
    normalized_provider_kind = _clean_scalar_text(provider_kind, limit=64, field_name="provider_kind") or "manual"
    normalized_connection_kind = _clean_scalar_text(connection_kind, limit=64, field_name="connection_kind") or "manual"
    normalized_meeting_id = _clean_scalar_text(meeting_id, limit=128, field_name="meeting_id")
    normalized_engagement_mode = _clean_scalar_text(engagement_mode, limit=64, field_name="engagement_mode") or "mentioned"
    normalized_timeout = _cli_number(timeout)
    normalized_poll_interval = _cli_number(poll_interval)
    normalized_max_chain_depth = str(_safe_nonnegative_int(max_chain_depth))
    agent = {
        "agent_id": normalized_agent_id,
        "display_name": normalized_display_name,
        "provider_kind": normalized_provider_kind,
        "connection_kind": normalized_connection_kind,
        "meeting_id": normalized_meeting_id,
        "engagement_mode": normalized_engagement_mode,
    }
    commands = {
        "register": _join_register_command(server=normalized_server, agent=agent),
        "wait_next": _join_wait_next_command(
            server=normalized_server,
            agent_id=normalized_agent_id,
            timeout=normalized_timeout,
            poll_interval=normalized_poll_interval,
            max_chain_depth=normalized_max_chain_depth,
        ),
        "room": _join_room_command(server=normalized_server, agent_id=normalized_agent_id),
        "roster_gate": _join_roster_gate_command(
            server=normalized_server,
            agent_id=normalized_agent_id,
            meeting_id=normalized_meeting_id,
        ),
        "leave": _join_leave_command(server=normalized_server, agent_id=normalized_agent_id),
    }
    templates = {
        "say": _join_say_template(server=normalized_server, agent_id=normalized_agent_id),
        "official_reply": _join_official_reply_template(server=normalized_server, agent_id=normalized_agent_id),
        "heartbeat": _join_heartbeat_template(server=normalized_server, agent_id=normalized_agent_id),
    }
    execution_contract = _execution_contract(
        provider_kind=normalized_provider_kind,
        connection_kind=normalized_connection_kind,
    )
    return {
        "status": "generated",
        "agent": agent,
        "execution_contract": execution_contract,
        "commands": commands,
        "templates": templates,
        "env": {
            "AGENTSASSEMBLE_SERVER": normalized_server,
            "AGENTSASSEMBLE_AGENT_ID": normalized_agent_id,
            "AGENTSASSEMBLE_DISPLAY_NAME": normalized_display_name,
            "AGENTSASSEMBLE_PROVIDER_KIND": normalized_provider_kind,
            "AGENTSASSEMBLE_CONNECTION_KIND": normalized_connection_kind,
            "AGENTSASSEMBLE_MEETING_ID": normalized_meeting_id,
            "AGENTSASSEMBLE_ENGAGEMENT_MODE": normalized_engagement_mode,
        },
        "instructions": [
            "Run commands.register once before observing the room.",
            "Loop commands.wait_next and inspect the returned action.",
            "Read room.shared_memory as official-only background context when present.",
            "Use execution_contract.context_durability as the declared agent-private context boundary.",
            "For lobby actions, replace templates.say placeholders and run it once.",
            "For observe_lobby actions, run the returned ack_command and do not post a reply.",
            "For official_turn actions, replace templates.official_reply placeholders and run it once.",
            "For return_packet actions, run the returned read_command before the ack_command and do not post a reply.",
            "Use templates.heartbeat to report online, working, error, or cursor-only observation.",
            "Run commands.leave before intentionally exiting the room.",
        ],
        "safety": {
            "room_contacted": False,
            "provider_executed": False,
            "contains_secrets": False,
        },
    }


def _execution_contract(*, provider_kind: str, connection_kind: str) -> dict[str, str]:
    contract = live_agent_context_contract(provider_kind, connection_kind)
    return {
        "join_semantics": contract["join_semantics"],
        "context_durability": contract["context_durability"],
        "evidence_basis": "operator_supplied_join_brief",
        "provider_execution": "not_started_by_join_brief",
    }


def _join_register_command(*, server: str, agent: dict[str, object]) -> list[str]:
    command = _module_cli_command("live-agent", "register", "--server", server)
    command.extend(
        [
            "--agent-id",
            str(agent["agent_id"]),
            "--display-name",
            str(agent["display_name"]),
            "--provider-kind",
            str(agent["provider_kind"]),
            "--connection-kind",
            str(agent["connection_kind"]),
        ]
    )
    meeting_id = str(agent.get("meeting_id") or "")
    if meeting_id:
        command.extend(["--meeting-id", meeting_id])
    command.extend(["--engagement-mode", str(agent["engagement_mode"]), "--json"])
    return command


def _join_wait_next_command(
    *,
    server: str,
    agent_id: str,
    timeout: str,
    poll_interval: str,
    max_chain_depth: str,
) -> list[str]:
    return _module_cli_command(
        "live-agent",
        "wait-next",
        "--server",
        server,
        "--agent-id",
        agent_id,
        "--max-chain-depth",
        max_chain_depth,
        "--timeout",
        timeout,
        "--poll-interval",
        poll_interval,
        "--json",
    )


def _join_room_command(*, server: str, agent_id: str) -> list[str]:
    return _module_cli_command("live-agent", "room", "--server", server, "--agent-id", agent_id)


def _join_roster_gate_command(*, server: str, agent_id: str, meeting_id: str) -> list[str]:
    command = _module_cli_command(
        "live-agent",
        "list",
        "--server",
        server,
        "--agent-id",
        agent_id,
    )
    if meeting_id:
        command.extend(["--meeting-id", meeting_id])
    command.extend(["--require-match", "--fail-on-attention", "--json"])
    return command


def _join_leave_command(*, server: str, agent_id: str) -> list[str]:
    return _module_cli_command(
        "live-agent",
        "leave",
        "--server",
        server,
        "--agent-id",
        agent_id,
        "--json",
    )


def _join_say_template(*, server: str, agent_id: str) -> list[str]:
    return _module_cli_command(
        "live-agent",
        "say",
        "--server",
        server,
        "--agent-id",
        agent_id,
        "--source-event-id",
        "{source_event_id}",
        "--auto-chain-depth",
        "{auto_chain_depth}",
        "--json",
        "--",
        "{message}",
    )


def _join_official_reply_template(*, server: str, agent_id: str) -> list[str]:
    return _module_cli_command(
        "live-agent",
        "official-reply",
        "--server",
        server,
        "--agent-id",
        agent_id,
        "--meeting-id",
        "{meeting_id}",
        "--source-event-id",
        "{source_event_id}",
        "--json",
        "--",
        "{message}",
    )


def _join_heartbeat_template(*, server: str, agent_id: str) -> list[str]:
    return _module_cli_command(
        "live-agent",
        "heartbeat",
        "--server",
        server,
        "--agent-id",
        agent_id,
        "--status",
        "{status}",
        "--last-error={last_error}",
        "--last-reply-at={last_reply_at}",
        "--last-observed-event-id={last_observed_event_id}",
        "--last-observed-live-event-id={last_observed_live_event_id}",
        "--json",
    )


def _module_cli_command(*args: str) -> list[str]:
    return ["python3", "-m", "agentsassemble.cli", *args]


def _clean_scalar_text(value: object, *, limit: int, field_name: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string.")
    return clean_lobby_text(value, limit=limit)


def _cli_number(value: object) -> str:
    number = _safe_nonnegative_float(value)
    return f"{number:g}"


def _safe_nonnegative_float(value: object) -> float:
    if isinstance(value, bool):
        return 0.0
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) and number >= 0 else 0.0


def _safe_nonnegative_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return number if number >= 0 else 0
