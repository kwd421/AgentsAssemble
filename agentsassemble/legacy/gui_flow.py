"""Retained GUI projection for the disabled play/free-flow feature."""
from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from agentsassemble.legacy.live_agent.roster_queries import (
    live_agent_roster_with_admission_evidence,
)
from agentsassemble.legacy.live_agent.runtime.roster import (
    filter_live_agent_roster,
    safe_live_agent_roster_payload,
)
from agentsassemble.legacy.live_agent.state import (
    read_live_agents,
    update_live_agent_engagement,
)
from agentsassemble.legacy.meeting.core.events import ROOM_TOPIC_LIMIT, clean_lobby_text
from agentsassemble.legacy.meeting.records import live_agent_admission_details
from agentsassemble.legacy.meeting.support.lobby_queries import read_lobby
from agentsassemble.live_agent_flow import FLOW_TERMINAL_EVENT_TYPES, FlowOptions, flow_turn_count


def safe_live_agent_flow_agents(
    output_root: Path,
    *,
    meeting_id: str = "",
    quota_viewer: dict[str, object] | None = None,
) -> list[dict[str, object]]:
    payload = live_agent_roster_with_admission_evidence(
        output_root,
        {
            "agents": filter_live_agent_roster(
                read_live_agents(output_root),
                meeting_id=meeting_id,
            )
        },
    )
    safe_payload = safe_live_agent_roster_payload(payload, quota_viewer=quota_viewer)
    agents = safe_payload.get("agents")
    return agents if isinstance(agents, list) else []


