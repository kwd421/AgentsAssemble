"""HTTP response writers shared by the GUI request handler.

These methods own byte delivery and response headers for the React app,
attachments, JSON, and one-shot SSE snapshots. Request routing and policy stay
outside this module; the mixin keeps the handler's existing method surface
intact.
"""
from __future__ import annotations

import json
import mimetypes
from http import HTTPStatus
from pathlib import Path

from agentsassemble.room.attachments import (
    INLINE_SAFE_IMAGE_TYPES,
    attachment_content_disposition,
)
from agentsassemble.web.frontend_runtime import (
    REACT_APP_MISSING_BUILD_MESSAGE,
    frontend_dist_status,
)


def _sse_event(
    event_name: str,
    payload: dict[str, object],
    event_id: str | None = None,
) -> bytes:
    lines = []
    if event_id:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event_name}")
    lines.append(f"data: {json.dumps(payload, ensure_ascii=False)}")
    return ("\n".join(lines) + "\n\n").encode("utf-8")


def _last_payload_event_id(payload: dict[str, object]) -> str | None:
    events = payload.get("events")
    if not isinstance(events, list) or not events:
        return None
    latest = events[-1]
    if not isinstance(latest, dict):
        return None
    event_id = latest.get("id")
    return event_id if isinstance(event_id, str) and event_id else None


def _rewrite_react_app_index(html: str) -> str:
    return html.replace('src="/assets/', 'src="/app/assets/').replace(
        'href="/assets/',
        'href="/app/assets/',
    )


class GuiResponseMethods:
    """Transport-only response methods mixed into the request handler."""

    def end_headers(self) -> None:
        # These are response-level browser boundaries, so they must also cover
        # framework errors and routes that do not use the JSON helpers below.
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
        super().end_headers()

    def handle(self) -> None:
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    def _send_react_app_index(self, frontend_root: Path) -> None:
        index_path = frontend_root / "index.html"
        if not frontend_dist_status(frontend_root).static_available:
            self._send_error(
                HTTPStatus.SERVICE_UNAVAILABLE,
                REACT_APP_MISSING_BUILD_MESSAGE,
            )
            return
        html = index_path.read_text(encoding="utf-8")
        data = _rewrite_react_app_index(html).encode("utf-8")
        self._send_bytes(
            data,
            "text/html; charset=utf-8",
            cache_control="no-cache",
            referrer_policy="no-referrer",
        )

    def _send_file(
        self,
        path: Path,
        content_type: str | None = None,
        *,
        cache_control: str = "no-store",
    ) -> None:
        if not path.exists() or not path.is_file():
            self._send_error(HTTPStatus.NOT_FOUND, "File not found")
            return
        guessed = (
            content_type
            or mimetypes.guess_type(path.name)[0]
            or "application/octet-stream"
        )
        data = path.read_bytes()
        self._send_bytes(data, guessed, cache_control=cache_control)

    def _send_bytes(
        self,
        data: bytes,
        content_type: str,
        *,
        cache_control: str,
        referrer_policy: str = "",
    ) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache_control)
        if referrer_policy:
            self.send_header("Referrer-Policy", referrer_policy)
        self.end_headers()
        self.wfile.write(data)

    def _send_attachment_file(
        self,
        path: Path,
        metadata: dict[str, object],
        *,
        inline: bool,
    ) -> None:
        if not path.exists() or not path.is_file():
            self._send_error(HTTPStatus.NOT_FOUND, "Attachment not found")
            return
        filename = str(metadata.get("filename") or path.name)
        content_type = str(
            metadata.get("content_type")
            or mimetypes.guess_type(path.name)[0]
            or "application/octet-stream"
        )
        safe_inline = inline and content_type in INLINE_SAFE_IMAGE_TYPES
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header(
            "Content-Disposition",
            attachment_content_disposition(filename, inline=safe_inline),
        )
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'; sandbox")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, payload: dict[str, object]) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self._send_public_invite_cors_headers()
        self.end_headers()
        self.wfile.write(data)

    def _send_sse_snapshot(
        self,
        event_name: str,
        payload: dict[str, object],
    ) -> None:
        data = _sse_event(
            event_name,
            payload,
            event_id=_last_payload_event_id(payload),
        )
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self._send_public_invite_cors_headers()
        self.end_headers()
        self.wfile.write(data)
