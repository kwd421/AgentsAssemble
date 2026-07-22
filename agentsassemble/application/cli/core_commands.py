"""Execution and output formatting for current core CLI commands."""
from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
from pathlib import Path

from agentsassemble.diagnostics.release_health import (
    DEFAULT_RELEASE_HEALTH_TIMEOUT_SECONDS,
    ReleaseHealthSelectionError,
    release_health_catalog_payload,
    run_release_health_checks,
    write_latest_release_health_report,
)
from agentsassemble.web.frontend_runtime import frontend_dist_status


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
