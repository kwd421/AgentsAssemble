"""Structured collaboration actions owned by one private RoomPortal."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from agentsassemble.providers.runtime_contracts import SUPPORTED_DECLINE_REASONS
from agentsassemble.room.text import clean_room_text
from agentsassemble.room.votes import (
    normalize_vote_definition,
    normalize_vote_duration_seconds,
    resolve_vote_choice,
    vote_deadline_has_passed,
    vote_poll,
)


class RoomPortalError(RuntimeError):
    pass


RoomPortalCollaborationError = RoomPortalError


@dataclass(frozen=True)
class RoomPublication:
    content: str = ""
    target_agent_id: str = ""
    message_kind: str = "message"
    vote_id: str = ""
    vote_question: str = ""
    vote_options: tuple[str, ...] = ()
    vote_duration_seconds: int = 0
    vote_choice: str = ""

    @property
    def has_message(self) -> bool:
        return bool(self.content) or self.message_kind in {
            "vote",
            "vote_cast",
            "vote_withdraw",
        }

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> RoomPublication:
        options = payload.get("vote_options")
        duration = payload.get("vote_duration_seconds")
        return cls(
            content=_publication_text(payload.get("content")),
            target_agent_id=clean_room_text(payload.get("target_agent_id"), limit=128),
            message_kind=clean_room_text(payload.get("message_kind"), limit=64) or "message",
            vote_id=clean_room_text(payload.get("vote_id"), limit=128),
            vote_question=clean_room_text(payload.get("vote_question"), limit=300),
            vote_options=tuple(
                option
                for value in (options if isinstance(options, list) else [])
                if (option := clean_room_text(value, limit=100))
            ),
            vote_duration_seconds=(
                max(0, duration)
                if isinstance(duration, int) and not isinstance(duration, bool)
                else 0
            ),
            vote_choice=clean_room_text(payload.get("vote_choice"), limit=100),
        )


JsonWriter = Callable[[Path, dict[str, object]], None]
ActivityRecorder = Callable[..., None]
ToolAuthorizer = Callable[[str], None]
MessageReader = Callable[[], list[dict[str, object]]]


def _safe_count(value: object) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


class RoomPortalCollaboration:
    """Own participant discovery, ballots, declines, and structured outbox state."""

    def __init__(
        self,
        *,
        participant_id: str,
        turn_path: Path,
        outbox_path: Path,
        activity_path: Path,
        participant_index_path: Path,
        lock: threading.RLock,
        write_json: JsonWriter,
        record_activity: ActivityRecorder,
        require_tool: ToolAuthorizer,
        messages: MessageReader,
    ) -> None:
        self.participant_id = participant_id
        self.turn_path = turn_path
        self.outbox_path = outbox_path
        self.activity_path = activity_path
        self.participant_index_path = participant_index_path
        self.message_index_path = participant_index_path.with_name("messages.json")
        self._lock = lock
        self._write_json = write_json
        self._record_activity = record_activity
        self._require_tool = require_tool
        self._messages = messages
        self._participants: dict[str, dict[str, str]] = {}

    def remember_participant(
        self,
        participant_id: str,
        *,
        display_name: str,
        participant_type: str,
        role: str,
    ) -> None:
        self._participants[participant_id] = {
            "participant_id": participant_id,
            "display_name": display_name or participant_id,
            "participant_type": participant_type,
            "role": role,
        }

    def write_participant_index(self, agent_ids: list[str]) -> None:
        self._write_json(
            self.participant_index_path,
            {
                "agents": sorted(agent_ids),
                "participants": self._sorted_participants(),
            },
        )

    def write_message_index(self) -> None:
        self._write_json(self.message_index_path, {"messages": self._messages()})

    def list_participants(self) -> list[dict[str, str]]:
        self._require_tool("list_participants")
        with self._lock:
            participants = self._sorted_participants()
        self._record_activity("list_participants")
        return participants

    def decline_to_speak(self, reason_code: object) -> dict[str, object]:
        self._require_tool("decline_to_speak")
        reason = clean_room_text(reason_code, limit=64)
        if reason not in SUPPORTED_DECLINE_REASONS:
            raise RoomPortalCollaborationError("A supported decline reason is required.")
        turn_id = self.active_turn_id()
        self.stage_publication(
            turn_id,
            {"content": "", "target_agent_id": "", "message_kind": "decline"},
        )
        self._record_activity(
            "decline_to_speak",
            turn_id=turn_id,
            details={"reason_code": reason},
        )
        return {"declined": True, "reason_code": reason}

    def create_vote(
        self,
        question: object,
        options: list[object],
        *,
        duration_seconds: object = 0,
    ) -> dict[str, object]:
        self._require_tool("create_vote")
        try:
            clean_question, clean_options = normalize_vote_definition(question, options)
            duration = normalize_vote_duration_seconds(duration_seconds)
        except ValueError as error:
            raise RoomPortalCollaborationError(str(error)) from error
        turn_id = self.active_turn_id()
        self.stage_publication(
            turn_id,
            {
                "content": "",
                "target_agent_id": "",
                "message_kind": "vote",
                "vote_question": clean_question,
                "vote_options": clean_options,
                "vote_duration_seconds": duration or 0,
            },
        )
        details = {
            "question": clean_question,
            "options": clean_options,
            "duration_seconds": duration or 0,
        }
        self._record_activity("create_vote", turn_id=turn_id, details=details)
        return {"queued": True, **details}

    def cast_vote(self, vote_id: object, choice: object) -> dict[str, object]:
        self._require_tool("cast_vote")
        clean_vote_id = clean_room_text(vote_id, limit=128)
        with self._lock:
            poll = next(
                (
                    item
                    for item in self._messages()
                    if clean_room_text(item.get("id"), limit=128) == clean_vote_id
                ),
                {},
            )
        try:
            canonical_poll = vote_poll(poll, clean_vote_id)
            if vote_deadline_has_passed(canonical_poll.get("vote_deadline_at")):
                raise ValueError("This vote has ended.")
            matched = resolve_vote_choice(
                choice,
                list(canonical_poll.get("vote_options") or []),
            )
            if not matched:
                raise ValueError("choice must match one of the vote options.")
        except ValueError as error:
            raise RoomPortalCollaborationError(str(error)) from error
        turn_id = self.active_turn_id()
        self.stage_publication(
            turn_id,
            {
                "content": "",
                "target_agent_id": "",
                "message_kind": "vote_cast",
                "vote_id": clean_vote_id,
                "vote_choice": matched,
            },
        )
        details = {"vote_id": clean_vote_id, "choice": matched}
        self._record_activity("cast_vote", turn_id=turn_id, details=details)
        return {"queued": True, **details}

    def withdraw_vote(self, vote_id: object) -> dict[str, object]:
        self._require_tool("withdraw_vote")
        clean_vote_id = clean_room_text(vote_id, limit=128)
        with self._lock:
            poll = next(
                (
                    item
                    for item in self._messages()
                    if clean_room_text(item.get("id"), limit=128) == clean_vote_id
                ),
                {},
            )
        try:
            canonical_poll = vote_poll(poll, clean_vote_id)
            if vote_deadline_has_passed(canonical_poll.get("vote_deadline_at")):
                raise ValueError("This vote has ended.")
        except ValueError as error:
            raise RoomPortalCollaborationError(str(error)) from error
        turn_id = self.active_turn_id()
        self.stage_publication(
            turn_id,
            {
                "content": "",
                "target_agent_id": "",
                "message_kind": "vote_withdraw",
                "vote_id": clean_vote_id,
            },
        )
        details = {"vote_id": clean_vote_id}
        self._record_activity("withdraw_vote", turn_id=turn_id, details=details)
        return {"queued": True, **details}

    def vote_summary(self, vote_id: object) -> dict[str, object]:
        self._require_tool("vote_summary")
        clean_vote_id = clean_room_text(vote_id, limit=128)
        with self._lock:
            poll = next(
                (
                    dict(item)
                    for item in self._messages()
                    if clean_room_text(
                        item.get("vote_id") or item.get("id"), limit=128
                    )
                    == clean_vote_id
                    and clean_room_text(item.get("message_kind"), limit=64)
                    == "vote"
                ),
                {},
            )
        try:
            canonical_poll = vote_poll(poll, clean_vote_id)
        except ValueError as error:
            raise RoomPortalCollaborationError(str(error)) from error
        options = [str(option) for option in canonical_poll.get("vote_options") or []]
        raw_tallies = (
            canonical_poll.get("vote_tallies")
            if isinstance(canonical_poll.get("vote_tallies"), dict)
            else {}
        )
        tallies = {
            option: max(0, _safe_count(raw_tallies.get(option)))
            for option in options
        }
        own_choice = resolve_vote_choice(
            canonical_poll.get("vote_own_choice"), options
        )
        summary = {
            "vote_id": clean_vote_id,
            "question": str(canonical_poll.get("vote_question") or ""),
            "options": options,
            "vote_duration_seconds": int(
                canonical_poll.get("vote_duration_seconds") or 0
            ),
            "vote_deadline_at": str(canonical_poll.get("vote_deadline_at") or ""),
            "created_by": str(canonical_poll.get("display_name") or ""),
            "created_at": str(canonical_poll.get("created_at") or ""),
            "tallies": tallies,
            "own_choice": own_choice,
            "total_votes": sum(tallies.values()),
        }
        self._record_activity("vote_summary", details={"vote_id": clean_vote_id})
        return {**summary, "scope": "bounded_current_view"}

    def decline_reason(self, turn_id: str) -> str:
        for record in self._activity_records(turn_id):
            if record.get("operation") != "decline_to_speak":
                continue
            details = record.get("details") if isinstance(record.get("details"), dict) else {}
            reason = clean_room_text(details.get("reason_code"), limit=64)
            if reason in SUPPORTED_DECLINE_REASONS:
                return reason
        return ""

    def active_turn_id(self) -> str:
        try:
            turn = json.loads(self.turn_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RoomPortalCollaborationError("No room observation is active.") from error
        turn_id = clean_room_text(
            turn.get("turn_id") if isinstance(turn, dict) else "",
            limit=128,
        )
        if not turn_id:
            raise RoomPortalCollaborationError("No room observation is active.")
        return turn_id

    def stage_publication(self, turn_id: str, payload: dict[str, object]) -> None:
        with self._lock:
            if self.outbox_path.exists():
                raise RoomPortalCollaborationError(
                    "A public room action is already staged for this turn."
                )
            self._write_json(self.outbox_path, {"turn_id": turn_id, **payload})

    def _sorted_participants(self) -> list[dict[str, str]]:
        return sorted(
            (dict(item) for item in self._participants.values()),
            key=lambda item: (item["display_name"].casefold(), item["participant_id"]),
        )

    def _activity_records(self, turn_id: str) -> list[dict[str, object]]:
        clean_turn_id = clean_room_text(turn_id, limit=128)
        if not clean_turn_id:
            return []
        try:
            turn = json.loads(self.turn_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        if (
            not isinstance(turn, dict)
            or clean_room_text(turn.get("turn_id"), limit=128) != clean_turn_id
        ):
            return []
        offset = turn.get("activity_offset")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            return []
        try:
            with self.activity_path.open("r", encoding="utf-8") as stream:
                stream.seek(offset)
                lines = stream.readlines()
        except OSError:
            return []
        records: list[dict[str, object]] = []
        for line in lines:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (
                isinstance(record, dict)
                and clean_room_text(record.get("turn_id"), limit=128) == clean_turn_id
            ):
                records.append(record)
        return records


def _publication_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return (
        value.replace("\x00", "")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .strip()[:12_000]
    )


__all__ = [
    "RoomPortalCollaboration",
    "RoomPortalCollaborationError",
    "RoomPortalError",
    "RoomPublication",
]
