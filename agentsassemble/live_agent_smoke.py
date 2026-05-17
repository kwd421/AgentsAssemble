from __future__ import annotations

import json
import sys
import tempfile
import time
import urllib.parse
from collections.abc import Callable
from pathlib import Path


RequestJson = Callable[..., dict[str, object]]


class LiveAgentSmokeFailed(Exception):
    pass


def run_live_agent_smoke(
    *,
    server: str,
    group_id: str = "",
    timeout_seconds: float = 12.0,
    request_json: RequestJson,
    sleep_fn: Callable[[float], None] = time.sleep,
    python_executable: str = sys.executable,
    temp_dir_factory: Callable[[], object] = tempfile.TemporaryDirectory,
) -> dict[str, object]:
    clean_group_id = smoke_group_id(group_id)
    agent_ids = {
        "local_cli": f"{clean_group_id}-local-cli",
        "live_session": f"{clean_group_id}-live-session",
    }
    expected_messages = {
        agent_ids["local_cli"]: "smoke local_cli ok",
        agent_ids["live_session"]: "smoke live_session ok",
    }
    latest_event_id = _latest_lobby_event_id(request_json(_server_url(server, "/api/lobby")))
    seed_smoke_agent_cursors(
        server,
        agent_ids=agent_ids,
        last_observed_event_id=latest_event_id,
        request_json=request_json,
    )

    started_group: dict[str, object] = {}
    stopped_group: dict[str, object] = {}
    group: dict[str, object] | None = None
    with temp_dir_factory() as temp_dir:
        config_path = Path(temp_dir).resolve() / "live-agents.json"
        config = build_live_agent_smoke_config(server=server, agent_ids=agent_ids, python_executable=python_executable)
        config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        probe_response = request_json(
            _server_url(server, "/api/lobby"),
            method="POST",
            payload={
                "name": "AgentsAssemble Smoke",
                "side": "mine",
                "message": f"live-agent smoke {clean_group_id} {int(time.time() * 1000)}",
            },
        )
        probe_event_id = _event_id(probe_response)
        try:
            start_response = request_json(
                _server_url(server, "/api/live-agent-processes/start"),
                method="POST",
                payload={"config_path": str(config_path), "server": server, "group_id": clean_group_id, "diagnostic": True},
            )
            started_group = start_response.get("group") if isinstance(start_response.get("group"), dict) else {}
            replies = wait_for_smoke_replies(
                server,
                expected_messages=expected_messages,
                source_event_id=probe_event_id,
                request_json=request_json,
                sleep_fn=sleep_fn,
                timeout_seconds=float(timeout_seconds),
            )
            process_payload = wait_for_smoke_group_to_settle(
                server,
                clean_group_id,
                request_json=request_json,
                sleep_fn=sleep_fn,
                timeout_seconds=2.0,
            )
            group = find_process_group(process_payload, clean_group_id)
        finally:
            if group is None:
                group = find_process_group(request_json(_server_url(server, "/api/live-agent-processes")), clean_group_id)
            if group is not None and group.get("status") in {"running", "restarting"}:
                stop_payload = request_json(
                    _server_url(server, f"/api/live-agent-processes/{urllib.parse.quote(clean_group_id, safe='')}/stop"),
                    method="POST",
                    payload={},
                )
                stopped_group = stop_payload.get("group") if isinstance(stop_payload.get("group"), dict) else {}
            elif group is not None:
                stopped_group = group

    return {
        "status": "ok",
        "group_id": clean_group_id,
        "agent_ids": list(agent_ids.values()),
        "source_event_id": probe_event_id,
        "started_group": started_group,
        "stopped_group": stopped_group,
        "replies": replies,
    }


def build_live_agent_smoke_config(
    *,
    server: str,
    agent_ids: dict[str, str],
    python_executable: str = sys.executable,
) -> dict[str, object]:
    local_cli_script = "import sys; sys.stdin.read(); print('smoke local_cli ok')"
    live_session_script = "\n".join(
        [
            "import json, sys",
            "for line in sys.stdin:",
            "    payload = json.loads(line)",
            "    print(json.dumps({'request_id': payload['request_id'], 'message': 'smoke live_session ok'}), flush=True)",
        ]
    )
    return {
        "server": server,
        "poll_interval": 0.05,
        "heartbeat_interval": 0,
        "cooldown": 0,
        "max_chain_depth": 0,
        "max_ticks": 5,
        "agents": [
            {
                "agent_id": agent_ids["local_cli"],
                "display_name": "Smoke Local CLI",
                "provider_kind": "local_cli",
                "connection_kind": "local_cli",
                "engagement_mode": "always",
                "command": [python_executable, "-c", local_cli_script],
                "timeout_seconds": 5,
            },
            {
                "agent_id": agent_ids["live_session"],
                "display_name": "Smoke Live Session",
                "provider_kind": "local_cli",
                "connection_kind": "live_session",
                "engagement_mode": "always",
                "command": [python_executable, "-u", "-c", live_session_script],
                "timeout_seconds": 5,
            },
        ],
    }


