from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable, Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RELEASE_HEALTH_TIMEOUT_SECONDS = 300.0
OUTPUT_TAIL_LIMIT = 800
ENV_ASSIGNMENT_PATTERN = re.compile(r"^[A-Z_][A-Z0-9_]*=.*$")


class ReleaseHealthSelectionError(ValueError):
    pass


@dataclass(frozen=True)
class ReleaseHealthCheck:
    id: str
    label: str
    kind: str
    category: str
    requires: tuple[str, ...]
    optional: bool = False

    def catalog_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "label": self.label,
            "kind": self.kind,
            "category": self.category,
            "requires": list(self.requires),
            "optional": self.optional,
        }


RELEASE_HEALTH_CHECKS: tuple[ReleaseHealthCheck, ...] = (
    ReleaseHealthCheck(
        id="node_check_static",
        label="Node syntax check for vanilla static JavaScript",
        kind="syntax",
        category="frontend_static",
        requires=("node",),
    ),
    ReleaseHealthCheck(
        id="unittest_static_ui_assets",
        label="Static UI asset contract tests",
        kind="unit",
        category="frontend_static",
        requires=("python3",),
    ),
    ReleaseHealthCheck(
        id="unittest_docs_architecture",
        label="Documentation architecture contract tests",
        kind="unit",
        category="docs",
        requires=("python3",),
    ),
    ReleaseHealthCheck(
        id="unittest_mcp_server",
        label="MCP participant/archive boundary tests",
        kind="unit",
        category="mcp",
        requires=("python3",),
    ),
    ReleaseHealthCheck(
        id="unittest_gui_and_live_agent_smoke",
        label="GUI and live-agent smoke tests",
        kind="integration",
        category="gui_live_agent",
        requires=("python3",),
    ),
    ReleaseHealthCheck(
        id="compileall_package",
        label="Python package compile check",
        kind="syntax",
        category="python",
        requires=("python3",),
    ),
    ReleaseHealthCheck(
        id="git_diff_check",
        label="Git whitespace diff check",
        kind="format",
        category="git",
        requires=("git",),
    ),
    ReleaseHealthCheck(
        id="room_event_benchmark",
        label="Room event log benchmark (numeric latency evidence, opt-in)",
        kind="benchmark",
        category="live_room",
        requires=("python3",),
        optional=True,
    ),
)
RELEASE_HEALTH_CHECK_IDS = [check.id for check in RELEASE_HEALTH_CHECKS]
_CHECKS_BY_ID = {check.id: check for check in RELEASE_HEALTH_CHECKS}


