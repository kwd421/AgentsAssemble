"""Execution and output formatting for current core CLI commands."""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from http import HTTPStatus
from pathlib import Path
from typing import Callable

from agentsassemble.application.room_repository_factory import RoomRepositoryUnavailable
from agentsassemble.diagnostics.release_health import (
    DEFAULT_RELEASE_HEALTH_TIMEOUT_SECONDS,
    ReleaseHealthSelectionError,
    release_health_catalog_payload,
    run_release_health_checks,
    write_latest_release_health_report,
)
from agentsassemble.web.frontend_runtime import frontend_dist_status


def run_demo_command(
    args: argparse.Namespace,
    *,
    run_demo_meeting: Callable[..., object],
) -> int:
    run_demo_meeting(
        adapter_name=args.adapter,
        output_root=Path(args.output_root),
        reporter=lambda message: print(message, flush=True),
        codex_timeout_seconds=args.codex_timeout,
        codex_search_enabled=not args.no_codex_search,
        research_depth=args.research_depth,
        research_steering=args.research_steering,
        council_config_path=args.council_config,
        agent_config_path=args.agent_config,
        meeting_mode="free_chat" if args.meeting_mode == "free-chat" else args.meeting_mode,
        moderator_enabled=None if args.moderator is None else args.moderator == "on",
        follow_up_of=args.follow_up_of,
        follow_up_from=args.follow_up_from,
        follow_up_note=args.follow_up_note,
    )
    return 0


def run_gui_command(
    args: argparse.Namespace,
    *,
    serve_gui: Callable[..., object],
) -> int:
    try:
        serve_gui(
            host=args.host,
            port=args.port,
            output_root=Path(args.output_root),
            room_repository_backend=args.room_repository_backend,
            room_postgres_dsn_env=args.room_postgres_dsn_env,
            attention_shadow_mode=args.attention_shadow_mode,
            public_url=args.public_url,
            host_token=args.host_token,
            unsafe_expose_control_plane=args.unsafe_expose_control_plane,
            start_public_tunnel=args.start_public_tunnel,
            live_agent_config=Path(args.live_agent_config) if args.live_agent_config else None,
            live_agent_group_id=args.live_agent_group_id,
            live_agent_auto_restart=args.live_agent_auto_restart,
            live_agent_max_restarts=args.live_agent_max_restarts,
            live_agent_restart_backoff_seconds=args.live_agent_restart_backoff_seconds,
            live_agent_stale_restart_after_seconds=args.live_agent_stale_restart_after_seconds,
        )
    except (ValueError, RoomRepositoryUnavailable) as error:
        print(f"error: {error}", file=sys.stderr)
        if "non-loopback GUI bind" in str(error):
            print("hint: bind to 127.0.0.1 and use the authenticated public tunnel", file=sys.stderr)
        return 2
    return 0


def frontend_info_payload(
    *,
    backend: str = "http://127.0.0.1:8765",
    port: int = 5173,
    frontend_dist_root: Path | None = None,
) -> dict[str, object]:
    backend_url = str(backend or "http://127.0.0.1:8765").rstrip("/") or "http://127.0.0.1:8765"
    frontend_port = int(port)
    frontend_url = f"http://127.0.0.1:{frontend_port}"
    react_app_path = "/app/"
    react_app_url = backend_url + react_app_path
    parity_matrix_doc = "docs/product/legacy-react-parity-matrix.md"
    backend_parts = urllib.parse.urlparse(backend_url)
    backend_host = backend_parts.hostname or "127.0.0.1"
    backend_port = backend_parts.port or 8765
    dist_status = frontend_dist_status(frontend_dist_root)
    recommended_ui_kind = "react" if dist_status.static_available else "react_build_required"
    recommended_ui_url = backend_url + "/"
    recommended_ui_label = (
        "Discord-style room client"
        if dist_status.static_available
        else "Discord-style room client (build required)"
    )
    default_console_kind = "react" if dist_status.static_available else "react_build_required"
    default_console_label = (
        "Discord-style room client (default entry point)"
        if dist_status.static_available
        else "Discord-style room client (build required)"
    )
    return {
        "frontend_dir": "frontend",
        "frontend_url": frontend_url,
        "frontend_dev_port": frontend_port,
        "frontend_dev_proxy_target": backend_url,
        "backend_url": backend_url,
        "legacy_console_status": "retired",
        "default_console_kind": default_console_kind,
        "default_console_label": default_console_label,
        "react_app_path": react_app_path,
        "react_app_url": react_app_url,
        "react_app_kind": "react_default",
        "react_app_label": "Discord-style React room client (default at /, alias at /app/)",
        "recommended_ui_kind": recommended_ui_kind,
        "recommended_ui_url": recommended_ui_url,
        "recommended_ui_label": recommended_ui_label,
        "app_dist_path": "frontend/dist",
        "app_static_available": dist_status.static_available,
        "app_index_present": dist_status.index_present,
        "app_assets_dir_present": dist_status.assets_dir_present,
        "app_referenced_assets_present": dist_status.referenced_assets_present,
        "app_build_status": dist_status.build_status,
        "parity_matrix_doc": parity_matrix_doc,
        "is_default_entry_point": True,
        "launch_commands": [
            f"python3 -m agentsassemble.cli gui --host {backend_host} --port {backend_port} --output-root .agentsassemble",
            "npm --prefix frontend run build",
            "cd frontend && AGENTSASSEMBLE_API_TARGET=" + backend_url + " npm run dev",
        ],
        "notes": [
            "assemble gui serves the Discord-style React room client at / once npm --prefix frontend run build exists.",
            "Until that build exists, / and /app/ return a build-required response instead of serving the retired vanilla console.",
            "The /legacy/ namespace and legacy static routes are retired; rebuild the React client instead of using a fallback UI.",
            "The React/Vite frontend reads existing HTTP/SSE state and does not start provider CLIs.",
            "The Vite proxy should target the same backend URL shown here unless AGENTSASSEMBLE_API_TARGET overrides it.",
            f"Browser parity for the default React surface is operator-verified; see {parity_matrix_doc}.",
        ],
    }