def seed_smoke_agent_cursors(
    server: str,
    *,
    agent_ids: dict[str, str],
    last_observed_event_id: str,
    request_json: RequestJson,
) -> None:
    specs = [
        (agent_ids["local_cli"], "Smoke Local CLI", "local_cli"),
        (agent_ids["live_session"], "Smoke Live Session", "live_session"),
    ]
    for agent_id, display_name, connection_kind in specs:
        request_json(
            _server_url(server, "/api/live-agents"),
            method="POST",
            payload={
                "agent_id": agent_id,
                "display_name": display_name,
                "provider_kind": "local_cli",
                "connection_kind": connection_kind,
                "session_id": "",
                "endpoint": "",
                "meeting_id": "",
                "engagement_mode": "always",
                "capabilities": ["room_chat", "mentions"],
                "diagnostic": True,
            },
        )
        request_json(
            _server_url(server, f"/api/live-agents/{urllib.parse.quote(agent_id, safe='')}/heartbeat"),
            method="POST",
            payload={"status": "online", "last_observed_event_id": last_observed_event_id, "diagnostic": True},
        )


def wait_for_smoke_replies(
    server: str,
    *,
    expected_messages: dict[str, str],
    source_event_id: str,
    request_json: RequestJson,
    sleep_fn: Callable[[float], None],
    timeout_seconds: float,
) -> list[dict[str, object]]:
    deadline = time.monotonic() + timeout_seconds
    last_found: list[dict[str, object]] = []
    while time.monotonic() < deadline:
        payload = request_json(_server_url(server, "/api/lobby"))
        events = payload.get("events") if isinstance(payload.get("events"), list) else []
        found = _matching_smoke_replies(events, expected_messages, source_event_id=source_event_id)
        if len(found) == len(expected_messages):
            return found
        last_found = found
        sleep_fn(0.05)
    found_ids = {str(reply["actor_id"]) for reply in last_found}
    missing = ", ".join(agent_id for agent_id in expected_messages if agent_id not in found_ids)
    raise LiveAgentSmokeFailed(f"Timed out waiting for live-agent smoke replies from {missing}.")


def wait_for_smoke_group_to_settle(
    server: str,
    group_id: str,
    *,
    request_json: RequestJson,
    sleep_fn: Callable[[float], None],
    timeout_seconds: float,
) -> dict[str, object]:
    deadline = time.monotonic() + timeout_seconds
    payload: dict[str, object] = {"groups": []}
    while time.monotonic() < deadline:
        payload = request_json(_server_url(server, "/api/live-agent-processes"))
        group = find_process_group(payload, group_id)
        if group is None or group.get("status") not in {"running", "restarting"}:
            return payload
        sleep_fn(0.05)
    return payload


def find_process_group(payload: dict[str, object], group_id: str) -> dict[str, object] | None:
    groups = payload.get("groups") if isinstance(payload.get("groups"), list) else []
    for group in groups:
        if isinstance(group, dict) and group.get("group_id") == group_id:
            return group
    return None


def smoke_group_id(value: object) -> str:
    cleaned = "".join(char if _is_ascii_group_id_char(char) else "-" for char in str(value or "").strip()).strip(".-")
    return cleaned or f"smoke-{int(time.time() * 1000)}"


def _matching_smoke_replies(
    events: list[object],
    expected_messages: dict[str, str],
    *,
    source_event_id: str,
) -> list[dict[str, object]]:
    found = []
    for agent_id, expected_message in expected_messages.items():
        event = next(
            (
                event
                for event in events
                if isinstance(event, dict)
                and event.get("actor_id") == agent_id
                and event.get("message") == expected_message
                and event.get("source_event_id") == source_event_id
            ),
            None,
        )
        if event is not None:
            found.append(
                {
                    "id": str(event.get("id") or ""),
                    "actor_id": agent_id,
                    "message": expected_message,
                    "source_event_id": str(event.get("source_event_id") or ""),
                }
            )
    return found


def _latest_lobby_event_id(payload: dict[str, object]) -> str:
    events = payload.get("events") if isinstance(payload.get("events"), list) else []
    for event in reversed(events):
        if isinstance(event, dict) and event.get("id"):
            return str(event["id"])
    return ""


def _event_id(payload: dict[str, object]) -> str:
    event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
    return str(event.get("id") or "") if isinstance(event, dict) else ""


def _is_ascii_group_id_char(char: str) -> bool:
    return char in "_.-" or "0" <= char <= "9" or "A" <= char <= "Z" or "a" <= char <= "z"


def _server_url(server: str, path: str) -> str:
    return f"{server.rstrip('/')}{path}"
