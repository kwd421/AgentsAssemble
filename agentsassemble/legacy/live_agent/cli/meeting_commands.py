"""Retained live-agent meeting and official-turn CLI commands."""
from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from agentsassemble.application.cli.common import MAX_LIVE_AGENT_ROUND_BATCH


MAX_LIVE_AGENT_SEQUENCE_TURNS = 12


@dataclass(frozen=True)
class LegacyMeetingCliRuntime:
    request_json: Callable[..., dict[str, object]]
    server_url: Callable[[str, str], str]
    operation_http_timeout: Callable[..., float]


def run_legacy_meeting_command(
    args: argparse.Namespace,
    *,
    runtime: LegacyMeetingCliRuntime,
) -> int | None:
    handlers = {
        "call": _run_call,
        "call-sequence": _run_call_sequence,
        "call-round": _run_call_round,
        "call-preset": _run_call_preset,
        "flow": _run_flow,
        "room-benchmark": _run_room_benchmark,
        "call-remaining-rounds": _run_call_remaining_rounds,
        "review-checkpoint": _run_review_checkpoint,
        "start-meeting": _run_start_meeting,
        "finalize-meeting": _run_finalize_meeting,
    }
    handler = handlers.get(str(getattr(args, "live_agent_command", "")))
    return handler(args, runtime) if handler is not None else None


def _run_call(args: argparse.Namespace, runtime: LegacyMeetingCliRuntime) -> int:
    meeting_id = urllib.parse.quote(args.meeting_id, safe="")
    payload = {
        "agent_id": args.agent_id,
        "role_id": args.role_id,
        "display_name": args.display_name,
        "content": " ".join(args.message),
        "turn_id": args.turn_id,
        "turn_index": args.turn_index,
    }
    if args.wait:
        payload["timeout_seconds"] = float(args.timeout)
        response = runtime.request_json(
            runtime.server_url(args.server, f"/api/meetings/{meeting_id}/live-agent-turns/call"),
            method="POST",
            payload=payload,
            timeout_seconds=runtime.operation_http_timeout(float(args.timeout)),
        )
        if args.as_json:
            print(json.dumps(response, ensure_ascii=False, indent=2))
        else:
            request_event = response.get("request_event") if isinstance(response.get("request_event"), dict) else {}
            reply_event = response.get("reply_event") if isinstance(response.get("reply_event"), dict) else {}
            if response.get("status") == "answered":
                print(
                    f"Answered {reply_event.get('actor_id') or args.agent_id} "
                    f"official turn {reply_event.get('id') or 'reply'}"
                )
            else:
                print(
                    f"Timed out waiting for {request_event.get('target_agent_id') or args.agent_id} "
                    f"official turn {request_event.get('id') or 'request'}"
                )
        return 0 if response.get("status") == "answered" else 1
    response = runtime.request_json(
        runtime.server_url(args.server, f"/api/meetings/{meeting_id}/live-agent-turns/request"),
        method="POST",
        payload=payload,
    )
    if args.as_json:
        print(json.dumps(response, ensure_ascii=False, indent=2))
        return 0
    event = response.get("event") if isinstance(response.get("event"), dict) else {}
    print(f"Called {event.get('target_agent_id') or args.agent_id} for official turn {event.get('id') or 'request'}")
    return 0


def _run_call_sequence(args: argparse.Namespace, runtime: LegacyMeetingCliRuntime) -> int:
    meeting_id = urllib.parse.quote(args.meeting_id, safe="")
    turns = _load_live_agent_sequence_turns(args)
    payload = {
        "turns": turns,
        "timeout_seconds": float(args.timeout),
        "stop_on_timeout": bool(args.stop_on_timeout),
    }
    response = runtime.request_json(
        runtime.server_url(args.server, f"/api/meetings/{meeting_id}/live-agent-turns/sequence"),
        method="POST",
        payload=payload,
        timeout_seconds=runtime.operation_http_timeout(float(args.timeout), windows=max(1, len(turns))),
    )
    if args.as_json:
        print(json.dumps(response, ensure_ascii=False, indent=2))
    else:
        print(
            "Official turn sequence "
            f"{response.get('status') or 'unknown'}: "
            f"{response.get('answered_count', 0)} answered, "
            f"{response.get('timeout_count', 0)} timed out, "
            f"{response.get('skipped_count', 0)} skipped"
        )
        for result in response.get("results", []):
            if not isinstance(result, dict):
                continue
            print(f"- {result.get('agent_id') or 'unknown'}: {_sequence_result_summary(result)}")
    return 0 if response.get("status") == "answered" else 1


