"""Token-isolated search bridge for provider-private RoomPortal tools."""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable
from uuid import uuid4


_REQUEST_NAME = re.compile(r"^(?P<request_id>[0-9a-f]{32})\.json$")
_MAX_REQUEST_BYTES = 8 * 1024
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_POLL_SECONDS = 0.025


class RoomPortalSearchError(RuntimeError):
    """A visible failure at the provider-to-search boundary."""


class RoomPortalSearchClient:
    """Submit bounded search operations without receiving a room session token."""

    def __init__(self, root: str | Path, *, timeout_seconds: float = 10.0) -> None:
        self.root = Path(root).expanduser().resolve()
        self.requests = self.root / "search-requests"
        self.responses = self.root / "search-responses"
        self.timeout_seconds = max(0.1, float(timeout_seconds))

    def search_messages(
        self,
        query: object,
        *,
        channel_id: object = "all",
        cursor: object = "",
    ) -> dict[str, object]:
        clean_query = _clean_text(query, 200)
        if not clean_query:
            raise RoomPortalSearchError("Room message search requires a query.")
        clean_channel = _clean_identifier(channel_id, default="all")
        clean_cursor = _clean_text(cursor, 2048)
        return self._exchange(
            "search_messages",
            {
                "query": clean_query,
                "channel_id": clean_channel,
                "cursor": clean_cursor,
            },
        )

    def read_message_context(
        self,
        channel_id: object,
        event_id: object,
    ) -> dict[str, object]:
        clean_channel = _clean_identifier(channel_id)
        if clean_channel == "all":
            raise RoomPortalSearchError("Message context requires one concrete channel id.")
        clean_event = _clean_identifier(event_id)
        return self._exchange(
            "read_message_context",
            {"channel_id": clean_channel, "event_id": clean_event},
        )

    def _exchange(self, operation: str, arguments: dict[str, object]) -> dict[str, object]:
        request_id = uuid4().hex
        request_path = self.requests / f"{request_id}.json"
        response_path = self.responses / f"{request_id}.json"
        self.requests.mkdir(parents=True, exist_ok=True)
        self.responses.mkdir(parents=True, exist_ok=True)
        _write_json_atomic(
            request_path,
            {"request_id": request_id, "operation": operation, "arguments": arguments},
        )
        deadline = time.monotonic() + self.timeout_seconds
        try:
            while time.monotonic() < deadline:
                try:
                    payload = _read_json(response_path, max_bytes=_MAX_RESPONSE_BYTES)
                except FileNotFoundError:
                    time.sleep(_POLL_SECONDS)
                    continue
                if not isinstance(payload, dict):
                    raise RoomPortalSearchError("Room search returned an invalid response.")
                if payload.get("ok") is not True:
                    message = _clean_text(payload.get("error"), 500)
                    raise RoomPortalSearchError(message or "Room search failed.")
                result = payload.get("result")
                if not isinstance(result, dict):
                    raise RoomPortalSearchError("Room search returned an invalid result.")
                return result
            raise RoomPortalSearchError("Room search timed out; no result was hidden.")
        finally:
            request_path.unlink(missing_ok=True)
            response_path.unlink(missing_ok=True)


class RoomPortalSearchTools:
    """RoomPortal methods owned by the token-isolated search boundary."""

    root: Path

    def _require_tool(self, name: str) -> None:
        raise NotImplementedError

    def _record_activity(self, operation: str, **kwargs: object) -> None:
        raise NotImplementedError

    def search_messages(
        self,
        query: object,
        *,
        channel_id: object = "all",
        cursor: object = "",
    ) -> dict[str, object]:
        self._require_tool("search_messages")
        result = RoomPortalSearchClient(self.root).search_messages(
            query,
            channel_id=channel_id,
            cursor=cursor,
        )
        self._record_activity("search_messages")
        return result

    def read_message_context(
        self,
        channel_id: object,
        event_id: object,
    ) -> dict[str, object]:
        self._require_tool("read_message_context")
        result = RoomPortalSearchClient(self.root).read_message_context(
            channel_id,
            event_id,
        )
        self._record_activity("read_message_context")
        return result


