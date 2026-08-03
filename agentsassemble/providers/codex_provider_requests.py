from __future__ import annotations

from collections.abc import Callable

from agentsassemble.providers.provider_requests import ProviderRequestHandler
from agentsassemble.room.projection import safe_activity_display_detail
from agentsassemble.room.text import clean_room_text


WriteJson = Callable[[dict[str, object]], None]

CODEX_PROVIDER_REQUEST_METHODS = frozenset(
    {
        "item/commandExecution/requestApproval",
        "item/fileChange/requestApproval",
        "item/permissions/requestApproval",
        "item/tool/requestUserInput",
        "command_execution/request_approval",
        "file_change/request_approval",
        "permissions/request_approval",
    }
)


def handle_codex_provider_request(
    message: dict[str, object],
    *,
    handler: ProviderRequestHandler | None,
    write_json: WriteJson,
) -> None:
    request_id = message.get("id")
    if not isinstance(request_id, (int, str)) or isinstance(request_id, bool):
        return
    method = clean_room_text(message.get("method"), limit=128)
    params = message.get("params") if isinstance(message.get("params"), dict) else {}
    request, response_for = _request_and_response(method, params)

    def respond(resolution: dict[str, object]) -> None:
        write_json(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": response_for(resolution),
            }
        )

    if handler is None:
        respond(_default_resolution(request))
    else:
        handler(request, respond)


def _request_and_response(
    method: str,
    params: dict[str, object],
) -> tuple[dict[str, object], Callable[[dict[str, object]], dict[str, object]]]:
    if "command" in method.casefold():
        options, decisions = _command_options(params)
        request = _permission_request(
            title="터미널 명령 실행",
            description=_command_description(params),
            options=options,
        )

        def command_response(resolution: dict[str, object]) -> dict[str, object]:
            option_id = clean_room_text(resolution.get("option_id"), limit=128)
            return {"decision": decisions.get(option_id, "decline")}

        return request, command_response
    if "filechange" in method.casefold() or "file_change" in method.casefold():
        options = _decision_options(include_session=True)
        request = _permission_request(
            title="파일 변경",
            description=safe_activity_display_detail(
                params.get("reason") or "Codex가 작업 폴더의 파일을 변경하려고 합니다.",
                limit=800,
            ),
            options=options,
        )

        def file_response(resolution: dict[str, object]) -> dict[str, object]:
            decision = clean_room_text(resolution.get("option_id"), limit=128)
            if decision not in {"accept", "acceptForSession", "decline", "cancel"}:
                decision = "decline"
            return {"decision": decision}

        return request, file_response
    if "permissions" in method.casefold():
        requested = dict(params.get("permissions")) if isinstance(params.get("permissions"), dict) else {}
        options = [
            {
                "id": "grant-turn",
                "label": "이번 작업에서 허용",
                "kind": "allow_once",
                "description": "현재 작업이 끝날 때까지 요청한 권한을 부여합니다.",
            },
            {
                "id": "grant-session",
                "label": "이 세션에서 허용",
                "kind": "allow_session",
                "description": "이 Agent Session 동안 요청한 권한을 부여합니다.",
            },
            {
                "id": "deny",
                "label": "거절",
                "kind": "deny",
                "description": "추가 권한을 부여하지 않습니다.",
            },
        ]
        request = _permission_request(
            title="추가 권한 요청",
            description=safe_activity_display_detail(
                params.get("reason") or "Codex가 추가 파일 또는 네트워크 권한을 요청했습니다.",
                limit=800,
            ),
            options=options,
        )

        def permissions_response(resolution: dict[str, object]) -> dict[str, object]:
            option_id = clean_room_text(resolution.get("option_id"), limit=128)
            if option_id == "grant-session":
                return {"permissions": requested, "scope": "session"}
            if option_id == "grant-turn":
                return {"permissions": requested, "scope": "turn"}
            return {"permissions": {}, "scope": "turn"}

        return request, permissions_response
    if method == "item/tool/requestUserInput":
        questions = [
            {
                "id": clean_room_text(question.get("id"), limit=128),
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
                "is_other": bool(question.get("isOther")),
                "is_secret": bool(question.get("isSecret")),
            }
            for question in list(params.get("questions") or [])[:3]
            if isinstance(question, dict)
        ]
        request = {
            "request_kind": "user_input",
            "response_kind": "answers",
            "title": "Codex가 선택을 요청했습니다",
            "description": "작업을 계속하려면 질문에 답해 주세요.",
            "options": [],
            "questions": questions,
            "timeout_seconds": _request_timeout(params),
        }

        def input_response(resolution: dict[str, object]) -> dict[str, object]:
            raw = resolution.get("answers")
            answers = raw if isinstance(raw, dict) else {}
            return {
                "answers": {
                    clean_room_text(question_id, limit=128): {
                        "answers": [
                            clean_room_text(value, limit=1000)
                            for value in (values if isinstance(values, list) else [values])
                            if clean_room_text(value, limit=1000)
                        ]
                    }
                    for question_id, values in answers.items()
                    if clean_room_text(question_id, limit=128)
                }
            }

        return request, input_response
    raise RuntimeError(f"Unsupported Codex provider request: {method}")


