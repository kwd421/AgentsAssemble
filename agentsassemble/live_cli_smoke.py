from __future__ import annotations

import json
import re
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from agentsassemble.meeting_events import clean_lobby_text
from agentsassemble.providers.live_cli import GENERAL_ROOM_ID, LiveCliRuntime


DEFAULT_LIVE_CLI_SMOKE_CONFIG = Path("configs/live-cli-providers.example.json")


def run_live_cli_smoke(
    *,
    config_path: str | Path = DEFAULT_LIVE_CLI_SMOKE_CONFIG,
    output_root: str | Path = ".agentsassemble",
    providers: list[str] | None = None,
    approve_real_provider: bool = False,
    timeout_seconds: float = 120.0,
    reporter: Callable[[dict[str, object]], None] | None = None,
) -> dict[str, object]:
    """Run an opt-in live CLI session smoke for configured provider commands."""

    selected = [clean_lobby_text(provider, limit=128) for provider in (providers or []) if provider]
    config = _load_config(Path(config_path))
    provider_specs = _selected_provider_specs(config, selected)
    run_id = "smoke_" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    result: dict[str, object] = {
        "run_id": run_id,
        "room_id": clean_lobby_text(config.get("room_id"), limit=128) or GENERAL_ROOM_ID,
        "config_path": str(Path(config_path)),
        "approved": bool(approve_real_provider),
        "requires_approval": True,
        "started_at": datetime.now(UTC).isoformat(),
        "providers": [],
    }
    if not approve_real_provider:
        result["status"] = "skipped"
        result["finished_at"] = datetime.now(UTC).isoformat()
        result["result_path"] = str(_write_smoke_result(output_root, result))
        return result

    for spec in provider_specs:
        provider_result = _run_provider_smoke(spec, timeout_seconds=max(0.1, float(timeout_seconds)), reporter=reporter)
        result["providers"].append(provider_result)  # type: ignore[index]
    result["finished_at"] = datetime.now(UTC).isoformat()
    result["status"] = _overall_status(result["providers"])  # type: ignore[arg-type]
    result["result_path"] = str(_write_smoke_result(output_root, result))
    return result


def _load_config(config_path: Path) -> dict[str, object]:
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        payload = {"providers": []}
    if not isinstance(payload, dict):
        raise ValueError("live CLI smoke config must be a JSON object")
    return payload


def _selected_provider_specs(config: dict[str, object], providers: list[str]) -> list[dict[str, object]]:
    raw_specs = config.get("providers")
    specs = [dict(item) for item in raw_specs if isinstance(item, dict)] if isinstance(raw_specs, list) else []
    if not providers:
        return specs
    provider_set = {provider.casefold() for provider in providers}
    selected = [
        spec
        for spec in specs
        if clean_lobby_text(spec.get("id"), limit=128).casefold() in provider_set
    ]
    known = {clean_lobby_text(spec.get("id"), limit=128).casefold() for spec in specs}
    for provider in providers:
        if provider.casefold() not in known:
            selected.append({"id": provider, "kind": "live_cli", "display_name": provider, "command": [provider]})
    return selected


