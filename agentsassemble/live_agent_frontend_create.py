from __future__ import annotations

import json
import platform
import re
import shutil
import shlex
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable, Sequence
from uuid import uuid4

from agentsassemble.identity.repository import IdentityBackend
from agentsassemble.live_agent_preflight import preflight_live_agent_config
from agentsassemble.legacy.live_agent.runtime.processes import clean_live_agent_group_id
from agentsassemble.legacy.live_agent.runtime.timing import DEFAULT_LIVE_AGENT_POLL_INTERVAL
from agentsassemble.live_agents import connect_live_agent, read_live_agents
from agentsassemble.legacy.meeting.core.events import clean_lobby_text, write_live_state
from agentsassemble.admission.invite import create_room_invite


@dataclass(frozen=True)
class FrontendLiveAgentOption:
    id: str
    label: str


@dataclass(frozen=True)
class FrontendLiveAgentProvider:
    id: str
    label: str
    provider_kind: str
    connection_kind: str
    participant_type: str
    command: list[str]
    timeout_seconds: int
    startable: bool = True
    terminal_idle_timeout: float = 0.35
    verification_note: str = ""
    model_options: tuple[FrontendLiveAgentOption, ...] = ()
    effort_options: tuple[FrontendLiveAgentOption, ...] = ()
    speed_options: tuple[FrontendLiveAgentOption, ...] = ()
    # The provider's OWN permission/sandbox options, surfaced as-is (the room
    # doesn't impose its own model). Empty = no choice for this provider.
    permission_options: tuple[FrontendLiveAgentOption, ...] = ()
    login_command: tuple[str, ...] = ()


@dataclass(frozen=True)
class FrontendLiveAgentTuning:
    model_id: str
    effort: str
    speed: str
    poll_interval: float
    reply_char_limit: int = 0  # 0 = no cap (narrate freely); >0 caps room messages
    permission_option: str = ""  # the provider's chosen permission/sandbox value (its own flag)
    fast_mode: bool = False  # per-agent fast toggle (codex --enable fast_mode, claude /fast)


DEFAULT_MODEL_OPTIONS: tuple[FrontendLiveAgentOption, ...] = (
    FrontendLiveAgentOption("", "기본값"),
)
CODEX_MODEL_OPTIONS: tuple[FrontendLiveAgentOption, ...] = (
    *DEFAULT_MODEL_OPTIONS,
    FrontendLiveAgentOption("gpt-5.5", "GPT 5.5"),
    FrontendLiveAgentOption("gpt-5.4", "GPT 5.4"),
    FrontendLiveAgentOption("gpt-5.4-mini", "GPT 5.4 mini"),
    FrontendLiveAgentOption("gpt-5.3-codex-spark", "GPT 5.3 Codex Spark"),
    FrontendLiveAgentOption("gpt-5.3-codex", "GPT 5.3 Codex"),
    FrontendLiveAgentOption("gpt-5.2", "GPT 5.2"),
)
CURSOR_MODEL_OPTIONS: tuple[FrontendLiveAgentOption, ...] = (
    *DEFAULT_MODEL_OPTIONS,
    FrontendLiveAgentOption("gpt-5", "GPT 5"),
    FrontendLiveAgentOption("sonnet-4", "Sonnet 4"),
    FrontendLiveAgentOption("sonnet-4-thinking", "Sonnet 4 Thinking"),
)
CLAUDE_MODEL_OPTIONS: tuple[FrontendLiveAgentOption, ...] = (
    *DEFAULT_MODEL_OPTIONS,
    FrontendLiveAgentOption("haiku", "Haiku"),
    FrontendLiveAgentOption("sonnet", "Sonnet"),
    FrontendLiveAgentOption("opus", "Opus"),
)
GROK_MODEL_OPTIONS: tuple[FrontendLiveAgentOption, ...] = (
    *DEFAULT_MODEL_OPTIONS,
    FrontendLiveAgentOption("grok-4", "Grok 4"),
)
ANTIGRAVITY_MODEL_OPTIONS: tuple[FrontendLiveAgentOption, ...] = (
    *DEFAULT_MODEL_OPTIONS,
    FrontendLiveAgentOption("Gemini 3.5 Flash (Medium)", "Gemini 3.5 Flash (Medium)"),
    FrontendLiveAgentOption("Gemini 3.5 Flash (High)", "Gemini 3.5 Flash (High)"),
    FrontendLiveAgentOption("Gemini 3.5 Flash (Low)", "Gemini 3.5 Flash (Low)"),
    FrontendLiveAgentOption("Gemini 3.1 Pro (Low)", "Gemini 3.1 Pro (Low)"),
    FrontendLiveAgentOption("Gemini 3.1 Pro (High)", "Gemini 3.1 Pro (High)"),
    FrontendLiveAgentOption("Claude Sonnet 4.6 (Thinking)", "Claude Sonnet 4.6 (Thinking)"),
    FrontendLiveAgentOption("Claude Opus 4.6 (Thinking)", "Claude Opus 4.6 (Thinking)"),
    FrontendLiveAgentOption("GPT-OSS 120B (Medium)", "GPT-OSS 120B (Medium)"),
)
EFFORT_OPTIONS: tuple[FrontendLiveAgentOption, ...] = (
    FrontendLiveAgentOption("", "기본값"),
    FrontendLiveAgentOption("low", "Low"),
    FrontendLiveAgentOption("medium", "Medium"),
    FrontendLiveAgentOption("high", "High"),
    FrontendLiveAgentOption("xhigh", "XHigh"),
)
EXTENDED_EFFORT_OPTIONS: tuple[FrontendLiveAgentOption, ...] = (
    *EFFORT_OPTIONS,
    FrontendLiveAgentOption("max", "Max"),
)
GROK_EFFORT_OPTIONS: tuple[FrontendLiveAgentOption, ...] = (
    *EXTENDED_EFFORT_OPTIONS,
)
SPEED_OPTIONS: tuple[FrontendLiveAgentOption, ...] = (
    FrontendLiveAgentOption("balanced", "균형"),
    FrontendLiveAgentOption("fast", "빠르게"),
    FrontendLiveAgentOption("slow", "천천히"),
)
SPEED_POLL_INTERVALS = {
    "balanced": DEFAULT_LIVE_AGENT_POLL_INTERVAL,
    "fast": 0.1,
    "slow": 1.0,
}