def _run_call_round(args: argparse.Namespace, runtime: LegacyMeetingCliRuntime) -> int:
    meeting_id = urllib.parse.quote(args.meeting_id, safe="")
    payload = {
        "round_id": args.round_id,
        "role_ids": list(args.role_ids or []),
        "timeout_seconds": float(args.timeout),
        "stop_on_timeout": bool(args.stop_on_timeout),
    }
    instruction = " ".join(args.instruction).strip()
    if instruction:
        payload["content"] = instruction
    turn_windows = len(args.role_ids) if args.role_ids else MAX_LIVE_AGENT_SEQUENCE_TURNS
    response = runtime.request_json(
        runtime.server_url(args.server, f"/api/meetings/{meeting_id}/live-agent-turns/round"),
        method="POST",
        payload=payload,
        timeout_seconds=runtime.operation_http_timeout(float(args.timeout), windows=max(1, turn_windows)),
    )
    if args.as_json:
        print(json.dumps(response, ensure_ascii=False, indent=2))
    else:
        print(
            f"Official round {response.get('round_id') or args.round_id} "
            f"{response.get('status') or 'unknown'}: "
            f"{response.get('answered_count', 0)} answered, "
            f"{response.get('timeout_count', 0)} timed out, "
            f"{response.get('skipped_count', 0)} skipped"
        )
        for result in response.get("results", []):
            if not isinstance(result, dict):
                continue
            print(f"- {result.get('agent_id') or 'unknown'}: {_sequence_result_summary(result)}")
    return 0 if response.get("status") in {"answered", "complete"} else 1


def _run_call_preset(args: argparse.Namespace, runtime: LegacyMeetingCliRuntime) -> int:
    meeting_id = urllib.parse.quote(args.meeting_id, safe="")
    payload = {
        "preset_id": args.preset_id,
        "role_ids": list(args.role_ids or []),
        "timeout_seconds": float(args.timeout),
        "stop_on_timeout": bool(args.stop_on_timeout),
    }
    turn_windows = len(args.role_ids) if args.role_ids else MAX_LIVE_AGENT_SEQUENCE_TURNS
    response = runtime.request_json(
        runtime.server_url(args.server, f"/api/meetings/{meeting_id}/live-agent-turns/preset"),
        method="POST",
        payload=payload,
        timeout_seconds=runtime.operation_http_timeout(float(args.timeout), windows=max(1, turn_windows)),
    )
    if args.as_json:
        print(json.dumps(response, ensure_ascii=False, indent=2))
    else:
        print(
            f"Play preset {response.get('preset_id') or args.preset_id} "
            f"{response.get('status') or 'unknown'}: "
            f"{response.get('answered_count', 0)} answered, "
            f"{response.get('timeout_count', 0)} timed out, "
            f"{response.get('skipped_count', 0)} skipped"
        )
        for result in response.get("results", []):
            if not isinstance(result, dict):
                continue
            print(f"- {result.get('agent_id') or 'unknown'}: {_sequence_result_summary(result)}")
    return 0 if response.get("status") == "answered" else 1


def _run_flow(args: argparse.Namespace, _runtime: LegacyMeetingCliRuntime) -> int:
    print("Play/free flow is disabled; use turn-based Agent Sessions.", file=sys.stderr)
    return 2


