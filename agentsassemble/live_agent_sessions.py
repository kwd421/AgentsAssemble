from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from agentsassemble.config import load_council_config
from agentsassemble.live_agent_meetings import start_live_agent_meeting
from agentsassemble.live_agent_preflight import preflight_live_agent_config
from agentsassemble.live_agent_processes import clean_live_agent_group_id
from agentsassemble.live_agent_runner import load_group_configs
from agentsassemble.live_agents import connect_live_agent, heartbeat_live_agent, read_live_agents
from agentsassemble.meeting_events import clean_lobby_text
from agentsassemble.meeting_setup import prepare_meeting_setup

SUPPORTED_SESSION_CONNECTION_KINDS = frozenset({"local_cli", "live_session", "remote_bridge"})


class LiveAgentSessionStartError(ValueError):
    def __init__(self, message: str, *, meeting_id: str) -> None:
        super().__init__(message)
        self.meeting_id = meeting_id


class LiveAgentSessionStopError(ValueError):
    def __init__(self, message: str, *, meeting_id: str) -> None:
        super().__init__(message)
        self.meeting_id = meeting_id


class LiveAgentSessionRestartError(ValueError):
    def __init__(self, message: str, *, meeting_id: str) -> None:
        super().__init__(message)
        self.meeting_id = meeting_id


class LiveAgentSessionRecoverError(ValueError):
    def __init__(self, message: str, *, meeting_id: str) -> None:
        super().__init__(message)
        self.meeting_id = meeting_id


def start_live_agent_session(
    output_root: Path,
    process_supervisor: object,
    *,
    server: str,
    council_config_path: Path | None = None,
    agent_config_path: Path | None = None,
    live_agent_config_path: Path,
    meeting_id: str = "",
    group_id: str = "",
    connect_timeout_seconds: float = 5.0,
    auto_restart: bool = False,
    max_restarts: int = 0,
    restart_backoff_seconds: float = 5.0,
    stale_restart_after_seconds: float = 0.0,
    diagnostic: bool = False,
    preflight_checker: Callable[..., dict[str, object]] | None = None,
) -> dict[str, object]:
    preflight = (preflight_checker or preflight_live_agent_config)(
        live_agent_config_path,
        server_override=server,
    )
    if preflight.get("status") != "ok":
        raise ValueError(_preflight_failure_message(preflight))

    expected_agents = _expected_meeting_agents(
        council_config_path=council_config_path,
        agent_config_path=agent_config_path,
    )
    expected_agent_ids = [agent["agent_id"] for agent in expected_agents]
    resident_configs = load_group_configs(live_agent_config_path, server_override=server)
    _validate_resident_config_meeting_ids(resident_configs, meeting_id=meeting_id)
    _validate_resident_manifest(resident_configs, expected_agents)
    requested_meeting_id = _safe_optional_meeting_id(meeting_id)
    if requested_meeting_id:
        _validate_existing_process_group_owner(
            process_supervisor,
            clean_live_agent_group_id(group_id.strip() or live_agent_config_path.stem),
            requested_meeting_id,
            action="start",
        )

    started_meeting = start_live_agent_meeting(
        output_root,
        council_config_path=council_config_path,
        agent_config_path=agent_config_path,
        meeting_id=meeting_id,
    )
    clean_meeting_id = str(started_meeting.get("meeting_id") or "")
    if diagnostic:
        _mark_expected_agents_diagnostic(output_root, expected_agent_ids)
    try:
        group = process_supervisor.start_group(
            config_path=live_agent_config_path,
            server=server,
            group_id=group_id.strip() or None,
            meeting_id=clean_meeting_id,
            auto_restart=auto_restart,
            max_restarts=max_restarts,
            restart_backoff_seconds=restart_backoff_seconds,
            stale_restart_after_seconds=stale_restart_after_seconds,
            diagnostic=diagnostic,
        )
    except Exception as error:
        raise LiveAgentSessionStartError(
            _start_group_failure_message(error),
            meeting_id=clean_meeting_id,
        ) from error
    process = _process_snapshot(group, expected_agent_ids=expected_agent_ids, meeting_id=clean_meeting_id)
    connection = _wait_for_connections(
        output_root,
        meeting_id=clean_meeting_id,
        expected_agent_ids=expected_agent_ids,
        process_group=group,
        timeout_seconds=connect_timeout_seconds,
    )
    status = "ready" if process["ready"] and connection["connected"] == connection["expected"] else "starting"
    return {
        "status": status,
        "meeting_id": clean_meeting_id,
        "group_id": str(group.get("group_id") if isinstance(group, dict) else group_id or ""),
        "meeting": _safe_meeting_summary(started_meeting.get("meeting")),
        "group": _safe_group_summary(group),
        "process": process,
        "connection": connection,
    }