# Each provider's OWN permission/sandbox options (real CLI values), surfaced
# as-is. The launch layer maps the chosen value to that CLI's actual flag.
CODEX_PERMISSION_OPTIONS: tuple[FrontendLiveAgentOption, ...] = (
    FrontendLiveAgentOption("read-only", "읽기 전용 (읽기·탐색만)"),
    FrontendLiveAgentOption("workspace-write", "작업 (작업폴더 쓰기)"),
    FrontendLiveAgentOption("danger-full-access", "전체 해제 (위험)"),
)
# claude + grok share Claude-Code-style --permission-mode values.
CLAUDE_PERMISSION_OPTIONS: tuple[FrontendLiveAgentOption, ...] = (
    FrontendLiveAgentOption("default", "기본 (행동마다 확인)"),
    FrontendLiveAgentOption("plan", "계획만 (실행 안 함)"),
    FrontendLiveAgentOption("acceptEdits", "편집 자동수락"),
    FrontendLiveAgentOption("bypassPermissions", "전체 해제 (위험)"),
)
ANTIGRAVITY_PERMISSION_OPTIONS: tuple[FrontendLiveAgentOption, ...] = (
    FrontendLiveAgentOption("default", "기본"),
    FrontendLiveAgentOption("sandbox", "샌드박스 (터미널 제한)"),
    FrontendLiveAgentOption("skip-permissions", "전체 해제 (위험)"),
)


