import tempfile
import unittest
import base64
import json
import os
import sys
import threading
import time
from types import SimpleNamespace
from datetime import UTC, datetime, timedelta
from io import BytesIO, StringIO
from pathlib import Path
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from unittest.mock import ANY, patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, quote, urlparse
from urllib.request import Request, urlopen

from agentsassemble.gui import (
    _make_handler,
    _request_trusted,
    _safe_static_path,
    _sse_event,
    _sse_stream_error_payload,
    _stream_snapshot_payload,
    append_lobby_event,
    build_meeting_payload,
    list_meetings,
    provider_catalog_payload,
    read_lobby,
    serve_gui,
    codex_session_invite_payload,
    codex_sessions_payload,
    connect_live_agent_payload,
    live_agent_official_turn_payload,
    live_agent_turn_sequence_payload,
    live_agent_turn_request_payload,
    live_agent_turn_round_payload,
    live_agent_turn_rounds_payload,
    _live_agent_turn_rounds_payload_locked,
    _live_agent_lobby_flow_metadata,
    _run_session_bound_agent_probe,
    _redact_real_session_smoke_lobby_events,
    _readiness_health_operation_details,
    _session_start_operation_details,
    live_agent_lobby_message_payload,
    live_agent_room_payload,
    LIVE_AGENT_ROOM_LOBBY_EVENT_LIMIT,
    LiveAgentFlowSupervisor,
    live_agents_payload,
    live_agent_health_payload,
    live_agent_discovery_payload,
    live_agent_session_ensure_payload,
    _attach_session_auto_rounds_if_requested,
    send_lobby_message_to_remote_bridge,
)
from agentsassemble.gui_room_http import register_room_routes
from agentsassemble.web.router import GuiDeps, RequestContext, Router
from agentsassemble.persistence.local.identity.repository import IdentityStore
from agentsassemble.agent_sessions import room_sse_frames_after_cursor
from agentsassemble.meeting_events import append_live_event, read_live_events, write_live_state
from agentsassemble.meeting_events import read_live_events_after, read_lobby_events_after, read_side_chat_events_after
from agentsassemble.meeting import run_demo_meeting
from agentsassemble.live_agents import connect_live_agent, heartbeat_live_agent, read_live_agents
from agentsassemble.live_agent_operations import append_live_agent_operation, read_live_agent_operations
from agentsassemble.live_agent_meetings import start_live_agent_meeting
from agentsassemble.live_agent_processes import LiveAgentProcessSupervisor
from agentsassemble.live_agent_session_runs import LiveAgentSessionRunController
from agentsassemble.live_agent_smoke import LiveAgentSmokeFailed
from agentsassemble.live_session_transport import terminal_sessions_supported
from agentsassemble.room_invite import (
    compatibility_public_invite_runtime,
    create_room_invite,
    join_room_with_invite,
    reset_state as reset_room_invite_state,
    set_runtime_host_token,
    set_runtime_public_url,
)
from agentsassemble.admission.session_service import RoomSessionService
from agentsassemble.persistence.local.admission.repository import (
    MemoryInviteSessionRepository,
)
from agentsassemble.room_store import RoomStore
from agentsassemble.features.side_chat.service import append_side_chat_event, read_side_chat
from agentsassemble.room.moderation import set_room_member_muted
from agentsassemble.application.room_users import (
    configure_room_users_store,
    reset_state as reset_room_users_state,
    user_for_participant,
)


def _read_sse_frame(response, timeout: float = 3.0) -> str:
    lines = []
    deadline = time.time() + timeout
    while time.time() < deadline:
        raw_line = response.readline()
        if raw_line == b"":
            break
        line = raw_line.decode("utf-8").strip()
        if not line:
            if lines:
                break
            continue
        lines.append(line)
    return "\n".join(lines)


class _RoomsRouteHandler:
    def __init__(
        self,
        *,
        path: str,
        method: str = "GET",
        payload: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
        loopback: bool = True,
    ) -> None:
        self.path = path
        self.command = method
        self.headers = dict(headers or {})
        self.headers.setdefault("Host", "127.0.0.1:8765" if loopback else "room.example.com")
        body = b""
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            self.headers.setdefault("Content-Type", "application/json")
            self.headers["Content-Length"] = str(len(body))
        self.rfile = BytesIO(body)
        self.sent_json: dict[str, object] | None = None
        self.sent_error: tuple[HTTPStatus, str] | None = None
        self.sent_error_code = ""
        self.server = SimpleNamespace(
            server_address=("127.0.0.1", 8765) if loopback else ("0.0.0.0", 8765)
        )

    def _send_json(self, payload: dict[str, object]) -> None:
        self.sent_json = payload

    def _send_error(
        self,
        status: HTTPStatus,
        message: str,
        *,
        code: str = "",
        details: dict[str, object] | None = None,
    ) -> None:
        del details
        self.sent_error = (status, message)
        self.sent_error_code = code

