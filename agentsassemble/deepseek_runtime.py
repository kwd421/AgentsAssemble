from __future__ import annotations

import json
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from typing import IO
from urllib.request import Request, urlopen

from agentsassemble.meeting_events import clean_lobby_text


UrlOpen = Callable[..., IO[bytes]]


class DeepSeekApiRuntime:
    """Persistent conversation state behind the room bridge streaming contract."""

    def __init__(
        self,
        agent_id: str,
        *,
        api_key: str,
        model: str = "deepseek-v4-flash",
        reasoning_effort: str = "high",
        thinking: bool = True,
        base_url: str = "https://api.deepseek.com",
        opener: UrlOpen = urlopen,
    ) -> None:
        if not str(api_key or "").strip():
            raise RuntimeError("credential_missing")
        self.agent_id = clean_lobby_text(agent_id, limit=128)
        self._api_key = str(api_key).strip()
        self.model = clean_lobby_text(model, limit=128) or "deepseek-v4-flash"
        if self.model not in {"deepseek-v4-flash", "deepseek-v4-pro"}:
            raise ValueError(f"Unsupported DeepSeek model: {self.model}")
        self.reasoning_effort = clean_lobby_text(reasoning_effort, limit=32) or "high"
        if self.reasoning_effort not in {"high", "max"}:
            raise ValueError("DeepSeek reasoning_effort must be high or max.")
        self.thinking = bool(thinking)
        self.base_url = str(base_url or "").rstrip("/")
        self._opener = opener
        self._messages: list[dict[str, str]] = []
        self._pending = ""
        self._running = False
        self._started_at = ""
        self._last_error = ""
        self._interrupted = threading.Event()
        self._lock = threading.RLock()
        self._response: IO[bytes] | None = None

    def start(self) -> dict[str, object]:
        with self._lock:
            self._running = True
            self._started_at = self._started_at or _now()
            self._last_error = ""
        return self.health()

    def send(self, text: str) -> None:
        content = str(text or "").strip()
        if not content:
            raise ValueError("DeepSeek turn input is required.")
        self.start()
        with self._lock:
            if self._pending:
                raise RuntimeError("DeepSeek runtime is already processing a turn.")
            self._pending = content
            self._interrupted.clear()

    def read_output(self, *, timeout_seconds: float, on_delta=None, on_activity=None) -> dict[str, object]:
        del on_activity
        with self._lock:
            prompt = self._pending
            self._pending = ""
            messages = [*self._messages, {"role": "user", "content": prompt}]
        if not prompt:
            raise RuntimeError("DeepSeek runtime has no pending turn.")
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "thinking": {"type": "enabled" if self.thinking else "disabled"},
            "reasoning_effort": self.reasoning_effort,
        }
        request = Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
            },
            method="POST",
        )
        content_parts: list[str] = []
        observed_model_id = ""
        try:
            response = self._opener(request, timeout=max(1.0, float(timeout_seconds)))
            with self._lock:
                self._response = response
            for raw_line in response:
                if self._interrupted.is_set():
                    raise RuntimeError("DeepSeek turn interrupted.")
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                chunk = json.loads(data)
                if isinstance(chunk, dict):
                    observed_model_id = clean_lobby_text(chunk.get("model"), limit=128) or observed_model_id
                choices = chunk.get("choices") if isinstance(chunk, dict) else None
                delta = choices[0].get("delta") if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
                text = str(delta.get("content") or "") if isinstance(delta, dict) else ""
                # reasoning_content stays provider-private and never enters the room stream.
                if text:
                    content_parts.append(text)
                    if on_delta is not None:
                        on_delta(text)
            content = "".join(content_parts).strip()
            if not content:
                raise RuntimeError("DeepSeek completed without a final message.")
            with self._lock:
                self._messages = _bounded_messages(
                    [*messages, {"role": "assistant", "content": content}]
                )
                self._last_error = ""
            return {
                "outcome": "message",
                "actor_id": self.agent_id,
                "actor_type": "agent",
                "kind": "agent_message",
                "content": content,
                "metadata": {
                    "message_source": "deepseek_sse",
                    "model": self.model,
                    "observed_model_id": observed_model_id,
                },
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
        if response is not None:
            try:
                response.close()
            except Exception:
                pass

    def stop(self, *, timeout_seconds: float = 2.0) -> None:
        del timeout_seconds
        self.interrupt()
        with self._lock:
            self._running = False
            self._pending = ""
            self._api_key = ""

    def health(self) -> dict[str, object]:
        with self._lock:
            return {
                "agent_id": self.agent_id,
                "runtime_kind": "api",
                "running": self._running,
                "transport": "https_sse",
                "pty": False,
                "is_one_shot": False,
                "provider_session_active": self._running,
                "provider_session_reused": bool(self._messages),
                "started_at": self._started_at,
                "last_error": self._last_error,
                "model": self.model,
                "reasoning_effort": self.reasoning_effort,
                "variant": "thinking" if self.thinking else "non_thinking",
                "permission_mode": "meeting_read_only",
            }


def _bounded_messages(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    bounded = messages[-50:]
    while bounded and sum(len(item.get("content", "")) for item in bounded) > 64_000:
        bounded.pop(0)
    return bounded


def _now() -> str:
    return datetime.now(UTC).isoformat()
