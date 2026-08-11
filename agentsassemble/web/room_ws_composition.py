"""Compose canonical room WebSocket dependencies from GUI services."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from agentsassemble.application.gui import GuiApplicationServices
from agentsassemble.plugin.host_service import plugin_registry
from agentsassemble.room.realtime import RoomCommandRejected
from agentsassemble.room.repository import RoomRepository
from agentsassemble.web.room_session import (
    WsCommandRejected,
    WsRoomDeps,
)


@dataclass(frozen=True)
class RoomWsComposition:
    stream_snapshot_payload: Callable[..., dict[str, object]]
    last_payload_event_id: Callable[[dict[str, object]], str | None]
    payload_signature: Callable[[dict[str, object]], str | None]
    mark_thinking: Callable[[str, str, bool], None]
    local_server_url: Callable[[object], str]


def build_ws_room_deps_factory(
    *,
    output_root: Path,
    services: GuiApplicationServices,
    room_repository: RoomRepository,
    composition: RoomWsComposition,
) -> Callable[..., WsRoomDeps]:
    room_realtime_controller = services.room_realtime_controller

    def ws_room_deps(channel, handler) -> WsRoomDeps:
        def read_lobby_after(meeting_id: str, after_id: str) -> tuple[list, str]:
            payload = composition.stream_snapshot_payload(
                output_root,
                "lobby",
                meeting_id=meeting_id,
                last_event_id=after_id or None,
                repository=room_repository,
            )
            events = list(payload.get("events", []))
            return events, (composition.last_payload_event_id(payload) or after_id)

        def read_roster(meeting_id: str) -> tuple[list, str]:
            payload = composition.stream_snapshot_payload(
                output_root,
                "roster",
                meeting_id=meeting_id,
                last_event_id=None,
                repository=room_repository,
                sessions=services.sessions.active_summary(),
            )
            return list(payload.get("members", [])), str(
                composition.payload_signature(payload) or "",
            )

        def read_side_chat_after(meeting_id: str, after_id: str) -> tuple[list, str]:
            payload = composition.stream_snapshot_payload(
                output_root,
                "side_chat",
                meeting_id=meeting_id,
                last_event_id=after_id or None,
                repository=room_repository,
            )
            events = list(payload.get("events", []))
            return events, (composition.last_payload_event_id(payload) or after_id)

        def set_thinking(identity: dict, on: bool) -> None:
            composition.mark_thinking(
                str(identity.get("meeting_id") or ""),
                str(identity.get("agent_id") or ""),
                on,
            )

        def execute_command(identity: dict, message: dict) -> dict[str, object]:
            try:
                return room_realtime_controller.handle_command(
                    identity,
                    message,
                    server_url=composition.local_server_url(
                        handler.server.server_address,
                    ),
                    ticket_issuer=services.issue_bridge_connection,
                )
            except RoomCommandRejected as rejected:
                raise WsCommandRejected(
                    str(rejected),
                    code=rejected.code,
                ) from rejected

        def active_plugin_id(meeting_id: str) -> str:
            return str(
                room_repository.room_settings(meeting_id).get("activity_plugin") or ""
            )

        def subscribe(identity: dict, streams: set[str], _after_seq: int) -> None:
            channel.subscribe(streams)
            if "plugin" not in streams:
                return
            meeting_id = str(identity.get("meeting_id") or "")
            configured_plugin = active_plugin_id(meeting_id)
            if configured_plugin:
                # Room settings are durable while plugin processes are not.
                # Recreate the process before the immediate subscribe poll so
                # a reconnect receives a fresh persisted snapshot.
                plugin_registry().activate(meeting_id, configured_plugin)

        return WsRoomDeps(
            read_lobby_after=read_lobby_after,
            read_roster=read_roster,
            read_side_chat_after=read_side_chat_after,
            set_thinking=set_thinking,
            is_session_active=lambda session_token: bool(
                services.sessions.verify(session_token),
            ),
            room_snapshot=lambda identity, after_seq: room_realtime_controller.snapshot(
                identity,
                after_seq=after_seq,
            ),
            execute_command=execute_command,
            on_subscribe=subscribe,
            active_plugin_id=active_plugin_id,
        )

    return ws_room_deps
