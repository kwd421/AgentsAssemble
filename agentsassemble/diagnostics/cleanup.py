from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
import sys
from typing import TextIO

from agentsassemble.room.text import clean_room_text as clean_lobby_text


@dataclass(frozen=True)
class CleanupFailure:
    stage: str
    handle_id: str
    error_type: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {
            "stage": self.stage,
            "handle_id": self.handle_id,
            "error_type": self.error_type,
            "message": self.message,
        }


@dataclass
class CleanupReport:
    component: str
    attempted: int = 0
    completed: int = 0
    failures: list[CleanupFailure] = field(default_factory=list)
    orphaned_handle_ids: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures and not self.orphaned_handle_ids

    def record_success(self) -> None:
        self.attempted += 1
        self.completed += 1

    def record_failure(
        self,
        stage: str,
        error: BaseException,
        *,
        handle_id: str = "",
        orphaned: bool = False,
    ) -> None:
        self.attempted += 1
        clean_handle = clean_lobby_text(handle_id, limit=128)
        self.failures.append(
            CleanupFailure(
                stage=clean_lobby_text(stage, limit=128) or "cleanup",
                handle_id=clean_handle,
                error_type=type(error).__name__,
                message=_safe_cleanup_message(error),
            )
        )
        if orphaned and clean_handle and clean_handle not in self.orphaned_handle_ids:
            self.orphaned_handle_ids.append(clean_handle)

    def merge(self, other: CleanupReport) -> None:
        self.attempted += other.attempted
        self.completed += other.completed
        self.failures.extend(other.failures)
        for handle_id in other.orphaned_handle_ids:
            if handle_id not in self.orphaned_handle_ids:
                self.orphaned_handle_ids.append(handle_id)

    def as_dict(self) -> dict[str, object]:
        return {
            "component": self.component,
            "attempted": self.attempted,
            "completed": self.completed,
            "failed": len(self.failures),
            "ok": self.ok,
            "failures": [failure.as_dict() for failure in self.failures],
            "orphaned_handle_ids": list(self.orphaned_handle_ids),
        }


def emit_cleanup_failure(report: CleanupReport, *, stream: TextIO | None = None) -> None:
    if report.ok:
        return
    output = stream or sys.stderr
    output.write("AgentsAssemble cleanup failure: ")
    output.write(json.dumps(report.as_dict(), ensure_ascii=True, sort_keys=True))
    output.write("\n")
    output.flush()


_SENSITIVE_CLEANUP_VALUE = re.compile(
    r"(?i)(?:authorization|api[_-]?key|password|secret|token)\s*[:=]\s*\S+"
)
_SECRET_PREFIX = re.compile(r"\b(?:sk|aai1)[-_\.][A-Za-z0-9._-]{6,}\b")


def _safe_cleanup_message(error: BaseException) -> str:
    text = clean_lobby_text(str(error), limit=500)
    if not text:
        return type(error).__name__
    text = _SENSITIVE_CLEANUP_VALUE.sub("[redacted]", text)
    text = _SECRET_PREFIX.sub("[redacted]", text)
    return text


__all__ = [
    "CleanupFailure",
    "CleanupReport",
    "emit_cleanup_failure",
]
