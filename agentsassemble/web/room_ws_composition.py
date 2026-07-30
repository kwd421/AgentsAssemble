"""Compose canonical room WebSocket dependencies from GUI services."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from agentsassemble.application.gui import GuiApplicationServices
from agentsassemble.room.attachments import AttachmentError
from agentsassemble.room.realtime import RoomCommandRejected
from agentsassemble.room.repository import RoomRepository
from agentsassemble.room.speech import (
    ActorIdentity,
    GovernedLobbySayRejected,
    governed_lobby_say,
)
from agentsassemble.web.room_session import (
    WsCommandRejected,
    WsRoomDeps,
    WsSayRejected,
)


@dataclass(frozen=True)
class RoomWsComposition:
    stream_snapshot_payload: Callable[..., dict[str, object]]
    last_payload_event_id: Callable[[dict[str, object]], str | None]
    payload_signature: Callable[[dict[str, object]], str | None]
    lobby_payload_with_attachments: Callable[..., dict[str, object]]
    append_lobby_event: Callable[..., dict[str, object]]
    public_lobby_allows_room_scope: Callable[[dict[str, object]], bool]
    is_muted: Callable[[Path, str, str], bool]
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
    ws_ticket_store = services.ws_ticket_store

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

        def append_server_lobby_event(
            event_output_root: Path,
            event: dict[str, object],
            *,
            live_agent_endpoint: bool = False,
            allow_flow_metadata: bool = False,
        ) -> dict[str, object]:
            return composition.append_lobby_event(
                event_output_root,
                event,
                live_agent_endpoint=live_agent_endpoint,
                allow_flow_metadata=allow_flow_metadata,
                identity_backend=services.identity_backend,
            )

        def post_say(identity: dict, payload: dict) -> dict:
            try:
                resolved = composition.lobby_payload_with_attachments(
                    output_root,
                    dict(payload),
                )
            except AttachmentError as error:
                raise WsSayRejected(str(error), category="bad_message") from error
            try:
                event = governed_lobby_say(
                    output_root,
                    identity=ActorIdentity.from_mapping(identity),
                    payload=resolved,
                    append_lobby_event=append_server_lobby_event,
                    public_lobby_allows_room_scope=(
                        composition.public_lobby_allows_room_scope
                    ),
                    is_muted=composition.is_muted,
                )
                return event
            except GovernedLobbySayRejected as rejected:
                raise WsSayRejected(
                    str(rejected),
                    category=rejected.category,
                ) from rejected

        def set_thinking(identity: dict, on: bool) -> None:
            composition.mark_thinking(
                str(identity.get("meeting_id") or ""),
                str(identity.get("agent_id") or ""),
                on,
            )

        def execute_command(identity: dict, message: dict) -> dict[str, object]:
            def issue_bridge_connection(bridge_identity: dict[str, object]) -> dict[str, str]:
                room_id = str(bridge_identity.get("meeting_id") or "")
                session_id = str(
                    bridge_identity.get("session_id")
                    or bridge_identity.get("agent_id")
                    or ""
                )
                session_token, bridge_session = services.sessions.ensure_server_bridge(
                    f"{room_id}:{session_id}",
                    bridge_identity,
                )
                return {
                    "ticket": ws_ticket_store.issue(
                        bridge_session,
                        session_token=session_token,
                    ),
                    "session_token": session_token,
                }

            try:
                return room_realtime_controller.handle_command(
                    identity,
                    message,
                    server_url=composition.local_server_url(
                        handler.server.server_address,
                    ),
                    ticket_issuer=issue_bridge_connection,
                )
            except RoomCommandRejected as rejected:
                raise WsCommandRejected(
                    str(rejected),
                    code=rejected.code,
                ) from rejected

        return WsRoomDeps(
            read_lobby_after=read_lobby_after,
            read_roster=read_roster,
            read_side_chat_after=read_side_chat_after,
            post_say=post_say,
            is_muted=lambda meeting_id, agent_id: composition.is_muted(
                output_root,
                meeting_id,
                agent_id,
            ),
            set_thinking=set_thinking,
            is_session_active=lambda session_token: bool(
                services.sessions.verify(session_token),
            ),
            room_snapshot=lambda identity, after_seq: room_realtime_controller.snapshot(
                identity,
                after_seq=after_seq,
            ),
            execute_command=execute_command,
            on_subscribe=lambda identity, streams, after_seq: channel.subscribe(streams),
        )

    return ws_room_deps