class LegacyGuiFlowSupervisor:
    def __init__(
        self,
        output_root: Path,
        *,
        append_lobby_event: Callable[..., dict[str, object]],
    ) -> None:
        self.output_root = output_root
        self._append_lobby_event = append_lobby_event
        self._lock = threading.RLock()
        self._runs: dict[str, dict[str, object]] = {}

    def start(self, payload: dict[str, object]) -> dict[str, object]:
        raise RuntimeError("Play/free flow is disabled; use turn-based Agent Sessions.")

    def status(
        self,
        *,
        meeting_id: str = "",
        quota_viewer: dict[str, object] | None = None,
    ) -> dict[str, object]:
        clean_meeting_id = clean_lobby_text(meeting_id, limit=128)
        events = read_lobby(self.output_root, meeting_id=clean_meeting_id)
        with self._lock:
            run = self._selected_run(clean_meeting_id)
            if run is None:
                flow = restored_flow_state(events, meeting_id=clean_meeting_id)
                flow_meeting_id = (
                    clean_lobby_text((flow or {}).get("meeting_id"), limit=128)
                    or clean_meeting_id
                )
                if not clean_meeting_id and flow_meeting_id:
                    events = read_lobby(self.output_root, meeting_id=flow_meeting_id)
                return {
                    "flow": flow or {"status": "idle"},
                    "agents": safe_live_agent_flow_agents(
                        self.output_root,
                        meeting_id=flow_meeting_id,
                        quota_viewer=quota_viewer,
                    ),
                    "events": events,
                    "flow_events": flow_events_for_state(events, flow),
                }
            self._refresh_counts_locked(run)
            flow = self._public_state(run)
            flow_meeting_id = clean_lobby_text(flow.get("meeting_id"), limit=128)
            if not clean_meeting_id and flow_meeting_id:
                events = read_lobby(self.output_root, meeting_id=flow_meeting_id)
            return {
                "flow": flow,
                "agents": safe_live_agent_flow_agents(
                    self.output_root,
                    meeting_id=flow_meeting_id or clean_meeting_id,
                    quota_viewer=quota_viewer,
                ),
                "events": events,
                "flow_events": flow_events_for_state(events, flow),
            }

    def stop(self, payload: dict[str, object]) -> dict[str, object]:
        meeting_id = clean_lobby_text(payload.get("meeting_id"), limit=128)
        with self._lock:
            run = self._selected_run(meeting_id)
            if run is None:
                return {"flow": {"status": "idle"}}
            stop_event = run.get("stop_event")
            if isinstance(stop_event, threading.Event):
                stop_event.set()
        thread = run.get("thread")
        if isinstance(thread, threading.Thread):
            thread.join(timeout=2)
        with self._lock:
            if self._public_state(run).get("status") == "running":
                self._finish_locked(run, "stopped")
            events = read_lobby(self.output_root)
            flow = self._public_state(run)
            return {
                "flow": flow,
                "agents": safe_live_agent_flow_agents(self.output_root),
                "events": events,
                "flow_events": flow_events_for_state(events, flow),
            }

    def _run_flow(self, meeting_id: str) -> None:
        try:
            while True:
                with self._lock:
                    run = self._runs.get(meeting_id)
                    if run is None:
                        return
                    state = run["state"] if isinstance(run.get("state"), dict) else {}
                    options = (
                        run["options"]
                        if isinstance(run.get("options"), FlowOptions)
                        else FlowOptions()
                    )
                    stop_event = run.get("stop_event")
                    if not isinstance(stop_event, threading.Event):
                        return
                    if stop_event.is_set():
                        self._finish_locked(run, "stopped")
                        return
                    self._refresh_counts_locked(run)
                    if self._flow_time_expired(state) or self._flow_turn_budget_exhausted(state):
                        self._finish_locked(run, "finished")
                        return
                    self._mark_silence_check_locked(run)
                if stop_event.wait(max(0.01, options.tick_interval)):
                    continue
        except Exception:
            with self._lock:
                run = self._runs.get(meeting_id)
                if run is None:
                    return
                try:
                    self._finish_locked(run, "stopped")
                except Exception:
                    state = run.get("state")
                    if isinstance(state, dict):
                        state["status"] = "stopped"
                    self._restore_previous_modes(run)

    def _selected_run(self, meeting_id: str) -> dict[str, object] | None:
        clean_meeting_id = clean_lobby_text(meeting_id, limit=128)
        if clean_meeting_id:
            return self._runs.get(clean_meeting_id)
        if not self._runs:
            return None
        return next(reversed(self._runs.values()))

    def _set_bound_agents_to_flow(
        self,
        meeting: dict[str, object],
        meeting_id: str,
    ) -> tuple[dict[str, str], int]:
        previous_modes: dict[str, str] = {}
        for agent in read_live_agents(self.output_root):
            agent_id = clean_lobby_text(agent.get("agent_id"), limit=64)
            if not agent_id:
                continue
            if clean_lobby_text(agent.get("meeting_id"), limit=128) != meeting_id:
                continue
            admission = live_agent_admission_details(meeting, agent, agent_id=agent_id)
            if admission.get("host_approved_binding") is not True:
                continue
            previous_modes[agent_id] = (
                clean_lobby_text(agent.get("engagement_mode"), limit=64) or "mentioned"
            )
            update_live_agent_engagement(self.output_root, agent_id, "flow")
        return previous_modes, len(previous_modes)

    def _restore_previous_modes(self, run: dict[str, object]) -> None:
        previous_modes = run.get("previous_modes")
        if not isinstance(previous_modes, dict):
            return
        for agent_id, mode in previous_modes.items():
            try:
                update_live_agent_engagement(self.output_root, str(agent_id), str(mode))
            except ValueError:
                continue

    def _refresh_counts_locked(self, run: dict[str, object]) -> None:
        state = run["state"] if isinstance(run.get("state"), dict) else {}
        flow_id = clean_lobby_text(state.get("flow_id"), limit=128)
        if not flow_id:
            return
        try:
            events = read_lobby(self.output_root, limit=None)
        except OSError:
            return
        state["total_turns"] = flow_turn_count(events, flow_id=flow_id)
        last_activity = latest_flow_activity_at(events, flow_id=flow_id) or clean_lobby_text(
            state.get("started_at"),
            limit=64,
        )
        if last_activity:
            state["last_activity_at"] = last_activity

    def _flow_time_expired(self, state: dict[str, object]) -> bool:
        deadline = parse_iso_datetime(state.get("deadline_at"))
        return deadline is not None and datetime.now(UTC) >= deadline

    def _flow_turn_budget_exhausted(self, state: dict[str, object]) -> bool:
        max_total = int(state.get("max_total_turns") or 0)
        total = int(state.get("total_turns") or 0)
        return bool(max_total and total >= max_total)

    def _mark_silence_check_locked(self, run: dict[str, object]) -> None:
        state = run["state"] if isinstance(run.get("state"), dict) else {}
        max_silence = float(state.get("max_silence_seconds") or 0)
        if max_silence <= 0:
            return
        last_activity = parse_iso_datetime(state.get("last_activity_at"))
        if last_activity is None:
            return
        now = datetime.now(UTC)
        if (now - last_activity).total_seconds() < max_silence:
            return
        last_silence_check_at = run.get("last_silence_check_at")
        if (
            isinstance(last_silence_check_at, datetime)
            and (now - last_silence_check_at).total_seconds() < max_silence
        ):
            return
        run["last_silence_check_at"] = now

    def _finish_locked(self, run: dict[str, object], status: str) -> None:
        state = run["state"] if isinstance(run.get("state"), dict) else {}
        if state.get("status") != "running":
            return
        self._refresh_counts_locked(run)
        state["status"] = status
        state["finished_at"] = datetime.now(UTC).isoformat()
        self._append_lobby_event(
            self.output_root,
            {
                "name": "Play Mode",
                "side": "other",
                "kind": "message",
                "message": "시간제 자유토론 종료" if status == "finished" else "시간제 자유토론 중지",
                "actor_id": "flow",
                **self._flow_event_metadata(state, event_type=status),
            },
            allow_flow_metadata=True,
        )
        self._restore_previous_modes(run)

    def _flow_event_metadata(
        self,
        state: dict[str, object],
        *,
        event_type: str,
    ) -> dict[str, object]:
        return {
            "flow_id": state.get("flow_id") or "",
            "flow_meeting_id": state.get("meeting_id") or "",
            "flow_event_type": event_type,
            "flow_status": state.get("status") or "",
            "flow_topic": state.get("topic") or "",
            "flow_policy": state.get("policy") or "",
            "flow_duration_seconds": int(float(state.get("duration_seconds") or 0)),
            "flow_tick_interval": int(float(state.get("tick_interval") or 0)),
            "flow_cooldown": int(float(state.get("cooldown") or 0)),
            "flow_max_agent_turns": int(state.get("max_agent_turns") or 0),
            "flow_max_total_turns": int(state.get("max_total_turns") or 0),
            "flow_max_silence_seconds": int(float(state.get("max_silence_seconds") or 0)),
            "flow_total_turns": int(state.get("total_turns") or 0),
            "flow_agent_count": int(state.get("agent_count") or 0),
            "flow_started_at": state.get("started_at") or "",
            "flow_deadline_at": state.get("deadline_at") or "",
        }

    def _public_state(self, run: dict[str, object]) -> dict[str, object]:
        state = dict(run["state"] if isinstance(run.get("state"), dict) else {})
        deadline = parse_iso_datetime(state.get("deadline_at"))
        if deadline is not None and state.get("status") == "running":
            state["remaining_seconds"] = max(
                0.0,
                (deadline - datetime.now(UTC)).total_seconds(),
            )
        return state


