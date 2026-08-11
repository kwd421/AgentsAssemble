"""Deterministic RimWorld-inspired colony simulation vertical slice.

Original RimWorld assets are not used. This is an independent prototype with
original shapes/colors and a subset of needs/skills/storyteller mechanics.
"""

from __future__ import annotations

import random
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

try:
    from .jobs import JOB_ORDER, choose_next_job, job_target_position, move_toward
except ImportError:  # Executed as the isolated plugin entrypoint.
    from jobs import JOB_ORDER, choose_next_job, job_target_position, move_toward

MAP_WIDTH = 48
MAP_HEIGHT = 32
BUILDABLES = {
    "bed": {"wood": 20},
    "wall": {"wood": 5},
    "door": {"wood": 10},
    "table": {"wood": 15},
    "campfire": {"wood": 10, "steel": 2},
    "workbench": {"wood": 25, "steel": 10},
    "storage": {"wood": 8},
}

# Mental break thresholds inspired by RimWorld wiki defaults (base 35%).
MINOR_BREAK = 0.35
MAJOR_BREAK = 0.20
EXTREME_BREAK = 0.05
BREAK_LOW_MOOD_TICKS = 450
INITIAL_THREAT_GRACE_TICKS = 1_800
THREAT_RECOVERY_TICKS = 1_800


@dataclass
class Colonist:
    id: str
    name: str
    x: int
    y: int
    hunger: float = 0.75
    rest: float = 0.75
    recreation: float = 0.65
    comfort: float = 0.55
    mood: float = 0.70
    traits: list[str] = field(default_factory=list)
    skills: dict[str, dict[str, Any]] = field(default_factory=dict)
    relations: dict[str, float] = field(default_factory=dict)
    injuries: list[dict[str, Any]] = field(default_factory=list)
    pain: float = 0.0
    job_priorities: dict[str, int] = field(default_factory=dict)
    current_job: dict[str, Any] | None = None
    waiting: bool = False
    stop_after_job: bool = False
    error: str = ""
    mental_break: str = ""
    low_mood_ticks: int = 0
    need_alerts: set[str] = field(default_factory=set)


