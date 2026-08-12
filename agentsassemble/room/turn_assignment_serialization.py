"""Serialization boundary for assigning one provider observation turn."""

from __future__ import annotations

from functools import wraps


def serialized_observation_assignment(method):
    @wraps(method)
    def locked(coordinator, *args, **kwargs):
        with coordinator._lock:
            return method(coordinator, *args, **kwargs)

    return locked


__all__ = ["serialized_observation_assignment"]