def _dispatch_room_route(
    output_root: Path,
    *,
    path: str,
    method: str = "GET",
    payload: dict[str, object] | None = None,
    headers: dict[str, str] | None = None,
    loopback: bool = True,
    deps: GuiDeps | None = None,
) -> _RoomsRouteHandler:
    parsed = urlparse(path)
    handler = _RoomsRouteHandler(
        path=path,
        method=method,
        payload=payload,
        headers=headers,
        loopback=loopback,
    )
    router = Router()
    register_room_routes(router)
    ctx = RequestContext(
        handler,
        deps
        or _default_room_route_dependencies(output_root),
        parsed,
        parse_qs(parsed.query),
    )
    self_handled = router.dispatch(method, ctx)
    if not self_handled:
        raise AssertionError(f"route not handled: {method} {path}")
    return handler


def _default_room_route_dependencies(output_root: Path) -> GuiDeps:
    session_repository = MemoryInviteSessionRepository()
    return GuiDeps(
        output_root=output_root,
        room_repository=RoomStore(output_root),
        identity_backend=IdentityStore(output_root / "identity.db"),
        public_invite_runtime=compatibility_public_invite_runtime(),
        room_sessions=RoomSessionService(
            session_repository,
            token_prefix="aas1",
            ttl_seconds=3600,
        ),
    )



def _write_health_resident_meeting(root: Path, *, agent_ids: list[str]) -> Path:
    meeting_dir = root / "meetings" / "resident-m1"
    meeting_dir.mkdir(parents=True)
    provider_configs = {
        f"{agent_id}-provider": {"id": f"{agent_id}-provider", "kind": "local_cli"}
        for agent_id in agent_ids
    }
    meeting = {
        "meeting_id": "resident-m1",
        "topic": "resident health",
        "question": "Are residents current?",
        "agent_bindings": [
            {
                "agent_id": agent_id,
                "role_id": agent_id,
                "provider_id": f"{agent_id}-provider",
            }
            for agent_id in agent_ids
        ],
        "provider_configs": provider_configs,
    }
    (meeting_dir / "meeting.json").write_text(json.dumps(meeting, ensure_ascii=False), encoding="utf-8")
    return meeting_dir


def _write_lobby_jsonl_event(root: Path, *, event_id: str, actor_id: str, created_at: str) -> None:
    event = {
        "id": event_id,
        "created_at": created_at,
        "name": "human",
        "side": "other",
        "kind": "message",
        "message": "stale event text must stay out",
        "channel": "lobby",
        "audience": "room",
        "official_record": False,
        "actor_id": actor_id,
        "source_event_id": "",
        "auto_chain_depth": 0,
        "live_agent_endpoint": False,
    }
    with (root / "lobby.jsonl").open("a", encoding="utf-8") as file:
        file.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


def _write_live_jsonl_event(
    meeting_dir: Path,
    *,
    event_id: str,
    kind: str,
    created_at: str,
    target_agent_id: str = "",
    actor_id: str = "",
    source_event_id: str = "",
    content: str = "",
) -> dict[str, object]:
    event = {
        "id": event_id,
        "created_at": created_at,
        "kind": kind,
        "meeting_id": "resident-m1",
        "channel": "official" if kind == "message" else "live",
        "audience": "room",
        "official_record": True,
        "actor_id": actor_id,
        "target_agent_id": target_agent_id,
        "source_event_id": source_event_id,
        "review_checkpoint_id": "",
        "role_id": "",
        "display_name": "",
        "round": None,
        "turn_id": "",
        "turn_index": None,
        "engagement_mode": "",
        "content": content,
        "position": "",
        "stance_status": None,
        "stance_delta": None,
        "changed_by": [],
        "change_reason": "",
        "remaining_resistance": "",
        "emotion": {},
        "confidence": None,
        "retry_status": None,
        "retry_attempts": None,
    }
    path = meeting_dir / "live_events.jsonl"
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    return event


