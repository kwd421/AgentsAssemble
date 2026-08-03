from __future__ import annotations

from agentsassemble.room.text import clean_room_text


PROVIDER_REQUEST_KINDS = frozenset({"permission", "user_input", "external_action"})
PROVIDER_RESPONSE_KINDS = frozenset({"option", "answers", "acknowledge"})
PROVIDER_REQUEST_TERMINAL_STATUSES = frozenset(
    {"resolved", "denied", "cancelled", "expired", "failed"}
)


class ProviderRequestValidationError(ValueError):
    def __init__(self, message: str, *, code: str = "invalid_provider_request") -> None:
        super().__init__(message)
        self.code = code


def normalize_provider_request(payload: dict[str, object]) -> dict[str, object]:
    request_id = clean_room_text(payload.get("provider_request_id"), limit=128)
    request_kind = clean_room_text(payload.get("request_kind"), limit=32)
    response_kind = clean_room_text(payload.get("response_kind"), limit=32)
    if not request_id:
        raise ProviderRequestValidationError("provider_request_id is required.")
    if request_kind not in PROVIDER_REQUEST_KINDS:
        raise ProviderRequestValidationError("Unsupported provider request kind.")
    if response_kind not in PROVIDER_RESPONSE_KINDS:
        raise ProviderRequestValidationError("Unsupported provider response kind.")

    options = _normalize_options(payload.get("options"))
    questions = _normalize_questions(payload.get("questions"))
    if response_kind == "option" and not options:
        raise ProviderRequestValidationError("A provider option request needs choices.")
    if response_kind == "answers" and not questions:
        raise ProviderRequestValidationError("A provider input request needs questions.")
    if response_kind == "acknowledge" and request_kind != "external_action":
        raise ProviderRequestValidationError("Only external actions may use acknowledge responses.")

    timeout_seconds = _bounded_int(
        payload.get("timeout_seconds"),
        default=600,
        minimum=15,
        maximum=900,
    )
    result: dict[str, object] = {
        "provider_request_id": request_id,
        "request_kind": request_kind,
        "response_kind": response_kind,
        "title": clean_room_text(payload.get("title"), limit=160) or "확인이 필요합니다",
        "description": clean_room_text(payload.get("description"), limit=1200),
        "options": options,
        "questions": questions,
        "timeout_seconds": timeout_seconds,
    }
    action_url = clean_room_text(payload.get("action_url"), limit=2000)
    if action_url:
        if not action_url.startswith("https://"):
            raise ProviderRequestValidationError(
                "External action links must use HTTPS.",
                code="invalid_provider_action_url",
            )
        result["action_url"] = action_url
    return result


def normalize_provider_resolution(
    request: dict[str, object],
    payload: dict[str, object],
) -> dict[str, object]:
    response_kind = clean_room_text(request.get("response_kind"), limit=32)
    if response_kind == "option":
        option_id = clean_room_text(payload.get("option_id"), limit=128)
        allowed = {
            clean_room_text(option.get("id"), limit=128)
            for option in list(request.get("options") or [])
            if isinstance(option, dict)
        }
        if not option_id or option_id not in allowed:
            raise ProviderRequestValidationError(
                "The selected provider option is unavailable.",
                code="provider_request_option_invalid",
            )
        return {"option_id": option_id}
    if response_kind == "answers":
        raw_answers = payload.get("answers")
        if not isinstance(raw_answers, dict):
            raise ProviderRequestValidationError(
                "Provider answers are required.",
                code="provider_request_answers_invalid",
            )
        questions = {
            clean_room_text(question.get("id"), limit=128): question
            for question in list(request.get("questions") or [])
            if isinstance(question, dict)
        }
        if set(raw_answers) != set(questions):
            raise ProviderRequestValidationError(
                "Every provider question must be answered exactly once.",
                code="provider_request_answers_invalid",
            )
        answers: dict[str, list[str]] = {}
        for question_id, question in questions.items():
            raw_values = raw_answers.get(question_id)
            values = raw_values if isinstance(raw_values, list) else [raw_values]
            cleaned = [
                clean_room_text(value, limit=1000)
                for value in values[:4]
                if clean_room_text(value, limit=1000)
            ]
            if not cleaned:
                raise ProviderRequestValidationError(
                    "Provider answers cannot be empty.",
                    code="provider_request_answers_invalid",
                )
            option_labels = {
                clean_room_text(option.get("label"), limit=240)
                for option in list(question.get("options") or [])
                if isinstance(option, dict)
            }
            if option_labels and not bool(question.get("is_other")) and any(
                value not in option_labels for value in cleaned
            ):
                raise ProviderRequestValidationError(
                    "A provider answer does not match the offered choices.",
                    code="provider_request_answers_invalid",
                )
            answers[question_id] = cleaned
        return {"answers": answers}
    if response_kind == "acknowledge":
        return {"acknowledged": True}
    raise ProviderRequestValidationError("Unsupported provider response kind.")


def _normalize_options(value: object) -> list[dict[str, str]]:
    options: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in list(value or [])[:12]:
        if not isinstance(raw, dict):
            continue
        option_id = clean_room_text(raw.get("id"), limit=128)
        label = clean_room_text(raw.get("label"), limit=120)
        if not option_id or not label or option_id in seen:
            continue
        seen.add(option_id)
        options.append(
            {
                "id": option_id,
                "label": label,
                "kind": clean_room_text(raw.get("kind"), limit=64),
                "description": clean_room_text(raw.get("description"), limit=400),
            }
        )
    return options


def _normalize_questions(value: object) -> list[dict[str, object]]:
    questions: list[dict[str, object]] = []
    seen: set[str] = set()
    for raw in list(value or [])[:3]:
        if not isinstance(raw, dict):
            continue
        question_id = clean_room_text(raw.get("id"), limit=128)
        question_text = clean_room_text(raw.get("question"), limit=800)
        if not question_id or not question_text or question_id in seen:
            continue
        seen.add(question_id)
        questions.append(
            {
                "id": question_id,
                "header": clean_room_text(raw.get("header"), limit=120),
                "question": question_text,
                "options": _normalize_options(raw.get("options")),
                "multiple": bool(raw.get("multiple")),
                "is_other": bool(raw.get("is_other")),
                "is_secret": bool(raw.get("is_secret")),
            }
        )
    return questions


def _bounded_int(value: object, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return min(maximum, max(minimum, parsed))


__all__ = [
    "PROVIDER_REQUEST_TERMINAL_STATUSES",
    "ProviderRequestValidationError",
    "normalize_provider_request",
    "normalize_provider_resolution",
]