FRONTEND_LIVE_AGENT_PROVIDERS: tuple[FrontendLiveAgentProvider, ...] = (
    FrontendLiveAgentProvider(
        id="codex",
        label="Codex",
        provider_kind="codex_live_session",
        connection_kind="live_session",
        participant_type="subscription_ai",
        command=[],
        timeout_seconds=240,
        verification_note="실제 프론트 생성/시작 검증 대상입니다.",
        model_options=CODEX_MODEL_OPTIONS,
        effort_options=EFFORT_OPTIONS,
        speed_options=SPEED_OPTIONS,
        permission_options=CODEX_PERMISSION_OPTIONS,
        login_command=("codex", "login"),
    ),
    FrontendLiveAgentProvider(
        id="claude",
        label="Claude",
        provider_kind="claude_code",
        connection_kind="terminal_session",
        participant_type="subscription_ai",
        command=["claude"],
        timeout_seconds=120,
        terminal_idle_timeout=0.75,
        verification_note="선택지는 제공하지만 이 환경에서 실제 세션 검증은 별도로 확인해야 합니다.",
        model_options=CLAUDE_MODEL_OPTIONS,
        effort_options=EXTENDED_EFFORT_OPTIONS,
        speed_options=SPEED_OPTIONS,
        permission_options=CLAUDE_PERMISSION_OPTIONS,
        login_command=("claude", "auth", "login"),
    ),
    FrontendLiveAgentProvider(
        id="cursor",
        label="Cursor",
        provider_kind="cursor_live_session",
        connection_kind="live_session",
        participant_type="subscription_ai",
        command=[],
        timeout_seconds=180,
        verification_note="실제 프론트 생성/시작 검증 대상입니다.",
        model_options=CURSOR_MODEL_OPTIONS,
        speed_options=SPEED_OPTIONS,
        login_command=("cursor-agent", "login"),
    ),
    FrontendLiveAgentProvider(
        id="grok",
        label="Grok",
        provider_kind="grok_live_session",
        connection_kind="live_session",
        participant_type="subscription_ai",
        command=[],
        timeout_seconds=240,
        verification_note="실제 프론트 생성/시작 검증 대상입니다.",
        model_options=GROK_MODEL_OPTIONS,
        effort_options=GROK_EFFORT_OPTIONS,
        speed_options=SPEED_OPTIONS,
        permission_options=CLAUDE_PERMISSION_OPTIONS,
        login_command=("grok", "login"),
    ),
    FrontendLiveAgentProvider(
        id="antigravity",
        label="Antigravity",
        provider_kind="antigravity_live_session",
        connection_kind="live_session",
        participant_type="subscription_ai",
        command=[],
        timeout_seconds=240,
        verification_note="실제 프론트 생성/시작 검증 대상입니다.",
        model_options=ANTIGRAVITY_MODEL_OPTIONS,
        speed_options=SPEED_OPTIONS,
        permission_options=ANTIGRAVITY_PERMISSION_OPTIONS,
        login_command=("agy",),
    ),
    FrontendLiveAgentProvider(
        id="local",
        label="Local",
        provider_kind="local_openai_compatible",
        connection_kind="manual",
        participant_type="local",
        command=[],
        timeout_seconds=120,
        startable=False,
        verification_note="Local 런타임 연결 설정 UI는 준비 중입니다.",
    ),
)


def frontend_live_agent_options_payload(*, default_workspace: Path | None = None) -> dict[str, object]:
    workspace = Path(default_workspace or Path.cwd()).expanduser()
    return {
        "default_workspace": str(workspace),
        "providers": [_provider_payload(provider) for provider in FRONTEND_LIVE_AGENT_PROVIDERS],
    }


