"""Freebuff subscription CLI runtime using PTY without inventing structured events.

Freebuff does not expose a structured protocol. This adapter:
- launches the installed ``freebuff`` executable over PTY
- discovers model labels from the live selection screen
- caches label indexes by freebuff version
- selects DeepSeek V4 Flash by name (never by fixed menu number)
- publishes only final speech; no synthetic reasoning or tool events
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

from agentsassemble.providers.live_cli import LiveCliRuntime
from agentsassemble.providers.room_portal import RoomPortal
from agentsassemble.room.text import clean_room_text

_DEEPSEEK_FLASH_PATTERNS = (
    re.compile(r"deepseek\s*v?4\s*flash", re.IGNORECASE),
    re.compile(r"deepseek.*flash.*07\s*/\s*31", re.IGNORECASE),
    re.compile(r"deepseek.*flash", re.IGNORECASE),
)
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*(?:\x07|\x1b\\)")
_CACHE_LOCK = threading.Lock()
_LABEL_CACHE: dict[str, dict[str, int]] = {}


class FreebuffUnavailable(RuntimeError):
    pass


class FreebuffRuntime:
    """Subscription Freebuff session with label-scanned model selection."""

    def __init__(
        self,
        agent_id: str,
        *,
        workspace: str | Path,
        state_dir: str | Path,
        model: str = "DeepSeek V4 Flash",
        permission_mode: str = "workspace_write",
        executable: str = "freebuff",
        room_portal: RoomPortal | None = None,
        terminal_runtime_factory=LiveCliRuntime,
        environment: dict[str, str] | None = None,
    ) -> None:
        self.agent_id = clean_room_text(agent_id, limit=128)
        self.workspace = Path(workspace).expanduser().resolve()
        self.state_dir = Path(state_dir).expanduser().resolve()
        self.model = clean_room_text(model, limit=128) or "DeepSeek V4 Flash"
        self.permission_mode = clean_room_text(permission_mode, limit=64) or "workspace_write"
        self.executable = executable
        self._room_portal = room_portal
        self._terminal_runtime_factory = terminal_runtime_factory
        self._environment = dict(environment or {})
        self._terminal = None
        self._selected_model = ""
        self._version = ""
        self._running = False
        self._last_error = ""
        self._pending = ""
        self._lock = threading.RLock()

    def set_request_handler(self, handler) -> None:
        del handler  # Freebuff has no structured approval channel.

    def start(self) -> dict[str, object]:
        with self._lock:
            if self._running and self._terminal is not None:
                return self.health()
        resolved = (
            self.executable
            if Path(self.executable).is_absolute()
            else shutil.which(self.executable)
        )
        if not resolved:
            raise FreebuffUnavailable("Freebuff CLI is not installed.")
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.workspace.mkdir(parents=True, exist_ok=True)
        self._version = _freebuff_version(resolved)
        self._terminal = self._terminal_runtime_factory(
            self.agent_id,
            [resolved, "--cwd", str(self.workspace)],
            cwd=str(self.workspace),
            env=self._environment,
            input_mode="bracketed_paste",
            submit_newline="\r",
            idle_quiet_seconds=5.0,
            startup_quiet_seconds=0.5,
            startup_timeout_seconds=30.0,
            profile_settings={
                "model": self.model,
                "permission_mode": self.permission_mode,
            },
        )
        self._terminal.start()
        try:
            self._selected_model = self._select_model_by_label()
        except Exception:
            self.stop()
            raise
        self._running = True
        self._last_error = ""
        return self.health()

    def send(self, text: str) -> None:
        content = str(text or "").strip()
        if not content:
            raise ValueError("Freebuff turn input is required.")
        self.start()
        with self._lock:
            if self._pending:
                raise RuntimeError("Freebuff runtime is already processing a turn.")
            self._pending = content
            self._terminal.send(content)

    def send_room_observation(self, text: str, *, media_blocks=None) -> None:
        del media_blocks
        self.send(text)

    def read_output(self, *, timeout_seconds: float, on_delta=None, on_activity=None):
        del on_activity  # Do not invent tool/reasoning events from PTY noise.
        with self._lock:
            prompt = self._pending
            self._pending = ""
        if not prompt:
            raise RuntimeError("Freebuff runtime has no pending turn.")
        result = self._terminal.read_output(
            timeout_seconds=timeout_seconds,
            on_delta=on_delta,
            on_activity=None,
        )
        content = ""
        if isinstance(result, dict):
            content = str(result.get("content") or result.get("text") or "").strip()
        elif isinstance(result, str):
            content = result.strip()
        return {
            "content": content,
            "observed_model_id": self._selected_model or self.model,
            "runtime_kind": "live_cli",
            "execution_harness": "builtin",
            "unsupported": [
                "tool_events",
                "public_reasoning",
                "approvals",
                "choices",
                "structured_protocol",
            ],
        }

    def interrupt(self) -> None:
        if self._terminal is not None:
            interrupt = getattr(self._terminal, "interrupt", None)
            if callable(interrupt):
                interrupt()

    def stop(self, *, timeout_seconds: float = 2.0) -> None:
        terminal = self._terminal
        self._terminal = None
        self._running = False
        if terminal is not None:
            terminal.stop(timeout_seconds=timeout_seconds)

    def health(self) -> dict[str, object]:
        terminal_health = {}
        if self._terminal is not None:
            reader = getattr(self._terminal, "health", None)
            if callable(reader):
                terminal_health = dict(reader())
        return {
            **terminal_health,
            "running": self._running,
            "runtime_kind": "live_cli",
            "provider_kind": "freebuff_live_session",
            "selected_model": self._selected_model,
            "requested_model": self.model,
            "freebuff_version": self._version,
            "unsupported": [
                "tool_events",
                "public_reasoning",
                "approvals",
                "choices",
                "structured_protocol",
            ],
            "last_error": self._last_error,
        }

    def _select_model_by_label(self) -> str:
        """Find the DeepSeek V4 Flash label on the live screen and activate it."""

        target = self.model
        cache_key = self._version or "unknown"
        deadline = time.monotonic() + 30.0
        screen = ""
        labels: list[str] = []
        match = None
        while time.monotonic() < deadline and match is None:
            screen += self._capture_screen(
                seconds=min(1.0, max(0.2, deadline - time.monotonic()))
            )
            startup_error = _freebuff_startup_error(screen)
            if startup_error:
                self._last_error = startup_error
                raise FreebuffUnavailable(startup_error)
            labels = _extract_model_labels(screen)
            match = _match_model_label(labels, target)
        if match is None:
            self._last_error = (
                f"Could not find model label matching {target!r} on the Freebuff "
                f"selection screen (version={cache_key}). Visible labels: "
                f"{', '.join(labels) or '(none)'}."
            )
            raise FreebuffUnavailable(self._last_error)
        index, label = match
        with _CACHE_LOCK:
            _LABEL_CACHE.setdefault(cache_key, {})[label.casefold()] = index
        self._persist_cache()
        # Prefer arrow navigation from current focus rather than absolute digits.
        self._navigate_to_index(index, labels)
        self._submit_selection()
        return label

    def _capture_screen(self, *, seconds: float) -> str:
        deadline = time.monotonic() + max(0.2, seconds)
        last = ""
        while time.monotonic() < deadline:
            reader = getattr(self._terminal, "read_available", None)
            if callable(reader):
                available = reader(
                    timeout_seconds=min(
                        0.15,
                        max(0.0, deadline - time.monotonic()),
                    )
                )
                if isinstance(available, dict):
                    visible = str(
                        available.get("content")
                        or available.get("terminal_output")
                        or ""
                    )
                    if visible:
                        last += visible
            health = self._terminal.health() if self._terminal is not None else {}
            tail = str(health.get("terminal_tail") or "")
            if tail:
                last += tail
            time.sleep(0.15)
        return _strip_ansi(last)

    def _navigate_to_index(self, index: int, labels: list[str]) -> None:
        del labels
        if self._terminal is None or not hasattr(self._terminal, "send_keys"):
            raise FreebuffUnavailable("Freebuff terminal cannot send navigation keys.")
        # Move to top, then down by index. No digit/menu ordinal hardcoding.
        for _ in range(12):
            self._terminal.send_keys("\x1b[A")
            time.sleep(0.03)
        for _ in range(max(0, index)):
            self._terminal.send_keys("\x1b[B")
            time.sleep(0.03)

    def _submit_selection(self) -> None:
        if self._terminal is not None and hasattr(self._terminal, "send_keys"):
            self._terminal.send_keys("\r")
            time.sleep(0.2)

    def _persist_cache(self) -> None:
        path = self.state_dir / "freebuff-model-label-cache.json"
        try:
            with _CACHE_LOCK:
                path.write_text(json.dumps(_LABEL_CACHE, indent=2) + "\n", encoding="utf-8")
        except OSError:
            pass


def _freebuff_version(executable: str) -> str:
    try:
        completed = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    text = (completed.stdout or completed.stderr or "").strip()
    match = re.search(r"\d+\.\d+\.\d+", text)
    return match.group(0) if match else (text.splitlines()[0] if text else "unknown")


def _freebuff_startup_error(screen: str) -> str:
    clean = " ".join(str(screen or "").split())
    marker = "freebuff session GET failed:"
    start = clean.casefold().find(marker.casefold())
    if start < 0:
        return ""
    return clean_room_text(clean[start : start + 500], limit=500)


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _extract_model_labels(screen: str) -> list[str]:
    labels: list[str] = []
    for raw_line in screen.splitlines():
        line = " ".join(raw_line.strip().split())
        if not line or len(line) > 120:
            continue
        if "›" in line and line.count("│") >= 2:
            continue
        folded = line.casefold()
        if any(
            token in folded
            for token in (
                "deepseek",
                "gpt",
                "minimax",
                "mimo",
                "gemini",
                "glm",
                "flash",
                "pro",
                "luna",
            )
        ):
            # Drop pure chrome lines.
            if folded in {"model", "models", "select a model", "choose model"}:
                continue
            labels.append(line)
    if not labels:
        # Full-screen TUIs repaint with cursor-position escape sequences rather
        # than newlines. After stripping ANSI the cards can be one flattened
        # line, but each model label still begins at a selected marker or card
        # edge and ends at the padding before its description.
        matches = []
        for pattern in (
            re.compile(r"›\s*([A-Za-z][A-Za-z0-9 .:/_-]{1,60}?)(?=\s{2,})"),
            re.compile(r"│\s{2}([A-Za-z][A-Za-z0-9 .:/_-]{1,60}?)(?=\s{2,})"),
        ):
            matches.extend(
                (match.start(), " ".join(match.group(1).split()))
                for match in pattern.finditer(screen)
            )
        labels.extend(label for _position, label in sorted(matches))
    # Preserve screen order, unique by casefold.
    seen: set[str] = set()
    ordered: list[str] = []
    for label in labels:
        key = label.casefold()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(label)
    return ordered


def _match_model_label(labels: list[str], requested: str) -> tuple[int, str] | None:
    requested_clean = " ".join(str(requested or "").split())
    if not requested_clean:
        requested_clean = "DeepSeek V4 Flash"
    requested_fold = requested_clean.casefold()
    for index, label in enumerate(labels):
        if label.casefold() == requested_fold:
            return index, label
    for index, label in enumerate(labels):
        if requested_fold in label.casefold() or label.casefold() in requested_fold:
            return index, label
    for pattern in _DEEPSEEK_FLASH_PATTERNS:
        for index, label in enumerate(labels):
            if pattern.search(label):
                return index, label
    return None


def load_freebuff_label_cache(path: Path) -> None:
    global _LABEL_CACHE
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if isinstance(document, dict):
        with _CACHE_LOCK:
            _LABEL_CACHE = {
                str(version): {
                    str(label): int(index)
                    for label, index in dict(labels).items()
                    if str(label) and str(index).lstrip("-").isdigit()
                }
                for version, labels in document.items()
                if isinstance(labels, dict)
            }


def freebuff_command(
    model: str,
    _effort: str,
    _service_tier: str,
    _variant: str,
    _permission_mode: str,
) -> tuple[str, ...]:
    # Model is selected from the live Freebuff screen by label, not argv ordinal.
    del model
    return ("freebuff",)


__all__ = [
    "FreebuffRuntime",
    "FreebuffUnavailable",
    "freebuff_command",
    "load_freebuff_label_cache",
]
