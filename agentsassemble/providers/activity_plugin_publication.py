"""Canonical Agent Bridge publication for staged activity-plugin commands."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

from agentsassemble.providers.bridge_protocol import (
    BridgeReportRejected,
    BridgeReportTimeout,
)
from agentsassemble.providers.bridge_report_tracker import BridgeReportTracker
from agentsassemble.room.text import clean_room_text


class ActivityPluginClient(Protocol):
    def plugin(
        self,
        plugin_id: str,
        action: str,
        args: dict[str, object] | None = None,
        *,
        revision: str = "",
        request_id: str = "",
    ) -> str: ...


class ActivityPluginPublicationError(RuntimeError):
    """The bridge could not determine whether an isolated plugin applied a command."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = clean_room_text(code, limit=128) or "plugin_command_failed"


@dataclass(frozen=True)
class ActivityPluginPublication:
    accepted: bool
    frame: dict[str, object]
    code: str = ""
    message: str = ""


def publish_activity_plugin_batch(
    batch: dict[str, object],
    *,
    client: ActivityPluginClient,
    report_tracker: BridgeReportTracker,
    pump: Callable[[], bool] | None,
    is_closed: Callable[[], bool],
    wait_interval_seconds: float,
    request_id: str,
) -> ActivityPluginPublication:
    """Publish once, preserving domain rejection without hiding transport loss.

    A plugin NACK means the isolated process received and rejected the game
    action. That outcome is already projected as ``plugin.error`` and must not
    poison the provider session. A missing ACK/NACK is different: command
    application is unknown, so the provider turn fails closed.
    """

    plugin_id = clean_room_text(batch.get("plugin_id"), limit=64)
    action = clean_room_text(batch.get("action"), limit=64)
    args = batch.get("args") if isinstance(batch.get("args"), dict) else {}
    revision = clean_room_text(batch.get("revision"), limit=64)
    try:
        frame = report_tracker.request(
            f"plugin.{action}",
            send=lambda correlated_id: client.plugin(
                plugin_id,
                action,
                args,
                revision=revision,
                request_id=correlated_id,
            ),
            pump=pump,
            is_closed=is_closed,
            wait_interval_seconds=wait_interval_seconds,
            request_id=request_id,
        )
    except BridgeReportRejected as error:
        return ActivityPluginPublication(
            accepted=False,
            frame={},
            code=error.code,
            message=str(error),
        )
    except BridgeReportTimeout as error:
        raise ActivityPluginPublicationError(str(error), code=error.code) from error
    return ActivityPluginPublication(accepted=True, frame=frame)


__all__ = [
    "ActivityPluginPublication",
    "ActivityPluginPublicationError",
    "publish_activity_plugin_batch",
]
