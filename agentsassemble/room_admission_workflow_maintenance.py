"""Safe selection and reporting for durable admission workflow maintenance."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Mapping


TERMINAL_ADMISSION_WORKFLOW_STATUSES = frozenset({"completed", "failed_terminal"})
_REPORT_WORKFLOW_ID_LIMIT = 100


@dataclass(frozen=True)
class AdmissionWorkflowSelection:
    """Select only terminal workflows, optionally scoped by room and age."""

    room_id: str = ""
    updated_before: datetime | None = None
    statuses: frozenset[str] = TERMINAL_ADMISSION_WORKFLOW_STATUSES

    def __post_init__(self) -> None:
        clean_room_id = str(self.room_id or "").strip()
        clean_statuses = frozenset(str(status or "").strip() for status in self.statuses)
        if not clean_statuses:
            raise ValueError("At least one terminal admission workflow status is required.")
        unsupported = clean_statuses - TERMINAL_ADMISSION_WORKFLOW_STATUSES
        if unsupported:
            raise ValueError(
                "Admission workflow maintenance cannot select non-terminal statuses: "
                + ", ".join(sorted(unsupported))
            )
        cutoff = self.updated_before
        if cutoff is not None:
            if cutoff.tzinfo is None or cutoff.utcoffset() is None:
                raise ValueError("Admission workflow maintenance cutoff must be timezone-aware.")
            cutoff = cutoff.astimezone(UTC)
        object.__setattr__(self, "room_id", clean_room_id)
        object.__setattr__(self, "statuses", clean_statuses)
        object.__setattr__(self, "updated_before", cutoff)

    def matches(self, record: Mapping[str, object]) -> bool:
        if str(record.get("status") or "").strip() not in self.statuses:
            return False
        if self.room_id and str(record.get("room_id") or "").strip() != self.room_id:
            return False
        if self.updated_before is None:
            return True
        updated_at = _parse_timestamp(record.get("updated_at"))
        return updated_at is not None and updated_at < self.updated_before

    def public_summary(self) -> dict[str, object]:
        return {
            "room_id": self.room_id,
            "updated_before": (
                self.updated_before.isoformat() if self.updated_before is not None else ""
            ),
            "statuses": sorted(self.statuses),
        }


@dataclass(frozen=True)
class PurgeReport:
    """Bounded result of a dry-run or applied workflow purge."""

    selection: AdmissionWorkflowSelection
    applied: bool
    selected_count: int
    purged_count: int
    status_counts: Mapping[str, int] = field(default_factory=dict)
    workflow_ids: tuple[str, ...] = ()
    workflow_ids_truncated: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "status_counts", MappingProxyType(dict(self.status_counts)))

    @property
    def mode(self) -> str:
        return "apply" if self.applied else "dry_run"

    def as_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "selection": self.selection.public_summary(),
            "selected_count": self.selected_count,
            "purged_count": self.purged_count,
            "status_counts": dict(self.status_counts),
            "workflow_ids": list(self.workflow_ids),
            "workflow_ids_truncated": self.workflow_ids_truncated,
        }


def build_purge_report(
    selection: AdmissionWorkflowSelection,
    records: list[Mapping[str, object]],
    *,
    applied: bool,
    purged_count: int,
) -> PurgeReport:
    ordered = sorted(
        records,
        key=lambda record: (
            str(record.get("updated_at") or ""),
            str(record.get("workflow_id") or ""),
        ),
    )
    workflow_ids = tuple(
        str(record.get("workflow_id") or "")
        for record in ordered[:_REPORT_WORKFLOW_ID_LIMIT]
    )
    return PurgeReport(
        selection=selection,
        applied=applied,
        selected_count=len(ordered),
        purged_count=purged_count,
        status_counts=Counter(str(record.get("status") or "") for record in ordered),
        workflow_ids=workflow_ids,
        workflow_ids_truncated=len(ordered) > _REPORT_WORKFLOW_ID_LIMIT,
    )


def _parse_timestamp(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or ""))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)

