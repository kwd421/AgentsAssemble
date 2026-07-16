"""Explicit command boundary for admission workflow retention maintenance."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from agentsassemble.admission.maintenance import AdmissionWorkflowSelection
from agentsassemble.room_invite_repository_factory import build_invite_session_repository
from agentsassemble.room_repository_factory import RoomRepositorySettings


def purge_admission_workflows(
    *,
    output_root: Path,
    repository_backend: str,
    postgres_dsn_env: str,
    updated_before: str,
    room_id: str = "",
    apply: bool = False,
) -> dict[str, object]:
    """Inspect or explicitly remove terminal workflows older than a cutoff."""

    try:
        cutoff = datetime.fromisoformat(str(updated_before or "").strip())
    except ValueError as error:
        raise ValueError("--before must be an ISO 8601 timestamp with a timezone.") from error
    selection = AdmissionWorkflowSelection(
        room_id=room_id,
        updated_before=cutoff,
    )
    settings = RoomRepositorySettings.from_environment(
        backend=repository_backend,
        postgres_dsn_env=postgres_dsn_env,
    )
    repository = build_invite_session_repository(Path(output_root), settings)
    try:
        report = repository.purge_admission_workflows(selection, apply=apply)
    finally:
        repository.close()
    return {
        "status": "applied" if apply else "ready",
        "backend": settings.backend,
        **report.as_dict(),
    }
