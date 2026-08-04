"""Loopback model-wire adapter owned by one native Agent Session."""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from agentsassemble.providers.native_harness_protocol import (
    anthropic_request_to_chat,
    approximate_anthropic_input_tokens,
    chat_response_to_anthropic_events,
    chat_response_to_anthropic_message,
    chat_response_to_responses_events,
    responses_request_to_chat,
)
from agentsassemble.room.text import clean_room_text


class NativeModelGatewayError(RuntimeError):
    pass


class _LoopbackServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class NativeModelGateway:
    """Expose Codex Responses and Claude Messages over one loopback socket."""

    def __init__(
        self,
        *,
        upstream_base_url: str,
        upstream_api_key: str,
        model: str,
        provider_kind: str,
        reasoning_effort: str = "",
        variant: str = "",
        max_output_tokens: int = 0,
        request_headers: tuple[tuple[str, str], ...] = (),
        request_timeout_seconds: float = 600.0,
    ) -> None:
        self.upstream_base_url = str(upstream_base_url or "").rstrip("/")
        self._upstream_api_key = str(upstream_api_key or "")
        self.model = clean_room_text(model, limit=256)
        self.provider_kind = clean_room_text(provider_kind, limit=64)
        self.reasoning_effort = clean_room_text(reasoning_effort, limit=32)
        self.variant = clean_room_text(variant, limit=64)
        self.max_output_tokens = max(0, int(max_output_tokens or 0))
        self.request_headers = tuple(
            (str(name), str(value))
            for name, value in request_headers
            if str(name).strip() and str(value).strip()
        )
        self.request_timeout_seconds = max(1.0, float(request_timeout_seconds))
        self._server = _LoopbackServer(("127.0.0.1", 0), self._handler_type())
        self._thread: threading.Thread | None = None
        self._running = False
        self._request_count = 0
        self._last_request_kind = ""
        self._last_error = ""
        host, port = self._server.server_address[:2]
        self.endpoint = f"http://{host}:{port}/v1"

    def _handler_type(self):
        gateway = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self) -> None:
                gateway._handle_get(self)

            def do_POST(self) -> None:
                gateway._handle_post(self)

            def log_message(self, _format: str, *args: object) -> None:
                del args

        return Handler

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="agentsassemble-native-model-gateway",
            daemon=True,
        )
        self._thread.start()

    def stop(self, *, timeout_seconds: float = 3.0) -> None:
        if not self._running:
            self._upstream_api_key = ""
            return
        self._running = False
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=max(0.1, float(timeout_seconds)))
            self._thread = None
        self._upstream_api_key = ""

    @property
    def pid(self) -> int | None:
        return os.getpid() if self._running else None

    def health(self) -> dict[str, object]:
        return {
            "running": self._running,
            "request_count": self._request_count,
            "last_request_kind": self._last_request_kind,
            "last_error": self._last_error,
        }

    def _handle_get(self, handler: BaseHTTPRequestHandler) -> None:
        request_path = urlsplit(handler.path).path.rstrip("/")
        if request_path == "/healthz":
            _write_json(handler, 200, {"status": "ok"})
            return
        if request_path in {"/models", "/v1/models"}:
            _write_json(
                handler,
                200,
                {
                    "object": "list",
                    "models": [],
                    "data": [
                        {
                            "id": self.model,
                            "object": "model",
                            "type": "model",
                            "display_name": self.model,
                            "created_at": "1970-01-01T00:00:00Z",
                        }
                    ],
                    "has_more": False,
                    "first_id": self.model,
                    "last_id": self.model,
                },
            )
            return
        _write_json(handler, 404, {"error": {"message": "Not found."}})

    def _handle_post(self, handler: BaseHTTPRequestHandler) -> None:
        try:
            request = _read_json(handler)
            request_path = urlsplit(handler.path).path.rstrip("/")
            if request_path in {"/v1/responses", "/responses"}:
                self._record_request("responses")
                payload = responses_request_to_chat(
                    request,
                    model=self.model,
                    max_output_tokens=self.max_output_tokens,
                    reasoning_effort=self.reasoning_effort,
                    extra_payload=self._provider_payload(),
                )
                response = self._complete(payload)
                _write_sse(
                    handler,
                    chat_response_to_responses_events(response, model=self.model),
                )
                return
            if request_path in {"/v1/messages", "/messages"}:
                self._record_request("messages")
                payload = anthropic_request_to_chat(
                    request,
                    model=self.model,
                    max_output_tokens=self.max_output_tokens,
                    reasoning_effort=self.reasoning_effort,
                    extra_payload=self._provider_payload(),
                )
                response = self._complete(payload)
                if bool(request.get("stream")):
                    _write_sse(
                        handler,
                        chat_response_to_anthropic_events(response, model=self.model),
                    )
                else:
                    _write_json(
                        handler,
                        200,
                        chat_response_to_anthropic_message(
                            response,
                            model=self.model,
                        ),
                    )
                return
            if request_path in {
                "/v1/messages/count_tokens",
                "/messages/count_tokens",
            }:
                self._record_request("count_tokens")
                _write_json(
                    handler,
                    200,
                    {"input_tokens": approximate_anthropic_input_tokens(request)},
                )
                return
            _write_json(handler, 404, {"error": {"message": "Not found."}})
        except _UpstreamHttpError as error:
            self._last_error = error.message
            _write_json(
                handler,
                error.status,
                {
                    "type": "error",
                    "error": {
                        "type": "upstream_error",
                        "message": error.message,
                    },
                },
            )
        except Exception as error:
            self._last_error = str(error) or type(error).__name__
            _write_json(
                handler,
                502,
                {
                    "type": "error",
                    "error": {
                        "type": "gateway_error",
                        "message": str(error) or type(error).__name__,
                    },
                },
            )

    def _record_request(self, request_kind: str) -> None:
        self._request_count += 1
        self._last_request_kind = request_kind
        self._last_error = ""

    def _provider_payload(self) -> dict[str, object]:
        if self.provider_kind == "deepseek_api":
            return {
                "thinking": {
                    "type": "disabled"
                    if self.variant == "non_thinking"
                    else "enabled"
                }
            }
        return {}

    def _complete(self, payload: dict[str, object]) -> dict[str, object]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "AgentsAssemble/1.0",
            **dict(self.request_headers),
        }
        if self._upstream_api_key:
            headers["Authorization"] = f"Bearer {self._upstream_api_key}"
        request = Request(
            f"{self.upstream_base_url}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.request_timeout_seconds) as response:
                body = response.read()
        except HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            raise _UpstreamHttpError(error.code, _upstream_error_message(body)) from error
        except URLError as error:
            raise NativeModelGatewayError(f"Upstream connection failed: {error.reason}") from error
        decoded = json.loads(body.decode("utf-8"))
        if not isinstance(decoded, dict):
            raise NativeModelGatewayError("Upstream response must be a JSON object.")
        return decoded


class _UpstreamHttpError(RuntimeError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = int(status)
        self.message = message


def _upstream_error_message(body: str) -> str:
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return body[:2000] or "Upstream request failed."
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
        if payload.get("message"):
            return str(payload["message"])
    return body[:2000] or "Upstream request failed."


def _read_json(handler: BaseHTTPRequestHandler) -> dict[str, object]:
    try:
        length = int(handler.headers.get("Content-Length") or 0)
    except ValueError as error:
        raise ValueError("Invalid Content-Length.") from error
    payload = json.loads(handler.rfile.read(length or 0) or b"{}")
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object.")
    return payload


def _write_json(
    handler: BaseHTTPRequestHandler, status: int, payload: dict[str, object]
) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Connection", "close")
    handler.end_headers()
    handler.wfile.write(body)


def _write_sse(
    handler: BaseHTTPRequestHandler, events: list[dict[str, object]]
) -> None:
    body = b"".join(
        (
            f"event: {event.get('type', 'message')}\n"
            f"data: {json.dumps(event, ensure_ascii=False, separators=(',', ':'))}\n\n"
        ).encode("utf-8")
        for event in events
    )
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Connection", "close")
    handler.end_headers()
    handler.wfile.write(body)


__all__ = ["NativeModelGateway", "NativeModelGatewayError"]
