from __future__ import annotations

import json
import mimetypes
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from agentsassemble.adapters.remote_bridge import RemoteBridgeAdapter
from agentsassemble.codex_sessions import (
    build_codex_live_invite_config,
    list_codex_sessions,
    read_agent_config,
    write_agent_config,
)
from agentsassemble.config import load_agent_runtime_config, load_council_config, providers_from_config
from agentsassemble.live_agents import connect_live_agent, heartbeat_live_agent, read_live_agents
from agentsassemble.live_agent_processes import LiveAgentProcessSupervisor
from agentsassemble.meeting import run_demo_meeting
from agentsassemble.meeting_events import (
    append_lobby_event_to_file,
    append_side_chat_event_to_file,
    read_live_events,
    read_live_events_after,
    read_lobby_events,
    read_lobby_events_after,
    read_side_chat_events,
    read_side_chat_events_after,
)
from agentsassemble.adapters import default_provider_registry
from agentsassemble.models import ProviderConfig, Role

TAB_LABELS = {"lobby": "로비", "live": "실황", "board": "작전판", "archive": "아카이브"}
TABS = ["lobby", "live", "board", "archive"]
STALE_RUNNING_SECONDS = 300
REMOTE_LOBBY_REQUESTER = None


def list_meetings(output_root: Path, now: float | None = None) -> list[dict[str, object]]:
    meetings_dir = output_root / "meetings"
    if not meetings_dir.exists():
        return []

    meetings = []
    for meeting_dir in meetings_dir.iterdir():
        record_path = meeting_dir / "meeting.json"
        live_path = meeting_dir / "live_state.json"
        if not record_path.exists() and not live_path.exists():
            continue
        try:
            meeting, source_path, has_final_record = _load_meeting_record(meeting_dir)
        except json.JSONDecodeError:
            continue
        meeting = _with_inferred_live_status(
            meeting,
            meeting_dir,
            has_final_record=has_final_record,
            now=now,
        )
        stat = source_path.stat()
        meetings.append(
            {
                "meeting_id": meeting.get("meeting_id", meeting_dir.name),
                "topic": meeting.get("topic", ""),
                "question": meeting.get("question", ""),
                "created_at": meeting.get("audit_metadata", {}).get("created_at", ""),
                "live_status": meeting.get("live_status", "complete" if record_path.exists() else "unknown"),
                "path": str(meeting_dir),
                "mtime": stat.st_mtime,
            }
        )
    return sorted(meetings, key=lambda item: item["mtime"], reverse=True)


