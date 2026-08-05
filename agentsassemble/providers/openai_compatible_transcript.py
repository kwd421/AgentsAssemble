"""Structured response state and user-visible diagnostics for OpenAI-compatible streams."""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass(frozen=True)
class OpenAIStreamRound:
    content: str
    reasoning_content: str
    tool_calls: list[dict[str, object]]
    observed_model_id: str
    usage: dict[str, object]
    finish_reason: str = ""


def reasoning_activity_reporter(on_activity, *, activity_id: str):
    """Coalesce provider reasoning deltas into bounded live activity updates."""

    parts: list[str] = []
    last_reported_chars = 0
    last_reported_at = 0.0

    def report(delta: str, completed: bool) -> None:
        nonlocal last_reported_chars, last_reported_at
        if delta:
            parts.append(str(delta))
        content = "".join(parts)
        now = time.monotonic()
        should_report = completed or (
            len(content) - last_reported_chars >= 40
            or now - last_reported_at >= 0.15
        )
        if not content or not should_report:
            return
        visible = content[-2000:]
        on_activity(
            {
                "category": "reasoning",
                "status": "completed" if completed else "running",
                "activity_id": activity_id,
                "activity_title": "생각",
                "activity_detail": visible,
                "content": visible,
            }
        )
        last_reported_chars = len(content)
        last_reported_at = now

    return report


def empty_round_message(
    provider_name: str,
    round_result: OpenAIStreamRound,
    *,
    max_output_tokens: object = None,
) -> str:
    """Explain an empty provider round using its protocol finish state."""

    if round_result.finish_reason == "length":
        try:
            limit = int(max_output_tokens or 0)
        except (TypeError, ValueError):
            limit = 0
        budget = f" (최대 응답 길이 {limit:,} 토큰)" if limit else ""
        thought = (
            "추론에만 쓰고 답변을 시작하지 못했습니다"
            if round_result.reasoning_content
            else "답변을 끝내지 못했습니다"
        )
        return (
            f"{provider_name}이(가) 최대 응답 길이에 걸려 {thought}{budget}. "
            "최대 응답 길이를 늘리거나 추론 강도를 낮추세요."
        )
    if round_result.reasoning_content:
        return (
            f"{provider_name}이(가) 추론만 남기고 답변 본문을 반환하지 않았습니다"
            f" (종료 사유: {round_result.finish_reason or '없음'})."
        )
    return (
        f"{provider_name} completed without a final message"
        f" (finish_reason: {round_result.finish_reason or 'none'})."
    )


def normalized_usage(value: dict[str, object]) -> dict[str, int]:
    details = (
        value.get("completion_tokens_details")
        if isinstance(value.get("completion_tokens_details"), dict)
        else {}
    )
    return {
        "input_tokens": _usage_int(value.get("prompt_tokens")),
        "output_tokens": _usage_int(value.get("completion_tokens")),
        "total_tokens": _usage_int(value.get("total_tokens")),
        "cache_hit_input_tokens": _usage_int(value.get("prompt_cache_hit_tokens")),
        "cache_miss_input_tokens": _usage_int(value.get("prompt_cache_miss_tokens")),
        "reasoning_tokens": _usage_int(details.get("reasoning_tokens")),
    }


def aggregate_usage(api_calls: list[dict[str, object]]) -> dict[str, int]:
    fields = (
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cache_hit_input_tokens",
        "cache_miss_input_tokens",
        "reasoning_tokens",
    )
    return {
        field: sum(_usage_int(call.get(field)) for call in api_calls)
        for field in fields
    }


def _usage_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(0, value)


__all__ = [
    "OpenAIStreamRound",
    "aggregate_usage",
    "empty_round_message",
    "normalized_usage",
    "reasoning_activity_reporter",
]
