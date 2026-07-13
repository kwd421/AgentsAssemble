from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass

from agentsassemble.cli_parser_common import MAX_LIVE_AGENT_ROUND_BATCH
from agentsassemble.cli_http_errors import CliHttpError


SESSION_BOUND_PROBE_HTTP_WINDOWS = 25
MAX_LIVE_AGENT_SEQUENCE_TURNS = 12
SESSION_COMMANDS = {
    "start-session",
    "resume-session",
    "restart-session",
    "recover-session",
    "ensure-session",
    "check-session",
    "session-readiness",
    "stop-session",
}


@dataclass(frozen=True)
class LegacySessionCliRuntime:
    request_json: Callable[..., dict[str, object]]
    server_url: Callable[[str, str], str]
    operation_http_timeout: Callable[..., float]
    monotonic: Callable[[], float]
    sleep: Callable[[float], None]
    is_wait_timeout: Callable[[BaseException], bool]
    session_ensure_action: Callable[[dict[str, object] | None], str]


def run_legacy_session_command(
    args: argparse.Namespace,
    *,
    runtime: LegacySessionCliRuntime,
) -> int | None:
    command = str(getattr(args, "live_agent_command", ""))
    if command not in SESSION_COMMANDS:
        return None
    handlers = {
        "start-session": _start,
        "resume-session": _resume,
        "restart-session": _restart,
        "recover-session": _recover,
        "ensure-session": _ensure,
        "check-session": _check,
        "session-readiness": _readiness,
        "stop-session": _stop,
    }
    return handlers[command](args, runtime)


def validate_session_auto_restart_args(args: argparse.Namespace) -> None:
    _validate_auto_restart(args)


def session_start_payload(args: argparse.Namespace) -> dict[str, object]:
    return _start_payload(args)


def session_request_timeout(
    args: argparse.Namespace,
    payload: dict[str, object],
    *,
    runtime: LegacySessionCliRuntime,
) -> float:
    return _remaining_rounds_request(args, payload, runtime, float(args.connect_timeout))


def wait_for_session_after_control(
    args: argparse.Namespace,
    response: dict[str, object],
    *,
    runtime: LegacySessionCliRuntime,
) -> dict[str, object]:
    return _wait_after_control(args, response, runtime)


def session_command_exit_code(response: dict[str, object]) -> int:
    return _exit_code(response)


def format_session_start(response: dict[str, object]) -> str:
    return _format_start(response)


def _start(args: argparse.Namespace, runtime: LegacySessionCliRuntime) -> int:
    _validate_auto_restart(args)
    payload = _start_payload(args)
    timeout_seconds = _remaining_rounds_request(args, payload, runtime, float(args.connect_timeout))
    response = runtime.request_json(
        runtime.server_url(args.server, "/api/live-agent-sessions/start"),
        method="POST",
        payload=payload,
        timeout_seconds=timeout_seconds,
    )
    return _print_session_result(args, _maybe_wait_ready(args, response, runtime))


def _resume(args: argparse.Namespace, runtime: LegacySessionCliRuntime) -> int:
    _validate_auto_restart(args)
    payload = _resume_payload(args)
    timeout_seconds = _remaining_rounds_request(args, payload, runtime, float(args.connect_timeout))
    response = runtime.request_json(
        runtime.server_url(args.server, "/api/live-agent-sessions/resume"),
        method="POST",
        payload=payload,
        timeout_seconds=timeout_seconds,
    )
    return _print_session_result(args, _maybe_wait_ready(args, response, runtime))


def _restart(args: argparse.Namespace, runtime: LegacySessionCliRuntime) -> int:
    return _restart_or_recover(args, runtime, "restart")


def _recover(args: argparse.Namespace, runtime: LegacySessionCliRuntime) -> int:
    return _restart_or_recover(args, runtime, "recover")


def _restart_or_recover(
    args: argparse.Namespace,
    runtime: LegacySessionCliRuntime,
    action: str,
) -> int:
    payload: dict[str, object] = {
        "meeting_id": str(args.meeting_id or ""),
        "group_id": str(args.group_id or ""),
        "connect_timeout_seconds": float(args.connect_timeout),
    }
    timeout_seconds = _remaining_rounds_request(args, payload, runtime, float(args.connect_timeout))
    response = runtime.request_json(
        runtime.server_url(args.server, f"/api/live-agent-sessions/{action}"),
        method="POST",
        payload=payload,
        timeout_seconds=timeout_seconds,
    )
    return _print_session_result(args, _maybe_wait_ready(args, response, runtime))


