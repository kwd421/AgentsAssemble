"""Public provider-failure categories derived only from explicit provider errors."""

from __future__ import annotations

import json
from urllib.error import HTTPError

from agentsassemble.room.text import clean_room_text


class ProviderTurnError(RuntimeError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


_EXHAUSTED_CODES = frozenset(
    {
        "billing_hard_limit_reached",
        "credit_balance_too_low",
        "insufficient_balance",
        "insufficient_credits",
        "insufficient_quota",
        "quota_exceeded",
        "quota_exhausted",
    }
)
_EXHAUSTED_MESSAGES = (
    "billing hard limit has been reached",
    "credit balance is too low",
    "insufficient balance",
    "insufficient credit",
    "insufficient quota",
    "quota exceeded",
    "quota exhausted",
    "usage limit reached",
)
_RATE_LIMIT_MESSAGES = (
    "rate limit",
    "rate_limit",
    "too many requests",
)


def provider_failure_code(error: BaseException) -> str:
    explicit = clean_room_text(getattr(error, "code", ""), limit=64)
    if explicit:
        return explicit
    return provider_failure_code_from_text(str(error))


def provider_failure_code_from_text(value: object) -> str:
    text = str(value or "").casefold()
    if any(marker in text for marker in _EXHAUSTED_CODES):
        return "quota_exhausted"
    if any(marker in text for marker in _EXHAUSTED_MESSAGES):
        return "quota_exhausted"
    if any(marker in text for marker in _RATE_LIMIT_MESSAGES):
        return "provider_rate_limited"
    return "provider_turn_failed"


def provider_http_error(error: HTTPError, *, provider_name: str) -> ProviderTurnError:
    payload = _read_http_error_payload(error)
    provider_code, provider_message = _provider_error_fields(payload)
    combined = " ".join(
        value
        for value in (
            provider_code,
            provider_message,
            str(getattr(error, "reason", "") or ""),
        )
        if value
    )
    code = provider_failure_code_from_text(combined)
    if code == "provider_turn_failed" and error.code == 429:
        code = "provider_rate_limited"
    public_message = clean_room_text(provider_message, limit=1000)
    if not public_message:
        public_message = f"{provider_name} request failed with HTTP {error.code}."
    return ProviderTurnError(public_message, code=code)


def _read_http_error_payload(error: HTTPError) -> object:
    try:
        raw = error.read(64_000)
    except Exception:
        return {}
    finally:
        try:
            error.close()
        except Exception:
            pass
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8", errors="replace"))
    except (AttributeError, json.JSONDecodeError):
        return {}


def _provider_error_fields(payload: object) -> tuple[str, str]:
    if not isinstance(payload, dict):
        return "", ""
    error = payload.get("error")
    values = error if isinstance(error, dict) else payload
    code = clean_room_text(
        values.get("code") or values.get("type"),
        limit=128,
    )
    message = clean_room_text(
        values.get("message") or values.get("detail") or values.get("error"),
        limit=1000,
    )
    return code, message


__all__ = [
    "ProviderTurnError",
    "provider_failure_code",
    "provider_failure_code_from_text",
    "provider_http_error",
]