def _run_provider_smoke(
    spec: dict[str, object],
    *,
    timeout_seconds: float,
    reporter: Callable[[dict[str, object]], None] | None,
) -> dict[str, object]:
    agent_id = clean_lobby_text(spec.get("id"), limit=128)
    display_name = clean_lobby_text(spec.get("display_name"), limit=128) or agent_id
    first_prompt = str(spec.get("session_probe_prompt") or f"AGENTSASSEMBLE_SESSION_MARKER={agent_id}-001 를 기억해줘. 짧게 확인만 해.")
    second_prompt = str(spec.get("memory_check_prompt") or "아까 내가 준 AGENTSASSEMBLE_SESSION_MARKER 값을 말해줘.")
    command = _command_for_prompt(spec, first_prompt)
    base = {
        "agent_id": agent_id,
        "display_name": display_name,
        "kind": clean_lobby_text(spec.get("kind"), limit=64) or "live_cli",
        "command": command,
        "command_display": " ".join(command),
        "resolved_executable": _resolve_command(command),
        "pty": True,
        "transport": "pty",
        "is_one_shot": False,
        "status": "pending",
        "last_error": "",
        "pid_first_turn": None,
        "pid_second_turn": None,
        "same_pid_over_turns": False,
        "memory_marker": "",
        "memory_marker_recalled": False,
        "ttfo_ms": [],
        "total_turn_ms": [],
        "output_tail": "",
        "alive_after_stop": False,
    }
    _report(reporter, {"type": "smoke_progress", "provider": agent_id, "phase": "resolve", "status": "running"})
    if not command or not base["resolved_executable"]:
        base["status"] = "unavailable"
        base["last_error"] = "configured command missing"
        _report(reporter, {"type": "smoke_progress", "provider": agent_id, "phase": "resolve", "status": "unavailable"})
        return base

    cwd_value = str(spec.get("cwd") or "").strip()
    cwd = Path(cwd_value).expanduser() if cwd_value else None
    runtime = LiveCliRuntime(
        agent_id,
        command,
        cwd=cwd,
        idle_quiet_seconds=float(spec.get("quiet_seconds") or 0.35),
        submit_newline=str(spec.get("submit_newline") or "\r"),
        input_mode=str(spec.get("input_mode") or "line"),
        terminal_rows=int(spec.get("terminal_rows") or 40),
        terminal_columns=int(spec.get("terminal_columns") or 120),
    )
    base["memory_marker"] = _marker_from_prompt(first_prompt)
    try:
        _report(reporter, {"type": "smoke_progress", "provider": agent_id, "phase": "start", "status": "running"})
        runtime.start()
        base.update(_runtime_provenance(runtime.health()))
        startup = _handle_startup(runtime, spec, timeout_seconds=min(10.0, timeout_seconds))
        if startup:
            base["startup_tail"] = startup[-4000:]
        if _uses_initial_prompt_args(spec):
            first = _read_turn(runtime, timeout_seconds=timeout_seconds)
        else:
            first = _run_turn(runtime, "smoke_probe", first_prompt, timeout_seconds)
        base["pid_first_turn"] = runtime.health().get("pid")
        base["ttfo_ms"].append(first["ttfo_ms"])  # type: ignore[union-attr]
        base["total_turn_ms"].append(first["total_turn_ms"])  # type: ignore[union-attr]
        base["output_tail"] = str(first.get("content") or "")[-4000:]
        _report(reporter, {"type": "smoke_progress", "provider": agent_id, "phase": "memory_check", "status": "running"})
        second = _run_turn(runtime, "smoke_memory", second_prompt, timeout_seconds)
        base["pid_second_turn"] = runtime.health().get("pid")
        base["ttfo_ms"].append(second["ttfo_ms"])  # type: ignore[union-attr]
        base["total_turn_ms"].append(second["total_turn_ms"])  # type: ignore[union-attr]
        base["same_pid_over_turns"] = bool(base["pid_first_turn"] and base["pid_first_turn"] == base["pid_second_turn"])
        marker = str(base["memory_marker"] or "")
        base["memory_marker_recalled"] = _marker_recalled(marker, str(second.get("content") or ""))
        base["output_tail"] = str(second.get("content") or "")[-4000:]
        if not base["same_pid_over_turns"]:
            raise RuntimeError("live CLI process pid changed between smoke turns")
        if marker and not base["memory_marker_recalled"]:
            raise RuntimeError("live CLI memory marker was not recalled")
        base["status"] = "ok"
        _report(reporter, {"type": "smoke_progress", "provider": agent_id, "phase": "complete", "status": "ok"})
    except Exception as error:
        base["status"] = "error"
        base["last_error"] = str(error)
        _report(reporter, {"type": "smoke_progress", "provider": agent_id, "phase": "error", "status": "error", "message": str(error)})
    finally:
        try:
            runtime.stop(timeout_seconds=2)
        finally:
            base["alive_after_stop"] = bool(runtime.health().get("running"))
    return base


def _run_turn(runtime: LiveCliRuntime, event_id: str, prompt: str, timeout_seconds: float) -> dict[str, object]:
    event = {
        "event_id": event_id,
        "actor_id": "smoke",
        "actor_type": "user",
        "kind": "user_message",
        "content": prompt,
    }
    first_output_at: float | None = None
    queued_at = time.monotonic()
    runtime.deliver([event])
    input_done_at = time.monotonic()

    def on_delta(_delta: str) -> None:
        nonlocal first_output_at
        if first_output_at is None:
            first_output_at = time.monotonic()

    output = runtime.read_output(timeout_seconds=timeout_seconds, on_delta=on_delta)
    completed_at = time.monotonic()
    return {
        "content": str(output.get("content") or ""),
        "ttfo_ms": _elapsed_ms(first_output_at or completed_at, input_done_at),
        "total_turn_ms": _elapsed_ms(completed_at, queued_at),
    }


