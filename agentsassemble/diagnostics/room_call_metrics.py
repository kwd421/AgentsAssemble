"""Collect provider-call metrics for a bounded canonical-room experiment."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen


_UNKNOWN_TOKEN_PROVIDERS = {
    "claude_code": "claude",
    "antigravity_live_session": "antigravity",
}


def collect_room_call_metrics(
    room_state: dict[str, object],
    *,
    since: str,
    until: str,
    codex_session_root: Path,
    runtime_root: Path,
) -> list[dict[str, object]]:
    events = [event for event in room_state.get("events", []) if isinstance(event, dict)]
    sessions = [session for session in room_state.get("sessions", []) if isinstance(session, dict)]
    provider_by_participant = {
        str(session.get("participant_id") or ""): _UNKNOWN_TOKEN_PROVIDERS.get(
            str(session.get("provider_kind") or "")
        )
        for session in sessions
    }
    rows = [
        {
            "timestamp": str(event.get("created_at") or ""),
            "provider": provider,
            "input_tokens": None,
            "output_tokens": None,
            "cumulative_room_message_count": _message_count_at(
                events, str(event.get("created_at") or "")
            ),
            "token_status": "unavailable",
            "source": "canonical_turn_started_lower_bound",
        }
        for event in events
        if event.get("type") == "turn_started"
        and (provider := provider_by_participant.get(str(event.get("participant_id") or "")))
        and _in_window(str(event.get("created_at") or ""), since, until)
    ]
    rows.extend(
        _codex_rows(
            events,
            root=codex_session_root,
            since=since,
            until=until,
        )
    )
    rows.extend(
        _grok_rows(
            events,
            root=runtime_root / "rooms" / _room_id(room_state),
            since=since,
            until=until,
        )
    )
    return sorted(rows, key=lambda row: (str(row["timestamp"]), str(row["provider"])))


def _codex_rows(
    events: list[dict[str, object]],
    *,
    root: Path,
    since: str,
    until: str,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for path in sorted(root.glob("**/rollout-*.jsonl")):
        try:
            stream = path.open(encoding="utf-8")
        except OSError:
            continue
        with stream:
            first = _next_json_object(stream)
            first_payload = first.get("payload") if isinstance(first, dict) else None
            if (
                not first
                or first.get("type") != "session_meta"
                or not isinstance(first_payload, dict)
                or str(first_payload.get("originator") or "") != "AgentsAssemble"
            ):
                continue
            for entry in stream:
                payload = _json_object(entry)
                if not payload or payload.get("type") != "event_msg":
                    continue
                timestamp = str(payload.get("timestamp") or "")
                event_payload = payload.get("payload")
                if (
                    not isinstance(event_payload, dict)
                    or event_payload.get("type") != "token_count"
                    or not _in_window(timestamp, since, until)
                ):
                    continue
                info = event_payload.get("info")
                usage = info.get("last_token_usage") if isinstance(info, dict) else None
                if not isinstance(usage, dict):
                    continue
                rows.append(
                    _token_row(
                        events,
                        timestamp=timestamp,
                        provider="codex",
                        input_tokens=usage.get("input_tokens"),
                        output_tokens=usage.get("output_tokens"),
                        source="codex_token_count",
                    )
                )
    return rows


def _grok_rows(
    events: list[dict[str, object]],
    *,
    root: Path,
    since: str,
    until: str,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for path in sorted(root.glob("bridges/grok-*/*/provider-state/logs/unified.jsonl")):
        try:
            stream = path.open(encoding="utf-8")
        except OSError:
            continue
        with stream:
            for entry in stream:
                payload = _json_object(entry)
                if not payload or payload.get("msg") != "shell.turn.inference_done":
                    continue
                timestamp = str(payload.get("ts") or "")
                context = payload.get("ctx")
                if not isinstance(context, dict) or not _in_window(timestamp, since, until):
                    continue
                rows.append(
                    _token_row(
                        events,
                        timestamp=timestamp,
                        provider="grok",
                        input_tokens=context.get("prompt_tokens"),
                        output_tokens=context.get("completion_tokens"),
                        source="grok_inference_done",
                    )
                )
    return rows


def _token_row(
    events: list[dict[str, object]],
    *,
    timestamp: str,
    provider: str,
    input_tokens: object,
    output_tokens: object,
    source: str,
) -> dict[str, object]:
    return {
        "timestamp": timestamp,
        "provider": provider,
        "input_tokens": max(0, int(input_tokens or 0)),
        "output_tokens": max(0, int(output_tokens or 0)),
        "cumulative_room_message_count": _message_count_at(events, timestamp),
        "token_status": "confirmed",
        "source": source,
    }


def _message_count_at(events: list[dict[str, object]], timestamp: str) -> int:
    point = _parse_timestamp(timestamp)
    return sum(
        1
        for event in events
        if event.get("type") == "message_final"
        and (created_at := _parse_timestamp(str(event.get("created_at") or ""))) is not None
        and point is not None
        and created_at <= point
    )


def _in_window(timestamp: str, since: str, until: str) -> bool:
    point = _parse_timestamp(timestamp)
    start = _parse_timestamp(since)
    end = _parse_timestamp(until)
    return point is not None and start is not None and end is not None and start <= point <= end


def _parse_timestamp(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _next_json_object(stream) -> dict[str, object] | None:
    for line in stream:
        if payload := _json_object(line):
            return payload
    return None


def _json_object(line: str) -> dict[str, object] | None:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _room_id(room_state: dict[str, object]) -> str:
    room = room_state.get("room")
    return str(room.get("room_id") or "") if isinstance(room, dict) else ""


def _room_state(server: str, room_id: str) -> dict[str, object]:
    query = urlencode({"room_id": room_id})
    with urlopen(f"{server.rstrip('/')}/api/rooms/state?{query}", timeout=10.0) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise ValueError("Room state response must be an object.")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", default="http://127.0.0.1:8765")
    parser.add_argument("--room-id", required=True)
    parser.add_argument("--since", required=True)
    parser.add_argument("--until", required=True)
    parser.add_argument("--runtime-root", type=Path, default=Path(".agentsassemble"))
    parser.add_argument("--codex-session-root", type=Path, default=Path.home() / ".codex" / "sessions")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--input-token-cap", type=int, default=2_000_000)
    args = parser.parse_args()

    state = _room_state(args.server, args.room_id)
    rows = collect_room_call_metrics(
        state,
        since=args.since,
        until=args.until,
        codex_session_root=args.codex_session_root,
        runtime_root=args.runtime_root,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    confirmed_input_tokens = sum(
        int(row["input_tokens"] or 0) for row in rows if row["token_status"] == "confirmed"
    )
    summary = {
        "call_rows": len(rows),
        "confirmed_input_tokens": confirmed_input_tokens,
        "confirmed_output_tokens": sum(
            int(row["output_tokens"] or 0) for row in rows if row["token_status"] == "confirmed"
        ),
        "input_token_cap": max(0, args.input_token_cap),
        "cap_reached": confirmed_input_tokens >= max(0, args.input_token_cap),
        "output": str(args.output),
        "provider_call_rows": {
            provider: sum(1 for row in rows if row["provider"] == provider)
            for provider in ("codex", "claude", "grok", "antigravity")
        },
    }
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 2 if summary["cap_reached"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
