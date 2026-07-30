"""Private room-view boundary used by autonomous provider sessions."""

from __future__ import annotations

import base64
import json
import os
import re
import sys
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from uuid import uuid4

from agentsassemble.providers.room_random import (
    choose_random as choose_random_result,
)
from agentsassemble.providers.room_random import roll_dice as roll_dice_result
from agentsassemble.providers.runtime_contracts import (
    AMBIENT_OBSERVATION,
    ORDERED_FLOOR,
    RoomObservationKind,
)
from agentsassemble.room.system_results import (
    RoomSystemResultError,
    validate_room_system_result,
)
from agentsassemble.room.text import clean_room_text


VIRTUAL_ROOM_VIEW_PATH = "/agentsassemble-room/current.md"
VIRTUAL_ROOM_OUTBOX_PATH = "/agentsassemble-room/outbox.txt"
VIRTUAL_ROOM_DIRECT_OUTBOX_PREFIX = "/agentsassemble-room/outbox-to/"
_ATTACHMENT_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
_DIRECT_OUTBOX_PATH = re.compile(
    rf"^{re.escape(VIRTUAL_ROOM_DIRECT_OUTBOX_PREFIX)}(?P<target>[A-Za-z0-9_.-]{{1,128}})\.txt$"
)
_MAX_ROOM_MESSAGE_CHARS = 12_000
_MAX_OBSERVATION_RESULTS = 32
_MAX_OBSERVATION_RESULT_BYTES = 64 * 1024
_MAX_OBSERVATION_RESULT_LINE_BYTES = 48 * 1024


def _room_interfaces(provider_kind: object = "") -> tuple[str, str, str]:
    kind = clean_room_text(provider_kind, limit=64)
    provider_note = ""
    if kind in {
        "codex_live_session",
        "cursor_live_session",
        "grok_live_session",
        "opencode_server",
        "cerebras_api",
        "deepseek_api",
        "openrouter_api",
        "vercel_ai_gateway",
    }:
        read_interface = "the `read_discussion` MCP tool"
        speak_interface = (
            "the `publish_message` MCP tool with `content` and, when deliberately "
            "handing the floor to one participant, `next_agent_id`"
        )
    elif kind in {"claude_code", "antigravity_live_session"}:
        read_interface = "terminal command `agentsassemble-room read`"
        speak_interface = (
            'terminal command `agentsassemble-room speak "<message>"`, or '
            '`agentsassemble-room speak-to <agent-id> "<message>"` when deliberately '
            "handing the floor to one participant"
        )
        provider_note = """
- `agentsassemble-room` is already on `PATH`. Run the documented `read`,
  `roll`, and `speak` commands directly; do not try to locate or inspect the
  helper with `which`, `find`, or other discovery commands.
- The terminal command is shell-parsed. Wrap the whole public message in one
  pair of ASCII double quotes. Inside the message, use Unicode quotation marks
  such as `「」` and Unicode arrows such as `→`; do not use ASCII `"`, `$`, or
  backticks. This keeps ordinary room prose inside the one approved command."""
    else:
        read_interface = (
            "the provider's private room read interface: Codex `read_discussion` MCP, "
            "terminal `agentsassemble-room read`, or ACP `/agentsassemble-room/current.md`"
        )
        speak_interface = (
            "the matching private room speak interface: Codex `publish_message` MCP, "
            'terminal `agentsassemble-room speak "<message>"` / `speak-to`, or ACP '
            "`/agentsassemble-room/outbox.txt` / `outbox-to/<agent-id>.txt`"
        )
    return read_interface, speak_interface, provider_note


def room_session_orientation(provider_kind: object = "") -> str:
    del provider_kind
    return f"""Shared room session:
- You are an ongoing participant in a shared AgentsAssemble room.
- Room vote cards are visible in finalized messages. Agent sessions do not have
  structured ballot buttons yet; when asked to vote, publish the chosen option
  clearly and do not claim it was counted by the structured tally.
- Public room messages follow the language of the latest human or host message,
  unless that message explicitly asks for another language.
- The private room mirror shows your canonical room role. In ordered mode, a
  `director` is the 진행 participant: ordinary agent replies return to it, and
  it can deliberately hand the floor to the next participant with the targeted
  publication form documented for its provider.
- Room norm: public messages add new substance. Resolving an open decision is new
  substance; after a point is settled, receipt, thanks, repeated agreement,
  restatement, a silence explanation, or another closing is not."""


