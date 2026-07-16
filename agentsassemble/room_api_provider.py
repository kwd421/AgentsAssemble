"""Compatibility exports for the OpenAI-compatible API provider adapter."""

from agentsassemble.providers.api import (
    ApiProviderError,
    ApiReply,
    ApiUsage,
    api_error_category,
    chat_completion,
    chat_completion_with_fallback,
    record_api_usage,
    run_api_call,
)


__all__ = [
    "ApiProviderError",
    "ApiReply",
    "ApiUsage",
    "api_error_category",
    "chat_completion",
    "chat_completion_with_fallback",
    "record_api_usage",
    "run_api_call",
]
