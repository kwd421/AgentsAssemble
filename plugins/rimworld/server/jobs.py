"""Deterministic work selection and movement for the colony simulation."""

from __future__ import annotations

from typing import Any

JOB_ORDER = (
    "fight",
    "tend",
    "construct",
    "haul",
    "eat",
    "sleep",
    "recreation",
    "social",
    "work",
)


def choose_next_job(simulation: Any, colonist: Any) -> dict[str, Any]:
    candidates = _job_candidates(simulation, colonist)
    if not candidates:
        raise RuntimeError("No colony work is currently available.")

    order = {kind: index for index, kind in enumerate(JOB_ORDER)}
    candidates.sort(
        key=lambda job: (
            _priority(colonist, str(job["kind"])),
            order[str(job["kind"])],
            _distance(colonist, job.get("target")),
            str((job.get("target") or {}).get("id") or ""),
        )
    )
    return candidates[0]


def job_target_position(
    simulation: Any,
    colonist: Any,
    job: dict[str, Any],
) -> tuple[int, int] | None:
    target = job.get("target") if isinstance(job.get("target"), dict) else {}
    for key in ("other_id", "patient_id"):
        target_id = str(target.get(key) or "")
        if not target_id:
            continue
        other = next(
            (item for item in simulation.colonists if item.id == target_id),
            None,
        )
        return (other.x, other.y) if other is not None else None
    if "x" in target and "y" in target:
        return int(target["x"]), int(target["y"])
    return None


def move_toward(colonist: Any, target: tuple[int, int]) -> bool:
    """Move one Manhattan tile and return whether the target is now reached."""

    target_x, target_y = target
    if colonist.x < target_x:
        colonist.x += 1
    elif colonist.x > target_x:
        colonist.x -= 1
    elif colonist.y < target_y:
        colonist.y += 1
    elif colonist.y > target_y:
        colonist.y -= 1
    return colonist.x == target_x and colonist.y == target_y


def _job_candidates(simulation: Any, colonist: Any) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    raid = simulation.raid if isinstance(simulation.raid, dict) else None
    if raid is not None:
        candidates.append(
            _job("fight", x=int(raid.get("x") or 0), y=int(raid.get("y") or 0))
        )

    for patient in simulation.colonists:
        if patient.injuries or patient.pain > 0.05:
            candidates.append(
                _job(
                    "tend",
                    id=patient.id,
                    patient_id=patient.id,
                    x=patient.x,
                    y=patient.y,
                )
            )

    for blueprint in simulation.blueprints:
        candidates.append(
            _job(
                "construct",
                id=str(blueprint.get("id") or ""),
                blueprint_id=str(blueprint.get("id") or ""),
                kind=str(blueprint.get("kind") or ""),
                x=int(blueprint.get("x") or 0),
                y=int(blueprint.get("y") or 0),
            )
        )

    if simulation.resources.get("wood", 0) < 50:
        candidates.append(_job("haul", x=4, y=4))
    if simulation.resources.get("food", 0) > 0 and colonist.hunger < 0.60:
        candidates.append(_job("eat", **_nearest_structure(simulation, colonist, {"storage", "table"}, (4, 4))))
    if colonist.rest < 0.60:
        candidates.append(_job("sleep", **_nearest_structure(simulation, colonist, {"bed"}, (8, 8))))
    if colonist.recreation < 0.55:
        candidates.append(
            _job(
                "recreation",
                **_nearest_structure(simulation, colonist, {"campfire", "table"}, (9, 9)),
            )
        )
    for other in simulation.colonists:
        if other.id != colonist.id:
            candidates.append(
                _job(
                    "social",
                    id=other.id,
                    other_id=other.id,
                    x=other.x,
                    y=other.y,
                )
            )
    candidates.append(
        _job("work", **_nearest_structure(simulation, colonist, {"workbench"}, (6, 6)))
    )
    return candidates


def _job(job_kind: str, **target: Any) -> dict[str, Any]:
    return {"kind": job_kind, "progress": 0.0, "target": target}


def _nearest_structure(
    simulation: Any,
    colonist: Any,
    kinds: set[str],
    default: tuple[int, int],
) -> dict[str, int]:
    candidates = [
        (int(item.get("x") or 0), int(item.get("y") or 0))
        for item in simulation.structures
        if str(item.get("kind") or "") in kinds
    ]
    if not candidates:
        return {"x": default[0], "y": default[1]}
    x, y = min(candidates, key=lambda point: abs(colonist.x - point[0]) + abs(colonist.y - point[1]))
    return {"x": x, "y": y}


def _priority(colonist: Any, kind: str) -> int:
    value = int(colonist.job_priorities.get(kind, 4))
    return value if 1 <= value <= 4 else 4


def _distance(colonist: Any, target: object) -> int:
    if not isinstance(target, dict) or "x" not in target or "y" not in target:
        return 0
    return abs(colonist.x - int(target["x"])) + abs(colonist.y - int(target["y"]))


__all__ = ["JOB_ORDER", "choose_next_job", "job_target_position", "move_toward"]
