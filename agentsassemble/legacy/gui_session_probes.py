"""Reply probes and transcript redaction for retained GUI session diagnostics."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from agentsassemble.legacy.gui_lobby import LOBBY_APPEND_LOCK
from agentsassemble.legacy.gui_payload import (
    operation_result_status,
    payload_bool,
    payload_nonnegative_float,
    safe_payload_strings,
)
from agentsassemble.legacy.live_agent.readiness_projection import (
    safe_readiness_probe_result,
)
from agentsassemble.legacy.live_agent.runtime.probe import safe_probe_timeout
from agentsassemble.legacy.live_agent.state import (
    read_live_agents,
    update_live_agent_engagement,
)
from agentsassemble.legacy.meeting.core.events import clean_lobby_text


REAL_SESSION_SMOKE_PROBE_REDACTION = "[redacted real session smoke probe]"
REAL_SESSION_SMOKE_REPLY_REDACTION = "[redacted real session smoke reply]"
REDACTED_SOURCE_EVENT_IDS: set[str] = set()

ProbeRunner = Callable[..., dict[str, object]]


def session_bound_agent_reply_probe_payload(
    output_root: Path,
    session: dict[str, object],
    payload: dict[str, object],
    *,
    run_probe: ProbeRunner,
) -> dict[str, object]:
    timeout_seconds = safe_probe_timeout(
        payload_nonnegative_float(
            payload.get("probe_timeout_seconds", payload.get("probe_timeout")),
            12.0,
        ),
    )
    agent_ids = session_bound_agent_ids(session)
    if operation_result_status(session.get("status")) != "ready":
        return session_reply_probe_summary(
            agent_ids,
            [],
            timeout_seconds=timeout_seconds,
            status="skipped",
            reason="session_not_ready",
        )
    if not agent_ids:
        return session_reply_probe_summary(
            agent_ids,
            [],
            timeout_seconds=timeout_seconds,
            status="skipped",
            reason="no_bound_agents",
        )
    probes = []
    redact_probe_events = payload_bool(payload.get("redact_probe_events"))
    for agent_id in agent_ids:
        try:
            probe = run_probe(
                output_root,
                agent_id,
                timeout_seconds=timeout_seconds,
                redact_events=redact_probe_events,
            )
        except ValueError:
            probe = {
                "status": "failed",
                "agent_id": agent_id,
                "reason": "probe could not be run",
            }
        probes.append(safe_readiness_probe_result(probe))
    status = (
        "ok"
        if probes
        and all(operation_result_status(probe.get("status")) == "ok" for probe in probes)
        else "failed"
    )
    return session_reply_probe_summary(
        agent_ids,
        probes,
        timeout_seconds=timeout_seconds,
        status=status,
    )


def session_bound_agent_ids(session: dict[str, object]) -> list[str]:
    connection = (
        session.get("connection")
        if isinstance(session.get("connection"), dict)
        else {}
    )
    for key in ("agent_ids", "connected_agent_ids"):
        agent_ids = safe_payload_strings(connection.get(key), limit=64)
        if agent_ids:
            return agent_ids
    process = session.get("process") if isinstance(session.get("process"), dict) else {}
    return safe_payload_strings(process.get("agent_ids"), limit=64)


def run_session_bound_agent_probe(
    output_root: Path,
    agent_id: str,
    *,
    timeout_seconds: float,
    probe_runner: ProbeRunner,
    redact_events: bool = False,
) -> dict[str, object]:
    previous_engagement = live_agent_engagement_snapshot(output_root, agent_id)
    previous_mode = str(previous_engagement.get("engagement_mode") or "")
    switch_for_probe = previous_mode in {"manual", "watch", "moderator_called"}
    if switch_for_probe:
        update_live_agent_engagement(output_root, agent_id, "human_only")
    try:
        result = probe_runner(
            output_root,
            agent_id,
            timeout_seconds=timeout_seconds,
        )
    finally:
        if switch_for_probe:
            restore_live_agent_engagement_snapshot(
                output_root,
                agent_id,
                previous_engagement,
            )
    if redact_events:
        source_event_id = str(result.get("source_event_id") or "").strip()
        if source_event_id:
            result["redaction"] = redact_real_session_smoke_lobby_events(
                output_root,
                [source_event_id],
            )
    return result


def redact_real_session_smoke_lobby_events(
    output_root: Path,
    source_event_ids: list[str],
) -> dict[str, object]:
    source_ids = {str(value or "").strip() for value in source_event_ids}
    source_ids.discard("")
    result = {"probe_event_count": 0, "reply_event_count": 0}
    if not source_ids:
        return result
    lobby_path = output_root / "lobby.jsonl"
    with LOBBY_APPEND_LOCK:
        REDACTED_SOURCE_EVENT_IDS.update(source_ids)
        if not lobby_path.exists():
            return result
        changed = False
        rewritten_lines: list[str] = []
        for line in lobby_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                rewritten_lines.append(line)
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                rewritten_lines.append(line)
                continue
            if not isinstance(event, dict):
                rewritten_lines.append(line)
                continue
            event_id = str(event.get("id") or "")
            source_event_id = str(event.get("source_event_id") or "")
            if event_id in source_ids:
                result["probe_event_count"] += 1
                if event.get("message") != REAL_SESSION_SMOKE_PROBE_REDACTION:
                    event["message"] = REAL_SESSION_SMOKE_PROBE_REDACTION
                    changed = True
            elif (
                source_event_id in source_ids
                and event.get("live_agent_endpoint") is True
            ):
                result["reply_event_count"] += 1
                if event.get("message") != REAL_SESSION_SMOKE_REPLY_REDACTION:
                    event["message"] = REAL_SESSION_SMOKE_REPLY_REDACTION
                    changed = True
            rewritten_lines.append(json.dumps(event, ensure_ascii=False, sort_keys=True))
        if changed:
            tmp_path = lobby_path.with_name(f"{lobby_path.name}.tmp")
            tmp_path.write_text(
                "\n".join(rewritten_lines) + ("\n" if rewritten_lines else ""),
                encoding="utf-8",
            )
            tmp_path.replace(lobby_path)
    return result


def real_session_smoke_reply_message(source_event_id: str, message: str) -> str:
    if source_event_id and source_event_id in REDACTED_SOURCE_EVENT_IDS:
        return REAL_SESSION_SMOKE_REPLY_REDACTION
    return message


def live_agent_engagement_snapshot(
    output_root: Path,
    agent_id: str,
) -> dict[str, object]:
    clean_agent_id = str(agent_id or "").strip()
    state = read_live_agent_presence_state(output_root)
    agents = state.get("agents")
    if isinstance(agents, list):
        for agent in agents:
            if (
                isinstance(agent, dict)
                and str(agent.get("agent_id") or "") == clean_agent_id
            ):
                snapshot: dict[str, object] = {
                    "engagement_mode": str(agent.get("engagement_mode") or ""),
                }
                if "engagement_mode_updated_at" in agent:
                    snapshot["engagement_mode_updated_at"] = str(
                        agent.get("engagement_mode_updated_at") or "",
                    )
                return snapshot
    for agent in read_live_agents(output_root):
        if str(agent.get("agent_id") or "") == clean_agent_id:
            return {"engagement_mode": str(agent.get("engagement_mode") or "")}
    return {"engagement_mode": ""}


def restore_live_agent_engagement_snapshot(
    output_root: Path,
    agent_id: str,
    snapshot: dict[str, object],
) -> None:
    clean_agent_id = str(agent_id or "").strip()
    if not clean_agent_id:
        return
    state = read_live_agent_presence_state(output_root)
    agents = state.get("agents")
    if not isinstance(agents, list):
        return
    for agent in agents:
        if (
            not isinstance(agent, dict)
            or str(agent.get("agent_id") or "") != clean_agent_id
        ):
            continue
        agent["engagement_mode"] = str(snapshot.get("engagement_mode") or "")
        if "engagement_mode_updated_at" in snapshot:
            agent["engagement_mode_updated_at"] = str(
                snapshot.get("engagement_mode_updated_at") or "",
            )
        else:
            agent.pop("engagement_mode_updated_at", None)
        write_live_agent_presence_state(output_root, state)
        return


def read_live_agent_presence_state(output_root: Path) -> dict[str, object]:
    path = output_root / "live_agents.json"
    if not path.exists():
        return {"agents": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"agents": []}
    return data if isinstance(data, dict) else {"agents": []}


def write_live_agent_presence_state(
    output_root: Path,
    state: dict[str, object],
) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    path = output_root / "live_agents.json"
    temp_path = path.with_suffix(".json.tmp")
    temp_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temp_path.replace(path)


def session_reply_probe_summary(
    agent_ids: list[str],
    probes: list[dict[str, object]],
    *,
    timeout_seconds: float,
    status: str,
    reason: str = "",
) -> dict[str, object]:
    ok_count = sum(
        1 for probe in probes if operation_result_status(probe.get("status")) == "ok"
    )
    timeout_count = sum(
        1
        for probe in probes
        if operation_result_status(probe.get("status")) == "timeout"
    )
    skipped_count = sum(
        1
        for probe in probes
        if operation_result_status(probe.get("status")) == "skipped"
    )
    failed_count = sum(
        1
        for probe in probes
        if operation_result_status(probe.get("status"))
        not in {"ok", "timeout", "skipped"}
    )
    summary: dict[str, object] = {
        "status": status,
        "agent_ids": agent_ids,
        "probe_count": len(probes),
        "ok_count": ok_count,
        "timeout_count": timeout_count,
        "failed_count": failed_count,
        "skipped_count": skipped_count,
        "timeout_seconds": timeout_seconds,
        "probes": probes,
    }
    if reason:
        summary["reason"] = clean_lobby_text(reason, limit=128)
    return summary