def frontend_live_agent_create_payload(
    output_root: Path,
    process_supervisor: object,
    payload: dict[str, object],
    *,
    default_server: str,
    preflight_checker: Callable[..., dict[str, object]] | None = None,
) -> dict[str, object]:
    provider = _provider_for_payload(payload)
    meeting_id = _clean_existing_meeting_id(payload.get("meeting_id"))
    display_name = clean_lobby_text(payload.get("display_name"), limit=64) or provider.label
    workspace = _workspace_path(payload.get("workspace_path"))
    tuning = _tuning_for_payload(provider, payload)
    start_now = _payload_bool(payload.get("start_now"))
    if start_now and not provider.startable:
        raise ValueError("Local runtime is not configured for frontend start yet.")

    # A UI room may not have a server meeting yet (it lives in localStorage);
    # materialize one on demand so adding an agent no longer fails with
    # "Meeting <id> was not found".
    meeting_dir = ensure_frontend_meeting(output_root, meeting_id, label=clean_lobby_text(payload.get("room_label"), limit=128))
    meeting = _read_meeting(meeting_dir)
    agent_id = _unique_agent_id(meeting, provider=provider, display_name=display_name)
    role_id = agent_id
    provider_id = f"{agent_id}-provider"
    permission_profile_id = "frontend-agent-default"
    engagement_mode = _engagement_mode(payload.get("engagement_mode"))
    live_agent_config_path: Path | None = None
    group_id = ""

    if provider.startable:
        live_agent_config_path = _write_frontend_live_agent_config(
            output_root,
            provider=provider,
            agent_id=agent_id,
            display_name=display_name,
            meeting_id=meeting_id,
            engagement_mode=engagement_mode,
            workspace_path=workspace,
            server=default_server,
            tuning=tuning,
            session_id=clean_lobby_text(payload.get("session_id"), limit=200),
        )
        group_id = clean_live_agent_group_id(f"agent-{agent_id}")
        if start_now:
            preflight = (preflight_checker or preflight_live_agent_config)(
                live_agent_config_path,
                server_override=default_server,
            )
            if preflight.get("status") != "ok":
                raise ValueError(_preflight_failure_message(preflight))

    updated_meeting = _meeting_with_frontend_agent(
        meeting,
        agent_id=agent_id,
        role_id=role_id,
        provider_id=provider_id,
        permission_profile_id=permission_profile_id,
        display_name=display_name,
        provider=provider,
        workspace_path=str(workspace),
        engagement_mode=engagement_mode,
        tuning=tuning,
    )
    write_live_state(meeting_dir, updated_meeting)

    agent = connect_live_agent(
        output_root,
        {
            "agent_id": agent_id,
            "display_name": display_name,
            "provider_kind": provider.provider_kind,
            "connection_kind": provider.connection_kind,
            "meeting_id": meeting_id,
            "engagement_mode": engagement_mode,
            "status": "offline",
            "capabilities": ["room_chat", "official_turn"],
            "process_group_id": group_id,
            "live_agent_config_path": str(live_agent_config_path) if live_agent_config_path else "",
            "workspace_path": str(workspace),
            "model_id": tuning.model_id,
            "effort": tuning.effort,
            "speed": tuning.speed,
            "poll_interval": tuning.poll_interval,
        },
    )

    response: dict[str, object] = {
        "status": "created",
        "meeting_id": meeting_id,
        "agent": agent,
        "agents": read_live_agents(output_root),
        "provider": _provider_payload(provider),
        "tuning": _tuning_payload(tuning),
    }
    if provider.startable:
        if live_agent_config_path is None:
            raise ValueError("Agent configuration was not created.")
        response.update(
            {
                "live_agent_config_path": str(live_agent_config_path),
                "group_id": group_id,
            }
        )
        if start_now:
            group = process_supervisor.start_group(
                config_path=live_agent_config_path,
                server=default_server,
                group_id=group_id,
                meeting_id=meeting_id,
                auto_restart=_payload_bool(payload.get("auto_restart")),
                max_restarts=_payload_nonnegative_int(payload.get("max_restarts"), 0),
                restart_backoff_seconds=_payload_nonnegative_float(payload.get("restart_backoff_seconds"), 5.0),
                stale_restart_after_seconds=_payload_nonnegative_float(payload.get("stale_restart_after_seconds"), 0.0),
            )
            response.update({"status": "starting", "group": group})
    return response


def frontend_live_agent_check_payload(
    output_root: Path,
    payload: dict[str, object],
    *,
    default_server: str,
    preflight_checker: Callable[..., dict[str, object]] | None = None,
) -> dict[str, object]:
    provider = _provider_for_payload(payload)
    workspace = _workspace_path(payload.get("workspace_path"))
    tuning = _tuning_for_payload(provider, payload)
    if not provider.startable:
        return {
            "status": "blocked",
            "provider": _provider_payload(provider),
            "workspace_path": str(workspace),
            "message": "Local runtime is not configured for frontend start yet.",
        }
    meeting_id = _clean_existing_meeting_id(payload.get("meeting_id"))
    display_name = clean_lobby_text(payload.get("display_name"), limit=64) or provider.label
    agent_id = _preview_agent_id(provider, display_name)
    config_path = _write_frontend_live_agent_config(
        output_root,
        provider=provider,
        agent_id=agent_id,
        display_name=display_name,
        meeting_id=meeting_id,
        engagement_mode=_engagement_mode(payload.get("engagement_mode")),
        workspace_path=workspace,
        server=default_server,
        tuning=tuning,
        draft=True,
    )
    preflight = (preflight_checker or preflight_live_agent_config)(config_path, server_override=default_server)
    status = "ok" if preflight.get("status") == "ok" else "failed"
    response = {
        "status": status,
        "provider": _provider_payload(provider),
        "tuning": _tuning_payload(tuning),
        "workspace_path": str(workspace),
        "preflight": preflight,
    }
    if status != "ok":
        response["message"] = _preflight_failure_message(preflight)
        auth_action = _auth_action_payload(provider, preflight)
        if auth_action:
            response["auth_action"] = auth_action
    return response


def frontend_live_agent_login_payload(
    payload: dict[str, object],
    *,
    command_resolver: Callable[[str], str | None] | None = None,
    command_launcher: Callable[[list[str]], object] | None = None,
) -> dict[str, object]:
    provider = _provider_for_payload(payload)
    if not provider.login_command:
        raise ValueError(f"{provider.label} does not support local login from this UI.")
    resolved_command = _resolved_login_command(
        provider.login_command,
        command_resolver=command_resolver or shutil.which,
    )
    launcher = command_launcher or _launch_login_command
    launcher(resolved_command)
    return {
        "status": "started",
        "provider_id": provider.id,
        "label": f"{provider.label} 로그인 열기",
        "message": f"{provider.label} 로그인 창을 열었습니다. 로그인 완료 후 연결 확인을 다시 누르세요.",
    }