class ColonySimulation:
    def __init__(self, *, seed: int = 1) -> None:
        self.seed = int(seed)
        self.rng = random.Random(self.seed)
        self.tick = 0
        self.speed = 0  # 0=pause, 1=1x, 3=3x
        self.resources = {"wood": 80, "steel": 30, "food": 40}
        self.structures: list[dict[str, Any]] = []
        self.blueprints: list[dict[str, Any]] = []
        self.raid: dict[str, Any] | None = None
        self.recovery_until_tick = 0
        # New colonies get time to establish before the storyteller may
        # introduce the first threat.
        self.last_threat_tick = 0
        self.events: list[dict[str, Any]] = []
        self.colonists = self._spawn_colonists()
        self.revision = 0
        self._agent_wakes: list[dict[str, str]] = []

    def _spawn_colonists(self) -> list[Colonist]:
        specs = (
            ("c1", "Aya", 8, 8, ["industrious"], {"construction": 8, "plants": 5, "medicine": 3}),
            ("c2", "Bok", 10, 8, ["night_owl"], {"shooting": 7, "melee": 6, "social": 4}),
            ("c3", "Cyra", 12, 8, ["ascetic"], {"crafting": 7, "cooking": 6, "artistic": 5}),
        )
        colonists: list[Colonist] = []
        for colonist_id, name, x, y, traits, skills in specs:
            skill_payload = {
                skill: {
                    "level": level,
                    "passion": self.rng.choice(["none", "minor", "major"]),
                }
                for skill, level in skills.items()
            }
            priorities = {job: 3 for job in JOB_ORDER}
            priorities["eat"] = 1
            priorities["sleep"] = 1
            priorities["fight"] = 1
            colonists.append(
                Colonist(
                    id=colonist_id,
                    name=name,
                    x=x,
                    y=y,
                    traits=list(traits),
                    skills=skill_payload,
                    job_priorities=priorities,
                    relations={},
                )
            )
        for left in colonists:
            for right in colonists:
                if left.id != right.id:
                    left.relations[right.id] = 0.2
        return colonists

    def snapshot(self) -> dict[str, Any]:
        return {
            "tick": self.tick,
            "speed": self.speed,
            "seed": self.seed,
            "revision": self.revision,
            "map": {"width": MAP_WIDTH, "height": MAP_HEIGHT},
            "resources": dict(self.resources),
            "structures": deepcopy(self.structures),
            "blueprints": deepcopy(self.blueprints),
            "raid": deepcopy(self.raid),
            "recovery_until_tick": self.recovery_until_tick,
            "last_threat_tick": self.last_threat_tick,
            "events": list(self.events[-20:]),
            "colonists": [self._colonist_public(colonist) for colonist in self.colonists],
            "job_order": list(JOB_ORDER),
        }

    def _colonist_public(self, colonist: Colonist) -> dict[str, Any]:
        return {
            "id": colonist.id,
            "name": colonist.name,
            "x": colonist.x,
            "y": colonist.y,
            "needs": {
                "hunger": round(colonist.hunger, 3),
                "rest": round(colonist.rest, 3),
                "recreation": round(colonist.recreation, 3),
                "comfort": round(colonist.comfort, 3),
                "mood": round(colonist.mood, 3),
            },
            "traits": list(colonist.traits),
            "skills": deepcopy(colonist.skills),
            "relations": dict(colonist.relations),
            "injuries": deepcopy(colonist.injuries),
            "pain": round(colonist.pain, 3),
            "job_priorities": dict(colonist.job_priorities),
            "current_job": deepcopy(colonist.current_job),
            "waiting": colonist.waiting,
            "stop_after_job": colonist.stop_after_job,
            "error": colonist.error,
            "mental_break": colonist.mental_break,
            "low_mood_ticks": colonist.low_mood_ticks,
            "need_alerts": sorted(colonist.need_alerts),
        }

    def set_speed(self, speed: int) -> None:
        if speed not in {0, 1, 3}:
            raise ValueError("speed must be 0, 1, or 3")
        self.speed = speed
        self.revision += 1

    def step(self, steps: int = 1) -> list[dict[str, Any]]:
        self._agent_wakes = []
        if self.speed == 0:
            return []
        for _ in range(max(1, int(steps)) * self.speed):
            self.tick += 1
            self._decay_needs()
            self._progress_jobs()
            self._resolve_combat()
            self._recover_injuries()
            self._maybe_storyteller()
            self._check_mental_breaks()
        self.revision += 1
        return list(self._agent_wakes)

    def _wake(self, colonist_id: str, reason: str) -> None:
        wake = {"colonist_id": colonist_id, "reason": reason}
        if wake not in self._agent_wakes:
            self._agent_wakes.append(wake)

    def apply_act(self, colonist_id: str, action: str, args: dict[str, Any]) -> dict[str, Any]:
        colonist = self._colonist(colonist_id)
        if colonist.waiting:
            raise RuntimeError(f"{colonist.name} is waiting after a model error.")
        if colonist.mental_break:
            raise RuntimeError(f"{colonist.name} is in a mental break: {colonist.mental_break}")
        action = str(action or "").strip().lower()
        if action == "choose_work":
            colonist.current_job = choose_next_job(self, colonist)
        elif action == "set_priorities":
            priorities = args.get("priorities")
            if not isinstance(priorities, dict) or not priorities:
                raise ValueError("priorities must be a non-empty object")
            for job, priority in priorities.items():
                name = str(job or "").strip().lower()
                value = int(priority)
                if name not in JOB_ORDER or value not in {1, 2, 3, 4}:
                    raise ValueError("Job priorities must map known jobs to 1-4.")
                colonist.job_priorities[name] = value
            colonist.current_job = choose_next_job(self, colonist)
        elif action == "set_job":
            job = str(args.get("job") or "").strip().lower()
            if job not in JOB_ORDER:
                raise ValueError(f"Unsupported job: {job}")
            target = args.get("target") if isinstance(args.get("target"), dict) else {}
            colonist.current_job = {"kind": job, "progress": 0.0, "target": target}
        elif action == "build":
            kind = str(args.get("kind") or "").strip().lower()
            x = int(args.get("x"))
            y = int(args.get("y"))
            blueprint = self._queue_blueprint(kind, x, y)
            colonist.current_job = {
                "kind": "construct",
                "progress": 0.0,
                "target": {
                    "blueprint_id": blueprint["id"],
                    "kind": kind,
                    "x": x,
                    "y": y,
                },
            }
        elif action == "haul":
            colonist.current_job = {"kind": "haul", "progress": 0.0, "target": args}
        elif action == "eat":
            colonist.current_job = {"kind": "eat", "progress": 0.0, "target": {}}
        elif action == "sleep":
            colonist.current_job = {"kind": "sleep", "progress": 0.0, "target": {}}
        elif action == "recreation":
            colonist.current_job = {"kind": "recreation", "progress": 0.0, "target": {}}
        elif action == "social":
            colonist.current_job = {
                "kind": "social",
                "progress": 0.0,
                "target": {"other_id": str(args.get("other_id") or "")},
            }
        elif action == "fight":
            colonist.current_job = {"kind": "fight", "progress": 0.0, "target": args}
        elif action == "tend":
            colonist.current_job = {
                "kind": "tend",
                "progress": 0.0,
                "target": {"patient_id": str(args.get("patient_id") or "")},
            }
        else:
            raise ValueError(f"Unsupported act: {action}")
        colonist.error = ""
        self.revision += 1
        return {"ok": True, "job": colonist.current_job}

    def mark_model_error(self, colonist_id: str, message: str) -> None:
        colonist = self._colonist(colonist_id)
        colonist.error = str(message or "model error")[:300]
        # Finish an already-started job, then wait. Never substitute another AI.
        colonist.stop_after_job = colonist.current_job is not None
        colonist.waiting = colonist.current_job is None
        self.revision += 1

    def clear_wait(self, colonist_id: str) -> None:
        colonist = self._colonist(colonist_id)
        colonist.waiting = False
        colonist.stop_after_job = False
        colonist.error = ""
        self.revision += 1

    def _colonist(self, colonist_id: str) -> Colonist:
        for colonist in self.colonists:
            if colonist.id == colonist_id:
                return colonist
        raise KeyError(colonist_id)

    def _queue_blueprint(self, kind: str, x: int, y: int) -> dict[str, Any]:
        if kind not in BUILDABLES:
            raise ValueError(f"Unknown buildable: {kind}")
        if not (0 <= x < MAP_WIDTH and 0 <= y < MAP_HEIGHT):
            raise ValueError("Build target outside map.")
        cost = BUILDABLES[kind]
        for resource, amount in cost.items():
            if self.resources.get(resource, 0) < amount:
                raise ValueError(f"Insufficient {resource}")
        for resource, amount in cost.items():
            self.resources[resource] -= amount
        blueprint = {
            "id": f"bp-{self.tick}-{len(self.blueprints)}",
            "kind": kind,
            "x": x,
            "y": y,
            "progress": 0.0,
        }
        self.blueprints.append(blueprint)
        return blueprint

    def _decay_needs(self) -> None:
        for colonist in self.colonists:
            # At 3x the process advances 15 ticks per wall-clock second. These
            # rates leave enough time for a provider observation and response
            # while still producing need events during a 15-minute run.
            colonist.hunger = max(0.0, colonist.hunger - 0.00010)
            colonist.rest = max(0.0, colonist.rest - 0.00007)
            colonist.recreation = max(0.0, colonist.recreation - 0.00005)
            comfort_floor = 0.35 if any(item["kind"] == "bed" for item in self.structures) else 0.2
            colonist.comfort = max(comfort_floor, min(1.0, colonist.comfort - 0.00002))
            mood = (
                0.35 * colonist.hunger
                + 0.25 * colonist.rest
                + 0.15 * colonist.recreation
                + 0.15 * colonist.comfort
                - 0.25 * colonist.pain
            )
            if self.raid is not None:
                mood -= 0.08
            colonist.mood = max(0.0, min(1.0, mood))
            for name in ("hunger", "rest", "recreation"):
                value = float(getattr(colonist, name))
                reason = f"need_{name}"
                if value <= 0.30 and reason not in colonist.need_alerts:
                    colonist.need_alerts.add(reason)
                    self._wake(colonist.id, reason)
                elif value >= 0.45:
                    colonist.need_alerts.discard(reason)

    def _progress_jobs(self) -> None:
        for colonist in self.colonists:
            job = colonist.current_job
            if not job or colonist.waiting:
                continue
            kind = str(job.get("kind") or "")
            target_position = job_target_position(self, colonist, job)
            if target_position is not None and (
                colonist.x != target_position[0] or colonist.y != target_position[1]
            ):
                move_toward(colonist, target_position)
                continue
            progress = float(job.get("progress") or 0.0)
            skill_bonus = 1.0
            if kind == "construct":
                skill_bonus = 1.0 + 0.05 * float(
                    (colonist.skills.get("construction") or {}).get("level") or 0
                )
            progress += 0.08 * skill_bonus
            job["progress"] = progress
            if kind == "construct":
                target = job.get("target") if isinstance(job.get("target"), dict) else {}
                blueprint_id = str(target.get("blueprint_id") or "")
                for blueprint in self.blueprints:
                    if str(blueprint.get("id") or "") == blueprint_id:
                        blueprint["progress"] = min(1.0, progress)
                        break
            if kind == "eat" and progress >= 1.0:
                if self.resources.get("food", 0) > 0:
                    self.resources["food"] -= 1
                    colonist.hunger = min(1.0, colonist.hunger + 0.55)
                colonist.current_job = None
            elif kind == "sleep" and progress >= 1.0:
                colonist.rest = min(1.0, colonist.rest + 0.65)
                colonist.comfort = min(1.0, colonist.comfort + 0.2)
                colonist.current_job = None
            elif kind == "recreation" and progress >= 1.0:
                colonist.recreation = min(1.0, colonist.recreation + 0.45)
                colonist.current_job = None
            elif kind == "social" and progress >= 1.0:
                other_id = str((job.get("target") or {}).get("other_id") or "")
                if other_id in colonist.relations:
                    colonist.relations[other_id] = min(1.0, colonist.relations[other_id] + 0.05)
                    colonist.recreation = min(1.0, colonist.recreation + 0.1)
                colonist.current_job = None
            elif kind == "construct" and progress >= 1.0:
                target = job.get("target") if isinstance(job.get("target"), dict) else {}
                kind_name = str(target.get("kind") or "")
                x = int(target.get("x") or 0)
                y = int(target.get("y") or 0)
                blueprint_id = str(target.get("blueprint_id") or "")
                self.blueprints = [
                    item
                    for item in self.blueprints
                    if not (
                        (blueprint_id and item.get("id") == blueprint_id)
                        or (
                            not blueprint_id
                            and item.get("kind") == kind_name
                            and item.get("x") == x
                            and item.get("y") == y
                        )
                    )
                ]
                self.structures.append({"kind": kind_name, "x": x, "y": y})
                colonist.current_job = None
            elif kind == "haul" and progress >= 1.0:
                self.resources["wood"] = self.resources.get("wood", 0) + 2
                colonist.current_job = None
            elif kind == "tend" and progress >= 1.0:
                patient_id = str((job.get("target") or {}).get("patient_id") or "")
                try:
                    patient = self._colonist(patient_id)
                    if patient.injuries:
                        patient.injuries.pop(0)
                    patient.pain = max(0.0, patient.pain - 0.2)
                except KeyError:
                    pass
                colonist.current_job = None
            elif kind == "fight":
                # combat resolved in _resolve_combat
                if self.raid is None:
                    colonist.current_job = None
            elif progress >= 1.0:
                colonist.current_job = None
            if colonist.current_job is None:
                self._wake(
                    colonist.id,
                    "social_event" if kind == "social" else "job_completed",
                )
            if colonist.current_job is None and colonist.stop_after_job:
                colonist.stop_after_job = False
                colonist.waiting = True

    def _resolve_combat(self) -> None:
        if self.raid is None:
            return
        fighters = [c for c in self.colonists if c.current_job and c.current_job.get("kind") == "fight"]
        if not fighters:
            return
        damage = 0.05 * len(fighters)
        self.raid["hp"] = max(0.0, float(self.raid.get("hp") or 0.0) - damage)
        if self.rng.random() < 0.08:
            victim = self.rng.choice(self.colonists)
            victim.injuries.append({"kind": "cut", "severity": 0.3})
            victim.pain = min(1.0, victim.pain + 0.15)
            victim.mood = max(0.0, victim.mood - 0.1)
            self._wake(victim.id, "injury")
        if float(self.raid.get("hp") or 0.0) <= 0:
            self.events.append({"tick": self.tick, "kind": "raid_defeated"})
            self.raid = None
            self.recovery_until_tick = self.tick + THREAT_RECOVERY_TICKS
            self.last_threat_tick = self.tick
            for fighter in fighters:
                self._wake(fighter.id, "threat_resolved")

    def _recover_injuries(self) -> None:
        for colonist in self.colonists:
            if colonist.pain > 0:
                colonist.pain = max(0.0, colonist.pain - 0.002)
            if colonist.mental_break and colonist.mood > MAJOR_BREAK + 0.1:
                colonist.mental_break = ""

    def _maybe_storyteller(self) -> None:
        if self.tick < self.recovery_until_tick:
            return
        if self.raid is not None:
            return
        # Wealth and damage history influence a single simple raid.
        wealth = (
            self.resources.get("wood", 0)
            + self.resources.get("steel", 0) * 2
            + len(self.structures) * 8
        )
        since = self.tick - self.last_threat_tick
        if since < INITIAL_THREAT_GRACE_TICKS:
            return
        chance = min(0.02, 0.002 + wealth / 50_000)
        if self.rng.random() < chance:
            self.raid = {
                "id": f"raid-{self.tick}",
                "hp": 3.0 + wealth / 40.0,
                "started_tick": self.tick,
                "x": MAP_WIDTH - 2,
                "y": MAP_HEIGHT // 2,
            }
            self.events.append({"tick": self.tick, "kind": "raid_started", "raid": dict(self.raid)})
            self.last_threat_tick = self.tick
            for colonist in self.colonists:
                self._wake(colonist.id, "threat")

    def _check_mental_breaks(self) -> None:
        for colonist in self.colonists:
            if colonist.mental_break:
                continue
            if colonist.mood >= MINOR_BREAK:
                colonist.low_mood_ticks = 0
                continue
            colonist.low_mood_ticks += 1
            if colonist.low_mood_ticks < BREAK_LOW_MOOD_TICKS:
                continue
            colonist.low_mood_ticks = 0
            if colonist.mood < EXTREME_BREAK:
                colonist.mental_break = self.rng.choice(["berserk", "catatonic", "give_up"])
            elif colonist.mood < MAJOR_BREAK:
                colonist.mental_break = self.rng.choice(["hide_in_room", "binge_food", "insulting_spree"])
            else:
                colonist.mental_break = self.rng.choice(["wander", "complain", "sad_wander"])
            colonist.current_job = None
            self.events.append(
                {
                    "tick": self.tick,
                    "kind": "mental_break",
                    "colonist_id": colonist.id,
                    "break": colonist.mental_break,
                }
            )
            self._wake(colonist.id, "mental_break")


__all__ = [
    "BREAK_LOW_MOOD_TICKS",
    "ColonySimulation",
    "JOB_ORDER",
    "MAP_HEIGHT",
    "MAP_WIDTH",
]
