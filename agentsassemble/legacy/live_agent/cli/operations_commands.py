"""Retained live-agent operation and session-run CLI commands."""
from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class LegacyOperationsCliRuntime:
    request_json: Callable[..., dict[str, object]]
    server_url: Callable[[str, str], str]
    monotonic: Callable[[], float]
    sleep: Callable[[float], None]
    is_wait_timeout: Callable[[BaseException], bool]


def run_legacy_operations_command(
    args: argparse.Namespace,
    *,
    runtime: LegacyOperationsCliRuntime,
) -> int | None:
    command = str(getattr(args, "live_agent_command", ""))
    if command == "operations":
        return _run_operations(args, runtime)
    if command == "session-runs":
        return _run_session_runs(args, runtime)
    return None


def _run_operations(args: argparse.Namespace, runtime: LegacyOperationsCliRuntime) -> int:
    if args.live_agent_operations_command == "list":
        payload = runtime.request_json(runtime.server_url(args.server, _live_agent_operations_path(args, include_filters=True)))
        _print_live_agent_operations_payload(payload, as_json=args.as_json)
        if args.fail_on_attention and _live_agent_operations_payload_needs_attention(payload):
            return 1
        return 0
    if args.live_agent_operations_command == "wait":
        return _run_operations_wait(args, runtime)
    return 1


def _live_agent_operations_path(args: argparse.Namespace, *, include_filters: bool = False) -> str:
    query: dict[str, object] = {"limit": args.limit}
    if include_filters:
        operation = str(getattr(args, "operation", "") or "").strip()
        target_id = str(getattr(args, "target_id", "") or "").strip()
        status = str(getattr(args, "status", "") or "").strip()
        if operation:
            query["operation"] = operation
        if target_id:
            query["target_id"] = target_id
        if status:
            query["status"] = status
    if getattr(args, "scan_limit", None) is not None:
        query["scan_limit"] = args.scan_limit
    return f"/api/live-agent-operations?{urllib.parse.urlencode(query)}"


def _live_agent_operations_wait_path(args: argparse.Namespace) -> str:
    query: dict[str, object] = {"limit": args.limit}
    scan_limit = getattr(args, "scan_limit", None)
    if scan_limit is not None:
        query["scan_limit"] = scan_limit
        query["scan_tail"] = "1"
    return f"/api/live-agent-operations?{urllib.parse.urlencode(query)}"


def _run_session_runs(args: argparse.Namespace, runtime: LegacyOperationsCliRuntime) -> int:
    if args.live_agent_session_runs_command == "list":
        payload = runtime.request_json(
            runtime.server_url(
                args.server,
                _live_agent_session_runs_path(
                    args,
                    include_target_filters=True,
                    include_readiness=bool(getattr(args, "include_readiness", False)),
                ),
            )
        )
        _print_live_agent_session_runs_payload(payload, as_json=args.as_json)
        if args.fail_on_attention and _live_agent_session_runs_payload_needs_attention(payload):
            return 1
        return 0
    if args.live_agent_session_runs_command == "retry-now":
        _validate_live_agent_session_runs_retry_now_target(args)
        run_id = str(args.run_id or "").strip()
        path = "/api/live-agent-session-runs/retry-now"
        payload: dict[str, object] = {
            "meeting_id": str(args.meeting_id or "").strip(),
            "group_id": str(args.group_id or "").strip(),
        }
        if run_id:
            path = f"/api/live-agent-session-runs/{urllib.parse.quote(run_id, safe='')}/retry-now"
            payload = {}
        if bool(getattr(args, "approve_real_providers", False)):
            payload["approve_real_providers"] = True
        payload = runtime.request_json(
            runtime.server_url(
                args.server,
                path,
            ),
            method="POST",
            payload=payload,
            timeout_seconds=10.0,
        )
        _print_live_agent_session_runs_retry_now_payload(payload, as_json=args.as_json)
        return 0
    if args.live_agent_session_runs_command in {"pause", "resume", "stop"}:
        command = str(args.live_agent_session_runs_command)
        _validate_live_agent_session_runs_action_target(args, command)
        run_id = str(args.run_id or "").strip()
        path = f"/api/live-agent-session-runs/{command}"
        request_payload: dict[str, object] = {
            "meeting_id": str(args.meeting_id or "").strip(),
            "group_id": str(args.group_id or "").strip(),
        }
        if run_id:
            path = f"/api/live-agent-session-runs/{urllib.parse.quote(run_id, safe='')}/{command}"
            request_payload = {}
        payload = runtime.request_json(
            runtime.server_url(
                args.server,
                path,
            ),
            method="POST",
            payload=request_payload,
            timeout_seconds=10.0,
        )
        _print_live_agent_session_runs_action_payload(payload, as_json=args.as_json, command=command)
        return 0
    if args.live_agent_session_runs_command == "wait":
        return _run_session_runs_wait(args, runtime)
    return 1


