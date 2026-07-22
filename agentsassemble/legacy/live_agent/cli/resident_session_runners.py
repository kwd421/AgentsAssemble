"""Persistent JSONL and terminal session runners for legacy residents."""
from __future__ import annotations

import threading
from pathlib import Path

from agentsassemble.providers.live_session_transport import JsonlLiveSession, TerminalLiveSession


class JsonlLiveSessionCommandRunner:
    def __init__(self) -> None:
        self.session: JsonlLiveSession | None = None
        self._lock = threading.Lock()

    def __call__(self, command: list[str], prompt: str, *, timeout_seconds: int) -> str:
        with self._lock:
            if self.session is None:
                self.session = JsonlLiveSession(command)
            session = self.session
        try:
            return session.ask(prompt, timeout_seconds=timeout_seconds)
        except Exception:
            self._close_session(session)
            raise

    def close(self) -> None:
        with self._lock:
            session = self.session
            self.session = None
        if session is not None:
            session.close()

    def _close_session(self, session: JsonlLiveSession) -> None:
        with self._lock:
            if self.session is session:
                self.session = None
        session.close()


class TerminalLiveSessionCommandRunner:
    def __init__(
        self,
        *,
        idle_timeout_seconds: float,
        cwd: Path | None = None,
        message_extractor=None,
        ready_predicate=None,
        submit_newline: str = "\n",
        submit_settle_seconds: float = 0.0,
        warmup_idle_seconds: float = 0.0,
        stream_config=None,
        permission_mode: str = "",
        fast_mode: bool = False,
    ) -> None:
        self.idle_timeout_seconds = idle_timeout_seconds
        self.cwd = Path(cwd or Path.cwd())
        self.session: TerminalLiveSession | None = None
        self._message_extractor = message_extractor
        self._ready_predicate = ready_predicate
        self._submit_newline = submit_newline
        self._submit_settle_seconds = submit_settle_seconds
        self._warmup_idle_seconds = warmup_idle_seconds
        self._lock = threading.Lock()
        self._permission_mode = str(permission_mode or "").strip()
        self._fast_mode = bool(fast_mode)
        self._stream_config = stream_config
        self._stream_session_id = ""
        if stream_config is not None:
            from agentsassemble.providers.claude_transcript import generate_claude_session_id

            self._stream_session_id = generate_claude_session_id()

    def __call__(self, command: list[str], prompt: str, *, timeout_seconds: int) -> str:
        with self._lock:
            if self.session is None:
                launch_command = list(command)
                if self._permission_mode and "--permission-mode" not in launch_command:
                    launch_command = [*launch_command, "--permission-mode", self._permission_mode]
                if self._stream_config is not None and "--session-id" not in launch_command:
                    launch_command = [*launch_command, "--session-id", self._stream_session_id]
                self.session = TerminalLiveSession(
                    launch_command,
                    idle_timeout_seconds=self.idle_timeout_seconds,
                    cwd=self.cwd,
                    message_extractor=self._message_extractor,
                    ready_predicate=self._ready_predicate,
                    submit_newline=self._submit_newline,
                    submit_settle_seconds=self._submit_settle_seconds,
                    warmup_idle_seconds=self._warmup_idle_seconds,
                )
                if self._fast_mode:
                    try:
                        self.session.submit_slash_command("/fast")
                    except Exception:
                        pass
            session = self.session
        if self._stream_config is None:
            try:
                return session.ask(prompt, timeout_seconds=timeout_seconds)
            except Exception:
                self._close_session(session)
                raise
        return self._run_streaming_turn(session, prompt, timeout_seconds=timeout_seconds)

    def _run_streaming_turn(self, session: TerminalLiveSession, prompt: str, *, timeout_seconds: int) -> str:
        from agentsassemble.providers.claude_transcript import (
            ClaudeTranscriptTailer,
            find_claude_transcript,
            tail_until,
        )
        from agentsassemble.room_thought import post_room_thought

        config = self._stream_config
        session_id = self._stream_session_id
        done = {"value": False}

        def run_tailer() -> None:
            tailer = ClaudeTranscriptTailer(lambda: find_claude_transcript(session_id))

            def on_event(event: dict) -> None:
                if event["kind"] == "command":
                    post_room_thought(config, f"🔧 {event['text']}", kind="command")
                elif event["kind"] == "reasoning" and event["text"].strip():
                    post_room_thought(config, event["text"], kind="reasoning")

            tail_until(tailer, lambda: done["value"], on_event)

        tail_thread = threading.Thread(target=run_tailer, daemon=True)
        tail_thread.start()
        try:
            return session.ask(prompt, timeout_seconds=timeout_seconds)
        except Exception:
            self._close_session(session)
            raise
        finally:
            done["value"] = True
            tail_thread.join(timeout=2)

    def close(self) -> None:
        with self._lock:
            session = self.session
            self.session = None
        if session is not None:
            session.close()

    def _close_session(self, session: TerminalLiveSession) -> None:
        with self._lock:
            if self.session is session:
                self.session = None
        session.close()
