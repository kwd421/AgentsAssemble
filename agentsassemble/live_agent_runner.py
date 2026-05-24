from __future__ import annotations

import json
import math
import re
import subprocess
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from agentsassemble.codex_resident import default_codex_resident_command
from agentsassemble.adapters.remote_bridge import RemoteBridgeAdapter
from agentsassemble.live_agent_turns import (
    is_official_turn_cancellation_event,
    is_official_turn_reply_event,
    is_review_checkpoint_reply_event,
)
from agentsassemble.live_agent_flow import (
    FlowDecision,
    FLOW_SPEAKING_ACTIONS,
    FLOW_TERMINAL_EVENT_TYPES,
    active_flow_context,
    flow_context_options,
    flow_turn_count,
    parse_flow_decision,
)
from agentsassemble.models import ENGAGEMENT_MODES, ProviderConfig, Role
from agentsassemble.remote_bridge_config import (
    remote_bridge_auth_ref_available,
    remote_bridge_auth_ref_value,
    remote_bridge_endpoint_error,
)


SUPPORTED_RESIDENT_CONNECTION_KINDS = (
    "local_cli",
    "live_session",
    "terminal_session",
    "remote_bridge",
    "self_service",
)


@dataclass(frozen=True)
class ResidentAgentConfig:
    server: str
    agent_id: str
    display_name: str
    provider_kind: str
    connection_kind: str
    session_id: str
    endpoint: str
    auth_ref: str
    meeting_id: str
    engagement_mode: str
    command: list[str]
    timeout_seconds: int
    poll_interval: float
    heartbeat_interval: float
    cooldown: float
    max_chain_depth: int
    max_ticks: int = 0
    terminal_idle_timeout: float = 0.35