def _ensure(args: argparse.Namespace, runtime: LegacySessionCliRuntime) -> int:
    _validate_auto_restart(args)
    action, response = _ensure_session(args, runtime)
    if args.as_json:
        print(json.dumps({"action": action, "session": response}, ensure_ascii=False, indent=2))
    else:
        print(f"Ensured via {action}: {_format_start(response)}")
    return _exit_code(response)


def _ensure_session(
    args: argparse.Namespace,
    runtime: LegacySessionCliRuntime,
) -> tuple[str, dict[str, object]]:
    initial = _initial_readiness(args, runtime)
    action = runtime.session_ensure_action(initial)
    if action in {"start", "none"} and (action == "none" or _blank_meeting_requires_server_ensure(args)):
        payload = _start_payload(args)
        timeout_seconds = _remaining_rounds_request(args, payload, runtime, float(args.connect_timeout))
        response = runtime.request_json(
            runtime.server_url(str(args.server), "/api/live-agent-sessions/ensure"),
            method="POST",
            payload=payload,
            timeout_seconds=timeout_seconds,
        )
        ensured_action = str(response.get("action") or action)
        if ensured_action != "none":
            response = _wait_after_control(args, response, runtime)
        return ensured_action, response
    payload = _control_payload(args, action)
    timeout_seconds = _remaining_rounds_request(args, payload, runtime, float(args.connect_timeout))
    response = runtime.request_json(
        runtime.server_url(str(args.server), f"/api/live-agent-sessions/{action}"),
        method="POST",
        payload=payload,
        timeout_seconds=timeout_seconds,
    )
    return action, _wait_after_control(args, response, runtime)


def _check(args: argparse.Namespace, runtime: LegacySessionCliRuntime) -> int:
    response = runtime.request_json(
        runtime.server_url(args.server, "/api/live-agent-sessions/check"),
        method="POST",
        payload={"meeting_id": str(args.meeting_id or ""), "group_id": str(args.group_id or "")},
        timeout_seconds=10.0,
    )
    _print_check(response, as_json=args.as_json)
    return 1 if args.fail_on_degraded and response.get("status") != "ready" else 0


def _readiness(args: argparse.Namespace, runtime: LegacySessionCliRuntime) -> int:
    response = runtime.request_json(
        _readiness_url(runtime, str(args.server), str(args.meeting_id or ""), str(args.group_id or "")),
        timeout_seconds=10.0,
    )
    _print_check(response, as_json=args.as_json)
    return 1 if args.fail_on_degraded and response.get("status") != "ready" else 0


def _stop(args: argparse.Namespace, runtime: LegacySessionCliRuntime) -> int:
    response = runtime.request_json(
        runtime.server_url(args.server, "/api/live-agent-sessions/stop"),
        method="POST",
        payload={"meeting_id": str(args.meeting_id or ""), "group_id": str(args.group_id or "")},
        timeout_seconds=20.0,
    )
    if args.as_json:
        print(json.dumps(response, ensure_ascii=False, indent=2))
    else:
        print(_format_stop(response))
    return 0 if response.get("status") == "stopped" else 1


def _validate_auto_restart(args: argparse.Namespace) -> None:
    if args.auto_restart and args.max_restarts <= 0:
        raise ValueError("--auto-restart requires --max-restarts greater than 0.")
    if args.stale_restart_after_seconds > 0 and (not args.auto_restart or args.max_restarts <= 0):
        raise ValueError("--stale-restart-after-seconds requires --auto-restart and --max-restarts greater than 0.")


