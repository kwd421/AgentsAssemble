"""Compose current canonical room WebSocket dependencies."""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from agentsassemble.application.gui import GuiApplicationServices
from agentsassemble.features.side_chat.service import read_side_chat
from agentsassemble.plugin.host_service import plugin_registry
from agentsassemble.room.realtime import RoomCommandRejected
from agentsassemble.room.repository import RoomRepository
from agentsassemble.web.room_session import WsCommandRejected, WsRoomDeps


def build_ws_room_deps_factory(
    *,
    output_root: Path,
    services: GuiApplicationServices,
    room_repository: RoomRepository,
    local_server_url: Callable[[object], str],
    execute_room_command: Callable[
        [dict[str, object], dict[str, object], str], dict[str, object]
    ],
) -> Callable[..., WsRoomDeps]:
    room_realtime_controller = services.room_realtime_controller

    def ws_room_deps(channel, handler) -> WsRoomDeps:
        def read_side_chat_after(meeting_id: str, after_id: str) -> tuple[list, str]:
            events = read_side_chat(output_root, meeting_id=meeting_id)
            if after_id:
                for index, event in enumerate(events):
                    if event.get("id") == after_id:
                        events = events[index + 1 :]
                        break
            latest = str(events[-1].get("id") or after_id) if events else after_id
            return events, latest

        def execute_command(identity: dict, message: dict) -> dict[str, object]:
            try:
                return execute_room_command(
                    identity,
                    message,
                    local_server_url(handler.server.server_address),
                )
            except RoomCommandRejected as rejected:
                raise WsCommandRejected(
                    str(rejected),
                    code=rejected.code,
                ) from rejected

        def active_plugin_id(meeting_id: str) -> str:
            return str(
                room_repository.room_settings(meeting_id).get("activity_plugin")
                or ""
            )

        def subscribe(identity: dict, streams: set[str], _after_seq: int) -> None:
            channel.subscribe(streams)
            if "plugin" not in streams:
                return
            meeting_id = str(identity.get("meeting_id") or "")
            configured_plugin = active_plugin_id(meeting_id)
            if configured_plugin:
                plugin_registry().activate(meeting_id, configured_plugin)

        return WsRoomDeps(
            read_side_chat_after=read_side_chat_after,
            set_thinking=lambda _identity, _on: None,
            is_session_active=lambda session_token: bool(
                services.sessions.verify(session_token)
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


__all__ = ["build_ws_room_deps_factory"]
