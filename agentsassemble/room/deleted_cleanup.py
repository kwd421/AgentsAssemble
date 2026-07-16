from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable

from agentsassemble.room.event_broker import RoomEventBroker
from agentsassemble.room.provider_registry import RoomProviderRegistry
from agentsassemble.room.repository import RoomRepository


RoomCleanup = Callable[[str], object]
CleanupScheduler = Callable[[float, Callable[[], None]], object]


class RoomDeletedCleanupService:
    """Complete idempotent supporting-state cleanup for a deleted room."""

    def __init__(
        self,
        *,
        store: RoomRepository,
        broker: RoomEventBroker,
        provider_registry: RoomProviderRegistry,
        output_root: Path,
        revoke_room_invites: RoomCleanup,
        revoke_room_sessions: RoomCleanup,
        purge_terminal_admission_workflows: RoomCleanup,
        delete_identity_room: RoomCleanup,
        remove_event_listener: RoomCleanup,
        schedule_cleanup: CleanupScheduler,
    ) -> None:
        self.store = store
        self.broker = broker
        self.provider_registry = provider_registry
        self.output_root = Path(output_root)
        self._revoke_room_invites = revoke_room_invites
        self._revoke_room_sessions = revoke_room_sessions
        self._purge_terminal_admission_workflows = (
            purge_terminal_admission_workflows
        )
        self._delete_identity_room = delete_identity_room
        self._remove_event_listener = remove_event_listener
        self._schedule_cleanup = schedule_cleanup

    def complete(
        self,
        room_id: str,
        room_name: str,
        ack: dict[str, object],
        deduplicated: bool,
    ) -> dict[str, object]:
        self.broker.broadcast_control(
            room_id,
            {
                "op": "room_deleted",
                "room_id": room_id,
                "room_name": room_name,
            },
        )
        revoked = {
            "revoked_invites": int(
                self._revoke_room_invites(room_id)
            ),
            "revoked_sessions": int(
                self._revoke_room_sessions(room_id)
            ),
            "purged_admission_workflows": int(
                self._purge_terminal_admission_workflows(room_id)
            ),
        }
        self._delete_identity_room(room_id)
        self._remove_event_listener(room_id)
        self.provider_registry.remove_room(room_id)
        self._remove_room_artifacts(room_id)
        self._schedule_disconnect(room_id)
        result = dict(ack.get("result") or {})
        completed_ack = {
            **ack,
            "result": {
                **result,
                **revoked,
            },
            "deduplicated": deduplicated,
        }
        self.store.update_deleted_room_record(
            room_id,
            result={
                **completed_ack,
                "deduplicated": False,
            },
            cleanup_status="complete",
        )
        return completed_ack

    def _remove_room_artifacts(self, room_id: str) -> None:
        for path in (
            self.output_root / "rooms" / room_id,
            self.output_root / "meetings" / room_id,
        ):
            if path.exists() and path.is_dir():
                shutil.rmtree(path)

    def _schedule_disconnect(self, room_id: str) -> None:
        self._schedule_cleanup(
            0.1,
            lambda: self.broker.disconnect_room(room_id),
        )


__all__ = [
    "CleanupScheduler",
    "RoomCleanup",
    "RoomDeletedCleanupService",
]