def run_frontend_info_command(args: argparse.Namespace) -> int:
    payload = frontend_info_payload(backend=args.backend, port=args.port)
    if args.as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    print("AgentsAssemble frontend launch info")
    print(f"- {payload['default_console_label']}: {payload['recommended_ui_url']}")
    print(f"- {payload['react_app_label']}: {payload['react_app_url']}")
    print(f"- Recommended current UI: {payload['recommended_ui_url']} ({payload['recommended_ui_label']})")
    print(f"- React/Vite opt-in UI: {payload['frontend_url']}")
    print(f"- Vite API proxy target: {payload['frontend_dev_proxy_target']}")
    print(f"- Built React static available: {payload['app_static_available']} ({payload['app_dist_path']})")
    print(f"- React build status: {payload['app_build_status']}")
    print(f"- Parity matrix: {payload['parity_matrix_doc']}")
    print(f"- Default surface kind: {payload['default_console_kind']}")
    print("- Commands:")
    for command in payload["launch_commands"]:
        print(f"  {command}")
    print("- Note: React/Vite is the default room client at / once built.")
    return 0


ROLLING_RESTART_PATH = "/api/runtime/rolling-restart"


def run_rolling_restart_command(args: argparse.Namespace) -> int:
    """Drive the running server's rolling handover from the operator's shell.

    The endpoint already refuses to start while a provider turn is mid-flight,
    so --wait just keeps asking until those turns reach an idle boundary
    instead of making the operator poll by hand.
    """

    base = str(getattr(args, "server", "") or "http://127.0.0.1:8765").rstrip("/")
    url = f"{base}{ROLLING_RESTART_PATH}"
    as_json = bool(getattr(args, "as_json", False))

    if getattr(args, "status", False):
        payload, error = _rolling_restart_call(url, method="GET")
        if error:
            print(error, file=sys.stderr)
            return 2
        print(
            json.dumps(payload, ensure_ascii=False, indent=2)
            if as_json
            else _format_rolling_restart_status(payload)
        )
        return 0

    deadline = time.monotonic() + max(0.0, float(getattr(args, "wait", 0.0) or 0.0))
    while True:
        payload, error = _rolling_restart_call(url, method="POST")
        if error:
            print(error, file=sys.stderr)
            return 2
        blockers = payload.get("blockers") or []
        blocked = payload.get("accepted") is not True
        if not blocked or time.monotonic() >= deadline:
            break
        print(
            f"Waiting for {len(blockers)} provider turn(s) to finish...",
            file=sys.stderr,
            flush=True,
        )
        time.sleep(2.0)

    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif payload.get("accepted") is True:
        print(f"Rolling restart started: {payload.get('operation_id') or 'unknown'}")
        print(f"- generation: {payload.get('generation')}")
        print("- The listening socket is handed to the replacement; no connection is dropped.")
    else:
        print(str(payload.get("error") or "Rolling restart was refused."), file=sys.stderr)
        for blocker in payload.get("blockers") or []:
            print(
                f"  blocked by {blocker.get('session_id')} in {blocker.get('room_id')}"
                f" ({blocker.get('runtime_status')})",
                file=sys.stderr,
            )
    return 0 if payload.get("accepted") is True else 1


