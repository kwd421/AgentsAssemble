from __future__ import annotations

import json
import math
import re
import time
import urllib.parse
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Callable

FLOW_ACTIONS: set[str] = {
    "speak",
    "wait",
    "ask",
    "challenge",
    "clarify",
    "summarize",
    "call_human",
}
FLOW_SPEAKING_ACTIONS = FLOW_ACTIONS - {"wait"}
FLOW_TERMINAL_EVENT_TYPES = {"finished", "stopped"}


@dataclass(frozen=True)
class FlowDecision:
    action: str
    message: str = ""
    reason: str = ""
    target_agent_id: str = ""


@dataclass(frozen=True)
class FlowOptions:
    duration_seconds: float = 180.0
    tick_interval: float = 2.0
    cooldown: float = 8.0
    max_agent_turns: int = 12
    max_total_turns: int = 30
    max_silence_seconds: float = 20.0

    def to_payload(self) -> dict[str, object]:
        return {
            "duration_seconds": self.duration_seconds,
            "tick_interval": self.tick_interval,
            "cooldown": self.cooldown,
            "max_agent_turns": self.max_agent_turns,
            "max_total_turns": self.max_total_turns,
            "max_silence_seconds": self.max_silence_seconds,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> FlowOptions:
        return cls(
            duration_seconds=_nonnegative_float(payload.get("duration_seconds"), cls.duration_seconds),
            tick_interval=_nonnegative_float(payload.get("tick_interval"), cls.tick_interval),
            cooldown=_nonnegative_float(payload.get("cooldown"), cls.cooldown),
            max_agent_turns=_nonnegative_int(payload.get("max_agent_turns"), cls.max_agent_turns),
            max_total_turns=_nonnegative_int(payload.get("max_total_turns"), cls.max_total_turns),
            max_silence_seconds=_nonnegative_float(payload.get("max_silence_seconds"), cls.max_silence_seconds),
        )


class LiveAgentFlowClient:
    def __init__(
        self,
        *,
        server: str,
        request_json: Callable[..., dict[str, object]],
        sleep_fn: Callable[[float], None] = time.sleep,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self.server = server
        self.request_json = request_json
        self.sleep_fn = sleep_fn
        self.now_fn = now_fn or (lambda: datetime.now(UTC))

    def run(self, *, meeting_id: str, topic: str, options: FlowOptions) -> dict[str, object]:
        payload = {
            "meeting_id": meeting_id,
            "topic": topic,
            **options.to_payload(),
        }
        result = self.request_json(
            _server_url(self.server, "/api/live-agent-flow/start"),
            method="POST",
            payload=payload,
        )
        deadline = self.now_fn() + timedelta(seconds=options.duration_seconds + max(options.tick_interval, 1.0) * 2)
        while True:
            flow = result.get("flow") if isinstance(result.get("flow"), dict) else {}
            if str(flow.get("status") or "") in {"finished", "stopped", "failed"}:
                return result
            if self.now_fn() >= deadline:
                return result
            self.sleep_fn(options.tick_interval)
            result = self.status(meeting_id=meeting_id)

    def status(self, *, meeting_id: str = "") -> dict[str, object]:
        query = urllib.parse.urlencode({"meeting_id": meeting_id}) if meeting_id else ""
        suffix = f"?{query}" if query else ""
        return self.request_json(_server_url(self.server, f"/api/live-agent-flow{suffix}"))


def parse_flow_decision(raw_output: str) -> FlowDecision:
    text = str(raw_output or "").strip()
    if not text:
        return FlowDecision(action="wait")
    payload = _parse_json_object(text)
    if isinstance(payload, dict):
        action = _flow_action(payload.get("action"))
        message = _one_line(payload.get("message"), limit=1200)
        if action == "wait":
            return FlowDecision(
                action="wait",
                message="",
                reason=_one_line(payload.get("reason"), limit=400),
                target_agent_id=_one_line(payload.get("target_agent_id"), limit=64),
            )
        if message:
            return FlowDecision(
                action=action,
                message=message,
                reason=_one_line(payload.get("reason"), limit=400),
                target_agent_id=_one_line(payload.get("target_agent_id"), limit=64),
            )
    return FlowDecision(action="speak", message=_one_line(text, limit=1200))


def active_flow_context(events: list[dict[str, object]], *, meeting_id: str = "") -> dict[str, object] | None:
    active: dict[str, object] | None = None
    scoped_meeting_id = str(meeting_id or "").strip()
    for event in events:
        flow_event_type = str(event.get("flow_event_type") or "").strip()
        flow_id = str(event.get("flow_id") or "").strip()
        if not flow_id:
            continue
        event_meeting_id = str(event.get("flow_meeting_id") or "").strip()
        if scoped_meeting_id and event_meeting_id != scoped_meeting_id:
            continue
        if flow_event_type == "started":
            active = event
            continue
        if active is not None and flow_id == str(active.get("flow_id") or "") and flow_event_type in FLOW_TERMINAL_EVENT_TYPES:
            active = None
    return active


def flow_context_options(flow_context: dict[str, object] | None) -> FlowOptions:
    if flow_context is None:
        return FlowOptions()
    return FlowOptions.from_payload(
        {
            "duration_seconds": flow_context.get("flow_duration_seconds"),
            "tick_interval": flow_context.get("flow_tick_interval"),
            "cooldown": flow_context.get("flow_cooldown"),
            "max_agent_turns": flow_context.get("flow_max_agent_turns"),
            "max_total_turns": flow_context.get("flow_max_total_turns"),
            "max_silence_seconds": flow_context.get("flow_max_silence_seconds"),
        }
    )


def flow_turn_count(events: list[dict[str, object]], *, flow_id: str, agent_id: str = "") -> int:
    count = 0
    for event in events:
        if str(event.get("flow_id") or "") != flow_id:
            continue
        if str(event.get("flow_action") or "") not in FLOW_SPEAKING_ACTIONS:
            continue
        if agent_id and str(event.get("actor_id") or "") != agent_id:
            continue
        count += 1
    return count


def flow_should_yield_for_fairness(
    events: list[dict[str, object]],
    *,
    flow_id: str,
    agent_id: str,
    participant_agent_ids: list[str],
    max_lead: int = 0,
) -> bool:
    """Return true when the current active participant should silently yield.

    The baseline is the caller-provided current participant set, not every
    historical speaker. The caller must include `agent_id` in
    `participant_agent_ids`; an empty baseline or a missing self id yields
    False so a broken roster does not deadlock a solo resident.
    """
    clean_flow_id = str(flow_id or "").strip()
    clean_agent_id = str(agent_id or "").strip()
    participant_ids = _unique_agent_ids(participant_agent_ids)
    if not clean_flow_id or not clean_agent_id or clean_agent_id not in participant_ids:
        return False
    counts = {participant_id: 0 for participant_id in participant_ids}
    for event in events:
        if str(event.get("flow_id") or "") != clean_flow_id:
            continue
        if str(event.get("flow_action") or "") not in FLOW_SPEAKING_ACTIONS:
            continue
        actor_id = str(event.get("actor_id") or "").strip()
        if actor_id in counts:
            counts[actor_id] += 1
    self_count = counts[clean_agent_id]
    minimum_count = min(counts.values())
    return self_count > minimum_count + max(0, int(max_lead))


def _unique_agent_ids(agent_ids: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for agent_id in agent_ids:
        clean_agent_id = str(agent_id or "").strip()
        if not clean_agent_id or clean_agent_id in seen:
            continue
        seen.add(clean_agent_id)
        unique.append(clean_agent_id)
    return unique


def _parse_json_object(text: str) -> object:
    candidate = _strip_markdown_json_fence(text)
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", candidate, flags=re.DOTALL)
        if not match:
            return None
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None


def _strip_markdown_json_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"\s*```$", "", stripped)
    return stripped.strip()


def _flow_action(value: object) -> str:
    action = str(value or "").strip().lower().replace("-", "_")
    return action if action in FLOW_ACTIONS else "speak"


def _one_line(value: object, *, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _nonnegative_float(value: object, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(default)
    if not math.isfinite(parsed):
        return float(default)
    return max(0.0, parsed)


def _nonnegative_int(value: object, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return int(default)
    return max(0, parsed)


def _server_url(server: str, path: str) -> str:
    return server.rstrip("/") + path
