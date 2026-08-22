"""Execution and output formatting for current core CLI commands."""
from __future__ import annotations

import argparse
import json
import os
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


def gui_reuse_conflicts(args: argparse.Namespace, existing_url: str) -> list[str]:
    """Return explicit gui flags that cannot be applied to a running engine."""

    from urllib.parse import urlparse

    parsed = urlparse(str(existing_url or "").strip())
    conflicts: list[str] = []
    requested_port = getattr(args, "port", None)
    if requested_port not in (None, 0):
        existing_port = parsed.port
        if existing_port is None:
            existing_port = 80 if parsed.scheme == "http" else 443
        if int(requested_port) != int(existing_port):
            conflicts.append(
                f"--port {requested_port} does not match the running engine at port {existing_port}"
            )
    requested_host = str(getattr(args, "host", "") or "").strip()
    if requested_host and requested_host not in {"127.0.0.1", "localhost", "::1"}:
        conflicts.append(
            f"--host {requested_host} cannot be applied to the running loopback engine"
        )
    backend = str(getattr(args, "room_repository_backend", "sqlite") or "sqlite")
    if backend != "sqlite":
        conflicts.append(
            f"--room-repository-backend {backend} cannot be applied to an already running engine"
        )
    if str(getattr(args, "public_url", "") or "").strip():
        conflicts.append("--public-url cannot be applied to an already running engine")
    if str(getattr(args, "host_token", "") or "").strip():
        conflicts.append("--host-token cannot be applied to an already running engine")
    if bool(getattr(args, "unsafe_expose_control_plane", False)):
        conflicts.append(
            "--unsafe-expose-control-plane cannot be applied to an already running engine"
        )
    if bool(getattr(args, "start_public_tunnel", False)):
        conflicts.append(
            "--start-public-tunnel cannot be applied to an already running engine"
        )
    shadow = str(getattr(args, "attention_shadow_mode", "off") or "off")
    if shadow not in {"", "off"}:
        conflicts.append(
            f"--attention-shadow-mode {shadow} cannot be applied to an already running engine"
        )
    return conflicts


def run_gui_command(
    args: argparse.Namespace,
    *,
    serve_gui: Callable[..., object],
) -> int:
    from agentsassemble.application.local_engine_registry import (
        LocalEngineStartupTimeout,
        claim_local_engine_startup,
    )
    from agentsassemble.application.user_data_root import resolve_output_root

    output_root = resolve_output_root(getattr(args, "output_root", None))
    bind_port = 8765 if getattr(args, "port", None) is None else int(args.port)
    try:
        with claim_local_engine_startup(output_root) as existing:
            if existing is not None:
                conflicts = gui_reuse_conflicts(args, existing)
                if conflicts:
                    print(
                        "error: an AgentsAssemble engine is already running for "
                        f"{output_root} at {existing}. Refusing to ignore: "
                        + "; ".join(conflicts),
                        file=sys.stderr,
                    )
                    return 2
                if os.environ.get("AGENTSASSEMBLE_DESKTOP_RUNTIME") == "1":
                    from agentsassemble.application.gui_runtime import (
                        DESKTOP_RUNTIME_URL_PREFIX,
                    )

                    print(f"{DESKTOP_RUNTIME_URL_PREFIX}{existing}", flush=True)
                print(
                    f"AgentsAssemble local engine already running for {output_root}: {existing}",
                    flush=True,
                )
                print(
                    "Reusing the existing engine instead of starting a second process.",
                    flush=True,
                )
                return 0
            serve_gui(
                host=args.host,
                port=bind_port,
                output_root=output_root,
                room_repository_backend=args.room_repository_backend,
                room_postgres_dsn_env=args.room_postgres_dsn_env,
                attention_shadow_mode=args.attention_shadow_mode,
                public_url=args.public_url,
                host_token=args.host_token,
                unsafe_expose_control_plane=args.unsafe_expose_control_plane,
                start_public_tunnel=args.start_public_tunnel,
            )
    except (ValueError, RoomRepositoryUnavailable, LocalEngineStartupTimeout) as error:
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
        "is_default_entry_point": True,
        "launch_commands": [
            f"python3 -m agentsassemble.cli gui --host {backend_host} --port {backend_port} --output-root .agentsassemble",
            "npm --prefix frontend run build",
            "cd frontend && AGENTSASSEMBLE_API_TARGET=" + backend_url + " npm run dev",
        ],
        "notes": [
            "assemble gui serves the Discord-style React room client at / once npm --prefix frontend run build exists.",
            "Until that build exists, / and /app/ return a build-required response.",
            "The React/Vite frontend reads existing HTTP/SSE state and does not start provider CLIs.",
            "The Vite proxy should target the same backend URL shown here unless AGENTSASSEMBLE_API_TARGET overrides it.",
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
    host_token_env = str(
        getattr(args, "host_token_env", "AGENTSASSEMBLE_HOST_TOKEN")
        or "AGENTSASSEMBLE_HOST_TOKEN"
    )
    host_token = os.environ.get(host_token_env, "")

    if getattr(args, "status", False):
        payload, error = _rolling_restart_call(
            url,
            method="GET",
            host_token=host_token,
        )
        if error:
            print(error, file=sys.stderr)
            return 2
        print(
            json.dumps(payload, ensure_ascii=False, indent=2)
            if as_json
            else _format_rolling_restart_status(payload)
        )
        return 0 if payload.get("supported") is True else 2

    deadline = time.monotonic() + max(0.0, float(getattr(args, "wait", 0.0) or 0.0))
    while True:
        payload, error = _rolling_restart_call(
            url,
            method="POST",
            host_token=host_token,
        )
        if error:
            print(error, file=sys.stderr)
            return 2
        blockers = payload.get("blockers") or []
        blocked = payload.get("accepted") is not True
        if not blocked or not blockers or time.monotonic() >= deadline:
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
        print("- The listener stays available; connected clients reconnect and resync.")
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
    host_token: str = "",
) -> tuple[dict[str, object], str]:
    headers = {"Content-Type": "application/json"}
    if host_token:
        headers["X-Host-Token"] = host_token
    request = urllib.request.Request(
        url,
        method=method,
        data=b"{}" if method == "POST" else None,
        headers=headers,
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