def _read_turn(runtime: LiveCliRuntime, *, timeout_seconds: float) -> dict[str, object]:
    first_output_at: float | None = None
    queued_at = time.monotonic()

    def on_delta(_delta: str) -> None:
        nonlocal first_output_at
        if first_output_at is None:
            first_output_at = time.monotonic()

    output = runtime.read_output(timeout_seconds=timeout_seconds, on_delta=on_delta)
    completed_at = time.monotonic()
    return {
        "content": str(output.get("content") or ""),
        "ttfo_ms": _elapsed_ms(first_output_at or completed_at, queued_at),
        "total_turn_ms": _elapsed_ms(completed_at, queued_at),
    }


def _command_for_prompt(spec: dict[str, object], first_prompt: str) -> list[str]:
    command = [str(item) for item in spec.get("command", [])] if isinstance(spec.get("command"), list) else []
    if not _uses_initial_prompt_args(spec):
        return command
    raw_args = spec.get("initial_prompt_args")
    args = [str(item) for item in raw_args] if isinstance(raw_args, list) else ["{prompt}"]
    return command + [arg.replace("{prompt}", first_prompt) for arg in args]


def _uses_initial_prompt_args(spec: dict[str, object]) -> bool:
    return bool(spec.get("initial_prompt_args") or spec.get("append_initial_prompt"))


def _handle_startup(runtime: LiveCliRuntime, spec: dict[str, object], *, timeout_seconds: float) -> str:
    startup_wait = float(spec.get("startup_wait_seconds") or 0)
    if startup_wait > 0:
        time.sleep(min(startup_wait, timeout_seconds))
    output = runtime.read_available(timeout_seconds=0.5)
    text = str(output.get("content") or "")
    accept_contains = str(spec.get("startup_accept_contains") or "")
    if accept_contains and accept_contains in text:
        runtime.send_keys(str(spec.get("startup_accept_keys") or "\r"))
        time.sleep(float(spec.get("startup_after_accept_wait_seconds") or 1.0))
        output = runtime.read_available(timeout_seconds=0.5)
        text += str(output.get("content") or "")
    return text


def _runtime_provenance(health: dict[str, object]) -> dict[str, object]:
    return {
        "resolved_executable": str(health.get("resolved_executable") or ""),
        "pid": health.get("pid"),
        "cwd": str(health.get("cwd") or ""),
        "workspace_dir": str(health.get("workspace_dir") or ""),
        "started_at": str(health.get("started_at") or ""),
    }


def _resolve_command(command: list[str]) -> str:
    if not command:
        return ""
    first = command[0]
    if "/" in first:
        path = Path(first).expanduser()
        return str(path) if path.exists() else ""
    return shutil.which(first) or ""


def _marker_from_prompt(prompt: str) -> str:
    match = re.search(r"AGENTSASSEMBLE_SESSION_MARKER=([A-Za-z0-9_.-]+)", prompt)
    return match.group(1) if match else ""


def _marker_recalled(marker: str, content: str) -> bool:
    if not marker:
        return False
    if marker in content:
        return True
    compact_marker = re.sub(r"[^A-Za-z0-9]+", "", marker).casefold()
    compact_content = re.sub(r"[^A-Za-z0-9]+", "", content).casefold()
    if compact_marker and compact_marker in compact_content:
        return True
    marker_parts = [part for part in re.split(r"[^A-Za-z0-9]+", marker) if part]
    if len(marker_parts) < 2:
        return False
    search_from = 0
    for part in marker_parts:
        index = compact_content.find(part.casefold(), search_from)
        if index < 0:
            return False
        search_from = index + len(part)
    return True


def _overall_status(provider_results: list[object]) -> str:
    results = [dict(item) for item in provider_results if isinstance(item, dict)]
    statuses = {str(item.get("status") or "") for item in results}
    if not results:
        return "empty"
    if statuses <= {"unavailable"}:
        return "unavailable"
    if "error" in statuses:
        return "error"
    return "ok"


def _write_smoke_result(output_root: str | Path, result: dict[str, object]) -> Path:
    path = Path(output_root) / "rooms" / str(result.get("room_id") or GENERAL_ROOM_ID) / "smoke"
    path.mkdir(parents=True, exist_ok=True)
    result_path = path / f"{result.get('run_id')}.json"
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return result_path


def _elapsed_ms(end: float, start: float) -> int:
    return max(0, int(round((end - start) * 1000)))


def _report(reporter: Callable[[dict[str, object]], None] | None, payload: dict[str, object]) -> None:
    if reporter is None:
        return
    reporter(payload)
