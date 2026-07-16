"""Compatibility exports for GUI HTTP response writers."""
from agentsassemble.web.response import (
    GuiResponseMethods,
    _last_payload_event_id,
    _rewrite_react_app_index,
    _sse_event,
)

__all__ = [
    "GuiResponseMethods",
    "_last_payload_event_id",
    "_rewrite_react_app_index",
    "_sse_event",
]
