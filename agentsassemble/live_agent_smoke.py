from __future__ import annotations

import json
import math
import sys
import tempfile
import threading
import time
import urllib.parse
from collections.abc import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from agentsassemble.meeting_events import write_live_state


RequestJson = Callable[..., dict[str, object]]
SMOKE_BRIDGE_TOKEN = "agentsassemble-smoke-token"
OFFICIAL_ROUND_SMOKE_ROUND_ID = "official_round_smoke"
SMOKE_GROUP_ID_LIMIT = 48


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
        "remote_bridge": f"{clean_group_id}-remote-bridge",
    }
    expected_messages = {
        agent_ids["local_cli"]: "smoke local_cli ok",
        agent_ids["live_session"]: "smoke live_session ok",
        agent_ids["remote_bridge"]: "smoke remote_bridge ok",
    }
    started_group: dict[str, object] = {}
    stopped_group: dict[str, object] = {}
    group: dict[str, object] | None = None
    with _SmokeRemoteBridgeServer() as bridge:
        latest_event_id = _latest_lobby_event_id(request_json(_server_url(server, "/api/lobby")))
        seed_smoke_agent_cursors(
            server,
            agent_ids=agent_ids,
            last_observed_event_id=latest_event_id,
            request_json=request_json,
            bridge_endpoint=bridge["endpoint"],
        )

        with temp_dir_factory() as temp_dir:
            config_path = Path(temp_dir).resolve() / "live-agents.json"
            config = build_live_agent_smoke_config(
                server=server,
                agent_ids=agent_ids,
                python_executable=python_executable,
                bridge_endpoint=bridge["endpoint"],
                bridge_auth_ref=bridge["auth_ref"],
            )
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
    bridge_endpoint: str,
    bridge_auth_ref: str,
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
            {
                "agent_id": agent_ids["remote_bridge"],
                "display_name": "Smoke Remote Bridge",
                "provider_kind": "remote_http_bridge",
                "connection_kind": "remote_bridge",
                "engagement_mode": "always",
                "endpoint": bridge_endpoint,
                "auth_ref": bridge_auth_ref,
                "timeout_seconds": 5,
            },
        ],
    }


def run_live_agent_official_round_smoke(
    *,
    output_root: Path,
    server: str,
    group_id: str = "",
    timeout_seconds: float = 12.0,
    request_json: RequestJson,
    sleep_fn: Callable[[float], None] = time.sleep,
    python_executable: str = sys.executable,
    temp_dir_factory: Callable[[], object] = tempfile.TemporaryDirectory,
) -> dict[str, object]:
    clean_group_id = smoke_group_id(group_id or f"round-smoke-{int(time.time() * 1000)}")
    meeting_id = f"official-round-smoke-{clean_group_id}"
    agent_ids = {
        "local_cli": f"{clean_group_id}-local-cli",
        "live_session": f"{clean_group_id}-live-session",
        "remote_bridge": f"{clean_group_id}-remote-bridge",
    }
    role_ids = {
        "local_cli": "smoke_local_cli",
        "live_session": "smoke_live_session",
        "remote_bridge": "smoke_remote_bridge",
    }
    group: dict[str, object] | None = None
    stopped_group: dict[str, object] = {}
    with _SmokeRemoteBridgeServer() as bridge:
        _write_official_round_smoke_meeting(output_root, meeting_id=meeting_id, agent_ids=agent_ids, role_ids=role_ids)
        seed_official_round_smoke_agents(
            server,
            meeting_id=meeting_id,
            agent_ids=agent_ids,
            request_json=request_json,
            bridge_endpoint=bridge["endpoint"],
        )

        with temp_dir_factory() as temp_dir:
            config_path = Path(temp_dir).resolve() / "live-agents.json"
            config = build_live_agent_official_round_smoke_config(
                server=server,
                meeting_id=meeting_id,
                agent_ids=agent_ids,
                python_executable=python_executable,
                bridge_endpoint=bridge["endpoint"],
                bridge_auth_ref=bridge["auth_ref"],
                timeout_seconds=float(timeout_seconds),
            )
            config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
            try:
                request_json(
                    _server_url(server, "/api/live-agent-processes/start"),
                    method="POST",
                    payload={
                        "config_path": str(config_path),
                        "server": server,
                        "group_id": clean_group_id,
                        "diagnostic": True,
                    },
                )
                round_result = request_json(
                    _server_url(
                        server,
                        f"/api/meetings/{urllib.parse.quote(meeting_id, safe='')}/live-agent-turns/round",
                    ),
                    method="POST",
                    payload={
                        "round_id": OFFICIAL_ROUND_SMOKE_ROUND_ID,
                        "timeout_seconds": float(timeout_seconds),
                        "stop_on_timeout": False,
                    },
                    timeout_seconds=_smoke_operation_http_timeout(float(timeout_seconds), windows=4),
                )
            finally:
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
                sleep_fn(0)

    return _safe_official_round_smoke_result(
        round_result,
        group_id=clean_group_id,
        meeting_id=meeting_id,
        agent_ids=list(agent_ids.values()),
        role_ids=list(role_ids.values()),
        stopped_group=stopped_group,
        timeout_seconds=float(timeout_seconds),
    )


