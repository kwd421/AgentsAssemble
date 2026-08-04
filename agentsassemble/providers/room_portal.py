"""Private room-view boundary used by autonomous provider sessions."""

from __future__ import annotations

import base64
import json
import os
import re
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
from agentsassemble.providers.room_portal_helper import (
    helper_interpreter,
    helper_script,
    windows_helper_wrapper,
)
from agentsassemble.providers.room_portal_collaboration import (
    RoomPortalCollaboration,
    RoomPortalError,
    RoomPublication,
)
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
from agentsassemble.room.tool_modes import (
    CHAT_TOOL_MODE,
    CORE_ROOM_TOOLS,
    room_tool_names,
    validate_room_tool_mode,
)


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
        "llm_gateway_api",
        "tokenrouter_api",
        "custom_openai_api",
        "ollama_api",
        "lmstudio_api",
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
  collaboration, and `speak` commands directly. `agentsassemble-room help`
  lists their syntax; do not try to locate or inspect the helper with `which`,
  `find`, or other discovery commands.
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
- Structured room votes are available through `create_vote`, `cast_vote`, and
  `vote_summary` when those names appear under Available room tools.
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
    tool_names: Iterable[str] = CORE_ROOM_TOOLS,
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
    available_tools = frozenset(tool_names)
    if "roll_dice" in available_tools and kind in {
        "codex_live_session",
        "cursor_live_session",
        "grok_live_session",
        "opencode_server",
        "cerebras_api",
        "deepseek_api",
        "openrouter_api",
        "vercel_ai_gateway",
        "llm_gateway_api",
        "tokenrouter_api",
        "custom_openai_api",
        "ollama_api",
        "lmstudio_api",
    }:
        random_note = """
- For official game randomness, use `roll_dice` with NdS±M notation or
  `choose_random`; do not invent a result yourself."""
    elif "roll_dice" in available_tools and kind in {"claude_code", "antigravity_live_session"}:
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
  Do not merely draft the intended public message as your final answer.
- Never invent or simulate a room tool. Only operations listed under Available
  room tools in the private room mirror are real for this turn.{floor_note}{random_note}{provider_note}"""


def automatic_turn_orientation() -> str:
    return """Current turn contract: automatic final
- The assigned room context is included below; do not use the private room
  read or publication boundary for this turn.
