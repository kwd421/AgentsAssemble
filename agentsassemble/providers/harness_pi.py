"""Pi JSONL RPC harness bound to a session-local NativeModelGateway."""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
import time
from pathlib import Path

from agentsassemble.providers.harness_events import (
    compaction_activity,
    error_activity,
    reasoning_activity,
    tool_activity,
)
from agentsassemble.providers.native_harness import NativeHarnessRuntime, NativeHarnessUnavailable
from agentsassemble.providers.native_harness_gateway import NativeModelGateway
from agentsassemble.providers.pi_room_extension import (
    PI_READ_ONLY_TOOLS,
    write_pi_room_extension,
)
from agentsassemble.providers.process_environment import sanitized_provider_environment
from agentsassemble.providers.provider_requests import ProviderRequestHandler
from agentsassemble.providers.room_portal import RoomPortal
from agentsassemble.providers.turn_progress import ProviderTurnProgress
from agentsassemble.room.text import clean_room_text


def create_pi_harness_runtime(
    *,
    agent_id: str,
    runtime_kind: str,
    provider_kind: str,
    provider_endpoint: str,
    credential: str,
    model: str,
    reasoning_effort: str,
    permission_mode: str,
    workspace: str,
    runtime_state_dir: str,
    environment: dict[str, str] | None,
    room_portal: RoomPortal | None,
    request_headers: tuple[tuple[str, str], ...] = (),
    variant: str = "",
    max_output_tokens: int = 0,
    context_contract_bytes: int = 256_000,
) -> NativeHarnessRuntime:
    del permission_mode  # Pi exposes no interactive approval bridge yet.
    executable = shutil.which("pi")
    if not executable:
        raise NativeHarnessUnavailable("Pi CLI is not installed.")
    state_dir = Path(runtime_state_dir).expanduser().resolve()
    state_dir.mkdir(parents=True, exist_ok=True)
    if room_portal is None:
        raise NativeHarnessUnavailable("Pi room sessions require a RoomPortal.")
    room_portal.prepare()
    extension_path = write_pi_room_extension(
        state_dir / "pi-room-extension.ts",
        workspace=workspace,
        room_helper=room_portal.helper_path,
    )
    gateway = NativeModelGateway(
        upstream_base_url=provider_endpoint,
        upstream_api_key=credential,
        model=model,
        provider_kind=provider_kind,
        reasoning_effort=reasoning_effort,
        variant=variant,
        max_output_tokens=max_output_tokens,
        request_headers=request_headers,
        context_contract_bytes=context_contract_bytes,
        state_dir=str(state_dir / "gateway"),
    )
    delegate = PiHarnessRuntime(
        agent_id=agent_id,
        executable=executable,
        workspace=workspace,
        state_dir=state_dir,
        model=model,
        reasoning_effort=reasoning_effort,
        environment=environment,
        gateway=gateway,
        extension_path=extension_path,
    )
    return NativeHarnessRuntime(
        delegate,
        harness="pi",
        runtime_kind=runtime_kind,
        gateway=gateway,
    )


