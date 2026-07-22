"""Governed WebSocket launch helpers for retained resident commands."""
from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from agentsassemble.live_agent_runner import ResidentAgentConfig


def joined_room_session_token(joined: object) -> str:
    if isinstance(joined, dict):
        return str(joined.get("session_token") or "")
    return str(joined or "")


def config_with_joined_room_session(
    config: ResidentAgentConfig,
    joined: object,
) -> ResidentAgentConfig:
    if not isinstance(joined, dict):
        return config
    updates: dict[str, str] = {}
    for field, key in (
        ("agent_id", "agent_id"),
        ("display_name", "display_name"),
        ("meeting_id", "meeting_id"),
    ):
        value = str(joined.get(key) or "").strip()
        if value:
            updates[field] = value
    return replace(config, **updates) if updates else config


def run_ws_resident_command(
    args: argparse.Namespace,
    config: ResidentAgentConfig,
    *,
    command_runner_for_config: Callable[..., Any],
    install_shutdown_handlers: Callable[[Callable[[], None]], Callable[[], None]],
    close_command_runner: Callable[[Any], None],
) -> int:
    from agentsassemble.room_engagement import resolve_engagement, room_uses_floor
    from agentsassemble.legacy.live_agent.room_resident import run_provider_ws_resident
    from agentsassemble.web.room_client import (
        fetch_room_conversation_mode,
        join_room_session,
        meeting_id_from_invite_token,
    )

    invite_token = str(getattr(args, "invite_token", "") or "")
    session_token = str(getattr(args, "session_token", "") or "")
    if not session_token:
        if not invite_token:
            raise ValueError("--transport ws requires --session-token or --invite-token.")
        joined_session = join_room_session(
            config.server,
            invite_token,
            display_name=config.display_name or config.agent_id,
            participant_type="agent",
            device_token=config.agent_id,
        )
        session_token = joined_room_session_token(joined_session)
        config = config_with_joined_room_session(config, joined_session)
    meeting_id = str(config.meeting_id or "") or meeting_id_from_invite_token(invite_token)
    if meeting_id and not config.meeting_id:
        config = replace(config, meeting_id=meeting_id)
    conversation_mode = fetch_room_conversation_mode(config.server, meeting_id)
    effective_engagement = resolve_engagement(conversation_mode, config.engagement_mode)
    use_floor = room_uses_floor(conversation_mode)
    command_runner = command_runner_for_config(
        config,
        output_root=str(getattr(args, "output_root", "") or ""),
    )
    restore_signal_handlers = install_shutdown_handlers(
        lambda: close_command_runner(command_runner)
    )
    try:
        replies = run_provider_ws_resident(
            config.server,
            session_token,
            config,
            command_runner,
            max_replies=int(getattr(config, "max_ticks", 0) or 0),
            engagement_mode=effective_engagement,
            use_floor=use_floor,
        )
    finally:
        restore_signal_handlers()
        close_command_runner(command_runner)
    print(f"WS resident agent stopped after posting {replies} replies")
    return 0


def run_ws_group_resident(
    config: ResidentAgentConfig,
    *,
    command_runner_for_config: Callable[..., Any],
    close_command_runner: Callable[[Any], None],
) -> int:
    from agentsassemble.room_engagement import resolve_engagement, room_uses_floor
    from agentsassemble.legacy.live_agent.room_resident import run_provider_ws_resident
    from agentsassemble.web.room_client import (
        fetch_room_conversation_mode,
        join_room_session,
        meeting_id_from_invite_token,
    )

    invite_token = str(getattr(config, "invite_token", "") or "")
    if not invite_token:
        raise ValueError(f"{config.agent_id}: ws transport requires invite_token in group config.")
    joined_session = join_room_session(
        config.server,
        invite_token,
        display_name=config.display_name or config.agent_id,
        participant_type="agent",
        device_token=config.agent_id,
    )
    session_token = joined_room_session_token(joined_session)
    config = config_with_joined_room_session(config, joined_session)
    meeting_id = str(config.meeting_id or "") or meeting_id_from_invite_token(invite_token)
    if meeting_id and not config.meeting_id:
        config = replace(config, meeting_id=meeting_id)
    conversation_mode = fetch_room_conversation_mode(config.server, meeting_id)
    effective_engagement = resolve_engagement(conversation_mode, config.engagement_mode)
    use_floor = room_uses_floor(conversation_mode)
    command_runner = command_runner_for_config(config)
    try:
        return run_provider_ws_resident(
            config.server,
            session_token,
            config,
            command_runner,
            max_replies=int(config.max_ticks or 0),
            engagement_mode=effective_engagement,
            use_floor=use_floor,
        )
    finally:
        close_command_runner(command_runner)