def latest_flow_activity_at(events: list[dict[str, object]], *, flow_id: str) -> str:
    for event in reversed(events):
        if str(event.get("flow_id") or "") != flow_id:
            continue
        if str(event.get("flow_action") or "") or str(event.get("flow_event_type") or "") in {
            "started",
            "nudge",
        }:
            return clean_lobby_text(event.get("created_at"), limit=64)
    return ""


def restored_flow_state(
    events: list[dict[str, object]],
    *,
    meeting_id: str = "",
) -> dict[str, object] | None:
    context = latest_flow_context(events, meeting_id=meeting_id)
    if context is None:
        return None
    flow_id = clean_lobby_text(context.get("flow_id"), limit=128)
    if not flow_id:
        return None
    event_type = clean_lobby_text(context.get("flow_event_type"), limit=64)
    status = clean_lobby_text(context.get("flow_status"), limit=64)
    if event_type == "started":
        status = "running"
    elif event_type in FLOW_TERMINAL_EVENT_TYPES:
        status = event_type
    status = status or "running"
    deadline = parse_iso_datetime(context.get("flow_deadline_at"))
    if status == "running" and deadline is not None and datetime.now(UTC) >= deadline:
        status = "finished"
    state: dict[str, object] = {
        "flow_id": flow_id,
        "meeting_id": clean_lobby_text(context.get("flow_meeting_id"), limit=128),
        "topic": clean_lobby_text(context.get("flow_topic"), limit=ROOM_TOPIC_LIMIT),
        "policy": clean_lobby_text(context.get("flow_policy"), limit=64) or "turn_based_floor",
        "status": status,
        "started_at": clean_lobby_text(context.get("flow_started_at"), limit=64),
        "deadline_at": clean_lobby_text(context.get("flow_deadline_at"), limit=64),
        "duration_seconds": _nonnegative_float(context.get("flow_duration_seconds"), 0.0),
        "tick_interval": _nonnegative_float(context.get("flow_tick_interval"), 0.0),
        "cooldown": _nonnegative_float(context.get("flow_cooldown"), 0.0),
        "max_agent_turns": _nonnegative_int(context.get("flow_max_agent_turns"), 0),
        "max_total_turns": _nonnegative_int(context.get("flow_max_total_turns"), 0),
        "max_silence_seconds": _nonnegative_float(
            context.get("flow_max_silence_seconds"),
            0.0,
        ),
        "agent_count": _nonnegative_int(context.get("flow_agent_count"), 0),
        "total_turns": flow_turn_count(events, flow_id=flow_id),
        "last_activity_at": latest_flow_activity_at(events, flow_id=flow_id)
        or clean_lobby_text(context.get("created_at"), limit=64),
    }
    if status == "running" and deadline is not None:
        state["remaining_seconds"] = max(
            0.0,
            (deadline - datetime.now(UTC)).total_seconds(),
        )
    if event_type in FLOW_TERMINAL_EVENT_TYPES:
        state["finished_at"] = clean_lobby_text(context.get("created_at"), limit=64)
    return state


