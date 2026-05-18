from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from agentsassemble.config import load_council_config
from agentsassemble.live_agent_meetings import start_live_agent_meeting
from agentsassemble.live_agent_preflight import preflight_live_agent_config
from agentsassemble.live_agent_runner import load_group_configs
from agentsassemble.live_agents import read_live_agents
from agentsassemble.meeting_setup import prepare_meeting_setup

SUPPORTED_SESSION_CONNECTION_KINDS = frozenset({"local_cli", "live_session", "remote_bridge"})


class LiveAgentSessionStartError(ValueError):
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

    started_meeting = start_live_agent_meeting(
        output_root,
        council_config_path=council_config_path,
        agent_config_path=agent_config_path,
        meeting_id=meeting_id,
    )
    clean_meeting_id = str(started_meeting.get("meeting_id") or "")
    try:
        group = process_supervisor.start_group(
            config_path=live_agent_config_path,
            server=server,
            group_id=group_id.strip() or None,
            auto_restart=auto_restart,
            max_restarts=max_restarts,
            restart_backoff_seconds=restart_backoff_seconds,
            stale_restart_after_seconds=stale_restart_after_seconds,
        )
    except Exception as error:
        raise LiveAgentSessionStartError(
            _start_group_failure_message(error),
            meeting_id=clean_meeting_id,
        ) from error
    process = _process_snapshot(group, expected_agent_ids=expected_agent_ids)
    connection = _wait_for_connections(
        output_root,
        meeting_id=clean_meeting_id,
        expected_agent_ids=expected_agent_ids,
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


def _validate_resident_manifest(configs: object, expected_agents: list[dict[str, str]]) -> None:
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


def _wait_for_connections(
    output_root: Path,
    *,
    meeting_id: str,
    expected_agent_ids: list[str],
    timeout_seconds: float,
) -> dict[str, object]:
    timeout = max(0.0, float(timeout_seconds))
    deadline = time.monotonic() + timeout
    while True:
        connection = _connection_snapshot(output_root, meeting_id=meeting_id, expected_agent_ids=expected_agent_ids)
        if connection["connected"] == connection["expected"] or time.monotonic() >= deadline:
            return connection
        time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))


def _connection_snapshot(output_root: Path, *, meeting_id: str, expected_agent_ids: list[str]) -> dict[str, object]:
    agents = {str(agent.get("agent_id") or ""): agent for agent in read_live_agents(output_root)}
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


def _process_snapshot(group: object, *, expected_agent_ids: list[str]) -> dict[str, object]:
    group_payload = group if isinstance(group, dict) else {}
    status = str(group_payload.get("status") or "unknown")
    manifest_ids = _process_agent_ids(group_payload.get("agents"))
    missing_agent_ids = [agent_id for agent_id in expected_agent_ids if agent_id not in manifest_ids]
    attention = []
    if status != "running":
        attention.append(f"group:{status}")
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
    return {
        "group_id": str(value.get("group_id") or ""),
        "status": str(value.get("status") or ""),
    }


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
