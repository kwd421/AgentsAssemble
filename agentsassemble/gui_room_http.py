"""Public coordinator and local runtime adapters for room HTTP routes.

The domain registrars keep route behavior near the room concern that owns it.
This module retains the public ``register_room_routes`` import and the local
provider runner names used by ``gui.py`` and the HTTP tests.
"""
from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from http import HTTPStatus

from agentsassemble.agent_sessions import (
    AgentSessionProcessService,
    create_agent_session_payload,
    enqueue_agent_session_auto_turn_for_lobby_event,
    resume_agent_session_payload,
    room_action_payload,
    room_lifecycle_payload,
    room_status_payload,
    run_agent_session_turn_payload,
    run_next_agent_session_turn_payload,
)
from agentsassemble.codex_app_server_runtime import CodexAppServerRuntimeManager
from agentsassemble.web.routes.agent_sessions import register_agent_session_routes
from agentsassemble.web.routes.room_invite import register_invite_admission_routes
from agentsassemble.gui_room_lifecycle_http import (
    register_room_history_routes,
    register_room_lifecycle_routes,
)
from agentsassemble.gui_room_moderation_media_http import register_moderation_media_routes
from agentsassemble.web.router import RequestContext, Router
from agentsassemble.live_agent_room_admin import expel_live_agent_from_room_payload
from agentsassemble.live_agents import connect_live_agent, read_live_agents
from agentsassemble.meeting_events import (
    append_lobby_event_to_file,
    clean_lobby_text,
    read_lobby_events,
    read_lobby_events_after,
)
from agentsassemble.multi_host_invites import NATIVE_REMOTE_ROOM_CLIENT_KIND
from agentsassemble.room_channels import (
    ChannelError,
    add_channel,
    channel_stream_filename,
    find_channel,
    remove_channel,
    rename_channel,
    reorder_channels,
)
from agentsassemble.room_invite import (
    active_sessions_summary,
    create_room_invite,
    get_public_url,
    join_room_with_invite,
    pending_invites_summary,
    revoke_invite,
    revoke_session,
    revoke_sessions_for_participant,
)
from agentsassemble.room_members import (
    is_room_member_muted,
    remove_room_member,
    room_members_payload,
    set_room_member_muted,
    upsert_room_member,
)
from agentsassemble.room_speech import (
    ActorIdentity,
    GovernedLobbySayRejected,
    ensure_lobby_say_allowed,
    governed_channel_say,
    governed_lobby_say,
)
from agentsassemble.persistence.local.room.repository import RoomStore
from agentsassemble.room_users import (
    grant_operator_to_device,
    list_rooms,
    operator_user_id,
    set_room_archived,
    user_for_participant,
)
from agentsassemble.room_votes import vote_summary
from agentsassemble.stable_entry import stable_entry_url
from agentsassemble.voice_presence import (
    join_voice,
    leave_all_voice,
    leave_voice,
    voice_participants,
)

# Historical callers imported these service names from this route module. They
# remain compatibility re-exports; new code should import the owner modules
# above directly instead of treating this coordinator as a service catalog.


_CODEX_APP_SERVER_RUNTIMES = CodexAppServerRuntimeManager()


def _speech_rejection_status(category: str) -> HTTPStatus:
    if category == "rate_limited":
        return HTTPStatus.TOO_MANY_REQUESTS
    if category == "chain_depth":
        return HTTPStatus.CONFLICT
    if category in {"read_only", "muted"}:
        return HTTPStatus.FORBIDDEN
    return HTTPStatus.BAD_REQUEST


def _local_agent_session_command_runner(command: list[str]) -> dict[str, object]:
    process = subprocess.Popen(
        [str(part) for part in command],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return {"returncode": 0, "pid": process.pid}


def _local_agent_session_turn_command_runner(
    command: list[str],
    prompt: str,
    timeout_seconds: float,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(part) for part in command],
        input=prompt,
        text=True,
        capture_output=True,
        timeout=timeout_seconds,
        check=False,
    )


def _local_agent_session_turn_command_streamer(
    command: list[str],
    prompt: str,
    timeout_seconds: float,
):
    from agentsassemble.agent_sessions import _default_agent_turn_jsonl_streamer

    yield from _default_agent_turn_jsonl_streamer(command, prompt, timeout_seconds)


def _local_agent_session_turn_adapter(session: dict[str, object], packet: dict[str, object]):
    yield from _CODEX_APP_SERVER_RUNTIMES.send_turn(session, packet)


def _agent_session_control_allowed(ctx: RequestContext) -> bool:
    has_host_token = bool(ctx.provided_host_token())
    return ctx.uses_loopback_host() or (has_host_token and ctx.is_host()) or ctx.is_operator_session()


@dataclass(frozen=True)
class RoomRouteAdapters:
    agent_session_control_allowed: Callable[[RequestContext], bool]
    speech_rejection_status: Callable[[str], HTTPStatus]
    process_command_runner: Callable[..., object]
    turn_adapter: Callable[..., object]
    turn_command_runner: Callable[..., object]
    turn_command_streamer: Callable[..., object]


def _default_room_route_adapters() -> RoomRouteAdapters:
    return RoomRouteAdapters(
        agent_session_control_allowed=_agent_session_control_allowed,
        speech_rejection_status=_speech_rejection_status,
        process_command_runner=_local_agent_session_command_runner,
        turn_adapter=_local_agent_session_turn_adapter,
        turn_command_runner=_local_agent_session_turn_command_runner,
        turn_command_streamer=_local_agent_session_turn_command_streamer,
    )


def register_room_routes(
    router: Router,
    *,
    adapters: RoomRouteAdapters | None = None,
) -> None:
    """Attach the canonical room route domains to the exact-path router."""
    resolved = adapters or _default_room_route_adapters()

    register_room_history_routes(
        router,
        agent_session_control_allowed=resolved.agent_session_control_allowed,
        agent_turn_adapter=resolved.turn_adapter,
        speech_rejection_status=resolved.speech_rejection_status,
    )
    register_agent_session_routes(
        router,
        agent_session_control_allowed=resolved.agent_session_control_allowed,
        process_command_runner=resolved.process_command_runner,
        turn_adapter=resolved.turn_adapter,
        turn_command_runner=resolved.turn_command_runner,
        turn_command_streamer=resolved.turn_command_streamer,
    )
    register_room_lifecycle_routes(router)
    register_moderation_media_routes(
        router,
        agent_session_control_allowed=resolved.agent_session_control_allowed,
        agent_turn_adapter=resolved.turn_adapter,
        speech_rejection_status=resolved.speech_rejection_status,
    )
    register_invite_admission_routes(router)