def latest_flow_context(
    events: list[dict[str, object]],
    *,
    meeting_id: str = "",
) -> dict[str, object] | None:
    scoped_meeting_id = clean_lobby_text(meeting_id, limit=128)
    latest: dict[str, object] | None = None
    latest_flow_id = ""
    for event in events:
        flow_id = clean_lobby_text(event.get("flow_id"), limit=128)
        if not flow_id:
            continue
        event_meeting_id = clean_lobby_text(event.get("flow_meeting_id"), limit=128)
        if scoped_meeting_id and event_meeting_id != scoped_meeting_id:
            continue
        event_type = clean_lobby_text(event.get("flow_event_type"), limit=64)
        if event_type == "started":
            latest = event
            latest_flow_id = flow_id
            continue
        if event_type in FLOW_TERMINAL_EVENT_TYPES and latest is not None and flow_id == latest_flow_id:
            latest = event
    return latest


def flow_events_for_state(
    events: list[dict[str, object]],
    flow: dict[str, object] | None,
) -> list[dict[str, object]]:
    if not isinstance(flow, dict):
        return []
    flow_id = clean_lobby_text(flow.get("flow_id"), limit=128)
    if not flow_id:
        return []
    return [
        event
        for event in events
        if clean_lobby_text(event.get("flow_id"), limit=128) == flow_id
    ]


def parse_iso_datetime(value: object) -> datetime | None:
    text = clean_lobby_text(value, limit=64)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _nonnegative_int(value: object, default: int) -> int:
    try:
        parsed = int(value)
    except (OverflowError, TypeError, ValueError):
        return default
    return max(0, parsed)


def _nonnegative_float(value: object, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if parsed != parsed or parsed in {float("inf"), float("-inf")}:
        return default
    return max(0.0, parsed)
