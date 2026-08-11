"""Agent tool surface for the RimWorld activity plugin."""

from __future__ import annotations

TOOL_NAMES = (
    "rimworld.observe",
    "rimworld.inspect",
    "rimworld.act",
    "rimworld.speak",
)

TOOL_SCHEMAS = [
    {
        "name": "rimworld.observe",
        "description": "Observe colony-wide state for the assigned colonist.",
        "parameters": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "rimworld.inspect",
        "description": "Inspect one colonist, structure, or map cell.",
        "parameters": {
            "type": "object",
            "properties": {
                "target_type": {
                    "type": "string",
                    "enum": ["colonist", "structure", "cell"],
                },
                "target_id": {"type": "string"},
                "x": {"type": "integer"},
                "y": {"type": "integer"},
            },
            "required": ["target_type"],
            "additionalProperties": False,
        },
    },
    {
        "name": "rimworld.act",
        "description": (
            "Stage one colonist action. action_args formats: build requires "
            "{kind: bed|wall|door|table|campfire|workbench|storage, x: 0-47, y: 0-31}; "
            "set_priorities requires {priorities: {job: 1-4}}; set_job requires "
            "{job, target?}; social uses {other_id}; tend uses {patient_id}. "
            "choose_work, eat, sleep, and recreation use {}."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "set_job",
                        "choose_work",
                        "set_priorities",
                        "build",
                        "haul",
                        "eat",
                        "sleep",
                        "recreation",
                        "social",
                        "fight",
                        "tend",
                    ],
                },
                "action_args": {
                    "type": "object",
                    "description": "Arguments matching the selected action; use {} when none are required.",
                    "properties": {
                        "kind": {
                            "type": "string",
                            "enum": [
                                "bed",
                                "wall",
                                "door",
                                "table",
                                "campfire",
                                "workbench",
                                "storage",
                            ],
                        },
                        "x": {"type": "integer", "minimum": 0, "maximum": 47},
                        "y": {"type": "integer", "minimum": 0, "maximum": 31},
                        "priorities": {
                            "type": "object",
                            "description": (
                                "Map known jobs (fight, tend, construct, haul, eat, sleep, "
                                "recreation, social, work) to priority 1-4."
                            ),
                            "additionalProperties": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 4,
                            },
                        },
                        "job": {
                            "type": "string",
                            "enum": [
                                "fight",
                                "tend",
                                "construct",
                                "haul",
                                "eat",
                                "sleep",
                                "recreation",
                                "social",
                                "work",
                            ],
                        },
                        "target": {"type": "object"},
                        "other_id": {"type": "string"},
                        "patient_id": {"type": "string"},
                    },
                    "additionalProperties": True,
                },
            },
            "required": ["action"],
            "additionalProperties": False,
        },
    },
    {
        "name": "rimworld.speak",
        "description": "Speak a short in-character line in the room side chat.",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "minLength": 1, "maxLength": 500},
            },
            "required": ["text"],
            "additionalProperties": False,
        },
    },
]


def dispatch_tool(
    *,
    name: str,
    arguments: dict[str, object],
    colonist_id: str,
    simulation,
) -> dict[str, object]:
    if name not in TOOL_NAMES:
        raise ValueError(f"Unsupported RimWorld tool: {name}")
    if name == "rimworld.observe":
        return {"snapshot": simulation.snapshot(), "colonist_id": colonist_id}
    if name == "rimworld.inspect":
        target_type = str(arguments.get("target_type") or "")
        if target_type == "colonist":
            target_id = str(arguments.get("target_id") or colonist_id)
            colonist = next(
                item for item in simulation.snapshot()["colonists"] if item["id"] == target_id
            )
            return {"colonist": colonist}
        if target_type == "structure":
            structures = simulation.snapshot()["structures"]
            return {"structures": structures}
        return {
            "cell": {
                "x": int(arguments.get("x") or 0),
                "y": int(arguments.get("y") or 0),
            }
        }
    if name == "rimworld.act":
        return simulation.apply_act(
            colonist_id,
            str(arguments.get("action") or ""),
            arguments.get("action_args")
            if isinstance(arguments.get("action_args"), dict)
            else {},
        )
    return {"spoken": str(arguments.get("text") or "")[:500]}


__all__ = ["TOOL_NAMES", "TOOL_SCHEMAS", "dispatch_tool"]