def room_wake_orientation(
    provider_kind: object = "",
    *,
    observation_kind: RoomObservationKind,
) -> str:
    read_interface, speak_interface, provider_note = _room_interfaces(provider_kind)
    kind = clean_room_text(provider_kind, limit=64)
    if observation_kind == ORDERED_FLOOR:
        floor_note = """
- Queue provenance: ordered selection. This session was the single provider
  selected for this event when it was queued."""
    elif observation_kind == AMBIENT_OBSERVATION:
        floor_note = """
- Queue provenance: shared ambient observation. Other eligible sessions may
  receive the same triggering event."""
    else:
        raise ValueError("Unsupported room observation kind.")
    random_note = ""
    if kind in {
        "codex_live_session",
        "cursor_live_session",
        "grok_live_session",
        "opencode_server",
        "cerebras_api",
        "deepseek_api",
        "openrouter_api",
        "vercel_ai_gateway",
    }:
        random_note = """
- For official game randomness, use `roll_dice` with NdS±M notation or
  `choose_random`; do not invent a result yourself."""
    elif kind in {"claude_code", "antigravity_live_session"}:
        random_note = """
- For official game dice, run exactly one terminal command per roll:
  `agentsassemble-room roll '<NdS±M>'`. If another roll is needed, wait for the
  first result and use a separate tool call. Shell chaining such as `&&`, `;`,
  or `|` is rejected."""
    return f"""Current turn contract: room wake
- `room.wake <turn-id>` is only a content-free signal that assigned, finalized
  room activity is available.
- Read the private room mirror through {read_interface}.
- If you should speak, only {speak_interface} creates a public room message.
- Ordinary assistant output is private on this turn and is never published.
  Do not merely draft the intended public message as your final answer.{floor_note}{random_note}{provider_note}"""


def automatic_turn_orientation() -> str:
    return """Current turn contract: automatic final
- The assigned room context is included below; do not use the private room
  read or publication boundary for this turn.
- Your ordinary final answer is published automatically as the room message."""


ROOM_SESSION_ORIENTATION = room_session_orientation()


class RoomPortalError(RuntimeError):
    pass


@dataclass(frozen=True)
class RoomPublication:
    content: str = ""
    target_agent_id: str = ""


@dataclass(frozen=True)
class RoomObservationResultBatch:
    results: tuple[dict[str, object], ...] = ()
    malformed_count: int = 0
    capped_count: int = 0
    bytes_truncated: bool = False

    @property
    def diagnostic_count(self) -> int:
        return (
            self.malformed_count
            + self.capped_count
            + int(self.bytes_truncated)
        )