def build_live_agent_official_round_smoke_config(
    *,
    server: str,
    meeting_id: str,
    agent_ids: dict[str, str],
    python_executable: str = sys.executable,
    bridge_endpoint: str,
    bridge_auth_ref: str,
    timeout_seconds: float = 12.0,
) -> dict[str, object]:
    config = build_live_agent_smoke_config(
        server=server,
        agent_ids=agent_ids,
        python_executable=python_executable,
        bridge_endpoint=bridge_endpoint,
        bridge_auth_ref=bridge_auth_ref,
    )
    config["max_ticks"] = _official_round_smoke_max_ticks(
        timeout_seconds,
        poll_interval=float(config.get("poll_interval") or 0.05),
    )
    for agent in config["agents"]:
        if not isinstance(agent, dict):
            continue
        agent["engagement_mode"] = "moderator_called"
        agent["meeting_id"] = meeting_id
    return config


def seed_official_round_smoke_agents(
    server: str,
    *,
    meeting_id: str,
    agent_ids: dict[str, str],
    request_json: RequestJson,
    bridge_endpoint: str = "",
) -> None:
    specs = [
        (agent_ids["local_cli"], "Smoke Local CLI", "local_cli", "local_cli"),
        (agent_ids["live_session"], "Smoke Live Session", "local_cli", "live_session"),
        (agent_ids["remote_bridge"], "Smoke Remote Bridge", "remote_http_bridge", "remote_bridge"),
    ]
    for agent_id, display_name, provider_kind, connection_kind in specs:
        request_json(
            _server_url(server, "/api/live-agents"),
            method="POST",
            payload={
                "agent_id": agent_id,
                "display_name": display_name,
                "provider_kind": provider_kind,
                "connection_kind": connection_kind,
                "session_id": "",
                "endpoint": bridge_endpoint if connection_kind == "remote_bridge" else "",
                "meeting_id": meeting_id,
                "engagement_mode": "moderator_called",
                "capabilities": ["room_chat", "mentions", "official_turns"],
                "diagnostic": True,
            },
        )
        request_json(
            _server_url(server, f"/api/live-agents/{urllib.parse.quote(agent_id, safe='')}/heartbeat"),
            method="POST",
            payload={"status": "online", "diagnostic": True},
        )