def _provider_payload(provider: FrontendLiveAgentProvider) -> dict[str, object]:
    return {
        "id": provider.id,
        "label": provider.label,
        "provider_kind": provider.provider_kind,
        "connection_kind": provider.connection_kind,
        "participant_type": provider.participant_type,
        "startable": provider.startable,
        "verification_note": provider.verification_note,
        "model_options": [_option_payload(option) for option in provider.model_options],
        "effort_options": [_option_payload(option) for option in provider.effort_options],
        "speed_options": [_option_payload(option) for option in provider.speed_options],
        "permission_options": [_option_payload(option) for option in provider.permission_options],
        "login_available": bool(provider.login_command),
        "login_label": f"{provider.label} 로그인 열기" if provider.login_command else "",
    }


def _option_payload(option: FrontendLiveAgentOption) -> dict[str, str]:
    return {"id": option.id, "label": option.label}


def _tuning_payload(tuning: FrontendLiveAgentTuning) -> dict[str, object]:
    return {
        "model_id": tuning.model_id,
        "effort": tuning.effort,
        "speed": tuning.speed,
        "poll_interval": tuning.poll_interval,
    }


def _provider_for_payload(payload: dict[str, object]) -> FrontendLiveAgentProvider:
    provider_id = clean_lobby_text(payload.get("provider_id"), limit=64).casefold()
    for provider in FRONTEND_LIVE_AGENT_PROVIDERS:
        if provider.id == provider_id:
            return provider
    raise ValueError("Unknown agent provider.")


def _tuning_for_payload(provider: FrontendLiveAgentProvider, payload: dict[str, object]) -> FrontendLiveAgentTuning:
    model_id = _selected_option_id(
        payload.get("model_id"),
        provider.model_options,
        default="",
        error_label="model",
    )
    effort = _selected_option_id(
        payload.get("effort"),
        provider.effort_options,
        default="",
        error_label="effort",
    )
    speed = _selected_option_id(
        payload.get("speed"),
        provider.speed_options,
        default="balanced" if provider.speed_options else "",
        error_label="speed",
    )
    reply_char_limit = _clean_reply_char_limit(payload.get("reply_char_limit"))
    permission_option = _selected_option_id(
        payload.get("permission_option"),
        provider.permission_options,
        default=provider.permission_options[0].id if provider.permission_options else "",
        error_label="permission",
    )
    return FrontendLiveAgentTuning(
        model_id=model_id,
        effort=effort,
        speed=speed,
        poll_interval=SPEED_POLL_INTERVALS.get(speed, DEFAULT_LIVE_AGENT_POLL_INTERVAL),
        reply_char_limit=reply_char_limit,
        permission_option=permission_option,
        fast_mode=bool(payload.get("fast_mode")),
    )


REPLY_CHAR_LIMIT_CHOICES = (0, 100, 250, 400, 700, 1000)


def _clean_reply_char_limit(value: object) -> int:
    """0 = no cap (default). Anything off the menu snaps to the nearest choice
    so a stray value can't smuggle in an unbounded/odd limit."""
    try:
        limit = int(value or 0)
    except (TypeError, ValueError):
        return 0
    if limit <= 0:
        return 0
    return min(REPLY_CHAR_LIMIT_CHOICES[1:], key=lambda choice: abs(choice - limit))


def _selected_option_id(
    value: object,
    options: tuple[FrontendLiveAgentOption, ...],
    *,
    default: str,
    error_label: str,
) -> str:
    raw = clean_lobby_text(value, limit=128)
    if not raw:
        return default
    allowed = {option.id for option in options}
    if raw in allowed:
        return raw
    raise ValueError(f"Unsupported {error_label}.")


def _workspace_path(value: object) -> Path:
    raw = clean_lobby_text(value, limit=2048)
    if not raw:
        raise ValueError("Workspace folder is required.")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    path = path.resolve()
    if not path.exists() or not path.is_dir():
        raise ValueError("Workspace folder was not found.")
    return path


def _clean_existing_meeting_id(value: object) -> str:
    meeting_id = clean_lobby_text(value, limit=128)
    if not meeting_id or meeting_id in {".", ".."}:
        raise ValueError("Meeting was not found.")
    if "/" in meeting_id or "\\" in meeting_id or Path(meeting_id).name != meeting_id:
        raise ValueError(f"Meeting {meeting_id} was not found.")
    return meeting_id


