from __future__ import annotations

import json
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from agentsassemble.meeting_events import clean_lobby_text
from agentsassemble.process_environment import sanitized_provider_environment


class OpenCodeServerProcess:
    """Host-owned shared OpenCode server lifecycle."""

    def __init__(
        self,
        *,
        cwd: str | Path,
        executable: str = "opencode",
        popen_factory=subprocess.Popen,
    ) -> None:
        self.cwd = Path(cwd).expanduser().resolve()
        self.executable = executable
        self._popen_factory = popen_factory
        self.process = None
        self.endpoint = ""

    def start(self) -> dict[str, object]:
        if self.process is not None and self.process.poll() is None:
            return self.health()
        resolved = self.executable if Path(self.executable).is_absolute() else shutil.which(self.executable)
        if not resolved:
            raise FileNotFoundError(f"configured command missing: {self.executable}")
        self.cwd.mkdir(parents=True, exist_ok=True)
        port = _reserve_loopback_port()
        self.endpoint = f"http://127.0.0.1:{port}"
        self.process = self._popen_factory(
            [resolved, "serve", "--hostname", "127.0.0.1", "--port", str(port), "--log-level", "ERROR"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=str(self.cwd),
            env=sanitized_provider_environment(),
            start_new_session=True,
        )
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                break
            try:
                with urlopen(f"{self.endpoint}/global/health", timeout=0.5) as response:
                    if response.status == 200:
                        return self.health()
            except Exception:
                time.sleep(0.05)
        self.stop()
        raise RuntimeError("OpenCode shared server did not become ready.")

    def stop(self) -> None:
        process = self.process
        self.process = None
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=1.0)

    def health(self) -> dict[str, object]:
        process = self.process
        return {
            "running": process is not None and process.poll() is None,
            "pid": process.pid if process is not None else None,
            "endpoint": self.endpoint,
        }