- Your ordinary final answer is published automatically as the room message."""


ROOM_SESSION_ORIENTATION = room_session_orientation()


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
        self.participant_index_path = self.root / "participants.json"
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
        self._tool_mode = CHAT_TOOL_MODE
        self._media: dict[str, dict[str, object]] = {}
        self._active_media_ids: tuple[str, ...] = ()
        self._active_messages: tuple[dict[str, object], ...] | None = None
        # Boundary between what this participant was already shown and what
        # arrived since. Without it every read re-presents the whole window as
        # current room state, and an agent that already answered restates
        # itself because nothing marks which line is new.
        self._seen_through_seq = 0
        self._new_since_seq = 0
        self._collaboration = RoomPortalCollaboration(
            participant_id=self.participant_id,
            turn_path=self.turn_path,
            outbox_path=self.outbox_path,
            activity_path=self.activity_path,
            participant_index_path=self.participant_index_path,
            lock=self._lock,
            write_json=self._write_json_atomic,
            record_activity=self._record_activity,
            require_tool=self._require_tool,
            messages=lambda: self._messages,
        )

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
                    helper_script(),
                    mode=0o600,
                )
                self._write_atomic(
                    self.helper_windows_path,
                    windows_helper_wrapper(),
                    mode=0o600,
                )
            else:
                self._write_atomic(self.helper_path, helper_script(), mode=0o700)
            self._write_media_index()
            self._write_participant_index()
            self._write_message_index()
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
            self._write_participant_index()
            self._bound_messages()
            self._write_message_index()
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
            # Freeze this turn's boundary. The seen cursor advances only after
            # observation_receipt verifies that the provider read this view.
            self._new_since_seq = self._seen_through_seq
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
                    "tool_mode": self._tool_mode,
                    "allowed_tools": sorted(room_tool_names(self._tool_mode)),
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
                with self._lock:
                    self._seen_through_seq = max(
                        self._seen_through_seq,
                        assigned_seq,
                    )
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
            return RoomPublication.from_payload(payload)

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

    def list_participants(self) -> list[dict[str, str]]:
        return self._collaboration.list_participants()

    def active_tool_names(self) -> frozenset[str]:
        """Return tools allowed for the active observation, failing closed."""

        with self._lock:
            try:
                turn = json.loads(self.turn_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return CORE_ROOM_TOOLS
        values = turn.get("allowed_tools") if isinstance(turn, dict) else None
        if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
            return CORE_ROOM_TOOLS
        try:
            tool_mode = validate_room_tool_mode(turn.get("tool_mode"))
        except ValueError:
            return CORE_ROOM_TOOLS
        allowed_for_mode = room_tool_names(tool_mode)
        return frozenset(value for value in values if value in allowed_for_mode)

    def tool_allowed(self, name: object) -> bool:
        return clean_room_text(name, limit=64) in self.active_tool_names()

    def acp_write_text(self, path: object, content: object) -> None:
        requested_target_id = direct_outbox_target(path)
        if str(path or "") != VIRTUAL_ROOM_OUTBOX_PATH and not requested_target_id:
            raise RoomPortalError("Only the shared room outbox may be written.")
        target_agent_id = self.resolve_handoff_target(requested_target_id)
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
            self._collaboration.stage_publication(
                turn_id,
                {
                    "content": text,
                    "target_agent_id": target_agent_id,
                    "message_kind": "message",
                },
            )
            self._record_activity("speak", turn_id=turn_id)

    def resolve_handoff_target(self, value: object) -> str:
        """Return an exact visible agent id, or no handoff for aliases/humans."""

        target_agent_id = clean_room_text(value, limit=128)
        if not target_agent_id or target_agent_id == self.participant_id:
            return ""
        with self._lock:
            known_agent_ids = set(self._participants)
            try:
                persisted = json.loads(
                    self.participant_index_path.read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError):
                persisted = {}
            values = persisted.get("agents") if isinstance(persisted, dict) else None
            if isinstance(values, list):
                known_agent_ids.update(
                    agent_id
                    for value in values
                    if (agent_id := clean_room_text(value, limit=128))
                )
            return target_agent_id if target_agent_id in known_agent_ids else ""

    def publish_message(self, content: object, *, next_agent_id: object = "") -> None:
        """Stage one public room message for the active observation."""

        target_agent_id = self.resolve_handoff_target(next_agent_id)
        path = (
            f"{VIRTUAL_ROOM_DIRECT_OUTBOX_PREFIX}{target_agent_id}.txt"
            if target_agent_id
            else VIRTUAL_ROOM_OUTBOX_PATH
        )
        self.acp_write_text(path, content)

    def decline_to_speak(self, reason_code: object) -> dict[str, object]:
        return self._collaboration.decline_to_speak(reason_code)

    def create_vote(self, question: object, options: list[object], *,
                    duration_seconds: object = 0) -> dict[str, object]:
        return self._collaboration.create_vote(
            question, options, duration_seconds=duration_seconds,
        )

    def cast_vote(self, vote_id: object, choice: object) -> dict[str, object]:
        return self._collaboration.cast_vote(vote_id, choice)

    def vote_summary(self, vote_id: object) -> dict[str, object]:
        return self._collaboration.vote_summary(vote_id)

    def observation_decline_reason(self, turn_id: str) -> str:
        return self._collaboration.decline_reason(turn_id)

    def roll_dice(self, notation: object, *, reason: object = "") -> dict[str, object]:
        """Record one validated, server-random dice result for publication."""

        self._require_tool("roll_dice")
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

        self._require_tool("choose_random")
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
        settings = value.get("room_settings")
        if isinstance(settings, dict) and "tool_mode" in settings:
            try:
                self._tool_mode = validate_room_tool_mode(settings["tool_mode"])
            except ValueError:
                self._tool_mode = CHAT_TOOL_MODE
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
            self._collaboration.remember_participant(
                identity,
                display_name=name or identity,
                participant_type=participant_type or (
                    "agent" if item in sessions else "human"
                ),
                role=role or "",
            )
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
            f"Available room tools: {', '.join(sorted(room_tool_names(self._tool_mode)))}",
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
        boundary = self._new_since_seq
        already_seen = sum(
            1
            for message in visible_messages
            if boundary and int(message.get("seq") or 0) <= boundary
        )
        fresh = [
            message
            for message in visible_messages
            if not boundary or int(message.get("seq") or 0) > boundary
        ]
        if already_seen:
            # No recap: the agent's own session already carries those turns
            # verbatim from the reads that first showed them. Re-presenting
            # settled conversation as current room state -- even condensed --
            # is what makes an agent answer settled points again. State the
            # fact and let its memory do the rest.
            lines.append(
                f"({already_seen} earlier message(s) already shown to you"
                " are not repeated here.)"
            )
            lines.append("")
            lines.append(
                "New since your last read"
                + (f" ({len(fresh)}):" if fresh else ": (nothing new)")
            )
        for message in fresh:
            name = self._speaker_label(message)
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

    def _speaker_label(self, message: dict[str, object]) -> str:
        name = str(
            message.get("display_name")
            or message.get("participant_id")
            or "participant"
        )
        if clean_room_text(message.get("participant_id"), limit=128) == self.participant_id:
            return f"{name} (you)"
        return name

    def _write_media_index(self) -> None:
        self._write_json_atomic(self.media_index_path, {"media": self._media})

    def _write_message_index(self) -> None:
        self._collaboration.write_message_index()

    def _write_participant_index(self) -> None:
        self._collaboration.write_participant_index(list(self._participants))

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

    def _require_tool(self, name: str) -> None:
        if not self.tool_allowed(name):
            raise RoomPortalError(
                f"Room tool {name} is unavailable in {self._tool_mode} mode."
            )


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
    "helper_interpreter",
    "room_wake_orientation",
    "room_session_orientation",
]
