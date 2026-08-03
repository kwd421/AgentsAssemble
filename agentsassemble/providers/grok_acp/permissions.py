from __future__ import annotations

from collections.abc import Callable

from agentsassemble.providers.provider_requests import ProviderRequestHandler
from agentsassemble.room.text import clean_room_text


SendJson = Callable[[dict[str, object]], None]


def handle_permission_request(
    message: dict[str, object],
    *,
    handler: ProviderRequestHandler | None,
    send_json: SendJson,
) -> tuple[bool, str]:
    request_id = message.get("id")
    if not isinstance(request_id, (int, str)) or isinstance(request_id, bool):
        return False, ""
    params = message.get("params") if isinstance(message.get("params"), dict) else {}
    tool_call = params.get("toolCall") if isinstance(params.get("toolCall"), dict) else {}
    options = [
        {
            "id": clean_room_text(option.get("optionId"), limit=128),
            "label": _option_label(option.get("kind")),
            "kind": clean_room_text(option.get("kind"), limit=64),
            "description": "",
        }
        for option in list(params.get("options") or [])
        if isinstance(option, dict)
        and clean_room_text(option.get("optionId"), limit=128)
    ]
    title = clean_room_text(
        tool_call.get("title") or tool_call.get("name") or params.get("title"),
        limit=160,
    ) or "Grok 도구 사용"
    request = {
        "request_kind": "permission",
        "response_kind": "option",
        "title": title,
        "description": "Grok이 이 작업을 실행하려고 합니다.",
        "options": options,
        "questions": [],
        "timeout_seconds": 600,
    }
    selected = ""

    def respond(resolution: dict[str, object]) -> None:
        nonlocal selected
        selected = clean_room_text(resolution.get("option_id"), limit=128)
        allowed = {str(option["id"]) for option in options}
        outcome: dict[str, object]
        if selected in allowed:
            outcome = {"outcome": "selected", "optionId": selected}
        else:
            outcome = {"outcome": "cancelled"}
        send_json(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {"outcome": outcome},
            }
        )

    if handler is None:
        reject = next(
            (
                str(option["id"])
                for option in options
                if option.get("kind") == "reject_once"
            ),
            "",
        )
        respond({"option_id": reject})
    else:
        handler(request, respond)
    selected_kind = next(
        (
            str(option.get("kind") or "")
            for option in options
            if option.get("id") == selected
        ),
        "",
    )
    return selected_kind not in {"reject_once", "cancel"}, selected


def _option_label(value: object) -> str:
    return {
        "allow_once": "이번만 허용",
        "allow_always": "항상 허용",
        "reject_once": "거절",
        "reject_always": "항상 거절",
    }.get(clean_room_text(value, limit=64), "선택")


__all__ = ["handle_permission_request"]