def _existing_meeting_dir(output_root: Path, meeting_id: str) -> Path:
    meetings_root = (output_root / "meetings").resolve()
    meeting_dir = (meetings_root / meeting_id).resolve()
    try:
        meeting_dir.relative_to(meetings_root)
    except ValueError as error:
        raise ValueError(f"Meeting {meeting_id} was not found.") from error
    if not (meeting_dir / "live_state.json").exists():
        raise ValueError(f"Meeting {meeting_id} was not found.")
    return meeting_dir


def ensure_frontend_meeting(
    output_root: Path,
    meeting_id: str,
    *,
    label: str = "",
    owner_id: str = "",
    identity_backend: IdentityBackend | None = None,
) -> Path:
    """Return the meeting dir for a UI room, materializing a minimal one if absent.

    Frontend rooms (the Discord-style dock) live in the browser's localStorage and
    have no server-side meeting of their own. The agent-create flow, roster, and
    lobby all expect a real meeting dir, so the first time a room needs server
    backing we create a minimal-but-valid ``live_state.json`` here. This is the
    seam that promotes a localStorage room to a first-class server object on
    demand — localStorage becomes a cache, the meeting dir the source of truth.
    """
    clean = _clean_existing_meeting_id(meeting_id)
    meetings_root = (output_root / "meetings").resolve()
    meeting_dir = (meetings_root / clean).resolve()
    try:
        meeting_dir.relative_to(meetings_root)
    except ValueError as error:
        raise ValueError(f"Meeting {clean} was not found.") from error
    title = clean_lobby_text(label, limit=128) or clean
    if (meeting_dir / "live_state.json").exists():
        _upsert_frontend_room_registry(
            clean,
            owner_id=owner_id,
            label=title,
            identity_backend=identity_backend,
        )
        return meeting_dir
    meeting_dir.mkdir(parents=True, exist_ok=True)
    write_live_state(
        meeting_dir,
        {
            "meeting_id": clean,
            "topic": title,
            "display_topic": title,
            "roles": [],
            "agent_bindings": [],
            "provider_configs": {},
            "permission_profiles": {},
            "room_chat": [],
            "research_depth": {"name": "resident_live"},
            "live_status": "running",
            "origin": "frontend_room",
        },
    )
    _upsert_frontend_room_registry(
        clean,
        owner_id=owner_id,
        label=title,
        identity_backend=identity_backend,
    )
    return meeting_dir


def _upsert_frontend_room_registry(
    room_id: str,
    *,
    owner_id: str = "",
    label: str = "",
    identity_backend: IdentityBackend | None = None,
) -> None:
    """Best-effort DB room registry write; meeting materialization is primary."""
    try:
        if identity_backend is not None:
            identity_backend.upsert_room(
                room_id=room_id,
                owner_id=owner_id,
                label=label,
                origin="frontend_room",
            )
            return

        from agentsassemble.application.room_users import upsert_room

        upsert_room(
            room_id=room_id,
            owner_id=owner_id,
            label=label,
            origin="frontend_room",
        )
    except Exception:
        return


