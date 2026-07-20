"""Retained Codex meeting-session invite and join compatibility workflow."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentsassemble.legacy.live_agent.codex_sessions import (
    CODEX_LIVE_PROVIDER_ID,
    DEFAULT_LIVE_AGENT_CONFIG_PATH,
    build_codex_live_agent_config,
    build_codex_live_invite_config,
    read_agent_config,
    write_agent_config,
)
from agentsassemble.config import load_council_config
from agentsassemble.legacy.live_agent.diagnostics import session_process_groups_snapshot
from agentsassemble.legacy.live_agent.session_control import session_start_operation_status
from agentsassemble.legacy.meeting.records import read_meeting_record
from agentsassemble.legacy.meeting.turn_scheduler import meeting_turn_lock
from agentsassemble.legacy.live_agent.runtime.processes import LiveAgentProcessSupervisor, clean_live_agent_group_id
from agentsassemble.legacy.live_agent.state import read_live_agents
from agentsassemble.legacy.meeting.core.events import clean_lobby_text, read_live_events, write_live_state


EnsureSession = Callable[..., dict[str, object]]
RestartSession = Callable[..., dict[str, object]]
RecordOperation = Callable[..., object]


class LegacyCodexSessionError(Exception):
    def __init__(self, message: str, *, details: dict[str, object]) -> None:
        super().__init__(message)
        self.details = details


@dataclass
class LegacyCodexSessionCompatibilityService:
    output_root: Path
    processes: LiveAgentProcessSupervisor
    ensure_session: EnsureSession
    restart_session: RestartSession
    record_operation: RecordOperation

    def invite(self, payload: dict[str, object]) -> dict[str, object]:
        try:
            invite = codex_session_invite_payload(
                self.output_root,
                session_id=str(payload.get("session_id") or ""),
                role_id=str(payload.get("role_id") or ""),
                meeting_id=_optional_str(payload.get("meeting_id")),
            )
        except ValueError as error:
            details = codex_session_invite_error_details(self.output_root, payload)
            safe_error = "Codex live session invite failed."
            self.record_operation(
                self.output_root,
                operation="codex_session.invite",
                status="failed",
                target_id=details.get("role_id", ""),
                summary="Codex live session invite failed",
                error=safe_error,
                details=details,
            )
            raise LegacyCodexSessionError(safe_error, details=details) from error
        details = codex_session_invite_operation_details(invite)
        self.record_operation(
            self.output_root,
            operation="codex_session.invite",
            status="success",
            target_id=details.get("role_id", ""),
            summary="wrote Codex live session invite",
            details=details,
        )
        return invite

    def join(self, payload: dict[str, object], *, default_server: str) -> dict[str, object]:
        try:
            joined = codex_session_join_payload(
                self.output_root,
                self.processes,
                payload,
                default_server=default_server,
                ensure_session=self.ensure_session,
                restart_session=self.restart_session,
            )
        except (OSError, ValueError) as error:
            details = codex_session_join_error_details(self.output_root, payload)
            safe_error = "Codex live session join failed."
            self.record_operation(
                self.output_root,
                operation="codex_session.join",
                status="failed",
                target_id=str(details.get("role_id") or details.get("meeting_id") or ""),
                summary="Codex live session join failed",
                error=safe_error,
                details=details,
            )
            raise LegacyCodexSessionError(safe_error, details=details) from error
        details = codex_session_join_operation_details(joined)
        self.record_operation(
            self.output_root,
            operation="codex_session.join",
            status=session_start_operation_status(joined),
            target_id=str(details.get("role_id") or joined.get("meeting_id") or ""),
            summary="joined Codex live session",
            details=details,
        )
        return joined


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


def codex_session_join_payload(
    output_root: Path,
    process_supervisor: LiveAgentProcessSupervisor,
    payload: dict[str, object],
    *,
    default_server: str,
    ensure_session: EnsureSession,
    restart_session: RestartSession,
) -> dict[str, object]:
    meeting_id = _clean_codex_join_meeting_id(payload.get("meeting_id"))
    role_id = str(payload.get("role_id") or "")
    session_id = str(payload.get("session_id") or "")
    with meeting_turn_lock(meeting_id):
        meeting_dir = _codex_join_meeting_dir(output_root, meeting_id)
        meeting = read_meeting_record(meeting_dir)
        _validate_codex_join_pre_round(meeting_dir, meeting)

        config_path = output_root / "codex-live-session.local.json"
        live_agent_config_path = output_root / DEFAULT_LIVE_AGENT_CONFIG_PATH.name
        effective_server = str(payload.get("server") or default_server)
        role_ids = _codex_invite_role_ids(output_root, meeting_id)
        config = build_codex_live_invite_config(
            session_id=session_id,
            role_id=role_id,
            role_ids=role_ids,
            existing=_codex_join_agent_config_from_meeting(meeting),
        )
        resident_config = build_codex_live_agent_config(
            config,
            server=effective_server,
            meeting_id=meeting_id,
            engagement_mode=str(payload.get("engagement_mode") or "moderator_called"),
        )
        write_agent_config(config_path, config)
        write_agent_config(live_agent_config_path, resident_config)
        write_live_state(
            meeting_dir,
            _meeting_with_codex_live_config(meeting, config, config_path=config_path),
        )

        group_id = clean_live_agent_group_id(live_agent_config_path.stem)
        session_payload = {
            "server": effective_server,
            "meeting_id": meeting_id,
            "group_id": group_id,
            "live_agent_config_path": str(live_agent_config_path),
            "connect_timeout_seconds": _payload_nonnegative_float(
                payload.get("connect_timeout_seconds"),
                5.0,
            ),
            "auto_restart": _payload_bool(payload.get("auto_restart")),
            "max_restarts": _payload_nonnegative_int(payload.get("max_restarts"), 0),
            "restart_backoff_seconds": _payload_nonnegative_float(
                payload.get("restart_backoff_seconds"),
                5.0,
            ),
            "stale_restart_after_seconds": _payload_nonnegative_float(
                payload.get("stale_restart_after_seconds"),
                0.0,
            ),
        }
        binding = _binding_for_role(config.get("agent_bindings", []), role_id)
        if _codex_join_needs_session_restart(
            output_root,
            process_supervisor,
            group_id=group_id,
            binding=binding,
        ):
            session = restart_session(output_root, process_supervisor, session_payload)
            session["action"] = "restart"
        else:
            session = ensure_session(
                output_root,
                process_supervisor,
                session_payload,
                default_server=effective_server,
            )
        session["config_path"] = str(config_path)
        session["live_agent_config_path"] = str(live_agent_config_path)
        session["invite"] = {
            "config_path": str(config_path),
            "live_agent_config_path": str(live_agent_config_path),
            "group_id": group_id,
            "binding": binding,
        }
        return session


def codex_session_invite_operation_details(invite: dict[str, object]) -> dict[str, object]:
    binding = invite.get("binding") if isinstance(invite.get("binding"), dict) else {}
    return {
        "role_id": clean_lobby_text(binding.get("role_id"), limit=128),
        "agent_id": clean_lobby_text(binding.get("agent_id"), limit=128),
        "join_mode": clean_lobby_text(binding.get("join_mode"), limit=64),
        "provider_id": clean_lobby_text(binding.get("provider_id"), limit=128),
    }


def codex_session_invite_error_details(
    output_root: Path,
    payload: dict[str, object],
) -> dict[str, object]:
    role_id = clean_lobby_text(payload.get("role_id"), limit=128)
    meeting_id = _optional_str(payload.get("meeting_id"))
    try:
        known_role_ids = set(_codex_invite_role_ids(output_root, meeting_id))
    except ValueError:
        known_role_ids = set()
    return {"role_id": role_id} if role_id in known_role_ids else {}


def codex_session_join_operation_details(join: dict[str, object]) -> dict[str, object]:
    invite = join.get("invite") if isinstance(join.get("invite"), dict) else {}
    details = codex_session_invite_operation_details(invite)
    details.update(
        {
            "meeting_id": clean_lobby_text(join.get("meeting_id"), limit=128),
            "group_id": clean_lobby_text(join.get("group_id"), limit=128),
            "result_status": str(join.get("status") or "unknown").strip() or "unknown",
        }
    )
    ensure_action = clean_lobby_text(join.get("action"), limit=64)
    if ensure_action:
        details["ensure_action"] = ensure_action
    return details


def codex_session_join_error_details(
    output_root: Path,
    payload: dict[str, object],
) -> dict[str, object]:
    details: dict[str, object] = {}
    meeting_id = clean_lobby_text(payload.get("meeting_id"), limit=128)
    role_id = clean_lobby_text(payload.get("role_id"), limit=128)
    try:
        _codex_join_meeting_dir(output_root, meeting_id)
        details["meeting_id"] = meeting_id
        if role_id in set(_codex_invite_role_ids(output_root, meeting_id)):
            details["role_id"] = role_id
    except ValueError:
        pass
    return details


def _clean_codex_join_meeting_id(value: object) -> str:
    meeting_id = clean_lobby_text(value, limit=128)
    if not meeting_id or meeting_id in {".", ".."}:
        raise ValueError("Meeting was not found.")
    if "/" in meeting_id or "\\" in meeting_id or Path(meeting_id).name != meeting_id:
        raise ValueError(f"Meeting {meeting_id} was not found.")
    return meeting_id


def _codex_join_meeting_dir(output_root: Path, meeting_id: str) -> Path:
    meetings_root = (output_root / "meetings").resolve()
    meeting_dir = (meetings_root / meeting_id).resolve()
    try:
        meeting_dir.relative_to(meetings_root)
    except ValueError as error:
        raise ValueError(f"Meeting {meeting_id} was not found.") from error
    if not meeting_dir.exists() or not meeting_dir.is_dir():
        raise ValueError(f"Meeting {meeting_id} was not found.")
    if not (meeting_dir / "live_state.json").exists():
        raise ValueError("Codex live session join requires a live pre-round meeting.")
    return meeting_dir


def _validate_codex_join_pre_round(meeting_dir: Path, meeting: dict[str, object]) -> None:
    if clean_lobby_text(meeting.get("live_status"), limit=64) not in {"running", "stalled"}:
        raise ValueError("Codex live session join requires a live pre-round meeting.")
    if _as_dict_list(meeting.get("debate_rounds")):
        raise ValueError("Codex live session join is only available before official rounds begin.")
    for event in read_live_events(meeting_dir, limit=None):
        if (
            event.get("official_record") is True
            or event.get("channel") == "official"
            or event.get("kind") == "live_agent_turn_request"
        ):
            raise ValueError("Codex live session join is only available before official rounds begin.")


def _codex_join_agent_config_from_meeting(meeting: dict[str, object]) -> dict[str, object]:
    return {
        "providers": _config_map_values(meeting.get("provider_configs")),
        "permission_profiles": _config_map_values(meeting.get("permission_profiles")),
        "agent_bindings": [
            binding
            for binding in _as_dict_list(meeting.get("agent_bindings"))
            if binding.get("provider_id") == CODEX_LIVE_PROVIDER_ID
        ],
    }


def _codex_join_needs_session_restart(
    output_root: Path,
    process_supervisor: LiveAgentProcessSupervisor,
    *,
    group_id: str,
    binding: dict[str, object],
) -> bool:
    group = _find_process_group(session_process_groups_snapshot(process_supervisor), group_id)
    if str(group.get("status") or "") not in {"running", "restarting"}:
        return False
    agent_id = str(binding.get("agent_id") or "").strip()
    requested_session_id = str(binding.get("session_id") or "").strip()
    if not agent_id or not requested_session_id:
        return False
    for agent in read_live_agents(output_root):
        if str(agent.get("agent_id") or "") != agent_id:
            continue
        return str(agent.get("session_id") or "").strip() != requested_session_id
    return False


def _meeting_with_codex_live_config(
    meeting: dict[str, object],
    config: dict[str, object],
    *,
    config_path: Path,
) -> dict[str, object]:
    updated = dict(meeting)
    updated["provider_configs"] = _dicts_by_id(config.get("providers"))
    updated["permission_profiles"] = _dicts_by_id(config.get("permission_profiles"))
    updated["agent_bindings"] = _as_dict_list(config.get("agent_bindings"))
    updated["agent_config_source"] = str(config_path)
    return updated


def _codex_invite_role_ids(output_root: Path, meeting_id: str | None) -> list[str]:
    if meeting_id:
        meeting_dir = output_root / "meetings" / meeting_id
        if not meeting_dir.exists():
            raise ValueError(f"Meeting {meeting_id} was not found.")
        meeting = read_meeting_record(meeting_dir)
        role_ids = [
            str(role["id"])
            for role in _as_dict_list(meeting.get("roles", []))
            if role.get("id")
        ]
        if role_ids:
            return role_ids
    return [role.id for role in load_council_config().roles]


def _binding_for_role(bindings: object, role_id: str) -> dict[str, object]:
    for binding in _as_dict_list(bindings):
        if binding.get("role_id") == role_id:
            return binding
    raise ValueError(f"No Codex live binding was written for role {role_id}.")


def _config_map_values(value: object) -> list[dict[str, object]]:
    if isinstance(value, dict):
        return [dict(item) for item in value.values() if isinstance(item, dict)]
    return _as_dict_list(value)


def _dicts_by_id(value: object) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for item in _as_dict_list(value):
        item_id = str(item.get("id") or "").strip()
        if item_id:
            result[item_id] = item
    return result


def _as_dict_list(value: object) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        return [item for item in value.values() if isinstance(item, dict)]
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _find_process_group(
    groups: list[dict[str, object]],
    group_id: str,
) -> dict[str, object]:
    for group in groups:
        if str(group.get("group_id") or "") == group_id:
            return group
    return {}


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _payload_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def _payload_nonnegative_int(value: object, default: int) -> int:
    try:
        parsed = int(value)
    except (OverflowError, TypeError, ValueError):
        return default
    return max(0, parsed)


def _payload_nonnegative_float(value: object, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(parsed):
        return default
    return max(0.0, parsed)
