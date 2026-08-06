"""Workspace-scoped tools for OpenAI-compatible API Agent Sessions.

The work harness is deliberately smaller than a native coding CLI.  It gives a
tool-capable model enough primitives to inspect and change one operator-selected
workspace without giving the remote model an ambient shell or filesystem.
"""

from __future__ import annotations

import json
import os
import signal
import stat
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path

from agentsassemble.providers.api_work_tool_schemas import work_tool_schemas
from agentsassemble.providers.provider_requests import ProviderRequestHandler


SIDE_EFFECT_WORK_TOOLS = frozenset(
    {"write_workspace_file", "replace_workspace_text", "run_workspace_command"}
)
_POST_TERMINATION_DRAIN_SECONDS = 2.0


class ApiWorkApprovalDenied(PermissionError):
    """A workspace side effect stopped before execution because approval failed."""


class ApiWorkHarness:
    """Execute bounded work tools inside one canonical workspace root."""

    def __init__(
        self,
        workspace: str | Path,
        *,
        permission_mode: str,
        request_handler: ProviderRequestHandler | None = None,
        interrupt_requested: Callable[[], bool] | None = None,
    ) -> None:
        self.workspace = Path(workspace).expanduser().resolve()
        self.permission_mode = str(permission_mode or "meeting_read_only")
        self.request_handler = request_handler
        self._interrupt_requested = interrupt_requested or (lambda: False)
        self._process_lock = threading.RLock()
        self._active_process: subprocess.Popen[str] | None = None
        self._active_process_group = False
        self._command_interrupted = threading.Event()

    @property
    def enabled(self) -> bool:
        return self.permission_mode == "workspace_write"

    def execute(self, name: str, arguments: dict[str, object]) -> object:
        if not self.enabled:
            raise RuntimeError("The API work harness is not enabled for this Agent Session.")
        self._raise_if_interrupted()
        if name == "list_workspace_files":
            return self._list_files(arguments)
        if name == "read_workspace_file":
            return self._read_file(arguments)
        if name == "search_workspace_text":
            return self._search_text(arguments)
        if name == "write_workspace_file":
            return self._write_file(arguments)
        if name == "replace_workspace_text":
            return self._replace_text(arguments)
        if name == "run_workspace_command":
            return self._run_command(arguments)
        raise RuntimeError(f"Unsupported API work tool: {name or '(missing)'}.")

    def _list_files(self, arguments: dict[str, object]) -> dict[str, object]:
        base = self._path(arguments.get("path") or ".", directory=True)
        files: list[str] = []
        for path in sorted(base.rglob("*")):
            if len(files) >= 500:
                break
            if _safe_discovered_file(path, self.workspace) is not None:
                files.append(path.relative_to(self.workspace).as_posix())
        return {"files": files, "truncated": len(files) >= 500}

    def _read_file(self, arguments: dict[str, object]) -> dict[str, object]:
        path = self._path(arguments.get("path"))
        content = _read_text(path)
        lines = content.splitlines(keepends=True)
        start = _bounded_int(arguments.get("start_line"), 1, minimum=1, maximum=max(1, len(lines)))
        end = _bounded_int(arguments.get("end_line"), min(len(lines), start + 399), minimum=start, maximum=max(start, len(lines)))
        selected = "".join(lines[start - 1 : end])
        return {
            "path": path.relative_to(self.workspace).as_posix(),
            "start_line": start,
            "end_line": end,
            "content": selected[:100_000],
            "truncated": len(selected) > 100_000 or end < len(lines),
        }

    def _search_text(self, arguments: dict[str, object]) -> dict[str, object]:
        query = str(arguments.get("query") or "")
        if not query or len(query) > 1000:
            raise ValueError("search_workspace_text query must contain 1 to 1000 characters.")
        base = self._path(arguments.get("path") or ".", directory=True)
        matches: list[dict[str, object]] = []
        for path in sorted(base.rglob("*")):
            if len(matches) >= 200:
                break
            safe_path = _safe_discovered_file(path, self.workspace)
            if safe_path is None:
                continue
            try:
                text = safe_path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
            if len(text) > 1_000_000:
                continue
            for line_number, line in enumerate(text.splitlines(), start=1):
                if query in line:
                    matches.append(
                        {
                            "path": path.relative_to(self.workspace).as_posix(),
                            "line": line_number,
                            "text": line[:500],
                        }
                    )
                    if len(matches) >= 200:
                        break
        return {"matches": matches, "truncated": len(matches) >= 200}

    def _write_file(self, arguments: dict[str, object]) -> dict[str, object]:
        path = self._path(arguments.get("path"), allow_missing=True)
        content = str(arguments.get("content") or "")
        if len(content.encode("utf-8")) > 1_000_000:
            raise ValueError("write_workspace_file content exceeds 1 MB.")
        relative = path.relative_to(self.workspace).as_posix()
        self._approve("파일 쓰기 승인", f"{relative} 파일을 생성하거나 덮어씁니다.")
        _secure_write_text(self.workspace, Path(relative), content)
        return {"path": relative, "bytes_written": len(content.encode("utf-8"))}

    def _replace_text(self, arguments: dict[str, object]) -> dict[str, object]:
        path = self._path(arguments.get("path"))
        old_text = str(arguments.get("old_text") or "")
        new_text = str(arguments.get("new_text") or "")
        expected = _bounded_int(arguments.get("expected_replacements"), 1, minimum=1, maximum=100)
        if not old_text:
            raise ValueError("replace_workspace_text old_text cannot be empty.")
        content = _read_text(path, maximum_bytes=1_000_000)
        occurrences = content.count(old_text)
        if occurrences != expected:
            raise RuntimeError(
                f"Expected {expected} replacement target(s), found {occurrences}; file was not changed."
            )
        relative = path.relative_to(self.workspace).as_posix()
        self._approve("파일 수정 승인", f"{relative}에서 정확히 {expected}곳을 바꿉니다.")
        _secure_replace_text(
            self.workspace,
            Path(relative),
            expected_content=content,
            replacement=content.replace(old_text, new_text),
        )
        return {"path": relative, "replacements": expected}

    def _run_command(self, arguments: dict[str, object]) -> dict[str, object]:
        command = arguments.get("command")
        if not isinstance(command, list) or not command or len(command) > 64:
            raise ValueError("run_workspace_command command must be a non-empty argv list.")
        argv = [str(part) for part in command]
        if any(not part or "\x00" in part or len(part) > 4000 for part in argv):
            raise ValueError("run_workspace_command contains an invalid argument.")
        cwd = self._path(arguments.get("cwd") or ".", directory=True)
        timeout = _bounded_int(arguments.get("timeout_seconds"), 30, minimum=1, maximum=120)
        shown = " ".join(json.dumps(part, ensure_ascii=False) for part in argv)
        self._approve(
            "명령 실행 승인",
            f"{cwd.relative_to(self.workspace).as_posix() or '.'}에서 실행:\n{shown[:900]}",
        )
        # Approval can remain open for minutes. Re-resolve the directory after
        # it closes so a swapped symlink cannot redirect the command cwd.
        cwd = self._path(cwd.relative_to(self.workspace), directory=True)
        completed = _run_bounded_command(
            argv,
            cwd=cwd,
            env=_command_environment(),
            timeout=timeout,
            on_started=self._track_active_process,
            on_finished=self._clear_active_process,
            interrupted=lambda: (
                self._command_interrupted.is_set()
                or self._interrupt_requested()
            ),
        )
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        return {
            "exit_code": completed.returncode,
            "stdout": stdout[-20_000:],
            "stderr": stderr[-20_000:],
            "stdout_truncated": len(stdout) > 20_000,
            "stderr_truncated": len(stderr) > 20_000,
        }

    def interrupt(self) -> bool:
        """Terminate the currently running workspace command, if any."""
        with self._process_lock:
            process = self._active_process
            process_group = self._active_process_group
            if process is None:
                return False
            self._command_interrupted.set()
        _terminate_process_tree(process, process_group=process_group)
        return True

    def _track_active_process(
        self,
        process: subprocess.Popen[str],
        process_group: bool,
    ) -> None:
        with self._process_lock:
            if self._active_process is not None:
                raise RuntimeError("An API workspace command is already running.")
            self._raise_if_interrupted()
            self._command_interrupted.clear()
            self._active_process = process
            self._active_process_group = process_group

    def _clear_active_process(self, process: subprocess.Popen[str]) -> None:
        with self._process_lock:
            if self._active_process is process:
                self._active_process = None
                self._active_process_group = False

    def _raise_if_interrupted(self) -> None:
        if self._interrupt_requested():
            raise RuntimeError("API workspace action was interrupted.")

    def _approve(self, title: str, description: str) -> None:
        if self.request_handler is None:
            raise ApiWorkApprovalDenied(
                "No owner approval channel is connected for this work action."
            )
        resolution: dict[str, object] = {}
        self.request_handler(
            {
                "request_kind": "permission",
                "response_kind": "option",
                "title": title,
                "description": description,
                "options": [
                    {
                        "id": "allow_once",
                        "label": "이번만 허용",
                        "kind": "allow_once",
                        "description": "표시된 작업 한 번만 실행합니다.",
                    },
                    {
                        "id": "deny",
                        "label": "거절",
                        "kind": "deny",
                        "description": "이 작업을 실행하지 않습니다.",
                    },
                ],
                "timeout_seconds": 600,
            },
            lambda value: resolution.update(value),
        )
        if resolution.get("option_id") != "allow_once":
            raise ApiWorkApprovalDenied(
                "The workspace action was not approved by the owner."
            )

    def _path(
        self,
        value: object,
        *,
        directory: bool = False,
        allow_missing: bool = False,
    ) -> Path:
        raw = str(value or "").strip()
        if not raw:
            raise ValueError("A workspace-relative path is required.")
        candidate = Path(raw)
        if candidate.is_absolute():
            raise ValueError("Workspace tool paths must be relative.")
        resolved = (self.workspace / candidate).resolve(strict=False)
        if resolved != self.workspace and self.workspace not in resolved.parents:
            raise ValueError("Workspace tool path escapes the selected workspace.")
        if _hidden_control_path(resolved, self.workspace):
            raise ValueError("Workspace tools cannot access repository control directories.")
        if not allow_missing and not resolved.exists():
            raise FileNotFoundError(f"Workspace path was not found: {raw}")
        if directory and (not resolved.exists() or not resolved.is_dir()):
            raise NotADirectoryError(f"Workspace directory was not found: {raw}")
        if not directory and resolved.exists() and not resolved.is_file():
            raise IsADirectoryError(f"Workspace path is not a file: {raw}")
        return resolved