def _read_meeting(meeting_dir: Path) -> dict[str, object]:
    data = json.loads((meeting_dir / "live_state.json").read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Meeting state is invalid.")
    return data


def _meeting_with_frontend_agent(
    meeting: dict[str, object],
    *,
    agent_id: str,
    role_id: str,
    provider_id: str,
    permission_profile_id: str,
    display_name: str,
    provider: FrontendLiveAgentProvider,
    workspace_path: str,
    engagement_mode: str,
    tuning: FrontendLiveAgentTuning,
) -> dict[str, object]:
    updated = dict(meeting)
    roles = _as_dict_list(updated.get("roles"))
    roles.append(
        {
            "id": role_id,
            "display_name": display_name,
            "lens": f"{display_name} live room agent",
            "research_focus": "Respond in the shared room while respecting the selected workspace.",
        }
    )
    updated["roles"] = roles

    providers = _as_dict_map(updated.get("provider_configs"))
    providers[provider_id] = {
        "id": provider_id,
        "kind": provider.provider_kind,
        "display_name": display_name,
        "default_model": tuning.model_id or None,
        "endpoint": None,
        "auth_ref": None,
        "timeout_seconds": provider.timeout_seconds,
        "search_enabled": False,
        "notes": "Created from the Discord-style room UI.",
        "command": ["<redacted>"] if provider.command else None,
        "command_configured": bool(provider.command),
        "workspace_path": workspace_path,
        "effort": tuning.effort,
        "speed": tuning.speed,
    }
    updated["provider_configs"] = providers

    profiles = _as_dict_map(updated.get("permission_profiles"))
    profiles.setdefault(
        permission_profile_id,
        {
            "id": permission_profile_id,
            "meeting_read": True,
            "lobby_chat": True,
            "official_turn": True,
            "web_search": False,
            "tool_use": False,
            "filesystem_read": True,
            "filesystem_write": False,
            "git_write": False,
            "push": False,
            "secrets": False,
            "implementation": False,
        },
    )
    updated["permission_profiles"] = profiles

    bindings = _as_dict_list(updated.get("agent_bindings"))
    bindings.append(
        {
            "agent_id": agent_id,
            "role_id": role_id,
            "owner_id": "host",
            "provider_id": provider_id,
            "model_id": tuning.model_id or None,
            "permission_profile_id": permission_profile_id,
            "memory_profile_id": None,
            "join_mode": "fresh",
            "engagement_mode": engagement_mode,
            "session_id": None,
            "effort": tuning.effort,
            "speed": tuning.speed,
        }
    )
    updated["agent_bindings"] = bindings
    updated["updated_at"] = datetime.now(UTC).isoformat()
    return updated


def _write_frontend_live_agent_config(
    output_root: Path,
    *,
    provider: FrontendLiveAgentProvider,
    agent_id: str,
    display_name: str,
    meeting_id: str,
    engagement_mode: str,
    workspace_path: Path,
    server: str,
    tuning: FrontendLiveAgentTuning,
    session_id: str = "",
    draft: bool = False,
) -> Path:
    config_dir = output_root / ("live-agent-drafts" if draft else "live-agent-created")
    config_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{_safe_filename(agent_id)}.json"
    config_path = config_dir / filename
    agent: dict[str, object] = {
        "agent_id": agent_id,
        "display_name": display_name,
        "provider_kind": provider.provider_kind,
        "connection_kind": provider.connection_kind,
        "meeting_id": meeting_id,
        "engagement_mode": engagement_mode,
        "timeout_seconds": provider.timeout_seconds,
        "workspace_path": str(workspace_path),
        "speed": tuning.speed,
        "poll_interval": tuning.poll_interval,
    }
    if tuning.model_id:
        agent["model_id"] = tuning.model_id
    if tuning.effort:
        agent["effort"] = tuning.effort
    if tuning.reply_char_limit:
        agent["reply_char_limit"] = tuning.reply_char_limit
    if tuning.permission_option:
        agent["permission_option"] = tuning.permission_option
    if tuning.fast_mode:
        agent["fast_mode"] = True
    if session_id:
        agent["session_id"] = session_id  # resume an existing local session
    command = _frontend_resident_command(provider, tuning)
    if command:
        agent["command"] = command
    if provider.terminal_idle_timeout != 0.35:
        agent["terminal_idle_timeout"] = provider.terminal_idle_timeout
    agent["invite_token"] = _agent_ws_invite_token(
        server=server,
        meeting_id=meeting_id,
        agent_id=agent_id,
        display_name=display_name,
    )
    config = {
        "server": server,
        "transport": "ws",
        "poll_interval": DEFAULT_LIVE_AGENT_POLL_INTERVAL,
        "heartbeat_interval": 30,
        "cooldown": 0,
        "max_chain_depth": 1,
        "agents": [agent],
    }
    _write_json(config_path, config)
    return config_path


def _frontend_resident_command(provider: FrontendLiveAgentProvider, tuning: FrontendLiveAgentTuning) -> list[str]:
    command = list(provider.command)
    if not command:
        return []
    if provider.provider_kind == "claude_code" and provider.connection_kind == "terminal_session":
        if tuning.model_id:
            command.extend(["--model", tuning.model_id])
        if tuning.effort:
            command.extend(["--effort", tuning.effort])
    return command


def _agent_ws_invite_token(
    *,
    server: str,
    meeting_id: str,
    agent_id: str,
    display_name: str,
) -> str:
    invite = create_room_invite(
        room_url=server,
        meeting_id=meeting_id,
        agent_id=agent_id,
        display_name=display_name,
        participant_type="agent",
        ttl_seconds=7 * 24 * 3600,
        max_uses=0,
    )
    token = str(invite.get("invite_token") or "")
    if not token:
        raise ValueError("Agent WebSocket invite was not created.")
    return token


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    temp_path.replace(path)


def _unique_agent_id(
    meeting: dict[str, object],
    *,
    provider: FrontendLiveAgentProvider,
    display_name: str,
) -> str:
    existing_ids = {
        str(role.get("id") or "")
        for role in _as_dict_list(meeting.get("roles"))
    } | {
        str(binding.get("agent_id") or "")
        for binding in _as_dict_list(meeting.get("agent_bindings"))
    }
    for _ in range(20):
        agent_id = _preview_agent_id(provider, display_name)
        if agent_id not in existing_ids:
            return agent_id
    raise ValueError("Could not allocate a unique agent id.")


def _preview_agent_id(provider: FrontendLiveAgentProvider, display_name: str) -> str:
    stem = _slug(display_name) or provider.id
    return f"{provider.id}-{stem}-{uuid4().hex[:8]}"[:64].rstrip("-")


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")


def _safe_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip(".-") or "live-agent"


def _engagement_mode(value: object) -> str:
    mode = clean_lobby_text(value, limit=64)
    return mode if mode in {"manual", "mentioned", "moderator_called", "human_only", "always", "watch", "flow"} else "mentioned"


def _payload_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes", "on"}
    return bool(value)


def _payload_nonnegative_int(value: object, default: int) -> int:
    if value is None or value == "":
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(0, parsed)


def _payload_nonnegative_float(value: object, default: float) -> float:
    if value is None or value == "":
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, parsed)


