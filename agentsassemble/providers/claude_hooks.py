from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agentsassemble.providers.provider_hook_broker import ProviderHookBroker
from agentsassemble.providers.provider_requests import ProviderRequestHandler
from agentsassemble.room.projection import safe_activity_display_detail
from agentsassemble.room.text import clean_room_text


_CLAUDE_HOOK_TOKEN_ENV = "AGENTSASSEMBLE_CLAUDE_HOOK_TOKEN"
_HOOK_TIMEOUT_SECONDS = 900


class ClaudeHookRuntime:
    """Route interactive Claude Code questions and permissions through the room."""

    def __init__(
        self,
        agent_id: str,
        command: list[str],
        *,
        cwd: str | Path | None,
        state_dir: str | Path,
        terminal_runtime_factory: Callable[..., Any],
        **runtime_kwargs: object,
    ) -> None:
        self.agent_id = agent_id
        self.command = list(command)
        self.startup_ready_contains = str(
            runtime_kwargs.get("startup_ready_contains") or ""
        )
        self._base_command = list(command)
        self._cwd = cwd
        self._state_dir = Path(state_dir).expanduser().resolve()
        self._runtime_factory = terminal_runtime_factory
        self._runtime_kwargs = dict(runtime_kwargs)
        self._request_handler: ProviderRequestHandler | None = None
        self._settings_path: Path | None = None
        self._runtime: Any | None = None
        self._broker = ProviderHookBroker(
            self.handle_hook,
            failure_response=_deny_hook_failure,
        )

    def set_request_handler(self, handler: ProviderRequestHandler | None) -> None:
        self._request_handler = handler

    def start(self) -> dict[str, object]:
        if self._runtime is not None:
            return self.health()
        self._broker.start()
        try:
            settings_path = self._write_session_settings()
            runtime_kwargs = dict(self._runtime_kwargs)
            environment = dict(runtime_kwargs.get("env") or {})
            environment[_CLAUDE_HOOK_TOKEN_ENV] = self._broker.token
            runtime_kwargs["env"] = environment
            runtime = self._runtime_factory(
                self.agent_id,
                [*self._base_command, "--settings", str(settings_path)],
                cwd=self._cwd,
                **runtime_kwargs,
            )
            self._runtime = runtime
            runtime.start()
        except Exception:
            self._runtime = None
            self._remove_session_settings()
            self._broker.stop()
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
            self._remove_session_settings()

    def health(self) -> dict[str, object]:
        runtime = self._runtime
        health = dict(runtime.health()) if runtime is not None else {"running": False}
        health.update(
            {
                "provider_request_transport": "claude_http_hook",
                "provider_request_hook_active": self._broker.running,
                "provider_request_settings_path": str(self._settings_path or ""),
            }
        )
        return health

    def handle_hook(self, payload: dict[str, object]) -> dict[str, object]:
        event_name = clean_room_text(payload.get("hook_event_name"), limit=64)
        tool_name = clean_room_text(payload.get("tool_name"), limit=128)
        tool_input = payload.get("tool_input")
        inputs = dict(tool_input) if isinstance(tool_input, dict) else {}
        if event_name == "PermissionRequest":
            return self._resolve_permission(tool_name, inputs)
        if event_name == "PreToolUse" and tool_name == "AskUserQuestion":
            return self._resolve_questions(inputs)
        if event_name == "PreToolUse" and tool_name == "ExitPlanMode":
            return self._resolve_plan(inputs)
        return _deny_pre_tool_use("Unsupported Claude Code hook request.")

    def __getattr__(self, name: str) -> Any:
        runtime = self.__dict__.get("_runtime")
        if runtime is None:
            raise AttributeError(name)
        return getattr(runtime, name)

    def _resolve_permission(
        self,
        tool_name: str,
        tool_input: dict[str, object],
    ) -> dict[str, object]:
        resolution = self._request(
            {
                "request_kind": "permission",
                "response_kind": "option",
                "title": _permission_title(tool_name),
                "description": _permission_description(tool_name, tool_input),
                "options": _allow_or_deny_options(),
                "questions": [],
                "timeout_seconds": 600,
            }
        )
        allowed = clean_room_text(resolution.get("option_id"), limit=128) == "allow-once"
        decision: dict[str, object] = {"behavior": "allow" if allowed else "deny"}
        if not allowed:
            decision["message"] = "Denied by the room participant."
        return {
            "hookSpecificOutput": {
                "hookEventName": "PermissionRequest",
                "decision": decision,
            }
        }

    def _resolve_plan(self, tool_input: dict[str, object]) -> dict[str, object]:
        resolution = self._request(
            {
                "request_kind": "permission",
                "response_kind": "option",
                "title": "계획 실행",
                "description": "Claude Code가 계획 모드를 끝내고 작업을 시작하려고 합니다.",
                "options": _allow_or_deny_options(),
                "questions": [],
                "timeout_seconds": 600,
            }
        )
        if clean_room_text(resolution.get("option_id"), limit=128) != "allow-once":
            return _deny_pre_tool_use("The room participant rejected the plan.")
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
                "updatedInput": tool_input,
            }
        }

    def _resolve_questions(self, tool_input: dict[str, object]) -> dict[str, object]:
        native_questions = [
            question
            for question in list(tool_input.get("questions") or [])[:3]
            if isinstance(question, dict)
        ]
        questions = []
        for index, question in enumerate(native_questions):
            text = clean_room_text(question.get("question"), limit=800)
            if not text:
                continue
            options = [
                {
                    "id": clean_room_text(option.get("label"), limit=240),
                    "label": clean_room_text(option.get("label"), limit=240),
                    "kind": "answer",
                    "description": clean_room_text(option.get("description"), limit=400),
                }
                for option in list(question.get("options") or [])
                if isinstance(option, dict)
                and clean_room_text(option.get("label"), limit=240)
            ]
            questions.append(
                {
                    "id": f"question-{index}",
                    "header": clean_room_text(question.get("header"), limit=120),
                    "question": text,
                    "options": options,
                    "multiple": bool(question.get("multiSelect")),
                    "is_other": True,
                    "is_secret": False,
                }
            )
        if not questions:
            return _deny_pre_tool_use("Claude Code did not provide a usable question.")
        resolution = self._request(
            {
                "request_kind": "user_input",
                "response_kind": "answers",
                "title": "Claude Code가 선택을 요청했습니다",
                "description": "작업을 계속하려면 질문에 답해 주세요.",
                "options": [],
                "questions": questions,
                "timeout_seconds": 600,
            }
        )
        raw_answers = resolution.get("answers")
        answer_map = raw_answers if isinstance(raw_answers, dict) else {}
        native_answers: dict[str, str] = {}
        for question in questions:
            raw_values = answer_map.get(question["id"])
            values = raw_values if isinstance(raw_values, list) else [raw_values]
            cleaned = [clean_room_text(value, limit=1000) for value in values]
            cleaned = [value for value in cleaned if value]
            if not cleaned:
                return _deny_pre_tool_use("The room participant did not answer every question.")
            native_answers[str(question["question"])] = ", ".join(cleaned)
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
                "updatedInput": {**tool_input, "answers": native_answers},
            }
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

    def _write_session_settings(self) -> Path:
        if any(
            part == "--settings" or part.startswith("--settings=")
            for part in self._base_command[1:]
        ):
            raise RuntimeError(
                "Claude Code Agent Sessions cannot combine an unmanaged --settings argument "
                "with room provider hooks."
            )
        self._state_dir.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="claude-room-hooks-",
            suffix=".json",
            dir=self._state_dir,
        )
        settings_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(_hook_settings(self._broker.endpoint), stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            os.chmod(settings_path, 0o600)
        except Exception:
            try:
                os.close(descriptor)
            except OSError:
                pass
            settings_path.unlink(missing_ok=True)
            raise
        self._settings_path = settings_path
        return settings_path

    def _remove_session_settings(self) -> None:
        settings_path = self._settings_path
        self._settings_path = None
        if settings_path is not None:
            settings_path.unlink(missing_ok=True)


def _hook_settings(endpoint: str) -> dict[str, object]:
    http_hook = {
        "type": "http",
        "url": endpoint,
        "timeout": _HOOK_TIMEOUT_SECONDS,
        "headers": {"Authorization": f"Bearer ${_CLAUDE_HOOK_TOKEN_ENV}"},
        "allowedEnvVars": [_CLAUDE_HOOK_TOKEN_ENV],
    }
    return {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "AskUserQuestion|ExitPlanMode",
                    "hooks": [dict(http_hook)],
                }
            ],
            "PermissionRequest": [{"hooks": [dict(http_hook)]}],
        }
    }