def parse_work_tool_arguments(tool_call: dict[str, object]) -> tuple[str, dict[str, object]]:
    function = tool_call.get("function")
    if not isinstance(function, dict):
        raise RuntimeError("Provider returned a malformed work tool call.")
    name = str(function.get("name") or "")
    try:
        arguments = json.loads(function.get("arguments") or "{}")
    except (TypeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"{name or 'Work tool'} arguments were not valid JSON.") from error
    if not isinstance(arguments, dict):
        raise RuntimeError(f"{name or 'Work tool'} arguments must be an object.")
    return name, arguments


def _read_text(path: Path, *, maximum_bytes: int = 2_000_000) -> str:
    if path.stat().st_size > maximum_bytes:
        raise ValueError(f"Workspace file exceeds {maximum_bytes:,} bytes.")
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeError as error:
        raise ValueError("Workspace tool can read only UTF-8 text files.") from error


def _hidden_control_path(path: Path, workspace: Path) -> bool:
    try:
        parts = path.relative_to(workspace).parts
    except ValueError:
        return True
    return any(part in {".git", ".hg", ".svn"} for part in parts)


def _safe_discovered_file(path: Path, workspace: Path) -> Path | None:
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        return None
    if resolved != workspace and workspace not in resolved.parents:
        return None
    if _hidden_control_path(resolved, workspace) or not resolved.is_file():
        return None
    return resolved


