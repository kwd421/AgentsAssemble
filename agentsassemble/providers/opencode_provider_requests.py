from __future__ import annotations

from collections.abc import Callable
from urllib.parse import quote

from agentsassemble.providers.provider_requests import ProviderRequestHandler
from agentsassemble.room.projection import safe_activity_display_detail
from agentsassemble.room.text import clean_room_text


PostJson = Callable[[str, dict[str, object]], None]


def handle_opencode_permission_request(
    properties: dict[str, object],
    *,
    handler: ProviderRequestHandler | None,
    post_json: PostJson,
) -> None:
    request_id = _request_id(properties)
    if not request_id:
        raise RuntimeError("OpenCode permission request did not include an id.")
    permission = clean_room_text(properties.get("permission"), limit=120) or "tool"
    patterns = [
        safe_activity_display_detail(pattern, limit=240)
        for pattern in list(properties.get("patterns") or [])[:8]
        if safe_activity_display_detail(pattern, limit=240)
    ]
    options = [
        {
            "id": "once",
            "label": "이번만 허용",
            "kind": "allow_once",
            "description": "현재 OpenCode 요청만 허용합니다.",
        }
    ]
    if list(properties.get("always") or []):
        options.append(
            {
                "id": "always",
                "label": "이 프로젝트에서 항상 허용",
                "kind": "allow_always",
                "description": "OpenCode가 제안한 패턴을 현재 프로젝트에 저장합니다.",
            }
        )
    options.append(
        {
            "id": "reject",
            "label": "거절",
            "kind": "reject_once",
            "description": "요청을 거절합니다.",
        }
    )
    request = {
        "request_kind": "permission",
        "response_kind": "option",
        "title": _permission_title(permission),
        "description": _permission_description(permission, patterns),
        "options": options,
        "questions": [],
        "timeout_seconds": 600,
    }

    def respond(resolution: dict[str, object]) -> None:
        allowed = {str(option["id"]) for option in options}
        reply = clean_room_text(resolution.get("option_id"), limit=64)
        if reply not in allowed:
            reply = "reject"
        post_json(
            f"/permission/{quote(request_id)}/reply",
            {"reply": reply},
        )

    if handler is None:
        respond({"option_id": "reject"})
    else:
        handler(request, respond)


def handle_opencode_question_request(
    properties: dict[str, object],
    *,
    handler: ProviderRequestHandler | None,
    post_json: PostJson,
) -> None:
    request_id = _request_id(properties)
    if not request_id:
        raise RuntimeError("OpenCode question request did not include an id.")
    native_questions = [
        question
        for question in list(properties.get("questions") or [])[:3]
        if isinstance(question, dict)
    ]
    questions = [
        {
            "id": f"question-{index}",
            "header": clean_room_text(question.get("header"), limit=120),
            "question": clean_room_text(question.get("question"), limit=800),
            "options": [
                {
                    "id": clean_room_text(option.get("label"), limit=240),
                    "label": clean_room_text(option.get("label"), limit=240),
                    "kind": "answer",
                    "description": clean_room_text(option.get("description"), limit=400),
                }
                for option in list(question.get("options") or [])
                if isinstance(option, dict)
                and clean_room_text(option.get("label"), limit=240)
            ],
            "multiple": bool(question.get("multiple")),
            "is_other": question.get("custom") is not False,
            "is_secret": False,
        }
        for index, question in enumerate(native_questions)
        if clean_room_text(question.get("question"), limit=800)
    ]
    if not questions:
        raise RuntimeError("OpenCode question request did not include a usable question.")
    request = {
        "request_kind": "user_input",
        "response_kind": "answers",
        "title": "OpenCode가 선택을 요청했습니다",
        "description": "작업을 계속하려면 질문에 답해 주세요.",
        "options": [],
        "questions": questions,
        "timeout_seconds": 600,
    }

    def respond(resolution: dict[str, object]) -> None:
        raw_answers = resolution.get("answers")
        answer_map = raw_answers if isinstance(raw_answers, dict) else {}
        ordered_answers = []
        for question in questions:
            raw_values = answer_map.get(question["id"])
            values = raw_values if isinstance(raw_values, list) else [raw_values]
            ordered_answers.append(
                [
                    clean_room_text(value, limit=1000)
                    for value in values
                    if clean_room_text(value, limit=1000)
                ]
            )
        if any(not values for values in ordered_answers):
            post_json(f"/question/{quote(request_id)}/reject", {})
            return
        post_json(
            f"/question/{quote(request_id)}/reply",
            {"answers": ordered_answers},
        )

    if handler is None:
        post_json(f"/question/{quote(request_id)}/reject", {})
    else:
        handler(request, respond)


def opencode_error_message(value: object) -> str:
    error = value if isinstance(value, dict) else {}
    data = error.get("data") if isinstance(error.get("data"), dict) else {}
    message = safe_activity_display_detail(data.get("message"), limit=1200)
    if message:
        return message
    name = clean_room_text(error.get("name"), limit=120)
    if name:
        return f"OpenCode provider error: {name}"
    return "OpenCode provider request failed."


def _request_id(properties: dict[str, object]) -> str:
    return clean_room_text(
        properties.get("id") or properties.get("requestID"),
        limit=128,
    )


def _permission_title(permission: str) -> str:
    return {
        "bash": "터미널 명령 실행",
        "edit": "파일 변경",
        "external_directory": "작업 폴더 밖 접근",
        "webfetch": "웹 페이지 접근",
        "websearch": "웹 검색",
        "task": "하위 에이전트 실행",
        "question": "사용자에게 질문",
    }.get(permission, f"OpenCode 권한 요청 · {permission}")


def _permission_description(permission: str, patterns: list[str]) -> str:
    if not patterns:
        return f"OpenCode가 {permission} 권한을 요청했습니다."
    return f"OpenCode가 {permission} 권한을 요청했습니다: {', '.join(patterns)}"


__all__ = [
    "handle_opencode_permission_request",
    "handle_opencode_question_request",
    "opencode_error_message",
]