def _live_agent_session_runs_path(
    args: argparse.Namespace,
    *,
    include_target_filters: bool = False,
    include_readiness: bool = False,
) -> str:
    query: dict[str, object] = {"limit": args.limit}
    if include_target_filters:
        run_id = str(getattr(args, "run_id", "") or "").strip()
        if run_id:
            query["run_id"] = run_id
        else:
            meeting_id = str(args.meeting_id or "").strip()
            group_id = str(args.group_id or "").strip()
            if meeting_id:
                query["meeting_id"] = meeting_id
            if group_id:
                query["group_id"] = group_id
    if include_readiness:
        query["include_readiness"] = "1"
    return f"/api/live-agent-session-runs?{urllib.parse.urlencode(query)}"


def _run_session_runs_wait(args: argparse.Namespace, runtime: LegacyOperationsCliRuntime) -> int:
    _validate_live_agent_session_runs_wait_target(args)
    timeout_seconds = float(args.timeout)
    poll_interval = max(0.01, float(args.poll_interval))
    deadline = runtime.monotonic() + timeout_seconds
    attempts = 0
    last_payload: dict[str, object] | None = None
    while True:
        now = runtime.monotonic()
        if attempts > 0 and now >= deadline:
            _print_live_agent_session_runs_wait_result(
                _live_agent_session_runs_wait_result("timeout", args, timeout_seconds, attempts, None, last_payload),
                as_json=args.as_json,
            )
            return 1
        remaining_before_poll = max(0.01, deadline - now)
        attempts += 1
        try:
            payload = runtime.request_json(
                runtime.server_url(
                    args.server,
                    _live_agent_session_runs_path(
                        args,
                        include_target_filters=True,
                        include_readiness=_live_agent_session_runs_wait_requires_readiness(args),
                    ),
                ),
                timeout_seconds=remaining_before_poll,
            )
        except (TimeoutError, urllib.error.URLError) as error:
            if not runtime.is_wait_timeout(error):
                raise
            _print_live_agent_session_runs_wait_result(
                _live_agent_session_runs_wait_result(
                    "timeout",
                    args,
                    timeout_seconds,
                    attempts,
                    None,
                    last_payload,
                    error=str(error) or error.__class__.__name__,
                ),
                as_json=args.as_json,
            )
            return 1
        last_payload = payload
        run = _find_live_agent_session_run(
            payload,
            run_id=args.run_id,
            meeting_id=args.meeting_id,
            group_id=args.group_id,
            status=args.status,
        )
        if run is not None:
            _print_live_agent_session_runs_wait_result(
                _live_agent_session_runs_wait_result("observed", args, timeout_seconds, attempts, run, payload),
                as_json=args.as_json,
            )
            return 0
        remaining_after_poll = max(0.0, deadline - runtime.monotonic())
        if remaining_after_poll > 0:
            runtime.sleep(min(poll_interval, remaining_after_poll))


def _live_agent_session_runs_wait_result(
    status: str,
    args: argparse.Namespace,
    timeout_seconds: float,
    attempts: int,
    run: dict[str, object] | None,
    payload: dict[str, object] | None,
    *,
    error: str = "",
) -> dict[str, object]:
    result: dict[str, object] = {
        "status": status,
        "run_id": str(run.get("run_id") or "") if isinstance(run, dict) else str(args.run_id or ""),
        "meeting_id": str(args.meeting_id or ""),
        "group_id": str(args.group_id or ""),
        "wanted_status": args.status,
        "run_status": str(run.get("status") or "") if isinstance(run, dict) else "",
        "timeout_seconds": timeout_seconds,
        "attempts": attempts,
        "run": run,
    }
    if status == "timeout":
        result["runs"] = payload.get("runs") if isinstance(payload, dict) and isinstance(payload.get("runs"), list) else []
    if error:
        result["error"] = error
    return result


