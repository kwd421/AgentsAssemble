from __future__ import annotations

import json
import math
import os
import signal
import sys
import tempfile
import threading
import time
import urllib.parse
from collections.abc import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from agentsassemble.live_session_transport import terminal_sessions_supported
from agentsassemble.meeting_events import write_live_state


RequestJson = Callable[..., dict[str, object]]
SMOKE_BRIDGE_TOKEN = "agentsassemble-smoke-token"
OFFICIAL_ROUND_SMOKE_ROUND_ID = "official_round_smoke"
SESSION_SMOKE_ROUND_ID = "session_smoke_round"
SMOKE_GROUP_ID_LIMIT = 48
MAX_SESSION_SMOKE_LOBBY_PROBES = 5
MAX_SESSION_SMOKE_SOAK_CYCLES = 5
MAX_SESSION_SMOKE_SOAK_INTERVAL_SECONDS = 60.0
SESSION_SMOKE_RECOVERABLE_PROCESS_STATUSES = frozenset({"error", "unknown"})
SESSION_SMOKE_TRANSPORT_ORDER = ("local_cli", "live_session", "terminal_session", "remote_bridge", "self_service")
SESSION_SMOKE_AGENT_SUFFIXES = {
    "local_cli": "local-cli",
    "live_session": "live-session",
    "terminal_session": "terminal-session",
    "remote_bridge": "remote-bridge",
    "self_service": "self-service",
}
SESSION_SMOKE_GROUP_ID_LIMIT = 64 - 1 - len(SESSION_SMOKE_AGENT_SUFFIXES["terminal_session"])
SESSION_SMOKE_ROLE_IDS = {
    "local_cli": "session_smoke_local_cli",
    "live_session": "session_smoke_live_session",
    "terminal_session": "session_smoke_terminal_session",
    "remote_bridge": "session_smoke_remote_bridge",
    "self_service": "session_smoke_self_service",
}
SESSION_SMOKE_DISPLAY_NAMES = {
    "local_cli": "Session Smoke Local CLI",
    "live_session": "Session Smoke Live Session",
    "terminal_session": "Session Smoke Terminal Session",
    "remote_bridge": "Session Smoke Remote Bridge",
    "self_service": "Session Smoke Self Service",
}
SESSION_SMOKE_PROVIDER_KINDS = {
    "local_cli": "local_cli",
    "live_session": "local_cli",
    "terminal_session": "local_cli",
    "remote_bridge": "remote_http_bridge",
    "self_service": "local_cli",
}
SESSION_SMOKE_MODEL_IDS = {
    "local_cli": "local-cli",
    "live_session": "local-cli",
    "terminal_session": "terminal-session",
    "remote_bridge": "remote-bridge",
    "self_service": "self-service",
}
SESSION_SMOKE_ROLE_TEXT = {
    "local_cli": {
        "lens": "Credential-free resident session smoke through a local CLI process.",
        "research_focus": "Verify resident session start, official turn, and lobby auto-reply through local_cli.",
    },
    "live_session": {
        "lens": "Credential-free resident session smoke through a persistent JSONL live session.",
        "research_focus": "Verify the resident live_session transport survives official and lobby turns.",
    },
    "terminal_session": {
        "lens": "Credential-free resident session smoke through a PTY-backed terminal session.",
        "research_focus": "Verify the terminal_session transport stays alive across official and lobby turns.",
    },
    "remote_bridge": {
        "lens": "Credential-free resident session smoke through a loopback remote bridge.",
        "research_focus": "Verify a remote_bridge resident participates without exposing bridge credentials.",
    },
    "self_service": {
        "lens": "Credential-free resident session smoke through a supervised self-service process.",
        "research_focus": "Verify self_service observes the room and replies without prompt injection.",
    },
}


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