def _run_room_benchmark(args: argparse.Namespace, runtime: LegacyMeetingCliRuntime) -> int:
    from agentsassemble.legacy.diagnostics.room_event_benchmark import RoomEventBenchmarkOptions, run_room_event_benchmark

    output_root = Path(args.output_root) if args.output_root else None
    sse_samples = int(args.sse_samples)
    http_handler_factory = None
    if sse_samples:
        from agentsassemble.gui import _make_handler

        http_handler_factory = _make_handler
    result = run_room_event_benchmark(
        RoomEventBenchmarkOptions(
            output_root=output_root,
            events=int(args.events),
            read_window=int(args.read_window),
            warmup_events=int(args.warmup_events),
            agent_count=int(args.agent_count),
            sse_samples=sse_samples,
            cleanup=not bool(args.keep_output),
        ),
        http_handler_factory=http_handler_factory,
    )
    if args.as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        metrics = result.get("metrics") if isinstance(result.get("metrics"), dict) else {}
        lobby_append = metrics.get("lobby_append_ms") if isinstance(metrics.get("lobby_append_ms"), dict) else {}
        live_append = metrics.get("live_append_ms") if isinstance(metrics.get("live_append_ms"), dict) else {}
        lobby_tail = metrics.get("lobby_tail_read_ms") if isinstance(metrics.get("lobby_tail_read_ms"), dict) else {}
        fairness = metrics.get("flow_speaking_distribution") if isinstance(metrics.get("flow_speaking_distribution"), dict) else {}
        lobby_sse = metrics.get("lobby_sse_append_to_frame_ms") if isinstance(metrics.get("lobby_sse_append_to_frame_ms"), dict) else {}
        print("Room event benchmark:")
        print(f"- lobby append avg/p95: {lobby_append.get('avg_ms', 0)} / {lobby_append.get('p95_ms', 0)} ms")
        print(f"- live append avg/p95: {live_append.get('avg_ms', 0)} / {live_append.get('p95_ms', 0)} ms")
        print(f"- lobby tail read: {lobby_tail.get('avg_ms', 0)} ms")
        if lobby_sse:
            print(
                "- lobby SSE append-to-frame avg/p95: "
                f"{lobby_sse.get('avg_ms', 0)} / {lobby_sse.get('p95_ms', 0)} ms "
                f"(samples={lobby_sse.get('count', 0)}, cadence={lobby_sse.get('polling_cadence_seconds', 0)}s, "
                f"keepalive={lobby_sse.get('keepalive_interval_seconds', 0)}s)"
            )
        print(f"- speaking imbalance: {fairness.get('imbalance_ratio', 0)} ({fairness.get('definition', '')})")
    return 0


def _run_call_remaining_rounds(args: argparse.Namespace, runtime: LegacyMeetingCliRuntime) -> int:
    meeting_id = urllib.parse.quote(args.meeting_id, safe="")
    max_rounds = max(1, int(args.max_rounds))
    if max_rounds > MAX_LIVE_AGENT_ROUND_BATCH:
        raise ValueError(f"--max-rounds supports at most {MAX_LIVE_AGENT_ROUND_BATCH}.")
    payload = {
        "timeout_seconds": float(args.timeout),
        "stop_on_timeout": bool(args.stop_on_timeout),
        "max_rounds": max_rounds,
    }
    if getattr(args, "finalize_after_rounds", False):
        payload["finalize_after_rounds"] = True
    response = runtime.request_json(
        runtime.server_url(args.server, f"/api/meetings/{meeting_id}/live-agent-turns/rounds"),
        method="POST",
        payload=payload,
        timeout_seconds=runtime.operation_http_timeout(
            float(args.timeout),
            windows=max_rounds * MAX_LIVE_AGENT_SEQUENCE_TURNS,
        ),
    )
    if args.as_json:
        print(json.dumps(response, ensure_ascii=False, indent=2))
    else:
        finalization = response.get("finalization") if isinstance(response.get("finalization"), dict) else None
        finalization_suffix = ""
        if finalization is not None:
            finalization_suffix = (
                f"; finalization {finalization.get('status') or 'unknown'}: "
                f"{finalization.get('official_event_count', 0)} official events"
            )
        print(
            "Official remaining rounds "
            f"{response.get('status') or 'unknown'}: "
            f"{response.get('round_count', 0)} rounds, "
            f"{response.get('answered_round_count', 0)} answered, "
            f"{response.get('completed_round_count', 0)} already complete, "
            f"{response.get('timeout_round_count', 0)} timed out, "
            f"{response.get('skipped_round_count', 0)} skipped"
            f"{finalization_suffix}"
        )
        for result in response.get("results", []):
            if not isinstance(result, dict):
                continue
            print(f"- {result.get('round_id') or 'unknown'}: {result.get('status') or 'unknown'}")
    if response.get("status") not in {"answered", "complete"}:
        return 1
    finalization = response.get("finalization") if isinstance(response.get("finalization"), dict) else None
    if finalization is not None and finalization.get("status") not in {"finalized", "already_finalized"}:
        return 1
    return 0