def resume_live_agent_session(
    output_root: Path,
    process_supervisor: object,
    *,
    server: str,
    live_agent_config_path: Path,
    meeting_id: str,
    group_id: str = "",
    connect_timeout_seconds: float = 5.0,
    auto_restart: bool = False,
    max_restarts: int = 0,
    restart_backoff_seconds: float = 5.0,
    stale_restart_after_seconds: float = 0.0,
    preflight_checker: Callable[..., dict[str, object]] | None = None,
) -> dict[str, object]:
    clean_meeting_id = _clean_existing_meeting_id(meeting_id)
    meeting_dir = _existing_meeting_dir(output_root, clean_meeting_id)
    meeting = _read_existing_meeting(meeting_dir)
    expected_agents = _expected_agents_from_meeting(meeting)
    expected_agent_ids = [agent["agent_id"] for agent in expected_agents]

    preflight = (preflight_checker or preflight_live_agent_config)(
        live_agent_config_path,
        server_override=server,
    )
    if preflight.get("status") != "ok":
        raise ValueError(_preflight_failure_message(preflight))

    resident_configs = load_group_configs(live_agent_config_path, server_override=server)
    _validate_resident_config_meeting_ids(resident_configs, meeting_id=clean_meeting_id)
    _validate_resident_manifest(resident_configs, expected_agents)
    clean_group_id = clean_live_agent_group_id(group_id.strip() or live_agent_config_path.stem)
    _validate_existing_process_group_owner(
        process_supervisor,
        clean_group_id,
        clean_meeting_id,
        action="resume",
    )
    _ensure_bound_agent_roster(
        output_root,
        meeting,
        resident_configs,
        meeting_id=clean_meeting_id,
        expected_agent_ids=expected_agent_ids,
    )
    group = _resume_process_group(
        process_supervisor,
        live_agent_config_path=live_agent_config_path,
        server=server,
        group_id=clean_group_id,
        meeting_id=clean_meeting_id,
        auto_restart=auto_restart,
        max_restarts=max_restarts,
        restart_backoff_seconds=restart_backoff_seconds,
        stale_restart_after_seconds=stale_restart_after_seconds,
    )
    process = _process_snapshot(group, expected_agent_ids=expected_agent_ids, meeting_id=clean_meeting_id)
    connection = _wait_for_connections(
        output_root,
        meeting_id=clean_meeting_id,
        expected_agent_ids=expected_agent_ids,
        process_group=group,
        timeout_seconds=connect_timeout_seconds,
    )
    status = "ready" if process["ready"] and connection["connected"] == connection["expected"] else "starting"
    return {
        "status": status,
        "meeting_id": clean_meeting_id,
        "group_id": str(group.get("group_id") if isinstance(group, dict) else group_id or ""),
        "meeting": _safe_meeting_summary(meeting),
        "group": _safe_group_summary(group),
        "process": process,
        "connection": connection,
    }


def check_live_agent_session(
    output_root: Path,
    process_supervisor: object,
    *,
    meeting_id: str,
    group_id: str,
) -> dict[str, object]:
    clean_meeting_id = _clean_existing_meeting_id(meeting_id)
    if not str(group_id or "").strip():
        raise ValueError("Live agent group id is required.")
    clean_group_id = clean_live_agent_group_id(group_id)
    meeting_dir = _existing_meeting_dir(output_root, clean_meeting_id)
    meeting = _read_existing_meeting(meeting_dir)
    expected_agents = _expected_agents_from_meeting(meeting)
    expected_agent_ids = [agent["agent_id"] for agent in expected_agents]
    group = _snapshot_process_group(process_supervisor, clean_group_id)
    process = _check_process_snapshot(group, expected_agent_ids=expected_agent_ids, meeting_id=clean_meeting_id)
    connection = _connection_snapshot(
        output_root,
        meeting_id=clean_meeting_id,
        expected_agent_ids=expected_agent_ids,
        process_group=group,
    )
    status = "ready" if process["ready"] and connection["connected"] == connection["expected"] else "degraded"
    return {
        "status": status,
        "meeting_id": clean_meeting_id,
        "group_id": str(group.get("group_id") if isinstance(group, dict) and group.get("group_id") else clean_group_id),
        "meeting": _safe_meeting_summary(meeting),
        "group": _safe_group_summary(group),
        "process": process,
        "connection": connection,
    }


def restart_live_agent_session(
    output_root: Path,
    process_supervisor: object,
    *,
    meeting_id: str,
    group_id: str,
    connect_timeout_seconds: float = 5.0,
) -> dict[str, object]:
    clean_meeting_id = _clean_existing_meeting_id(meeting_id)
    if not str(group_id or "").strip():
        raise ValueError("Live agent group id is required.")
    clean_group_id = clean_live_agent_group_id(group_id)
    meeting_dir = _existing_meeting_dir(output_root, clean_meeting_id)
    meeting = _read_existing_meeting(meeting_dir)
    expected_agents = _expected_agents_from_meeting(meeting)
    expected_agent_ids = [agent["agent_id"] for agent in expected_agents]
    existing_group = _validate_restart_group_matches_meeting(
        process_supervisor,
        clean_group_id,
        expected_agent_ids,
        meeting_id=clean_meeting_id,
    )
    _validate_restart_persisted_config(
        existing_group,
        group_id=clean_group_id,
        expected_agents=expected_agents,
        meeting_id=clean_meeting_id,
        preflight_checker=getattr(process_supervisor, "preflight_checker", None),
    )
    existing_status = str(existing_group.get("status") or "unknown")
    if existing_status in {"running", "restarting"}:
        try:
            process_supervisor.stop_group(clean_group_id)
        except Exception as error:
            raise LiveAgentSessionRestartError(
                _stop_group_failure_message(error, group_id=clean_group_id),
                meeting_id=clean_meeting_id,
            ) from error
    offline = _mark_bound_agents_offline(
        output_root,
        meeting,
        meeting_id=clean_meeting_id,
        expected_agent_ids=expected_agent_ids,
    )
    try:
        group = process_supervisor.restart_group(clean_group_id)
    except Exception as error:
        raise LiveAgentSessionRestartError(
            _restart_group_failure_message(error, group_id=clean_group_id),
            meeting_id=clean_meeting_id,
        ) from error
    process = _check_process_snapshot(group, expected_agent_ids=expected_agent_ids, meeting_id=clean_meeting_id)
    connection = _wait_for_connections(
        output_root,
        meeting_id=clean_meeting_id,
        expected_agent_ids=expected_agent_ids,
        process_group=group,
        timeout_seconds=connect_timeout_seconds,
    )
    status = "ready" if process["ready"] and connection["connected"] == connection["expected"] else "starting"
    return {
        "status": status,
        "meeting_id": clean_meeting_id,
        "group_id": str(group.get("group_id") if isinstance(group, dict) else clean_group_id),
        "meeting": _safe_meeting_summary(meeting),
        "group": _safe_group_summary(group),
        "process": process,
        "offline": offline,
        "connection": connection,
    }