def _allow_or_deny_options() -> list[dict[str, object]]:
    return [
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
            "description": "현재 요청을 실행하지 않습니다.",
        },
    ]


def _permission_title(tool_name: str) -> str:
    return {
        "Bash": "터미널 명령 실행",
        "Edit": "파일 변경",
        "Write": "파일 작성",
        "WebFetch": "웹 접근",
        "WebSearch": "웹 검색",
    }.get(tool_name, f"Claude Code {tool_name or '도구'} 사용")


def _permission_description(tool_name: str, tool_input: dict[str, object]) -> str:
    detail = (
        tool_input.get("command")
        or tool_input.get("file_path")
        or tool_input.get("url")
        or tool_input.get("query")
    )
    rendered = safe_activity_display_detail(detail, limit=800)
    if rendered:
        return rendered
    return f"Claude Code가 {tool_name or '도구'} 작업을 실행하려고 합니다."


def _deny_pre_tool_use(reason: str) -> dict[str, object]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def _deny_hook_failure(
    error: Exception,
    payload: dict[str, object],
) -> dict[str, object]:
    reason = f"AgentsAssemble Claude hook failed: {error}"
    if clean_room_text(payload.get("hook_event_name"), limit=64) == "PermissionRequest":
        return {
            "hookSpecificOutput": {
                "hookEventName": "PermissionRequest",
                "decision": {
                    "behavior": "deny",
                    "message": reason,
                },
            }
        }
    return _deny_pre_tool_use(reason)


__all__ = ["ClaudeHookRuntime"]
