from __future__ import annotations

import json
import os
import secrets
import shlex
import stat
import subprocess
import sys
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agentsassemble.providers.provider_hook_broker import ProviderHookBroker
from agentsassemble.providers.provider_requests import ProviderRequestHandler
from agentsassemble.providers.terminal_interactions import is_safe_room_portal_command
from agentsassemble.room.text import clean_room_text


_HOOK_NAME = "agentsassemble-room-requests"
_HOOK_TIMEOUT_SECONDS = 900
_MAX_COMMAND_LINE_CHARACTERS = 4000


class AntigravityHookRuntime:
    """Add Antigravity's official hook protocol to a terminal runtime."""

    def __init__(
        self,
        agent_id: str,
        command: list[str],
        *,
        cwd: str | Path | None,
        terminal_runtime_factory: Callable[..., Any],
        **runtime_kwargs: object,
    ) -> None:
        self.agent_id = agent_id
        self._workspace = Path(cwd).expanduser().resolve() if cwd else Path.cwd().resolve()
        self._hooks_path = self._workspace / ".agents" / "hooks.json"
        self._request_handler: ProviderRequestHandler | None = None
        self._broker = ProviderHookBroker(
            self.handle_hook,
            failure_response=lambda error, _payload: {
                "decision": "deny",
                "reason": f"AgentsAssemble hook failed: {error}",
            },
        )
        self._base_command = list(command)
        self._runtime_factory = terminal_runtime_factory
        self._cwd = cwd
        self._runtime_kwargs = dict(runtime_kwargs)
        self._terminal_interaction_policy = self._runtime_kwargs.get(
            "terminal_interaction_policy"
        )
        self._runtime: Any | None = None

    def set_request_handler(self, handler: ProviderRequestHandler | None) -> None:
        self._request_handler = handler

    def start(self) -> dict[str, object]:
        if self._runtime is not None:
            return self.health()
        self._broker.start()
        try:
            _register_workspace_hook(self._hooks_path)
            runtime_kwargs = dict(self._runtime_kwargs)
            environment = dict(runtime_kwargs.get("env") or {})
            environment.update(
                {
                    "AGENTSASSEMBLE_ANTIGRAVITY_HOOK_ENDPOINT": self._broker.endpoint,
                    "AGENTSASSEMBLE_ANTIGRAVITY_HOOK_TOKEN": self._broker.token,
                }
            )
            runtime_kwargs["env"] = environment
            runtime = self._runtime_factory(
                self.agent_id,
                self._base_command,
                cwd=self._cwd,
                **runtime_kwargs,
            )
            self._runtime = runtime
            runtime.start()
        except Exception:
            self._runtime = None
            self._broker.stop()
            _unregister_workspace_hook(self._hooks_path)
            raise
        return self.health()

    def stop(self, *, timeout_seconds: float = 2.0) -> None:
        runtime = self._runtime
        self._runtime = None
        try:
            if runtime is not None:
                runtime.stop(timeout_seconds=timeout_seconds)
        finally:
            self._broker.stop()
            _unregister_workspace_hook(self._hooks_path)

    def health(self) -> dict[str, object]:
        runtime = self._runtime
        health = dict(runtime.health()) if runtime is not None else {"running": False}
        health.update(
            {
                "provider_request_transport": "antigravity_hook",
                "provider_request_hook_active": self._broker.running,
                "provider_request_hooks_path": str(self._hooks_path),
            }
        )
        return health

    def handle_hook(self, payload: dict[str, object]) -> dict[str, object]:
        tool_call = payload.get("toolCall")
        tool = tool_call if isinstance(tool_call, dict) else {}
        name = clean_room_text(tool.get("name"), limit=128)
        args = tool.get("args") if isinstance(tool.get("args"), dict) else {}
        if name == "run_command":
            raw_command = args.get("CommandLine")
            command = raw_command if isinstance(raw_command, str) else ""
            exact_safe_command = _exact_safe_command_line(raw_command)
            if exact_safe_command and is_safe_room_portal_command(exact_safe_command):
                return {
                    "decision": "allow",
                    "reason": "AgentsAssemble room tool command.",
                    "permissionOverrides": [
                        "command(agentsassemble-room)",
                        "unsandboxed(agentsassemble-room)",
                    ],
                }
            result = self._resolve_permission(
                title="Antigravity 터미널 명령",
                description=_command_description(command, args.get("Cwd")),
            )
            resolver = getattr(
                self._terminal_interaction_policy,
                "resolve_external_permission",
                None,
            )
            if callable(resolver):
                resolver(command, allowed=result.get("decision") == "allow")
            return result
        if name == "ask_permission":
            action = clean_room_text(args.get("Action"), limit=200)
            target = clean_room_text(args.get("Target"), limit=1000)
            reason = clean_room_text(args.get("Reason"), limit=1000)
            description = " · ".join(value for value in (action, target, reason) if value)
            result = self._resolve_permission(
                title=action or "Antigravity 권한 요청",
                description=description or "Antigravity가 권한을 요청했습니다.",
            )
            if result.get("decision") == "allow" and action and target:
                result["permissionOverrides"] = [f"{action}({target})"]
            return result
        if name == "ask_question":
            return self._resolve_questions(args.get("questions"))
        return {"decision": "allow", "reason": "No AgentsAssemble policy for this tool."}

    def __getattr__(self, name: str) -> Any:
        runtime = self.__dict__.get("_runtime")
        if runtime is None:
            raise AttributeError(name)
        return getattr(runtime, name)

    def _resolve_permission(self, *, title: str, description: str) -> dict[str, object]:
        resolution = self._request(
            {
                "request_kind": "permission",
                "response_kind": "option",
                "title": title,
                "description": description,
                "options": [
                    {
                        "id": "allow-once",
                        "label": "이번만 허용",
                        "kind": "allow_once",
                        "description": "현재 요청만 허용합니다.",
                    },
                    {
                        "id": "deny",
                        "label": "거절",
                        "kind": "deny",
                        "description": "요청을 실행하지 않습니다.",
                    },
                ],
                "questions": [],
                "timeout_seconds": 600,
            }
        )
        if clean_room_text(resolution.get("option_id"), limit=128) == "allow-once":
            return {"decision": "allow", "reason": "Allowed by the room participant."}
        return {"decision": "deny", "reason": "Denied by the room participant."}

    def _resolve_questions(self, raw_questions: object) -> dict[str, object]:
        questions = []
        for index, raw in enumerate(list(raw_questions or [])[:3]):
            if not isinstance(raw, dict):
                continue
            question = clean_room_text(raw.get("question"), limit=800)
            if not question:
                continue
            options = []
            for raw_option in list(raw.get("options") or []):
                if isinstance(raw_option, dict):
                    label = clean_room_text(
                        raw_option.get("label") or raw_option.get("value"), limit=240
                    )
                    description = clean_room_text(raw_option.get("description"), limit=400)
                else:
                    label = clean_room_text(raw_option, limit=240)
                    description = ""
                if label:
                    options.append(
                        {"id": label, "label": label, "kind": "answer", "description": description}
                    )
            questions.append(
                {
                    "id": f"question-{index}",
                    "header": clean_room_text(raw.get("header"), limit=120),
                    "question": question,
                    "options": options,
                    "multiple": bool(raw.get("is_multi_select")),
                    "is_other": not options,
                    "is_secret": False,
                }
            )
        if not questions:
            return {"decision": "deny", "reason": "No usable question was provided."}
        resolution = self._request(
            {
                "request_kind": "user_input",
                "response_kind": "answers",
                "title": "Antigravity가 선택을 요청했습니다",
                "description": "작업을 계속하려면 질문에 답해 주세요.",
                "options": [],
                "questions": questions,
                "timeout_seconds": 600,
            }
        )
        answers = resolution.get("answers") if isinstance(resolution.get("answers"), dict) else {}
        rendered = []
        for question in questions:
            values = answers.get(question["id"])
            values = values if isinstance(values, list) else [values]
            cleaned = [clean_room_text(value, limit=1000) for value in values]
            cleaned = [value for value in cleaned if value]
            if cleaned:
                rendered.append(f"{question['question']}: {', '.join(cleaned)}")
        if not rendered:
            return {"decision": "deny", "reason": "The room participant did not answer."}
        return {
            "decision": "deny",
            "reason": "The room participant answered: " + " | ".join(rendered),
        }

    def _request(self, request: dict[str, object]) -> dict[str, object]:
        handler = self._request_handler
        if handler is None:
            return {}
        resolution: dict[str, object] = {}

        def respond(value: dict[str, object]) -> None:
            resolution.update(value)

        handler(request, respond)
        return resolution