def build_meeting_payload(meeting_dir: Path, now: float | None = None) -> dict[str, object]:
    meeting, _, has_final_record = _load_meeting_record(meeting_dir)
    meeting = _with_inferred_live_status(
        meeting,
        meeting_dir,
        has_final_record=has_final_record,
        now=now,
    )
    artifacts = {
        name: _read_optional(meeting_dir / name)
        for name in ("agenda.md", "transcript.md", "decision.md", "room-log.md", "meeting.json")
    }
    tasks = {
        task_path.name: task_path.read_text(encoding="utf-8")
        for task_path in sorted((meeting_dir / "tasks").glob("*.md"))
    }
    return_packets = {
        packet_path.name: packet_path.read_text(encoding="utf-8")
        for packet_path in sorted((meeting_dir / "return_packets").glob("*.md"))
    }
    research = {}
    research_json = {}
    research_root = meeting_dir / "private_research"
    if research_root.exists():
        for research_path in sorted(research_root.glob("*/research.md")):
            research[f"{research_path.parent.name}/research.md"] = research_path.read_text(encoding="utf-8")
        for research_path in sorted(research_root.glob("*/research.json")):
            try:
                research_json[research_path.parent.name] = json.loads(research_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                research_json[research_path.parent.name] = {"error": "Research JSON could not be parsed."}
    return {
        "tabs": TABS,
        "tab_labels": TAB_LABELS,
        "meeting": meeting,
        "artifacts": artifacts,
        "tasks": tasks,
        "return_packets": return_packets,
        "research": research,
        "research_json": research_json,
        "live_events": read_live_events(meeting_dir),
    }


def _load_meeting_record(meeting_dir: Path) -> tuple[dict[str, object], Path, bool]:
    meeting_path = meeting_dir / "meeting.json"
    live_path = meeting_dir / "live_state.json"
    if meeting_path.exists():
        try:
            return json.loads(meeting_path.read_text(encoding="utf-8")), meeting_path, True
        except json.JSONDecodeError:
            if not live_path.exists():
                raise
    return json.loads(live_path.read_text(encoding="utf-8")), live_path, False


def _with_inferred_live_status(
    meeting: dict[str, object],
    meeting_dir: Path,
    has_final_record: bool,
    now: float | None = None,
) -> dict[str, object]:
    if has_final_record or meeting.get("live_status") != "running":
        return meeting
    latest_mtime = _latest_live_mtime(meeting_dir)
    if latest_mtime is None:
        return meeting
    if (now if now is not None else time.time()) - latest_mtime < STALE_RUNNING_SECONDS:
        return meeting
    inferred = dict(meeting)
    inferred["live_status"] = "stalled"
    inferred["stalled_reason"] = "No live meeting update has been observed recently."
    inferred["last_live_update_mtime"] = latest_mtime
    return inferred


def _latest_live_mtime(meeting_dir: Path) -> float | None:
    mtimes = [
        path.stat().st_mtime
        for path in (meeting_dir / "live_state.json", meeting_dir / "live_events.jsonl")
        if path.exists()
    ]
    return max(mtimes) if mtimes else None


def serve_gui(host: str = "127.0.0.1", port: int = 8765, output_root: Path | None = None) -> None:
    root = output_root or Path(".agentsassemble")
    process_supervisor = LiveAgentProcessSupervisor(root)
    handler = _make_handler(root, process_supervisor=process_supervisor)
    server = ThreadingHTTPServer((host, port), handler)
    print(f"AgentsAssemble GUI: http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping AgentsAssemble GUI")
    finally:
        process_supervisor.close()
        server.server_close()


def _read_optional(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def read_lobby(output_root: Path, limit: int = 80) -> list[dict[str, object]]:
    return read_lobby_events(output_root / "lobby.jsonl", limit=limit)


def append_lobby_event(output_root: Path, event: dict[str, object]) -> dict[str, object]:
    return append_lobby_event_to_file(output_root / "lobby.jsonl", event)


def read_side_chat(output_root: Path, limit: int = 120) -> list[dict[str, object]]:
    return read_side_chat_events(output_root / "side_chat.jsonl", limit=limit)


def append_side_chat_event(output_root: Path, event: dict[str, object]) -> dict[str, object]:
    return append_side_chat_event_to_file(output_root / "side_chat.jsonl", event)


def _sse_event(event_name: str, payload: dict[str, object], event_id: str | None = None) -> bytes:
    lines = []
    if event_id:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event_name}")
    lines.append(f"data: {json.dumps(payload, ensure_ascii=False)}")
    return ("\n".join(lines) + "\n\n").encode("utf-8")


def _stream_snapshot_payload(
    output_root: Path,
    stream: str,
    meeting_id: str | None = None,
    last_event_id: str | None = None,
) -> dict[str, object]:
    if stream == "lobby":
        events = read_lobby_events_after(output_root / "lobby.jsonl", last_event_id)
        return {"stream": "lobby", "events": events}
    if stream == "side_chat":
        events = read_side_chat_events_after(output_root / "side_chat.jsonl", last_event_id)
        return {"stream": "side_chat", "events": events}
    if stream == "meeting":
        if not meeting_id:
            raise ValueError("Meeting id is required for meeting event stream.")
        meeting_dir = output_root / "meetings" / meeting_id
        if not meeting_dir.exists():
            raise ValueError(f"Meeting {meeting_id} was not found.")
        events = read_live_events_after(meeting_dir, last_event_id)
        payload: dict[str, object] = {
            "stream": "meeting",
            "meeting_id": meeting_id,
            "events": events,
            "payload_signature": json.dumps(events, ensure_ascii=False, sort_keys=True),
        }
        if (meeting_dir / "meeting.json").exists():
            try:
                meeting_payload = build_meeting_payload(meeting_dir)
            except json.JSONDecodeError:
                payload["meeting_payload_pending"] = True
                payload["payload_signature"] = json.dumps(payload, ensure_ascii=False, sort_keys=True)
            else:
                payload["meeting_payload"] = meeting_payload
                payload["payload_signature"] = json.dumps(meeting_payload, ensure_ascii=False, sort_keys=True)
        return payload
    raise ValueError(f"Unknown event stream: {stream}")


def send_lobby_message_to_remote_bridge(
    output_root: Path,
    message: str,
    meeting_id: str | None = None,
    target_agent_id: str | None = None,
    speaker_name: str = "나",
) -> dict[str, object]:
    if not message.strip():
        raise ValueError("Message is required.")
    meeting_dir = _resolve_lobby_meeting_dir(output_root, meeting_id)
    meeting = _read_meeting_record(meeting_dir)
    role_data, binding, provider_data = _select_remote_bridge_binding(meeting, target_agent_id)
    role = _role_from_payload(role_data)
    provider = _runtime_provider_for_binding(meeting, binding, provider_data)
    session = {
        "meeting_id": meeting.get("meeting_id", meeting_dir.name),
        "agent_id": binding.get("agent_id"),
        "owner_id": binding.get("owner_id"),
        "join_mode": binding.get("join_mode"),
        "session_id": binding.get("session_id"),
    }
    adapter = RemoteBridgeAdapter(provider, requester=REMOTE_LOBBY_REQUESTER)
    remote_event = adapter.run_lobby_message(role, session, speaker_name=speaker_name, message=message.strip())
    event = {
        "name": remote_event.get("name") or role.display_name,
        "side": "other-agent",
        "kind": remote_event.get("kind") or "message",
        "message": remote_event.get("message") or "",
    }
    return append_lobby_event(output_root, event)


def provider_catalog_payload() -> dict[str, object]:
    return {"providers": default_provider_registry().catalog()}


def codex_sessions_payload(limit: int = 20) -> dict[str, object]:
    return {"sessions": list_codex_sessions(limit=limit)}


def live_agents_payload(output_root: Path) -> dict[str, object]:
    return {"agents": read_live_agents(output_root)}


def connect_live_agent_payload(output_root: Path, payload: dict[str, object]) -> dict[str, object]:
    return {"agent": connect_live_agent(output_root, payload), "agents": read_live_agents(output_root)}


def live_agent_room_payload(output_root: Path, agent_id: str) -> dict[str, object]:
    agent = _live_agent_for_id(output_root, agent_id)
    return {
        "agent": agent,
        "agents": read_live_agents(output_root),
        "meetings": list_meetings(output_root),
        "lobby_events": read_lobby(output_root),
        "side_chat_events": read_side_chat(output_root),
    }


def live_agent_heartbeat_payload(output_root: Path, agent_id: str, payload: dict[str, object]) -> dict[str, object]:
    agent = heartbeat_live_agent(output_root, agent_id, status=str(payload.get("status") or "online"), metadata=payload)
    return {"agent": agent, "agents": read_live_agents(output_root)}


def live_agent_lobby_message_payload(output_root: Path, agent_id: str, payload: dict[str, object]) -> dict[str, object]:
    agent = heartbeat_live_agent(output_root, agent_id, status="online")
    message = str(payload.get("message") or "").strip()
    if not message:
        raise ValueError("Message is required.")
    event = append_lobby_event(
        output_root,
        {
            "name": agent.get("display_name") or agent.get("agent_id") or agent_id,
            "side": "other-agent",
            "kind": payload.get("kind") or "message",
            "message": message,
            "actor_id": agent.get("agent_id") or agent_id,
            "source_event_id": payload.get("source_event_id") or "",
            "auto_chain_depth": payload.get("auto_chain_depth") or 0,
        },
    )
    return {"agent": agent, "event": event, "events": read_lobby(output_root)}


def live_agent_processes_payload(process_supervisor: LiveAgentProcessSupervisor) -> dict[str, object]:
    return {"groups": process_supervisor.list_groups()}


def start_live_agent_process_payload(
    process_supervisor: LiveAgentProcessSupervisor,
    payload: dict[str, object],
    *,
    default_server: str,
) -> dict[str, object]:
    config_path = Path(str(payload.get("config_path") or "configs/live-agents.example.json"))
    server = str(payload.get("server") or default_server)
    group_id = str(payload.get("group_id") or "").strip() or None
    group = process_supervisor.start_group(config_path=config_path, server=server, group_id=group_id)
    return {"group": group, "groups": process_supervisor.list_groups()}


def stop_live_agent_process_payload(
    process_supervisor: LiveAgentProcessSupervisor,
    group_id: str,
) -> dict[str, object]:
    group = process_supervisor.stop_group(group_id)
    return {"group": group, "groups": process_supervisor.list_groups()}


def codex_session_invite_payload(
    output_root: Path,
    *,
    session_id: str,
    role_id: str,
    meeting_id: str | None = None,
) -> dict[str, object]:
    config_path = output_root / "codex-live-session.local.json"
    role_ids = _codex_invite_role_ids(output_root, meeting_id)
    config = build_codex_live_invite_config(
        session_id=session_id,
        role_id=role_id,
        role_ids=role_ids,
        existing=read_agent_config(config_path),
    )
    write_agent_config(config_path, config)
    binding = _binding_for_role(config.get("agent_bindings", []), role_id)
    return {"config_path": str(config_path), "binding": binding}


def _codex_invite_role_ids(output_root: Path, meeting_id: str | None) -> list[str]:
    if meeting_id:
        meeting_dir = output_root / "meetings" / meeting_id
        if not meeting_dir.exists():
            raise ValueError(f"Meeting {meeting_id} was not found.")
        meeting = _read_meeting_record(meeting_dir)
        role_ids = [str(role["id"]) for role in _as_dict_list(meeting.get("roles", [])) if role.get("id")]
        if role_ids:
            return role_ids
    return [role.id for role in load_council_config().roles]


def _binding_for_role(bindings: object, role_id: str) -> dict[str, object]:
    for binding in _as_dict_list(bindings):
        if binding.get("role_id") == role_id:
            return binding
    raise ValueError(f"No Codex live binding was written for role {role_id}.")


def _live_agent_for_id(output_root: Path, agent_id: str) -> dict[str, object]:
    for agent in read_live_agents(output_root):
        if agent.get("agent_id") == agent_id:
            return agent
    raise ValueError(f"Live agent {agent_id} was not found.")


def _live_agent_action_path(path: str, action: str) -> str | None:
    parts = path.strip("/").split("/")
    if len(parts) == 4 and parts[0] == "api" and parts[1] == "live-agents" and parts[3] == action:
        return unquote(parts[2])
    return None


def _live_agent_process_action_path(path: str, action: str) -> str | None:
    parts = path.strip("/").split("/")
    if len(parts) == 4 and parts[0] == "api" and parts[1] == "live-agent-processes" and parts[3] == action:
        return unquote(parts[2])
    return None


def _resolve_lobby_meeting_dir(output_root: Path, meeting_id: str | None) -> Path:
    if meeting_id:
        meeting_dir = output_root / "meetings" / meeting_id
        if meeting_dir.exists():
            return meeting_dir
        raise ValueError(f"Meeting {meeting_id} was not found.")
    meetings = list_meetings(output_root)
    if not meetings:
        raise ValueError("No meeting is available for remote lobby chat.")
    return Path(str(meetings[0]["path"]))


def _read_meeting_record(meeting_dir: Path) -> dict[str, object]:
    meeting_path = meeting_dir / "meeting.json"
    live_path = meeting_dir / "live_state.json"
    source_path = meeting_path if meeting_path.exists() else live_path
    if not source_path.exists():
        raise ValueError("Meeting record is missing.")
    return json.loads(source_path.read_text(encoding="utf-8"))


def _select_remote_bridge_binding(
    meeting: dict[str, object],
    target_agent_id: str | None,
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    roles = _index_by_id(meeting.get("roles", []))
    providers = _index_by_id(meeting.get("provider_configs", []))
    for binding in _as_dict_list(meeting.get("agent_bindings", [])):
        if target_agent_id and binding.get("agent_id") != target_agent_id:
            continue
        provider = providers.get(str(binding.get("provider_id")))
        if not provider or provider.get("kind") != "remote_http_bridge":
            continue
        role = roles.get(str(binding.get("role_id")))
        if role:
            return role, binding, provider
    raise ValueError("No remote bridge lobby participant is available.")


def _index_by_id(items: object) -> dict[str, dict[str, object]]:
    return {str(item["id"]): item for item in _as_dict_list(items) if item.get("id")}


def _as_dict_list(value: object) -> list[dict[str, object]]:
    if isinstance(value, dict):
        return [item for item in value.values() if isinstance(item, dict)]
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _role_from_payload(payload: dict[str, object]) -> Role:
    return Role(
        id=str(payload.get("id") or "remote"),
        display_name=str(payload.get("display_name") or payload.get("id") or "원격 에이전트"),
        lens=str(payload.get("lens") or "Remote participant"),
        research_focus=str(payload.get("research_focus") or "Lobby participation"),
        personality=payload.get("personality") if isinstance(payload.get("personality"), dict) else None,
        source_preferences=payload.get("source_preferences") if isinstance(payload.get("source_preferences"), list) else None,
    )


def _provider_from_payload(payload: dict[str, object]) -> ProviderConfig:
    return ProviderConfig(
        id=str(payload.get("id") or "remote"),
        kind="remote_http_bridge",
        display_name=str(payload.get("display_name") or payload.get("id") or "Remote bridge"),
        default_model=_optional_str(payload.get("default_model")),
        endpoint=_optional_str(payload.get("endpoint")),
        auth_ref=_optional_str(payload.get("auth_ref")),
        timeout_seconds=payload.get("timeout_seconds") if isinstance(payload.get("timeout_seconds"), int) else None,
        search_enabled=bool(payload.get("search_enabled")),
        notes=_optional_str(payload.get("notes")),
    )


def _runtime_provider_for_binding(
    meeting: dict[str, object],
    binding: dict[str, object],
    public_provider: dict[str, object],
) -> ProviderConfig:
    provider_id = str(binding.get("provider_id") or public_provider.get("id") or "remote")
    runtime_provider = _provider_from_agent_config(meeting.get("agent_config_source"), provider_id)
    if runtime_provider is not None:
        return runtime_provider
    auth_ref = _optional_str(public_provider.get("auth_ref"))
    if auth_ref == "literal:<redacted>" or auth_ref == "<redacted>":
        raise ValueError(
            "Remote bridge credential is not available from the public meeting artifact. "
            "Use an env: auth_ref or rerun with the original agent config available."
        )
    return _provider_from_payload(public_provider)


def _provider_from_agent_config(source: object, provider_id: str) -> ProviderConfig | None:
    if not isinstance(source, str) or not source or source == "default":
        return None
    config_path = Path(source)
    if not config_path.exists():
        return None
    runtime_config = load_agent_runtime_config(config_path)
    if runtime_config is None:
        return None
    return providers_from_config(runtime_config).get(provider_id)


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _make_handler(
    output_root: Path,
    *,
    process_supervisor: LiveAgentProcessSupervisor | None = None,
) -> type[BaseHTTPRequestHandler]:
    static_root = Path(__file__).parent / "static"
    live_agent_process_supervisor = process_supervisor or LiveAgentProcessSupervisor(output_root)

    class AgentsAssembleHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            query = parse_qs(parsed.query)
            if path == "/":
                self._send_file(static_root / "index.html", "text/html; charset=utf-8")
                return
            if path.startswith("/static/"):
                rel = path.removeprefix("/static/")
                static_path = _safe_static_path(static_root, rel)
                if static_path is None:
                    self._send_error(HTTPStatus.NOT_FOUND, "File not found")
                    return
                self._send_file(static_path)
                return
            if path == "/api/meetings":
                self._send_json({"meetings": list_meetings(output_root)})
                return
            if path == "/api/lobby":
                self._send_json({"events": read_lobby(output_root)})
                return
            if path == "/api/events/lobby":
                self._send_sse_stream("lobby", "lobby", last_event_id=self._last_event_id(query))
                return
            if path == "/api/side-chat":
                self._send_json({"events": read_side_chat(output_root)})
                return
            if path == "/api/events/side-chat":
                self._send_sse_stream("side_chat", "side_chat", last_event_id=self._last_event_id(query))
                return
            if path == "/api/providers":
                self._send_json(provider_catalog_payload())
                return
            if path == "/api/live-agents":
                self._send_json(live_agents_payload(output_root))
                return
            if path == "/api/live-agent-processes":
                self._send_json(live_agent_processes_payload(live_agent_process_supervisor))
                return
            live_agent_room_id = _live_agent_action_path(path, "room")
            if live_agent_room_id is not None:
                try:
                    self._send_json(live_agent_room_payload(output_root, live_agent_room_id))
                except ValueError as error:
                    self._send_error(HTTPStatus.NOT_FOUND, str(error))
                return
            if path == "/api/codex-sessions":
                self._send_json(codex_sessions_payload(limit=self._limit(query, default=20)))
                return
            if path == "/api/meetings/latest":
                meetings = list_meetings(output_root)
                if not meetings:
                    self._send_json({"meeting": None})
                    return
                self._send_json(build_meeting_payload(Path(str(meetings[0]["path"]))))
                return
            meeting_events_id = self._meeting_events_id(path)
            if meeting_events_id:
                meeting_dir = output_root / "meetings" / meeting_events_id
                if not meeting_dir.exists():
                    self._send_error(HTTPStatus.NOT_FOUND, "Meeting not found")
                    return
                self._send_sse_stream("meeting", "meeting", meeting_id=meeting_events_id, last_event_id=self._last_event_id(query))
                return
            if path.startswith("/api/meetings/"):
                meeting_id = unquote(path.removeprefix("/api/meetings/"))
                meeting_dir = output_root / "meetings" / meeting_id
                if not meeting_dir.exists():
                    self._send_error(HTTPStatus.NOT_FOUND, "Meeting not found")
                    return
                self._send_json(build_meeting_payload(meeting_dir))
                return
            self._send_error(HTTPStatus.NOT_FOUND, "Not found")

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/api/demo":
                result = run_demo_meeting(adapter_name="mock", output_root=output_root)
                self._send_json({"meeting_id": result.meeting_id, "path": str(result.meeting_dir)})
                return
            if parsed.path == "/api/lobby":
                length = int(self.headers.get("Content-Length", "0") or "0")
                try:
                    payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
                except json.JSONDecodeError:
                    self._send_error(HTTPStatus.BAD_REQUEST, "Invalid JSON")
                    return
                event = append_lobby_event(output_root, payload if isinstance(payload, dict) else {})
                self._send_json({"event": event, "events": read_lobby(output_root)})
                return
            if parsed.path == "/api/side-chat":
                length = int(self.headers.get("Content-Length", "0") or "0")
                try:
                    payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
                except json.JSONDecodeError:
                    self._send_error(HTTPStatus.BAD_REQUEST, "Invalid JSON")
                    return
                event = append_side_chat_event(output_root, payload if isinstance(payload, dict) else {})
                self._send_json({"event": event, "events": read_side_chat(output_root)})
                return
            if parsed.path == "/api/lobby/remote":
                length = int(self.headers.get("Content-Length", "0") or "0")
                try:
                    payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
                except json.JSONDecodeError:
                    self._send_error(HTTPStatus.BAD_REQUEST, "Invalid JSON")
                    return
                if not isinstance(payload, dict):
                    self._send_error(HTTPStatus.BAD_REQUEST, "Invalid JSON")
                    return
                try:
                    event = send_lobby_message_to_remote_bridge(
                        output_root,
                        str(payload.get("message") or ""),
                        meeting_id=_optional_str(payload.get("meeting_id")),
                        target_agent_id=_optional_str(payload.get("target_agent_id")),
                        speaker_name=str(payload.get("speaker_name") or "나"),
                    )
                except ValueError as error:
                    self._send_error(HTTPStatus.BAD_REQUEST, str(error))
                    return
                self._send_json({"event": event, "events": read_lobby(output_root)})
                return
            if parsed.path == "/api/live-agents":
                length = int(self.headers.get("Content-Length", "0") or "0")
                try:
                    payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
                except json.JSONDecodeError:
                    self._send_error(HTTPStatus.BAD_REQUEST, "Invalid JSON")
                    return
                if not isinstance(payload, dict):
                    self._send_error(HTTPStatus.BAD_REQUEST, "Invalid JSON")
                    return
                try:
                    live_agent = connect_live_agent_payload(output_root, payload)
                except ValueError as error:
                    self._send_error(HTTPStatus.BAD_REQUEST, str(error))
                    return
                self._send_json(live_agent)
                return
            if parsed.path == "/api/live-agent-processes/start":
                length = int(self.headers.get("Content-Length", "0") or "0")
                try:
                    payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
                except json.JSONDecodeError:
                    self._send_error(HTTPStatus.BAD_REQUEST, "Invalid JSON")
                    return
                if not isinstance(payload, dict):
                    self._send_error(HTTPStatus.BAD_REQUEST, "Invalid JSON")
                    return
                try:
                    started = start_live_agent_process_payload(
                        live_agent_process_supervisor,
                        payload,
                        default_server=self._request_server_url(),
                    )
                except ValueError as error:
                    self._send_error(HTTPStatus.BAD_REQUEST, str(error))
                    return
                self._send_json(started)
                return
            live_agent_process_stop_id = _live_agent_process_action_path(parsed.path, "stop")
            if live_agent_process_stop_id is not None:
                try:
                    stopped = stop_live_agent_process_payload(live_agent_process_supervisor, live_agent_process_stop_id)
                except ValueError as error:
                    self._send_error(HTTPStatus.BAD_REQUEST, str(error))
                    return
                self._send_json(stopped)
                return
            live_agent_heartbeat_id = _live_agent_action_path(parsed.path, "heartbeat")
            if live_agent_heartbeat_id is not None:
                length = int(self.headers.get("Content-Length", "0") or "0")
                try:
                    payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
                except json.JSONDecodeError:
                    self._send_error(HTTPStatus.BAD_REQUEST, "Invalid JSON")
                    return
                if not isinstance(payload, dict):
                    self._send_error(HTTPStatus.BAD_REQUEST, "Invalid JSON")
                    return
                try:
                    heartbeat = live_agent_heartbeat_payload(output_root, live_agent_heartbeat_id, payload)
                except ValueError as error:
                    self._send_error(HTTPStatus.BAD_REQUEST, str(error))
                    return
                self._send_json(heartbeat)
                return
            live_agent_lobby_id = _live_agent_action_path(parsed.path, "lobby")
            if live_agent_lobby_id is not None:
                length = int(self.headers.get("Content-Length", "0") or "0")
                try:
                    payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
                except json.JSONDecodeError:
                    self._send_error(HTTPStatus.BAD_REQUEST, "Invalid JSON")
                    return
                if not isinstance(payload, dict):
                    self._send_error(HTTPStatus.BAD_REQUEST, "Invalid JSON")
                    return
                try:
                    message = live_agent_lobby_message_payload(output_root, live_agent_lobby_id, payload)
                except ValueError as error:
                    self._send_error(HTTPStatus.BAD_REQUEST, str(error))
                    return
                self._send_json(message)
                return
            if parsed.path == "/api/codex-sessions/invite":
                length = int(self.headers.get("Content-Length", "0") or "0")
                try:
                    payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
                except json.JSONDecodeError:
                    self._send_error(HTTPStatus.BAD_REQUEST, "Invalid JSON")
                    return
                if not isinstance(payload, dict):
                    self._send_error(HTTPStatus.BAD_REQUEST, "Invalid JSON")
                    return
                try:
                    invite = codex_session_invite_payload(
                        output_root,
                        session_id=str(payload.get("session_id") or ""),
                        role_id=str(payload.get("role_id") or ""),
                        meeting_id=_optional_str(payload.get("meeting_id")),
                    )
                except ValueError as error:
                    self._send_error(HTTPStatus.BAD_REQUEST, str(error))
                    return
                self._send_json(invite)
                return
            self._send_error(HTTPStatus.NOT_FOUND, "Not found")

        def log_message(self, format: str, *args: object) -> None:
            return

        def _request_server_url(self) -> str:
            host = self.headers.get("Host")
            if host:
                return f"http://{host}"
            address = self.server.server_address
            return f"http://{address[0]}:{address[1]}"

        def _send_file(self, path: Path, content_type: str | None = None) -> None:
            if not path.exists() or not path.is_file():
                self._send_error(HTTPStatus.NOT_FOUND, "File not found")
                return
            guessed = content_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            data = path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", guessed)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _send_json(self, payload: dict[str, object]) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _send_sse_snapshot(self, event_name: str, payload: dict[str, object]) -> None:
            data = _sse_event(event_name, payload, event_id=_last_payload_event_id(payload))
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(data)

        def _send_sse_stream(
            self,
            event_name: str,
            stream: str,
            meeting_id: str | None = None,
            last_event_id: str | None = None,
        ) -> None:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            current_last_event_id = last_event_id
            current_payload_signature: str | None = None
            while True:
                try:
                    payload = _stream_snapshot_payload(
                        output_root,
                        stream,
                        meeting_id=meeting_id,
                        last_event_id=current_last_event_id,
                    )
                    latest_event_id = _last_payload_event_id(payload)
                    if latest_event_id:
                        self.wfile.write(_sse_event(event_name, payload, event_id=latest_event_id))
                        current_last_event_id = latest_event_id
                        current_payload_signature = _payload_signature(payload)
                    elif _payload_signature(payload) and _payload_signature(payload) != current_payload_signature:
                        self.wfile.write(_sse_event(event_name, payload))
                        current_payload_signature = _payload_signature(payload)
                    else:
                        self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
                    time.sleep(1)
                except (BrokenPipeError, ConnectionResetError):
                    return

        def _send_error(self, status: HTTPStatus, message: str) -> None:
            data = json.dumps({"error": message}, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _last_event_id(self, query: dict[str, list[str]]) -> str | None:
            query_value = query.get("last_event_id", [None])[0]
            header_value = self.headers.get("Last-Event-ID")
            return _optional_str(header_value) or _optional_str(query_value)

        def _meeting_events_id(self, path: str) -> str | None:
            prefix = "/api/meetings/"
            suffix = "/events"
            if not path.startswith(prefix) or not path.endswith(suffix):
                return None
            meeting_id = path[len(prefix) : -len(suffix)]
            return unquote(meeting_id) if meeting_id else None

        def _limit(self, query: dict[str, list[str]], default: int) -> int:
            try:
                return int(query.get("limit", [str(default)])[0])
            except (TypeError, ValueError):
                return default

    return AgentsAssembleHandler


def _last_payload_event_id(payload: dict[str, object]) -> str | None:
    events = payload.get("events")
    if not isinstance(events, list) or not events:
        return None
    latest = events[-1]
    if not isinstance(latest, dict):
        return None
    event_id = latest.get("id")
    return event_id if isinstance(event_id, str) and event_id else None


def _payload_signature(payload: dict[str, object]) -> str | None:
    signature = payload.get("payload_signature")
    return signature if isinstance(signature, str) and signature else None


def _safe_static_path(static_root: Path, relative_path: str) -> Path | None:
    root = static_root.resolve()
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate
