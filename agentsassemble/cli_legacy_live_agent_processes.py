from __future__ import annotations

import argparse
import urllib.error
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass

from agentsassemble.live_agent_processes import clean_live_agent_group_id


@dataclass(frozen=True)
class LegacyProcessCliRuntime:
    request_json: Callable[..., dict[str, object]]
    server_url: Callable[[str, str], str]
    monotonic: Callable[[], float]
    sleep: Callable[[float], None]
    is_wait_timeout: Callable[[BaseException], bool]
    print_payload: Callable[..., None]
    print_events: Callable[..., None]
    print_wait_result: Callable[..., None]
    print_event_wait_result: Callable[..., None]


def run_legacy_process_command(args: argparse.Namespace, *, runtime: LegacyProcessCliRuntime) -> int | None:
    if str(getattr(args, "live_agent_command", "")) != "processes":
        return None
    command = str(args.live_agent_process_command)
    if command == "list":
        payload = runtime.request_json(runtime.server_url(args.server, "/api/live-agent-processes"))
        runtime.print_payload(payload, as_json=args.as_json)
        return 1 if args.fail_on_attention and _payload_needs_attention(payload) else 0
    if command == "events":
        payload = runtime.request_json(runtime.server_url(args.server, _event_path(args)))
        runtime.print_events(payload, as_json=args.as_json)
        return 0
    if command == "wait-event":
        return _wait_event(args, runtime)
    if command == "wait":
        return _wait_group(args, runtime)
    if command == "start":
        _validate_restart(args)
        payload: dict[str, object] = {
            "config_path": args.config,
            "server": args.server,
            "auto_restart": args.auto_restart,
            "max_restarts": args.max_restarts,
            "restart_backoff_seconds": args.restart_backoff_seconds,
        }
        if args.stale_restart_after_seconds > 0:
            payload["stale_restart_after_seconds"] = args.stale_restart_after_seconds
        if args.group_id:
            payload["group_id"] = args.group_id
        response = runtime.request_json(
            runtime.server_url(args.server, "/api/live-agent-processes/start"),
            method="POST",
            payload=payload,
        )
        runtime.print_payload(response, as_json=args.as_json, action="start")
        return 0
    if command in {"stop", "restart", "recover"}:
        group_id = urllib.parse.quote(args.group_id, safe="")
        response = runtime.request_json(
            runtime.server_url(args.server, f"/api/live-agent-processes/{group_id}/{command}"),
            method="POST",
            payload={},
        )
        runtime.print_payload(response, as_json=args.as_json, action=command)
        return 0
    if command == "stop-running":
        response = runtime.request_json(
            runtime.server_url(args.server, "/api/live-agent-processes/stop-running"),
            method="POST",
            payload={},
        )
        runtime.print_payload(response, as_json=args.as_json, action="stop-running")
        return 0
    return 1


def _validate_restart(args: argparse.Namespace) -> None:
    if args.auto_restart and args.max_restarts <= 0:
        raise ValueError("--auto-restart requires --max-restarts greater than 0.")
    if args.stale_restart_after_seconds > 0 and (not args.auto_restart or args.max_restarts <= 0):
        raise ValueError("--stale-restart-after-seconds requires --auto-restart and --max-restarts greater than 0.")


def _wait_group(args: argparse.Namespace, runtime: LegacyProcessCliRuntime) -> int:
    timeout_seconds = float(args.timeout)
    poll_interval = max(0.01, float(args.poll_interval))
    deadline = runtime.monotonic() + timeout_seconds
    attempts = 0
    last_group: dict[str, object] | None = None
    while True:
        now = runtime.monotonic()
        if attempts > 0 and now >= deadline:
            runtime.print_wait_result(
                _group_wait_result("timeout", args, timeout_seconds, attempts, last_group),
                as_json=args.as_json,
            )
            return 1
        attempts += 1
        try:
            payload = runtime.request_json(
                runtime.server_url(args.server, "/api/live-agent-processes"),
                timeout_seconds=max(0.01, deadline - now),
            )
        except (TimeoutError, urllib.error.URLError) as error:
            if not runtime.is_wait_timeout(error):
                raise
            result = _group_wait_result("timeout", args, timeout_seconds, attempts, last_group)
            result["error"] = str(error) or error.__class__.__name__
            runtime.print_wait_result(result, as_json=args.as_json)
            return 1
        last_group = _find_group(payload, args.group_id)
        if last_group is not None and _group_ready(last_group):
            runtime.print_wait_result(
                _group_wait_result("ready", args, timeout_seconds, attempts, last_group),
                as_json=args.as_json,
            )
            return 0
        remaining = max(0.0, deadline - runtime.monotonic())
        if remaining > 0:
            runtime.sleep(min(poll_interval, remaining))


def _group_wait_result(
    status: str,
    args: argparse.Namespace,
    timeout_seconds: float,
    attempts: int,
    group: dict[str, object] | None,
) -> dict[str, object]:
    return {
        "status": status,
        "group_id": args.group_id,
        "timeout_seconds": timeout_seconds,
        "attempts": attempts,
        "group": group,
    }