def _start_payload(args: argparse.Namespace) -> dict[str, object]:
    payload: dict[str, object] = {
        "meeting_id": str(args.meeting_id or ""),
        "group_id": str(args.group_id or ""),
        "council_config_path": str(args.council_config or ""),
        "agent_config_path": str(args.agent_config or ""),
        "live_agent_config_path": str(args.live_agent_config or ""),
        "connect_timeout_seconds": float(args.connect_timeout),
        "auto_restart": bool(args.auto_restart),
        "max_restarts": int(args.max_restarts),
        "restart_backoff_seconds": float(args.restart_backoff_seconds),
        "stale_restart_after_seconds": float(args.stale_restart_after_seconds),
    }
    if bool(getattr(args, "approve_real_providers", False)):
        payload["approve_real_providers"] = True
    return payload


def _resume_payload(args: argparse.Namespace) -> dict[str, object]:
    return {
        "meeting_id": str(args.meeting_id or ""),
        "group_id": str(args.group_id or ""),
        "live_agent_config_path": str(args.live_agent_config or ""),
        "connect_timeout_seconds": float(args.connect_timeout),
        "auto_restart": bool(args.auto_restart),
        "max_restarts": int(args.max_restarts),
        "restart_backoff_seconds": float(args.restart_backoff_seconds),
        "stale_restart_after_seconds": float(args.stale_restart_after_seconds),
    }


def _control_payload(args: argparse.Namespace, action: str) -> dict[str, object]:
    if action == "start":
        return _start_payload(args)
    if action == "resume":
        return _resume_payload(args)
    return {
        "meeting_id": str(args.meeting_id or ""),
        "group_id": str(args.group_id or ""),
        "connect_timeout_seconds": float(args.connect_timeout),
    }


def _remaining_rounds_request(
    args: argparse.Namespace,
    payload: dict[str, object],
    runtime: LegacySessionCliRuntime,
    connect_timeout_seconds: float,
) -> float:
    timeout_seconds = connect_timeout_seconds + 6.0
    if getattr(args, "probe_bound_agents"):
        probe_timeout = float(getattr(args, "probe_timeout"))
        payload.update({"probe_bound_agents": True, "probe_timeout_seconds": probe_timeout})
        timeout_seconds = connect_timeout_seconds + runtime.operation_http_timeout(
            probe_timeout,
            windows=SESSION_BOUND_PROBE_HTTP_WINDOWS,
        )
    max_rounds = max(1, int(getattr(args, "max_rounds")))
    if getattr(args, "run_remaining_rounds"):
        if max_rounds > MAX_LIVE_AGENT_ROUND_BATCH:
            raise ValueError(f"--max-rounds supports at most {MAX_LIVE_AGENT_ROUND_BATCH}.")
        payload.update(
            {
                "run_remaining_rounds": True,
                "round_timeout_seconds": float(getattr(args, "round_timeout")),
                "round_max_rounds": max_rounds,
                "round_stop_on_timeout": bool(getattr(args, "stop_on_timeout")),
            }
        )
        if getattr(args, "finalize_after_rounds", False):
            payload["finalize_after_rounds"] = True
        round_timeout = runtime.operation_http_timeout(
            float(getattr(args, "round_timeout")),
            windows=max_rounds * MAX_LIVE_AGENT_SEQUENCE_TURNS,
        )
        timeout_seconds = timeout_seconds + round_timeout if getattr(args, "probe_bound_agents") else connect_timeout_seconds + round_timeout
    return timeout_seconds


def _maybe_wait_ready(
    args: argparse.Namespace,
    response: dict[str, object],
    runtime: LegacySessionCliRuntime,
) -> dict[str, object]:
    if not getattr(args, "wait_ready", False):
        return response
    meeting_id = str(response.get("meeting_id") or getattr(args, "meeting_id", "") or "").strip()
    group_id = str(response.get("group_id") or getattr(args, "group_id", "") or "").strip()
    if not meeting_id or not group_id:
        raise ValueError("Session readiness wait requires meeting_id and group_id in the session response.")
    initial = {**response, "status": "starting"} if response.get("status") == "ready" else response
    waited = _wait_ready(
        runtime,
        server=str(args.server),
        meeting_id=meeting_id,
        group_id=group_id,
        timeout_seconds=float(args.wait_timeout),
        poll_interval_seconds=float(args.wait_poll_interval),
        initial_response=initial,
    )
    return _attach_post_ready(waited, response)