def _permission_request(
    *,
    title: str,
    description: str,
    options: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "request_kind": "permission",
        "response_kind": "option",
        "title": title,
        "description": description,
        "options": options,
        "questions": [],
        "timeout_seconds": 600,
    }


def _decision_options(*, include_session: bool) -> list[dict[str, object]]:
    options: list[dict[str, object]] = [
        {
            "id": "accept",
            "label": "이번만 허용",
            "kind": "allow_once",
            "description": "현재 요청만 허용합니다.",
        }
    ]
    if include_session:
        options.append(
            {
                "id": "acceptForSession",
                "label": "이 세션에서 허용",
                "kind": "allow_session",
                "description": "같은 Agent Session의 후속 요청에도 적용될 수 있습니다.",
            }
        )
    options.extend(
        [
            {
                "id": "decline",
                "label": "거절하고 계속",
                "kind": "decline",
                "description": "요청을 거절하지만 현재 작업은 계속합니다.",
            },
            {
                "id": "cancel",
                "label": "거절하고 작업 중단",
                "kind": "cancel",
                "description": "요청을 거절하고 현재 작업도 중단합니다.",
            },
        ]
    )
    return options


def _command_options(
    params: dict[str, object],
) -> tuple[list[dict[str, object]], dict[str, object]]:
    options = _decision_options(include_session=True)
    decisions: dict[str, object] = {
        "accept": "accept",
        "acceptForSession": "acceptForSession",
        "decline": "decline",
        "cancel": "cancel",
    }
    amendment = [
        clean_room_text(value, limit=300)
        for value in list(params.get("proposedExecpolicyAmendment") or [])
        if clean_room_text(value, limit=300)
    ]
    if amendment:
        option_id = "acceptWithExecpolicyAmendment"
        options.insert(
            2,
            {
                "id": option_id,
                "label": "규칙에 추가하고 허용",
                "kind": "allow_policy",
                "description": "Codex가 제안한 실행 규칙을 적용합니다.",
            },
        )
        decisions[option_id] = {
            "acceptWithExecpolicyAmendment": {
                "execpolicy_amendment": amendment,
            }
        }
    for index, raw in enumerate(list(params.get("proposedNetworkPolicyAmendments") or [])[:4]):
        if not isinstance(raw, dict):
            continue
        host = clean_room_text(raw.get("host"), limit=255)
        action = clean_room_text(raw.get("action"), limit=16)
        if not host or action not in {"allow", "deny"}:
            continue
        option_id = f"network-policy-{index}"
        options.insert(
            2,
            {
                "id": option_id,
                "label": f"{host} 규칙 적용",
                "kind": "allow_policy",
                "description": f"네트워크 정책에 {action} 규칙을 추가합니다.",
            },
        )
        decisions[option_id] = {
            "applyNetworkPolicyAmendment": {
                "network_policy_amendment": {"host": host, "action": action},
            }
        }
    return options, decisions


def _command_description(params: dict[str, object]) -> str:
    reason = safe_activity_display_detail(params.get("reason"), limit=500)
    command = safe_activity_display_detail(params.get("command"), limit=500)
    return "\n".join(
        part
        for part in (
            reason,
            f"명령: {command}" if command else "Codex가 터미널 명령을 실행하려고 합니다.",
        )
        if part
    )


def _request_timeout(params: dict[str, object]) -> int:
    try:
        milliseconds = int(params.get("autoResolutionMs") or 0)
    except (TypeError, ValueError):
        milliseconds = 0
    if milliseconds > 0:
        return min(900, max(15, milliseconds // 1000))
    return 600


def _default_resolution(request: dict[str, object]) -> dict[str, object]:
    if request.get("response_kind") == "answers":
        return {"answers": {}}
    return {"option_id": "decline"}


__all__ = ["CODEX_PROVIDER_REQUEST_METHODS", "handle_codex_provider_request"]
