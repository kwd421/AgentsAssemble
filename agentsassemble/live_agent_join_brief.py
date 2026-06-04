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
        "read_since": _join_read_since_command(server=normalized_server, agent_id=normalized_agent_id),
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
        "dm_reply": _join_dm_reply_template(server=normalized_server, agent_id=normalized_agent_id),
        "heartbeat": _join_heartbeat_template(server=normalized_server, agent_id=normalized_agent_id),
    }
    mcp = {
        "profile": "participant",
        "command": _join_mcp_participant_command(
            server=normalized_server,
            agent_id=normalized_agent_id,
            display_name=normalized_display_name,
            provider_kind=normalized_provider_kind,
            connection_kind=normalized_connection_kind,
            meeting_id=normalized_meeting_id,
            engagement_mode=normalized_engagement_mode,
        ),
        "contract": "agent-owned room tooling; does not start a provider CLI",
    }
    execution_contract = _execution_contract(
        provider_kind=normalized_provider_kind,
        connection_kind=normalized_connection_kind,
    )
    return {
        "status": "generated",
        "packet_kind": "agent_owned_entry_packet",
        "agent": agent,
        "entry_contract": _entry_contract(),
        "admission_contract": _admission_contract(),
        "execution_contract": execution_contract,
        "commands": commands,
        "templates": templates,
        "mcp": mcp,
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
            "Treat this JSON as an agent-owned entry packet: the agent reads room diffs and chooses its own next room action.",
            "Run commands.register once before observing the room.",
            "Loop commands.wait_next and inspect the returned action.",
            "Use commands.read_since when you want the raw room diff instead of the next action.",
            "Read room.shared_memory as official-only background context when present.",
            "Use execution_contract.context_durability as the declared agent-private context boundary.",
            "Use execution_contract.sandbox_enforcement as the declared sandbox boundary.",
            "Treat admission_contract as the boundary: this packet is not host admission or identity proof by itself.",
            "For lobby actions, replace templates.say placeholders and run it once.",
            "For observe_lobby actions, run the returned ack_command and do not post a reply.",
            "For dm actions, replace templates.dm_reply placeholders and run it once.",
            "For official_turn actions, replace templates.official_reply placeholders and run it once.",
            "For return_packet actions, run the returned read_command before the ack_command and do not post a reply.",
            "Use templates.heartbeat to report online, working, error, or cursor-only observation.",
            "If your host supports MCP, connect mcp.command as participant tooling instead of relying on host-side prompt injection.",
            "Run commands.leave before intentionally exiting the room.",
        ],
        "safety": {
            "room_contacted": False,
            "provider_executed": False,
            "contains_secrets": False,
        },
    }


def _admission_contract() -> dict[str, str]:
    return {
        "host_admission": "required_before_room_access",
        "identity_proof": "not_included_in_join_brief",
        "lan_invite_proof": "separate_hmac_invite_optional",
        "registration_effect": "not_registered_until_commands_register_runs",
        "network_scope": "local_or_trusted_lan_only_until_signed_room_apis",
        "provider_execution": "not_started_by_join_brief",
    }


def _entry_contract() -> dict[str, object]:
    return {
        "mode": "agent_owned",
        "room_role": "place_record_state_board",
        "provider_context": "provider_owned",
        "host_prompt_injection": "not_required",
        "flow_status": "play_mode_demo_or_auxiliary",
        "primary_entry_paths": ["mcp.command", "self_service", "cli.commands"],
        "tool_order": [
            "commands.register",
            "commands.wait_next",
            "commands.read_since",
            "templates.say",
            "templates.official_reply",
            "templates.dm_reply",
            "templates.heartbeat",
            "commands.leave",
            "mcp.command",
        ],
        "self_service_loop": [
            "Start from a host-approved self_service resident config.",
            "Use AGENTSASSEMBLE_WAIT_NEXT_COMMAND to observe the next room action.",
            "Use AGENTSASSEMBLE_ROOM_COMMAND or AGENTSASSEMBLE_READ_SINCE-style CLI commands when extra room context is needed.",
            "Use AGENTSASSEMBLE_SAY_COMMAND_TEMPLATE, AGENTSASSEMBLE_DM_REPLY_COMMAND_TEMPLATE, or AGENTSASSEMBLE_OFFICIAL_REPLY_COMMAND_TEMPLATE for exactly one reply.",
            "Use AGENTSASSEMBLE_HEARTBEAT_COMMAND_TEMPLATE to preserve cursors and status.",
            "Use AGENTSASSEMBLE_LEAVE_COMMAND before intentional exit.",
        ],
    }


def _execution_contract(*, provider_kind: str, connection_kind: str) -> dict[str, str]:
    contract = live_agent_context_contract(provider_kind, connection_kind)
    return {
        "join_semantics": contract["join_semantics"],
        "context_durability": contract["context_durability"],
        "sandbox_enforcement": contract["sandbox_enforcement"],
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


def _join_read_since_command(*, server: str, agent_id: str) -> list[str]:
    return _module_cli_command("live-agent", "read-since", "--server", server, "--agent-id", agent_id, "--json")


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


def _join_dm_reply_template(*, server: str, agent_id: str) -> list[str]:
    return _module_cli_command(
        "live-agent",
        "dm-reply",
        "--server",
        server,
        "--agent-id",
        agent_id,
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
        "--last-observed-dm-event-id={last_observed_dm_event_id}",
        "--json",
    )


def _join_mcp_participant_command(
    *,
    server: str,
    agent_id: str,
    display_name: str,
    provider_kind: str,
    connection_kind: str,
    meeting_id: str,
    engagement_mode: str,
) -> list[str]:
    command = _module_cli_command(
        "mcp",
        "serve",
        "--profile",
        "participant",
        "--server",
        server,
        "--agent-id",
        agent_id,
        "--display-name",
        display_name,
        "--provider-kind",
        provider_kind,
        "--connection-kind",
        connection_kind,
    )
    if meeting_id:
        command.extend(["--meeting-id", meeting_id])
    command.extend(["--engagement-mode", engagement_mode])
    return command


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