def recover_live_agent_session(
    output_root: Path,
    process_supervisor: object,
    *,
    meeting_id: str,
    group_id: str,
    connect_timeout_seconds: float = 5.0,
) -> dict[str, object]:
    clean_meeting_id = _clean_existing_meeting_id(meeting_id)
    if not str(group_id or "").strip():
        raise ValueError("Live agent group id is required.")
    clean_group_id = clean_live_agent_group_id(group_id)
    meeting_dir = _existing_meeting_dir(output_root, clean_meeting_id)
    meeting = _read_existing_meeting(meeting_dir)
    expected_agents = _expected_agents_from_meeting(meeting)
    expected_agent_ids = [agent["agent_id"] for agent in expected_agents]
    _validate_recover_group_matches_meeting(
        process_supervisor,
        clean_group_id,
        expected_agent_ids,
        meeting_id=clean_meeting_id,
    )
    offline = _mark_bound_agents_offline(
        output_root,
        meeting,
        meeting_id=clean_meeting_id,
        expected_agent_ids=expected_agent_ids,
    )
    try:
        group = process_supervisor.recover_group(clean_group_id)
    except Exception as error:
        raise LiveAgentSessionRecoverError(
            _recover_group_failure_message(error, group_id=clean_group_id),
            meeting_id=clean_meeting_id,
        ) from error
    process = _check_process_snapshot(group, expected_agent_ids=expected_agent_ids, meeting_id=clean_meeting_id)
    connection = _wait_for_connections(
        output_root,
        meeting_id=clean_meeting_id,
        expected_agent_ids=expected_agent_ids,
        process_group=group,
        timeout_seconds=connect_timeout_seconds,
    )
    status = "ready" if process["ready"] and connection["connected"] == connection["expected"] else "starting"
    return {
        "status": status,
        "meeting_id": clean_meeting_id,
        "group_id": str(group.get("group_id") if isinstance(group, dict) else clean_group_id),
        "meeting": _safe_meeting_summary(meeting),
        "group": _safe_group_summary(group),
        "process": process,
        "offline": offline,
        "connection": connection,
    }


def stop_live_agent_session(
    output_root: Path,
    process_supervisor: object,
    *,
    meeting_id: str,
    group_id: str,
) -> dict[str, object]:
    clean_meeting_id = _clean_existing_meeting_id(meeting_id)
    if not str(group_id or "").strip():
        raise ValueError("Live agent group id is required.")
    clean_group_id = clean_live_agent_group_id(group_id)
    meeting_dir = _existing_meeting_dir(output_root, clean_meeting_id)
    meeting = _read_existing_meeting(meeting_dir)
    expected_agents = _expected_agents_from_meeting(meeting)
    expected_agent_ids = [agent["agent_id"] for agent in expected_agents]
    _validate_stop_group_matches_meeting(
        process_supervisor,
        clean_group_id,
        expected_agent_ids,
        meeting_id=clean_meeting_id,
    )
    try:
        group = process_supervisor.stop_group(clean_group_id)
    except Exception as error:
        raise LiveAgentSessionStopError(
            _stop_group_failure_message(error, group_id=clean_group_id),
            meeting_id=clean_meeting_id,
        ) from error
    offline = _mark_bound_agents_offline(
        output_root,
        meeting,
        meeting_id=clean_meeting_id,
        expected_agent_ids=expected_agent_ids,
    )
    process = _stop_process_snapshot(group, expected_agent_ids=expected_agent_ids, meeting_id=clean_meeting_id)
    group_status = str(group.get("status") if isinstance(group, dict) else "unknown") or "unknown"
    status = (
        "stopped"
        if group_status in {"stopped", "error"} and int(offline.get("offline") or 0) == int(offline.get("expected") or 0)
        else "stopping"
    )
    return {
        "status": status,
        "meeting_id": clean_meeting_id,
        "group_id": str(group.get("group_id") if isinstance(group, dict) else clean_group_id),
        "meeting": _safe_meeting_summary(meeting),
        "group": _safe_group_summary(group),
        "process": process,
        "offline": offline,
    }


def _expected_meeting_agents(
    *,
    council_config_path: Path | None,
    agent_config_path: Path | None,
) -> list[dict[str, str]]:
    config = load_council_config(council_config_path)
    setup = prepare_meeting_setup(config.roles, "mock", None, True, agent_config_path)
    agents = []
    for binding in setup.agent_bindings:
        provider = setup.providers.get(binding.provider_id)
        agents.append(
            {
                "agent_id": binding.agent_id,
                "provider_kind": str(getattr(provider, "kind", "") or ""),
            }
        )
    return agents


