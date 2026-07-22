"""Owned child-process runners for retained resident modes."""
from __future__ import annotations

import json
import subprocess
import threading
import urllib.parse
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agentsassemble.character_mode import clean_persona_card_id, normalize_character_mode
from agentsassemble.live_agent_runner import ResidentAgentConfig
from agentsassemble.persona_cards import load_persona_card


class SelfServiceResidentSupervisor:
    def __init__(
        self,
        config: ResidentAgentConfig,
        *,
        request_json,
        sleep_fn,
        process_env: Callable[[ResidentAgentConfig], dict[str, str]],
        process_factory: Callable[..., Any],
        terminate_process: Callable[[Any], None],
        supports_process_groups: Callable[[], bool],
        server_url: Callable[[str, str], str],
        monotonic: Callable[[], float],
        exit_error: Callable[[int], str],
        stop_event: threading.Event | None = None,
        isolate_process_group: bool = True,
    ) -> None:
        self.config = config
        self.request_json = request_json
        self.sleep_fn = sleep_fn
        self.process_env = process_env
        self.process_factory = process_factory
        self.terminate_process = terminate_process
        self.supports_process_groups = supports_process_groups
        self.server_url = server_url
        self.monotonic = monotonic
        self.exit_error = exit_error
        self.stop_event = stop_event or threading.Event()
        self.isolate_process_group = isolate_process_group
        self.process: Any | None = None
        self.closed = False
        self.last_heartbeat_at = 0.0
        self._lock = threading.Lock()

    def run(self) -> int:
        self._register()
        self._heartbeat("online")
        keep_error_presence = False
        try:
            process = self._start_process()
            return self._supervise(process)
        except subprocess.CalledProcessError as error:
            if not self.stop_event.is_set():
                keep_error_presence = self._heartbeat_safely(
                    "error",
                    last_error=self.exit_error(error.returncode),
                )
            raise
        finally:
            self.close()
            if not keep_error_presence:
                self._heartbeat_final_offline()

    def close(self) -> None:
        with self._lock:
            self.closed = True
            process = self.process
        if process is not None:
            self.terminate_process(process)

    def _start_process(self):
        if not self.config.command:
            raise ValueError("self_service resident requires --command.")
        with self._lock:
            if self.closed:
                raise RuntimeError("Self-service resident supervisor is closed.")
        supports_process_groups = self.supports_process_groups()
        process = self.process_factory(
            self.config.command,
            stdin=subprocess.DEVNULL,
            env=self.process_env(self.config),
            start_new_session=self.isolate_process_group and supports_process_groups,
        )
        if self.isolate_process_group and supports_process_groups:
            process_group_pid = getattr(process, "pid", None)
            if isinstance(process_group_pid, int) and process_group_pid > 0:
                setattr(process, "_agentsassemble_process_group_pid", process_group_pid)
        with self._lock:
            if self.closed:
                should_close = True
            else:
                self.process = process
                should_close = False
        if should_close:
            self.terminate_process(process)
            raise RuntimeError("Self-service resident supervisor is closed.")
        return process

    def _supervise(self, process) -> int:
        ticks = 0
        while not self.stop_event.is_set():
            return_code = process.poll()
            if return_code is not None:
                if return_code:
                    raise subprocess.CalledProcessError(return_code, self.config.command)
                return 0
            ticks += 1
            if self.config.max_ticks and ticks >= self.config.max_ticks:
                return 0
            self._heartbeat_if_due()
            self.sleep_fn(self.config.poll_interval)
        return 0

    def _register(self) -> None:
        persona_card_id = clean_persona_card_id(self.config.persona_id)
        if not persona_card_id and self.config.persona_path:
            try:
                persona_card_id = clean_persona_card_id(load_persona_card(Path(self.config.persona_path)).id)
            except (OSError, ValueError, json.JSONDecodeError):
                persona_card_id = ""
        character_mode = normalize_character_mode(
            self.config.character_mode,
            has_card=bool(persona_card_id or self.config.persona_path),
        )
        self.request_json(
            self.server_url(self.config.server, "/api/live-agents"),
            method="POST",
            payload={
                "agent_id": self.config.agent_id,
                "display_name": self.config.display_name,
                "provider_kind": self.config.provider_kind,
                "connection_kind": self.config.connection_kind,
                "session_id": self.config.session_id,
                "endpoint": self.config.endpoint,
                "meeting_id": self.config.meeting_id,
                "engagement_mode": self.config.engagement_mode,
                "persona_card_id": persona_card_id,
                "character_mode": character_mode,
                "capabilities": ["room_chat", "mentions", "self_service"],
            },
        )

    def _heartbeat(self, status: str, **metadata: object) -> None:
        payload = {"status": status, **metadata}
        if self.config.session_id:
            payload.setdefault("session_id", self.config.session_id)
        self.request_json(
            self.server_url(
                self.config.server,
                f"/api/live-agents/{urllib.parse.quote(self.config.agent_id, safe='')}/heartbeat",
            ),
            method="POST",
            payload=payload,
        )
        self.last_heartbeat_at = self.monotonic()

    def _heartbeat_if_due(self) -> None:
        if self.config.heartbeat_interval <= 0:
            return
        if self.monotonic() - self.last_heartbeat_at >= self.config.heartbeat_interval:
            self._heartbeat_safely("online", preserve_status=True)

    def _heartbeat_safely(self, status: str, **metadata: object) -> bool:
        try:
            self._heartbeat(status, **metadata)
        except Exception:
            return False
        return True

    def _heartbeat_final_offline(self) -> None:
        self._heartbeat_safely("offline")


class LocalCliCommandRunner:
    def __init__(
        self,
        *,
        process_factory: Callable[..., Any],
        terminate_process: Callable[[Any], None],
        supports_process_groups: Callable[[], bool],
    ) -> None:
        self.process_factory = process_factory
        self.terminate_process = terminate_process
        self.supports_process_groups = supports_process_groups
        self.process: Any | None = None
        self.closed = False
        self._lock = threading.Lock()

    def __call__(self, command: list[str], prompt: str, *, timeout_seconds: int) -> str:
        if not command:
            raise ValueError("Delegate command is required.")
        with self._lock:
            if self.closed:
                raise RuntimeError("Local CLI runner is closed.")
        supports_process_groups = self.supports_process_groups()
        process = self.process_factory(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=supports_process_groups,
        )
        if supports_process_groups:
            process_group_pid = getattr(process, "pid", None)
            if isinstance(process_group_pid, int) and process_group_pid > 0:
                setattr(process, "_agentsassemble_process_group_pid", process_group_pid)
        with self._lock:
            if self.closed:
                should_close = True
            else:
                self.process = process
                should_close = False
        if should_close:
            self.terminate_process(process)
            raise RuntimeError("Local CLI runner is closed.")
        try:
            try:
                stdout, stderr = process.communicate(input=prompt, timeout=timeout_seconds)
            except subprocess.TimeoutExpired as error:
                self.terminate_process(process)
                stdout, stderr = process.communicate()
                raise subprocess.TimeoutExpired(
                    command,
                    timeout_seconds,
                    output=stdout,
                    stderr=stderr,
                ) from error
            if process.returncode:
                raise subprocess.CalledProcessError(
                    process.returncode,
                    command,
                    output=stdout,
                    stderr=stderr,
                )
            return stdout
        except BaseException:
            self.terminate_process(process)
            raise
        finally:
            with self._lock:
                if self.process is process:
                    self.process = None

    def close(self) -> None:
        with self._lock:
            self.closed = True
            process = self.process
        if process is not None:
            self.terminate_process(process)