def _validate_live_agent_session_runs_wait_target(args: argparse.Namespace) -> None:
    if str(args.run_id or "").strip():
        return
    if str(args.meeting_id or "").strip() and str(args.group_id or "").strip():
        return
    raise ValueError("live-agent session-runs wait requires --run-id or both --meeting-id and --group-id.")


def _validate_live_agent_session_runs_retry_now_target(args: argparse.Namespace) -> None:
    _validate_live_agent_session_runs_target(args, "retry-now")


def _validate_live_agent_session_runs_action_target(args: argparse.Namespace, command: str) -> None:
    _validate_live_agent_session_runs_target(args, command)


def _validate_live_agent_session_runs_target(args: argparse.Namespace, command: str) -> None:
    if str(args.run_id or "").strip():
        return
    if str(args.meeting_id or "").strip() and str(args.group_id or "").strip():
        return
    raise ValueError(f"live-agent session-runs {command} requires --run-id or both --meeting-id and --group-id.")


def _live_agent_session_runs_wait_requires_readiness(args: argparse.Namespace) -> bool:
    return str(args.status or "").strip() == "ready"


def _find_live_agent_session_run(
    payload: dict[str, object],
    *,
    run_id: str = "",
    meeting_id: str = "",
    group_id: str = "",
    status: str = "",
) -> dict[str, object] | None:
    runs = payload.get("runs") if isinstance(payload.get("runs"), list) else []
    if run_id:
        for item in runs:
            if not isinstance(item, dict):
                continue
            if str(item.get("run_id") or "") != run_id:
                continue
            if status and str(item.get("status") or "") != status:
                continue
            if not _live_agent_session_run_readiness_allows_status(item, status=status):
                continue
            return item
        return None
    latest = _latest_live_agent_session_run_for_target(runs, meeting_id=meeting_id, group_id=group_id)
    if latest is None:
        return None
    if status and str(latest.get("status") or "") != status:
        return None
    if not _live_agent_session_run_readiness_allows_status(latest, status=status):
        return None
    return latest


def _live_agent_session_run_readiness_allows_status(run: dict[str, object], *, status: str = "") -> bool:
    if str(status or "").strip() != "ready":
        return True
    readiness = run.get("readiness") if isinstance(run.get("readiness"), dict) else {}
    return str(readiness.get("status") or "") == "ready"


def _latest_live_agent_session_run_for_target(
    runs: list[object],
    *,
    meeting_id: str = "",
    group_id: str = "",
) -> dict[str, object] | None:
    for item in reversed(runs):
        if not isinstance(item, dict):
            continue
        if meeting_id and str(item.get("meeting_id") or "") != meeting_id:
            continue
        if group_id and str(item.get("group_id") or "") != group_id:
            continue
        return item
    return None


def _run_operations_wait(args: argparse.Namespace, runtime: LegacyOperationsCliRuntime) -> int:
    timeout_seconds = float(args.timeout)
    poll_interval = max(0.01, float(args.poll_interval))
    deadline = runtime.monotonic() + timeout_seconds
    attempts = 0
    last_payload: dict[str, object] | None = None
    after_id_seen = not bool(args.after_id)
    ignored_operation_ids: set[str] = set()
    while True:
        now = runtime.monotonic()
        if attempts > 0 and now >= deadline:
            _print_live_agent_operations_wait_result(
                _live_agent_operations_wait_result("timeout", args, timeout_seconds, attempts, None, last_payload),
                as_json=args.as_json,
            )
            return 1
        remaining_before_poll = max(0.01, deadline - now)
        attempts += 1
        try:
            payload = runtime.request_json(
                runtime.server_url(args.server, _live_agent_operations_wait_path(args)),
                timeout_seconds=remaining_before_poll,
            )
        except (TimeoutError, urllib.error.URLError) as error:
            if not runtime.is_wait_timeout(error):
                raise
            _print_live_agent_operations_wait_result(
                _live_agent_operations_wait_result(
                    "timeout",
                    args,
                    timeout_seconds,
                    attempts,
                    None,
                    last_payload,
                    error=str(error) or error.__class__.__name__,
                ),
                as_json=args.as_json,
            )
            return 1
        last_payload = payload
        after_id_in_payload = bool(args.after_id) and _live_agent_operation_id_present(payload, args.after_id)
        if not after_id_seen and not after_id_in_payload:
            operation = None
        else:
            operation = _find_live_agent_operation(
                payload,
                args.operation,
                args.target_id,
                args.status,
                args.after_id if after_id_in_payload else "",
                ignored_operation_ids=ignored_operation_ids,
            )
        if after_id_in_payload:
            after_id_seen = True
            ignored_operation_ids.update(_live_agent_operation_ids_through(payload, args.after_id))
        if operation is not None:
            _print_live_agent_operations_wait_result(
                _live_agent_operations_wait_result("observed", args, timeout_seconds, attempts, operation, payload),
                as_json=args.as_json,
            )
            return 0
        remaining_after_poll = max(0.0, deadline - runtime.monotonic())
        if remaining_after_poll > 0:
            runtime.sleep(min(poll_interval, remaining_after_poll))


