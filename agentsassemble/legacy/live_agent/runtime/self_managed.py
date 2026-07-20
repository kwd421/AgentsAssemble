"""STOP/RESUME for residents the server did NOT spawn.

A standalone `live-agent run` (e.g. launched from a terminal) owns its own OS
process, so the process supervisor has no handle on it. To still give the owner
real control from the room, the resident registers its pid + relaunch recipe
(argv + cwd + host); this module turns that into:

- STOP  → a real signal to the registered pid (SIGTERM, then SIGKILL). This is a
          genuine kill, distinct from expel (which only removes from the roster).
- RESUME → relaunch the recorded argv/cwd as a detached process, so it comes back
          up with its real working capability (its own permission/sandbox) and
          rejoins the room — not a stalled read-only shell.

Both are owner-gated at the HTTP layer and only act on a pid/recipe the resident
itself advertised, on the same host as the server (a remote resident can't be
signalled, so we refuse rather than kill an unrelated local pid)."""

from __future__ import annotations

import os
import signal
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from agentsassemble.legacy.live_agent.runtime.operations import append_live_agent_operation
from agentsassemble.live_agents import read_live_agents, set_live_agent_status
from agentsassemble.legacy.meeting.core.events import clean_lobby_text


def _agent_record(output_root: Path, agent_id: str) -> dict[str, object]:
    clean_agent_id = clean_lobby_text(agent_id, limit=64)
    if not clean_agent_id:
        raise ValueError("Agent id is required.")
    for agent in read_live_agents(output_root):
        if str(agent.get("agent_id") or "") == clean_agent_id:
            return agent
    raise ValueError(f"Live agent {clean_agent_id} was not found.")


def _host_is_local(recorded_host: str) -> bool:
    host = (recorded_host or "").strip()
    if not host:
        # No host recorded (older registration) → assume local; this is a
        # local-first tool and the pid is only meaningful here anyway.
        return True
    return host == socket.gethostname()


def stop_self_managed_agent_payload(
    output_root: Path,
    payload: dict[str, object],
    *,
    signal_sender: Callable[[int, int], None] | None = None,
    now: object | None = None,
) -> dict[str, object]:
    """Send a real stop signal to a self-managed resident's registered pid."""
    send = signal_sender or os.kill
    agent = _agent_record(output_root, str(payload.get("agent_id") or ""))
    agent_id = str(agent.get("agent_id") or "")
    try:
        pid = int(agent.get("relaunch_pid") or 0)
    except (TypeError, ValueError):
        pid = 0
    if pid <= 0:
        raise ValueError("This agent did not register a process id, so it cannot be stopped from here.")
    if not _host_is_local(str(agent.get("relaunch_host") or "")):
        raise ValueError("This agent runs on another host, so the room cannot stop its process.")

    term = getattr(signal, "SIGTERM", 15)
    kill = getattr(signal, "SIGKILL", 9)
    signalled = False
    try:
        send(pid, term)
        signalled = True
    except ProcessLookupError:
        signalled = False  # already gone — treat as stopped
    except PermissionError as error:
        raise ValueError("The room is not allowed to signal that process.") from error
    except OSError as error:
        raise ValueError(f"Could not stop the agent process: {error.__class__.__name__}.") from error
    # Best-effort hard kill in case SIGTERM is ignored; never fatal.
    try:
        send(pid, kill)
    except OSError:
        pass

    stopped_agent = set_live_agent_status(output_root, agent_id, "offline", now=now)
    return {
        "status": "stopped",
        "agent_id": agent_id,
        "pid": pid,
        "signalled": signalled,
        "agent": stopped_agent,
    }


