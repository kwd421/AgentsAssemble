"""Current-session room connector over the canonical room WebSocket."""

from __future__ import annotations

import json
import secrets
import threading
from collections import deque
from collections.abc import Iterable
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen
from uuid import uuid4

from agentsassemble.room.text import clean_room_text
from agentsassemble.web.room_client import (
    WsRoomClient,
    connect_room_ws,
    join_room_session,
)


ROOM_CONNECTOR_MESSAGE_LIMIT = 50
ROOM_CONNECTOR_DEDUPE_LIMIT = 200
ROOM_CONNECTOR_COMMAND_TIMEOUT_SECONDS = 30.0
ROOM_CONNECTOR_JOIN_TIMEOUT_SECONDS = 10.0
ROOM_CONNECTOR_SOCKET_IDLE_SECONDS = 30.0
ROOM_CONNECTOR_SEARCH_RESPONSE_LIMIT_BYTES = 2 * 1024 * 1024


class RoomConnectorError(RuntimeError):
    """The current app or CLI session could not use its room connection."""


class RoomConnectorRejected(RoomConnectorError):
    def __init__(self, message: str, *, code: str = "rejected") -> None:
        super().__init__(message)
        self.code = code


class RoomConnector:
    """Join one invite as the caller and expose blocking, incremental room tools.

    The connector owns transport state only. It never launches a provider
    process or creates a managed Agent Session.
    """

    def __init__(
        self,
        *,
        allowed_server_urls: Iterable[str] | None = None,
    ) -> None:
        self._condition = threading.Condition(threading.RLock())
        self._client: WsRoomClient | None = None
        self._receiver: threading.Thread | None = None
        self._stop = threading.Event()
        self._ready = False
        self._closed_reason = ""
        self._invite_url = ""
        self._server_url = ""
        self._session_token = ""
        self._participant_id = ""
        self._display_name = ""
        self._room_id = ""
        self._room: dict[str, object] = {}
        self._participants: list[dict[str, object]] = []
        self._messages: deque[dict[str, object]] = deque(
            maxlen=ROOM_CONNECTOR_MESSAGE_LIMIT,
        )
        self._pending_messages: deque[dict[str, object]] = deque()
        self._seen_message_keys: deque[str] = deque()
        self._seen_message_key_set: set[str] = set()
        self._responses: dict[str, dict[str, object]] = {}
        self._last_seq = 0
        self._device_token = f"room-connector-{secrets.token_urlsafe(24)}"
        self._allowed_server_urls = (
            frozenset(
                normalize_room_server_url(server_url)
                for server_url in allowed_server_urls
            )
            if allowed_server_urls is not None
            else None
        )

    def join(
        self,
        invite_url: str,
        *,
        display_name: str = "",
    ) -> dict[str, object]:
        clean_invite_url = str(invite_url or "").strip()
        with self._condition:
            if self._client is not None:
                if clean_invite_url == self._invite_url:
                    return self._joined_payload()
                raise RoomConnectorError(
                    "This connector is already in a room. Leave it before joining another."
                )
        server_url, invite_token = parse_room_invite_url(clean_invite_url)
        if (
            self._allowed_server_urls is not None
            and server_url not in self._allowed_server_urls
        ):
            raise RoomConnectorError(
                "This remote connector is not allowed to contact that room server."
            )
        joined = join_room_session(
            server_url,
            invite_token,
            display_name=clean_room_text(display_name, limit=128),
            participant_type="agent",
            device_token=self._device_token,
            timeout=ROOM_CONNECTOR_JOIN_TIMEOUT_SECONDS,
        )
        session_token = str(joined.get("session_token") or "")
        client: WsRoomClient | None = None
        try:
            client = connect_room_ws(
                server_url,
                session_token,
                ["room_events"],
                timeout=ROOM_CONNECTOR_JOIN_TIMEOUT_SECONDS,
            )
            client.set_receive_timeout(ROOM_CONNECTOR_SOCKET_IDLE_SECONDS)
            with self._condition:
                self._invite_url = clean_invite_url
                self._server_url = server_url
                self._session_token = session_token
                self._participant_id = clean_room_text(
                    joined.get("agent_id"),
                    limit=128,
                )
                self._display_name = clean_room_text(
                    joined.get("display_name"),
                    limit=128,
                )
                self._room_id = clean_room_text(
                    joined.get("meeting_id"),
                    limit=128,
                )
                self._client = client
                self._stop.clear()
                self._closed_reason = ""
                self._ready = False
                self._receiver = threading.Thread(
                    target=self._receive_loop,
                    name=f"room-connector-{self._participant_id}",
                    daemon=True,
                )
                self._receiver.start()
                if not self._condition.wait_for(
                    lambda: self._ready or bool(self._closed_reason),
                    timeout=ROOM_CONNECTOR_JOIN_TIMEOUT_SECONDS,
                ):
                    raise RoomConnectorError(
                        "The room connection did not deliver its initial snapshot."
                    )
                self._raise_if_closed()
                return self._joined_payload()
        except Exception:
            if client is not None:
                client.close()
            self._reset_connection_state()
            raise

    def read(self) -> dict[str, object]:
        with self._condition:
            self._require_joined()
            self._raise_if_closed()
            return {
                "room": dict(self._room),
                "participants": [dict(item) for item in self._participants],
                "messages": [dict(item) for item in self._messages],
                "last_seq": self._last_seq,
            }

    def search_messages(
        self,
        query: str,
        *,
        channel_id: str = "all",
        cursor: str = "",
    ) -> dict[str, object]:
        clean_query = clean_room_text(query, limit=200)
        if not clean_query:
            raise RoomConnectorError("Room message search requires a query.")
        return self._search_get(
            "/api/room-search",
            {
                "q": clean_query,
                "channel_id": clean_room_text(channel_id, limit=128) or "all",
                "cursor": clean_room_text(cursor, limit=2048),
            },
        )

    def read_message_context(
        self,
        channel_id: str,
        event_id: str,
    ) -> dict[str, object]:
        clean_channel = clean_room_text(channel_id, limit=128)
        clean_event = clean_room_text(event_id, limit=128)
        if not clean_channel or clean_channel == "all" or not clean_event:
            raise RoomConnectorError(
                "Message context requires a concrete channel id and event id."
            )
        return self._search_get(
            "/api/room-search/context",
            {"channel_id": clean_channel, "event_id": clean_event},
        )

    def wait_next(self) -> dict[str, object]:
        """Block without a model-visible timeout until a new public message exists."""

        with self._condition:
            self._require_joined()
            self._condition.wait_for(
                lambda: bool(self._pending_messages) or bool(self._closed_reason),
            )
            self._raise_if_closed()
            messages = [dict(item) for item in self._pending_messages]
            self._pending_messages.clear()
            return {
                "messages": messages,
                "last_seq": self._last_seq,
            }

    def say(self, content: str) -> dict[str, object]:
        message = str(content or "").replace("\x00", "").strip()[:12_000]
        if not message:
            raise RoomConnectorError("A room message cannot be empty.")
        return self._event_command(
            "message.send",
            {"content": message},
        )

    def create_vote(
        self,
        question: str,
        options: list[str],
        *,
        duration_seconds: int = 0,
    ) -> dict[str, object]:
        return self._event_command(
            "message.send",
            {
                "kind": "vote",
                "vote_question": question,
                "vote_options": list(options),
                "vote_duration_seconds": duration_seconds,
            },
        )

    def cast_vote(self, vote_id: str, choice: str) -> dict[str, object]:
        return self._event_command(
            "message.send",
            {
                "kind": "vote_cast",
                "vote_id": vote_id,
                "vote_choice": choice,
            },
        )

    def withdraw_vote(self, vote_id: str) -> dict[str, object]:
        return self._event_command(
            "message.send",
            {
                "kind": "vote_withdraw",
                "vote_id": vote_id,
            },
        )

    def close_vote(self, vote_id: str) -> dict[str, object]:
        return self._event_command(
            "message.send",
            {
                "kind": "vote_close",
                "vote_id": vote_id,
            },
        )

    def vote_summary(self, vote_id: str) -> dict[str, object]:
        return self._command_result(
            "room.vote.summary",
            {"vote_id": vote_id},
        )

    def roll_dice(self, notation: str, *, reason: str = "") -> dict[str, object]:
        return self._event_command(
            "room.random.roll",
            {"notation": notation, "reason": reason},
        )

    def choose_random(
        self,
        options: list[str],
        *,
        reason: str = "",
    ) -> dict[str, object]:
        return self._event_command(
            "room.random.choose",
            {"options": list(options), "reason": reason},
        )

    def _event_command(
        self,
        action: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        result = self._command_result(action, payload)
        event = result.get("event")
        if not isinstance(event, dict):
            raise RoomConnectorError("The room acknowledged the command without an event.")
        return {"event": dict(event)}

    def _search_get(
        self,
        path: str,
        parameters: dict[str, str],
    ) -> dict[str, object]:
        with self._condition:
            self._require_joined()
            self._raise_if_closed()
            server_url = self._server_url
            session_token = self._session_token
            room_id = self._room_id
        request = Request(
            f"{server_url.rstrip('/')}{path}?{urlencode({'room_id': room_id, **parameters})}",
            headers={"Authorization": f"Bearer {session_token}"},
            method="GET",
        )
        try:
            with urlopen(
                request,
                timeout=ROOM_CONNECTOR_COMMAND_TIMEOUT_SECONDS,
            ) as response:
                raw = response.read(ROOM_CONNECTOR_SEARCH_RESPONSE_LIMIT_BYTES + 1)
                if len(raw) > ROOM_CONNECTOR_SEARCH_RESPONSE_LIMIT_BYTES:
                    raise RoomConnectorError(
                        "Room search response exceeded its bounded size."
                    )
                payload = json.loads(raw.decode("utf-8"))
        except HTTPError as error:
            details = error.read(4096).decode("utf-8", "replace")
            error.close()
            raise RoomConnectorError(
                details or f"Room search failed with HTTP {error.code}."
            ) from error
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RoomConnectorError(f"Room search failed: {error}") from error
        if not isinstance(payload, dict):
            raise RoomConnectorError("Room search returned an invalid response.")
        return payload

    def _command_result(
        self,
        action: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        with self._condition:
            self._require_joined()
            self._raise_if_closed()
            client = self._client
            assert client is not None
            request_id = client.command(
                action,
                payload,
                request_id=f"connector-{uuid4().hex}",
            )
            response = self._wait_for_response(request_id)
        if response.get("op") == "nack":
            error = response.get("error")
            details = dict(error) if isinstance(error, dict) else {}
            raise RoomConnectorRejected(
                str(details.get("message") or "The room rejected this message."),
                code=str(details.get("code") or "rejected"),
            )
        result = response.get("result")
        if not isinstance(result, dict):
            raise RoomConnectorError("The room acknowledged the command without a result.")
        return dict(result)

    def leave(self) -> dict[str, object]:
        with self._condition:
            self._require_joined()
            server_url = self._server_url
            session_token = self._session_token
        request = Request(
            f"{server_url.rstrip('/')}/api/room-invite/leave",
            data=b"{}",
            headers={
                "Authorization": f"Bearer {session_token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=ROOM_CONNECTOR_JOIN_TIMEOUT_SECONDS) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            details = error.read().decode("utf-8", "replace")
            error.close()
            raise RoomConnectorError(
                details or f"Room leave failed with HTTP {error.code}."
            ) from error
        self.close()
        return dict(payload) if isinstance(payload, dict) else {"status": "left"}

    def close(self) -> None:
        with self._condition:
            client = self._client
            receiver = self._receiver
            self._stop.set()
            self._closed_reason = self._closed_reason or "The room connection was closed."
            self._condition.notify_all()
        if client is not None:
            client.close()
        if receiver is not None and receiver is not threading.current_thread():
            receiver.join(timeout=1.0)
        self._reset_connection_state(
            closed_reason="The room connection was closed.",
        )

    def _receive_loop(self) -> None:
        while not self._stop.is_set():
            with self._condition:
                client = self._client
            if client is None:
                return
            messages = client.receive()
            if client.closed:
                with self._condition:
                    if not self._stop.is_set():
                        self._closed_reason = "The room WebSocket disconnected."
                    self._condition.notify_all()
                return
            if not messages:
                continue
            with self._condition:
                for message in messages:
                    self._ingest_server_message(message)
                self._condition.notify_all()

    def _ingest_server_message(self, message: dict[str, object]) -> None:
        op = str(message.get("op") or "")
        if op == "snapshot" and message.get("stream") == "room_events":
            self._room = (
                dict(message["room"])
                if isinstance(message.get("room"), dict)
                else {}
            )
            self._participants = [
                dict(item)
                for item in (
                    message.get("participants")
                    if isinstance(message.get("participants"), list)
                    else []
                )
                if isinstance(item, dict)
            ]
            for event in _message_events(message):
                self._remember_message(event, pending=False)
            self._last_seq = max(
                self._last_seq,
                _safe_nonnegative_int(message.get("last_seq")),
            )
            self._ready = True
            return
        if op == "event" and message.get("stream") == "room_events":
            for event in _message_events(message):
                self._remember_message(
                    event,
                    pending=(
                        clean_room_text(event.get("actor_id"), limit=128)
                        != self._participant_id
                    ),
                )
            return
        request_id = clean_room_text(message.get("request_id"), limit=128)
        if op in {"ack", "nack"} and request_id:
            self._responses[request_id] = dict(message)
            return
        if op in {"error", "room_deleted", "resync_required"}:
            self._closed_reason = str(
                message.get("message")
                or message.get("reason")
                or "The room connection cannot continue."
            )

    def _remember_message(
        self,
        event: dict[str, object],
        *,
        pending: bool,
    ) -> None:
        seq = _safe_nonnegative_int(event.get("seq"))
        event_id = clean_room_text(event.get("id"), limit=128)
        message_key = event_id or (f"seq:{seq}" if seq else "")
        if message_key and message_key in self._seen_message_key_set:
            return
        if message_key:
            if len(self._seen_message_keys) >= ROOM_CONNECTOR_DEDUPE_LIMIT:
                expired = self._seen_message_keys.popleft()
                self._seen_message_key_set.discard(expired)
            self._seen_message_keys.append(message_key)
            self._seen_message_key_set.add(message_key)
        self._last_seq = max(self._last_seq, seq)
        projected = {
            "id": event_id,
            "seq": seq,
            "actor_id": clean_room_text(event.get("actor_id"), limit=128),
            "actor_type": clean_room_text(event.get("actor_type"), limit=32),
            "name": clean_room_text(
                event.get("actor_display_name")
                or event.get("display_name")
                or event.get("name"),
                limit=128,
            ),
            "content": str(event.get("content") or ""),
            "created_at": str(event.get("created_at") or ""),
            "attachments": [
                dict(item)
                for item in (
                    event.get("attachments")
                    if isinstance(event.get("attachments"), list)
                    else []
                )
                if isinstance(item, dict)
            ],
        }
        self._messages.append(projected)
        if pending:
            self._pending_messages.append(projected)

    def _wait_for_response(self, request_id: str) -> dict[str, object]:
        if not self._condition.wait_for(
            lambda: request_id in self._responses or bool(self._closed_reason),
            timeout=ROOM_CONNECTOR_COMMAND_TIMEOUT_SECONDS,
        ):
            raise RoomConnectorError("The room did not acknowledge the command.")
        self._raise_if_closed()
        return self._responses.pop(request_id)

    def _joined_payload(self) -> dict[str, object]:
        return {
            "status": "joined",
            "room_id": self._room_id,
            "participant_id": self._participant_id,
            "display_name": self._display_name,
            "instructions": (
                "You are now this room participant. Immediately call room_read. "
                "Use room_say only for a substantive contribution and room_wait_next "
                "to await new messages. Do not launch or delegate to another model."
            ),
        }

    def _require_joined(self) -> None:
        if self._client is None:
            raise RoomConnectorError("Join a room link first with room_join.")

    def _raise_if_closed(self) -> None:
        if self._closed_reason:
            raise RoomConnectorError(self._closed_reason)

    def _reset_connection_state(self, *, closed_reason: str = "") -> None:
        with self._condition:
            self._client = None
            self._receiver = None
            self._ready = False
            self._closed_reason = closed_reason
            self._invite_url = ""
            self._server_url = ""
            self._session_token = ""
            self._participant_id = ""
            self._display_name = ""
            self._room_id = ""
            self._room = {}
            self._participants = []
            self._messages.clear()
            self._pending_messages.clear()
            self._seen_message_keys.clear()
            self._seen_message_key_set.clear()
            self._responses.clear()
            self._last_seq = 0
            self._condition.notify_all()


def parse_room_invite_url(value: str) -> tuple[str, str]:
    try:
        parsed = urlsplit(str(value or "").strip())
    except ValueError:
        raise ValueError("Invite URL is invalid.") from None
    token = str(parse_qs(parsed.query).get("token", [""])[0] or "")
    path = parsed.path.rstrip("/")
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or not token:
        raise ValueError("Invite URL must be an HTTP(S) /join URL containing a token.")
    if not path.endswith("/join"):
        raise ValueError("Invite URL must point to /join.")
    server_path = path[: -len("/join")].rstrip("/")
    server_url = urlunsplit(
        (parsed.scheme, parsed.netloc, server_path, "", ""),
    )
    return server_url, token


def normalize_room_server_url(value: str) -> str:
    try:
        parsed = urlsplit(str(value or "").strip())
    except ValueError:
        raise ValueError("Room server URL is invalid.") from None
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Room server URL must be HTTP(S).")
    if parsed.query or parsed.fragment:
        raise ValueError("Room server URL cannot contain a query or fragment.")
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""),
    )


def _message_events(message: dict[str, object]) -> list[dict[str, object]]:
    events = message.get("events")
    if not isinstance(events, list):
        return []
    return [
        dict(event)
        for event in events
        if isinstance(event, dict)
        and clean_room_text(event.get("type"), limit=64) == "message_final"
    ]


def _safe_nonnegative_int(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


__all__ = [
    "RoomConnector",
    "RoomConnectorError",
    "RoomConnectorRejected",
    "normalize_room_server_url",
    "parse_room_invite_url",
]
