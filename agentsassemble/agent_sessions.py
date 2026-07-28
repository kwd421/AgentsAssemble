"""Compatibility exports for current Agent Session application services."""

from pathlib import Path
from typing import Any

from agentsassemble.application import agent_sessions as _owned
from agentsassemble.application.agent_sessions import (
    AGENT_SESSION_AUTO_TURN_QUEUE_LIMIT,
    CODEX_APP_SERVER_SMOKE_COMMANDS,
    DEFAULT_ROOM_CONTEXT_CHARS,
    DEFAULT_ROOM_CONTEXT_MESSAGES,
    DEFAULT_ROOM_TURN_MAX_PROMPT_CHARS,
    DEFAULT_ROOM_TURN_MAX_RECENT_EVENTS,
    ROOM_MEMORY_EMPTY,
    UNSUPPORTED_MEDIA_AUDIT_NOTE,
    AgentSessionAdapter,
    AgentTurnAdapter,
    AgentTurnChunk,
    AgentTurnCommandRunner,
    AgentTurnCommandStreamer,
    AgentTurnRunner,
    AgyAgentSessionAdapter,
    ClaudeAgentSessionAdapter,
    CodexAppServerRuntime,
    CodexAppServerRuntimeManager,
    CommandRunner,
    GrokAgentSessionAdapter,
    ProcessFactory,
    ProviderSyncCursorParityError,
    RoomRepository,
    UnsupportedAgentSessionAdapter,
    agent_session_codex_jsonl_turn_runner,
    agent_session_command_turn_runner,
    agent_session_streaming_command_turn_runner,
    assert_provider_sync_cursor_parity,
    build_agent_session_launch_plan,
    build_agent_session_plain_turn_command,
    build_agent_session_turn_command,
    build_provider_bootstrap_input,
    build_provider_recovery_input,
    build_provider_turn_input,
    build_room_turn_packet,
    clean_agent_session_provider_kind,
    clean_codex_app_server_runtime_sharing_policy,
    clean_provider_session_id,
    clean_room_request_payload,
    codex_app_server_runtime_command,
    project_room_context,
    provider_sync_session_fields,
    room_memory_from_session,
    run_codex_app_server_smoke,
    runtime_profile_key,
    runtime_profile_settings,
    sanitized_provider_environment,
    _codex_app_server_smoke_turn_failure_kind,
    _default_agent_turn_jsonl_streamer,
    _diagnostics_indicate_timeout,
    _empty_codex_app_server_smoke_metrics,
    _finalize_codex_app_server_smoke_metrics,
)
from agentsassemble.persistence.local.room.repository import RoomStore


def _local_repository(
    output_root: Path,
    repository: RoomRepository | None,
) -> RoomRepository:
    """Choose local persistence only at this legacy compatibility boundary."""

    return repository if repository is not None else RoomStore(output_root)


class AgentSessionProcessService(_owned.AgentSessionProcessService):
    """Legacy service wrapper with the historical local-store default."""

    def run_turn(
        self,
        output_root: Path,
        payload: dict[str, object],
        *,
        repository: RoomRepository | None = None,
    ) -> dict[str, object]:
        return super().run_turn(
            output_root,
            payload,
            repository=_local_repository(output_root, repository),
        )


def resume_agent_session_payload(
    output_root: Path,
    payload: dict[str, object],
    *,
    repository: RoomRepository | None = None,
    **kwargs: Any,
) -> dict[str, object]:
    return _owned.resume_agent_session_payload(
        output_root,
        payload,
        repository=_local_repository(output_root, repository),
        **kwargs,
    )


def create_agent_session_payload(
    output_root: Path,
    payload: dict[str, object],
    *,
    repository: RoomRepository | None = None,
    **kwargs: Any,
) -> dict[str, object]:
    return _owned.create_agent_session_payload(
        output_root,
        payload,
        repository=_local_repository(output_root, repository),
        **kwargs,
    )


def run_agent_session_turn_payload(
    output_root: Path,
    payload: dict[str, object],
    *,
    repository: RoomRepository | None = None,
    **kwargs: Any,
) -> dict[str, object]:
    return _owned.run_agent_session_turn_payload(
        output_root,
        payload,
        repository=_local_repository(output_root, repository),
        **kwargs,
    )


def run_next_agent_session_turn_payload(
    output_root: Path,
    payload: dict[str, object],
    *,
    repository: RoomRepository | None = None,
    **kwargs: Any,
) -> dict[str, object]:
    return _owned.run_next_agent_session_turn_payload(
        output_root,
        payload,
        repository=_local_repository(output_root, repository),
        **kwargs,
    )


def enqueue_agent_session_auto_turn_for_lobby_event(
    output_root: Path,
    lobby_event: dict[str, object],
    *,
    repository: RoomRepository | None = None,
    **kwargs: Any,
) -> dict[str, object]:
    return _owned.enqueue_agent_session_auto_turn_for_lobby_event(
        output_root,
        lobby_event,
        repository=_local_repository(output_root, repository),
        **kwargs,
    )


