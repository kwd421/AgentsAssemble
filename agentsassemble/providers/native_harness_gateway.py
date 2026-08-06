"""Loopback model-wire adapter owned by one native Agent Session."""

from __future__ import annotations

import json
import os
import secrets
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from agentsassemble.providers.api_context import ApiContextLimitError, ApiContextPolicy
from agentsassemble.providers.api_session import ApiToolResultStore
from agentsassemble.providers.remote_http import safe_remote_urlopen
from agentsassemble.providers.native_harness_protocol import (
    anthropic_request_to_chat,
    approximate_anthropic_input_tokens,
    chat_response_to_anthropic_events,
    chat_response_to_anthropic_message,
    chat_response_to_responses_events,
    responses_request_to_chat,
)
from agentsassemble.providers.turn_progress import (
    DEFAULT_PROVIDER_INACTIVITY_TIMEOUT_SECONDS,
)
from agentsassemble.room.text import clean_room_text


class NativeModelGatewayError(RuntimeError):
    pass


class _GatewayRequestError(RuntimeError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = int(status)
        self.message = message


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
        request_timeout_seconds: float = DEFAULT_PROVIDER_INACTIVITY_TIMEOUT_SECONDS,
        context_contract_bytes: int = 256_000,
        state_dir: str = "",
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
        self._context_policy = ApiContextPolicy(context_contract_bytes)
        self._maximum_request_bytes = min(
            8 * 1024 * 1024,
            max(1024 * 1024, int(context_contract_bytes) * 2),
        )
        self._access_token = secrets.token_urlsafe(32)
        self._context_lock = threading.Lock()
        self._delivered_tool_call_ids: set[str] = set()
        self._tool_result_references: dict[str, str] = {}
        self._temporary_state = (
            tempfile.TemporaryDirectory(prefix="agentsassemble-api-context-")
            if not str(state_dir or "").strip()
            else None
        )
        self._tool_result_store = ApiToolResultStore(
            state_dir or self._temporary_state.name
        )
        self._compacted_tool_result_count = 0
        self._last_request_context_bytes = 0
        self._server = _LoopbackServer(("127.0.0.1", 0), self._handler_type())
        self._thread: threading.Thread | None = None
        self._running = False
        self._request_count = 0
        self._last_request_kind = ""
        self._last_error = ""
        host, port = self._server.server_address[:2]
        self.endpoint = f"http://{host}:{port}/{self._access_token}/v1"

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
            if self._temporary_state is not None:
                self._temporary_state.cleanup()
                self._temporary_state = None
            return
        self._running = False
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=max(0.1, float(timeout_seconds)))
            self._thread = None
        self._upstream_api_key = ""
        if self._temporary_state is not None:
            self._temporary_state.cleanup()
            self._temporary_state = None

    @property
    def pid(self) -> int | None:
        return os.getpid() if self._running else None

    def health(self) -> dict[str, object]:
        return {
            "running": self._running,
            "request_count": self._request_count,
            "last_request_kind": self._last_request_kind,
            "last_error": self._last_error,
            "compacted_tool_result_count": self._compacted_tool_result_count,
            "last_request_context_bytes": self._last_request_context_bytes,
        }

    def _handle_get(self, handler: BaseHTTPRequestHandler) -> None:
        request_path = self._authorized_request_path(handler)
        if request_path is None:
            return
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
            request_path = self._authorized_request_path(handler)
            if request_path is None:
                return
            request = _read_json(
                handler,
                maximum_bytes=self._maximum_request_bytes,
            )
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
        except _GatewayRequestError as error:
            self._last_error = error.message
            _write_json(
                handler,
                error.status,
                {"error": {"type": "invalid_request", "message": error.message}},
            )
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
        except ApiContextLimitError as error:
            self._last_error = str(error)
            _write_json(
                handler,
                413,
                {
                    "type": "error",
                    "error": {
                        "type": error.code,
                        "message": str(error),
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

    def _authorized_request_path(
        self,
        handler: BaseHTTPRequestHandler,
    ) -> str | None:
        request_path = urlsplit(handler.path).path.rstrip("/")
        prefix, separator, remainder = request_path.lstrip("/").partition("/")
        if not (
            separator
            and secrets.compare_digest(prefix, self._access_token)
        ):
            _write_json(
                handler,
                401,
                {"error": {"type": "unauthorized", "message": "Unauthorized."}},
            )
            return None
        return f"/{remainder}".rstrip("/")

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
        with self._context_lock:
            messages = payload.get("messages")
            if isinstance(messages, list):
                for message in messages:
                    if not isinstance(message, dict) or message.get("role") != "tool":
                        continue
                    tool_call_id = str(message.get("tool_call_id") or "")
                    if tool_call_id and tool_call_id not in self._tool_result_references:
                        self._tool_result_references[tool_call_id] = (
                            self._tool_result_store.record(str(message.get("content") or ""))
                        )
            request_view = self._context_policy.prepare(
                payload,
                delivered_tool_call_ids=set(self._delivered_tool_call_ids),
                tool_result_references=dict(self._tool_result_references),
            )
            self._last_request_context_bytes = request_view.encoded_bytes
            self._compacted_tool_result_count += len(
                request_view.compacted_tool_call_ids
            )
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
            data=json.dumps(request_view.payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            opener = (
                safe_remote_urlopen
                if urlsplit(request.full_url).scheme.casefold() == "https"
                else urlopen
            )
            with opener(request, timeout=self.request_timeout_seconds) as response:
                body = response.read()
        except HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            raise _UpstreamHttpError(error.code, _upstream_error_message(body)) from error
        except URLError as error:
            raise NativeModelGatewayError(f"Upstream connection failed: {error.reason}") from error
        decoded = json.loads(body.decode("utf-8"))
        if not isinstance(decoded, dict):
            raise NativeModelGatewayError("Upstream response must be a JSON object.")
        with self._context_lock:
            self._delivered_tool_call_ids.update(request_view.raw_tool_call_ids)
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


def _read_json(
    handler: BaseHTTPRequestHandler,
    *,
    maximum_bytes: int,
) -> dict[str, object]:
    try:
        length = int(handler.headers.get("Content-Length") or 0)
    except ValueError as error:
        raise _GatewayRequestError(400, "Invalid Content-Length.") from error
    if length < 0:
        raise _GatewayRequestError(400, "Invalid Content-Length.")
    if length > maximum_bytes:
        raise _GatewayRequestError(413, "Gateway request body is too large.")
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