class LiveAgentRunner:
    def __init__(
        self,
        config: ResidentAgentConfig,
        *,
        request_json: Callable[..., dict[str, object]],
        command_runner: Callable[..., str],
        sleep_fn: Callable[[float], None],
        now_fn: Callable[[], datetime] | None = None,
        stop_event: threading.Event | None = None,
    ) -> None:
        self.config = config
        self.request_json = request_json
        self.command_runner = command_runner
        self.sleep_fn = sleep_fn
        self.now_fn = now_fn or (lambda: datetime.now(UTC))
        self.stop_event = stop_event or threading.Event()
        self.last_observed_event_id = ""
        self.last_observed_live_event_id = ""
        self.last_reply_at: datetime | None = None
        self.last_error_at: datetime | None = None
        self.last_error = ""
        self.last_heartbeat_at: datetime | None = None
        self.seen_room_snapshot = False
        self.transient_room_error_active = False

    def run(self) -> int:
        self._register()
        self._heartbeat("online")
        replies = 0
        ticks = 0
        try:
            while not self.stop_event.is_set():
                ticks += 1
                replies += self.tick()
                if self.config.max_ticks and ticks >= self.config.max_ticks:
                    break
                self.sleep_fn(self.config.poll_interval)
        finally:
            self._heartbeat_final_offline()
        return replies

    def tick(self) -> int:
        try:
            room = self._room()
        except Exception as error:
            if not self.seen_room_snapshot or self.stop_event.is_set():
                raise
            self.last_error = _safe_room_read_error(error)
            self.transient_room_error_active = True
            self._heartbeat_due_safely("error", last_error=self.last_error, **self._cursor_metadata())
            return 0
        engagement_mode = _runtime_engagement_mode(self.config, room)
        if engagement_mode == "moderator_called":
            self._observe_lobby_cursor(_lobby_events(room))
            events = _live_events(room)
            candidate = official_turn_request_candidate(
                events,
                self.config.agent_id,
                self.last_observed_live_event_id,
            )
            if candidate is None:
                self._advance_live_cursor(events)
                self._heartbeat_if_due()
                return 0
            if self._in_cooldown():
                self._heartbeat_if_due()
                return 0
            if self._in_failure_backoff():
                self._heartbeat_if_due()
                return 0

            generated = self._generate_reply(
                candidate,
                official_turn_prompt(self.config, room, candidate),
                cursor_field="last_observed_live_event_id",
            )
            if generated is None:
                return 0
            source_event_id, reply = generated
            try:
                response = self.request_json(
                    _server_url(self.config.server, f"/api/live-agents/{_quote(self.config.agent_id)}/official-turn"),
                    method="POST",
                    payload={
                        "meeting_id": _official_turn_meeting_id(self.config, room, candidate),
                        "source_event_id": source_event_id,
                        "content": reply,
                        "role_id": str(candidate.get("role_id") or self.config.agent_id),
                        "display_name": str(candidate.get("display_name") or self.config.display_name or self.config.agent_id),
                        "turn_id": str(candidate.get("turn_id") or ""),
                        "turn_index": _optional_int(candidate.get("turn_index")),
                    },
                )
            except Exception as error:
                self._record_reply_post_error(
                    error,
                    cursor_field="last_observed_live_event_id",
                    observed_event_id=source_event_id,
                )
                raise
            self._record_reply_success(
                response.get("event"),
                cursor_field="last_observed_live_event_id",
                observed_event_id=source_event_id,
            )
            return 1

        if engagement_mode == "flow":
            return self._tick_flow(room)

        events = _lobby_events(room)
        candidate = event_reply_candidate(
            events,
            self.config.agent_id,
            self.config.display_name,
            self.last_observed_event_id,
            max_chain_depth=self.config.max_chain_depth,
            engagement_mode=engagement_mode,
        )
        if candidate is None:
            self._advance_cursor(events)
            self._heartbeat_if_due()
            return 0
        if self._in_cooldown():
            self._heartbeat_if_due()
            return 0
        if self._in_failure_backoff():
            self._heartbeat_if_due()
            return 0

        generated = self._generate_reply(
            candidate,
            delegate_prompt(self.config, room, candidate),
            cursor_field="last_observed_event_id",
        )
        if generated is None:
            return 0
        source_event_id, reply = generated

        source_depth = _chain_depth(candidate)
        try:
            response = self.request_json(
                _server_url(self.config.server, f"/api/live-agents/{_quote(self.config.agent_id)}/lobby"),
                method="POST",
                payload={
                    "message": reply,
                    "kind": "message",
                    "actor_id": self.config.agent_id,
                    "source_event_id": source_event_id,
                    "auto_chain_depth": source_depth + 1,
                },
            )
        except Exception as error:
            self._record_reply_post_error(
                error,
                cursor_field="last_observed_event_id",
                observed_event_id=source_event_id,
            )
            raise
        self._record_reply_success(
            response.get("event"),
            cursor_field="last_observed_event_id",
            observed_event_id=source_event_id,
        )
        return 1

    def _tick_flow(self, room: dict[str, object]) -> int:
        live_events = _live_events(room)
        official_candidate = official_turn_request_candidate(
            live_events,
            self.config.agent_id,
            self.last_observed_live_event_id,
        )
        if official_candidate is not None:
            if self._in_cooldown():
                self._heartbeat_if_due()
                return 0
            if self._in_failure_backoff():
                self._heartbeat_if_due()
                return 0
            generated = self._generate_reply(
                official_candidate,
                official_turn_prompt(self.config, room, official_candidate),
                cursor_field="last_observed_live_event_id",
            )
            if generated is None:
                return 0
            source_event_id, reply = generated
            try:
                response = self.request_json(
                    _server_url(self.config.server, f"/api/live-agents/{_quote(self.config.agent_id)}/official-turn"),
                    method="POST",
                    payload={
                        "meeting_id": _official_turn_meeting_id(self.config, room, official_candidate),
                        "source_event_id": source_event_id,
                        "content": reply,
                        "role_id": str(official_candidate.get("role_id") or self.config.agent_id),
                        "display_name": str(official_candidate.get("display_name") or self.config.display_name or self.config.agent_id),
                        "turn_id": str(official_candidate.get("turn_id") or ""),
                        "turn_index": _optional_int(official_candidate.get("turn_index")),
                    },
                )
            except Exception as error:
                self._record_reply_post_error(
                    error,
                    cursor_field="last_observed_live_event_id",
                    observed_event_id=source_event_id,
                )
                raise
            self._record_reply_success(
                response.get("event"),
                cursor_field="last_observed_live_event_id",
                observed_event_id=source_event_id,
            )
            return 1
        self._advance_live_cursor(live_events)

        events = _lobby_events(room)
        meeting_id = _flow_room_meeting_id(self.config, room)
        candidate = flow_event_candidate(
            events,
            self.config.agent_id,
            self.config.display_name,
            self.last_observed_event_id,
            max_chain_depth=self.config.max_chain_depth,
            meeting_id=meeting_id,
        )
        if candidate is None:
            self._advance_cursor(events)
            self._heartbeat_if_due()
            return 0
        flow_context = active_flow_context(events, meeting_id=meeting_id) or {}
        flow_options = flow_context_options(flow_context)
        if self._in_cooldown(flow_options.cooldown):
            self._heartbeat_if_due()
            return 0
        if self._in_failure_backoff():
            self._heartbeat_if_due()
            return 0

        generated = self._generate_flow_decision(
            candidate,
            flow_decision_prompt(self.config, room, candidate),
        )
        if generated is None:
            return 0
        source_event_id, decision = generated
        flow_id = str(flow_context.get("flow_id") or "")
        if flow_id and not self._flow_still_active(flow_id, meeting_id):
            self._record_flow_wait_success(source_event_id)
            return 0
        if decision.action == "wait" or not decision.message:
            self._record_flow_wait_success(source_event_id)
            return 0

        source_depth = _chain_depth(candidate)
        try:
            response = self.request_json(
                _server_url(self.config.server, f"/api/live-agents/{_quote(self.config.agent_id)}/lobby"),
                method="POST",
                payload={
                    "message": decision.message,
                    "kind": "message",
                    "actor_id": self.config.agent_id,
                    "source_event_id": source_event_id,
                    "auto_chain_depth": source_depth + 1,
                    "flow_id": str(flow_context.get("flow_id") or ""),
                    "flow_meeting_id": meeting_id,
                    "flow_action": decision.action,
                    "flow_reason": decision.reason,
                    "target_agent_id": decision.target_agent_id,
                },
            )
        except Exception as error:
            self._record_reply_post_error(
                error,
                cursor_field="last_observed_event_id",
                observed_event_id=source_event_id,
            )
            raise
        self._record_reply_success(
            response.get("event"),
            cursor_field="last_observed_event_id",
            observed_event_id=source_event_id,
        )
        return 1

    def _generate_reply(
        self,
        candidate: dict[str, object],
        prompt: str,
        *,
        cursor_field: str,
    ) -> tuple[str, str] | None:
        source_event_id = str(candidate.get("id") or "")
        self._set_cursor(cursor_field, source_event_id)
        self._heartbeat_due_safely("working", **self._cursor_metadata(cursor_field, source_event_id))
        try:
            reply = self._run_command_with_working_heartbeats(
                self.config.command,
                prompt,
                source_event_id=source_event_id,
                cursor_field=cursor_field,
                timeout_seconds=self.config.timeout_seconds,
            ).strip()
            if not reply:
                raise ValueError("Delegate command returned an empty reply.")
        except Exception as error:
            if self.stop_event.is_set():
                return None
            self.transient_room_error_active = False
            self.last_error = _safe_provider_command_error(error)
            self.last_error_at = self.now_fn()
            self._heartbeat_due_safely(
                "error",
                last_error=self.last_error,
                **self._cursor_metadata(cursor_field, source_event_id),
            )
            return None
        return source_event_id, reply

    def _generate_flow_decision(
        self,
        candidate: dict[str, object],
        prompt: str,
    ) -> tuple[str, FlowDecision] | None:
        source_event_id = str(candidate.get("id") or "")
        self._set_cursor("last_observed_event_id", source_event_id)
        self._heartbeat_due_safely("working", **self._cursor_metadata("last_observed_event_id", source_event_id))
        try:
            raw_output = self._run_command_with_working_heartbeats(
                self.config.command,
                prompt,
                source_event_id=source_event_id,
                cursor_field="last_observed_event_id",
                timeout_seconds=self.config.timeout_seconds,
            )
        except Exception as error:
            if self.stop_event.is_set():
                return None
            self.transient_room_error_active = False
            self.last_error = _safe_provider_command_error(error)
            self.last_error_at = self.now_fn()
            self._heartbeat_due_safely(
                "error",
                last_error=self.last_error,
                **self._cursor_metadata("last_observed_event_id", source_event_id),
            )
            return None
        decision = parse_flow_decision(raw_output)
        return source_event_id, decision

    def _flow_still_active(self, flow_id: str, meeting_id: str) -> bool:
        try:
            room = self._room()
        except Exception:
            return True
        events = _lobby_events(room)
        for event in reversed(events):
            if str(event.get("flow_id") or "") != flow_id:
                continue
            event_meeting_id = str(event.get("flow_meeting_id") or "").strip()
            if meeting_id and event_meeting_id and event_meeting_id != meeting_id:
                continue
            if str(event.get("flow_event_type") or "") in FLOW_TERMINAL_EVENT_TYPES:
                return False
            break
        current_flow = active_flow_context(events, meeting_id=meeting_id)
        if current_flow is None:
            return True
        return str((current_flow or {}).get("flow_id") or "") == flow_id

    def _record_flow_wait_success(self, source_event_id: str) -> None:
        self._set_cursor("last_observed_event_id", source_event_id)
        self.last_reply_at = self.now_fn()
        self.last_error_at = None
        self.last_error = ""
        self.transient_room_error_active = False
        self._heartbeat_due_safely(
            "online",
            last_reply_at=self.last_reply_at.isoformat(),
            last_error="",
            **self._cursor_metadata("last_observed_event_id", source_event_id),
        )

    def _record_reply_success(
        self,
        event_payload: object,
        *,
        cursor_field: str,
        observed_event_id: str | None = None,
    ) -> None:
        event = event_payload if isinstance(event_payload, dict) else {}
        if observed_event_id:
            self._set_cursor(cursor_field, observed_event_id)
        elif event.get("id"):
            self._set_cursor(cursor_field, str(event["id"]))
        self.last_reply_at = self.now_fn()
        self.last_error_at = None
        self.last_error = ""
        self.transient_room_error_active = False
        self._heartbeat_due_safely(
            "online",
            last_reply_at=self.last_reply_at.isoformat(),
            last_error="",
            **self._cursor_metadata(),
        )

    def _record_reply_post_error(
        self,
        error: Exception,
        *,
        cursor_field: str,
        observed_event_id: str,
    ) -> None:
        if self.stop_event.is_set():
            return
        self.transient_room_error_active = False
        self.last_error = _safe_reply_post_error(error)
        self.last_error_at = self.now_fn()
        self._heartbeat_due_safely(
            "error",
            last_error=self.last_error,
            **self._cursor_metadata(cursor_field, observed_event_id),
        )

    def _register(self) -> None:
        response = self.request_json(
            _server_url(self.config.server, "/api/live-agents"),
            method="POST",
            payload={
                "agent_id": self.config.agent_id,
                "display_name": self.config.display_name,
                "provider_kind": self.config.provider_kind,
                "connection_kind": self.config.connection_kind,
                "session_id": self._current_session_id(),
                "endpoint": self.config.endpoint,
                "meeting_id": self.config.meeting_id,
                "engagement_mode": self.config.engagement_mode,
                "capabilities": ["room_chat", "mentions"],
            },
        )
        self._restore_agent_snapshot(response.get("agent"))

    def _heartbeat(self, status: str, **metadata: object) -> None:
        payload = {"status": status, **metadata}
        session_id = self._current_session_id()
        if session_id:
            payload.setdefault("session_id", session_id)
        self.request_json(
            _server_url(self.config.server, f"/api/live-agents/{_quote(self.config.agent_id)}/heartbeat"),
            method="POST",
            payload=payload,
        )
        self.last_heartbeat_at = self.now_fn()

    def _current_session_id(self) -> str:
        runner_session_id = str(getattr(self.command_runner, "session_id", "") or "").strip()
        return runner_session_id or str(self.config.session_id or "").strip()

    def _heartbeat_final_offline(self) -> None:
        try:
            self._heartbeat("offline", **self._cursor_metadata())
        except Exception:
            return

    def _heartbeat_if_due(self) -> None:
        if self.config.heartbeat_interval <= 0:
            return
        if self.last_heartbeat_at is None:
            self._heartbeat_due_safely("online", **self._cursor_metadata())
            return
        elapsed = (self.now_fn() - self.last_heartbeat_at).total_seconds()
        if elapsed >= self.config.heartbeat_interval:
            if self._in_failure_backoff():
                self._heartbeat_due_safely("error", last_error=self.last_error, **self._cursor_metadata())
                return
            self._heartbeat_due_safely("online", **self._cursor_metadata())

    def _heartbeat_due_safely(self, status: str, **metadata: object) -> None:
        try:
            self._heartbeat(status, **metadata)
        except Exception:
            return

    def _room(self) -> dict[str, object]:
        room = self.request_json(_server_url(self.config.server, f"/api/live-agents/{_quote(self.config.agent_id)}/room"))
        self._restore_agent_snapshot(room.get("agent"))
        self.seen_room_snapshot = True
        self._clear_transient_room_error_if_needed()
        return room

    def _clear_transient_room_error_if_needed(self) -> None:
        if not self.transient_room_error_active:
            return
        try:
            self._heartbeat("online", last_error="", **self._cursor_metadata())
        except Exception:
            return
        self.last_error = ""
        self.transient_room_error_active = False

    def _restore_agent_snapshot(self, agent: object) -> None:
        self._restore_observed_cursor(agent)
        self._restore_command_runner_session_id(agent)

    def _restore_observed_cursor(self, agent: object) -> None:
        if not isinstance(agent, dict):
            return
        agent_id = str(agent.get("agent_id") or "")
        if agent_id != self.config.agent_id:
            return
        cursor = str(agent.get("last_observed_event_id") or "").strip()
        if cursor and not self.last_observed_event_id:
            self.last_observed_event_id = cursor
        live_cursor = str(agent.get("last_observed_live_event_id") or "").strip()
        if live_cursor and not self.last_observed_live_event_id:
            self.last_observed_live_event_id = live_cursor

    def _restore_command_runner_session_id(self, agent: object) -> None:
        if not isinstance(agent, dict):
            return
        agent_id = str(agent.get("agent_id") or "")
        if agent_id != self.config.agent_id:
            return
        if self._current_session_id():
            return
        session_id = str(agent.get("session_id") or "").strip()
        if session_id and hasattr(self.command_runner, "session_id"):
            setattr(self.command_runner, "session_id", session_id)

    def _advance_cursor(self, events: list[dict[str, object]]) -> None:
        latest_id = _latest_event_id(events)
        if latest_id and latest_id != self.last_observed_event_id:
            self.last_observed_event_id = latest_id
            self._heartbeat_due_safely("online", last_observed_event_id=latest_id)

    def _observe_lobby_cursor(self, events: list[dict[str, object]]) -> None:
        latest_id = _latest_event_id(events)
        if latest_id and latest_id != self.last_observed_event_id:
            self.last_observed_event_id = latest_id

    def _advance_live_cursor(self, events: list[dict[str, object]]) -> None:
        latest_id = _latest_event_id(events)
        if latest_id and latest_id != self.last_observed_live_event_id:
            self.last_observed_live_event_id = latest_id
            self._heartbeat_due_safely("online", **self._cursor_metadata())

    def _set_cursor(self, cursor_field: str, event_id: str) -> None:
        if cursor_field == "last_observed_live_event_id":
            self.last_observed_live_event_id = event_id
            return
        self.last_observed_event_id = event_id

    def _cursor_metadata(self, cursor_field: str | None = None, event_id: str | None = None) -> dict[str, object]:
        lobby_cursor = self.last_observed_event_id
        live_cursor = self.last_observed_live_event_id
        if cursor_field == "last_observed_event_id" and event_id is not None:
            lobby_cursor = event_id
        if cursor_field == "last_observed_live_event_id" and event_id is not None:
            live_cursor = event_id
        metadata: dict[str, object] = {}
        if lobby_cursor:
            metadata["last_observed_event_id"] = lobby_cursor
        if live_cursor:
            metadata["last_observed_live_event_id"] = live_cursor
        return metadata

    def _in_cooldown(self, cooldown_seconds: float | None = None) -> bool:
        cooldown = self.config.cooldown if cooldown_seconds is None else float(cooldown_seconds)
        if self.last_reply_at is None or cooldown <= 0:
            return False
        return (self.now_fn() - self.last_reply_at).total_seconds() < cooldown

    def _in_failure_backoff(self) -> bool:
        if self.last_error_at is None or self.config.cooldown <= 0:
            return False
        return (self.now_fn() - self.last_error_at).total_seconds() < self.config.cooldown

    def _run_command_with_working_heartbeats(
        self,
        command: list[str],
        prompt: str,
        *,
        source_event_id: str,
        cursor_field: str,
        timeout_seconds: int,
    ) -> str:
        heartbeat_stop = threading.Event()
        heartbeat_thread = self._start_working_heartbeat_loop(source_event_id, heartbeat_stop, cursor_field=cursor_field)
        try:
            return self.command_runner(command, prompt, timeout_seconds=timeout_seconds)
        finally:
            heartbeat_stop.set()
            if heartbeat_thread is not None:
                heartbeat_thread.join()

    def _start_working_heartbeat_loop(
        self,
        source_event_id: str,
        stop_event: threading.Event,
        *,
        cursor_field: str,
    ) -> threading.Thread | None:
        if self.config.heartbeat_interval <= 0:
            return None
        interval = max(0.01, self.config.heartbeat_interval)

        def keep_working_fresh() -> None:
            while not stop_event.wait(interval):
                if self.stop_event.is_set():
                    return
                try:
                    self._heartbeat("working", **self._cursor_metadata(cursor_field, source_event_id))
                except Exception:
                    return

        thread = threading.Thread(
            target=keep_working_fresh,
            daemon=True,
            name=f"AgentsAssembleWorkingHeartbeat-{self.config.agent_id}",
        )
        thread.start()
        return thread