def seed_smoke_agent_cursors(
    server: str,
    *,
    agent_ids: dict[str, str],
    last_observed_event_id: str,
    request_json: RequestJson,
    bridge_endpoint: str = "",
) -> None:
    specs = [
        (agent_ids["local_cli"], "Smoke Local CLI", "local_cli", "local_cli"),
        (agent_ids["live_session"], "Smoke Live Session", "local_cli", "live_session"),
        (agent_ids["remote_bridge"], "Smoke Remote Bridge", "remote_http_bridge", "remote_bridge"),
    ]
    for agent_id, display_name, provider_kind, connection_kind in specs:
        request_json(
            _server_url(server, "/api/live-agents"),
            method="POST",
            payload={
                "agent_id": agent_id,
                "display_name": display_name,
                "provider_kind": provider_kind,
                "connection_kind": connection_kind,
                "session_id": "",
                "endpoint": bridge_endpoint if connection_kind == "remote_bridge" else "",
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
    if not cleaned:
        cleaned = f"smoke-{int(time.time() * 1000)}"
    return cleaned[:SMOKE_GROUP_ID_LIMIT].strip(".-") or f"smoke-{int(time.time() * 1000)}"


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
                and event.get("live_agent_endpoint") is True
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
                    "live_agent_endpoint": True,
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


def _write_official_round_smoke_meeting(
    output_root: Path,
    *,
    meeting_id: str,
    agent_ids: dict[str, str],
    role_ids: dict[str, str],
) -> None:
    meeting_dir = output_root / "meetings" / meeting_id
    meeting_dir.mkdir(parents=True, exist_ok=True)
    roles = [
        {
            "id": role_ids["local_cli"],
            "display_name": "Smoke Local CLI",
            "lens": "Credential-free local CLI official turn smoke.",
            "research_focus": "Verify official round dispatch through local_cli.",
        },
        {
            "id": role_ids["live_session"],
            "display_name": "Smoke Live Session",
            "lens": "Credential-free JSONL live session official turn smoke.",
            "research_focus": "Verify official round dispatch through live_session.",
        },
        {
            "id": role_ids["remote_bridge"],
            "display_name": "Smoke Remote Bridge",
            "lens": "Credential-free loopback remote bridge official turn smoke.",
            "research_focus": "Verify official round dispatch through remote_bridge.",
        },
    ]
    write_live_state(
        meeting_dir,
        {
            "meeting_id": meeting_id,
            "topic": "Official round live-agent smoke",
            "question": "Can credential-free resident agents answer a moderator-called official round?",
            "live_status": "running",
            "diagnostic": True,
            "diagnostic_kind": "official_round_smoke",
            "roles": roles,
            "agent_bindings": [
                {"role_id": role_ids["local_cli"], "agent_id": agent_ids["local_cli"]},
                {"role_id": role_ids["live_session"], "agent_id": agent_ids["live_session"]},
                {"role_id": role_ids["remote_bridge"], "agent_id": agent_ids["remote_bridge"]},
            ],
            "meeting_template": {
                "rounds": [
                    {
                        "id": OFFICIAL_ROUND_SMOKE_ROUND_ID,
                        "title": "Official Round Smoke",
                        "instruction": "Return one concise credential-free smoke reply.",
                        "turn_control": {"selection": "all_roles"},
                    }
                ]
            },
            "debate_rounds": [],
        },
    )


def _safe_official_round_smoke_result(
    round_result: dict[str, object],
    *,
    group_id: str,
    meeting_id: str,
    agent_ids: list[str],
    role_ids: list[str],
    stopped_group: dict[str, object],
    timeout_seconds: float,
) -> dict[str, object]:
    results = round_result.get("results") if isinstance(round_result.get("results"), list) else []
    request_event_ids = []
    reply_event_ids = []
    statuses = []
    for result in results:
        if not isinstance(result, dict):
            continue
        statuses.append(str(result.get("status") or "unknown"))
        request_event = result.get("request_event") if isinstance(result.get("request_event"), dict) else {}
        reply_event = result.get("reply_event") if isinstance(result.get("reply_event"), dict) else {}
        if request_event.get("id"):
            request_event_ids.append(str(request_event.get("id") or ""))
        if reply_event.get("id"):
            reply_event_ids.append(str(reply_event.get("id") or ""))
    stopped = stopped_group.get("status") == "stopped"
    return {
        "status": "ok" if round_result.get("status") == "answered" and stopped else "failed",
        "group_id": group_id,
        "meeting_id": meeting_id,
        "round_id": str(round_result.get("round_id") or OFFICIAL_ROUND_SMOKE_ROUND_ID),
        "agent_ids": agent_ids,
        "role_ids": role_ids,
        "turn_count": _nonnegative_int(round_result.get("turn_count")),
        "answered_count": _nonnegative_int(round_result.get("answered_count")),
        "timeout_count": _nonnegative_int(round_result.get("timeout_count")),
        "skipped_count": _nonnegative_int(round_result.get("skipped_count")),
        "stopped": stopped,
        "stopped_group_status": str(stopped_group.get("status") or ""),
        "timeout_seconds": max(0.0, float(timeout_seconds)),
        "statuses": statuses,
        "request_event_ids": request_event_ids,
        "reply_event_ids": reply_event_ids,
    }


def _nonnegative_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _official_round_smoke_max_ticks(timeout_seconds: float, *, poll_interval: float) -> int:
    interval = max(0.01, float(poll_interval))
    return math.ceil(_smoke_operation_http_timeout(timeout_seconds, windows=4) / interval) + 20


def _smoke_operation_http_timeout(wait_seconds: float, *, windows: int = 1) -> float:
    return max(10.0, float(wait_seconds) * max(1, int(windows)) + 6.0)


def _is_ascii_group_id_char(char: str) -> bool:
    return char in "_.-" or "0" <= char <= "9" or "A" <= char <= "Z" or "a" <= char <= "z"


class _SmokeRemoteBridgeServer:
    def __init__(self, token: str = SMOKE_BRIDGE_TOKEN) -> None:
        self.token = token
        self.server: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None

    def __enter__(self) -> dict[str, str]:
        token = self.token

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                if self.path != "/agentsassemble/run":
                    self.send_response(HTTPStatus.NOT_FOUND)
                    self.end_headers()
                    return
                if self.headers.get("Authorization") != f"Bearer {token}":
                    self.send_response(HTTPStatus.UNAUTHORIZED)
                    self.end_headers()
                    return
                length = int(self.headers.get("Content-Length", "0") or "0")
                if length:
                    self.rfile.read(length)
                body = json.dumps(
                    {
                        "text": json.dumps({"message": "smoke remote_bridge ok", "kind": "message"}),
                        "metadata": {"bridge": "smoke-remote-bridge"},
                    },
                    ensure_ascii=False,
                ).encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return {
            "endpoint": f"http://127.0.0.1:{self.server.server_port}",
            "auth_ref": f"literal:{self.token}",
        }

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
        if self.thread is not None:
            self.thread.join(timeout=1)


def _server_url(server: str, path: str) -> str:
    return f"{server.rstrip('/')}{path}"
