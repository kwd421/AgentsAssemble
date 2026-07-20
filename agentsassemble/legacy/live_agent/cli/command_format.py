from __future__ import annotations

import json

def _print_live_agent_process_payload(payload: dict[str, object], *, as_json: bool, action: str = "list") -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    if action == "stop-running":
        print(_format_live_agent_process_bulk_stop(payload))
        return

    group = payload.get("group") if isinstance(payload.get("group"), dict) else None
    if group is not None:
        print(_format_live_agent_process_action(group, action))
        return

    groups = payload.get("groups") if isinstance(payload.get("groups"), list) else []
    if not groups:
        print("no live-agent process groups")
        return
    for item in groups:
        if isinstance(item, dict):
            print(_format_live_agent_process_group(item))


def _print_live_agent_process_wait_result(result: dict[str, object], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    group_id = str(result.get("group_id") or "unknown")
    timeout_seconds = _safe_float(result.get("timeout_seconds"))
    group = result.get("group") if isinstance(result.get("group"), dict) else None
    if result.get("status") == "ready":
        suffix = f": {_format_live_agent_process_group(group)}" if group is not None else ""
        print(f"Process group {group_id} ready{suffix}")
        return
    if group is None:
        print(f"Process group {group_id} not ready after {timeout_seconds:.1f}s: group not found")
        return
    print(f"Process group {group_id} not ready after {timeout_seconds:.1f}s: {_format_live_agent_process_group(group)}")


def _print_live_agent_process_event_wait_result(result: dict[str, object], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    event = result.get("event") if isinstance(result.get("event"), dict) else None
    if result.get("status") == "observed":
        suffix = f": {_format_live_agent_process_event(event)}" if event is not None else ""
        print(f"Observed live-agent process event{suffix}")
        return
    parts = [str(result.get("event_type") or "unknown")]
    if result.get("group_id"):
        parts.append(f"group {result.get('group_id')}")
    if result.get("event_status"):
        parts.append(f"status {result.get('event_status')}")
    if result.get("after_timestamp"):
        parts.append(f"after {result.get('after_timestamp')}")
    timeout_seconds = _safe_float(result.get("timeout_seconds"))
    print(f"Timed out waiting for live-agent process event {' '.join(parts)} after {timeout_seconds:.1f}s")
    if event is not None:
        print(f"last event: {_format_live_agent_process_event(event)}")
    if result.get("truncated") is True:
        print("searched bounded lifecycle history; older matches may exist")


def _print_live_agent_process_events_payload(payload: dict[str, object], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    events = payload.get("events") if isinstance(payload.get("events"), list) else []
    if not events:
        print("no live-agent process events")
    else:
        for item in events:
            if isinstance(item, dict):
                print(_format_live_agent_process_event(item))
    scan_notice = _format_live_agent_process_event_scan_notice(payload)
    if scan_notice:
        print(scan_notice)


def _format_live_agent_process_event(event: dict[str, object]) -> str:
    timestamp = str(event.get("timestamp") or "-")
    group_id = str(event.get("group_id") or "unknown")
    event_type = str(event.get("event_type") or "unknown")
    status = str(event.get("status") or "unknown")
    parts = [timestamp, group_id, event_type, status]
    pid = event.get("pid")
    if pid not in (None, ""):
        parts.append(f"pid {pid}")
    returncode = event.get("returncode")
    if returncode not in (None, ""):
        parts.append(f"returncode {returncode}")
    parts.append(f"restarts {_safe_int(event.get('restart_count'))}/{_safe_int(event.get('max_restarts'))}")
    next_restart_at = str(event.get("next_restart_at") or "").strip()
    if next_restart_at:
        parts.append(f"next restart {next_restart_at}")
    previous_status = str(event.get("previous_status") or "").strip()
    if previous_status:
        parts.append(f"previous {previous_status}")
    reason = _format_live_agent_process_event_reason(event.get("reason"))
    if reason:
        parts.append(f"reason {reason}")
    offline = _live_agent_process_offline_summary(event.get("offline"))
    if offline:
        parts.append(offline)
    attention = _format_live_agent_process_offline_attention(event.get("offline"))
    if attention:
        parts.append(attention)
    return " ".join(parts)


def _format_live_agent_process_event_scan_notice(payload: dict[str, object]) -> str:
    if payload.get("truncated") is not True:
        return ""
    scanned = _safe_int(payload.get("scanned_event_count")) or _safe_int(payload.get("scan_limit"))
    if scanned <= 0:
        return "searched bounded lifecycle history; older matches may exist"
    return f"searched recent {scanned} lifecycle events; older matches may exist"


def _format_live_agent_process_bulk_stop(payload: dict[str, object]) -> str:
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    stopped_count = _safe_int(result.get("stopped_count"))
    failed_count = _safe_int(result.get("failed_count"))
    skipped_count = _safe_int(result.get("skipped_count"))
    summary = f"Stopped {stopped_count} live-agent process groups"
    details = []
    offline = _live_agent_process_bulk_offline_summary(result.get("stopped"))
    if offline:
        details.append(offline)
    if failed_count:
        details.append(f"failed {failed_count}")
    if skipped_count:
        details.append(f"skipped {skipped_count}")
    return f"{summary} ({', '.join(details)})" if details else summary


def _format_live_agent_process_group(group: dict[str, object]) -> str:
    group_id = str(group.get("group_id") or "unknown")
    status = str(group.get("status") or "unknown")
    pid = group.get("pid")
    pid_text = f"pid {pid}" if pid not in (None, "") else "pid -"
    auto_restart = "auto-restart on" if group.get("auto_restart") else "auto-restart off"
    restart_count = group.get("restart_count", 0)
    max_restarts = group.get("max_restarts", 0)
    config_path = str(group.get("config_path") or "").strip()
    agents = _format_live_agent_process_agents(group.get("agents"))
    connection = _format_live_agent_process_connection(group.get("agent_connection"))
    last_event = _format_live_agent_process_last_event(group.get("recent_events"))
    stale_watchdog = _format_live_agent_process_stale_watchdog(group.get("stale_restart_after_seconds"))
    next_restart = _format_live_agent_process_next_restart(group.get("next_restart_at"))
    suffix_parts = [part for part in (config_path, agents, connection, stale_watchdog, next_restart, last_event) if part]
    suffix = f" {'; '.join(suffix_parts)}" if suffix_parts else ""
    return f"{group_id}: {status} ({pid_text}, {auto_restart}, restarts {restart_count}/{max_restarts}){suffix}"


def _format_live_agent_process_action(group: dict[str, object], action: str) -> str:
    group_id = str(group.get("group_id") or "unknown")
    status = str(group.get("status") or "unknown")
    pid = group.get("pid")
    if action == "start":
        return f"Started {group_id} (pid {pid if pid not in (None, '') else '-'})"
    if action == "stop":
        offline = _live_agent_process_offline_summary(group.get("offline"))
        suffix = f", {offline}" if offline else ""
        return f"Stopped {group_id} ({status}{suffix})"
    if action == "restart":
        return f"Restarted {group_id} (pid {pid if pid not in (None, '') else '-'})"
    if action == "recover":
        previous_status = str(group.get("recovered_from_status") or "unknown")
        return f"Recovered {group_id} from {previous_status} (pid {pid if pid not in (None, '') else '-'})"
    return _format_live_agent_process_group(group)


def _format_live_agent_process_agents(value: object) -> str:
    if not isinstance(value, list) or not value:
        return ""
    labels = []
    for item in value:
        if not isinstance(item, dict):
            continue
        name = str(item.get("display_name") or item.get("agent_id") or "").strip()
        connection_kind = str(item.get("connection_kind") or "").strip()
        if not name:
            continue
        labels.append(f"{name}/{connection_kind}" if connection_kind else name)
    return f"agents {', '.join(labels)}" if labels else ""


def _format_live_agent_process_connection(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    expected = _safe_int(value.get("expected"))
    connected = _safe_int(value.get("connected"))
    if expected <= 0 and connected <= 0 and not value.get("attention"):
        return ""
    attention = _format_live_agent_process_connection_attention(value.get("attention"))
    suffix = f"; {attention}" if attention else ""
    return f"agents connected {connected}/{expected}{suffix}"


def _format_live_agent_process_stale_watchdog(value: object) -> str:
    seconds = _safe_float(value)
    if seconds <= 0:
        return ""
    if seconds.is_integer():
        return f"stale watchdog {int(seconds)}s"
    return f"stale watchdog {seconds:.1f}s"


def _format_live_agent_process_next_restart(value: object) -> str:
    timestamp = str(value or "").strip()
    return f"next restart {timestamp}" if timestamp else ""


def _format_live_agent_process_connection_attention(value: object) -> str:
    if not isinstance(value, list):
        return ""
    labels = []
    for item in value:
        if not isinstance(item, dict):
            continue
        agent_id = str(item.get("agent_id") or "").strip()
        status = str(item.get("status") or "").strip()
        if agent_id and status:
            labels.append(f"{status} {agent_id}")
    return ", ".join(labels)


def _live_agent_process_offline_summary(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    expected = _safe_int(value.get("expected"))
    offline = _safe_int(value.get("offline"))
    if expected <= 0:
        return ""
    return f"offline {offline}/{expected}"


def _live_agent_process_bulk_offline_summary(records: object) -> str:
    if not isinstance(records, list):
        return ""
    expected = 0
    offline = 0
    for record in records:
        if not isinstance(record, dict):
            continue
        summary = record.get("offline")
        if not isinstance(summary, dict):
            continue
        expected += _safe_int(summary.get("expected"))
        offline += _safe_int(summary.get("offline"))
    if expected <= 0:
        return ""
    return f"offline {offline}/{expected}"


def _format_live_agent_process_last_event(value: object) -> str:
    if not isinstance(value, list) or not value:
        return ""
    latest = _latest_live_agent_process_event(value)
    if latest is None:
        return ""
    event_type = str(latest.get("event_type") or "").strip()
    offline = _format_live_agent_process_last_offline_event(value, latest_event=latest)
    reason = _format_live_agent_process_last_reason_event(value, latest_event=latest)
    suffix = ", ".join(detail for detail in (offline, reason) if detail)
    suffix = f", {suffix}" if suffix else ""
    return f"last event {event_type}{suffix}"


def _latest_live_agent_process_event(value: list[object]) -> dict[str, object] | None:
    for item in reversed(value):
        if not isinstance(item, dict):
            continue
        if str(item.get("event_type") or "").strip():
            return item
    return None


def _format_live_agent_process_last_offline_event(
    value: list[object],
    *,
    latest_event: dict[str, object],
) -> str:
    for item in reversed(value):
        if not isinstance(item, dict):
            continue
        event_type = str(item.get("event_type") or "").strip()
        if not event_type:
            continue
        offline = _live_agent_process_offline_summary(item.get("offline"))
        if not offline:
            continue
        attention = _format_live_agent_process_offline_attention(item.get("offline"))
        details = ", ".join(detail for detail in (offline, attention) if detail)
        if item is latest_event:
            return details
        return f"last offline {event_type} {details}"
    return ""


def _format_live_agent_process_last_reason_event(
    value: list[object],
    *,
    latest_event: dict[str, object],
) -> str:
    for item in reversed(value):
        if not isinstance(item, dict):
            continue
        reason = _format_live_agent_process_event_reason(item.get("reason"))
        if not reason:
            continue
        if item is latest_event:
            return f"reason {reason}"
        event_type = str(item.get("event_type") or "").strip()
        return f"last reason {event_type} {reason}" if event_type else f"last reason {reason}"
    return ""


def _format_live_agent_process_event_reason(value: object) -> str:
    return str(value or "").strip()


def _format_live_agent_process_offline_attention(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    attention = value.get("attention")
    if not isinstance(attention, list):
        return ""
    labels = []
    for item in attention[:10]:
        if not isinstance(item, dict):
            continue
        agent_id = str(item.get("agent_id") or "").strip()
        status = str(item.get("status") or "").strip()
        if agent_id and status:
            labels.append(f"{status} {agent_id}")
    return ", ".join(labels)



def _format_live_agent_real_session_smoke(result: dict[str, object]) -> str:
    official_part = ""
    if result.get("official_round_smoke") is True:
        official_part = (
            f"official {result.get('official_rounds_status') or 'unknown'}: "
            f"{result.get('official_answered_round_count', 0)}/{result.get('official_round_count', 0)} answered; "
        )
    restart_part = ""
    if result.get("restart_smoke") is True:
        restart_part = (
            f"restart {result.get('restart_status') or 'unknown'}; "
            f"post-restart probes {result.get('post_restart_reply_probe_status') or 'unknown'}: "
            f"{result.get('post_restart_reply_probe_ok_count', 0)}/{result.get('post_restart_reply_probe_count', 0)} ok; "
        )
    return (
        f"real resident session smoke {result.get('status') or 'unknown'}: "
        f"{result.get('meeting_id') or 'real-session-smoke'} "
        f"group {result.get('group_id') or 'real-session-smoke'}; "
        f"start {result.get('start_status') or 'unknown'}; "
        f"probes {result.get('reply_probe_status') or 'unknown'}: "
        f"{result.get('reply_probe_ok_count', 0)}/{result.get('reply_probe_count', 0)} ok; "
        f"{official_part}"
        f"{restart_part}"
        f"stop {result.get('stop_status') or 'unknown'}; "
        f"post-stop {result.get('post_stop_process_status') or 'unknown'}"
    )

def _format_live_agent_session_smoke(result: dict[str, object]) -> str:
    expected_replies = result.get("expected_reply_count", 0)
    lobby_probe_count = max(1, int(result.get("lobby_probe_count") or 1))
    expected_reply_total = int(expected_replies) * lobby_probe_count
    soak_cycle_count = max(0, int(result.get("soak_cycle_count") or 0))
    soak_part = ""
    if soak_cycle_count:
        soak_expected_total = int(expected_replies) * soak_cycle_count
        soak_part = f"soak {result.get('soak_reply_count', 0)}/{soak_expected_total} replies over {soak_cycle_count} cycles; "
    return (
        f"resident session smoke {result.get('status') or 'unknown'}: "
        f"{result.get('meeting_id') or 'session-smoke'} "
        f"group {result.get('group_id') or 'session-smoke'}; "
        f"rounds {result.get('rounds_status') or 'unknown'} "
        f"({result.get('answered_round_count', 0)} answered); "
        f"{lobby_probe_count} lobby probes; "
        f"{result.get('reply_count', 0)}/{expected_reply_total} replies; "
        f"post-restart {result.get('post_restart_reply_count', 0)}/{expected_reply_total} replies; "
        f"post-recover {result.get('post_recover_reply_count', 0)}/{expected_reply_total} replies; "
        f"{soak_part}"
        f"post-stop {result.get('post_stop_process_status') or 'unknown'}; "
        f"start {result.get('start_status') or 'unknown'}, "
        f"check {result.get('check_status') or 'unknown'}, "
        f"resume {result.get('resume_status') or 'unknown'}, "
        f"restart {result.get('restart_status') or 'unknown'}, "
        f"recover {result.get('recover_status') or 'unknown'}, "
        f"stop {result.get('stop_status') or 'unknown'}"
    )

def _format_provider_health(report: dict[str, object]) -> str:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    providers = report.get("providers") if isinstance(report.get("providers"), list) else []
    bindings = report.get("bindings") if isinstance(report.get("bindings"), list) else []
    lines = [
        f"provider health: {report.get('status') or 'unknown'}",
        f"providers: {summary.get('providers', 0)} checked, {summary.get('failed_providers', 0)} failed",
        f"bindings: {summary.get('bindings', 0)} checked, {summary.get('failed_bindings', 0)} failed",
        f"checks failed: {summary.get('checks_failed', 0)}, warnings: {summary.get('warnings', 0)}",
    ]
    for provider in providers:
        if not isinstance(provider, dict) or provider.get("status") == "ok":
            continue
        failed_checks = [
            check
            for check in provider.get("checks", [])
            if isinstance(check, dict) and check.get("status") in {"failed", "warning"}
        ]
        for check in failed_checks:
            lines.append(f"{provider.get('provider_id') or 'unknown'}: {check.get('id')}: {check.get('message')}")
    for binding in bindings:
        if not isinstance(binding, dict) or binding.get("status") == "ok":
            continue
        failed_checks = [
            check
            for check in binding.get("checks", [])
            if isinstance(check, dict) and check.get("status") in {"failed", "warning"}
        ]
        for check in failed_checks:
            lines.append(f"{binding.get('agent_id') or 'unknown'}: {check.get('id')}: {check.get('message')}")
    return "\n".join(lines)

def _format_live_agent_readiness(payload: dict[str, object]) -> str:
    health = payload.get("health") if isinstance(payload.get("health"), dict) else {}
    smoke = payload.get("smoke") if isinstance(payload.get("smoke"), dict) else {}
    official_round_smoke = payload.get("official_round_smoke") if isinstance(payload.get("official_round_smoke"), dict) else {}
    agents = health.get("agents") if isinstance(health.get("agents"), dict) else {}
    processes = health.get("processes") if isinstance(health.get("processes"), dict) else {}
    connections = health.get("connections") if isinstance(health.get("connections"), dict) else {}
    sessions = health.get("sessions") if isinstance(health.get("sessions"), dict) else {}
    agent_attention = agents.get("attention") if isinstance(agents.get("attention"), list) else []
    process_attention = processes.get("attention") if isinstance(processes.get("attention"), list) else []
    connection_attention = connections.get("attention") if isinstance(connections.get("attention"), list) else []
    session_attention = sessions.get("attention") if isinstance(sessions.get("attention"), list) else []
    process_reasons = _process_reason_summary(processes.get("reasons"))
    smoke_suffix = str(smoke.get("group_id") or "").strip()
    smoke_label = f"{smoke.get('status') or 'unknown'} {smoke_suffix}".strip()
    lines = [
        f"readiness: {payload.get('status') or 'unknown'}",
        f"health: {health.get('status') or 'unknown'}",
        f"smoke: {smoke_label}",
        f"agent attention: {_attention_summary(agent_attention)}",
        f"process attention: {_attention_summary(process_attention)}",
        f"connection attention: {_attention_summary(connection_attention)}",
        f"session attention: {_attention_summary(session_attention)}",
    ]
    if process_reasons:
        lines.append(f"process reasons: {process_reasons}")
    probes = payload.get("probes") if isinstance(payload.get("probes"), list) else []
    if probes:
        lines.append(f"probes: {_readiness_probe_summary(probes)}")
    if official_round_smoke:
        lines.append(f"official round smoke: {_official_round_smoke_summary(official_round_smoke)}")
    session_smoke = payload.get("session_smoke") if isinstance(payload.get("session_smoke"), dict) else {}
    if session_smoke:
        lines.append(f"session smoke: {_session_smoke_summary(session_smoke)}")
    probe_groups = payload.get("probe_groups") if isinstance(payload.get("probe_groups"), list) else []
    if probe_groups:
        lines.append(f"probe groups: {_readiness_probe_group_summary(probe_groups)}")
    if payload.get("probe_error"):
        lines.append(f"probe error: {payload.get('probe_error')}")
    if smoke.get("error"):
        lines.append(f"smoke error: {smoke.get('error')}")
    return "\n".join(lines)

def _format_live_agent_probe(payload: dict[str, object]) -> str:
    lines = [
        f"probe: {payload.get('status') or 'unknown'}",
        f"agent: {payload.get('agent_id') or 'unknown'}",
    ]
    if payload.get("agent_status"):
        lines.append(f"agent status: {payload.get('agent_status')}")
    if payload.get("source_event_id"):
        lines.append(f"source: {payload.get('source_event_id')}")
    if payload.get("reply_event_id"):
        lines.append(f"reply: {payload.get('reply_event_id')}")
    if payload.get("reason"):
        lines.append(f"reason: {payload.get('reason')}")
    return "\n".join(lines)

def _readiness_probe_summary(probes: list[object]) -> str:
    labels = []
    for probe in probes:
        if not isinstance(probe, dict):
            continue
        agent_id = str(probe.get("agent_id") or "unknown")
        status = str(probe.get("status") or "unknown")
        labels.append(f"{agent_id} {status}")
    return ", ".join(labels) if labels else "none"

def _readiness_probe_group_summary(probe_groups: list[object]) -> str:
    labels = []
    for group in probe_groups:
        if not isinstance(group, dict):
            continue
        group_id = str(group.get("group_id") or "unknown")
        status = str(group.get("status") or "unknown")
        reason = str(group.get("reason") or "")
        label = f"{group_id} {status}"
        if reason:
            label = f"{label} ({reason})"
        labels.append(label)
    return ", ".join(labels) if labels else "none"

def _official_round_smoke_summary(smoke: dict[str, object]) -> str:
    group_id = str(smoke.get("group_id") or "").strip()
    label = f"{smoke.get('status') or 'unknown'} {group_id}".strip()
    return (
        f"{label} ("
        f"{smoke.get('answered_count', 0)} answered, "
        f"{smoke.get('timeout_count', 0)} timed out, "
        f"{smoke.get('skipped_count', 0)} skipped)"
    )

def _session_smoke_summary(smoke: dict[str, object]) -> str:
    group_id = str(smoke.get("group_id") or "").strip()
    label = f"{smoke.get('status') or 'unknown'} {group_id}".strip()
    lobby_probe_count = max(1, int(smoke.get("lobby_probe_count") or 1))
    expected_total = int(smoke.get("expected_reply_count") or 0) * lobby_probe_count
    soak_cycle_count = max(0, int(smoke.get("soak_cycle_count") or 0))
    soak_part = ""
    if soak_cycle_count:
        soak_expected_total = int(smoke.get("expected_reply_count") or 0) * soak_cycle_count
        soak_part = f", soak {smoke.get('soak_reply_count', 0)}/{soak_expected_total} over {soak_cycle_count} cycles"
    post_stop_part = ""
    if smoke.get("post_stop_process_status"):
        post_stop_part = f", post-stop {smoke.get('post_stop_process_status')}"
    return (
        f"{label} ("
        f"{smoke.get('reply_count', 0)}/{expected_total} replies, "
        f"post-restart {smoke.get('post_restart_reply_count', 0)}/{expected_total}, "
        f"post-recover {smoke.get('post_recover_reply_count', 0)}/{expected_total}"
        f"{soak_part}"
        f"{post_stop_part})"
    )

def _attention_summary(items: list[object]) -> str:
    cleaned = [str(item) for item in items if str(item)]
    return ", ".join(cleaned) if cleaned else "none"

def _process_reason_summary(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    labels = []
    for group_id, reason_payload in value.items():
        clean_group_id = str(group_id or "").strip()
        if not clean_group_id:
            continue
        if isinstance(reason_payload, dict):
            event_type = str(reason_payload.get("event_type") or "").strip()
            reason = str(reason_payload.get("reason") or "").strip()
        else:
            event_type = ""
            reason = str(reason_payload or "").strip()
        if not reason:
            continue
        labels.append(" ".join(part for part in (clean_group_id, event_type, reason) if part))
    return ", ".join(labels)

def _safe_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _safe_float(value: object) -> float:
    if isinstance(value, bool):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
