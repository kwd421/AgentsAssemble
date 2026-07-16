"""Compatibility exports for React frontend build inspection."""

from agentsassemble.web.frontend_runtime import (
    REACT_APP_BUILD_COMMAND,
    REACT_APP_MISSING_BUILD_MESSAGE,
    FrontendDistStatus,
    default_frontend_dist_root,
    frontend_dist_status,
)


__all__ = [
    "REACT_APP_BUILD_COMMAND",
    "REACT_APP_MISSING_BUILD_MESSAGE",
    "FrontendDistStatus",
    "default_frontend_dist_root",
    "frontend_dist_status",
]