class RemoteBridgeResidentCommandRunner:
    def __init__(
        self,
        config: ResidentAgentConfig,
        *,
        requester: Callable[..., dict[str, object]] | None = None,
    ) -> None:
        self.config = config
        self._validate_config()
        provider = ProviderConfig(
            id=config.agent_id,
            kind="remote_http_bridge",
            display_name=config.display_name or config.agent_id,
            endpoint=config.endpoint,
            auth_ref=config.auth_ref,
            timeout_seconds=config.timeout_seconds,
        )
        self.adapter = RemoteBridgeAdapter(provider, requester=requester)
        self.role = Role(
            id=config.agent_id,
            display_name=config.display_name or config.agent_id,
            lens=_remote_bridge_lens(config),
            research_focus="Live lobby participation",
        )
        self.session = {
            "meeting_id": config.meeting_id,
            "agent_id": config.agent_id,
            "owner_id": "remote_bridge",
            "join_mode": "current_session",
            "session_id": config.session_id,
        }

    def __call__(self, command: list[str], prompt: str, *, timeout_seconds: int) -> str:
        del command, timeout_seconds
        try:
            response = self.adapter.run_lobby_prompt(self.role, self.session, prompt)
        except Exception as error:
            raise RuntimeError(_sanitized_remote_bridge_error(error, self.config.auth_ref)) from error
        message = str(response.get("message") or "").strip()
        if not message:
            raise ValueError("Remote bridge returned an empty reply.")
        return message

    def _validate_config(self) -> None:
        endpoint_error = remote_bridge_endpoint_error(self.config.endpoint)
        if endpoint_error == "Remote bridge endpoint is required.":
            raise ValueError("Remote bridge resident requires an endpoint.")
        if endpoint_error:
            raise ValueError("Remote bridge resident requires a safe endpoint.")
        if not remote_bridge_auth_ref_available(self.config.auth_ref):
            raise ValueError("Remote bridge resident requires an available auth_ref.")