def _wait_after_control(
    args: argparse.Namespace,
    response: dict[str, object],
    runtime: LegacySessionCliRuntime,
) -> dict[str, object]:
    meeting_id = str(response.get("meeting_id") or getattr(args, "meeting_id", "") or "").strip()
    group_id = str(response.get("group_id") or getattr(args, "group_id", "") or "").strip()
    if not meeting_id or not group_id:
        raise ValueError("Session readiness wait requires meeting_id and group_id in the control response.")
    initial = {**response, "status": "starting"} if response.get("status") == "ready" else response
    waited = _wait_ready(
        runtime,
        server=str(args.server),
        meeting_id=meeting_id,
        group_id=group_id,
        timeout_seconds=float(args.wait_timeout),
        poll_interval_seconds=float(args.wait_poll_interval),
        initial_response=initial,
    )
    return _attach_post_ready(waited, response)


def _wait_ready(
    runtime: LegacySessionCliRuntime,
    *,
    server: str,
    meeting_id: str,
    group_id: str,
    timeout_seconds: float,
    poll_interval_seconds: float,
    initial_response: dict[str, object],
) -> dict[str, object]:
    poll_interval = max(0.01, poll_interval_seconds)
    deadline = runtime.monotonic() + timeout_seconds
    attempts = 0
    last_response = initial_response
    while True:
        now = runtime.monotonic()
        if attempts > 0 and now >= deadline:
            return {**last_response, "wait_status": "timeout"}
        attempts += 1
        try:
            response = runtime.request_json(
                _readiness_url(runtime, server, meeting_id, group_id),
                timeout_seconds=max(0.01, deadline - now),
            )
        except (TimeoutError, urllib.error.URLError) as error:
            if not runtime.is_wait_timeout(error):
                raise
            return {**last_response, "wait_status": "timeout"}
        last_response = response
        if response.get("status") == "ready":
            return {**response, "wait_status": "ready"}
        remaining = max(0.0, deadline - runtime.monotonic())
        if remaining > 0:
            runtime.sleep(min(poll_interval, remaining))


def _initial_readiness(
    args: argparse.Namespace,
    runtime: LegacySessionCliRuntime,
) -> dict[str, object] | None:
    meeting_id = str(args.meeting_id or "").strip()
    group_id = str(args.group_id or "").strip()
    if not meeting_id or not group_id:
        return None
    try:
        return runtime.request_json(
            _readiness_url(runtime, str(args.server), meeting_id, group_id),
            timeout_seconds=10.0,
        )
    except CliHttpError as error:
        if error.code == "not_found":
            return None
        raise


def _readiness_url(runtime: LegacySessionCliRuntime, server: str, meeting_id: str, group_id: str) -> str:
    query = urllib.parse.urlencode({"meeting_id": meeting_id, "group_id": group_id})
    return runtime.server_url(server, f"/api/live-agent-sessions/readiness?{query}")


def _blank_meeting_requires_server_ensure(args: argparse.Namespace) -> bool:
    return not str(getattr(args, "meeting_id", "") or "").strip() and bool(
        str(getattr(args, "group_id", "") or "").strip()
    )


def _attach_post_ready(response: dict[str, object], source: dict[str, object]) -> dict[str, object]:
    merged = response
    for key in ("reply_probe", "auto_rounds", "finalization", "session_run"):
        value = source.get(key)
        if isinstance(value, dict):
            if merged is response:
                merged = dict(response)
            merged[key] = value
    return merged


def _print_session_result(args: argparse.Namespace, response: dict[str, object]) -> int:
    print(json.dumps(response, ensure_ascii=False, indent=2) if args.as_json else _format_start(response))
    return _exit_code(response)


def _print_check(response: dict[str, object], *, as_json: bool) -> None:
    print(json.dumps(response, ensure_ascii=False, indent=2) if as_json else _format_check(response))


def _exit_code(response: dict[str, object]) -> int:
    if response.get("status") != "ready":
        return 1
    reply_probe = response.get("reply_probe") if isinstance(response.get("reply_probe"), dict) else None
    auto_rounds = response.get("auto_rounds") if isinstance(response.get("auto_rounds"), dict) else None
    finalization = response.get("finalization") if isinstance(response.get("finalization"), dict) else None
    if reply_probe is not None and reply_probe.get("status") != "ok":
        return 1
    if auto_rounds is not None and auto_rounds.get("status") not in {"answered", "complete"}:
        return 1
    if finalization is not None and finalization.get("status") not in {"finalized", "already_finalized"}:
        return 1
    return 0


