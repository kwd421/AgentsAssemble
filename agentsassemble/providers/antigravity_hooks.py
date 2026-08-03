from __future__ import annotations

import json
import os
import secrets
import shlex
import subprocess
import sys
import tempfile
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from agentsassemble.providers.provider_requests import ProviderRequestHandler
from agentsassemble.providers.terminal_interactions import is_safe_room_portal_command
from agentsassemble.room.text import clean_room_text


_HOOK_NAME = "agentsassemble-room-requests"
_HOOK_TIMEOUT_SECONDS = 900
_BROKER_BODY_LIMIT = 128_000


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
        self._broker = _HookBroker(self.handle_hook)
        self._base_command = list(command)
        self._runtime_factory = terminal_runtime_factory
        self._cwd = cwd
        self._runtime_kwargs = dict(runtime_kwargs)
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
            command = clean_room_text(args.get("CommandLine"), limit=4000)
            if is_safe_room_portal_command(command):
                return {"decision": "allow", "reason": "AgentsAssemble room tool command."}
            return self._resolve_permission(
                title="Antigravity 터미널 명령",
                description=_command_description(command, args.get("Cwd")),
            )
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


class _HookBroker:
    def __init__(self, handle: Callable[[dict[str, object]], dict[str, object]]) -> None:
        self._handle = handle
        self._token = secrets.token_urlsafe(32)
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def endpoint(self) -> str:
        server = self._server
        if server is None:
            raise RuntimeError("Antigravity hook broker is not running.")
        return f"http://127.0.0.1:{server.server_port}/hook"

    @property
    def token(self) -> str:
        return self._token

    @property
    def running(self) -> bool:
        return self._server is not None

    def start(self) -> None:
        if self._server is not None:
            return
        broker = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                broker._post(self)

            def log_message(self, _format: str, *_args: object) -> None:
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        self._server = server
        self._thread = threading.Thread(target=server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        server = self._server
        thread = self._thread
        self._server = None
        self._thread = None
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=2.0)

    def _post(self, request: BaseHTTPRequestHandler) -> None:
        if request.path != "/hook" or request.headers.get("Authorization") != f"Bearer {self._token}":
            _write_response(request, 404, {"error": "not_found"})
            return
        try:
            length = int(request.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > _BROKER_BODY_LIMIT:
            _write_response(request, 400, {"error": "invalid_body"})
            return
        try:
            payload = json.loads(request.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("hook payload must be an object")
            result = self._handle(payload)
        except Exception as error:
            result = {"decision": "deny", "reason": f"AgentsAssemble hook failed: {error}"}
        _write_response(request, 200, result)


def _write_response(
    request: BaseHTTPRequestHandler, status: int, payload: dict[str, object]
) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request.send_response(status)
    request.send_header("Content-Type", "application/json; charset=utf-8")
    request.send_header("Content-Length", str(len(body)))
    request.end_headers()
    request.wfile.write(body)


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
        try:
            path.unlink()
            path.parent.rmdir()
        except OSError:
            pass


def _read_hooks_document(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError("Antigravity hooks.json must contain an object.")
    return dict(document)


def _write_hooks_document(path: Path, document: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".hooks-", suffix=".json", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as temporary:
            json.dump(document, temporary, ensure_ascii=False, indent=2)
            temporary.write("\n")
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


def _command_description(command: str, cwd: object) -> str:
    directory = clean_room_text(cwd, limit=1000)
    if directory:
        return f"{command or '(빈 명령)'}\n작업 폴더: {directory}"
    return command or "Antigravity가 터미널 명령을 실행하려고 합니다."


__all__ = ["AntigravityHookRuntime"]