def _bounded_int(value: object, default: int, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value) if value is not None else default
    except (TypeError, ValueError):
        parsed = default
    return min(maximum, max(minimum, parsed))


def _command_environment() -> dict[str, str]:
    allowed = ("PATH", "LANG", "LC_ALL", "TERM", "TMPDIR", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT")
    return {key: os.environ[key] for key in allowed if os.environ.get(key)}


def _secure_write_text(workspace: Path, relative: Path, content: str) -> None:
    if not _supports_directory_descriptors():
        target = _validated_fallback_target(
            workspace,
            relative,
            create_directories=True,
        )
        target.write_text(content, encoding="utf-8")
        return
    parent_fd, filename = _open_workspace_parent(
        workspace,
        relative,
        create_directories=True,
    )
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(filename, flags, 0o600, dir_fd=parent_fd)
        try:
            file_stat = os.fstat(descriptor)
            if not stat.S_ISREG(file_stat.st_mode):
                raise ValueError("Workspace write target must be a regular file.")
            with os.fdopen(descriptor, "w", encoding="utf-8", closefd=False) as stream:
                stream.write(content)
        finally:
            os.close(descriptor)
    finally:
        os.close(parent_fd)


def _secure_replace_text(
    workspace: Path,
    relative: Path,
    *,
    expected_content: str,
    replacement: str,
) -> None:
    if not _supports_directory_descriptors():
        target = _validated_fallback_target(
            workspace,
            relative,
            create_directories=False,
        )
        current = target.read_text(encoding="utf-8")
        if current != expected_content:
            raise RuntimeError(
                "Workspace file changed while approval was pending; file was not changed."
            )
        target.write_text(replacement, encoding="utf-8")
        return
    parent_fd, filename = _open_workspace_parent(
        workspace,
        relative,
        create_directories=False,
    )
    try:
        flags = os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(filename, flags, dir_fd=parent_fd)
        try:
            file_stat = os.fstat(descriptor)
            if not stat.S_ISREG(file_stat.st_mode):
                raise ValueError("Workspace replacement target must be a regular file.")
            with os.fdopen(descriptor, "r+", encoding="utf-8", closefd=False) as stream:
                current = stream.read()
                if current != expected_content:
                    raise RuntimeError(
                        "Workspace file changed while approval was pending; file was not changed."
                    )
                stream.seek(0)
                stream.write(replacement)
                stream.truncate()
        finally:
            os.close(descriptor)
    finally:
        os.close(parent_fd)


def _open_workspace_parent(
    workspace: Path,
    relative: Path,
    *,
    create_directories: bool,
) -> tuple[int, str]:
    parts = tuple(relative.parts)
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("Workspace tool path is invalid.")
    if any(part in {".git", ".hg", ".svn"} for part in parts):
        raise ValueError("Workspace tools cannot access repository control directories.")
    directory_flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        directory_flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        directory_flags |= os.O_NOFOLLOW
    descriptor = os.open(workspace, directory_flags)
    try:
        for part in parts[:-1]:
            try:
                child = os.open(part, directory_flags, dir_fd=descriptor)
            except FileNotFoundError:
                if not create_directories:
                    raise
                os.mkdir(part, mode=0o700, dir_fd=descriptor)
                child = os.open(part, directory_flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor, parts[-1]
    except Exception:
        os.close(descriptor)
        raise


def _supports_directory_descriptors() -> bool:
    return os.open in os.supports_dir_fd and os.mkdir in os.supports_dir_fd


def _validated_fallback_target(
    workspace: Path,
    relative: Path,
    *,
    create_directories: bool,
) -> Path:
    """Windows fallback where openat-style directory handles are unavailable."""

    parts = tuple(relative.parts)
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("Workspace tool path is invalid.")
    if any(part in {".git", ".hg", ".svn"} for part in parts):
        raise ValueError("Workspace tools cannot access repository control directories.")
    root = workspace.resolve(strict=True)
    parent = root
    for part in parts[:-1]:
        candidate = parent / part
        if os.path.lexists(candidate):
            if candidate.is_symlink() or not candidate.is_dir():
                raise ValueError("Workspace path contains an unsafe directory.")
        elif create_directories:
            candidate.mkdir(mode=0o700)
        else:
            raise FileNotFoundError(candidate)
        parent = candidate
    target = parent / parts[-1]
    if os.path.lexists(target) and target.is_symlink():
        raise ValueError("Workspace path contains an unsafe symbolic link.")
    resolved = target.resolve(strict=False)
    if resolved != root and root not in resolved.parents:
        raise ValueError("Workspace tool path escapes the selected workspace.")
    return resolved


def _run_bounded_command(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: int,
    on_started: Callable[[subprocess.Popen[str], bool], None],
    on_finished: Callable[[subprocess.Popen[str]], None],
    interrupted: Callable[[], bool],
) -> subprocess.CompletedProcess[str]:
    supports_process_groups = hasattr(os, "killpg") and hasattr(os, "setsid")
    creation_flags = 0
    if os.name == "nt":
        creation_flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    process = subprocess.Popen(
        argv,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=supports_process_groups,
        creationflags=creation_flags,
    )
    try:
        on_started(process, supports_process_groups)
    except Exception:
        _terminate_process_tree(process, process_group=supports_process_groups)
        _drain_after_termination(process)
        raise
    try:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as error:
            _terminate_process_tree(process, process_group=supports_process_groups)
            _drain_after_termination(process)
            raise TimeoutError(
                f"Workspace command exceeded its {timeout}-second limit."
            ) from error
        if interrupted():
            raise RuntimeError("API workspace command was interrupted.")
        return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
    finally:
        on_finished(process)


def _drain_after_termination(process: subprocess.Popen[str]) -> None:
    try:
        process.communicate(timeout=_POST_TERMINATION_DRAIN_SECONDS)
        return
    except subprocess.TimeoutExpired:
        pass
    for stream in (process.stdout, process.stderr):
        if stream is not None:
            try:
                stream.close()
            except OSError:
                pass
    try:
        process.kill()
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=0.5)
    except subprocess.TimeoutExpired:
        pass


def _terminate_process_tree(
    process: subprocess.Popen[str],
    *,
    process_group: bool,
) -> None:
    if process.poll() is not None:
        return
    if process_group:
        try:
            os.killpg(process.pid, signal.SIGKILL)
            return
        except ProcessLookupError:
            return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                check=False,
                timeout=5,
            )
        except subprocess.TimeoutExpired:
            pass
        if process.poll() is None:
            process.kill()
        return
    process.kill()


__all__ = [
    "ApiWorkApprovalDenied",
    "ApiWorkHarness",
    "SIDE_EFFECT_WORK_TOOLS",
    "parse_work_tool_arguments",
    "work_tool_schemas",
]