def _wait_event(args: argparse.Namespace, runtime: LegacyProcessCliRuntime) -> int:
    timeout_seconds = float(args.timeout)
    poll_interval = max(0.01, float(args.poll_interval))
    deadline = runtime.monotonic() + timeout_seconds
    attempts = 0
    last_payload: dict[str, object] | None = None
    last_event: dict[str, object] | None = None
    while True:
        now = runtime.monotonic()
        if attempts > 0 and now >= deadline:
            runtime.print_event_wait_result(
                _event_wait_result("timeout", args, timeout_seconds, attempts, last_event, last_payload),
                as_json=args.as_json,
            )
            return 1
        attempts += 1
        try:
            payload = runtime.request_json(
                runtime.server_url(args.server, _event_path(args)),
                timeout_seconds=max(0.01, deadline - now),
            )
        except (TimeoutError, urllib.error.URLError) as error:
            if not runtime.is_wait_timeout(error):
                raise
            runtime.print_event_wait_result(
                _event_wait_result(
                    "timeout",
                    args,
                    timeout_seconds,
                    attempts,
                    last_event,
                    last_payload,
                    error=str(error) or error.__class__.__name__,
                ),
                as_json=args.as_json,
            )
            return 1
        last_payload = payload
        last_event = _last_event(payload) or last_event
        event = _find_event(
            payload,
            args.event_type,
            group_id=args.group_id,
            status=args.status,
            after_timestamp=args.after_timestamp,
        )
        if event is not None:
            runtime.print_event_wait_result(
                _event_wait_result("observed", args, timeout_seconds, attempts, event, payload),
                as_json=args.as_json,
            )
            return 0
        remaining = max(0.0, deadline - runtime.monotonic())
        if remaining > 0:
            runtime.sleep(min(poll_interval, remaining))


def _event_path(args: argparse.Namespace) -> str:
    params: dict[str, object] = {"limit": args.limit}
    if args.scan_limit is not None:
        params["scan_limit"] = args.scan_limit
    if args.group_id:
        params["group_id"] = args.group_id
    return f"/api/live-agent-process-events?{urllib.parse.urlencode(params)}"


def _find_event(
    payload: dict[str, object],
    event_type: str,
    *,
    group_id: str = "",
    status: str = "",
    after_timestamp: str = "",
) -> dict[str, object] | None:
    events = payload.get("events") if isinstance(payload.get("events"), list) else []
    for item in events:
        if not isinstance(item, dict) or str(item.get("event_type") or "") != event_type:
            continue
        if group_id and str(item.get("group_id") or "") != clean_live_agent_group_id(group_id):
            continue
        if status and str(item.get("status") or "") != status:
            continue
        if after_timestamp and str(item.get("timestamp") or "") <= after_timestamp:
            continue
        return item
    return None


def _last_event(payload: dict[str, object]) -> dict[str, object] | None:
    events = payload.get("events") if isinstance(payload.get("events"), list) else []
    return next((item for item in reversed(events) if isinstance(item, dict)), None)


def _event_wait_result(
    status: str,
    args: argparse.Namespace,
    timeout_seconds: float,
    attempts: int,
    event: dict[str, object] | None,
    payload: dict[str, object] | None,
    *,
    error: str = "",
) -> dict[str, object]:
    result: dict[str, object] = {
        "status": status,
        "event_type": args.event_type,
        "group_id": args.group_id,
        "event_status": args.status,
        "after_timestamp": args.after_timestamp,
        "timeout_seconds": timeout_seconds,
        "attempts": attempts,
        "event": event,
    }
    if status == "timeout" and isinstance(payload, dict):
        result["truncated"] = payload.get("truncated") is True
        if isinstance(payload.get("events"), list):
            result["events"] = payload["events"]
    if error:
        result["error"] = error
    return result


def _find_group(payload: dict[str, object], group_id: str) -> dict[str, object] | None:
    groups = payload.get("groups") if isinstance(payload.get("groups"), list) else []
    return next(
        (item for item in groups if isinstance(item, dict) and str(item.get("group_id") or "") == group_id),
        None,
    )


def _payload_needs_attention(payload: dict[str, object]) -> bool:
    groups = payload.get("groups") if isinstance(payload.get("groups"), list) else []
    return any(isinstance(group, dict) and _group_needs_attention(group) for group in groups)


def _group_needs_attention(group: dict[str, object]) -> bool:
    if str(group.get("status") or "").strip() in {"error", "unknown", "restarting"}:
        return True
    connection = group.get("agent_connection") if isinstance(group.get("agent_connection"), dict) else {}
    attention = connection.get("attention") if isinstance(connection.get("attention"), list) else []
    return bool(attention)


def _group_ready(group: dict[str, object]) -> bool:
    if str(group.get("status") or "").strip() != "running" or _group_needs_attention(group):
        return False
    connection = group.get("agent_connection") if isinstance(group.get("agent_connection"), dict) else {}
    expected = _safe_int(connection.get("expected"))
    connected = _safe_int(connection.get("connected"))
    return expected <= 0 or connected >= expected


def _safe_int(value: object) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