def _rolling_restart_call(
    url: str,
    *,
    method: str,
) -> tuple[dict[str, object], str]:
    request = urllib.request.Request(
        url,
        method=method,
        data=b"{}" if method == "POST" else None,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30.0) as response:
            return json.loads(response.read().decode("utf-8")), ""
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", "replace")
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            return {}, f"Server refused the request: HTTP {error.code} {body[:200]}"
        # A blocked restart is a normal answer, not a transport failure.
        if error.code == HTTPStatus.CONFLICT:
            details = parsed.get("details") if isinstance(parsed.get("details"), dict) else {}
            return {
                "accepted": False,
                "error": parsed.get("error") or parsed.get("message") or "Rolling restart blocked.",
                "blockers": details.get("blockers") or [],
                "state": details.get("state") or "",
            }, ""
        return {}, f"Server refused the request: HTTP {error.code} {body[:200]}"
    except OSError as error:
        return {}, f"Could not reach {url}: {error}"


def _format_rolling_restart_status(payload: dict[str, object]) -> str:
    if payload.get("supported") is not True:
        return f"Rolling restart unavailable: {payload.get('error') or 'not supported'}"
    lines = [
        f"state: {payload.get('state')}",
        f"pid: {payload.get('pid')}  generation: {payload.get('generation')}",
        f"frontend_version: {payload.get('frontend_version')}",
        f"started_at: {payload.get('started_at')}",
    ]
    blockers = payload.get("blockers") or []
    if not blockers:
        lines.append("blockers: none -- safe to roll now")
    else:
        lines.append(f"blockers: {len(blockers)} provider turn(s) still active")
        for blocker in blockers:
            lines.append(
                f"  - {blocker.get('session_id')} in {blocker.get('room_id')}"
                f" ({blocker.get('runtime_status')})"
            )
    if payload.get("error"):
        lines.append(f"last error: {payload.get('error')}")
    return "\n".join(lines)


def run_release_health_command(args: argparse.Namespace) -> int:
    if getattr(args, "release_health_command", None) in {None, "list"}:
        payload = release_health_catalog_payload()
        if getattr(args, "as_json", False):
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(_format_release_health_catalog(payload))
        return 0
    if args.release_health_command == "run":
        try:
            payload = run_release_health_checks(
                check_ids=getattr(args, "check", []),
                skip_ids=getattr(args, "skip", []),
                timeout_seconds=getattr(args, "timeout", DEFAULT_RELEASE_HEALTH_TIMEOUT_SECONDS),
            )
        except ReleaseHealthSelectionError as error:
            print(str(error), file=sys.stderr)
            return 2
        if getattr(args, "save_report", False):
            write_latest_release_health_report(
                payload,
                output_root=Path(getattr(args, "output_root", ".agentsassemble")),
            )
        if getattr(args, "as_json", False):
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(_format_release_health_run(payload))
        return 0 if payload.get("summary", {}).get("ok") is True else 1
    return 1


def _format_release_health_catalog(payload: dict[str, object]) -> str:
    checks = payload.get("checks") if isinstance(payload.get("checks"), list) else []
    lines = ["AgentsAssemble release-health checks"]
    for item in checks:
        if not isinstance(item, dict):
            continue
        check_id = str(item.get("id") or "")
        label = str(item.get("label") or check_id)
        category = str(item.get("category") or "check")
        kind = str(item.get("kind") or "")
        lines.append(f"- {check_id}: {label} [{category}/{kind}]")
    lines.append("Run: python3 -m agentsassemble.cli release-health run --json")
    return "\n".join(lines)


def _format_release_health_run(payload: dict[str, object]) -> str:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    results = payload.get("results") if isinstance(payload.get("results"), list) else []
    lines = [
        "AgentsAssemble release-health run",
        (
            f"- summary: passed {summary.get('passed', 0)}, failed {summary.get('failed', 0)}, "
            f"skipped {summary.get('skipped', 0)}, total {summary.get('total', 0)}"
        ),
    ]
    for item in results:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "unknown")
        check_id = str(item.get("id") or "check")
        duration = item.get("duration_seconds")
        suffix = f" ({duration}s)" if duration is not None else ""
        if status == "skipped" and item.get("skipped_reason"):
            suffix = f"{suffix} {item['skipped_reason']}"
        lines.append(f"- {status}: {check_id}{suffix}")
    return "\n".join(lines)
