from __future__ import annotations

from agentsassemble.agent_sessions import CodexAppServerRuntime, codex_app_server_runtime_command
from agentsassemble.meeting_events import clean_lobby_text


class CodexAppServerLiveRuntime:
    """Expose Codex CLI app-server through the persistent room runtime contract."""

    def __init__(
        self,
        agent_id: str,
        *,
        workspace: str,
        model: str,
        reasoning_effort: str,
        permission_mode: str,
    ) -> None:
        sandbox = "workspace-write" if permission_mode == "workspace_write" else "read-only"
        permissions = "on-request" if permission_mode == "workspace_write" else "never"
        self.agent_id = clean_lobby_text(agent_id, limit=128)
        self.profile = {
            "workspace": workspace,
            "model": model,
            "effort": reasoning_effort,
            "sandbox": sandbox,
            "permissions": permissions,
        }
        self.runtime = CodexAppServerRuntime(
            command=codex_app_server_runtime_command(self.profile),
            profile_settings=self.profile,
        )
        self.handle: dict[str, object] = {"session_id": self.agent_id}
        self.pending = ""
        self.running = False

    def start(self) -> dict[str, object]:
        self.runtime.start(self.profile)
        self.running = True
        return self.health()

    def send(self, text: str) -> None:
        content = str(text or "").strip()
        if not content:
            raise ValueError("Codex turn input is required.")
        self.start()
        if self.pending:
            raise RuntimeError("Codex runtime is already processing a turn.")
        self.pending = content

    def read_output(self, *, timeout_seconds: float, on_delta=None, on_activity=None) -> dict[str, object]:
        prompt = self.pending
        self.pending = ""
        if not prompt:
            raise RuntimeError("Codex runtime has no pending turn.")
        final = ""
        errors: list[str] = []
        for chunk in self.runtime.send_turn(
            self.handle,
            {"provider_input": prompt, "timeout_seconds": timeout_seconds, "workspace": self.profile["workspace"]},
        ):
            chunk_type = str(chunk.get("type") or "")
            if chunk_type == "provider_session":
                self.handle.update(chunk)
            elif chunk_type == "message_delta":
                delta = str(chunk.get("content") or "")
                if delta:
                    final += delta
                    if on_delta is not None:
                        on_delta(delta)
            elif chunk_type == "thinking_delta" and on_activity is not None:
                on_activity(_codex_activity(chunk.get("content")))
            elif chunk_type == "message_final":
                final = str(chunk.get("content") or final)
            elif chunk_type == "error":
                errors.append(str(chunk.get("diagnostics") or "Codex app-server turn failed."))
        if not final.strip():
            raise RuntimeError(errors[-1] if errors else "Codex completed without a final message.")
        return {
            "actor_id": self.agent_id,
            "actor_type": "agent",
            "kind": "agent_message",
            "content": final.strip(),
            "metadata": {"message_source": "codex_app_server"},
        }

    def interrupt(self) -> None:
        self.stop()

    def stop(self, *, timeout_seconds: float = 2.0) -> None:
        del timeout_seconds
        self.runtime.detach(self.handle)
        self.running = False
        self.pending = ""

    def health(self) -> dict[str, object]:
        diagnostics = self.runtime.diagnose(self.handle)
        return {
            "agent_id": self.agent_id,
            "runtime_kind": "live_cli",
            "running": self.running,
            "transport": "stdio_jsonl",
            "pty": False,
            "is_one_shot": False,
            "pid": diagnostics.get("app_server_pid"),
            "provider_session_active": bool(self.handle.get("provider_session_id")),
            "provider_session_reused": bool(diagnostics.get("thread_reused")),
            "model": self.profile["model"],
            "reasoning_effort": self.profile["effort"],
            "permission_mode": "workspace_write" if self.profile["sandbox"] == "workspace-write" else "meeting_read_only",
            **diagnostics,
        }


def _codex_activity(value: object) -> dict[str, str]:
    text = clean_lobby_text(value, limit=500).casefold()
    status = "completed" if "finished" in text or "completed" in text else "running"
    if "thinking" in text or "reason" in text:
        category = "reasoning"
    elif any(word in text for word in ("read", "cat ", "sed ", "open file")):
        category = "file_read"
    elif any(word in text for word in ("search", "find", "grep", "rg ")):
        category = "search"
    elif any(word in text for word in ("http", "web", "curl", "fetch")):
        category = "web"
    elif "command" in text:
        category = "command"
    else:
        category = "tool"
    return {"category": category, "status": status}