class RoomPortal:
    """Maintain a bounded, private room mirror and one-turn publication outbox."""

    def __init__(
        self,
        root: str | Path,
        *,
        participant_id: str,
        max_messages: int = 50,
        max_chars: int = 32_768,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self.participant_id = clean_room_text(participant_id, limit=128)
        self.max_messages = max(1, int(max_messages))
        self.max_chars = max(1024, int(max_chars))
        self.view_path = self.root / "current.md"
        self.turn_path = self.root / "turn.json"
        self.outbox_path = self.root / "outbox.json"
        self.activity_path = self.root / "activity.jsonl"
        self.media_root = self.root / "media"
        self.media_index_path = self.root / "media.json"
        self.bin_dir = self.root / "bin"
        self.helper_path = self.bin_dir / "agentsassemble-room"
        self.helper_python_path = self.bin_dir / "agentsassemble_room_helper.py"
        self.helper_windows_path = self.bin_dir / "agentsassemble-room.cmd"
        self._lock = threading.RLock()
        self._messages: list[dict[str, object]] = []
        self._message_ids: set[str] = set()
        self._display_name = self.participant_id
        self._participants: dict[str, str] = {}
        self._participant_roles: dict[str, str] = {}
        self._media: dict[str, dict[str, object]] = {}
        self._active_media_ids: tuple[str, ...] = ()
        self._active_messages: tuple[dict[str, object], ...] | None = None

    def prepare(self) -> None:
        with self._lock:
            self.bin_dir.mkdir(parents=True, exist_ok=True)
            self.media_root.mkdir(parents=True, exist_ok=True)
            self._chmod(self.root, 0o700)
            self._chmod(self.bin_dir, 0o700)
            self._chmod(self.media_root, 0o700)
            if os.name == "nt":
                self._write_atomic(
                    self.helper_python_path,
                    _HELPER_SCRIPT,
                    mode=0o600,
                )
                self._write_atomic(
                    self.helper_windows_path,
                    _windows_helper_wrapper(),
                    mode=0o600,
                )
            else:
                self._write_atomic(self.helper_path, _HELPER_SCRIPT, mode=0o700)
            self._write_media_index()
            self._write_view()

    def provider_environment(self, source_path: str = "") -> dict[str, str]:
        path = os.pathsep.join(part for part in (str(self.bin_dir), source_path) if part)
        return {"PATH": path}

    def ingest_frame(self, frame: dict[str, object]) -> list[dict[str, object]]:
        attachments: list[dict[str, object]] = []
        with self._lock:
            self._ingest_identity(frame)
            for event in _frame_events(frame):
                self._ingest_identity(event)
                if clean_room_text(event.get("type"), limit=64) != "message_final":
                    continue
                event_id = clean_room_text(event.get("id"), limit=128)
                if not event_id or event_id in self._message_ids:
                    continue
                projected = _project_message(event)
                self._messages.append(projected)
                self._message_ids.add(event_id)
                attachments.extend(
                    item
                    for item in projected.get("attachments", [])
                    if isinstance(item, dict)
                )
            self._bound_messages()
            self._write_view()
        return attachments

    def begin_observation(
        self,
        turn_id: str,
        *,
        attachment_ids: Iterable[object] = (),
        input_up_to_seq: object = None,
    ) -> None:
        clean_turn_id = clean_room_text(turn_id, limit=128)
        if not clean_turn_id:
            raise RoomPortalError("Room observation requires a turn id.")
        active_media_ids = tuple(
            dict.fromkeys(
                attachment_id
                for value in attachment_ids
                if _ATTACHMENT_ID.fullmatch(
                    attachment_id := clean_room_text(value, limit=64)
                )
            )
        )
        with self._lock:
            if input_up_to_seq is None:
                assigned_seq = max(
                    (int(item.get("seq") or 0) for item in self._messages),
                    default=0,
                )
            else:
                assigned_seq = max(0, int(input_up_to_seq))
            self.outbox_path.unlink(missing_ok=True)
            self._active_media_ids = active_media_ids
            self._active_messages = tuple(
                message
                for message in self._messages
                if int(message.get("seq") or 0) <= assigned_seq
            )
            self._write_json_atomic(
                self.turn_path,
                {
                    "turn_id": clean_turn_id,
                    "input_up_to_seq": assigned_seq,
                    "activity_offset": (
                        self.activity_path.stat().st_size
                        if self.activity_path.exists()
                        else 0
                    ),
                },
            )
            self._write_view()

    def observation_receipt(self, turn_id: str) -> int | None:
        clean_turn_id = clean_room_text(turn_id, limit=128)
        with self._lock:
            try:
                turn = json.loads(self.turn_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return None
            if (
                not isinstance(turn, dict)
                or clean_room_text(turn.get("turn_id"), limit=128) != clean_turn_id
            ):
                return None
            assigned_seq = max(0, _safe_int(turn.get("input_up_to_seq"), 0))
            offset = max(0, _safe_int(turn.get("activity_offset"), 0))
            try:
                with self.activity_path.open("r", encoding="utf-8") as stream:
                    stream.seek(offset)
                    records = stream.readlines()
            except OSError:
                return None
        for line in records:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (
                isinstance(record, dict)
                and record.get("operation") == "read"
                and clean_room_text(record.get("turn_id"), limit=128)
                == clean_turn_id
            ):
                return assigned_seq
        return None

    def observation_results(self, turn_id: str) -> list[dict[str, object]]:
        """Return bounded official random-tool results recorded by this observation."""
        return list(self.observation_result_batch(turn_id).results)

    def observation_result_batch(self, turn_id: str) -> RoomObservationResultBatch:
        """Return valid results plus bounded diagnostics for discarded activity."""
        clean_turn_id = clean_room_text(turn_id, limit=128)
        if not clean_turn_id:
            return RoomObservationResultBatch()
        with self._lock:
            try:
                turn = json.loads(self.turn_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return RoomObservationResultBatch()
            if (
                not isinstance(turn, dict)
                or clean_room_text(turn.get("turn_id"), limit=128)
                != clean_turn_id
            ):
                return RoomObservationResultBatch()
            offset = turn.get("activity_offset")
            if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
                return RoomObservationResultBatch()
            try:
                with self.activity_path.open("rb") as stream:
                    stream.seek(0, os.SEEK_END)
                    if offset > stream.tell():
                        return RoomObservationResultBatch()
                    stream.seek(offset)
                    content = stream.read(_MAX_OBSERVATION_RESULT_BYTES + 1)
            except OSError:
                return RoomObservationResultBatch()
        bytes_truncated = len(content) > _MAX_OBSERVATION_RESULT_BYTES
        if bytes_truncated:
            content = content[:_MAX_OBSERVATION_RESULT_BYTES]
            content = content.rsplit(b"\n", 1)[0]
        results: list[dict[str, object]] = []
        malformed_count = 0
        capped_count = 0
        for line in content.splitlines():
            if not line:
                continue
            if len(line) > _MAX_OBSERVATION_RESULT_LINE_BYTES:
                malformed_count += 1
                continue
            try:
                record = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError):
                malformed_count += 1
                continue
            result, malformed = _bounded_observation_result(
                record,
                turn_id=clean_turn_id,
            )
            malformed_count += int(malformed)
            if result is None:
                continue
            if len(results) < _MAX_OBSERVATION_RESULTS:
                results.append(result)
            else:
                capped_count += 1
        return RoomObservationResultBatch(
            results=tuple(results),
            malformed_count=malformed_count,
            capped_count=capped_count,
            bytes_truncated=bytes_truncated,
        )

    def consume_publication(self, turn_id: str) -> str:
        return self.consume_publication_result(turn_id).content

    def consume_publication_result(self, turn_id: str) -> RoomPublication:
        clean_turn_id = clean_room_text(turn_id, limit=128)
        with self._lock:
            try:
                payload = json.loads(self.outbox_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return RoomPublication()
            finally:
                self.outbox_path.unlink(missing_ok=True)
                self.turn_path.unlink(missing_ok=True)
                self._active_media_ids = ()
                self._active_messages = None
                self._write_view()
            if not isinstance(payload, dict):
                return RoomPublication()
            if clean_room_text(payload.get("turn_id"), limit=128) != clean_turn_id:
                return RoomPublication()
            return RoomPublication(
                content=_publication_text(payload.get("content")),
                target_agent_id=clean_room_text(
                    payload.get("target_agent_id"),
                    limit=128,
                ),
            )

    def end_observation(self, turn_id: str) -> None:
        clean_turn_id = clean_room_text(turn_id, limit=128)
        with self._lock:
            try:
                turn = json.loads(self.turn_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                turn = {}
            active_turn_id = clean_room_text(
                turn.get("turn_id") if isinstance(turn, dict) else "",
                limit=128,
            )
            if active_turn_id and active_turn_id != clean_turn_id:
                return
            self.turn_path.unlink(missing_ok=True)
            self.outbox_path.unlink(missing_ok=True)
            self._active_media_ids = ()
            self._active_messages = None
            self._write_view()

    def acp_read_text(self, path: object, *, line: object = None, limit: object = None) -> str:
        if str(path or "") != VIRTUAL_ROOM_VIEW_PATH:
            raise RoomPortalError("Only the shared room view may be read.")
        with self._lock:
            text = self.view_path.read_text(encoding="utf-8")
            self._record_activity("read")
        lines = text.splitlines(keepends=True)
        start = max(0, _safe_int(line, 1) - 1)
        count = max(0, _safe_int(limit, len(lines)))
        return "".join(lines[start : start + count])

    def read_discussion(self) -> str:
        """Read the current bounded room view and record the observation receipt."""

        return self.acp_read_text(VIRTUAL_ROOM_VIEW_PATH)

    def acp_write_text(self, path: object, content: object) -> None:
        target_agent_id = direct_outbox_target(path)
        if str(path or "") != VIRTUAL_ROOM_OUTBOX_PATH and not target_agent_id:
            raise RoomPortalError("Only the shared room outbox may be written.")
        text = _publication_text(content)
        if not text:
            raise RoomPortalError("A room publication cannot be empty.")
        with self._lock:
            try:
                turn = json.loads(self.turn_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise RoomPortalError("No room observation is active.") from error
            turn_id = clean_room_text(
                turn.get("turn_id") if isinstance(turn, dict) else "",
                limit=128,
            )
            if not turn_id:
                raise RoomPortalError("No room observation is active.")
            self._write_json_atomic(
                self.outbox_path,
                {
                    "turn_id": turn_id,
                    "content": text,
                    "target_agent_id": target_agent_id,
                },
            )
            self._record_activity("speak", turn_id=turn_id)

    def publish_message(self, content: object, *, next_agent_id: object = "") -> None:
        """Stage one public room message for the active observation."""

        target_agent_id = clean_room_text(next_agent_id, limit=128)
        path = (
            f"{VIRTUAL_ROOM_DIRECT_OUTBOX_PREFIX}{target_agent_id}.txt"
            if target_agent_id
            else VIRTUAL_ROOM_OUTBOX_PATH
        )
        self.acp_write_text(path, content)

    def roll_dice(self, notation: object, *, reason: object = "") -> dict[str, object]:
        """Record one validated, server-random dice result for publication."""

        result = roll_dice_result(notation)
        self._record_activity(
            "roll_dice",
            details={
                **result,
                "reason": clean_room_text(reason, limit=200),
            },
        )
        return result

    def choose_random(
        self,
        options: list[object],
        *,
        reason: object = "",
    ) -> dict[str, object]:
        """Record one validated, server-random choice result for publication."""

        result = choose_random_result(options)
        self._record_activity(
            "choose_random",
            details={
                **result,
                "reason": clean_room_text(reason, limit=200),
            },
        )
        return result

    def stage_attachment(
        self,
        metadata: dict[str, object],
        content: bytes,
    ) -> Path:
        attachment_id = clean_room_text(metadata.get("id"), limit=64)
        if not _ATTACHMENT_ID.fullmatch(attachment_id):
            raise RoomPortalError("Attachment id is invalid.")
        filename = _safe_filename(metadata.get("filename"))
        directory = (self.media_root / attachment_id).resolve()
        directory.mkdir(parents=True, exist_ok=True)
        self._chmod(directory, 0o700)
        target = (directory / filename).resolve()
        try:
            target.relative_to(directory)
        except ValueError as error:
            raise RoomPortalError("Attachment path is invalid.") from error
        with self._lock:
            self._write_atomic(target, bytes(content), mode=0o600)
            self._media[attachment_id] = {
                "id": attachment_id,
                "filename": filename,
                "content_type": clean_room_text(metadata.get("content_type"), limit=128),
                "size": max(0, int(metadata.get("size") or len(content))),
                "path": str(target),
            }
            self._write_media_index()
            self._write_view()
        return target

    def mark_attachment_unavailable(
        self,
        metadata: dict[str, object],
        error: object,
    ) -> None:
        attachment_id = clean_room_text(metadata.get("id"), limit=64)
        if not _ATTACHMENT_ID.fullmatch(attachment_id):
            return
        with self._lock:
            self._media[attachment_id] = {
                "id": attachment_id,
                "filename": _safe_filename(metadata.get("filename")),
                "content_type": clean_room_text(metadata.get("content_type"), limit=128),
                "size": max(0, int(metadata.get("size") or 0)),
                "path": "",
                "error": clean_room_text(error, limit=500) or "media unavailable",
            }
            self._write_media_index()
            self._write_view()

    def native_media_blocks(self) -> list[dict[str, str]]:
        blocks: list[dict[str, str]] = []
        with self._lock:
            media = [
                self._media[attachment_id]
                for attachment_id in self._active_media_ids
                if attachment_id in self._media
            ]
        for item in media:
            content_type = str(item.get("content_type") or "")
            if content_type not in {"image/png", "image/jpeg", "image/gif", "image/webp"}:
                continue
            try:
                content = Path(str(item["path"])).read_bytes()
            except OSError:
                continue
            blocks.append(
                {
                    "type": "image",
                    "mimeType": content_type,
                    "data": base64.b64encode(content).decode("ascii"),
                }
            )
        return blocks

    def _ingest_identity(self, value: dict[str, object]) -> None:
        participants = (
            value.get("participants")
            if isinstance(value.get("participants"), list)
            else []
        )
        if clean_room_text(value.get("type"), limit=64) == "participant_updated":
            participants = [*participants, value]
        sessions = (
            value.get("agent_sessions")
            if isinstance(value.get("agent_sessions"), list)
            else []
        )
        session_event = value.get("agent_session")
        if isinstance(session_event, dict):
            sessions = [*sessions, session_event]
        for item in [*participants, *sessions]:
            if not isinstance(item, dict):
                continue
            identity = clean_room_text(
                item.get("participant_id") or item.get("session_id"),
                limit=128,
            )
            if not identity:
                continue
            name = clean_room_text(item.get("display_name"), limit=80)
            participant_type = clean_room_text(
                item.get("participant_type"),
                limit=32,
            )
            role = clean_room_text(item.get("role"), limit=32)
            if role:
                self._participant_roles[identity] = role
            if participant_type == "agent" or item in sessions:
                self._participants[identity] = name or identity
            if identity == self.participant_id and name:
                self._display_name = name

    def _bound_messages(self) -> None:
        self._messages.sort(key=lambda item: int(item.get("seq") or 0))
        if len(self._messages) > self.max_messages:
            self._messages = self._messages[-self.max_messages :]
        while (
            len(self._messages) > 1
            and len(self._render_view_unbounded(self._messages)) > self.max_chars
        ):
            self._messages.pop(0)
        self._message_ids = {
            clean_room_text(item.get("id"), limit=128)
            for item in self._messages
            if clean_room_text(item.get("id"), limit=128)
        }

    def _write_view(self) -> None:
        messages = (
            self._active_messages
            if self._active_messages is not None
            else tuple(self._messages)
        )
        text = self._render_view_unbounded(messages)
        if len(text) > self.max_chars:
            text = text[: self.max_chars].rstrip() + "\n"
        self._write_atomic(self.view_path, text, mode=0o600)

    def _render_view_unbounded(
        self,
        messages: Iterable[dict[str, object]],
    ) -> str:
        lines = [
            "# Shared room",
            f"Your display name: {self._display_name or self.participant_id}",
            f"Your room role: {self._participant_roles.get(self.participant_id, 'agent')}",
            "",
        ]
        peers = [
            (agent_id, display_name)
            for agent_id, display_name in self._participants.items()
            if agent_id != self.participant_id
        ]
        if peers:
            lines.append("## Agent handles")
            lines.extend(
                f"- `{agent_id}` — {display_name}"
                for agent_id, display_name in sorted(
                    peers,
                    key=lambda item: (item[1].casefold(), item[0]),
                )
            )
            lines.append("")
        visible_messages = tuple(messages)
        latest_human_index = -1
        latest_self_index = -1
        for index, message in enumerate(visible_messages):
            participant_type = clean_room_text(
                message.get("participant_type"),
                limit=32,
            )
            participant_id = clean_room_text(
                message.get("participant_id"),
                limit=128,
            )
            if participant_type == "human":
                latest_human_index = index
            if participant_id == self.participant_id:
                latest_self_index = index
        if latest_human_index >= 0:
            self_messages_since_human = sum(
                1
                for message in visible_messages[latest_human_index + 1 :]
                if clean_room_text(message.get("participant_id"), limit=128)
                == self.participant_id
            )
            lines.extend(
                [
                    "## Conversation position",
                    (
                        "- Your public messages since the latest human message: "
                        f"{self_messages_since_human}"
                    ),
                ]
            )
            if latest_self_index >= 0:
                lines.append(
                    "- Finalized messages since your latest public message: "
                    f"{len(visible_messages) - latest_self_index - 1}"
                )
            lines.append("")
        lines.append("## Finalized messages")
        if not visible_messages:
            lines.append("(No finalized messages.)")
        for message in visible_messages:
            name = str(message.get("display_name") or message.get("participant_id") or "participant")
            if clean_room_text(message.get("participant_id"), limit=128) == self.participant_id:
                name = f"{name} (you)"
            content = _visible_message_content(message)
            lines.extend([f"## {name}", content or "(media or structured message)"])
            for attachment in message.get("attachments", []):
                if not isinstance(attachment, dict):
                    continue
                attachment_id = str(attachment.get("id") or "")
                staged = self._media.get(attachment_id)
                if staged and staged.get("path"):
                    suffix = f"available via `agentsassemble-room media {attachment_id}`"
                elif staged and staged.get("error"):
                    suffix = f"unavailable: {staged['error']}"
                else:
                    suffix = "not staged"
                lines.append(
                    f"- Media `{attachment_id}`: {attachment.get('filename') or 'attachment'} "
                    f"({attachment.get('content_type') or 'application/octet-stream'}; {suffix})"
                )
            lines.append("")
        return "\n".join(lines).strip() + "\n"

    def _write_media_index(self) -> None:
        self._write_json_atomic(self.media_index_path, {"media": self._media})

    def _write_json_atomic(self, path: Path, value: dict[str, object]) -> None:
        self._write_atomic(
            path,
            json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n",
            mode=0o600,
        )

    @staticmethod
    def _write_atomic(path: Path, content: str | bytes, *, mode: int) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        if isinstance(content, bytes):
            temporary.write_bytes(content)
        else:
            temporary.write_text(content, encoding="utf-8")
        RoomPortal._chmod(temporary, mode)
        os.replace(temporary, path)

    @staticmethod
    def _chmod(path: Path, mode: int) -> None:
        try:
            path.chmod(mode)
        except OSError:
            pass

    def _record_activity(
        self,
        operation: str,
        *,
        turn_id: str = "",
        details: dict[str, object] | None = None,
    ) -> None:
        input_up_to_seq = 0
        if not turn_id:
            try:
                turn = json.loads(self.turn_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                turn = {}
            turn_id = clean_room_text(
                turn.get("turn_id") if isinstance(turn, dict) else "",
                limit=128,
            )
            input_up_to_seq = (
                max(0, _safe_int(turn.get("input_up_to_seq"), 0))
                if isinstance(turn, dict)
                else 0
            )
        payload = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "operation": clean_room_text(operation, limit=32),
            "turn_id": turn_id,
            "observed_through_seq": input_up_to_seq if operation == "read" else 0,
        }
        if operation in {"roll_dice", "choose_random"}:
            payload["result_id"] = f"result-{uuid4().hex}"
        if details:
            payload["details"] = details
        with self.activity_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        self._chmod(self.activity_path, 0o600)


def _frame_events(frame: dict[str, object]) -> Iterable[dict[str, object]]:
    if clean_room_text(frame.get("stream"), limit=32) != "room_events":
        return ()
    events = frame.get("events") if isinstance(frame.get("events"), list) else []
    return tuple(event for event in events if isinstance(event, dict))


def _project_message(event: dict[str, object]) -> dict[str, object]:
    attachments = event.get("attachments") if isinstance(event.get("attachments"), list) else []
    return {
        "id": clean_room_text(event.get("id"), limit=128),
        "seq": max(0, int(event.get("seq") or 0)),
        "created_at": clean_room_text(event.get("created_at"), limit=128),
        "participant_id": clean_room_text(
            event.get("participant_id") or event.get("actor_id"),
            limit=128,
        ),
        "participant_type": clean_room_text(
            event.get("participant_type") or event.get("actor_type"),
            limit=32,
        ),
        "display_name": clean_room_text(event.get("display_name"), limit=80),
        "content": str(event.get("content") or "")[:_MAX_ROOM_MESSAGE_CHARS],
        "message_kind": clean_room_text(event.get("message_kind"), limit=64),
        "vote_id": clean_room_text(event.get("vote_id") or event.get("id"), limit=128),
        "vote_question": clean_room_text(event.get("vote_question"), limit=500),
        "vote_options": [
            clean_room_text(option, limit=200)
            for option in (
                event.get("vote_options")
                if isinstance(event.get("vote_options"), list)
                else []
            )[:20]
            if clean_room_text(option, limit=200)
        ],
        "vote_duration_seconds": max(
            0,
            _safe_int(event.get("vote_duration_seconds"), 0),
        ),
        "vote_deadline_at": clean_room_text(
            event.get("vote_deadline_at"),
            limit=128,
        ),
        "vote_choice": clean_room_text(event.get("vote_choice"), limit=200),
        "attachments": [
            {
                "id": clean_room_text(item.get("id"), limit=64),
                "filename": _safe_filename(item.get("filename")),
                "content_type": clean_room_text(item.get("content_type"), limit=128),
                "size": max(0, int(item.get("size") or 0)),
            }
            for item in attachments
            if isinstance(item, dict) and _ATTACHMENT_ID.fullmatch(clean_room_text(item.get("id"), limit=64))
        ],
    }


def _visible_message_content(message: dict[str, object]) -> str:
    content = str(message.get("content") or "")
    kind = clean_room_text(message.get("message_kind"), limit=64)
    if kind == "vote":
        question = clean_room_text(message.get("vote_question"), limit=500)
        options = (
            message.get("vote_options")
            if isinstance(message.get("vote_options"), list)
            else []
        )
        lines = [
            f"[Vote {clean_room_text(message.get('vote_id'), limit=128)}]",
            question or "(No question provided.)",
        ]
        deadline_at = clean_room_text(message.get("vote_deadline_at"), limit=128)
        if deadline_at:
            lines.append(f"Closes at: {deadline_at}")
        lines.extend(
            f"{index}. {clean_room_text(option, limit=200)}"
            for index, option in enumerate(options, start=1)
            if clean_room_text(option, limit=200)
        )
        return "\n".join(lines)
    if kind == "vote_cast":
        return (
            f"[Vote {clean_room_text(message.get('vote_id'), limit=128)} ballot] "
            f"{clean_room_text(message.get('vote_choice'), limit=200) or '(no choice)'}"
        )
    return content


def _publication_text(value: object) -> str:
    return (
        str(value or "")
        .replace("\x00", "")
        .replace("\r\n", "\n")
        .replace("\r", "\n")[:_MAX_ROOM_MESSAGE_CHARS]
        .strip()
    )


def _bounded_observation_result(
    record: object,
    *,
    turn_id: str,
) -> tuple[dict[str, object] | None, bool]:
    if not isinstance(record, dict):
        return None, True
    if clean_room_text(record.get("turn_id"), limit=128) != turn_id:
        return None, False
    operation = clean_room_text(record.get("operation"), limit=32)
    is_result_record = (
        "result_id" in record
        or operation in {"roll_dice", "choose_random"}
    )
    if not is_result_record:
        return None, False
    try:
        validated = validate_room_system_result(
            result_id=record.get("result_id"),
            operation=operation,
            details=record.get("details"),
        )
    except RoomSystemResultError:
        return None, True
    return (
        {
            "result_id": validated.result_id,
            "operation": validated.operation,
            "details": validated.details,
        },
        False,
    )


def direct_outbox_target(path: object) -> str:
    match = _DIRECT_OUTBOX_PATH.fullmatch(str(path or ""))
    return match.group("target") if match else ""


def _safe_filename(value: object) -> str:
    name = Path(str(value or "attachment.bin").replace("\\", "/")).name
    name = "".join(
        character
        for character in name
        if character >= " " and character not in {"/", "\\", "\x7f"}
    ).strip()
    return name[:120] if name not in {"", ".", ".."} else "attachment.bin"


def _safe_int(value: object, default: int) -> int:
    try:
        return int(value) if value not in (None, "") else int(default)
    except (TypeError, ValueError):
        return int(default)


# This file is copied into provider-private state and must run without importing
# the AgentsAssemble package. The bridge and room authority independently
# revalidate its activity records through validate_room_system_result.
_HELPER_SCRIPT = r"""#!/usr/bin/env python3
import json
import os
import re
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VIEW = ROOT / "current.md"
TURN = ROOT / "turn.json"
OUTBOX = ROOT / "outbox.json"
MEDIA = ROOT / "media.json"
ACTIVITY = ROOT / "activity.jsonl"

def fail(message):
    print(message, file=sys.stderr)
    raise SystemExit(2)

def atomic_json(path, payload):
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
    try:
        temporary.chmod(0o600)
    except OSError:
        pass
    os.replace(temporary, path)

def audit(operation, turn_id="", details=None):
    observed_through_seq = 0
    if not turn_id:
        try:
            turn = json.loads(TURN.read_text(encoding="utf-8"))
            turn_id = str(turn.get("turn_id") or "")
            observed_through_seq = int(turn.get("input_up_to_seq") or 0)
        except (OSError, json.JSONDecodeError):
            turn_id = ""
    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "operation": operation,
        "turn_id": turn_id,
        "observed_through_seq": observed_through_seq if operation == "read" else 0,
    }
    if operation in {"roll_dice", "choose_random"}:
        payload["result_id"] = f"result-{secrets.token_hex(16)}"
    if details:
        payload["details"] = details
    with ACTIVITY.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    try:
        ACTIVITY.chmod(0o600)
    except OSError:
        pass

command = sys.argv[1] if len(sys.argv) > 1 else "help"
if command == "read":
    content = VIEW.read_text(encoding="utf-8")
    audit("read")
    sys.stdout.write(content)
elif command in {"speak", "speak-to"}:
    target_agent_id = ""
    content_start = 2
    if command == "speak-to":
        if len(sys.argv) < 4 or re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", sys.argv[2]) is None:
            fail("usage: agentsassemble-room speak-to <agent-id> '<message>'")
        target_agent_id = sys.argv[2]
        content_start = 3
    content = " ".join(sys.argv[content_start:]).strip() if len(sys.argv) > content_start else sys.stdin.read().strip()
    if not content:
        fail("room message is empty")
    turn = json.loads(TURN.read_text(encoding="utf-8"))
    turn_id = str(turn.get("turn_id") or "")
    if not turn_id:
        fail("no room observation is active")
    atomic_json(
        OUTBOX,
        {
            "turn_id": turn_id,
            "content": content[:12000],
            "target_agent_id": target_agent_id,
        },
    )
    audit("speak", turn_id, {"target_agent_id": target_agent_id} if target_agent_id else None)
elif command == "media":
    attachment_id = sys.argv[2] if len(sys.argv) > 2 else ""
    index = json.loads(MEDIA.read_text(encoding="utf-8")).get("media", {})
    item = index.get(attachment_id)
    if not isinstance(item, dict) or not item.get("path"):
        fail("media is unavailable")
    audit("media")
    print(item["path"])
elif command == "roll":
    if len(sys.argv) != 3:
        fail("usage: agentsassemble-room roll '<NdS+M>'")
    match = re.fullmatch(r"\s*(\d{0,3})d(\d{1,4})([+-]\d{1,5})?\s*", sys.argv[2], re.IGNORECASE)
    if match is None:
        fail("dice notation must look like d20, 2d6, or 1d20+3")
    count = int(match.group(1) or 1)
    sides = int(match.group(2))
    modifier = int(match.group(3) or 0)
    if not 1 <= count <= 100 or not 2 <= sides <= 1000 or not -100000 <= modifier <= 100000:
        fail("dice notation is out of range")
    rolls = [secrets.randbelow(sides) + 1 for _ in range(count)]
    notation = f"{count}d{sides}" + (f"{modifier:+d}" if modifier else "")
    result = {
        "notation": notation,
        "rolls": rolls,
        "modifier": modifier,
        "total": sum(rolls) + modifier,
    }
    audit("roll_dice", details=result)
    print(json.dumps(result, ensure_ascii=False))
elif command == "help":
    print("agentsassemble-room read | speak [text] | speak-to <agent-id> [text] | media <id> | roll '<NdS+M>'")
else:
    fail("unknown command")
"""


def _windows_helper_wrapper() -> str:
    executable = str(Path(sys.executable).resolve()).replace("%", "%%")
    return (
        "@echo off\r\n"
        f'"{executable}" "%~dp0\\agentsassemble_room_helper.py" %*\r\n'
    )


__all__ = [
    "ROOM_SESSION_ORIENTATION",
    "RoomObservationResultBatch",
    "RoomPortal",
    "RoomPortalError",
    "RoomPublication",
    "VIRTUAL_ROOM_DIRECT_OUTBOX_PREFIX",
    "VIRTUAL_ROOM_OUTBOX_PATH",
    "VIRTUAL_ROOM_VIEW_PATH",
    "automatic_turn_orientation",
    "direct_outbox_target",
    "room_wake_orientation",
    "room_session_orientation",
]