class OpenCodeRuntime:
    """One persistent OpenCode session attached to a host-shared server."""

    def __init__(
        self,
        agent_id: str,
        *,
        endpoint: str,
        workspace: str | Path,
        state_dir: str | Path,
        model: str = "opencode-go/glm-5.2",
        variant: str = "",
        permission_mode: str = "meeting_read_only",
        server_pid: int | None = None,
        opener=urlopen,
    ) -> None:
        self.agent_id = clean_lobby_text(agent_id, limit=128)
        self.endpoint = str(endpoint or "").rstrip("/")
        self.workspace = Path(workspace).expanduser().resolve()
        self.state_dir = Path(state_dir).expanduser().resolve()
        self.model = clean_lobby_text(model, limit=256) or "opencode-go/glm-5.2"
        if "/" not in self.model:
            raise ValueError("OpenCode model must be provider/model.")
        self.provider_id, self.model_id = self.model.split("/", 1)
        self.variant = clean_lobby_text(variant, limit=64)
        self.permission_mode = clean_lobby_text(permission_mode, limit=64) or "meeting_read_only"
        self.server_pid = server_pid
        self._opener = opener
        self._session_id = ""
        self._running = False
        self._started_at = ""
        self._last_error = ""
        self._pending = ""
        self._response = None
        self._lock = threading.RLock()
        self._interrupted = threading.Event()

    def start(self) -> dict[str, object]:
        with self._lock:
            if self._running and self._session_id:
                return self.health()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        restored = self._load_session_id()
        if restored and self._session_exists(restored):
            session_id = restored
            reused = True
        else:
            session_id = self._create_session()
            reused = False
            self._save_session_id(session_id)
        with self._lock:
            self._session_id = session_id
            self._running = True
            self._started_at = self._started_at or _now()
            self._last_error = ""
            self._session_reused = reused
        return self.health()

    def send(self, text: str) -> None:
        content = str(text or "").strip()
        if not content:
            raise ValueError("OpenCode turn input is required.")
        self.start()
        with self._lock:
            if self._pending:
                raise RuntimeError("OpenCode runtime is already processing a turn.")
            self._pending = content
            self._interrupted.clear()

    def read_output(self, *, timeout_seconds: float, on_delta=None, on_activity=None) -> dict[str, object]:
        with self._lock:
            prompt = self._pending
            self._pending = ""
            session_id = self._session_id
        if not prompt or not session_id:
            raise RuntimeError("OpenCode runtime has no pending turn.")
        deadline = time.monotonic() + max(1.0, float(timeout_seconds))
        assistant_message_ids: set[str] = set()
        part_types: dict[str, str] = {}
        buffered_deltas: dict[tuple[str, str], list[str]] = {}
        emitted = ""
        activity_states: set[tuple[str, str]] = set()
        reasoning_active = False

        def emit_activity(part: dict[str, object]) -> None:
            nonlocal reasoning_active
            if on_activity is None:
                return
            part_id = str(part.get("id") or "")
            part_type = str(part.get("type") or "").casefold()
            if part_type == "reasoning":
                if not reasoning_active:
                    reasoning_active = True
                    on_activity({"category": "reasoning", "status": "running"})
                return
            if part_type not in {"tool", "tool_use", "tool-call", "toolcall"}:
                return
            state = part.get("state") if isinstance(part.get("state"), dict) else {}
            raw_status = str(state.get("status") or part.get("status") or "running").casefold()
            status = "completed" if raw_status in {"completed", "success", "done"} else "running"
            marker = (part_id, status)
            if marker in activity_states:
                return
            activity_states.add(marker)
            tool_name = str(part.get("tool") or part.get("name") or "")
            on_activity({"category": _tool_category(tool_name), "status": status})

        def emit_text_part(message_id: str, part_id: str) -> None:
            nonlocal emitted
            if message_id not in assistant_message_ids or part_types.get(part_id) != "text":
                return
            for delta in buffered_deltas.pop((message_id, part_id), []):
                emitted += delta
                if on_delta is not None:
                    on_delta(delta)

        try:
            event_response = self._open("GET", "/event", timeout_seconds=timeout_seconds, stream=True)
            with self._lock:
                self._response = event_response
            self._prompt_async(session_id, prompt, timeout_seconds=min(10.0, timeout_seconds))
            for raw_line in event_response:
                if self._interrupted.is_set():
                    raise RuntimeError("OpenCode turn interrupted.")
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"OpenCode runtime timed out after {timeout_seconds} seconds.")
                event = _sse_event(raw_line)
                if not event:
                    continue
                event_type = str(event.get("type") or "")
                properties = event.get("properties") if isinstance(event.get("properties"), dict) else {}
                if str(properties.get("sessionID") or "") != session_id:
                    continue
                if event_type == "message.updated":
                    info = properties.get("info") if isinstance(properties.get("info"), dict) else {}
                    message_id = str(info.get("id") or "")
                    if str(info.get("role") or "") == "assistant" and message_id:
                        assistant_message_ids.add(message_id)
                        for buffered_message_id, part_id in list(buffered_deltas):
                            if buffered_message_id == message_id:
                                emit_text_part(message_id, part_id)
                    continue
                if event_type == "message.part.updated":
                    part = properties.get("part") if isinstance(properties.get("part"), dict) else {}
                    message_id = str(part.get("messageID") or properties.get("messageID") or "")
                    part_id = str(part.get("id") or properties.get("partID") or "")
                    part_type = str(part.get("type") or "")
                    if part_id and part_type:
                        part_types[part_id] = part_type
                        emit_activity(part)
                        if part_type == "text":
                            emit_text_part(message_id, part_id)
                        else:
                            buffered_deltas.pop((message_id, part_id), None)
                    continue
                if event_type == "message.part.delta" and str(properties.get("field") or "") == "text":
                    message_id = str(properties.get("messageID") or "")
                    part_id = str(properties.get("partID") or "")
                    delta = str(properties.get("delta") or "")
                    if not delta or not part_id:
                        continue
                    if message_id in assistant_message_ids and part_types.get(part_id) == "text":
                        emitted += delta
                        if on_delta is not None:
                            on_delta(delta)
                    elif part_id not in part_types:
                        buffered_deltas.setdefault((message_id, part_id), []).append(delta)
                    continue
                if event_type == "session.idle":
                    if reasoning_active and on_activity is not None:
                        on_activity({"category": "reasoning", "status": "completed"})
                    break
            final = self._latest_assistant_text(session_id, timeout_seconds=max(1.0, deadline - time.monotonic()))
            content = final or emitted.strip()
            if not content:
                raise RuntimeError("OpenCode completed without a final assistant message.")
            if on_delta is not None and final.startswith(emitted):
                remainder = final[len(emitted) :]
                if remainder:
                    on_delta(remainder)
            with self._lock:
                self._last_error = ""
            return {
                "actor_id": self.agent_id,
                "actor_type": "agent",
                "kind": "agent_message",
                "content": content,
                "metadata": {"message_source": "opencode_sse", "model": self.model},
            }
        except Exception as error:
            with self._lock:
                self._last_error = type(error).__name__
            raise
        finally:
            with self._lock:
                response = self._response
                self._response = None
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass
    def interrupt(self) -> None:
        self._interrupted.set()
        with self._lock:
            response = self._response
            session_id = self._session_id
        if response is not None:
            try:
                response.close()
            except Exception:
                pass
        if session_id:
            try:
                self._open("POST", f"/session/{quote(session_id)}/abort", payload={}, timeout_seconds=5.0).close()
            except Exception:
                pass

    def stop(self, *, timeout_seconds: float = 2.0) -> None:
        del timeout_seconds
        self.interrupt()
        with self._lock:
            self._running = False
            self._pending = ""

    def health(self) -> dict[str, object]:
        with self._lock:
            return {
                "agent_id": self.agent_id,
                "runtime_kind": "opencode",
                "running": self._running,
                "transport": "http_sse",
                "pty": False,
                "is_one_shot": False,
                "pid": self.server_pid,
                "provider_session_active": bool(self._session_id),
                "provider_session_load_supported": True,
                "provider_session_reused": bool(getattr(self, "_session_reused", False)),
                "started_at": self._started_at,
                "last_error": self._last_error,
                "model": self.model,
                "variant": self.variant,
                "permission_mode": self.permission_mode,
            }

    def _create_session(self) -> str:
        permission_action = "deny" if self.permission_mode == "meeting_read_only" else "ask"
        payload = {
            "title": f"AgentsAssemble {self.agent_id}",
            "model": {"id": self.model_id, "providerID": self.provider_id, **({"variant": self.variant} if self.variant else {})},
            "permission": [{"permission": "*", "pattern": "*", "action": permission_action}],
        }
        response = self._open("POST", "/session", payload=payload, timeout_seconds=10.0)
        result = json.loads(response.read().decode("utf-8", errors="replace"))
        response.close()
        session_id = clean_lobby_text(result.get("id") if isinstance(result, dict) else "", limit=128)
        if not session_id:
            raise RuntimeError("OpenCode did not return a session id.")
        return session_id

    def _prompt_async(self, session_id: str, prompt: str, *, timeout_seconds: float) -> None:
        payload = {
            "model": {"providerID": self.provider_id, "modelID": self.model_id},
            "parts": [{"type": "text", "text": prompt}],
            "tools": {},
        }
        if self.variant:
            payload["variant"] = self.variant
        response = self._open(
            "POST",
            f"/session/{quote(session_id)}/prompt_async",
            payload=payload,
            timeout_seconds=timeout_seconds,
        )
        response.close()

    def _latest_assistant_text(self, session_id: str, *, timeout_seconds: float) -> str:
        response = self._open(
            "GET",
            f"/session/{quote(session_id)}/message",
            timeout_seconds=timeout_seconds,
        )
        payload = json.loads(response.read().decode("utf-8", errors="replace"))
        response.close()
        for message in reversed(payload if isinstance(payload, list) else []):
            info = message.get("info") if isinstance(message, dict) and isinstance(message.get("info"), dict) else {}
            if str(info.get("role") or "") != "assistant":
                continue
            parts = message.get("parts") if isinstance(message, dict) else []
            return "".join(
                str(part.get("text") or "")
                for part in parts if isinstance(part, dict) and part.get("type") == "text"
            ).strip()
        return ""

    def _session_exists(self, session_id: str) -> bool:
        try:
            response = self._open("GET", f"/session/{quote(session_id)}", timeout_seconds=5.0)
            response.close()
            return True
        except Exception:
            return False

    def _open(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, object] | None = None,
        timeout_seconds: float,
        stream: bool = False,
    ):
        query = urlencode({"directory": str(self.workspace)})
        url = f"{self.endpoint}{path}{'&' if '?' in path else '?'}{query}"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        request = Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Accept": "text/event-stream" if stream else "application/json",
            },
            method=method,
        )
        return self._opener(request, timeout=max(1.0, float(timeout_seconds)))

    def _load_session_id(self) -> str:
        path = self.state_dir / "session.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return ""
        if not isinstance(payload, dict) or payload.get("model") != self.model:
            return ""
        return clean_lobby_text(payload.get("session_id"), limit=128)

    def _save_session_id(self, session_id: str) -> None:
        path = self.state_dir / "session.json"
        path.write_text(
            json.dumps({"session_id": session_id, "model": self.model, "variant": self.variant}, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        try:
            path.chmod(0o600)
        except OSError:
            pass


def _tool_category(tool_name: str) -> str:
    value = str(tool_name or "").casefold()
    if any(word in value for word in ("read", "file", "open")):
        return "file_read"
    if any(word in value for word in ("search", "find", "grep", "glob")):
        return "search"
    if any(word in value for word in ("web", "http", "fetch", "browser")):
        return "web"
    if any(word in value for word in ("shell", "bash", "command", "exec", "terminal")):
        return "command"
    return "tool"


def _sse_event(raw_line: bytes) -> dict[str, object]:
    line = raw_line.decode("utf-8", errors="replace").strip()
    if not line.startswith("data:"):
        return {}
    try:
        payload = json.loads(line[5:].strip())
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


def _reserve_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