def _hook_command() -> str:
    parts = [sys.executable, str(Path(__file__).with_name("antigravity_hook_client.py"))]
    return subprocess.list2cmdline(parts) if os.name == "nt" else shlex.join(parts)


_HOOK_REGISTRATION_LOCK = threading.RLock()
_HOOK_REGISTRATIONS: dict[Path, tuple[int, object]] = {}
_MISSING = object()


def _hook_definition() -> dict[str, object]:
    return {
        "PreToolUse": [
            {
                "matcher": "run_command|ask_permission|ask_question",
                "hooks": [
                    {
                        "type": "command",
                        "command": _hook_command(),
                        "timeout": _HOOK_TIMEOUT_SECONDS,
                    }
                ],
            }
        ]
    }


def _register_workspace_hook(path: Path) -> None:
    with _HOOK_REGISTRATION_LOCK:
        active = _HOOK_REGISTRATIONS.get(path)
        if active is not None:
            _HOOK_REGISTRATIONS[path] = (active[0] + 1, active[1])
            return
        document = _read_hooks_document(path)
        previous = document.get(_HOOK_NAME, _MISSING)
        document[_HOOK_NAME] = _hook_definition()
        _write_hooks_document(path, document)
        _HOOK_REGISTRATIONS[path] = (1, previous)


