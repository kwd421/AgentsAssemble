"""Compatibility exports for React static delivery."""
from agentsassemble.web.static import (
    REACT_APP_EXACT_PATHS,
    ReactStaticTransport,
    react_app_cache_control,
    react_app_content_type,
    safe_static_path,
)

__all__ = [
    "REACT_APP_EXACT_PATHS",
    "ReactStaticTransport",
    "react_app_cache_control",
    "react_app_content_type",
    "safe_static_path",
]
