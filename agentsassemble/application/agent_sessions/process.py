"""Provider process launch planning for canonical Agent Sessions."""

from __future__ import annotations

from typing import Callable

from agentsassemble.diagnostics.sensitive_text import redact_persisted_diagnostic_text
from agentsassemble.providers.codex_app_server import (
    clean_agent_session_provider_kind,
    clean_provider_session_id,
)
from agentsassemble.room.repository import RoomRepository
from agentsassemble.room.text import clean_room_text


CommandRunner = Callable[[list[str]], object]


def build_agent_session_launch_plan(session: dict[str, object]) -> dict[str, object]:
    provider_kind = clean_room_text(session.get("provider_kind"), limit=64)
    model = clean_room_text(session.get("model") or session.get("model_id"), limit=128)
    effort = clean_room_text(session.get("effort"), limit=64)
    sandbox = clean_room_text(session.get("sandbox") or session.get("permissions"), limit=64) or "read-only"
    provider_session_id_raw = clean_room_text(
        session.get("provider_session_id") or session.get("codex_session_id"),
        limit=200,
    )
    provider_session_id = clean_provider_session_id(provider_session_id_raw)
    if provider_kind == "codex_live_session":
        diagnostics = []
        if provider_session_id_raw == "--last":
            diagnostics.append(
                {
                    "setting": "resume_mode",
                    "status": "last_forbidden",
                    "message": (
                        "Agent Session runtime forbids Codex resume --last; attach an explicit "
                        "provider_session_id or use fresh mode."
                    ),
                }
            )
        command = ["codex", "exec", "--json"]
        if not provider_session_id:
            command.append("--ephemeral")
        if model:
            command.extend(["--model", model])
        if effort:
            command.extend(["-c", f'model_reasoning_effort="{effort}"'])
        command.extend(["--sandbox", sandbox, "--ignore-rules", "--skip-git-repo-check"])
        if provider_session_id:
            command.extend(["resume", provider_session_id])
        return {
            "provider_kind": provider_kind,
            "command": command,
            "permission_enforcement": "codex_readonly" if sandbox == "read-only" else "advisory",
            "resume_mode": (
                "explicit_session_id"
                if provider_session_id
                else ("last_forbidden" if provider_session_id_raw == "--last" else "none")
            ),
            "provider_session_id": provider_session_id,
            "diagnostics": diagnostics,
        }
    return {
        "provider_kind": provider_kind,
        "command": [],
        "permission_enforcement": "unsupported",
        "diagnostics": [
            {
                "setting": "launch",
                "status": "unsupported",
                "message": "This Agent Session provider has no verified launch/resume setting mapping yet.",
            }
        ],
    }


def agent_session_process_result(
    store: RoomRepository,
    room_id: str,
    agent_id: str,
    session: dict[str, object],
    payload: dict[str, object],
    *,
    command_runner: CommandRunner | None,
) -> dict[str, object]:
    launch_plan = build_agent_session_launch_plan(session)
    diagnostics = list(launch_plan.get("diagnostics") if isinstance(launch_plan.get("diagnostics"), list) else [])
    if not session.get("provider_kind"):
        diagnostics.append(
            {
                "setting": "provider_kind",
                "status": "missing",
                "message": "No provider was supplied or persisted; Agent Session state was attached only.",
            }
        )
        return {"process_status": "not_started", "launch_plan": launch_plan, "diagnostics": diagnostics}
    if launch_plan.get("permission_enforcement") == "unsupported":
        return {"process_status": "unsupported", "launch_plan": launch_plan, "diagnostics": diagnostics}
    if not bool(payload.get("start")):
        diagnostics.append(
            {
                "setting": "start",
                "status": "not_started",
                "message": "Agent Session state was attached; no provider process was requested.",
            }
        )
        return {"process_status": "not_started", "launch_plan": launch_plan, "diagnostics": diagnostics}
    if bool(payload.get("dry_run")):
        diagnostics.append(
            {
                "setting": "dry_run",
                "status": "not_started",
                "message": "Dry run returned the launch plan without starting the provider.",
            }
        )
        return {"process_status": "not_started", "launch_plan": launch_plan, "diagnostics": diagnostics}
    command = launch_plan.get("command") if isinstance(launch_plan.get("command"), list) else []
    if not command_runner:
        diagnostics.append(
            {
                "setting": "command_runner",
                "status": "not_started",
                "message": "No command runner was provided; real provider execution is opt-in.",
            }
        )
        return {"process_status": "not_started", "launch_plan": launch_plan, "diagnostics": diagnostics}
    try:
        result = command_runner([str(part) for part in command])
    except Exception as error:  # pragma: no cover - injected launchers own their exceptions
        diagnostics.append(
            {
                "setting": "launch",
                "status": "failed",
                "message": redact_persisted_diagnostic_text(error, limit=1000),
            }
        )
        return {"process_status": "failed", "launch_plan": launch_plan, "diagnostics": diagnostics}
    returncode = getattr(result, "returncode", None)
    if isinstance(result, dict):
        returncode = result.get("returncode", returncode)
    if returncode not in (0, None):
        diagnostics.append(
            {"setting": "launch", "status": "failed", "message": f"provider command exited {returncode}"}
        )
        return {"process_status": "failed", "launch_plan": launch_plan, "diagnostics": diagnostics}
    store.append_event(room_id, "process_resumed", participant_id=agent_id, session_id=session.get("session_id"))
    return {"process_status": "resumed", "launch_plan": launch_plan, "diagnostics": diagnostics}