def _expected_agents_from_meeting(meeting: dict[str, object]) -> list[dict[str, str]]:
    bindings = meeting.get("agent_bindings") if isinstance(meeting.get("agent_bindings"), list) else []
    providers = meeting.get("provider_configs") if isinstance(meeting.get("provider_configs"), dict) else {}
    agents = []
    for binding in bindings:
        if not isinstance(binding, dict):
            continue
        agent_id = str(binding.get("agent_id") or "").strip()
        provider_id = str(binding.get("provider_id") or "").strip()
        provider = providers.get(provider_id) if isinstance(providers, dict) else None
        provider_kind = str(provider.get("kind") or "") if isinstance(provider, dict) else ""
        if agent_id:
            agents.append({"agent_id": agent_id, "provider_kind": provider_kind})
    if not agents:
        raise ValueError("Meeting has no bound live agents to resume.")
    return agents


def _validate_resident_manifest(configs: object, expected_agents: list[dict[str, str]]) -> None:
    config_agent_ids = [str(config.agent_id) for config in configs]
    duplicate_agent_ids = _duplicate_agent_ids(config_agent_ids)
    if duplicate_agent_ids:
        raise ValueError(f"Resident group config does not match meeting agents: duplicate {', '.join(duplicate_agent_ids)}.")
    configs_by_id = {str(config.agent_id): config for config in configs}
    expected_agent_ids = [agent["agent_id"] for agent in expected_agents]
    manifest_agent_ids = set(configs_by_id)
    missing_agent_ids = [agent_id for agent_id in expected_agent_ids if agent_id not in manifest_agent_ids]
    if missing_agent_ids:
        raise ValueError(f"Resident group config does not cover meeting agents: {', '.join(missing_agent_ids)}.")
    extra_agent_ids = sorted(manifest_agent_ids - set(expected_agent_ids))
    if extra_agent_ids:
        raise ValueError(f"Resident group config does not match meeting agents: extra {', '.join(extra_agent_ids)}.")
    for expected in expected_agents:
        agent_id = expected["agent_id"]
        config = configs_by_id[agent_id]
        expected_provider_kind = expected["provider_kind"]
        actual_provider_kind = str(getattr(config, "provider_kind", "") or "")
        if _requires_resident_provider_kind_match(expected_provider_kind) and actual_provider_kind != expected_provider_kind:
            raise ValueError(
                "Resident group config provider_kind mismatch for "
                f"{agent_id}: expected {expected_provider_kind}, got {actual_provider_kind or 'blank'}."
            )
        allowed_connection_kinds = _allowed_resident_connection_kinds(expected_provider_kind)
        actual_connection_kind = str(getattr(config, "connection_kind", "") or "")
        if actual_connection_kind not in allowed_connection_kinds:
            expected_kinds = ", ".join(sorted(allowed_connection_kinds))
            raise ValueError(
                "Resident group config connection_kind mismatch for "
                f"{agent_id}: expected one of {expected_kinds}, got {actual_connection_kind or 'blank'}."
            )


def _allowed_resident_connection_kinds(provider_kind: str) -> frozenset[str]:
    if provider_kind == "remote_http_bridge":
        return frozenset({"remote_bridge"})
    if provider_kind == "codex_live_session":
        return frozenset({"live_session"})
    if provider_kind == "local_cli":
        return frozenset({"local_cli", "live_session"})
    return SUPPORTED_SESSION_CONNECTION_KINDS


def _requires_resident_provider_kind_match(provider_kind: str) -> bool:
    return provider_kind not in {"remote_http_bridge"}


def _validate_resident_config_meeting_ids(configs: object, *, meeting_id: str) -> None:
    clean_meeting_id = str(meeting_id or "").strip()
    for config in configs:
        config_meeting_id = str(getattr(config, "meeting_id", "") or "").strip()
        if not config_meeting_id:
            continue
        agent_id = str(getattr(config, "agent_id", "") or "unknown")
        if not clean_meeting_id:
            raise ValueError(
                f"Resident config for {agent_id} has a meeting id, but the session meeting id is generated."
            )
        if config_meeting_id != clean_meeting_id:
            raise ValueError(
                f"Resident config for {agent_id} meeting id does not match session meeting id {clean_meeting_id}."
            )


def _validate_restart_persisted_config(
    group: dict[str, object],
    *,
    group_id: str,
    expected_agents: list[dict[str, str]],
    meeting_id: str,
    preflight_checker: Callable[..., dict[str, object]] | None = None,
) -> None:
    config_path_value = str(group.get("config_path") or "").strip()
    if not config_path_value and "config_path" not in group:
        return
    if not config_path_value:
        raise ValueError(f"Live agent group {group_id} has no config to restart.")
    config_path = Path(config_path_value)
    server = str(group.get("server") or "").strip()
    if not server:
        raise ValueError(f"Live agent group {group_id} has no server to restart.")
    checker = preflight_checker if callable(preflight_checker) else preflight_live_agent_config
    preflight = checker(config_path, server_override=server)
    if preflight.get("status") != "ok":
        raise ValueError(_preflight_failure_message(preflight))
    resident_configs = load_group_configs(config_path, server_override=server)
    _validate_resident_config_meeting_ids(resident_configs, meeting_id=meeting_id)
    _validate_resident_manifest(resident_configs, expected_agents)


