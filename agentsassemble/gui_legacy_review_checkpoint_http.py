"""Compatibility exports for retained review-checkpoint HTTP routes."""

from agentsassemble.legacy.meeting.http.review_checkpoint import (
    register_legacy_review_checkpoint_route,
)

__all__ = ["register_legacy_review_checkpoint_route"]