class RoomPortalSearchBroker:
    """Perform authorized search HTTP calls while keeping the token bridge-private."""

    def __init__(
        self,
        root: str | Path,
        *,
        server_url: str,
        session_token: str,
        room_id: str,
        tool_allowed: Callable[[str], bool],
        request_timeout_seconds: float = 5.0,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self.requests = self.root / "search-requests"
        self.responses = self.root / "search-responses"
        self.server_url = _validated_server_url(server_url)
        self.session_token = str(session_token or "")
        self.room_id = _clean_identifier(room_id)
        self.tool_allowed = tool_allowed
        self.request_timeout_seconds = max(0.1, float(request_timeout_seconds))
        if not self.session_token:
            raise ValueError("Room search broker requires a session token.")
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        if not self.session_token:
            raise RoomPortalSearchError("A stopped room search broker cannot be restarted.")
        self.requests.mkdir(parents=True, exist_ok=True)
        self.responses.mkdir(parents=True, exist_ok=True)
        _chmod(self.requests, 0o700)
        _chmod(self.responses, 0o700)
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="room-portal-search",
            daemon=True,
        )
        self._thread.start()

    def stop(self, *, timeout_seconds: float = 6.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=max(0.0, float(timeout_seconds)))
        alive = bool(thread is not None and thread.is_alive())
        self._thread = None
        self.session_token = ""
        if alive:
            raise RoomPortalSearchError("Room search broker did not stop cleanly.")

    def _run(self) -> None:
        next_cleanup = 0.0
        while not self._stop.is_set():
            if time.monotonic() >= next_cleanup:
                self._prune(self.requests, max_age_seconds=15.0, max_files=64)
                self._prune(self.responses, max_age_seconds=30.0, max_files=64)
                next_cleanup = time.monotonic() + 5.0
            handled = False
            try:
                paths = sorted(self.requests.glob("*.json"), key=lambda item: item.name)[:32]
            except OSError:
                paths = []
            for path in paths:
                handled = True
                self._handle(path)
            if not handled:
                self._stop.wait(_POLL_SECONDS)

    @staticmethod
    def _prune(root: Path, *, max_age_seconds: float, max_files: int) -> None:
        try:
            paths = sorted(
                root.glob("*.json"),
                key=lambda item: item.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            return
        cutoff = time.time() - max_age_seconds
        for index, path in enumerate(paths):
            try:
                if index >= max_files or path.stat().st_mtime < cutoff:
                    path.unlink(missing_ok=True)
            except OSError:
                continue

    def _handle(self, request_path: Path) -> None:
        match = _REQUEST_NAME.fullmatch(request_path.name)
        if match is None:
            request_path.unlink(missing_ok=True)
            return
        request_id = match.group("request_id")
        try:
            payload = _read_json(request_path, max_bytes=_MAX_REQUEST_BYTES)
            request_path.unlink(missing_ok=True)
            result = self._execute(request_id, payload)
            response = {"ok": True, "result": result}
        except Exception as error:
            request_path.unlink(missing_ok=True)
            response = {"ok": False, "error": _visible_error(error)}
        _write_json_atomic(self.responses / f"{request_id}.json", response)

    def _execute(self, request_id: str, payload: object) -> dict[str, object]:
        if not isinstance(payload, dict) or payload.get("request_id") != request_id:
            raise RoomPortalSearchError("Room search request was invalid.")
        operation = str(payload.get("operation") or "")
        if operation not in {"search_messages", "read_message_context"}:
            raise RoomPortalSearchError("Room search operation was invalid.")
        if not self.tool_allowed(operation):
            raise RoomPortalSearchError(
                f"Room tool {operation} is unavailable for this observation."
            )
        arguments = payload.get("arguments")
        if not isinstance(arguments, dict):
            raise RoomPortalSearchError("Room search arguments were invalid.")
        if operation == "search_messages":
            query = _clean_text(arguments.get("query"), 200)
            if not query:
                raise RoomPortalSearchError("Room message search requires a query.")
            parameters = {
                "room_id": self.room_id,
                "channel_id": _clean_identifier(arguments.get("channel_id"), default="all"),
                "q": query,
                "cursor": _clean_text(arguments.get("cursor"), 2048),
            }
            path = "/api/room-search"
        else:
            channel_id = _clean_identifier(arguments.get("channel_id"))
            if channel_id == "all":
                raise RoomPortalSearchError(
                    "Message context requires one concrete channel id."
                )
            parameters = {
                "room_id": self.room_id,
                "channel_id": channel_id,
                "event_id": _clean_identifier(arguments.get("event_id")),
            }
            path = "/api/room-search/context"
        return self._get_json(path, parameters)

    def _get_json(self, path: str, parameters: dict[str, str]) -> dict[str, object]:
        query = urllib.parse.urlencode(parameters)
        request = urllib.request.Request(
            f"{self.server_url}{path}?{query}",
            headers={"Authorization": f"Bearer {self.session_token}"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=self.request_timeout_seconds,
            ) as response:
                raw = response.read(_MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as error:
            message = ""
            try:
                error_payload = json.loads(error.read(4096).decode("utf-8"))
                if isinstance(error_payload, dict):
                    message = _clean_text(
                        error_payload.get("error") or error_payload.get("message"),
                        500,
                    )
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                pass
            raise RoomPortalSearchError(
                message or f"Room search was rejected with HTTP {error.code}."
            ) from error
        except (OSError, TimeoutError) as error:
            raise RoomPortalSearchError(f"Room search could not reach the room server: {error}") from error
        if len(raw) > _MAX_RESPONSE_BYTES:
            raise RoomPortalSearchError("Room search response exceeded its bounded size.")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RoomPortalSearchError("Room search returned invalid JSON.") from error
        if not isinstance(payload, dict):
            raise RoomPortalSearchError("Room search returned an invalid response.")
        return payload


def _clean_text(value: object, limit: int) -> str:
    return str(value or "").replace("\x00", "").strip()[:limit]


def _clean_identifier(value: object, *, default: str = "") -> str:
    cleaned = _clean_text(value, 128) or default
    if not cleaned or re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", cleaned) is None:
        raise RoomPortalSearchError("Room search identifier was invalid.")
    return cleaned


def _validated_server_url(value: object) -> str:
    text = str(value or "").rstrip("/")
    parsed = urllib.parse.urlsplit(text)
    scheme = {"ws": "http", "wss": "https"}.get(parsed.scheme, parsed.scheme)
    if scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Room search broker requires an HTTP(S) or WS(S) server URL.")
    if parsed.query or parsed.fragment:
        raise ValueError("Room search broker server URL must not contain a query or fragment.")
    return urllib.parse.urlunsplit(
        (scheme, parsed.netloc, parsed.path.rstrip("/"), "", "")
    )


def _read_json(path: Path, *, max_bytes: int) -> object:
    with path.open("rb") as stream:
        raw = stream.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise RoomPortalSearchError("Room search IPC payload exceeded its bounded size.")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RoomPortalSearchError("Room search IPC payload was invalid.") from error


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _chmod(temporary, 0o600)
    os.replace(temporary, path)


def _chmod(path: Path, mode: int) -> None:
    try:
        path.chmod(mode)
    except OSError:
        pass


def _visible_error(error: Exception) -> str:
    if isinstance(error, RoomPortalSearchError):
        return _clean_text(error, 500)
    return "Room search failed inside the authenticated bridge."


__all__ = [
    "RoomPortalSearchBroker",
    "RoomPortalSearchClient",
    "RoomPortalSearchError",
    "RoomPortalSearchTools",
]