def _existing_meeting_dir(output_root: Path, meeting_id: str) -> Path:
    meetings_root = (output_root / "meetings").resolve()
    meeting_dir = (meetings_root / meeting_id).resolve()
    try:
        meeting_dir.relative_to(meetings_root)
    except ValueError as error:
        raise ValueError(f"Meeting {meeting_id} was not found.") from error
    if not meeting_dir.exists() or not meeting_dir.is_dir():
        raise ValueError(f"Meeting {meeting_id} was not found.")
    if not (meeting_dir / "live_state.json").exists() and not (meeting_dir / "meeting.json").exists():
        raise ValueError(f"Meeting {meeting_id} was not found.")
    return meeting_dir


def _clean_existing_meeting_id(meeting_id: str) -> str:
    clean_meeting_id = clean_lobby_text(meeting_id, limit=128)
    if not clean_meeting_id or clean_meeting_id in {".", ".."}:
        raise ValueError(f"Meeting {clean_meeting_id or '(blank)'} was not found.")
    if "/" in clean_meeting_id or "\\" in clean_meeting_id or Path(clean_meeting_id).name != clean_meeting_id:
        raise ValueError(f"Meeting {clean_meeting_id} was not found.")
    return clean_meeting_id


def _read_existing_meeting(meeting_dir: Path) -> dict[str, object]:
    for path in (meeting_dir / "live_state.json", meeting_dir / "meeting.json"):
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    raise ValueError(f"Meeting {meeting_dir.name} was not found.")


def _ensure_bound_agent_roster(
    output_root: Path,
    meeting: dict[str, object],
    configs: object,
    *,
    meeting_id: str,
    expected_agent_ids: list[str],
) -> None:
    agents_by_id = {str(agent.get("agent_id") or ""): agent for agent in read_live_agents(output_root)}
    roles_by_id = _meeting_roles_by_id(meeting)
    bindings_by_agent = _meeting_bindings_by_agent_id(meeting)
    configs_by_id = {str(getattr(config, "agent_id", "") or ""): config for config in configs}
    for agent_id in expected_agent_ids:
        existing = agents_by_id.get(agent_id)
        if existing is not None and str(existing.get("meeting_id") or "") == meeting_id:
            continue
        config = configs_by_id.get(agent_id)
        binding = bindings_by_agent.get(agent_id, {})
        role = roles_by_id.get(str(binding.get("role_id") or ""), {})
        connect_live_agent(
            output_root,
            {
                "agent_id": agent_id,
                "display_name": str(getattr(config, "display_name", "") or role.get("display_name") or agent_id),
                "provider_kind": str(getattr(config, "provider_kind", "") or "manual"),
                "connection_kind": str(getattr(config, "connection_kind", "") or "manual"),
                "session_id": str(getattr(config, "session_id", "") or binding.get("session_id") or ""),
                "endpoint": str(getattr(config, "endpoint", "") or ""),
                "meeting_id": meeting_id,
                "engagement_mode": "moderator_called",
                "status": "offline",
                "capabilities": ["room_chat", "official_turn"],
            },
        )


def _meeting_roles_by_id(meeting: dict[str, object]) -> dict[str, dict[str, object]]:
    roles = meeting.get("roles") if isinstance(meeting.get("roles"), list) else []
    return {
        str(role.get("id") or ""): role
        for role in roles
        if isinstance(role, dict) and str(role.get("id") or "")
    }


def _meeting_bindings_by_agent_id(meeting: dict[str, object]) -> dict[str, dict[str, object]]:
    bindings = meeting.get("agent_bindings") if isinstance(meeting.get("agent_bindings"), list) else []
    return {
        str(binding.get("agent_id") or ""): binding
        for binding in bindings
        if isinstance(binding, dict) and str(binding.get("agent_id") or "")
    }


def _mark_expected_agents_diagnostic(output_root: Path, expected_agent_ids: list[str]) -> None:
    for agent_id in expected_agent_ids:
        connect_live_agent(output_root, {"agent_id": agent_id, "diagnostic": True})


def _mark_bound_agents_offline(
    output_root: Path,
    meeting: dict[str, object],
    *,
    meeting_id: str,
    expected_agent_ids: list[str],
) -> dict[str, object]:
    agents_by_id = {str(agent.get("agent_id") or ""): agent for agent in read_live_agents(output_root)}
    roles_by_id = _meeting_roles_by_id(meeting)
    bindings_by_agent = _meeting_bindings_by_agent_id(meeting)
    providers = meeting.get("provider_configs") if isinstance(meeting.get("provider_configs"), dict) else {}
    offline_agent_ids = []
    attention = []
    for agent_id in expected_agent_ids:
        existing = agents_by_id.get(agent_id)
        if existing is not None and str(existing.get("meeting_id") or "") != meeting_id:
            attention.append(f"{agent_id}:wrong_meeting")
            continue
        if existing is None:
            binding = bindings_by_agent.get(agent_id, {})
            role = roles_by_id.get(str(binding.get("role_id") or ""), {})
            provider = providers.get(str(binding.get("provider_id") or "")) if isinstance(providers, dict) else None
            provider_kind = str(provider.get("kind") or "") if isinstance(provider, dict) else "manual"
            try:
                connect_live_agent(
                    output_root,
                    {
                        "agent_id": agent_id,
                        "display_name": str(role.get("display_name") or agent_id),
                        "provider_kind": provider_kind,
                        "connection_kind": _resident_connection_kind_for_provider(provider_kind),
                        "endpoint": str(provider.get("endpoint") or "") if isinstance(provider, dict) else "",
                        "session_id": str(binding.get("session_id") or ""),
                        "meeting_id": meeting_id,
                        "engagement_mode": "moderator_called",
                        "status": "offline",
                        "capabilities": ["room_chat", "official_turn"],
                    },
                )
            except ValueError:
                attention.append(f"{agent_id}:offline_record_failed")
                continue
        else:
            heartbeat_live_agent(output_root, agent_id, status="offline")
        offline_agent_ids.append(agent_id)
    return {
        "expected": len(expected_agent_ids),
        "offline": len(offline_agent_ids),
        "agent_ids": expected_agent_ids,
        "offline_agent_ids": offline_agent_ids,
        "attention": attention,
    }