def _format_start(response: dict[str, object]) -> str:
    connection = response.get("connection") if isinstance(response.get("connection"), dict) else {}
    suffix = _attention_suffix(response)
    reply_probe = response.get("reply_probe") if isinstance(response.get("reply_probe"), dict) else None
    if reply_probe is not None:
        suffix += f"; probes {reply_probe.get('status') or 'unknown'}: {reply_probe.get('ok_count', 0)}/{reply_probe.get('probe_count', 0)} ok"
    auto_rounds = response.get("auto_rounds") if isinstance(response.get("auto_rounds"), dict) else None
    if auto_rounds is not None:
        suffix += (
            f"; rounds {auto_rounds.get('status') or 'unknown'}: {auto_rounds.get('round_count', 0)} rounds, "
            f"{auto_rounds.get('answered_round_count', 0)} answered, "
            f"{auto_rounds.get('completed_round_count', 0)} already complete, "
            f"{auto_rounds.get('timeout_round_count', 0)} timed out, "
            f"{auto_rounds.get('skipped_round_count', 0)} skipped"
        )
    finalization = response.get("finalization") if isinstance(response.get("finalization"), dict) else None
    if finalization is not None:
        suffix += f"; finalization {finalization.get('status') or 'unknown'}: {finalization.get('official_event_count', 0)} official events"
    return (
        f"Resident session {response.get('meeting_id') or 'unknown'} {response.get('status') or 'unknown'}; "
        f"group {response.get('group_id') or 'unknown'}; {connection.get('connected', 0)}/{connection.get('expected', 0)} connected{suffix}"
    )


def _attention_suffix(response: dict[str, object]) -> str:
    attention: list[object] = []
    seen: set[str] = set()
    for section_name in ("connection", "process", "ownership"):
        section = response.get(section_name) if isinstance(response.get(section_name), dict) else {}
        values = section.get("attention") if isinstance(section.get("attention"), list) else []
        for value in values:
            if str(value) not in seen:
                seen.add(str(value))
                attention.append(value)
    return f"; attention {', '.join(str(item) for item in attention)}" if attention else ""


def _format_stop(response: dict[str, object]) -> str:
    offline = response.get("offline") if isinstance(response.get("offline"), dict) else {}
    suffixes: list[str] = []
    stopped_runs = sum(
        1
        for item in (response.get("session_runs") if isinstance(response.get("session_runs"), list) else [])
        if isinstance(item, dict) and item.get("status") == "stopped"
    )
    if stopped_runs:
        suffixes.append(f"{stopped_runs} {'session run' if stopped_runs == 1 else 'session runs'} stopped")
    attention = offline.get("attention") if isinstance(offline.get("attention"), list) else []
    if attention:
        suffixes.append(f"attention {', '.join(str(item) for item in attention)}")
    suffix = f"; {'; '.join(suffixes)}" if suffixes else ""
    return (
        f"Resident session {response.get('meeting_id') or 'unknown'} {response.get('status') or 'unknown'}; "
        f"group {response.get('group_id') or 'unknown'}; {offline.get('offline', 0)}/{offline.get('expected', 0)} offline{suffix}"
    )


def _format_check(response: dict[str, object]) -> str:
    connection = response.get("connection") if isinstance(response.get("connection"), dict) else {}
    process = response.get("process") if isinstance(response.get("process"), dict) else {}
    suffix = _attention_suffix(response)
    reason = response.get("process_reason") if isinstance(response.get("process_reason"), dict) else {}
    if reason:
        suffix += f"; reason {reason.get('event_type') or 'unknown'} {reason.get('reason') or 'unknown'}"
    return (
        f"Resident session {response.get('meeting_id') or 'unknown'} {response.get('status') or 'unknown'}; "
        f"group {response.get('group_id') or 'unknown'}; {connection.get('connected', 0)}/{connection.get('expected', 0)} connected; "
        f"process {process.get('status') or 'unknown'}{suffix}"
    )