def _write_single_agent_session_configs(council_config: Path, agent_config: Path, live_agent_config: Path) -> None:
    council_config.write_text(
        json.dumps(
            {
                "topic": "resident session",
                "question": "Can a resident session resume?",
                "roles": [
                    {"id": "architect", "display_name": "Architect", "lens": "Architecture", "research_focus": "system"}
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    agent_config.write_text(
        json.dumps(
            {
                "providers": [{"id": "local-cli", "kind": "local_cli", "display_name": "Local CLI"}],
                "permission_profiles": [{"id": "meeting_readonly", "meeting_read": True, "official_turn": True}],
                "agent_bindings": [
                    {
                        "agent_id": "agent-a",
                        "role_id": "architect",
                        "provider_id": "local-cli",
                        "permission_profile_id": "meeting_readonly",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    live_agent_config.write_text(
        json.dumps(
            {
                "agents": [
                    {
                        "agent_id": "agent-a",
                        "display_name": "Agent A",
                        "provider_kind": "local_cli",
                        "connection_kind": "local_cli",
                        "command": [sys.executable, "-c", "print('ok')"],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _write_three_agent_fake_session_configs(council_config: Path, agent_config: Path, live_agent_config: Path) -> None:
    council_config.write_text(
        json.dumps(
            {
                "topic": "resident fake completion",
                "question": "Can three resident fake agents complete and finalize?",
                "roles": [
                    {"id": "architect", "display_name": "Architect", "lens": "Architecture", "research_focus": "system"},
                    {"id": "critic", "display_name": "Critic", "lens": "Risk", "research_focus": "gaps"},
                    {"id": "operator", "display_name": "Operator", "lens": "Operations", "research_focus": "runbook"},
                ],
                "meeting_template": {
                    "id": "resident_fake_completion",
                    "display_name": "Resident Fake Completion",
                    "rounds": [
                        {
                            "id": "round_1",
                            "title": "Round 1",
                            "instruction": "Reply with one concise fake resident answer.",
                            "turn_control": {"selection": "all_roles"},
                        },
                        {
                            "id": "round_2",
                            "title": "Round 2",
                            "instruction": "Reply again so finalization proves all template rounds are complete.",
                            "turn_control": {"selection": "all_roles"},
                        },
                    ],
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    agent_config.write_text(
        json.dumps(
            {
                "providers": [{"id": "local-cli", "kind": "local_cli", "display_name": "Local CLI"}],
                "permission_profiles": [{"id": "meeting_readonly", "meeting_read": True, "official_turn": True}],
                "agent_bindings": [
                    {
                        "agent_id": "agent-a",
                        "role_id": "architect",
                        "provider_id": "local-cli",
                        "permission_profile_id": "meeting_readonly",
                    },
                    {
                        "agent_id": "agent-b",
                        "role_id": "critic",
                        "provider_id": "local-cli",
                        "permission_profile_id": "meeting_readonly",
                    },
                    {
                        "agent_id": "agent-c",
                        "role_id": "operator",
                        "provider_id": "local-cli",
                        "permission_profile_id": "meeting_readonly",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    live_agent_config.write_text(
        json.dumps(
            {
                "poll_interval": 0.05,
                "heartbeat_interval": 0,
                "cooldown": 0,
                "max_chain_depth": 0,
                "agents": [
                    {
                        "agent_id": "agent-a",
                        "display_name": "Agent A",
                        "provider_kind": "local_cli",
                        "connection_kind": "local_cli",
                        "engagement_mode": "moderator_called",
                        "command": [sys.executable, "-c", "import sys; sys.stdin.read(); print('agent-a official reply')"],
                        "timeout_seconds": 5,
                    },
                    {
                        "agent_id": "agent-b",
                        "display_name": "Agent B",
                        "provider_kind": "local_cli",
                        "connection_kind": "local_cli",
                        "engagement_mode": "moderator_called",
                        "command": [sys.executable, "-c", "import sys; sys.stdin.read(); print('agent-b official reply')"],
                        "timeout_seconds": 5,
                    },
                    {
                        "agent_id": "agent-c",
                        "display_name": "Agent C",
                        "provider_kind": "local_cli",
                        "connection_kind": "local_cli",
                        "engagement_mode": "moderator_called",
                        "command": [sys.executable, "-c", "import sys; sys.stdin.read(); print('agent-c official reply')"],
                        "timeout_seconds": 5,
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _write_fake_codex_executable(path: Path) -> None:
    path.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path


SESSION_IDS = {
    "codex-live-lore": "019e0000-0000-7000-a000-000000000001",
    "codex-live-feats": "019e0000-0000-7000-a000-000000000002",
    "codex-live-skeptic": "019e0000-0000-7000-a000-000000000003",
}


def sandbox_flags(args):
    if "exec" not in args:
        return []
    index = args.index("exec")
    return args[index + 1:index + 5]


def record(payload):
    log_path = os.environ.get("AGENTSASSEMBLE_FAKE_CODEX_LOG")
    if not log_path:
        return
    with open(log_path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\\n")


args = sys.argv[1:]
if args == ["login", "status"]:
    record({"mode": "login_status"})
    print("Logged in using ChatGPT")
    raise SystemExit(0)

if "--help" in args:
    record({"mode": "help", "sandbox_flags": sandbox_flags(args)})
    print("Usage: codex exec [OPTIONS] resume --help")
    raise SystemExit(0)

try:
    output_path = Path(args[args.index("--output-last-message") + 1])
except (ValueError, IndexError):
    print("missing --output-last-message", file=sys.stderr)
    raise SystemExit(2)

agent_id = output_path.name.removesuffix("-last-message.txt")
mode = "resume" if "resume" in args else "fresh"
if mode == "resume":
    try:
        session_id = args[args.index("--output-last-message") + 2]
    except (ValueError, IndexError):
        session_id = ""
else:
    session_id = SESSION_IDS.get(agent_id, "019e0000-0000-7000-a000-000000000099")

output_path.write_text(f"{agent_id} fake Codex {mode} reply", encoding="utf-8")
record(
    {
        "mode": mode,
        "agent_id": agent_id,
        "session_id": session_id,
        "sandbox_flags": sandbox_flags(args),
    }
)
if mode == "fresh":
    print(json.dumps({"type": "session.started", "session": {"id": session_id}}))
""",
        encoding="utf-8",
    )
    path.chmod(0o755)