def _resident_connection_kind_for_provider(provider_kind: str) -> str:
    if provider_kind == "remote_http_bridge":
        return "remote_bridge"
    if provider_kind == "codex_live_session":
        return "live_session"
    if provider_kind == "local_cli":
        return "local_cli"
    return "manual"


def _resume_process_group(
    process_supervisor: object,
    *,
    live_agent_config_path: Path,
    server: str,
    group_id: str,
    meeting_id: str,
    auto_restart: bool,
    max_restarts: int,
    restart_backoff_seconds: float,
    stale_restart_after_seconds: float,
) -> dict[str, object]:
    clean_group_id = clean_live_agent_group_id(group_id)
    existing_group = _find_process_group(process_supervisor, clean_group_id)
    if existing_group:
        _validate_process_group_owner(existing_group, clean_group_id, meeting_id, action="resume")
    existing_status = str(existing_group.get("status") or "") if existing_group else ""
    if existing_status == "running":
        return existing_group
    return process_supervisor.start_group(
        config_path=live_agent_config_path,
        server=server,
        group_id=clean_group_id,
        meeting_id=meeting_id,
        auto_restart=auto_restart,
        max_restarts=max_restarts,
        restart_backoff_seconds=restart_backoff_seconds,
        stale_restart_after_seconds=stale_restart_after_seconds,
    )


def _find_process_group(process_supervisor: object, group_id: str) -> dict[str, object]:
    if not hasattr(process_supervisor, "list_groups"):
        return {}
    groups = process_supervisor.list_groups()
    return _find_group_in_list(groups, group_id)


def _snapshot_process_group(process_supervisor: object, group_id: str) -> dict[str, object]:
    if not hasattr(process_supervisor, "snapshot_groups"):
        return {}
    groups = process_supervisor.snapshot_groups()
    return _find_group_in_list(groups, group_id)


def _find_group_in_list(groups: object, group_id: str) -> dict[str, object]:
    if not isinstance(groups, list):
        return {}
    for group in groups:
        if isinstance(group, dict) and str(group.get("group_id") or "") == group_id:
            return group
    return {}


def _validate_existing_process_group_owner(
    process_supervisor: object,
    group_id: str,
    meeting_id: str,
    *,
    action: str,
) -> None:
    group = _snapshot_process_group(process_supervisor, group_id)
    if not group:
        group = _find_process_group(process_supervisor, group_id)
    if group:
        _validate_process_group_owner(group, group_id, meeting_id, action=action)


def _validate_process_group_owner(group: dict[str, object], group_id: str, meeting_id: str, *, action: str) -> None:
    if not _process_group_owner_conflicts(group, meeting_id):
        return
    raise ValueError(
        f"Live agent group {group_id} belongs to a different meeting; "
        f"refusing session {action} for {meeting_id}."
    )


def _process_group_owner_conflicts(group: dict[str, object], meeting_id: str) -> bool:
    raw_owner = clean_lobby_text(group.get("meeting_id"), limit=128)
    if not raw_owner:
        return False
    safe_owner = _safe_optional_meeting_id(raw_owner)
    return not safe_owner or safe_owner != meeting_id


def _wait_for_connections(
    output_root: Path,
    *,
    meeting_id: str,
    expected_agent_ids: list[str],
    process_group: object = None,
    timeout_seconds: float,
) -> dict[str, object]:
    timeout = max(0.0, float(timeout_seconds))
    deadline = time.monotonic() + timeout
    while True:
        connection = _connection_snapshot(
            output_root,
            meeting_id=meeting_id,
            expected_agent_ids=expected_agent_ids,
            process_group=process_group,
        )
        if connection["connected"] == connection["expected"] or time.monotonic() >= deadline:
            return connection
        time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))


def _connection_snapshot(
    output_root: Path,
    *,
    meeting_id: str,
    expected_agent_ids: list[str],
    process_group: object = None,
) -> dict[str, object]:
    agents = {str(agent.get("agent_id") or ""): agent for agent in read_live_agents(output_root)}
    group_payload = process_group if isinstance(process_group, dict) else {}
    connected_agent_ids = []
    attention = []
    for agent_id in expected_agent_ids:
        agent = agents.get(agent_id)
        if agent is None:
            attention.append(f"{agent_id}:missing")
            continue
        agent_meeting_id = str(agent.get("meeting_id") or "")
        status = str(agent.get("status") or "unknown")
        if agent_meeting_id != meeting_id:
            attention.append(f"{agent_id}:wrong_meeting")
            continue
        if _agent_last_seen_before_process_start(agent, group_payload):
            attention.append(f"{agent_id}:not_reconnected")
            continue
        if status in {"online", "working"}:
            connected_agent_ids.append(agent_id)
        else:
            attention.append(f"{agent_id}:{status}")
    return {
        "expected": len(expected_agent_ids),
        "connected": len(connected_agent_ids),
        "agent_ids": expected_agent_ids,
        "connected_agent_ids": connected_agent_ids,
        "attention": attention,
    }


