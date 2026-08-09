"""Permanent public entrypoint over the rotating quick tunnel.

A Cloudflare Worker (infra/room-redirector) redirects one fixed URL to the
current public room URL stored in KV. Publications are asynchronous, but their
owner is persisted under the application output root so a rolling replacement
cannot be overwritten or cleared later by the process it replaced.

Publishing remains best-effort: when wrangler or its login is unavailable, the
direct public URL remains authoritative.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, BinaryIO

_REDIRECTOR_DIR = Path(__file__).resolve().parents[2] / "infra" / "room-redirector"
_CONFIG_PATH = _REDIRECTOR_DIR / "stable-entry.json"
_ANNOUNCE_TIMEOUT_SECONDS = 90
_ANNOUNCE_ATTEMPTS = 3
_ANNOUNCE_RETRY_DELAY_SECONDS = 15
_PROCESS_LOCKS_GUARD = threading.Lock()
_PROCESS_LOCKS: dict[str, threading.Lock] = {}


def stable_entry_config() -> dict[str, str] | None:
    try:
        payload = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    url = str(payload.get("url") or "").strip().rstrip("/")
    namespace_id = str(payload.get("namespace_id") or "").strip()
    if not url.startswith("https://") or not namespace_id:
        return None
    return {
        "url": url,
        "namespace_id": namespace_id,
        "kv_key": str(payload.get("kv_key") or "target").strip() or "target",
    }


def stable_entry_url() -> str:
    config = stable_entry_config()
    return config["url"] if config else ""


class StableEntryOwnershipError(RuntimeError):
    """Raised when a rolling replacement cannot safely claim publication."""


def _process_lock_for(path: Path) -> threading.Lock:
    key = str(path.resolve())
    with _PROCESS_LOCKS_GUARD:
        return _PROCESS_LOCKS.setdefault(key, threading.Lock())


@contextmanager
def _exclusive_file_lock(path: Path) -> Iterator[None]:
    """Serialize stable-entry ownership across both threads and processes."""

    path.parent.mkdir(parents=True, exist_ok=True)
    process_lock = _process_lock_for(path)
    with process_lock, path.open("a+b") as handle:
        _lock_file(handle)
        try:
            yield
        finally:
            _unlock_file(handle)


def _lock_file(handle: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)


def _unlock_file(handle: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class StableEntryPublisher:
    """Order stable-entry updates for one GUI process and rolling lineage."""

    def __init__(
        self,
        *,
        state_root: Path | None = None,
        owner_id: str = "",
        predecessor_owner_id: str = "",
        active: bool = True,
        config_provider: Callable[[], dict[str, str] | None] | None = None,
        command_runner: Callable[..., Any] | None = None,
    ) -> None:
        clean_owner = str(owner_id or "").strip()
        if state_root is not None and not clean_owner:
            raise ValueError("stable-entry owner_id is required with a state root")
        self._owner_id = clean_owner
        self._predecessor_owner_id = str(predecessor_owner_id or "").strip()
        self._state_dir = (
            Path(state_root) / "runtime" / "stable-entry"
            if state_root is not None
            else None
        )
        self._owner_path = self._state_dir / "owner.json" if self._state_dir else None
        self._lock_path = self._state_dir / "owner.lock" if self._state_dir else None
        self._config_provider = config_provider
        self._command_runner = command_runner
        self._state_lock = threading.Lock()
        self._publish_lock = threading.Lock()
        self._generation = 0
        self._active = bool(active)
        self._claimed = False
        self._pending_set = False
        self._pending_url: str | None = None
        if self._state_dir is not None and self._active:
            self._claim_owner()

    def announce(self, public_url: str) -> threading.Thread | None:
        """Point the stable worker at ``public_url`` asynchronously."""

        return self._schedule(public_url)

    def clear(self) -> threading.Thread | None:
        """Remove the stable target if this process still owns publication."""

        return self._schedule(None)

    def activate(self) -> threading.Thread | None:
        """Claim publication after a rolling parent has released the listener."""

        with self._state_lock:
            if self._active:
                return None
            self._claim_owner()
            self._active = True
            pending_set = self._pending_set
            pending_url = self._pending_url
            self._pending_set = False
            self._pending_url = None
        return self._schedule(pending_url) if pending_set else None

    def _schedule(self, public_url: str | None) -> threading.Thread | None:
        config = self._stable_entry_config()
        clean_url = str(public_url or "").strip().rstrip("/")
        deleting = public_url is None
        if not config or (not deleting and not clean_url.startswith("https://")):
            return None
        with self._state_lock:
            self._generation += 1
            generation = self._generation
            if not self._active:
                self._pending_set = True
                self._pending_url = None if deleting else clean_url
                return None

        def push() -> None:
            for attempt in range(1, _ANNOUNCE_ATTEMPTS + 1):
                with self._publish_lock:
                    if not self._is_current(generation):
                        return
                    try:
                        completed = self._run_if_owner(
                            self._wrangler_command(
                                config,
                                public_url=None if deleting else clean_url,
                            )
                        )
                    except StableEntryOwnershipError:
                        print(
                            "Stable entry publication stopped because ownership "
                            "could not be verified."
                        )
                        return
                    except (OSError, subprocess.TimeoutExpired):
                        completed = False
                    if completed is None:
                        return
                    if completed is not False and completed.returncode == 0:
                        if deleting:
                            print(f"Stable entry cleared: {config['url']}")
                        else:
                            print(f"Stable entry updated: {config['url']} -> {clean_url}")
                        return
                if attempt < _ANNOUNCE_ATTEMPTS:
                    if not self._is_current(generation):
                        return
                    time.sleep(_ANNOUNCE_RETRY_DELAY_SECONDS)
            operation_label = "clear" if deleting else "update"
            print(
                f"Stable entry {operation_label} failed "
                "(the direct public URL remains authoritative)."
            )

        thread = threading.Thread(
            target=push,
            daemon=True,
            name="AgentsAssembleStableEntryAnnounce",
        )
        thread.start()
        return thread

    def _claim_owner(self) -> None:
        if self._state_dir is None or self._owner_path is None or self._lock_path is None:
            return
        with _exclusive_file_lock(self._lock_path):
            current_owner = self._read_owner()
            if self._predecessor_owner_id:
                allowed = {"", self._owner_id, self._predecessor_owner_id}
                if current_owner not in allowed:
                    raise StableEntryOwnershipError(
                        "stable-entry publication belongs to another runtime"
                    )
            elif self._claimed and current_owner not in {"", self._owner_id}:
                raise StableEntryOwnershipError(
                    "stable-entry publication was superseded by another runtime"
                )
            self._write_owner(self._owner_id)
            self._claimed = True

    def _run_if_owner(self, command: list[str]) -> Any | None:
        if self._state_dir is None or self._lock_path is None:
            return self._run(command)
        with _exclusive_file_lock(self._lock_path):
            if self._read_owner() != self._owner_id:
                return None
            return self._run(command)

    def _run(self, command: list[str]) -> Any:
        runner = self._command_runner or subprocess.run
        return runner(
            command,
            cwd=_REDIRECTOR_DIR,
            capture_output=True,
            text=True,
            timeout=_ANNOUNCE_TIMEOUT_SECONDS,
            check=False,
        )

    def _stable_entry_config(self) -> dict[str, str] | None:
        provider = self._config_provider or stable_entry_config
        return provider()

    def _read_owner(self) -> str:
        assert self._owner_path is not None
        try:
            payload = json.loads(self._owner_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return ""
        except (OSError, json.JSONDecodeError) as error:
            raise StableEntryOwnershipError(
                "stable-entry owner state could not be verified"
            ) from error
        owner_id = str(payload.get("owner_id") or "").strip()
        if len(owner_id) > 256:
            raise StableEntryOwnershipError("stable-entry owner state is invalid")
        return owner_id

    def _write_owner(self, owner_id: str) -> None:
        assert self._owner_path is not None
        self._owner_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._owner_path.with_name(
            f"{self._owner_path.name}.tmp-{os.getpid()}-{threading.get_ident()}"
        )
        temporary.write_text(
            json.dumps({"owner_id": owner_id}, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(temporary, self._owner_path)

    def _is_current(self, generation: int) -> bool:
        with self._state_lock:
            return generation == self._generation

    @staticmethod
    def _wrangler_command(
        config: dict[str, str],
        *,
        public_url: str | None,
    ) -> list[str]:
        operation = (
            ["delete", config["kv_key"]]
            if public_url is None
            else ["put", config["kv_key"], public_url]
        )
        return [
            "npx",
            "wrangler",
            "kv",
            "key",
            *operation,
            f"--namespace-id={config['namespace_id']}",
            "--remote",
        ]


_PUBLISHER_LOCK = threading.Lock()
_DEFAULT_PUBLISHER = StableEntryPublisher()
_PUBLISHER = _DEFAULT_PUBLISHER


def configure_stable_entry_publisher(
    state_root: Path,
    *,
    owner_id: str,
    predecessor_owner_id: str = "",
    active: bool = True,
) -> StableEntryPublisher:
    """Bind stable publication to one server runtime's persisted ownership."""

    publisher = StableEntryPublisher(
        state_root=state_root,
        owner_id=owner_id,
        predecessor_owner_id=predecessor_owner_id,
        active=active,
    )
    global _PUBLISHER
    with _PUBLISHER_LOCK:
        _PUBLISHER = publisher
    return publisher


def activate_stable_entry_publisher() -> threading.Thread | None:
    with _PUBLISHER_LOCK:
        publisher = _PUBLISHER
    return publisher.activate()


def reset_stable_entry_publisher() -> None:
    global _PUBLISHER
    with _PUBLISHER_LOCK:
        _PUBLISHER = _DEFAULT_PUBLISHER


def announce_stable_entry(public_url: str) -> None:
    with _PUBLISHER_LOCK:
        publisher = _PUBLISHER
    publisher.announce(public_url)


def clear_stable_entry() -> None:
    with _PUBLISHER_LOCK:
        publisher = _PUBLISHER
    publisher.clear()