def _live_agent_operations_wait_result(
    status: str,
    args: argparse.Namespace,
    timeout_seconds: float,
    attempts: int,
    operation: dict[str, object] | None,
    payload: dict[str, object] | None,
    *,
    error: str = "",
) -> dict[str, object]:
    result: dict[str, object] = {
        "status": status,
        "operation_name": args.operation,
        "target_id": args.target_id,
        "operation_status": args.status,
        "after_id": args.after_id,
        "timeout_seconds": timeout_seconds,
        "attempts": attempts,
        "operation": operation,
    }
    if status == "timeout":
        operations = payload.get("operations") if isinstance(payload, dict) and isinstance(payload.get("operations"), list) else []
        result["operations"] = operations[-max(1, int(args.limit)) :]
        if isinstance(payload, dict):
            result["truncated"] = payload.get("truncated") is True
            if "scan_limit" in payload:
                result["scan_limit"] = payload.get("scan_limit")
            if "scanned_operation_count" in payload:
                result["scanned_operation_count"] = payload.get("scanned_operation_count")
    if error:
        result["error"] = error
    return result


def _find_live_agent_operation(
    payload: dict[str, object],
    operation_name: str,
    target_id: str = "",
    status: str = "",
    after_id: str = "",
    ignored_operation_ids: set[str] | None = None,
) -> dict[str, object] | None:
    operations = payload.get("operations") if isinstance(payload.get("operations"), list) else []
    start_index = 0
    if after_id:
        for index, item in enumerate(operations):
            if isinstance(item, dict) and str(item.get("id") or "") == after_id:
                start_index = index + 1
                break
        else:
            return None
    for item in operations[start_index:]:
        if not isinstance(item, dict):
            continue
        if ignored_operation_ids and str(item.get("id") or "") in ignored_operation_ids:
            continue
        if str(item.get("operation") or "") != operation_name:
            continue
        if target_id and str(item.get("target_id") or "") != target_id:
            continue
        if status and str(item.get("status") or "") != status:
            continue
        return item
    return None


def _live_agent_operation_id_present(payload: dict[str, object], operation_id: str) -> bool:
    operations = payload.get("operations") if isinstance(payload.get("operations"), list) else []
    return any(isinstance(item, dict) and str(item.get("id") or "") == operation_id for item in operations)


def _live_agent_operation_ids_through(payload: dict[str, object], operation_id: str) -> set[str]:
    operations = payload.get("operations") if isinstance(payload.get("operations"), list) else []
    operation_ids: set[str] = set()
    for item in operations:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id") or "")
        if item_id:
            operation_ids.add(item_id)
        if item_id == operation_id:
            break
    return operation_ids