def _agent_last_seen_before_process_start(agent: dict[str, object], group: dict[str, object]) -> bool:
    group_started_at = _parse_public_timestamp(group.get("started_at"))
    agent_last_seen_at = _parse_public_timestamp(agent.get("last_seen_at"))
    if group_started_at is None or agent_last_seen_at is None:
        return False
    return agent_last_seen_at < group_started_at


def _parse_public_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _validate_stop_group_matches_meeting(
    process_supervisor: object,
    group_id: str,
    expected_agent_ids: list[str],
    *,
    meeting_id: str,
) -> None:
    group = _find_process_group(process_supervisor, group_id)
    if not group:
        return
    _validate_process_group_owner(group, group_id, meeting_id, action="stop")
    manifest_agent_ids = _process_agent_ids(group.get("agents"))
    if not manifest_agent_ids:
        raise ValueError(f"Live agent group {group_id} has no agent manifest; refusing session stop.")
    duplicate_agent_ids = _duplicate_agent_ids(manifest_agent_ids)
    if duplicate_agent_ids:
        raise ValueError(
            f"Live agent group {group_id} does not match meeting agents: "
            f"duplicate {', '.join(duplicate_agent_ids)}."
        )
    expected = set(expected_agent_ids)
    actual = set(manifest_agent_ids)
    if actual == expected:
        return
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    details = []
    if missing:
        details.append(f"missing {', '.join(missing)}")
    if extra:
        details.append(f"extra {', '.join(extra)}")
    suffix = "; ".join(details) if details else "different agent manifest"
    raise ValueError(f"Live agent group {group_id} does not match meeting agents: {suffix}.")


def _validate_restart_group_matches_meeting(
    process_supervisor: object,
    group_id: str,
    expected_agent_ids: list[str],
    *,
    meeting_id: str,
) -> dict[str, object]:
    return _validate_process_group_manifest_matches_meeting(
        process_supervisor,
        group_id,
        expected_agent_ids,
        meeting_id=meeting_id,
        action="restart",
    )


def _validate_recover_group_matches_meeting(
    process_supervisor: object,
    group_id: str,
    expected_agent_ids: list[str],
    *,
    meeting_id: str,
) -> dict[str, object]:
    group = _validate_process_group_manifest_matches_meeting(
        process_supervisor,
        group_id,
        expected_agent_ids,
        meeting_id=meeting_id,
        action="recover",
    )
    status = str(group.get("status") or "unknown") if isinstance(group, dict) else "unknown"
    if status == "running":
        raise ValueError(f"Live agent group {group_id} is already running.")
    if status not in {"unknown", "error"}:
        raise ValueError(f"Live agent group {group_id} is {status}; use restart.")
    if isinstance(group, dict) and "server" in group and not str(group.get("server") or "").strip():
        raise ValueError(f"Live agent group {group_id} has no server to recover.")
    if isinstance(group, dict) and "config_path" in group:
        config_path = str(group.get("config_path") or "").strip()
        if not config_path:
            raise ValueError(f"Live agent group {group_id} has no config to recover.")
        if not Path(config_path).exists():
            raise ValueError(f"Live agent group {group_id} has no recoverable config.")
    return group


def _validate_process_group_manifest_matches_meeting(
    process_supervisor: object,
    group_id: str,
    expected_agent_ids: list[str],
    *,
    meeting_id: str,
    action: str,
) -> dict[str, object]:
    group = _snapshot_process_group(process_supervisor, group_id)
    if not group:
        raise ValueError(f"Live agent group {group_id} was not found.")
    _validate_process_group_owner(group, group_id, meeting_id, action=action)
    manifest_agent_ids = _process_agent_ids(group.get("agents"))
    if not manifest_agent_ids:
        raise ValueError(f"Live agent group {group_id} has no agent manifest; refusing session {action}.")
    duplicate_agent_ids = _duplicate_agent_ids(manifest_agent_ids)
    if duplicate_agent_ids:
        raise ValueError(
            f"Live agent group {group_id} does not match meeting agents: "
            f"duplicate {', '.join(duplicate_agent_ids)}."
        )
    expected = set(expected_agent_ids)
    actual = set(manifest_agent_ids)
    if actual == expected:
        return group
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    details = []
    if missing:
        details.append(f"missing {', '.join(missing)}")
    if extra:
        details.append(f"extra {', '.join(extra)}")
    suffix = "; ".join(details) if details else "different agent manifest"
    raise ValueError(f"Live agent group {group_id} does not match meeting agents: {suffix}.")


def _stop_process_snapshot(group: object, *, expected_agent_ids: list[str], meeting_id: str = "") -> dict[str, object]:
    process = _process_snapshot(group, expected_agent_ids=expected_agent_ids, meeting_id=meeting_id)
    if process["status"] == "running" and "group:running" not in process["attention"]:
        process["attention"] = [*process["attention"], "group:running"]
        process["ready"] = False
    return process


def _check_process_snapshot(group: object, *, expected_agent_ids: list[str], meeting_id: str = "") -> dict[str, object]:
    process = _process_snapshot(group, expected_agent_ids=expected_agent_ids, meeting_id=meeting_id)
    expected = set(expected_agent_ids)
    extra_agent_ids = [agent_id for agent_id in process["agent_ids"] if agent_id not in expected]
    duplicate_agent_ids = _duplicate_agent_ids(process["agent_ids"])
    if not extra_agent_ids and not duplicate_agent_ids:
        return process
    process["attention"] = [
        *process["attention"],
        *(f"{agent_id}:extra_in_group" for agent_id in extra_agent_ids),
        *(f"{agent_id}:duplicate_in_group" for agent_id in duplicate_agent_ids),
    ]
    process["ready"] = False
    return process