def _unregister_workspace_hook(path: Path) -> None:
    with _HOOK_REGISTRATION_LOCK:
        active = _HOOK_REGISTRATIONS.get(path)
        if active is None:
            return
        count, previous = active
        if count > 1:
            _HOOK_REGISTRATIONS[path] = (count - 1, previous)
            return
        _HOOK_REGISTRATIONS.pop(path, None)
        try:
            document = _read_hooks_document(path)
        except (OSError, ValueError, json.JSONDecodeError):
            return
        if document.get(_HOOK_NAME) != _hook_definition():
            return
        if previous is _MISSING:
            document.pop(_HOOK_NAME, None)
        else:
            document[_HOOK_NAME] = previous
        if document:
            _write_hooks_document(path, document)
            return
        _remove_empty_hooks_document(path)


def _read_hooks_document(path: Path) -> dict[str, object]:
    if not _supports_hook_directory_descriptors():
        directory = _validated_hooks_directory(path, create=False)
        if directory is None:
            return {}
        target = directory / path.name
        if not os.path.lexists(target):
            return {}
        if _is_link_or_junction(target) or not target.is_file():
            raise ValueError("Antigravity hooks.json must be a regular file.")
        with target.open("r", encoding="utf-8") as stream:
            document = json.load(stream)
        if not isinstance(document, dict):
            raise ValueError("Antigravity hooks.json must contain an object.")
        return dict(document)

    directory_fd = _open_hooks_directory(path, create=False)
    if directory_fd is None:
        return {}
    try:
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(path.name, flags, dir_fd=directory_fd)
        except FileNotFoundError:
            return {}
        try:
            file_status = os.fstat(descriptor)
            if not stat.S_ISREG(file_status.st_mode):
                raise ValueError("Antigravity hooks.json must be a regular file.")
            with os.fdopen(descriptor, "r", encoding="utf-8", closefd=False) as stream:
                document = json.load(stream)
        finally:
            os.close(descriptor)
    finally:
        os.close(directory_fd)
    if not isinstance(document, dict):
        raise ValueError("Antigravity hooks.json must contain an object.")
    return dict(document)