def _print_live_agent_operations_wait_result(result: dict[str, object], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if result.get("status") == "observed":
        operation = result.get("operation") if isinstance(result.get("operation"), dict) else {}
        print(f"Observed live-agent operation: {_format_live_agent_operation(operation)}")
        return
    parts = [str(result.get("operation_name") or "unknown")]
    if result.get("target_id"):
        parts.append(f"target {result.get('target_id')}")
    if result.get("operation_status"):
        parts.append(f"status {result.get('operation_status')}")
    if result.get("after_id"):
        parts.append(f"after {result.get('after_id')}")
    timeout_seconds = _safe_float(result.get("timeout_seconds"))
    print(f"Timed out waiting for live-agent operation {' '.join(parts)} after {timeout_seconds:.1f}s")
    operations = result.get("operations") if isinstance(result.get("operations"), list) else []
    last_operation = next((item for item in reversed(operations) if isinstance(item, dict)), None)
    if last_operation is not None:
        print(f"last operation: {_format_live_agent_operation(last_operation)}")
    scan_notice = _format_live_agent_operation_scan_notice(result)
    if scan_notice:
        print(scan_notice)


def _print_live_agent_operations_payload(payload: dict[str, object], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    operations = payload.get("operations") if isinstance(payload.get("operations"), list) else []
    if not operations:
        print("no live-agent operations")
    else:
        for item in operations:
            if isinstance(item, dict):
                print(_format_live_agent_operation(item))
    scan_notice = _format_live_agent_operation_scan_notice(payload)
    if scan_notice:
        print(scan_notice)


def _print_live_agent_session_runs_payload(payload: dict[str, object], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    runs = payload.get("runs") if isinstance(payload.get("runs"), list) else []
    if not runs:
        print("no live-agent session runs")
        return
    for item in runs:
        if isinstance(item, dict):
            print(_format_live_agent_session_run(item))


def _print_live_agent_session_runs_retry_now_payload(payload: dict[str, object], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    run = payload.get("session_run") if isinstance(payload.get("session_run"), dict) else {}
    suffix = f": {_format_live_agent_session_run(run)}" if run else ""
    status = str(payload.get("status") or "scheduled")
    verb = {"reconciled": "Retried", "skipped": "Skipped"}.get(status, "Scheduled")
    print(f"{verb} live-agent session run retry{suffix}")


def _print_live_agent_session_runs_action_payload(
    payload: dict[str, object],
    *,
    as_json: bool,
    command: str,
) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    run = payload.get("session_run") if isinstance(payload.get("session_run"), dict) else {}
    suffix = f": {_format_live_agent_session_run(run)}" if run else ""
    verb = {"pause": "Paused", "resume": "Resumed", "stop": "Stopped"}.get(command, command.title())
    print(f"{verb} live-agent session run{suffix}")


def _print_live_agent_session_runs_wait_result(result: dict[str, object], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    run_id = str(result.get("run_id") or "").strip()
    meeting_id = str(result.get("meeting_id") or "").strip()
    group_id = str(result.get("group_id") or "").strip()
    target_label = f"session run {run_id}" if run_id else f"session run for {meeting_id or '-'} {group_id or '-'}"
    wanted_status = str(result.get("wanted_status") or "unknown")
    timeout_seconds = _safe_float(result.get("timeout_seconds"))
    run = result.get("run") if isinstance(result.get("run"), dict) else None
    if result.get("status") == "observed":
        suffix = f": {_format_live_agent_session_run(run)}" if run is not None else ""
        print(f"Observed live-agent {target_label} status {wanted_status}{suffix}")
        return
    print(f"Timed out waiting for live-agent {target_label} status {wanted_status} after {timeout_seconds:.1f}s")
    runs = result.get("runs") if isinstance(result.get("runs"), list) else []
    last_run = None
    if run_id:
        last_run = next((item for item in reversed(runs) if isinstance(item, dict) and str(item.get("run_id") or "") == run_id), None)
    if last_run is None and (meeting_id or group_id):
        last_run = _latest_live_agent_session_run_for_target(runs, meeting_id=meeting_id, group_id=group_id)
    if last_run is None:
        last_run = next((item for item in reversed(runs) if isinstance(item, dict)), None)
    if last_run is not None:
        print(f"last run: {_format_live_agent_session_run(last_run)}")


def _format_live_agent_session_run(run: dict[str, object]) -> str:
    run_id = str(run.get("run_id") or "-")
    action = str(run.get("action") or "unknown")
    status = str(run.get("status") or "unknown")
    meeting_id = str(run.get("meeting_id") or "-")
    group_id = str(run.get("group_id") or "-")
    activity = "active" if run.get("active") is True else "inactive"
    phase = str(run.get("phase") or "").strip()
    reconcile_count = _safe_int(run.get("reconcile_count"))
    suffix_parts = []
    if phase:
        suffix_parts.append(f"phase={phase}")
    if reconcile_count:
        suffix_parts.append(f"reconcile_count={reconcile_count}")
    reconcile_failure_count = _safe_int(run.get("reconcile_failure_count"))
    if reconcile_failure_count:
        suffix_parts.append(f"reconcile_failures={reconcile_failure_count}")
    reconcile_backoff_seconds = _safe_int(run.get("reconcile_backoff_seconds"))
    if reconcile_backoff_seconds:
        suffix_parts.append(f"reconcile_backoff={reconcile_backoff_seconds}s")
    next_reconcile_at = str(run.get("next_reconcile_at") or "").strip()
    if next_reconcile_at:
        suffix_parts.append(f"next_reconcile={next_reconcile_at}")
    paused_status = str(run.get("paused_status") or "").strip()
    if paused_status:
        suffix_parts.append(f"paused_from={paused_status}")
    readiness = run.get("readiness") if isinstance(run.get("readiness"), dict) else {}
    readiness_status = str(readiness.get("status") or "").strip()
    if readiness_status:
        suffix_parts.append(f"readiness={readiness_status}")
    readiness_expected = _safe_int(readiness.get("expected"))
    readiness_connected = _safe_int(readiness.get("connected"))
    if readiness_expected > 0:
        suffix_parts.append(f"current_connected={max(0, readiness_connected)}/{readiness_expected}")
    suffix = f" · {' · '.join(suffix_parts)}" if suffix_parts else ""
    return f"{run_id} {action} {status} {meeting_id} {group_id} {activity}{suffix}"


def _format_live_agent_operation(operation: dict[str, object]) -> str:
    timestamp = str(operation.get("timestamp") or "-")
    operation_name = str(operation.get("operation") or "unknown")
    status = str(operation.get("status") or "unknown")
    target_id = str(operation.get("target_id") or "-")
    summary = str(operation.get("summary") or operation.get("error") or "").strip()
    details = _format_live_agent_operation_details(operation.get("details"), operation_name=operation_name)
    suffix_parts = [part for part in (summary, details) if part]
    suffix = f" · {' · '.join(suffix_parts)}" if suffix_parts else ""
    return f"{timestamp} {operation_name} {status} {target_id}{suffix}"


def _format_live_agent_operation_scan_notice(payload: dict[str, object]) -> str:
    if payload.get("truncated") is not True:
        return ""
    scanned = _safe_int(payload.get("scanned_operation_count")) or _safe_int(payload.get("scan_limit"))
    if scanned <= 0:
        return "searched bounded operation history; older matches may exist"
    return f"searched recent {scanned} live-agent operations; older matches may exist"


def _live_agent_operations_payload_needs_attention(payload: dict[str, object]) -> bool:
    operations = payload.get("operations") if isinstance(payload.get("operations"), list) else []
    for item in operations:
        if isinstance(item, dict) and str(item.get("status") or "").strip() != "success":
            return True
    return False


def _live_agent_session_runs_payload_needs_attention(payload: dict[str, object]) -> bool:
    runs = payload.get("runs") if isinstance(payload.get("runs"), list) else []
    for item in runs:
        if isinstance(item, dict) and _live_agent_session_run_needs_attention(item):
            return True
    return False


def _live_agent_session_run_needs_attention(run: dict[str, object]) -> bool:
    status = str(run.get("status") or "").strip()
    if status in {"failed", "error"}:
        return True
    active = run.get("active") is True
    if active and status != "ready":
        return True
    readiness = run.get("readiness") if isinstance(run.get("readiness"), dict) else {}
    readiness_status = str(readiness.get("status") or "").strip()
    return bool(active and readiness_status and readiness_status != "ready")


def _format_live_agent_operation_details(value: object, *, operation_name: str = "") -> str:
    if not isinstance(value, dict):
        return ""
    labels = []
    detail_limit = _live_agent_operation_detail_limit(operation_name)
    for key, raw_detail in _ordered_live_agent_operation_details(value, operation_name=operation_name):
        clean_key = str(key or "").strip()
        clean_value = _format_live_agent_operation_detail_value(raw_detail)
        if clean_key and clean_value:
            labels.append(f"{clean_key}={clean_value}")
        if len(labels) >= detail_limit:
            break
    return "; ".join(labels)


def _ordered_live_agent_operation_details(
    value: dict[str, object],
    *,
    operation_name: str = "",
) -> list[tuple[str, object]]:
    priority = _live_agent_operation_detail_priority(operation_name)
    seen = set()
    ordered: list[tuple[str, object]] = []
    for key in priority:
        if key in value:
            ordered.append((key, value[key]))
            seen.add(key)
    ordered.extend((key, raw_detail) for key, raw_detail in value.items() if key not in seen)
    return ordered


def _live_agent_operation_detail_priority(operation_name: str) -> list[str]:
    if operation_name == "session.smoke":
        return [
            "result_status",
            "finalization_status",
            "finalization_official_event_count",
            "return_packet_event_count",
            "artifact_status",
            "reply_count",
            "post_restart_reply_count",
            "post_recover_reply_count",
            "soak_cycle_count",
            "soak_reply_count",
            "soak_check_statuses",
            "post_stop_process_status",
        ]
    if operation_name == "session.real_smoke":
        return [
            "result_status",
            "start_status",
            "connected_agent_count",
            "expected_agent_count",
            "reply_probe_status",
            "reply_probe_ok_count",
            "reply_probe_count",
            "stop_status",
            "post_stop_process_status",
        ]
    if operation_name == "readiness.check":
        return [
            "result_status",
            "health_process_reasons",
            "health_process_attention",
            "health_observation_attention",
            "health_observation_lobby_behind_count",
            "health_observation_live_behind_count",
            "health_observation_error_count",
            "health_shared_memory_attention",
            "health_session_run_attention",
            "health_session_run_retrying",
            "health_session_run_monitor_attention",
            "health_session_attention",
            "health_connection_attention",
            "health_agent_attention",
            "session_smoke_reply_count",
            "session_smoke_finalization_status",
            "session_smoke_finalization_official_event_count",
            "session_smoke_return_packet_event_count",
            "session_smoke_artifact_status",
            "session_smoke_post_restart_reply_count",
            "session_smoke_post_recover_reply_count",
            "session_smoke_soak_cycle_count",
            "session_smoke_soak_reply_count",
            "session_smoke_soak_check_statuses",
            "session_smoke_post_stop_process_status",
            "probe_statuses",
        ]
    if operation_name in {"session.start", "session.ensure", "session.resume", "session.restart", "session.recover"}:
        return [
            "ensure_action",
            "result_status",
            "connected_agent_count",
            "reply_probe_status",
            "reply_probe_statuses",
            "auto_rounds_status",
            "auto_rounds_reason",
            "finalization_status",
            "finalization_reason",
            "finalization_official_event_count",
            "auto_rounds_answered_round_count",
            "auto_rounds_round_count",
        ]
    if operation_name == "discovery.run":
        return [
            "result_status",
            "approved_count",
            "approved_agent_ids",
            "approved_cli_count",
            "excluded_agent_count",
            "excluded_cli_count",
            "unmatched_approval_count",
            "agents",
            "discovered",
            "approval_required",
        ]
    if operation_name == "official_turn.rounds":
        return [
            "finalization_status",
            "finalization_reason",
            "finalization_official_event_count",
            "round_count",
            "answered_round_count",
            "completed_round_count",
            "timeout_round_count",
            "skipped_round_count",
            "stopped_round_count",
            "statuses",
        ]
    if operation_name == "review.checkpoint":
        return [
            "result_status",
            "checkpoint_id",
            "answered_count",
            "timeout_count",
            "skipped_count",
            "agent_ids",
            "statuses",
            "reply_event_ids",
        ]
    return []


def _live_agent_operation_detail_limit(operation_name: str) -> int:
    if operation_name == "session.real_smoke":
        return 9
    if operation_name == "session.smoke":
        return 8
    if operation_name == "readiness.check":
        return 12
    if operation_name == "session.ensure":
        return 11
    if operation_name in {"session.start", "session.resume", "session.restart", "session.recover"}:
        return 10
    if operation_name == "official_turn.rounds":
        return 8
    if operation_name == "review.checkpoint":
        return 8
    if operation_name == "discovery.run":
        return 10
    return 7


def _format_live_agent_operation_detail_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        items = []
        for item in value[:10]:
            if isinstance(item, bool):
                items.append("true" if item else "false")
            elif isinstance(item, (int, float)) and not isinstance(item, bool):
                items.append(str(item))
            elif isinstance(item, str) and item.strip():
                items.append(item.strip())
        return ",".join(items)
    return ""



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