def event_reply_candidate(
    events: list[dict[str, object]],
    agent_id: str,
    display_name: str,
    last_observed_event_id: str,
    *,
    max_chain_depth: int,
    engagement_mode: str = "always",
) -> dict[str, object] | None:
    for event in _events_after(events, last_observed_event_id):
        if _is_self_event(event, agent_id, display_name):
            continue
        if _chain_depth(event) > max_chain_depth:
            continue
        if not str(event.get("message") or "").strip():
            continue
        if not should_reply_to_event(engagement_mode, event, agent_id, display_name):
            continue
        return event
    return None


def flow_event_candidate(
    events: list[dict[str, object]],
    agent_id: str,
    display_name: str,
    last_observed_event_id: str,
    *,
    max_chain_depth: int,
    meeting_id: str = "",
) -> dict[str, object] | None:
    flow_context = active_flow_context(events, meeting_id=meeting_id)
    if flow_context is None:
        return None
    flow_id = str(flow_context.get("flow_id") or "").strip()
    if not flow_id:
        return None
    options = flow_context_options(flow_context)
    if options.max_agent_turns and flow_turn_count(events, flow_id=flow_id, agent_id=agent_id) >= options.max_agent_turns:
        return None
    if options.max_total_turns and flow_turn_count(events, flow_id=flow_id) >= options.max_total_turns:
        return None

    start_id = str(flow_context.get("id") or "").strip()
    in_active_flow = not start_id or _cursor_is_at_or_after(events, last_observed_event_id, start_id)
    for event in _events_after(events, last_observed_event_id):
        event_id = str(event.get("id") or "").strip()
        if not in_active_flow:
            if event_id != start_id:
                continue
            in_active_flow = True
        if str(event.get("flow_event_type") or "") in {"finished", "stopped"}:
            continue
        if _is_self_event(event, agent_id, display_name):
            continue
        if _chain_depth(event) > max_chain_depth:
            continue
        if not str(event.get("message") or "").strip():
            continue
        event_flow_id = str(event.get("flow_id") or "").strip()
        if event_flow_id and event_flow_id != flow_id:
            continue
        event_meeting_id = str(event.get("flow_meeting_id") or "").strip()
        if meeting_id and event_flow_id and event_meeting_id and event_meeting_id != meeting_id:
            continue
        if event_flow_id == flow_id and str(event.get("flow_event_type") or "") in {"started", "nudge"}:
            return event
        if event_flow_id == flow_id and str(event.get("flow_action") or "") in FLOW_SPEAKING_ACTIONS:
            return event
        if _is_human_lobby_event(event) or _message_mentions_agent(str(event.get("message") or ""), agent_id, display_name):
            return event
        if _chain_depth(event) > 0:
            return event
        continue
    return _flow_idle_tick_candidate(
        events,
        agent_id,
        display_name,
        flow_id=flow_id,
        meeting_id=meeting_id,
    )