def release_health_catalog_payload(
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    generated_at = now or datetime.now(UTC)
    return {
        "status": "ok",
        "schema_version": 1,
        "generated_at": generated_at.isoformat(),
        "checks": [check.catalog_dict() for check in RELEASE_HEALTH_CHECKS],
    }


def validate_release_health_check_selection(
    *,
    check_ids: Iterable[str] | None = None,
    skip_ids: Iterable[str] | None = None,
) -> list[ReleaseHealthCheck]:
    requested = [str(item).strip() for item in check_ids or [] if str(item).strip()]
    skipped = [str(item).strip() for item in skip_ids or [] if str(item).strip()]
    unknown = sorted({item for item in requested + skipped if item not in _CHECKS_BY_ID})
    if unknown:
        raise ReleaseHealthSelectionError(f"Unknown release-health check id: {', '.join(unknown)}")

    selected_ids = requested if requested else [check.id for check in RELEASE_HEALTH_CHECKS if not check.optional]
    skip_set = set(skipped)
    return [_CHECKS_BY_ID[check_id] for check_id in selected_ids if check_id not in skip_set]


def run_release_health_checks(
    *,
    check_ids: Iterable[str] | None = None,
    skip_ids: Iterable[str] | None = None,
    timeout_seconds: float = DEFAULT_RELEASE_HEALTH_TIMEOUT_SECONDS,
    runner: Callable[..., object] | None = None,
    now_fn: Callable[[], datetime] | None = None,
    repo_root: Path = REPO_ROOT,
) -> dict[str, object]:
    selected = validate_release_health_check_selection(check_ids=check_ids, skip_ids=skip_ids)
    process_runner = runner or subprocess.run
    clock = now_fn or (lambda: datetime.now(UTC))
    started_at = clock()
    monotonic_started = time.monotonic()
    results = [
        _run_release_health_check(
            check,
            process_runner=process_runner,
            timeout_seconds=max(0.0, float(timeout_seconds)),
            repo_root=repo_root,
        )
        for check in selected
    ]
    completed_at = clock()
    summary = _release_health_summary(results)
    return {
        "status": "ok" if summary["ok"] else "failed",
        "schema_version": 1,
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "duration_seconds": round(max(0.0, time.monotonic() - monotonic_started), 3),
        "summary": summary,
        "results": results,
    }


def sanitize_release_health_output(
    value: object,
    *,
    repo_root: Path = REPO_ROOT,
    limit: int = OUTPUT_TAIL_LIMIT,
) -> str:
    text = _safe_text(value)
    root_text = str(repo_root.resolve())
    home_text = str(Path.home().resolve())
    lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if ENV_ASSIGNMENT_PATTERN.match(stripped):
            continue
        redacted = line.replace(root_text, "<repo>")
        if home_text and home_text != root_text:
            redacted = redacted.replace(home_text, "<home>")
        lines.append(redacted)
    sanitized = "\n".join(lines)
    max_len = max(0, int(limit))
    if max_len and len(sanitized) > max_len:
        return sanitized[-max_len:]
    return sanitized


def _run_release_health_check(
    check: ReleaseHealthCheck,
    *,
    process_runner: Callable[..., object],
    timeout_seconds: float,
    repo_root: Path,
) -> dict[str, object]:
    check_started = time.monotonic()
    commands = _commands_for_check(check, repo_root=repo_root)
    if not commands:
        return _release_health_result(
            check,
            status="skipped",
            duration_seconds=time.monotonic() - check_started,
            skipped_reason="no_matching_files",
        )

    stdout_parts: list[object] = []
    stderr_parts: list[object] = []
    exit_code = 0
    for command in commands:
        try:
            completed = process_runner(
                command,
                cwd=repo_root,
                text=True,
                capture_output=True,
                check=False,
                timeout=timeout_seconds,
                shell=False,
            )
        except FileNotFoundError:
            return _release_health_result(
                check,
                status="skipped",
                duration_seconds=time.monotonic() - check_started,
                skipped_reason=f"missing_tool:{command[0]}",
            )
        except subprocess.TimeoutExpired as error:
            stdout_parts.append(error.output or getattr(error, "stdout", "") or "")
            stderr_parts.append(error.stderr or "")
            stderr_parts.append(f"release-health check timed out after {timeout_seconds:.1f}s")
            return _release_health_result(
                check,
                status="failed",
                duration_seconds=time.monotonic() - check_started,
                exit_code=None,
                stdout_tail=_joined_output(stdout_parts),
                stderr_tail=_joined_output(stderr_parts),
                repo_root=repo_root,
            )
        stdout_parts.append(getattr(completed, "stdout", "") or "")
        stderr_parts.append(getattr(completed, "stderr", "") or "")
        exit_code = int(getattr(completed, "returncode", 1) or 0)
        if exit_code != 0:
            return _release_health_result(
                check,
                status="failed",
                duration_seconds=time.monotonic() - check_started,
                exit_code=exit_code,
                stdout_tail=_joined_output(stdout_parts),
                stderr_tail=_joined_output(stderr_parts),
                repo_root=repo_root,
            )

    return _release_health_result(
        check,
        status="passed",
        duration_seconds=time.monotonic() - check_started,
        exit_code=exit_code,
        stdout_tail=_joined_output(stdout_parts),
        stderr_tail=_joined_output(stderr_parts),
        repo_root=repo_root,
    )


def _commands_for_check(check: ReleaseHealthCheck, *, repo_root: Path) -> list[list[str]]:
    if check.id == "node_check_static":
        return [["node", "--check", str(path.relative_to(repo_root))] for path in sorted((repo_root / "agentsassemble" / "static").glob("*.js"))]
    if check.id == "unittest_static_ui_assets":
        return [["python3", "-m", "unittest", "tests.test_static_ui_assets", "-v"]]
    if check.id == "unittest_docs_architecture":
        return [["python3", "-m", "unittest", "tests.test_docs_architecture", "-v"]]
    if check.id == "unittest_mcp_server":
        return [["python3", "-m", "unittest", "tests.test_mcp_server", "-v"]]
    if check.id == "unittest_gui_and_live_agent_smoke":
        return [["python3", "-m", "unittest", "tests.test_gui_server", "tests.test_live_agent_smoke", "-v"]]
    if check.id == "compileall_package":
        return [["python3", "-m", "compileall", "-q", "agentsassemble"]]
    if check.id == "git_diff_check":
        return [["git", "diff", "--check"]]
    if check.id == "room_event_benchmark":
        return [
            [
                "python3",
                "-m",
                "agentsassemble.cli",
                "live-agent",
                "room-benchmark",
                "--events",
                "120",
                "--read-window",
                "20",
                "--warmup-events",
                "10",
                "--agent-count",
                "3",
                "--json",
            ]
        ]
    return []


def _release_health_result(
    check: ReleaseHealthCheck,
    *,
    status: str,
    duration_seconds: float,
    exit_code: int | None = None,
    stdout_tail: object = "",
    stderr_tail: object = "",
    skipped_reason: str = "",
    repo_root: Path = REPO_ROOT,
) -> dict[str, object]:
    result: dict[str, object] = {
        "id": check.id,
        "label": check.label,
        "category": check.category,
        "kind": check.kind,
        "status": status,
        "exit_code": exit_code,
        "duration_seconds": round(max(0.0, duration_seconds), 3),
        "stdout_tail": sanitize_release_health_output(stdout_tail, repo_root=repo_root),
        "stderr_tail": sanitize_release_health_output(stderr_tail, repo_root=repo_root),
    }
    if skipped_reason:
        result["skipped_reason"] = skipped_reason
    return result


def _release_health_summary(results: list[dict[str, object]]) -> dict[str, object]:
    passed = sum(1 for result in results if result.get("status") == "passed")
    failed = sum(1 for result in results if result.get("status") == "failed")
    skipped = sum(1 for result in results if result.get("status") == "skipped")
    return {
        "total": len(results),
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "ok": failed == 0,
    }


def _safe_text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value or "")


def _joined_output(parts: Iterable[object]) -> str:
    return "\n".join(text for text in (_safe_text(part) for part in parts) if text)
