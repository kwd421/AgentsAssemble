"""Durable state restoration for the deterministic colony simulation."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from sim import Colonist, ColonySimulation, MAP_HEIGHT, MAP_WIDTH


def restore_simulation(sim: ColonySimulation, payload: dict[str, Any]) -> None:
    map_payload = payload.get("map") if isinstance(payload.get("map"), dict) else {}
    if int(map_payload.get("width") or 0) != MAP_WIDTH:
        raise ValueError("Stored map width is incompatible with this plugin version.")
    if int(map_payload.get("height") or 0) != MAP_HEIGHT:
        raise ValueError("Stored map height is incompatible with this plugin version.")
    colonist_payloads = payload.get("colonists")
    if not isinstance(colonist_payloads, list) or not colonist_payloads:
        raise ValueError("Stored simulation has no colonists.")

    colonists: list[Colonist] = []
    for item in colonist_payloads:
        if not isinstance(item, dict):
            raise ValueError("Stored colonist is invalid.")
        needs = item.get("needs") if isinstance(item.get("needs"), dict) else {}
        colonists.append(
            Colonist(
                id=str(item["id"]),
                name=str(item["name"]),
                x=int(item["x"]),
                y=int(item["y"]),
                hunger=float(needs.get("hunger", 0.75)),
                rest=float(needs.get("rest", 0.75)),
                recreation=float(needs.get("recreation", 0.65)),
                comfort=float(needs.get("comfort", 0.55)),
                mood=float(needs.get("mood", 0.70)),
                traits=[str(value) for value in item.get("traits", [])],
                skills=deepcopy(item.get("skills") if isinstance(item.get("skills"), dict) else {}),
                relations={
                    str(key): float(value)
                    for key, value in (
                        item.get("relations") if isinstance(item.get("relations"), dict) else {}
                    ).items()
                },
                injuries=deepcopy(item.get("injuries") if isinstance(item.get("injuries"), list) else []),
                pain=float(item.get("pain") or 0.0),
                job_priorities={
                    str(key): int(value)
                    for key, value in (
                        item.get("job_priorities")
                        if isinstance(item.get("job_priorities"), dict)
                        else {}
                    ).items()
                },
                current_job=deepcopy(
                    item.get("current_job") if isinstance(item.get("current_job"), dict) else None
                ),
                waiting=bool(item.get("waiting")),
                stop_after_job=bool(item.get("stop_after_job")),
                error=str(item.get("error") or ""),
                mental_break=str(item.get("mental_break") or ""),
                low_mood_ticks=max(0, int(item.get("low_mood_ticks") or 0)),
                need_alerts={
                    str(value)
                    for value in item.get("need_alerts", [])
                    if str(value)
                },
            )
        )

    sim.seed = int(payload.get("seed") or sim.seed)
    sim.rng.seed(sim.seed)
    sim.tick = int(payload.get("tick") or 0)
    sim.speed = int(payload.get("speed") or 0)
    sim.revision = int(payload.get("revision") or 0)
    sim.resources = {
        str(key): int(value)
        for key, value in (
            payload.get("resources") if isinstance(payload.get("resources"), dict) else {}
        ).items()
    }
    sim.structures = deepcopy(payload.get("structures") if isinstance(payload.get("structures"), list) else [])
    sim.blueprints = deepcopy(payload.get("blueprints") if isinstance(payload.get("blueprints"), list) else [])
    sim.raid = deepcopy(payload.get("raid") if isinstance(payload.get("raid"), dict) else None)
    sim.recovery_until_tick = int(payload.get("recovery_until_tick") or 0)
    last_threat_tick = payload.get("last_threat_tick")
    sim.last_threat_tick = int(last_threat_tick) if last_threat_tick is not None else 0
    sim.events = deepcopy(payload.get("events") if isinstance(payload.get("events"), list) else [])
    sim.colonists = colonists
    sim._agent_wakes = []


__all__ = ["restore_simulation"]