def run_live_agent_session_smoke(
    *,
    server: str,
    group_id: str = "",
    meeting_id: str = "",
    timeout_seconds: float = 12.0,
    lobby_probe_count: int = 1,
    soak_cycle_count: int = 0,
    soak_interval_seconds: float = 0.0,
    request_json: RequestJson,
    output_root: Path | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    python_executable: str = sys.executable,
    temp_dir_factory: Callable[[], object] = tempfile.TemporaryDirectory,
    process_killer: Callable[[int], None] | None = None,
) -> dict[str, object]:
    clean_lobby_probe_count = _session_smoke_lobby_probe_count(lobby_probe_count)
    clean_soak_cycle_count = _session_smoke_soak_cycle_count(soak_cycle_count)
    clean_soak_interval_seconds = _session_smoke_soak_interval_seconds(soak_interval_seconds)
    clean_group_id = session_smoke_group_id(group_id) if group_id else session_smoke_group_id(f"session-smoke-{int(time.time() * 1000)}")
    clean_meeting_id = smoke_group_id(meeting_id) if meeting_id else smoke_group_id(f"session-{clean_group_id}")
    terminal_session_supported = _session_smoke_terminal_session_supported()
    transport_keys = _session_smoke_transport_keys(include_terminal_session=terminal_session_supported)
    agent_ids = _session_smoke_agent_ids(clean_group_id, transport_keys)
    role_ids = _session_smoke_role_ids(transport_keys)
    expected_messages = _session_smoke_expected_messages(agent_ids, transport_keys)
    start_result: dict[str, object] = {}
    rounds_result: dict[str, object] = {}
    check_result: dict[str, object] = {}
    resume_result: dict[str, object] = {}
    restart_result: dict[str, object] = {}
    recover_result: dict[str, object] = {}
    stop_result: dict[str, object] = {}
    replies: list[dict[str, object]] = []
    post_restart_replies: list[dict[str, object]] = []
    post_recover_replies: list[dict[str, object]] = []
    soak_replies: list[dict[str, object]] = []
    probe_event_ids: list[str] = []
    post_restart_probe_event_ids: list[str] = []
    post_recover_probe_event_ids: list[str] = []
    soak_probe_event_ids: list[str] = []
    soak_check_statuses: list[str] = []
    post_stop_process_status = ""
    with _SmokeRemoteBridgeServer(response_message=expected_messages[agent_ids["remote_bridge"]]) as bridge:
        with temp_dir_factory() as temp_dir:
            temp_root = Path(temp_dir).resolve()
            council_config_path = temp_root / "council.json"
            agent_config_path = temp_root / "agents.json"
            live_agent_config_path = temp_root / "live-agents.json"
            _write_session_smoke_configs(
                council_config_path=council_config_path,
                agent_config_path=agent_config_path,
                live_agent_config_path=live_agent_config_path,
                server=server,
                meeting_id=clean_meeting_id,
                agent_ids=agent_ids,
                role_ids=role_ids,
                expected_messages=expected_messages,
                python_executable=python_executable,
                bridge_endpoint=bridge["endpoint"],
                bridge_auth_ref=bridge["auth_ref"],
            )
            try:
                start_result = request_json(
                    _server_url(server, "/api/live-agent-sessions/start"),
                    method="POST",
                    payload={
                        "meeting_id": clean_meeting_id,
                        "group_id": clean_group_id,
                        "council_config_path": str(council_config_path),
                        "agent_config_path": str(agent_config_path),
                        "live_agent_config_path": str(live_agent_config_path),
                        "connect_timeout_seconds": float(timeout_seconds),
                        "diagnostic": True,
                    },
                    timeout_seconds=_smoke_operation_http_timeout(float(timeout_seconds)),
                )
                if start_result.get("status") != "ready":
                    raise LiveAgentSmokeFailed("Session smoke start did not become ready.")
                _mark_session_smoke_meeting_diagnostic(output_root, clean_meeting_id)
                rounds_result = request_json(
                    _server_url(
                        server,
                        f"/api/meetings/{urllib.parse.quote(clean_meeting_id, safe='')}/live-agent-turns/rounds",
                    ),
                    method="POST",
                    payload={
                        "max_rounds": 1,
                        "timeout_seconds": float(timeout_seconds),
                        "stop_on_timeout": False,
                    },
                    timeout_seconds=_smoke_operation_http_timeout(float(timeout_seconds), windows=4),
                )
                if rounds_result.get("status") != "answered" or _nonnegative_int(rounds_result.get("answered_round_count")) < 1:
                    raise LiveAgentSmokeFailed("Session smoke official round did not answer.")
                _set_session_smoke_engagement(server, agent_ids.values(), request_json=request_json)
                probe_event_ids, replies = _session_smoke_lobby_probe_replies(
                    server,
                    clean_group_id=clean_group_id,
                    expected_messages=expected_messages,
                    lobby_probe_count=clean_lobby_probe_count,
                    request_json=request_json,
                    sleep_fn=sleep_fn,
                    timeout_seconds=float(timeout_seconds),
                    phase="",
                )
                check_result = request_json(
                    _server_url(server, "/api/live-agent-sessions/check"),
                    method="POST",
                    payload={"meeting_id": clean_meeting_id, "group_id": clean_group_id},
                    timeout_seconds=10.0,
                )
                if check_result.get("status") != "ready":
                    raise LiveAgentSmokeFailed("Session smoke check did not report ready.")
                resume_result = request_json(
                    _server_url(server, "/api/live-agent-sessions/resume"),
                    method="POST",
                    payload={
                        "meeting_id": clean_meeting_id,
                        "group_id": clean_group_id,
                        "live_agent_config_path": str(live_agent_config_path),
                        "connect_timeout_seconds": float(timeout_seconds),
                    },
                    timeout_seconds=_smoke_operation_http_timeout(float(timeout_seconds)),
                )
                if resume_result.get("status") != "ready":
                    raise LiveAgentSmokeFailed("Session smoke resume did not report ready.")
                restart_result = request_json(
                    _server_url(server, "/api/live-agent-sessions/restart"),
                    method="POST",
                    payload={
                        "meeting_id": clean_meeting_id,
                        "group_id": clean_group_id,
                        "connect_timeout_seconds": float(timeout_seconds),
                    },
                    timeout_seconds=_smoke_operation_http_timeout(float(timeout_seconds)),
                )
                if restart_result.get("status") != "ready":
                    raise LiveAgentSmokeFailed("Session smoke restart did not become ready.")
                post_restart_probe_event_ids, post_restart_replies = _session_smoke_lobby_probe_replies(
                    server,
                    clean_group_id=clean_group_id,
                    expected_messages=expected_messages,
                    lobby_probe_count=clean_lobby_probe_count,
                    request_json=request_json,
                    sleep_fn=sleep_fn,
                    timeout_seconds=float(timeout_seconds),
                    phase="restart",
                )
                _make_session_smoke_group_recoverable(
                    server,
                    clean_group_id,
                    meeting_id=clean_meeting_id,
                    expected_agent_ids=list(agent_ids.values()),
                    request_json=request_json,
                    sleep_fn=sleep_fn,
                    timeout_seconds=float(timeout_seconds),
                    process_killer=process_killer or _kill_session_smoke_process_group,
                )
                recover_result = request_json(
                    _server_url(server, "/api/live-agent-sessions/recover"),
                    method="POST",
                    payload={
                        "meeting_id": clean_meeting_id,
                        "group_id": clean_group_id,
                        "connect_timeout_seconds": float(timeout_seconds),
                    },
                    timeout_seconds=_smoke_operation_http_timeout(float(timeout_seconds)),
                )
                if recover_result.get("status") != "ready":
                    raise LiveAgentSmokeFailed("Session smoke recover did not become ready.")
                _set_session_smoke_engagement(server, agent_ids.values(), request_json=request_json)
                post_recover_probe_event_ids, post_recover_replies = _session_smoke_lobby_probe_replies(
                    server,
                    clean_group_id=clean_group_id,
                    expected_messages=expected_messages,
                    lobby_probe_count=clean_lobby_probe_count,
                    request_json=request_json,
                    sleep_fn=sleep_fn,
                    timeout_seconds=float(timeout_seconds),
                    phase="recover",
                )
                for cycle_index in range(clean_soak_cycle_count):
                    if clean_soak_interval_seconds:
                        sleep_fn(clean_soak_interval_seconds)
                    soak_check_result = request_json(
                        _server_url(server, "/api/live-agent-sessions/check"),
                        method="POST",
                        payload={"meeting_id": clean_meeting_id, "group_id": clean_group_id},
                        timeout_seconds=10.0,
                    )
                    soak_check_status = str(soak_check_result.get("status") or "")
                    soak_check_statuses.append(soak_check_status)
                    if soak_check_status != "ready":
                        raise LiveAgentSmokeFailed("Session smoke soak check did not report ready.")
                    _set_session_smoke_engagement(server, agent_ids.values(), request_json=request_json)
                    cycle_probe_event_ids, cycle_replies = _session_smoke_lobby_probe_replies(
                        server,
                        clean_group_id=clean_group_id,
                        expected_messages=expected_messages,
                        lobby_probe_count=1,
                        request_json=request_json,
                        sleep_fn=sleep_fn,
                        timeout_seconds=float(timeout_seconds),
                        phase=f"soak-{cycle_index + 1}",
                    )
                    soak_probe_event_ids.extend(cycle_probe_event_ids)
                    soak_replies.extend(cycle_replies)
            finally:
                try:
                    stop_result = request_json(
                        _server_url(server, "/api/live-agent-sessions/stop"),
                        method="POST",
                        payload={"meeting_id": clean_meeting_id, "group_id": clean_group_id},
                        timeout_seconds=20.0,
                    )
                except Exception:
                    if start_result:
                        raise
                else:
                    post_stop_process_status = _ensure_session_smoke_process_group_stopped(
                        server,
                        clean_group_id,
                        request_json=request_json,
                        sleep_fn=sleep_fn,
                        timeout_seconds=min(2.0, max(0.1, float(timeout_seconds))),
                    )

    safe_replies = _safe_session_smoke_replies(replies)
    safe_post_restart_replies = _safe_session_smoke_replies(post_restart_replies)
    safe_post_recover_replies = _safe_session_smoke_replies(post_recover_replies)
    safe_soak_replies = _safe_session_smoke_replies(soak_replies)
    return {
        "status": "ok" if stop_result.get("status") == "stopped" else "failed",
        "meeting_id": clean_meeting_id,
        "group_id": clean_group_id,
        "agent_ids": list(agent_ids.values()),
        "terminal_session_supported": terminal_session_supported,
        "terminal_session_included": "terminal_session" in agent_ids,
        "terminal_session_status": "covered" if "terminal_session" in agent_ids else "skipped",
        "terminal_session_reason": "" if "terminal_session" in agent_ids else "pty_unavailable",
        "lobby_probe_count": clean_lobby_probe_count,
        "source_event_id": probe_event_ids[0] if probe_event_ids else "",
        "source_event_ids": probe_event_ids,
        "post_restart_source_event_id": post_restart_probe_event_ids[0] if post_restart_probe_event_ids else "",
        "post_restart_source_event_ids": post_restart_probe_event_ids,
        "post_recover_source_event_id": post_recover_probe_event_ids[0] if post_recover_probe_event_ids else "",
        "post_recover_source_event_ids": post_recover_probe_event_ids,
        "soak_cycle_count": clean_soak_cycle_count,
        "soak_interval_seconds": clean_soak_interval_seconds,
        "soak_source_event_ids": soak_probe_event_ids,
        "soak_check_statuses": soak_check_statuses,
        "rounds_status": str(rounds_result.get("status") or ""),
        "round_count": _nonnegative_int(rounds_result.get("round_count")),
        "answered_round_count": _nonnegative_int(rounds_result.get("answered_round_count")),
        "completed_round_count": _nonnegative_int(rounds_result.get("completed_round_count")),
        "timeout_round_count": _nonnegative_int(rounds_result.get("timeout_round_count")),
        "skipped_round_count": _nonnegative_int(rounds_result.get("skipped_round_count")),
        "stopped_round_count": _nonnegative_int(rounds_result.get("stopped_round_count")),
        "expected_reply_count": len(expected_messages),
        "reply_count": len(safe_replies),
        "post_restart_reply_count": len(safe_post_restart_replies),
        "post_recover_reply_count": len(safe_post_recover_replies),
        "soak_reply_count": len(safe_soak_replies),
        "replies": safe_replies,
        "post_restart_replies": safe_post_restart_replies,
        "post_recover_replies": safe_post_recover_replies,
        "soak_replies": safe_soak_replies,
        "start_status": str(start_result.get("status") or ""),
        "check_status": str(check_result.get("status") or ""),
        "resume_status": str(resume_result.get("status") or ""),
        "restart_status": str(restart_result.get("status") or ""),
        "recover_status": str(recover_result.get("status") or ""),
        "stop_status": str(stop_result.get("status") or ""),
        "post_stop_process_status": post_stop_process_status,
    }


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