def _flow_idle_tick_candidate(
    events: list[dict[str, object]],
    agent_id: str,
    display_name: str,
    *,
    flow_id: str,
    meeting_id: str = "",
) -> dict[str, object] | None:
    for event in reversed(events):
        event_flow_id = str(event.get("flow_id") or "").strip()
        event_flow_type = str(event.get("flow_event_type") or "").strip()
        if event_flow_id and event_flow_id != flow_id:
            continue
        if event_flow_type in FLOW_TERMINAL_EVENT_TYPES:
            return None
        if event_flow_type == "nudge":
            continue
        event_meeting_id = str(event.get("flow_meeting_id") or "").strip()
        if meeting_id and event_flow_id and event_meeting_id and event_meeting_id != meeting_id:
            continue
        if _is_self_event(event, agent_id, display_name):
            continue
        message = str(event.get("message") or "").strip()
        is_flow_start = event_flow_id == flow_id and event_flow_type == "started"
        is_flow_speech = event_flow_id == flow_id and str(event.get("flow_action") or "") in FLOW_SPEAKING_ACTIONS
        is_room_event = bool(message and (is_flow_start or is_flow_speech or _is_human_lobby_event(event)))
        if not is_room_event:
            continue
        candidate = dict(event)
        candidate["flow_event_type"] = "tick"
        candidate["name"] = "Play Mode"
        candidate["actor_id"] = "flow"
        candidate["auto_chain_depth"] = 0
        candidate["message"] = "방 전체 맥락을 보고 지금 말할지 기다릴지 판단하세요."
        return candidate
    return None


def official_turn_request_candidate(
    events: list[dict[str, object]],
    agent_id: str,
    last_observed_event_id: str,
) -> dict[str, object] | None:
    answered_request_ids = _visible_official_reply_source_ids(events, agent_id)
    for event in _events_after(events, last_observed_event_id):
        if str(event.get("kind") or "") != "live_agent_turn_request":
            continue
        event_id = str(event.get("id") or "").strip()
        if not event_id:
            continue
        if event_id in answered_request_ids:
            continue
        if str(event.get("actor_id") or "") == agent_id:
            continue
        if str(event.get("target_agent_id") or "") != agent_id:
            continue
        if not str(event.get("content") or "").strip():
            continue
        return event
    return None


def _visible_official_reply_source_ids(events: list[dict[str, object]], agent_id: str) -> set[str]:
    source_ids: set[str] = set()
    for event in events:
        if is_official_turn_cancellation_event(event):
            if str(event.get("target_agent_id") or "") != agent_id:
                continue
        else:
            if not is_official_turn_reply_event(event) and not is_review_checkpoint_reply_event(event):
                continue
            if str(event.get("actor_id") or "") != agent_id:
                continue
        source_event_id = str(event.get("source_event_id") or "").strip()
        if source_event_id:
            source_ids.add(source_event_id)
    return source_ids


def _runtime_engagement_mode(config: ResidentAgentConfig, room: dict[str, object]) -> str:
    agent = room.get("agent")
    if not isinstance(agent, dict):
        return config.engagement_mode
    if str(agent.get("agent_id") or "") != config.agent_id:
        return config.engagement_mode
    mode = str(agent.get("engagement_mode") or "").strip()
    return mode if mode in ENGAGEMENT_MODES else config.engagement_mode


def should_reply_to_event(
    engagement_mode: str,
    event: dict[str, object],
    agent_id: str,
    display_name: str,
) -> bool:
    mode = str(engagement_mode or "mentioned").strip().lower().replace("-", "_")
    if mode == "always":
        return True
    if mode in {"watch", "manual", "moderator_called"}:
        return False
    if mode == "human_only":
        return _is_human_lobby_event(event)
    if mode == "flow":
        return _is_human_lobby_event(event) or _message_mentions_agent(str(event.get("message") or ""), agent_id, display_name)
    if mode == "mentioned":
        return _message_mentions_agent(str(event.get("message") or ""), agent_id, display_name)
    return _message_mentions_agent(str(event.get("message") or ""), agent_id, display_name)


def delegate_prompt(config: ResidentAgentConfig, room: dict[str, object], source_event: dict[str, object]) -> str:
    lines = [
        "You are a live AgentsAssemble participant connected through a resident agent bridge.",
        f"Agent id: {config.agent_id}",
        f"Display name: {config.display_name or config.agent_id}",
        "Reply with one concise lobby message only.",
        "Do not include markdown fences or multiple alternatives.",
        "",
        *_room_delivery_envelope_lines(config, room, source_event),
        "",
        *_shared_memory_prompt_lines(room),
        "",
        "New event to answer:",
        f"- {source_event.get('name') or 'participant'}: {source_event.get('message') or ''}",
    ]
    return "\n".join(lines).strip() + "\n"