def _run_review_checkpoint(args: argparse.Namespace, runtime: LegacyMeetingCliRuntime) -> int:
    meeting_id = urllib.parse.quote(args.meeting_id, safe="")
    payload = {
        "group_id": str(args.group_id or ""),
        "agent_ids": list(args.agent_ids or []),
        "content": " ".join(args.message),
        "checkpoint_id": str(args.checkpoint_id or ""),
        "timeout_seconds": float(args.timeout),
    }
    target_windows = len(args.agent_ids) if args.agent_ids else MAX_LIVE_AGENT_SEQUENCE_TURNS
    response = runtime.request_json(
        runtime.server_url(args.server, f"/api/meetings/{meeting_id}/review-checkpoints"),
        method="POST",
        payload=payload,
        timeout_seconds=runtime.operation_http_timeout(float(args.timeout), windows=max(1, target_windows)),
    )
    if args.as_json:
        print(json.dumps(response, ensure_ascii=False, indent=2))
    else:
        print(
            f"Review checkpoint {response.get('checkpoint_id') or args.checkpoint_id or 'unknown'} "
            f"{response.get('status') or 'unknown'}: "
            f"{response.get('answered_count', 0)}/{response.get('turn_count', 0)} answered, "
            f"{response.get('timeout_count', 0)} timed out, "
            f"{response.get('skipped_count', 0)} skipped"
        )
        reason = str(response.get("reason") or "").strip()
        if reason:
            print(f"reason: {reason}")
        for result in response.get("results", []):
            if not isinstance(result, dict):
                continue
            print(f"- {result.get('agent_id') or 'unknown'}: {_sequence_result_summary(result)}")
    return 0 if response.get("status") == "answered" else 1


def _run_start_meeting(args: argparse.Namespace, runtime: LegacyMeetingCliRuntime) -> int:
    payload = {
        "meeting_id": str(args.meeting_id or ""),
        "council_config_path": str(args.council_config or ""),
        "agent_config_path": str(args.agent_config or ""),
    }
    response = runtime.request_json(
        runtime.server_url(args.server, "/api/live-agent-meetings/start"),
        method="POST",
        payload=payload,
    )
    if args.as_json:
        print(json.dumps(response, ensure_ascii=False, indent=2))
        return 0
    meeting = response.get("meeting") if isinstance(response.get("meeting"), dict) else {}
    roles = meeting.get("roles") if isinstance(meeting.get("roles"), list) else []
    bindings = meeting.get("agent_bindings") if isinstance(meeting.get("agent_bindings"), list) else []
    meeting_id = str(response.get("meeting_id") or meeting.get("meeting_id") or "unknown")
    print(
        f"Started resident live-agent meeting {meeting_id}: "
        f"{len(roles)} roles, {len(bindings)} bound agents"
    )
    return 0


def _run_finalize_meeting(args: argparse.Namespace, runtime: LegacyMeetingCliRuntime) -> int:
    meeting_id = urllib.parse.quote(str(args.meeting_id or ""), safe="")
    response = runtime.request_json(
        runtime.server_url(args.server, f"/api/meetings/{meeting_id}/finalize"),
        method="POST",
        payload={"force": bool(args.force), "close_pending": bool(args.close_pending)},
        timeout_seconds=20.0,
    )
    if args.as_json:
        print(json.dumps(response, ensure_ascii=False, indent=2))
    else:
        print(_format_live_agent_finalize_meeting(response))
    return 0 if response.get("status") in {"finalized", "already_finalized"} else 1


def _format_live_agent_finalize_meeting(response: dict[str, object]) -> str:
    status = str(response.get("status") or "unknown")
    meeting_id = str(response.get("meeting_id") or "unknown")
    official_count = response.get("official_event_count", 0)
    prefix = "Already finalized" if status == "already_finalized" else "Finalized"
    try:
        cancelled_count = max(0, int(response.get("cancelled_pending_count", 0)))
    except (TypeError, ValueError):
        cancelled_count = 0
    if cancelled_count:
        suffix = f", {cancelled_count} pending turn{'s' if cancelled_count != 1 else ''} cancelled"
    else:
        suffix = ""
    return f"{prefix} {meeting_id}: {official_count} official events{suffix}"


def _load_live_agent_sequence_turns(args: argparse.Namespace) -> list[dict[str, object]]:
    if bool(args.turns_json) == bool(args.turns_file):
        raise ValueError("Provide exactly one of --turns-json or --turns-file.")
    text = args.turns_json
    if args.turns_file:
        text = Path(args.turns_file).read_text(encoding="utf-8")
    loaded = json.loads(text)
    if not isinstance(loaded, list) or not loaded:
        raise ValueError("Official turn sequence requires a non-empty JSON array.")
    turns = []
    for index, item in enumerate(loaded):
        if not isinstance(item, dict):
            raise ValueError(f"Official turn sequence item {index} must be an object.")
        turns.append(item)
    return turns


def _sequence_result_summary(result: dict[str, object]) -> str:
    status = str(result.get("status") or "unknown")
    reply_event = result.get("reply_event") if isinstance(result.get("reply_event"), dict) else {}
    request_event = result.get("request_event") if isinstance(result.get("request_event"), dict) else {}
    if status == "answered":
        return f"answered {reply_event.get('id') or 'reply'}"
    if status == "timeout":
        return f"timeout {request_event.get('id') or 'request'}"
    if status == "skipped":
        return "skipped"
    return status
