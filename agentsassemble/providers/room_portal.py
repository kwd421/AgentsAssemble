"""Private room-view boundary used by autonomous provider sessions."""

from __future__ import annotations

import base64
import json
import os
import re
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from agentsassemble.room.text import clean_room_text


VIRTUAL_ROOM_VIEW_PATH = "/agentsassemble-room/current.md"
VIRTUAL_ROOM_OUTBOX_PATH = "/agentsassemble-room/outbox.txt"
_ATTACHMENT_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
_MAX_ROOM_MESSAGE_CHARS = 12_000


def room_session_orientation(provider_kind: object = "") -> str:
    kind = clean_room_text(provider_kind, limit=64)
    if kind == "codex_live_session":
        read_interface = "Codex dynamic tool `agentsassemble_room_read`"
        speak_interface = "Codex dynamic tool `agentsassemble_room_speak` with `content`"
    elif kind in {"claude_code", "antigravity_live_session"}:
        read_interface = "terminal command `agentsassemble-room read`"
        speak_interface = "terminal command `agentsassemble-room speak '<message>'`"
    elif kind == "grok_live_session":
        read_interface = "ACP read path `/agentsassemble-room/current.md`"
        speak_interface = (
            "ACP write path `/agentsassemble-room/outbox.txt` with the message as its content"
        )
    else:
        read_interface = (
            "the provider's private room read interface: Codex `agentsassemble_room_read`, "
            "terminal `agentsassemble-room read`, or ACP `/agentsassemble-room/current.md`"
        )
        speak_interface = (
            "the matching private room speak interface: Codex `agentsassemble_room_speak`, "
            "terminal `agentsassemble-room speak '<message>'`, or ACP "
            "`/agentsassemble-room/outbox.txt`"
        )
    return f"""Shared room session:
- You are an ongoing participant in a shared AgentsAssemble room.
- A `room.wake <turn-id>` notice is a content-free signal that assigned,
  finalized room activity is available. It is not a request or a public message.
- The private room mirror is read through {read_interface}; public messages are
  staged through {speak_interface}.
- Only that publication boundary creates a public room message. Ordinary
  assistant output remains private.
- Room norm: public messages add new substance. Resolving an open decision is new
  substance; after a point is settled, receipt, thanks, repeated agreement,
  restatement, a silence explanation, or another closing is not."""


ROOM_SESSION_ORIENTATION = room_session_orientation()


class RoomPortalError(RuntimeError):
    pass


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
                },
            )
            self._write_view()

    def consume_publication(self, turn_id: str) -> str:
        clean_turn_id = clean_room_text(turn_id, limit=128)
        with self._lock:
            try:
                payload = json.loads(self.outbox_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return ""
            finally:
                self.outbox_path.unlink(missing_ok=True)
                self.turn_path.unlink(missing_ok=True)
                self._active_media_ids = ()
                self._active_messages = None
                self._write_view()
            if not isinstance(payload, dict):
                return ""
            if clean_room_text(payload.get("turn_id"), limit=128) != clean_turn_id:
                return ""
            return _publication_text(payload.get("content"))

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

    def acp_write_text(self, path: object, content: object) -> None:
        if str(path or "") != VIRTUAL_ROOM_OUTBOX_PATH:
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
                {"turn_id": turn_id, "content": text},
            )
            self._record_activity("speak", turn_id=turn_id)

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
            if identity != self.participant_id:
                continue
            name = clean_room_text(item.get("display_name"), limit=80)
            if name:
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
            "",
        ]
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
            content = str(message.get("content") or "")
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

    def _record_activity(self, operation: str, *, turn_id: str = "") -> None:
        if not turn_id:
            try:
                turn = json.loads(self.turn_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                turn = {}
            turn_id = clean_room_text(
                turn.get("turn_id") if isinstance(turn, dict) else "",
                limit=128,
            )
        payload = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "operation": clean_room_text(operation, limit=32),
            "turn_id": turn_id,
        }
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


def _publication_text(value: object) -> str:
    return (
        str(value or "")
        .replace("\x00", "")
        .replace("\r\n", "\n")
        .replace("\r", "\n")[:_MAX_ROOM_MESSAGE_CHARS]
        .strip()
    )


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


_HELPER_SCRIPT = r"""#!/usr/bin/env python3
import json
import os
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

def audit(operation, turn_id=""):
    if not turn_id:
        try:
            turn_id = str(json.loads(TURN.read_text(encoding="utf-8")).get("turn_id") or "")
        except (OSError, json.JSONDecodeError):
            turn_id = ""
    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "operation": operation,
        "turn_id": turn_id,
    }
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
elif command == "speak":
    content = " ".join(sys.argv[2:]).strip() if len(sys.argv) > 2 else sys.stdin.read().strip()
    if not content:
        fail("room message is empty")
    turn = json.loads(TURN.read_text(encoding="utf-8"))
    turn_id = str(turn.get("turn_id") or "")
    if not turn_id:
        fail("no room observation is active")
    atomic_json(OUTBOX, {"turn_id": turn_id, "content": content[:12000]})
    audit("speak", turn_id)
elif command == "media":
    attachment_id = sys.argv[2] if len(sys.argv) > 2 else ""
    index = json.loads(MEDIA.read_text(encoding="utf-8")).get("media", {})
    item = index.get(attachment_id)
    if not isinstance(item, dict) or not item.get("path"):
        fail("media is unavailable")
    audit("media")
    print(item["path"])
elif command == "help":
    print("agentsassemble-room read | speak [text] | media <id>")
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
    "RoomPortal",
    "RoomPortalError",
    "VIRTUAL_ROOM_OUTBOX_PATH",
    "VIRTUAL_ROOM_VIEW_PATH",
    "room_session_orientation",
]