def official_turn_prompt(config: ResidentAgentConfig, room: dict[str, object], source_event: dict[str, object]) -> str:
    review_checkpoint_id = str(source_event.get("review_checkpoint_id") or "").strip()
    if review_checkpoint_id:
        intro = f"You are a live AgentsAssemble participant called into review checkpoint {review_checkpoint_id}."
        reply_rule = "Reply with one concise review message only."
        request_label = "Review request:"
    else:
        intro = "You are a live AgentsAssemble participant called into the official meeting record."
        reply_rule = "Reply with one concise official meeting turn only."
        request_label = "Moderator request:"
    lines = [
        intro,
        f"Agent id: {config.agent_id}",
        f"Display name: {config.display_name or config.agent_id}",
        reply_rule,
        "Do not include lobby chatter, markdown fences, or multiple alternatives.",
        "",
        *_room_delivery_envelope_lines(config, room, source_event),
        "",
        *_shared_memory_prompt_lines(room),
        "",
        request_label,
        f"- {source_event.get('content') or ''}",
    ]
    return "\n".join(lines).strip() + "\n"


def flow_decision_prompt(config: ResidentAgentConfig, room: dict[str, object], source_event: dict[str, object]) -> str:
    events = _lobby_events(room)
    flow_context = active_flow_context(events, meeting_id=_flow_room_meeting_id(config, room)) or {}
    flow_id = _prompt_text(flow_context.get("flow_id"), limit=128)
    topic = (
        _prompt_text(flow_context.get("flow_topic"), limit=240)
        or _prompt_text(room.get("display_topic"), limit=240)
        or _prompt_text(room.get("topic"), limit=240)
        or _prompt_text(room.get("question"), limit=240)
        or "(free conversation)"
    )
    recent_events = _recent_flow_prompt_events(events, flow_id=flow_id)
    lines = [
        "You are a live AgentsAssemble participant in a Play Mode lobby conversation.",
        "This is not an official meeting record. Do not write transcript or decision text.",
        f"Agent id: {config.agent_id}",
        f"Display name: {config.display_name or config.agent_id}",
        f"Topic: {topic}",
        "",
        "Choose your next room action from the recent context.",
        "Return one JSON object only with these keys:",
        '{"action":"speak|wait|ask|challenge|clarify|summarize|call_human","target_agent_id":"","reason":"short private reason","message":"one visible lobby message"}',
        "For wait, set message to an empty string. For every other action, write exactly one visible Korean lobby message.",
        "Avoid repeating yourself, avoid two-agent ping-pong, and keep the tone natural for the room.",
        "",
        *_room_delivery_envelope_lines(config, room, source_event),
        "",
        *_shared_memory_prompt_lines(room),
        "",
        "Recent lobby context:",
        *recent_events,
        "",
        "Newest event to consider:",
        f"- {source_event.get('name') or 'participant'}: {source_event.get('message') or ''}",
    ]
    return "\n".join(lines).strip() + "\n"


def _flow_room_meeting_id(config: ResidentAgentConfig, room: dict[str, object]) -> str:
    agent = room.get("agent") if isinstance(room.get("agent"), dict) else {}
    return str(room.get("meeting_id") or agent.get("meeting_id") or config.meeting_id or "").strip()


def _recent_flow_prompt_events(events: list[dict[str, object]], *, flow_id: str) -> list[str]:
    scoped: list[dict[str, object]] = []
    active_seen = not flow_id
    for event in events:
        event_flow_id = str(event.get("flow_id") or "").strip()
        if event_flow_id == flow_id and str(event.get("flow_event_type") or "") == "started":
            active_seen = True
        if not active_seen:
            continue
        if event_flow_id and event_flow_id != flow_id:
            continue
        if str(event.get("flow_event_type") or "") in {"finished", "stopped"}:
            continue
        scoped.append(event)
    recent = scoped[-8:]
    lines = []
    for event in recent:
        event_id = _prompt_text(event.get("id"), limit=64)
        speaker = _prompt_text(event.get("name"), limit=64) or _prompt_text(event.get("actor_id"), limit=64) or "participant"
        message = _prompt_text(event.get("message"), limit=360)
        action = _prompt_text(event.get("flow_action"), limit=64)
        action_suffix = f" [{action}]" if action else ""
        lines.append(f"- {event_id} {speaker}{action_suffix}: {message}")
    return lines or ["- (no recent lobby context)"]


def _room_delivery_envelope_lines(
    config: ResidentAgentConfig,
    room: dict[str, object],
    source_event: dict[str, object],
) -> list[str]:
    agent = room.get("agent") if isinstance(room.get("agent"), dict) else {}
    meeting_id = str(
        source_event.get("meeting_id")
        or room.get("meeting_id")
        or agent.get("meeting_id")
        or config.meeting_id
        or ""
    ).strip()
    source_event_id = _prompt_text(source_event.get("id"), limit=128) or "(none)"
    lobby_cursor = _prompt_text(agent.get("last_observed_event_id"), limit=128) or "(none)"
    live_cursor = _prompt_text(agent.get("last_observed_live_event_id"), limit=128) or "(none)"
    return [
        "Room delivery envelope (minimal room delivery envelope; not hidden moderator context):",
        f"- Source event id: {source_event_id}",
        f"- Meeting id: {meeting_id or '(none)'}",
        f"- Lobby cursor: {lobby_cursor}",
        f"- Official cursor: {live_cursor}",
        "- AgentsAssemble owns room records and shared memory; your provider/session owns private context.",
        "- If your transport has room tools, inspect read-since, archive artifacts, or shared memory before deciding.",
    ]


def _shared_memory_prompt_lines(room: dict[str, object]) -> list[str]:
    memory = room.get("shared_memory") if isinstance(room.get("shared_memory"), dict) else {}
    if not memory or _memory_int(memory.get("official_event_count")) <= 0:
        return []
    lines = [
        "Shared meeting memory (official-only; use as background, not as a new event):",
        f"- Official events: {_memory_int(memory.get('official_event_count'))}",
    ]
    last_event_id = _prompt_text(memory.get("last_official_event_id"))
    if last_event_id:
        lines.append(f"- Last official event id: {last_event_id}")
    lines.extend(_prompt_memory_section("Recent official summary", memory.get("rolling_summary"), text_key="summary"))
    lines.extend(_prompt_memory_section("Decisions", memory.get("decisions"), text_key="text"))
    lines.extend(_prompt_memory_section("Open questions", memory.get("open_questions"), text_key="text"))
    lines.extend(_prompt_memory_section("Action items", memory.get("action_items"), text_key="text"))
    return lines


def _prompt_memory_section(label: str, value: object, *, text_key: str) -> list[str]:
    items = value if isinstance(value, list) else []
    lines = []
    for item in items:
        if not isinstance(item, dict):
            continue
        text = _prompt_text(item.get(text_key))
        if not text:
            continue
        speaker = _prompt_text(item.get("speaker")) or "Unknown Speaker"
        event_id = _prompt_text(item.get("event_id"))
        suffix = f" ({speaker}, {event_id})" if event_id else f" ({speaker})"
        lines.append(f"- {label}: {text}{suffix}")
    return lines