class PiHarnessRuntime:
    """One Pi RPC process using only session-local agent config."""

    def __init__(
        self,
        *,
        agent_id: str,
        executable: str,
        workspace: str | Path,
        state_dir: str | Path,
        model: str,
        reasoning_effort: str = "",
        environment: dict[str, str] | None = None,
        gateway: NativeModelGateway,
        extension_path: str | Path | None = None,
        popen_factory=subprocess.Popen,
    ) -> None:
        self.agent_id = clean_room_text(agent_id, limit=128)
        self.executable = executable
        self.workspace = Path(workspace).expanduser().resolve()
        self.state_dir = Path(state_dir).expanduser().resolve()
        self.model = clean_room_text(model, limit=256) or "upstream"
        self.reasoning_effort = clean_room_text(reasoning_effort, limit=32)
        self._environment = dict(environment or {})
        self._gateway = gateway
        self._extension_path = (
            Path(extension_path).expanduser().resolve()
            if extension_path is not None
            else None
        )
        self._popen_factory = popen_factory
        self._process: subprocess.Popen[str] | None = None
        self._reader: threading.Thread | None = None
        self._events: queue.Queue[dict[str, object] | None] = queue.Queue()
        self._lock = threading.RLock()
        self._pending = ""
        self._command_id = 0
        self._running = False
        self._last_error = ""
        self._provider_request_handler: ProviderRequestHandler | None = None

    def set_request_handler(self, handler: ProviderRequestHandler | None) -> None:
        # Pi RPC does not expose AA provider-request approvals; surface unsupported.
        self._provider_request_handler = handler

    def start(self) -> dict[str, object]:
        with self._lock:
            if self._running and self._process is not None and self._process.poll() is None:
                return self.health()
        self._gateway.start()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        agent_dir = self.state_dir / "pi-agent"
        session_dir = self.state_dir / "pi-sessions"
        agent_dir.mkdir(parents=True, exist_ok=True)
        session_dir.mkdir(parents=True, exist_ok=True)
        models_path = agent_dir / "models.json"
        models_path.write_text(
            json.dumps(
                {
                    "providers": {
                        "agentsassemble": {
                            "baseUrl": self._gateway.endpoint.rstrip("/"),
                            "api": "openai-completions",
                            "apiKey": "agentsassemble-local-gateway",
                            "compat": {
                                "supportsDeveloperRole": False,
                                "supportsReasoningEffort": False,
                            },
                            "models": [
                                {
                                    "id": self.model,
                                    "name": self.model,
                                    "reasoning": bool(self.reasoning_effort),
                                    "input": ["text"],
                                    "contextWindow": 256000,
                                    "maxTokens": 8192,
                                    "cost": {
                                        "input": 0,
                                        "output": 0,
                                        "cacheRead": 0,
                                        "cacheWrite": 0,
                                    },
                                }
                            ],
                        }
                    }
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        command = [
            self.executable,
            "--mode",
            "rpc",
            "--provider",
            "agentsassemble",
            "--model",
            self.model,
            "--api-key",
            "agentsassemble-local-gateway",
            "--session-dir",
            str(session_dir),
            "--no-extensions",
            "--tools",
            ",".join(PI_READ_ONLY_TOOLS),
        ]
        if self._extension_path is not None:
            command.extend(("--extension", str(self._extension_path)))
        if self.reasoning_effort:
            command.extend(("--thinking", self.reasoning_effort))
        env = sanitized_provider_environment(
            {
                **self._environment,
                "PI_CODING_AGENT_DIR": str(agent_dir),
                "PI_CODING_AGENT_SESSION_DIR": str(session_dir),
                "PI_OFFLINE": "1",
                "PI_TELEMETRY": "0",
            }
        )
        process = self._popen_factory(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(self.workspace),
            env=env,
            text=True,
            bufsize=1,
            start_new_session=True,
        )
        self._process = process
        self._reader = threading.Thread(
            target=self._read_stdout,
            name=f"pi-harness-{self.agent_id}",
            daemon=True,
        )
        self._reader.start()
        self._running = True
        self._last_error = ""
        return self.health()

    def send(self, text: str) -> None:
        content = str(text or "").strip()
        if not content:
            raise ValueError("Pi harness turn input is required.")
        self.start()
        with self._lock:
            if self._pending:
                raise RuntimeError("Pi harness is already processing a turn.")
            self._pending = content

    def send_room_observation(self, text: str, *, media_blocks=None) -> None:
        del media_blocks
        self.send(text)

    def read_output(self, *, timeout_seconds: float, on_delta=None, on_activity=None):
        with self._lock:
            prompt = self._pending
            self._pending = ""
        if not prompt:
            raise RuntimeError("Pi harness has no pending turn.")
        self.start()
        progress = ProviderTurnProgress(timeout_seconds)
        request_id = self._next_command_id()
        self._write_command(
            {
                "id": request_id,
                "type": "prompt",
                "message": prompt,
            }
        )
        emitted = ""
        agent_settled = False
        provider_error = ""
        while not agent_settled:
            if progress.expired():
                self.interrupt()
                raise TimeoutError(f"Pi harness timed out after {timeout_seconds} seconds.")
            try:
                event = self._events.get(timeout=0.2)
            except queue.Empty:
                if self._process is not None and self._process.poll() is not None:
                    raise RuntimeError("Pi harness process exited during the turn.")
                continue
            if event is None:
                raise RuntimeError("Pi harness process closed stdout.")
            progress.record()
            event_type = str(event.get("type") or "")
            if event_type == "response" and str(event.get("id") or "") == request_id:
                if not event.get("success"):
                    error = event.get("error") if isinstance(event.get("error"), dict) else {}
                    message = str(error.get("message") or event.get("message") or "Pi prompt rejected.")
                    if on_activity is not None:
                        on_activity(error_activity(message=message))
                    raise RuntimeError(message)
                continue
            if event_type == "message_update":
                assistant = event.get("assistantMessageEvent")
                if not isinstance(assistant, dict):
                    continue
                update_type = str(assistant.get("type") or "")
                if update_type == "text_delta":
                    delta = str(assistant.get("delta") or "")
                    if delta:
                        emitted += delta
                        if on_delta is not None:
                            on_delta(delta)
                elif update_type in {"thinking_delta", "reasoning_delta"}:
                    if on_activity is not None:
                        on_activity(
                            reasoning_activity(
                                text=str(assistant.get("delta") or assistant.get("content") or ""),
                                status="running",
                            )
                        )
                continue
            if event_type == "tool_execution_start":
                if on_activity is not None:
                    on_activity(
                        tool_activity(
                            tool_name=str(event.get("toolName") or event.get("name") or "tool"),
                            status="running",
                            activity_id=str(event.get("toolCallId") or event.get("id") or ""),
                            detail=event.get("args") or event.get("input") or "",
                        )
                    )
                continue
            if event_type == "tool_execution_update":
                if on_activity is not None:
                    on_activity(
                        tool_activity(
                            tool_name=str(event.get("toolName") or event.get("name") or "tool"),
                            status="running",
                            activity_id=str(event.get("toolCallId") or event.get("id") or ""),
                            detail=event.get("partialResult") or event.get("output") or "",
                        )
                    )
                continue
            if event_type == "tool_execution_end":
                if on_activity is not None:
                    is_error = bool(event.get("isError") or event.get("error"))
                    on_activity(
                        tool_activity(
                            tool_name=str(event.get("toolName") or event.get("name") or "tool"),
                            status="failed" if is_error else "completed",
                            activity_id=str(event.get("toolCallId") or event.get("id") or ""),
                            detail=event.get("result") or event.get("output") or "",
                        )
                    )
                continue
            if event_type in {"session_compacted", "compaction_end", "auto_compaction"}:
                if on_activity is not None:
                    on_activity(compaction_activity(status="completed"))
                continue
            if event_type == "agent_error":
                provider_error = str(event.get("error") or event.get("message") or "Pi agent error")
                if on_activity is not None:
                    on_activity(error_activity(message=provider_error))
                agent_settled = True
                continue
            if event_type == "turn_end":
                message = event.get("message")
                if isinstance(message, dict):
                    text = _assistant_text(message)
                    if text and text.startswith(emitted):
                        remainder = text[len(emitted) :]
                        if remainder and on_delta is not None:
                            on_delta(remainder)
                        emitted = text
                    elif text and not emitted:
                        emitted = text
                        if on_delta is not None:
                            on_delta(text)
                # A Pi agent run may contain several turns. A tool-use turn is
                # followed by tool execution and another model turn, so only
                # agent_settled is the completion boundary of an RPC prompt.
                continue
            if event_type == "agent_settled":
                agent_settled = True
                continue
        if provider_error:
            raise RuntimeError(provider_error)
        content = emitted.strip()
        metadata = {
            "observed_model_id": self.model,
            "runtime_kind": "api",
            "execution_harness": "pi",
            "unsupported": ["approvals", "choices"],
        }
        if content:
            return {
                "outcome": "message",
                "content": content,
                "metadata": metadata,
            }
        return {
            "outcome": "decline",
            "reason_code": "nothing_useful_to_add",
            "metadata": metadata,
        }

    def interrupt(self) -> None:
        try:
            self._write_command({"type": "abort"})
        except Exception:
            process = self._process
            if process is not None and process.poll() is None:
                process.terminate()

    def stop(self, *, timeout_seconds: float = 2.0) -> None:
        process = self._process
        self._process = None
        self._running = False
        if process is None:
            return
        try:
            if process.stdin is not None:
                process.stdin.close()
        except Exception:
            pass
        process.terminate()
        try:
            process.wait(timeout=max(0.1, timeout_seconds))
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=1.0)

    def health(self) -> dict[str, object]:
        process = self._process
        running = bool(process is not None and process.poll() is None)
        return {
            "running": running,
            "transport": "stdio_jsonl",
            "provider_session_active": running,
            "runtime_kind": "api",
            "execution_harness": "pi",
            "pid": process.pid if process is not None else None,
            "last_error": self._last_error,
            "unsupported": ["approvals", "choices"],
            "model": self.model,
        }

    def _next_command_id(self) -> str:
        self._command_id += 1
        return f"pi-{self._command_id}"

    def _write_command(self, payload: dict[str, object]) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise RuntimeError("Pi harness is not running.")
        process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        process.stdin.flush()

    def _read_stdout(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            self._events.put(None)
            return
        try:
            for raw_line in process.stdout:
                line = raw_line[:-1] if raw_line.endswith("\n") else raw_line
                if line.endswith("\r"):
                    line = line[:-1]
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict):
                    self._events.put(event)
        finally:
            self._events.put(None)


def _assistant_text(message: dict[str, object]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and str(item.get("type") or "") == "text":
                parts.append(str(item.get("text") or ""))
            elif isinstance(item, str):
                parts.append(item)
        return "".join(parts)
    return str(message.get("text") or "")


__all__ = ["PiHarnessRuntime", "create_pi_harness_runtime"]
