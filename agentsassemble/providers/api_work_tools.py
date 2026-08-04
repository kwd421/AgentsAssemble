"""Workspace-scoped tools for OpenAI-compatible API Agent Sessions.

The work harness is deliberately smaller than a native coding CLI.  It gives a
tool-capable model enough primitives to inspect and change one operator-selected
workspace without giving the remote model an ambient shell or filesystem.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from agentsassemble.providers.api_work_tool_schemas import work_tool_schemas
from agentsassemble.providers.provider_requests import ProviderRequestHandler


class ApiWorkHarness:
    """Execute bounded work tools inside one canonical workspace root."""

    def __init__(
        self,
        workspace: str | Path,
        *,
        permission_mode: str,
        request_handler: ProviderRequestHandler | None = None,
    ) -> None:
        self.workspace = Path(workspace).expanduser().resolve()
        self.permission_mode = str(permission_mode or "meeting_read_only")
        self.request_handler = request_handler

    @property
    def enabled(self) -> bool:
        return self.permission_mode == "workspace_write"

    def execute(self, name: str, arguments: dict[str, object]) -> object:
        if not self.enabled:
            raise RuntimeError("The API work harness is not enabled for this Agent Session.")
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
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
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
        path.write_text(content.replace(old_text, new_text), encoding="utf-8")
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
        completed = subprocess.run(
            argv,
            cwd=cwd,
            env=_command_environment(),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
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

    def _approve(self, title: str, description: str) -> None:
        if self.request_handler is None:
            raise PermissionError("No owner approval channel is connected for this work action.")
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
            raise PermissionError("The workspace action was not approved by the owner.")

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


__all__ = [
    "ApiWorkHarness",
    "parse_work_tool_arguments",
    "work_tool_schemas",
]
