from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from agentsassemble.live_cli import (
    AgentRuntime,
    AgentRuntimeBinding,
    GeneralRoomEventStore,
    LiveCliRuntime,
    RoomScheduler,
)
from agentsassemble.live_cli_smoke import run_live_cli_smoke
from agentsassemble.meeting_events import clean_lobby_text


@dataclass(frozen=True)
class LiveCliProviderSpec:
    agent_id: str
    display_name: str
    command: list[str]
    cwd: str = ""
    default_responder: bool = True
    quiet_seconds: float = 0.35


DEFAULT_LIVE_CLI_PROVIDER_SPECS = [
    LiveCliProviderSpec(agent_id="codex", display_name="Codex CLI", command=["codex"]),
    LiveCliProviderSpec(agent_id="antigravity", display_name="Antigravity CLI", command=["antigravity"]),
    LiveCliProviderSpec(agent_id="grok", display_name="Grok CLI", command=["grok"]),
]


class GeneralRoomController:
    """In-process control plane for the local CLI-first #general room."""

    def __init__(
        self,
        output_root: str | Path,
        *,
        providers: list[LiveCliProviderSpec] | None = None,
        runtime_factory: Callable[[LiveCliProviderSpec], AgentRuntime] | None = None,
        read_timeout_seconds: float = 120.0,
    ) -> None:
        self.output_root = Path(output_root)
        self.room = GeneralRoomEventStore(self.output_root)
        self.specs = {
            clean_lobby_text(spec.agent_id, limit=128): spec
            for spec in (providers if providers is not None else DEFAULT_LIVE_CLI_PROVIDER_SPECS)
        }
        self.runtimes: dict[str, AgentRuntime] = {}
        for agent_id, spec in self.specs.items():
            self.runtimes[agent_id] = (
                runtime_factory(spec)
                if runtime_factory is not None
                else LiveCliRuntime(
                    spec.agent_id,
                    spec.command,
                    cwd=spec.cwd or None,
                    idle_quiet_seconds=spec.quiet_seconds,
                )
            )
        self.scheduler = RoomScheduler(
            self.room,
            [
                AgentRuntimeBinding(agent_id=agent_id, runtime=self.runtimes[agent_id], default_responder=spec.default_responder)
                for agent_id, spec in self.specs.items()
            ],
            read_timeout_seconds=read_timeout_seconds,
        )
        self._resumed_process: dict[str, bool] = {agent_id: False for agent_id in self.specs}

    def events_payload(self, *, after: str = "", limit: int = 200) -> dict[str, object]:
        clean_limit = max(1, min(1000, int(limit or 200)))
        events = self.room.read_events(after=after)
        has_more = len(events) > clean_limit
        return {"room_id": self.room.room_id, "events": events[:clean_limit], "has_more": has_more}

    def post_message(self, *, content: str, actor_id: str = "human", dispatch: bool = True) -> dict[str, object]:
        event = self.room.append_user_message(
            clean_lobby_text(actor_id, limit=128) or "human",
            clean_lobby_text(content, limit=12000),
        )
        dispatched = self.dispatch() if dispatch else {"dispatched": []}
        return {"event": event, **dispatched}

    def dispatch(self) -> dict[str, object]:
        dispatched = self.scheduler.dispatch_new_events()
        return {
            "dispatched": [
                {
                    "event_id": event.get("event_id", ""),
                    "kind": event.get("kind", ""),
                    "content": event.get("content", ""),
                }
                for event in dispatched
            ]
        }

    def wait_for_idle(self, *, timeout_seconds: float) -> bool:
        return self.scheduler.wait_for_idle(timeout_seconds=timeout_seconds)

    def agents_payload(self) -> dict[str, object]:
        statuses = self.scheduler.agent_statuses()
        return {"room_id": self.room.room_id, "agents": [self._agent_payload(agent_id, statuses) for agent_id in self.specs]}

    def latency_payload(self) -> dict[str, object]:
        return {"room_id": self.room.room_id, "agents": self.scheduler.latency_payload()}

    def smoke_runs_payload(self) -> dict[str, object]:
        smoke_dir = self.output_root / "rooms" / self.room.room_id / "smoke"
        runs: list[dict[str, object]] = []
        if smoke_dir.exists():
            for path in sorted(smoke_dir.glob("*.json"), reverse=True)[:50]:
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if isinstance(payload, dict):
                    runs.append(
                        {
                            "run_id": payload.get("run_id") or path.stem,
                            "status": payload.get("status") or "unknown",
                            "started_at": payload.get("started_at") or "",
                            "finished_at": payload.get("finished_at") or "",
                            "result_path": str(path),
                        }
                    )
        return {"room_id": self.room.room_id, "smoke_runs": runs}

    def smoke_payload(
        self,
        payload: dict[str, object],
        *,
        reporter: Callable[[dict[str, object]], None] | None = None,
    ) -> dict[str, object]:
        return run_live_cli_smoke(
            config_path=str(payload.get("config") or payload.get("config_path") or "configs/live-cli-providers.example.json"),
            output_root=self.output_root,
            providers=_provider_list(payload.get("providers")),
            approve_real_provider=bool(payload.get("approve_real_provider") or payload.get("approve_real_providers")),
            timeout_seconds=float(payload.get("timeout_seconds") or payload.get("timeout") or 120),
            reporter=reporter,
        )

    def start_agent(self, agent_id: str) -> dict[str, object]:
        runtime = self._runtime(agent_id)
        before_pid = runtime.health().get("pid")
        try:
            runtime.start()
            self._resumed_process[clean_lobby_text(agent_id, limit=128)] = bool(before_pid and before_pid == runtime.health().get("pid"))
        except Exception:
            # health() carries the runtime's last_error when the command is missing
            pass
        return {"agent": self._agent_payload(agent_id)}

    def stop_agent(self, agent_id: str) -> dict[str, object]:
        self._runtime(agent_id).stop()
        return {"agent": self._agent_payload(agent_id)}

    def resume_agent(self, agent_id: str) -> dict[str, object]:
        clean_agent_id = clean_lobby_text(agent_id, limit=128)
        runtime = self._runtime(clean_agent_id)
        before_pid = runtime.health().get("pid")
        try:
            runtime.start()
            after_pid = runtime.health().get("pid")
            self._resumed_process[clean_agent_id] = bool(before_pid and before_pid == after_pid)
        except Exception:
            self._resumed_process[clean_agent_id] = False
        dispatched = self.dispatch()
        return {"agent": self._agent_payload(clean_agent_id), **dispatched}

    def interrupt_agent(self, agent_id: str) -> dict[str, object]:
        runtime = self._runtime(agent_id)
        try:
            runtime.interrupt()
        except Exception:
            pass
        return {"agent": self._agent_payload(agent_id)}

    def stop_all(self) -> None:
        for runtime in self.runtimes.values():
            runtime.stop()
        self.wait_for_idle(timeout_seconds=1.0)

    def _agent_payload(
        self,
        agent_id: str,
        statuses: dict[str, dict[str, object]] | None = None,
    ) -> dict[str, object]:
        clean_agent_id = clean_lobby_text(agent_id, limit=128)
        spec = self.specs.get(clean_agent_id)
        if spec is None:
            raise ValueError(f"unknown agent: {agent_id}")
        status_by_agent = statuses if statuses is not None else self.scheduler.agent_statuses()
        health = dict(status_by_agent.get(clean_agent_id, self.runtimes[clean_agent_id].health()))
        running = bool(health.get("running"))
        status = clean_lobby_text(health.get("status"), limit=32) or ("idle" if running else "stopped")
        last_error = clean_lobby_text(health.get("last_error"), limit=500)
        if last_error and not running:
            status = "error" if status not in {"stopped"} else "stopped"
        return {
            "agent_id": clean_agent_id,
            "display_name": spec.display_name,
            "provider_label": spec.display_name,
            "runtime_kind": health.get("runtime_kind") or "live_cli",
            "command_configured": health.get("command_configured") or list(spec.command),
            "command_display": health.get("command_display") or " ".join(spec.command),
            "resolved_executable": health.get("resolved_executable") or "",
            "pid": health.get("pid"),
            "status": status,
            "started_at": health.get("started_at") or "",
            "stopped_at": health.get("stopped_at") or "",
            "last_seen_event_id": health.get("last_seen_event_id") or "",
            "last_input_event_id": health.get("last_input_event_id") or "",
            "last_output_event_id": health.get("last_output_event_id") or "",
            "turn_count": int(health.get("turn_count") or 0),
            "last_error": last_error,
            "latency": health.get("latency") if isinstance(health.get("latency"), dict) else {},
            "session_dir": health.get("session_dir") or "",
            "workspace_dir": health.get("workspace_dir") or health.get("cwd") or spec.cwd,
            "pty": bool(health.get("pty", True)),
            "transport": health.get("transport") or "pty",
            "is_one_shot": bool(health.get("is_one_shot", False)),
            "resumed_process": bool(self._resumed_process.get(clean_agent_id, False)),
        }

    def _runtime(self, agent_id: str) -> AgentRuntime:
        clean_agent_id = clean_lobby_text(agent_id, limit=128)
        runtime = self.runtimes.get(clean_agent_id)
        if runtime is None:
            raise ValueError(f"unknown agent: {agent_id}")
        return runtime


def _provider_list(value: object) -> list[str]:
    if isinstance(value, str):
        return [clean_lobby_text(item, limit=128) for item in value.split(",") if item.strip()]
    if isinstance(value, list):
        return [clean_lobby_text(item, limit=128) for item in value if clean_lobby_text(item, limit=128)]
    return []