def resume_self_managed_agent_payload(
    output_root: Path,
    payload: dict[str, object],
    *,
    launcher: Callable[..., Any] | None = None,
) -> dict[str, object]:
    """Relaunch a self-managed resident from its registered argv/cwd, detached.

    The recipe is the resident's own earlier command, so it comes back with its
    real permission/sandbox and rejoins the room — actually working, not paused."""
    popen = launcher or subprocess.Popen
    agent = _agent_record(output_root, str(payload.get("agent_id") or ""))
    agent_id = str(agent.get("agent_id") or "")
    argv_raw = agent.get("relaunch_argv")
    argv = [str(part) for part in argv_raw] if isinstance(argv_raw, list) else []
    argv = [part for part in argv if part]
    if not argv:
        raise ValueError("This agent did not register a relaunch command, so it cannot be resumed from here.")
    if not _host_is_local(str(agent.get("relaunch_host") or "")):
        raise ValueError("This agent was launched on another host, so the room cannot relaunch it.")
    cwd = str(agent.get("relaunch_cwd") or "").strip()
    cwd_arg = cwd if cwd and Path(cwd).is_dir() else None

    kwargs: dict[str, Any] = {"cwd": cwd_arg}
    # Detach so the relaunched resident outlives this server request/process.
    if hasattr(os, "setsid"):
        kwargs["start_new_session"] = True
    try:
        process = popen(argv, **kwargs)
    except OSError as error:
        raise ValueError(f"Could not relaunch the agent: {error.__class__.__name__}.") from error
    new_pid = int(getattr(process, "pid", 0) or 0)
    return {
        "status": "resuming",
        "agent_id": agent_id,
        "pid": new_pid,
        "cwd": cwd_arg or "",
    }


SelfManagedCommand = Callable[[Path, dict[str, object]], dict[str, object]]


@dataclass(frozen=True)
class LegacySelfManagedAgentService:
    """Run retained self-managed process controls and record their audit."""

    output_root: Path
    stop_command: SelfManagedCommand = stop_self_managed_agent_payload
    resume_command: SelfManagedCommand = resume_self_managed_agent_payload

    def stop(self, payload: dict[str, object]) -> dict[str, object]:
        agent_id = clean_lobby_text(payload.get("agent_id"), limit=128)
        try:
            result = self.stop_command(self.output_root, payload)
        except (OSError, ValueError) as error:
            self._record_failure("stop_self_managed", agent_id, str(error))
            raise
        result_agent_id = str(result.get("agent_id") or agent_id)
        self._record_success(
            "stop_self_managed",
            result_agent_id,
            summary=f"stopped self-managed agent pid={result.get('pid')}",
            pid=result.get("pid"),
        )
        return result

    def resume(self, payload: dict[str, object]) -> dict[str, object]:
        agent_id = clean_lobby_text(payload.get("agent_id"), limit=128)
        try:
            result = self.resume_command(self.output_root, payload)
        except (OSError, ValueError) as error:
            self._record_failure("resume_self_managed", agent_id, str(error))
            raise
        result_agent_id = str(result.get("agent_id") or agent_id)
        self._record_success(
            "resume_self_managed",
            result_agent_id,
            summary=f"relaunched self-managed agent pid={result.get('pid')}",
            pid=result.get("pid"),
        )
        return result

    def record_invalid_json(self, action: str) -> None:
        self._record_failure(action, "", "Invalid JSON", include_agent_detail=False)

    def _record_failure(
        self,
        action: str,
        agent_id: str,
        error: str,
        *,
        include_agent_detail: bool = True,
    ) -> None:
        append_live_agent_operation(
            self.output_root,
            operation=f"frontend_agent.{action}",
            status="failed",
            target_id=agent_id,
            error=error,
            details={"agent_id": agent_id} if include_agent_detail else {},
        )

    def _record_success(
        self,
        action: str,
        agent_id: str,
        *,
        summary: str,
        pid: object,
    ) -> None:
        append_live_agent_operation(
            self.output_root,
            operation=f"frontend_agent.{action}",
            status="success",
            target_id=agent_id,
            summary=summary,
            details={"agent_id": agent_id, "pid": pid},
        )