def _session_smoke_terminal_session_supported() -> bool:
    return terminal_sessions_supported()


def _session_smoke_transport_keys(*, include_terminal_session: bool) -> list[str]:
    return [
        key
        for key in SESSION_SMOKE_TRANSPORT_ORDER
        if include_terminal_session or key != "terminal_session"
    ]


def _session_smoke_agent_ids(clean_group_id: str, transport_keys: list[str]) -> dict[str, str]:
    return {key: f"{clean_group_id}-{SESSION_SMOKE_AGENT_SUFFIXES[key]}" for key in transport_keys}


def _session_smoke_role_ids(transport_keys: list[str]) -> dict[str, str]:
    return {key: SESSION_SMOKE_ROLE_IDS[key] for key in transport_keys}


def _session_smoke_expected_messages(agent_ids: dict[str, str], transport_keys: list[str]) -> dict[str, str]:
    return {agent_ids[key]: f"session smoke {key} ok" for key in transport_keys}


def _session_smoke_role_config(key: str, role_id: str) -> dict[str, str]:
    text = SESSION_SMOKE_ROLE_TEXT[key]
    return {
        "id": role_id,
        "display_name": SESSION_SMOKE_DISPLAY_NAMES[key],
        "lens": text["lens"],
        "research_focus": text["research_focus"],
    }


