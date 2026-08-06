from __future__ import annotations


MAX_PROVIDER_CLEANUP_ATTEMPTS = 3


def provider_cleanup_delay_seconds(attempt: int) -> float:
    """Return the shared bounded backoff for provider retirement cleanup."""
    normalized_attempt = max(1, int(attempt))
    return min(4.0, float(2 ** (normalized_attempt - 1)))


__all__ = [
    "MAX_PROVIDER_CLEANUP_ATTEMPTS",
    "provider_cleanup_delay_seconds",
]