def _prompt_text(value: object, *, limit: int = 240) -> str:
    return " ".join(str(value or "").split())[:limit]


def _memory_int(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return max(0, value)
    return 0


def load_group_configs(
    path: Path,
    *,
    max_ticks_override: int | None = None,
    server_override: str | None = None,
) -> list[ResidentAgentConfig]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Live agent group config must be a JSON object.")
    server = str(server_override or data.get("server") or "http://127.0.0.1:8765")
    defaults = {
        "poll_interval": live_agent_nonnegative_float(data.get("poll_interval"), 2.0, "poll_interval"),
        "heartbeat_interval": live_agent_nonnegative_float(data.get("heartbeat_interval"), 30.0, "heartbeat_interval"),
        "cooldown": live_agent_nonnegative_float(data.get("cooldown"), 5.0, "cooldown"),
        "max_chain_depth": live_agent_nonnegative_int(data.get("max_chain_depth"), 1, "max_chain_depth"),
        "max_ticks": live_agent_nonnegative_int(data.get("max_ticks"), 0, "max_ticks"),
    }
    if max_ticks_override is not None:
        defaults["max_ticks"] = live_agent_nonnegative_int(max_ticks_override, 0, "max_ticks")
    agents = data.get("agents")
    if not isinstance(agents, list) or not agents:
        raise ValueError("Live agent group config requires a non-empty agents list.")
    if not all(isinstance(agent, dict) for agent in agents):
        raise ValueError("Each live agent entry must be a JSON object.")
    return [
        _config_from_mapping(agent, server=server, defaults=defaults, server_override=server_override)
        for agent in agents
    ]


def config_from_args(args: object) -> ResidentAgentConfig:
    provider_kind = str(getattr(args, "provider_kind"))
    connection_kind = str(getattr(args, "connection_kind"))
    command = list(getattr(args, "resident_command", []) or [])
    return ResidentAgentConfig(
        server=str(getattr(args, "server")),
        agent_id=str(getattr(args, "agent_id")),
        display_name=str(getattr(args, "display_name") or getattr(args, "agent_id")),
        provider_kind=provider_kind,
        connection_kind=connection_kind,
        session_id=str(getattr(args, "session_id")),
        endpoint=str(getattr(args, "endpoint")),
        auth_ref=str(getattr(args, "auth_ref", "")),
        meeting_id=str(getattr(args, "meeting_id")),
        engagement_mode=str(getattr(args, "engagement_mode")),
        command=default_codex_resident_command(provider_kind, connection_kind, command),
        timeout_seconds=int(getattr(args, "timeout")),
        poll_interval=float(getattr(args, "poll_interval")),
        heartbeat_interval=float(getattr(args, "heartbeat_interval")),
        cooldown=float(getattr(args, "cooldown")),
        max_chain_depth=int(getattr(args, "max_chain_depth")),
        max_ticks=int(getattr(args, "max_ticks")),
        terminal_idle_timeout=float(getattr(args, "terminal_idle_timeout", 0.35)),
    )


def _config_from_mapping(
    data: dict[str, object],
    *,
    server: str,
    defaults: dict[str, int | float],
    server_override: str | None = None,
) -> ResidentAgentConfig:
    connection_kind = str(data.get("connection_kind") or "local_cli")
    if connection_kind not in SUPPORTED_RESIDENT_CONNECTION_KINDS:
        raise ValueError(resident_connection_kind_error())
    provider_kind = str(data.get("provider_kind") or "local_cli")
    command = data.get("command")
    endpoint = data.get("endpoint")
    auth_ref = data.get("auth_ref")
    command_parts = live_agent_command_parts(command)
    command_parts = default_codex_resident_command(provider_kind, connection_kind, command_parts)
    if connection_kind != "remote_bridge" and not command_parts:
        raise ValueError("Each live agent requires a command list.")
    agent_id = str(data.get("agent_id") or "")
    if not agent_id:
        raise ValueError("Each live agent requires agent_id.")
    return ResidentAgentConfig(
        server=str(server_override or data.get("server") or server),
        agent_id=agent_id,
        display_name=str(data.get("display_name") or agent_id),
        provider_kind=provider_kind,
        connection_kind=connection_kind,
        session_id=str(data.get("session_id") or ""),
        endpoint=endpoint if isinstance(endpoint, str) else "",
        auth_ref=auth_ref if isinstance(auth_ref, str) else "",
        meeting_id=str(data.get("meeting_id") or ""),
        engagement_mode=str(data.get("engagement_mode") or "mentioned"),
        command=command_parts,
        timeout_seconds=int(data.get("timeout_seconds") or data.get("timeout") or 120),
        poll_interval=live_agent_nonnegative_float(data.get("poll_interval"), defaults["poll_interval"], "poll_interval"),
        heartbeat_interval=live_agent_nonnegative_float(
            data.get("heartbeat_interval"),
            defaults["heartbeat_interval"],
            "heartbeat_interval",
        ),
        cooldown=live_agent_nonnegative_float(data.get("cooldown"), defaults["cooldown"], "cooldown"),
        max_chain_depth=live_agent_nonnegative_int(
            data.get("max_chain_depth"),
            defaults["max_chain_depth"],
            "max_chain_depth",
        ),
        max_ticks=live_agent_nonnegative_int(data.get("max_ticks"), defaults["max_ticks"], "max_ticks"),
        terminal_idle_timeout=live_agent_nonnegative_float(
            data.get("terminal_idle_timeout"),
            0.35,
            "terminal_idle_timeout",
        ),
    )


def live_agent_command_parts(value: object) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        return []
    if not all(isinstance(part, str) for part in value):
        raise ValueError("Live agent command entries must be strings.")
    return list(value)


def live_agent_nonnegative_float(value: object, default: int | float, field_name: str) -> float:
    raw_value = default if value is None else value
    if isinstance(raw_value, bool):
        raise ValueError(f"Live agent {field_name} must be a finite non-negative number.")
    try:
        parsed = float(raw_value)
    except (TypeError, ValueError):
        raise ValueError(f"Live agent {field_name} must be a finite non-negative number.") from None
    if not math.isfinite(parsed) or parsed < 0:
        raise ValueError(f"Live agent {field_name} must be a finite non-negative number.")
    return parsed


def live_agent_nonnegative_int(value: object, default: int, field_name: str) -> int:
    raw_value = default if value is None else value
    if isinstance(raw_value, bool):
        raise ValueError(f"Live agent {field_name} must be a non-negative integer.")
    if isinstance(raw_value, int):
        parsed = raw_value
    elif isinstance(raw_value, float):
        if not math.isfinite(raw_value) or not raw_value.is_integer():
            raise ValueError(f"Live agent {field_name} must be a non-negative integer.")
        parsed = int(raw_value)
    else:
        try:
            parsed = int(raw_value)
        except (TypeError, ValueError):
            raise ValueError(f"Live agent {field_name} must be a non-negative integer.") from None
    if parsed < 0:
        raise ValueError(f"Live agent {field_name} must be a non-negative integer.")
    return parsed


def _lobby_events(room: dict[str, object]) -> list[dict[str, object]]:
    events = room.get("lobby_events")
    if not isinstance(events, list):
        return []
    return [event for event in events if isinstance(event, dict)]


def _live_events(room: dict[str, object]) -> list[dict[str, object]]:
    events = room.get("live_events")
    if not isinstance(events, list):
        return []
    return [event for event in events if isinstance(event, dict)]


def _official_turn_meeting_id(
    config: ResidentAgentConfig,
    room: dict[str, object],
    source_event: dict[str, object],
) -> str:
    for value in (source_event.get("meeting_id"), room.get("meeting_id"), config.meeting_id):
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _optional_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _events_after(events: list[dict[str, object]], last_observed_event_id: str) -> list[dict[str, object]]:
    if not last_observed_event_id:
        return events
    for index, event in enumerate(events):
        if event.get("id") == last_observed_event_id:
            return events[index + 1 :]
    return events


def _cursor_is_at_or_after(events: list[dict[str, object]], cursor_event_id: str, target_event_id: str) -> bool:
    if not cursor_event_id or not target_event_id:
        return False
    target_index = None
    cursor_index = None
    for index, event in enumerate(events):
        event_id = str(event.get("id") or "")
        if event_id == target_event_id:
            target_index = index
        if event_id == cursor_event_id:
            cursor_index = index
    return target_index is not None and cursor_index is not None and cursor_index >= target_index


def _is_self_event(event: dict[str, object], agent_id: str, display_name: str) -> bool:
    actor_id = str(event.get("actor_id") or "")
    if actor_id and actor_id == agent_id:
        return True
    return bool(display_name) and str(event.get("name") or "") == display_name


def _is_human_lobby_event(event: dict[str, object]) -> bool:
    if str(event.get("actor_id") or ""):
        return False
    side = str(event.get("side") or "")
    return side in {"", "mine", "other"}


def _message_mentions_agent(message: str, agent_id: str, display_name: str) -> bool:
    normalized_message = message.casefold()
    mentions = [agent_id, display_name]
    return any(_contains_mention_token(normalized_message, str(mention or "").casefold()) for mention in mentions)


def _contains_mention_token(normalized_message: str, normalized_mention: str) -> bool:
    mention = normalized_mention.strip()
    if not mention:
        return False
    pattern = rf"(?<![\w-]){re.escape(mention)}(?![\w-])"
    return re.search(pattern, normalized_message) is not None


def _chain_depth(event: dict[str, object]) -> int:
    value = event.get("auto_chain_depth")
    if isinstance(value, int) and not isinstance(value, bool):
        return max(0, value)
    return 0


def _value_or_default(value: object, default: int | float) -> object:
    return default if value is None else value


def _latest_event_id(events: list[dict[str, object]]) -> str:
    for event in reversed(events):
        if event.get("id"):
            return str(event["id"])
    return ""


def _quote(value: str) -> str:
    from urllib.parse import quote

    return quote(value, safe="")


def _server_url(server: str, path: str) -> str:
    return f"{server.rstrip('/')}{path}"


def _remote_bridge_lens(config: ResidentAgentConfig) -> str:
    provider = str(config.provider_kind or "").strip()
    if provider:
        return f"{provider} remote bridge lobby participant"
    return "Remote bridge lobby participant"


def _sanitized_remote_bridge_error(error: Exception, auth_ref: str) -> str:
    text = str(error).strip() or error.__class__.__name__
    secret = _resolved_secret_for_redaction(auth_ref)
    if secret and secret in text:
        return "Remote bridge request failed."
    if _looks_sensitive_error(text):
        return "Remote bridge request failed."
    return text


def _resolved_secret_for_redaction(auth_ref: str) -> str:
    return remote_bridge_auth_ref_value(auth_ref)


def _looks_sensitive_error(text: str) -> bool:
    normalized = text.casefold()
    markers = (
        "authorization",
        "bearer ",
        "secret",
        "token",
        "api-key",
        "apikey",
        "x-api-key",
        "password",
        "http://",
        "https://",
        "env:",
        ".json",
        ".env",
        ".toml",
    )
    if any(marker in normalized for marker in markers):
        return True
    if "/" in text or "\\" in text or "--" in text:
        return True
    return bool(re.search(r"\$(?:\{[A-Za-z_][A-Za-z0-9_]*\}|[A-Za-z_][A-Za-z0-9_]*)", text))


def _safe_provider_command_error(error: Exception) -> str:
    if isinstance(error, subprocess.CalledProcessError):
        return f"Resident command exited with return code {error.returncode}."
    if isinstance(error, subprocess.TimeoutExpired):
        return f"Resident command timed out after {error.timeout} seconds."
    if isinstance(error, OSError):
        detail = str(getattr(error, "strerror", "") or "").strip() or error.__class__.__name__
        return f"Resident command failed: {detail}."
    text = str(error).strip()
    if not text:
        return "Resident command failed."
    return "Resident command error details redacted." if _looks_sensitive_error(text) else text


def _safe_room_read_error(error: Exception) -> str:
    return _safe_resident_surface_error(
        error,
        fallback_label="Resident room read failed.",
        redacted_label="Resident room read error details redacted.",
    )


def _safe_reply_post_error(error: Exception) -> str:
    return _safe_resident_surface_error(
        error,
        fallback_label="Resident reply post failed.",
        redacted_label="Resident reply post error details redacted.",
    )


def _safe_resident_surface_error(error: Exception, *, fallback_label: str, redacted_label: str) -> str:
    text = str(error).strip()
    if not text:
        return fallback_label
    return redacted_label if _looks_sensitive_error(text) else text


def resident_connection_kind_error() -> str:
    return "Resident groups support local_cli, live_session, terminal_session, remote_bridge, and self_service connections."