def _session_smoke_agent_binding(key: str, agent_id: str, role_id: str) -> dict[str, str]:
    return {
        "agent_id": agent_id,
        "role_id": role_id,
        "owner_id": "session-smoke",
        "provider_id": "session-smoke-bridge" if key == "remote_bridge" else "session-smoke-local",
        "model_id": SESSION_SMOKE_MODEL_IDS[key],
        "permission_profile_id": "meeting_readonly",
        "join_mode": "fresh",
    }


def _write_session_smoke_configs(
    *,
    council_config_path: Path,
    agent_config_path: Path,
    live_agent_config_path: Path,
    server: str,
    meeting_id: str,
    agent_ids: dict[str, str],
    role_ids: dict[str, str],
    expected_messages: dict[str, str],
    python_executable: str,
    bridge_endpoint: str,
    bridge_auth_ref: str,
) -> None:
    transport_keys = [key for key in SESSION_SMOKE_TRANSPORT_ORDER if key in agent_ids]
    council_config_path.parent.mkdir(parents=True, exist_ok=True)
    council_config_path.write_text(
        json.dumps(
            {
                "topic": "Resident session smoke",
                "question": "Can credential-free resident agents start, auto-reply, resume, restart, and stop?",
                "roles": [_session_smoke_role_config(key, role_ids[key]) for key in transport_keys],
                "meeting_template": {
                    "id": "session_smoke",
                    "display_name": "Session Smoke",
                    "rounds": [
                        {
                            "id": SESSION_SMOKE_ROUND_ID,
                            "title": "Session Smoke Round",
                            "instruction": "Return one concise credential-free smoke reply.",
                            "turn_control": {"selection": "all_roles"},
                        }
                    ],
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    agent_config_path.write_text(
        json.dumps(
            {
                "providers": [
                    {
                        "id": "session-smoke-local",
                        "kind": "local_cli",
                        "display_name": "Session Smoke Local CLI",
                        "command": [python_executable, "-c", "import sys; sys.stdin.read(); print('session smoke provider ready')"],
                    },
                    {
                        "id": "session-smoke-bridge",
                        "kind": "remote_http_bridge",
                        "display_name": "Session Smoke Remote Bridge",
                        "endpoint": bridge_endpoint,
                        "auth_ref": bridge_auth_ref,
                    }
                ],
                "permission_profiles": [
                    {
                        "id": "meeting_readonly",
                        "meeting_read": True,
                        "lobby_chat": True,
                        "official_turn": True,
                        "web_search": False,
                        "tool_use": False,
                        "filesystem_read": False,
                        "filesystem_write": False,
                        "git_write": False,
                        "push": False,
                        "secrets": False,
                        "implementation": False,
                    }
                ],
                "agent_bindings": [
                    _session_smoke_agent_binding(key, agent_ids[key], role_ids[key]) for key in transport_keys
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    live_agent_config_path.write_text(
        json.dumps(
            {
                "server": server,
                "poll_interval": 0.05,
                "heartbeat_interval": 0,
                "cooldown": 0,
                "max_chain_depth": 0,
                "agents": [
                    _session_smoke_agent_config(
                        agent_id=agent_ids[key],
                        display_name=SESSION_SMOKE_DISPLAY_NAMES[key],
                        provider_kind=SESSION_SMOKE_PROVIDER_KINDS[key],
                        connection_kind=key,
                        meeting_id=meeting_id,
                        message=expected_messages[agent_ids[key]],
                        python_executable=python_executable,
                        bridge_endpoint=bridge_endpoint,
                        bridge_auth_ref=bridge_auth_ref,
                    )
                    for key in transport_keys
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _mark_session_smoke_meeting_diagnostic(output_root: Path | None, meeting_id: str) -> None:
    if output_root is None:
        return
    live_state_path = output_root / "meetings" / meeting_id / "live_state.json"
    if not live_state_path.exists():
        return
    try:
        data = json.loads(live_state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return
    if not isinstance(data, dict):
        return
    data["diagnostic"] = True
    data["diagnostic_kind"] = "session_smoke"
    write_live_state(live_state_path.parent, data)


def _session_smoke_agent_config(
    *,
    agent_id: str,
    display_name: str,
    provider_kind: str,
    connection_kind: str,
    meeting_id: str,
    message: str,
    python_executable: str,
    bridge_endpoint: str = "",
    bridge_auth_ref: str = "",
) -> dict[str, object]:
    if connection_kind == "remote_bridge":
        return {
            "agent_id": agent_id,
            "display_name": display_name,
            "provider_kind": provider_kind,
            "connection_kind": "remote_bridge",
            "meeting_id": meeting_id,
            "engagement_mode": "moderator_called",
            "endpoint": bridge_endpoint,
            "auth_ref": bridge_auth_ref,
            "timeout_seconds": 5,
        }
    if connection_kind == "live_session":
        script = "\n".join(
            [
                "import json, sys",
                "for line in sys.stdin:",
                "    payload = json.loads(line)",
                f"    print(json.dumps({{'request_id': payload['request_id'], 'message': {message!r}}}), flush=True)",
            ]
        )
        command = [python_executable, "-u", "-c", script]
    elif connection_kind == "terminal_session":
        script = "\n".join(
            [
                "import sys",
                "for line in sys.stdin:",
                f"    print({message!r}, flush=True)",
            ]
        )
        command = [python_executable, "-u", "-c", script]
    elif connection_kind == "self_service":
        command = [python_executable, "-u", "-c", _session_smoke_self_service_script(message)]
    else:
        script = f"import sys; sys.stdin.read(); print({message!r})"
        command = [python_executable, "-c", script]
    config: dict[str, object] = {
        "agent_id": agent_id,
        "display_name": display_name,
        "provider_kind": provider_kind,
        "connection_kind": connection_kind,
        "meeting_id": meeting_id,
        "engagement_mode": "moderator_called",
        "command": command,
        "timeout_seconds": 5,
    }
    if connection_kind == "terminal_session":
        config["terminal_idle_timeout"] = 0.1
    return config


def _session_smoke_self_service_script(message: str) -> str:
    return "\n".join(
        [
            "import json, os, shlex, subprocess, time",
            "MEETING_ID = os.environ.get('AGENTSASSEMBLE_MEETING_ID', '')",
            "MESSAGE = " + repr(message),
            "POLL = max(float(os.environ.get('AGENTSASSEMBLE_POLL_INTERVAL') or 0.05), 0.05)",
            "",
            "def command_from_env(name):",
            "    return shlex.split(os.environ[name])",
            "",
            "def replace_tokens(command, replacements):",
            "    replaced = []",
            "    for part in command:",
            "        for token, value in replacements.items():",
            "            part = part.replace(token, value)",
            "        replaced.append(part)",
            "    return replaced",
            "",
            "def insert_before_message_separator(command, *args):",
            "    marker = command.index('--') if '--' in command else len(command)",
            "    return command[:marker] + list(args) + command[marker:]",
            "",
            "def cli(command, timeout=5):",
            "    return subprocess.run(command, capture_output=True, text=True, timeout=timeout)",
            "",
            "def heartbeat(status, last_error='', last_reply_at='', last_observed_event_id='', last_observed_live_event_id=''):",
            "    try:",
            "        heartbeat_command = replace_tokens(",
            "            command_from_env('AGENTSASSEMBLE_HEARTBEAT_COMMAND_TEMPLATE'),",
            "            {",
            "                '{status}': status,",
            "                '{last_error}': last_error,",
            "                '{last_reply_at}': last_reply_at,",
            "                '{last_observed_event_id}': last_observed_event_id,",
            "                '{last_observed_live_event_id}': last_observed_live_event_id,",
            "            },",
            "        )",
            "        cli(heartbeat_command, timeout=3)",
            "    except Exception:",
            "        return",
            "",
            "def current_engagement_mode():",
            "    room = cli(command_from_env('AGENTSASSEMBLE_ROOM_COMMAND'), timeout=3)",
            "    if room.returncode != 0:",
            "        return ''",
            "    try:",
            "        payload = json.loads(room.stdout or '{}')",
            "    except json.JSONDecodeError:",
            "        return ''",
            "    agent = payload.get('agent') if isinstance(payload.get('agent'), dict) else {}",
            "    return str(agent.get('engagement_mode') or '')",
            "",
            "while True:",
            "    wait_command = command_from_env('AGENTSASSEMBLE_WAIT_NEXT_COMMAND') + ['--timeout', '1']",
            "    wait = cli(wait_command, timeout=3)",
            "    if wait.returncode != 0:",
            "        time.sleep(POLL)",
            "        continue",
            "    try:",
            "        payload = json.loads(wait.stdout or '{}')",
            "    except json.JSONDecodeError:",
            "        time.sleep(POLL)",
            "        continue",
            "    if payload.get('status') != 'event':",
            "        time.sleep(POLL)",
            "        continue",
            "    action = payload.get('action')",
            "    source_event_id = str(payload.get('source_event_id') or '')",
            "    if action == 'official_turn' and source_event_id:",
            "        meeting_id = str(payload.get('meeting_id') or MEETING_ID)",
            "        if meeting_id:",
            "            heartbeat('working', last_observed_live_event_id=source_event_id)",
            "            reply_command = replace_tokens(",
            "                command_from_env('AGENTSASSEMBLE_OFFICIAL_REPLY_COMMAND_TEMPLATE'),",
            "                {'{meeting_id}': meeting_id, '{source_event_id}': source_event_id, '{message}': MESSAGE},",
            "            )",
            "            reply = cli(insert_before_message_separator(reply_command, '--json'))",
            "            if reply.returncode == 0:",
            "                heartbeat('online', last_error='', last_reply_at=str(time.time()), last_observed_live_event_id=source_event_id)",
            "            else:",
            "                heartbeat('error', last_error='official reply failed', last_observed_live_event_id=source_event_id)",
            "    elif action == 'lobby' and source_event_id and current_engagement_mode() == 'always':",
            "        auto_chain_depth = str(payload.get('auto_chain_depth') or '1')",
            "        heartbeat('working', last_observed_event_id=source_event_id)",
            "        say_command = replace_tokens(",
            "            command_from_env('AGENTSASSEMBLE_SAY_COMMAND_TEMPLATE'),",
            "            {'{source_event_id}': source_event_id, '{auto_chain_depth}': auto_chain_depth, '{message}': MESSAGE},",
            "        )",
            "        reply = cli(insert_before_message_separator(say_command, '--json'))",
            "        if reply.returncode == 0:",
            "            heartbeat('online', last_error='', last_reply_at=str(time.time()), last_observed_event_id=source_event_id)",
            "        else:",
            "            heartbeat('error', last_error='lobby reply failed', last_observed_event_id=source_event_id)",
            "    time.sleep(POLL)",
        ]
    )


def _set_session_smoke_engagement(
    server: str,
    agent_ids: object,
    *,
    request_json: RequestJson,
) -> None:
    for agent_id in agent_ids:
        request_json(
            _server_url(server, f"/api/live-agents/{urllib.parse.quote(str(agent_id), safe='')}/engagement"),
            method="POST",
            payload={"engagement_mode": "always"},
        )


def _session_smoke_lobby_probe_count(value: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(
            f"Session smoke lobby_probe_count must be between 1 and {MAX_SESSION_SMOKE_LOBBY_PROBES}."
        ) from error
    if parsed < 1 or parsed > MAX_SESSION_SMOKE_LOBBY_PROBES:
        raise ValueError(f"Session smoke lobby_probe_count must be between 1 and {MAX_SESSION_SMOKE_LOBBY_PROBES}.")
    return parsed


def _session_smoke_soak_cycle_count(value: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(
            f"Session smoke soak_cycle_count must be between 0 and {MAX_SESSION_SMOKE_SOAK_CYCLES}."
        ) from error
    if parsed < 0 or parsed > MAX_SESSION_SMOKE_SOAK_CYCLES:
        raise ValueError(f"Session smoke soak_cycle_count must be between 0 and {MAX_SESSION_SMOKE_SOAK_CYCLES}.")
    return parsed


def _session_smoke_soak_interval_seconds(value: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(
            f"Session smoke soak_interval_seconds must be between 0 and {MAX_SESSION_SMOKE_SOAK_INTERVAL_SECONDS}."
        ) from error
    if not math.isfinite(parsed) or parsed < 0 or parsed > MAX_SESSION_SMOKE_SOAK_INTERVAL_SECONDS:
        raise ValueError(
            f"Session smoke soak_interval_seconds must be between 0 and {MAX_SESSION_SMOKE_SOAK_INTERVAL_SECONDS}."
        )
    return parsed


def _session_smoke_lobby_probe_replies(
    server: str,
    *,
    clean_group_id: str,
    expected_messages: dict[str, str],
    lobby_probe_count: int,
    request_json: RequestJson,
    sleep_fn: Callable[[float], None],
    timeout_seconds: float,
    phase: str,
) -> tuple[list[str], list[dict[str, object]]]:
    probe_event_ids = []
    replies = []
    phase_label = f" {phase}" if phase else ""
    for index in range(lobby_probe_count):
        probe_response = request_json(
            _server_url(server, "/api/lobby"),
            method="POST",
            payload={
                "name": "AgentsAssemble Session Smoke",
                "side": "mine",
                "message": (
                    f"live-agent session-smoke{phase_label} "
                    f"{clean_group_id} {index + 1}/{lobby_probe_count} {int(time.time() * 1000)}"
                ),
            },
        )
        probe_event_id = _event_id(probe_response)
        probe_event_ids.append(probe_event_id)
        replies.extend(
            wait_for_smoke_replies(
                server,
                expected_messages=expected_messages,
                source_event_id=probe_event_id,
                request_json=request_json,
                sleep_fn=sleep_fn,
                timeout_seconds=float(timeout_seconds),
            )
        )
    return probe_event_ids, replies


def _make_session_smoke_group_recoverable(
    server: str,
    group_id: str,
    *,
    meeting_id: str,
    expected_agent_ids: list[str],
    request_json: RequestJson,
    sleep_fn: Callable[[float], None],
    timeout_seconds: float,
    process_killer: Callable[[int], None],
) -> dict[str, object]:
    payload = request_json(_server_url(server, "/api/live-agent-processes"))
    group = find_process_group(payload, group_id)
    if group is None:
        raise LiveAgentSmokeFailed("Session smoke process group was not found before recover.")
    _validate_session_smoke_recoverable_group(group, meeting_id=meeting_id, expected_agent_ids=expected_agent_ids)
    status = str(group.get("status") or "unknown")
    if status in SESSION_SMOKE_RECOVERABLE_PROCESS_STATUSES:
        return group
    if status not in {"running", "restarting"}:
        raise LiveAgentSmokeFailed(f"Session smoke process group is {status}; cannot verify recover-session.")
    pid = group.get("pid")
    if not isinstance(pid, int) or pid <= 0:
        raise LiveAgentSmokeFailed("Session smoke process group has no pid to make recoverable.")
    process_killer(pid)
    return _wait_for_session_smoke_recoverable_group(
        server,
        group_id,
        meeting_id=meeting_id,
        expected_agent_ids=expected_agent_ids,
        request_json=request_json,
        sleep_fn=sleep_fn,
        timeout_seconds=timeout_seconds,
    )


def _ensure_session_smoke_process_group_stopped(
    server: str,
    group_id: str,
    *,
    request_json: RequestJson,
    sleep_fn: Callable[[float], None],
    timeout_seconds: float,
) -> str:
    payload = wait_for_smoke_group_to_settle(
        server,
        group_id,
        request_json=request_json,
        sleep_fn=sleep_fn,
        timeout_seconds=timeout_seconds,
    )
    group = find_process_group(payload, group_id)
    if group is None:
        return "missing"
    status = str(group.get("status") or "unknown")
    if status in {"running", "restarting"}:
        raise LiveAgentSmokeFailed(f"Session smoke did not stop process group; status {status}.")
    return status


def _wait_for_session_smoke_recoverable_group(
    server: str,
    group_id: str,
    *,
    meeting_id: str,
    expected_agent_ids: list[str],
    request_json: RequestJson,
    sleep_fn: Callable[[float], None],
    timeout_seconds: float,
) -> dict[str, object]:
    deadline = time.monotonic() + timeout_seconds
    last_status = "missing"
    while time.monotonic() < deadline:
        payload = request_json(_server_url(server, "/api/live-agent-processes"))
        group = find_process_group(payload, group_id)
        if group is None:
            last_status = "missing"
        else:
            _validate_session_smoke_recoverable_group(group, meeting_id=meeting_id, expected_agent_ids=expected_agent_ids)
            last_status = str(group.get("status") or "unknown")
            if last_status in SESSION_SMOKE_RECOVERABLE_PROCESS_STATUSES:
                return group
        sleep_fn(0.05)
    raise LiveAgentSmokeFailed(f"Session smoke process group did not become recoverable; last status {last_status}.")


def _validate_session_smoke_recoverable_group(
    group: dict[str, object],
    *,
    meeting_id: str,
    expected_agent_ids: list[str],
) -> None:
    if group.get("diagnostic") is not True:
        raise LiveAgentSmokeFailed("Session smoke process group is not diagnostic; refusing recover-session smoke.")
    if str(group.get("meeting_id") or "") != meeting_id:
        raise LiveAgentSmokeFailed("Session smoke process group belongs to a different meeting; refusing recover-session smoke.")
    actual_agent_ids = _session_smoke_group_agent_ids(group.get("agents"))
    expected = sorted(str(agent_id) for agent_id in expected_agent_ids)
    if sorted(actual_agent_ids) != expected or len(actual_agent_ids) != len(set(actual_agent_ids)):
        raise LiveAgentSmokeFailed("Session smoke process group manifest does not match expected smoke agents.")


def _session_smoke_group_agent_ids(agents: object) -> list[str]:
    if not isinstance(agents, list):
        return []
    return [
        str(agent.get("agent_id") or "")
        for agent in agents
        if isinstance(agent, dict) and str(agent.get("agent_id") or "")
    ]


def _kill_session_smoke_process_group(pid: int) -> None:
    kill_signal = _session_smoke_kill_signal(signal)
    try:
        os.killpg(pid, kill_signal)
        return
    except AttributeError:
        pass
    except ProcessLookupError:
        pass
    except PermissionError as error:
        raise LiveAgentSmokeFailed("Session smoke could not stop process group for recover.") from error
    try:
        os.kill(pid, kill_signal)
    except ProcessLookupError:
        return
    except PermissionError as error:
        raise LiveAgentSmokeFailed("Session smoke could not stop process for recover.") from error


def _session_smoke_kill_signal(signal_module: object) -> int:
    sigterm = getattr(signal_module, "SIGTERM")
    return int(getattr(signal_module, "SIGKILL", sigterm))


def _safe_session_smoke_replies(replies: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        {
            "id": str(reply.get("id") or ""),
            "actor_id": str(reply.get("actor_id") or ""),
            "source_event_id": str(reply.get("source_event_id") or ""),
            "live_agent_endpoint": reply.get("live_agent_endpoint") is True,
        }
        for reply in replies
    ]


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


def session_smoke_group_id(value: object) -> str:
    cleaned = smoke_group_id(value)
    bounded = cleaned[:SESSION_SMOKE_GROUP_ID_LIMIT].strip(".-")
    if bounded:
        return bounded
    fallback = f"session-smoke-{int(time.time() * 1000)}"
    return fallback[:SESSION_SMOKE_GROUP_ID_LIMIT].strip(".-")


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
    def __init__(self, token: str = SMOKE_BRIDGE_TOKEN, response_message: str = "smoke remote_bridge ok") -> None:
        self.token = token
        self.response_message = response_message
        self.server: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None

    def __enter__(self) -> dict[str, str]:
        token = self.token
        response_message = self.response_message

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
                        "text": json.dumps({"message": response_message, "kind": "message"}),
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