def room_sse_frames_after_cursor(
    output_root: Path,
    room_id: str,
    *,
    cursor: str = "",
    include_heartbeat: bool = True,
    repository: RoomRepository | None = None,
) -> list[str]:
    return _owned.room_sse_frames_after_cursor(
        output_root,
        room_id,
        cursor=cursor,
        include_heartbeat=include_heartbeat,
        repository=_local_repository(output_root, repository),
    )


def stream_room_sse_frames(
    output_root: Path,
    room_id: str,
    *,
    cursor: str = "",
    max_iterations: int | None = None,
    wait: Any = None,
    repository: RoomRepository | None = None,
):
    return _owned.stream_room_sse_frames(
        output_root,
        room_id,
        cursor=cursor,
        max_iterations=max_iterations,
        wait=wait,
        repository=_local_repository(output_root, repository),
    )


def room_status_payload(
    output_root: Path,
    room_id: str,
    *,
    repository: RoomRepository | None = None,
) -> dict[str, object]:
    return _owned.room_status_payload(
        output_root,
        room_id,
        repository=_local_repository(output_root, repository),
    )


def room_action_payload(
    output_root: Path,
    payload: dict[str, object],
    action: str,
    *,
    repository: RoomRepository | None = None,
) -> dict[str, object]:
    return _owned.room_action_payload(
        output_root,
        payload,
        action,
        repository=_local_repository(output_root, repository),
    )


def room_lifecycle_payload(
    output_root: Path,
    payload: dict[str, object],
    action: str,
    *,
    repository: RoomRepository | None = None,
) -> dict[str, object]:
    return _owned.room_lifecycle_payload(
        output_root,
        payload,
        action,
        repository=_local_repository(output_root, repository),
    )


def active_room_members(
    output_root: Path,
    room_id: str,
    *,
    repository: RoomRepository | None = None,
) -> list[dict[str, object]]:
    return _owned.active_room_members(
        output_root,
        room_id,
        repository=_local_repository(output_root, repository),
    )


def merge_room_store_members(
    output_root: Path,
    meeting_id: str,
    existing_members: list[dict[str, object]],
    *,
    repository: RoomRepository | None = None,
) -> list[dict[str, object]]:
    return _owned.merge_room_store_members(
        output_root,
        meeting_id,
        existing_members,
        repository=_local_repository(output_root, repository),
    )

__all__ = [
    "AGENT_SESSION_AUTO_TURN_QUEUE_LIMIT",
    "CODEX_APP_SERVER_SMOKE_COMMANDS",
    "DEFAULT_ROOM_CONTEXT_CHARS",
    "DEFAULT_ROOM_CONTEXT_MESSAGES",
    "DEFAULT_ROOM_TURN_MAX_PROMPT_CHARS",
    "DEFAULT_ROOM_TURN_MAX_RECENT_EVENTS",
    "ROOM_MEMORY_EMPTY",
    "UNSUPPORTED_MEDIA_AUDIT_NOTE",
    "AgentSessionAdapter",
    "AgentSessionProcessService",
    "AgentTurnAdapter",
    "AgentTurnChunk",
    "AgentTurnCommandRunner",
    "AgentTurnCommandStreamer",
    "AgentTurnRunner",
    "AgyAgentSessionAdapter",
    "ClaudeAgentSessionAdapter",
    "CodexAppServerRuntime",
    "CodexAppServerRuntimeManager",
    "CommandRunner",
    "GrokAgentSessionAdapter",
    "ProcessFactory",
    "ProviderSyncCursorParityError",
    "RoomRepository",
    "RoomStore",
    "UnsupportedAgentSessionAdapter",
    "_default_agent_turn_jsonl_streamer",
    "_codex_app_server_smoke_turn_failure_kind",
    "_diagnostics_indicate_timeout",
    "_empty_codex_app_server_smoke_metrics",
    "_finalize_codex_app_server_smoke_metrics",
    "active_room_members",
    "agent_session_codex_jsonl_turn_runner",
    "agent_session_command_turn_runner",
    "agent_session_streaming_command_turn_runner",
    "assert_provider_sync_cursor_parity",
    "build_agent_session_launch_plan",
    "build_agent_session_plain_turn_command",
    "build_agent_session_turn_command",
    "build_provider_bootstrap_input",
    "build_provider_recovery_input",
    "build_provider_turn_input",
    "build_room_turn_packet",
    "clean_agent_session_provider_kind",
    "clean_codex_app_server_runtime_sharing_policy",
    "clean_provider_session_id",
    "clean_room_request_payload",
    "codex_app_server_runtime_command",
    "create_agent_session_payload",
    "enqueue_agent_session_auto_turn_for_lobby_event",
    "merge_room_store_members",
    "project_room_context",
    "provider_sync_session_fields",
    "resume_agent_session_payload",
    "room_action_payload",
    "room_lifecycle_payload",
    "room_memory_from_session",
    "room_sse_frames_after_cursor",
    "room_status_payload",
    "run_agent_session_turn_payload",
    "run_codex_app_server_smoke",
    "run_next_agent_session_turn_payload",
    "runtime_profile_key",
    "runtime_profile_settings",
    "sanitized_provider_environment",
    "stream_room_sse_frames",
]