def _write_hooks_document(path: Path, document: dict[str, object]) -> None:
    if not _supports_hook_directory_descriptors():
        directory = _validated_hooks_directory(path, create=True)
        if directory is None:  # pragma: no cover - create=True guarantees a directory
            raise ValueError("Antigravity hooks directory is unavailable.")
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".hooks-",
            suffix=".json",
            dir=directory,
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as temporary:
                json.dump(document, temporary, ensure_ascii=False, indent=2)
                temporary.write("\n")
            verified_directory = _validated_hooks_directory(path, create=False)
            if verified_directory != directory:
                raise ValueError("Antigravity hooks directory changed during registration.")
            os.replace(temporary_path, directory / path.name)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise
        return

    directory_fd = _open_hooks_directory(path, create=True)
    if directory_fd is None:  # pragma: no cover - create=True guarantees a directory
        raise ValueError("Antigravity hooks directory is unavailable.")
    temporary_name = f".hooks-{secrets.token_hex(12)}.json"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(temporary_name, flags, 0o600, dir_fd=directory_fd)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as temporary:
            json.dump(document, temporary, ensure_ascii=False, indent=2)
            temporary.write("\n")
        os.replace(
            temporary_name,
            path.name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            os.unlink(temporary_name, dir_fd=directory_fd)
        except OSError:
            pass
        raise
    finally:
        os.close(directory_fd)


def _open_hooks_directory(path: Path, *, create: bool) -> int | None:
    """Open ``workspace/.agents`` without following a redirected directory."""

    if path.name != "hooks.json" or path.parent.name != ".agents":
        raise ValueError("Antigravity hooks path must be workspace/.agents/hooks.json.")
    workspace = path.parent.parent
    directory_flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        directory_flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        directory_flags |= os.O_NOFOLLOW
    workspace_fd = os.open(workspace, directory_flags)
    try:
        try:
            return os.open(".agents", directory_flags, dir_fd=workspace_fd)
        except FileNotFoundError:
            if not create:
                return None
            os.mkdir(".agents", mode=0o700, dir_fd=workspace_fd)
            return os.open(".agents", directory_flags, dir_fd=workspace_fd)
        except OSError as error:
            raise ValueError("Antigravity hooks directory must not be a symbolic link.") from error
    finally:
        os.close(workspace_fd)


def _supports_hook_directory_descriptors() -> bool:
    return all(
        operation in os.supports_dir_fd
        for operation in (os.open, os.mkdir, os.rename, os.unlink, os.rmdir)
    )


def _validated_hooks_directory(path: Path, *, create: bool) -> Path | None:
    """Portable no-link check for platforms without openat-style operations."""

    if path.name != "hooks.json" or path.parent.name != ".agents":
        raise ValueError("Antigravity hooks path must be workspace/.agents/hooks.json.")
    workspace = path.parent.parent.resolve(strict=True)
    directory = workspace / ".agents"
    if os.path.lexists(directory):
        if _is_link_or_junction(directory) or not directory.is_dir():
            raise ValueError("Antigravity hooks directory must not be a symbolic link.")
    elif create:
        directory.mkdir(mode=0o700)
    else:
        return None
    if _is_link_or_junction(directory) or directory.resolve(strict=True).parent != workspace:
        raise ValueError("Antigravity hooks directory escaped the workspace.")
    return directory


def _is_link_or_junction(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(os.path, "isjunction", None)
    if callable(is_junction) and is_junction(path):
        return True
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _remove_empty_hooks_document(path: Path) -> None:
    if not _supports_hook_directory_descriptors():
        try:
            directory = _validated_hooks_directory(path, create=False)
            if directory is None:
                return
            target = directory / path.name
            if os.path.lexists(target):
                if _is_link_or_junction(target) or not target.is_file():
                    return
                target.unlink()
            _validated_hooks_directory(path, create=False)
            directory.rmdir()
        except (OSError, ValueError):
            pass
        return
    directory_fd = _open_hooks_directory(path, create=False)
    if directory_fd is None:
        return
    try:
        try:
            os.unlink(path.name, dir_fd=directory_fd)
        except OSError:
            return
    finally:
        os.close(directory_fd)
    workspace_fd = os.open(path.parent.parent, os.O_RDONLY)
    try:
        os.rmdir(".agents", dir_fd=workspace_fd)
    except OSError:
        pass
    finally:
        os.close(workspace_fd)


def _exact_safe_command_line(value: object) -> str:
    """Return the exact command only when it is safe to authorize automatically."""

    if not isinstance(value, str):
        return ""
    if any(character in value for character in ("\x00", "\r", "\n")):
        return ""
    if len(value) > _MAX_COMMAND_LINE_CHARACTERS:
        return ""
    return value


def _command_description(command: str, cwd: object) -> str:
    command = clean_room_text(command, limit=_MAX_COMMAND_LINE_CHARACTERS)
    directory = clean_room_text(cwd, limit=1000)
    if directory:
        return f"{command or '(빈 명령)'}\n작업 폴더: {directory}"
    return command or "Antigravity가 터미널 명령을 실행하려고 합니다."


__all__ = ["AntigravityHookRuntime"]