def _process_snapshot(group: object, *, expected_agent_ids: list[str], meeting_id: str = "") -> dict[str, object]:
    group_payload = group if isinstance(group, dict) else {}
    status = str(group_payload.get("status") or "unknown")
    manifest_ids = _process_agent_ids(group_payload.get("agents"))
    missing_agent_ids = [agent_id for agent_id in expected_agent_ids if agent_id not in manifest_ids]
    attention = []
    if status != "running":
        attention.append(f"group:{status}")
    if meeting_id and _process_group_owner_conflicts(group_payload, meeting_id):
        attention.append("group:wrong_meeting")
    attention.extend(f"{agent_id}:not_in_group" for agent_id in missing_agent_ids)
    return {
        "ready": not attention,
        "status": status,
        "expected": len(expected_agent_ids),
        "matched": len(expected_agent_ids) - len(missing_agent_ids),
        "agent_ids": manifest_ids,
        "attention": attention,
    }


def _process_agent_ids(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    agent_ids = []
    for item in value:
        if not isinstance(item, dict):
            continue
        agent_id = str(item.get("agent_id") or "").strip()
        if agent_id:
            agent_ids.append(agent_id)
    return agent_ids


def _duplicate_agent_ids(agent_ids: list[str]) -> list[str]:
    seen = set()
    duplicates = []
    for agent_id in agent_ids:
        if agent_id in seen and agent_id not in duplicates:
            duplicates.append(agent_id)
        seen.add(agent_id)
    return duplicates


def _safe_meeting_summary(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    roles = value.get("roles") if isinstance(value.get("roles"), list) else []
    bindings = value.get("agent_bindings") if isinstance(value.get("agent_bindings"), list) else []
    return {
        "meeting_id": str(value.get("meeting_id") or ""),
        "live_status": str(value.get("live_status") or ""),
        "role_count": len(roles),
        "bound_agent_count": len(bindings),
    }


def _safe_group_summary(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    summary = {
        "group_id": str(value.get("group_id") or ""),
        "status": str(value.get("status") or ""),
    }
    meeting_id = _safe_optional_meeting_id(value.get("meeting_id"))
    if meeting_id:
        summary["meeting_id"] = meeting_id
    return summary


def _safe_optional_meeting_id(value: object) -> str:
    meeting_id = clean_lobby_text(value, limit=128)
    if not meeting_id or meeting_id in {".", ".."}:
        return ""
    if "/" in meeting_id or "\\" in meeting_id or Path(meeting_id).name != meeting_id:
        return ""
    return meeting_id


def _preflight_failure_message(report: dict[str, object]) -> str:
    checks = report.get("checks") if isinstance(report.get("checks"), list) else []
    for check in checks:
        if isinstance(check, dict) and check.get("status") == "failed":
            check_id = str(check.get("id") or "check").strip() or "check"
            message = _safe_preflight_message(check.get("message"))
            return f"Live agent preflight failed: {check_id}: {message}"
    agents = report.get("agents") if isinstance(report.get("agents"), list) else []
    for agent in agents:
        if not isinstance(agent, dict):
            continue
        agent_id = str(agent.get("agent_id") or "").strip()
        agent_checks = agent.get("checks") if isinstance(agent.get("checks"), list) else []
        for check in agent_checks:
            if not isinstance(check, dict) or check.get("status") != "failed":
                continue
            check_id = str(check.get("id") or "check").strip() or "check"
            message = _safe_preflight_message(check.get("message"))
            prefix = f"{agent_id} " if agent_id else ""
            return f"Live agent preflight failed: {prefix}{check_id}: {message}"
    return "Live agent preflight failed."


def _safe_preflight_message(value: object) -> str:
    message = str(value or "failed").replace("\r", " ").replace("\n", " ").strip() or "failed"
    if _looks_sensitive_preflight_message(message):
        return "details redacted"
    return message[:240]


def _looks_sensitive_preflight_message(message: str) -> bool:
    return "/" in message or "\\" in message or ".json" in message.casefold()


def _start_group_failure_message(error: Exception) -> str:
    message = str(error).replace("\r", " ").replace("\n", " ").strip() or error.__class__.__name__
    if _looks_sensitive_preflight_message(message):
        return "Resident process group failed to start: details redacted."
    return f"Resident process group failed to start: {message[:240]}"


def _stop_group_failure_message(error: Exception, *, group_id: str) -> str:
    message = str(error).replace("\r", " ").replace("\n", " ").strip()
    expected_not_found = f"Live agent group {group_id} was not found."
    if message == expected_not_found:
        return message
    return "Resident process group failed to stop: details redacted."


def _restart_group_failure_message(error: Exception, *, group_id: str) -> str:
    message = str(error).replace("\r", " ").replace("\n", " ").strip()
    expected_not_found = f"Live agent group {group_id} was not found."
    if message == expected_not_found:
        return message
    return "Resident process group failed to restart: details redacted."


def _recover_group_failure_message(error: Exception, *, group_id: str) -> str:
    message = str(error).replace("\r", " ").replace("\n", " ").strip()
    expected_not_found = f"Live agent group {group_id} was not found."
    if message == expected_not_found:
        return message
    return "Resident process group failed to recover: details redacted."