def _as_dict_list(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _as_dict_map(value: object) -> dict[str, dict[str, object]]:
    if isinstance(value, dict):
        return {
            str(key): dict(item)
            for key, item in value.items()
            if str(key) and isinstance(item, dict)
        }
    result: dict[str, dict[str, object]] = {}
    for item in _as_dict_list(value):
        item_id = str(item.get("id") or "").strip()
        if item_id:
            result[item_id] = item
    return result


def _preflight_failure_message(preflight: dict[str, object]) -> str:
    agents = preflight.get("agents") if isinstance(preflight.get("agents"), list) else []
    for agent in agents:
        if not isinstance(agent, dict):
            continue
        checks = agent.get("checks") if isinstance(agent.get("checks"), list) else []
        for check in checks:
            if isinstance(check, dict) and check.get("status") == "failed":
                return str(check.get("message") or "Agent connection check failed.")
    checks = preflight.get("checks") if isinstance(preflight.get("checks"), list) else []
    for check in checks:
        if isinstance(check, dict) and check.get("status") == "failed":
            return str(check.get("message") or "Agent connection check failed.")
    return "Agent connection check failed."


def _auth_action_payload(provider: FrontendLiveAgentProvider, preflight: dict[str, object]) -> dict[str, str]:
    if not provider.login_command:
        return {}
    failed_check_ids = set(_failed_preflight_check_ids(preflight))
    if failed_check_ids & {"codex_auth", "cursor_auth", "grok_auth", "antigravity_auth"}:
        return {"provider_id": provider.id, "label": f"{provider.label} 로그인 열기"}
    return {}


def _failed_preflight_check_ids(preflight: dict[str, object]) -> list[str]:
    ids: list[str] = []
    agents = preflight.get("agents") if isinstance(preflight.get("agents"), list) else []
    for agent in agents:
        if not isinstance(agent, dict):
            continue
        checks = agent.get("checks") if isinstance(agent.get("checks"), list) else []
        ids.extend(
            str(check.get("id") or "")
            for check in checks
            if isinstance(check, dict) and check.get("status") == "failed"
        )
    checks = preflight.get("checks") if isinstance(preflight.get("checks"), list) else []
    ids.extend(
        str(check.get("id") or "")
        for check in checks
        if isinstance(check, dict) and check.get("status") == "failed"
    )
    return [check_id for check_id in ids if check_id]


def _resolved_login_command(
    command: Sequence[str],
    *,
    command_resolver: Callable[[str], str | None],
) -> list[str]:
    parts = [str(part).strip() for part in command if str(part).strip()]
    if not parts:
        raise ValueError("Provider login command is not configured.")
    resolved = command_resolver(parts[0])
    if not resolved:
        raise ValueError(f"{parts[0]} command was not found. Install or configure the provider CLI first.")
    return [resolved, *parts[1:]]


def _launch_login_command(command: list[str]) -> None:
    if platform.system() == "Darwin" and shutil.which("osascript"):
        shell_command = shlex.join(command)
        script = f'tell application "Terminal" to do script {json.dumps(shell_command)}'
        subprocess.Popen(
            ["osascript", "-e", script],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return
    subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
