"""Private activity-plugin state and one-turn agent command staging.

Provider processes cannot access the server-owned plugin process directly.
They receive a bounded snapshot through the private RoomPortal and stage at
most one action plus one side-chat line.  AgentBridge forwards that batch over
the canonical plugin WebSocket operation after the provider turn completes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Iterable

from agentsassemble.plugin.agent_tools import plugin_agent_tool_names
from agentsassemble.room.text import clean_room_text


class ActivityPluginPortalError(ValueError):
    pass


class ActivityPluginPortal:
    def __init__(
        self,
        root: Path,
        *,
        participant_id: str,
        write_json: Callable[[Path, dict[str, object]], None],
    ) -> None:
        self.participant_id = clean_room_text(participant_id, limit=128)
        self.state_path = root / "activity-plugin-state.json"
        self.action_path = root / "activity-plugin-action.json"
        self.speech_path = root / "activity-plugin-speech.json"
        self._write_json = write_json

    def ingest_frame(self, frame: dict[str, object]) -> None:
        if clean_room_text(frame.get("stream"), limit=32) != "plugin":
            return
        events = frame.get("events") if isinstance(frame.get("events"), list) else []
        for event in events:
            if not isinstance(event, dict):
                continue
            event_type = clean_room_text(event.get("type"), limit=64)
            payload = event.get("payload")
            plugin_id = clean_room_text(event.get("plugin_id"), limit=64)
            if (
                event_type not in {"plugin.snapshot", "plugin.delta"}
                or not plugin_id
                or not isinstance(payload, dict)
            ):
                continue
            self._write_json(
                self.state_path,
                {"plugin_id": plugin_id, "payload": payload},
            )

    def begin_observation(
        self,
        *,
        plugin_id: str,
        participant_ids: Iterable[str],
    ) -> dict[str, object]:
        self.action_path.unlink(missing_ok=True)
        self.speech_path.unlink(missing_ok=True)
        plugin = clean_room_text(plugin_id, limit=64)
        if not plugin:
            return {}
        stored = self._read_json(self.state_path)
        if clean_room_text(stored.get("plugin_id"), limit=64) != plugin:
            return {}
        payload = stored.get("payload")
        if not isinstance(payload, dict):
            return {}
        colonists = payload.get("colonists")
        colonist_ids = [
            clean_room_text(value.get("id"), limit=64)
            for value in colonists if isinstance(value, dict)
        ] if isinstance(colonists, list) else []
        agents = sorted(
            {
                clean_room_text(value, limit=128)
                for value in participant_ids
                if clean_room_text(value, limit=128)
            }
        )
        try:
            colonist_id = colonist_ids[agents.index(self.participant_id)]
        except (ValueError, IndexError):
            return {}
        tools = plugin_agent_tool_names(plugin)
        if not tools:
            return {}
        return {
            "activity_plugin": plugin,
            "activity_plugin_revision": str(payload.get("revision") or "0"),
            "activity_plugin_colonist_id": colonist_id,
            "activity_plugin_tools": sorted(tools),
        }

    def active_tool_names(self, turn: dict[str, object]) -> frozenset[str]:
        plugin = clean_room_text(turn.get("activity_plugin"), limit=64)
        values = turn.get("activity_plugin_tools")
        if not plugin or not isinstance(values, list):
            return frozenset()
        declared = plugin_agent_tool_names(plugin)
        return frozenset(
            value for value in values
            if isinstance(value, str) and value in declared
        )

    def observe(self) -> dict[str, object]:
        turn, payload = self._active_state("observe")
        return {
            "plugin_id": turn["activity_plugin"],
            "colonist_id": turn["activity_plugin_colonist_id"],
            "snapshot": payload,
        }

    def inspect(self, arguments: dict[str, object]) -> dict[str, object]:
        turn, payload = self._active_state("inspect")
        target_type = clean_room_text(arguments.get("target_type"), limit=32)
        if target_type == "colonist":
            target_id = clean_room_text(
                arguments.get("target_id") or turn["activity_plugin_colonist_id"],
                limit=64,
            )
            values = payload.get("colonists") if isinstance(payload.get("colonists"), list) else []
            target = next(
                (
                    value for value in values
                    if isinstance(value, dict)
                    and clean_room_text(value.get("id"), limit=64) == target_id
                ),
                None,
            )
            if target is None:
                raise ActivityPluginPortalError(f"Colonist {target_id!r} was not found.")
            return {"colonist": target}
        if target_type == "structure":
            return {
                "structures": payload.get("structures")
                if isinstance(payload.get("structures"), list)
                else []
            }
        if target_type == "cell":
            return {
                "cell": {
                    "x": int(arguments.get("x") or 0),
                    "y": int(arguments.get("y") or 0),
                }
            }
        raise ActivityPluginPortalError("target_type must be colonist, structure, or cell.")

    def stage_action(self, action: object, action_args: object) -> dict[str, object]:
        turn, _payload = self._active_state("act")
        if self.action_path.exists():
            raise ActivityPluginPortalError("Only one colony action may be staged per turn.")
        name = clean_room_text(action, limit=64)
        if not name:
            raise ActivityPluginPortalError("A colony action is required.")
        args = action_args if isinstance(action_args, dict) else {}
        self._write_json(self.action_path, {"action": name, "action_args": args})
        return {
            "queued": True,
            "plugin_id": turn["activity_plugin"],
            "colonist_id": turn["activity_plugin_colonist_id"],
            "action": name,
        }

    def stage_speech(self, text: object) -> dict[str, object]:
        turn, _payload = self._active_state("speak")
        if self.speech_path.exists():
            raise ActivityPluginPortalError("Only one colony speech line may be staged per turn.")
        value = clean_room_text(text, limit=500)
        if not value:
            raise ActivityPluginPortalError("Colony speech cannot be empty.")
        self._write_json(self.speech_path, {"text": value})
        return {
            "queued": True,
            "plugin_id": turn["activity_plugin"],
            "colonist_id": turn["activity_plugin_colonist_id"],
            "text": value,
        }

    def command_batch(self, turn_id: str) -> dict[str, object]:
        turn = self._active_turn(turn_id)
        plugin = clean_room_text(turn.get("activity_plugin"), limit=64)
        colonist_id = clean_room_text(
            turn.get("activity_plugin_colonist_id"), limit=64
        )
        if not plugin or not colonist_id:
            return {}
        action = self._read_json(self.action_path)
        speech = clean_room_text(self._read_json(self.speech_path).get("text"), limit=500)
        if not action and not speech:
            return {}
        args: dict[str, object] = {"colonist_id": colonist_id}
        if action:
            args["act"] = {
                "action": clean_room_text(action.get("action"), limit=64),
                "action_args": action.get("action_args")
                if isinstance(action.get("action_args"), dict)
                else {},
            }
        if speech:
            args["speak"] = speech
        return {
            "plugin_id": plugin,
            "action": "agent_turn",
            "revision": clean_room_text(
                turn.get("activity_plugin_revision"), limit=64
            ),
            "args": args,
        }

    def model_error_batch(self, turn_id: str, error_kind: object) -> dict[str, object]:
        turn = self._active_turn(turn_id)
        plugin = clean_room_text(turn.get("activity_plugin"), limit=64)
        colonist_id = clean_room_text(
            turn.get("activity_plugin_colonist_id"), limit=64
        )
        if not plugin or not colonist_id:
            return {}
        kind = clean_room_text(error_kind, limit=80) or "provider_error"
        return {
            "plugin_id": plugin,
            "action": "model_error",
            "revision": clean_room_text(
                turn.get("activity_plugin_revision"), limit=64
            ),
            "args": {
                "colonist_id": colonist_id,
                "message": f"Provider turn failed ({kind}).",
            },
        }

    def end_observation(self) -> None:
        self.action_path.unlink(missing_ok=True)
        self.speech_path.unlink(missing_ok=True)

    def _active_state(
        self,
        tool_suffix: str,
    ) -> tuple[dict[str, object], dict[str, object]]:
        turn = self._active_turn("")
        plugin = clean_room_text(turn.get("activity_plugin"), limit=64)
        if f"{plugin}.{tool_suffix}" not in self.active_tool_names(turn):
            raise ActivityPluginPortalError(
                f"Activity plugin tool {tool_suffix} is unavailable for this turn."
            )
        stored = self._read_json(self.state_path)
        payload = stored.get("payload")
        if (
            clean_room_text(stored.get("plugin_id"), limit=64) != plugin
            or not isinstance(payload, dict)
        ):
            raise ActivityPluginPortalError("Activity plugin state is unavailable.")
        return turn, payload

    def _active_turn(self, turn_id: str) -> dict[str, object]:
        turn_path = self.state_path.with_name("turn.json")
        turn = self._read_json(turn_path)
        active_id = clean_room_text(turn.get("turn_id"), limit=128)
        if not active_id or (turn_id and active_id != clean_room_text(turn_id, limit=128)):
            raise ActivityPluginPortalError("No matching room observation is active.")
        return turn

    @staticmethod
    def _read_json(path: Path) -> dict[str, object]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}


__all__ = ["ActivityPluginPortal", "ActivityPluginPortalError"]
