"""Execution for current room and Agent Session CLI commands."""
from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Collection

from agentsassemble.application.agent_sessions import clean_agent_session_provider_kind


JsonObject = dict[str, Any]


@dataclass(frozen=True)
class RoomCliRuntime:
    """Root-owned side effects retained for CLI compatibility and testing."""

    request_json: Callable[..., JsonObject]
    server_url: Callable[[str, str], str]
    clean_text: Callable[..., str]
    run_codex_smoke: Callable[..., JsonObject]
    run_native_smoke: Callable[..., JsonObject]
    codex_smoke_commands: Collection[str]
    default_live_cli_smoke_config: Path


def run_room_command(args: argparse.Namespace, *, runtime: RoomCliRuntime) -> int:
    if args.room_command == "purge-admission-workflows":
        from agentsassemble.admission.maintenance_command import purge_admission_workflows
        from agentsassemble.admission.repository import InviteRepositoryError
        from agentsassemble.application.room_repository_factory import (
            RoomRepositoryConfigurationError,
            RoomRepositoryUnavailable,
        )

        try:
            result = purge_admission_workflows(
                output_root=Path(args.output_root),
                repository_backend=str(args.room_repository_backend),
                postgres_dsn_env=str(args.room_postgres_dsn_env),
                updated_before=str(args.before),
                room_id=str(args.room_id or ""),
                apply=bool(args.apply),
            )
        except (
            InviteRepositoryError,
            RoomRepositoryConfigurationError,
            RoomRepositoryUnavailable,
            OSError,
            ValueError,
        ) as error:
            print(f"error: {error}", file=sys.stderr)
            return 2
        if args.as_json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(
                f"Admission workflow purge {result['mode']}: "
                f"selected={result['selected_count']} · purged={result['purged_count']}"
            )
            if not args.apply:
                print("Review the dry-run result, then repeat with --apply to delete it.")
        return 0

    if args.room_command == "list":
        query = urllib.parse.urlencode({"include_archived": "true"} if args.include_archived else {})
        path = "/api/rooms" + (f"?{query}" if query else "")
        payload = runtime.request_json(runtime.server_url(args.server, path))
        if args.as_json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            rooms = payload.get("rooms") if isinstance(payload.get("rooms"), list) else []
            if not rooms:
                print("no rooms")
            for room in rooms:
                print(
                    f"{room.get('room_id')}: "
                    f"{room.get('status') or ('archived' if room.get('archived') else 'active')}"
                )
        return 0

    if args.room_command == "status":
        query = urllib.parse.urlencode({"room_id": args.room_id})
        payload = runtime.request_json(runtime.server_url(args.server, f"/api/rooms/state?{query}"))
        if args.as_json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            room = payload.get("room") if isinstance(payload.get("room"), dict) else {}
            participants = (
                payload.get("active_participants")
                if isinstance(payload.get("active_participants"), list)
                else []
            )
            print(f"{room.get('room_id') or args.room_id}: {room.get('status') or 'unknown'}")
            print(f"active participants: {len(participants)}")
        return 0

    if args.room_command == "benchmark":
        from agentsassemble.diagnostics.canonical_room_benchmark import (
            CanonicalRoomBenchmarkOptions,
            run_canonical_room_benchmark,
        )

        result = run_canonical_room_benchmark(
            CanonicalRoomBenchmarkOptions(
                output_root=Path(args.output_root) if args.output_root else None,
                events=int(args.events),
                agent_count=int(args.agent_count),
                read_window=int(args.read_window),
                samples=int(args.samples),
                cleanup=not bool(args.keep_output),
            )
        )
        if args.as_json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            metrics = result.get("metrics") if isinstance(result.get("metrics"), dict) else {}
            latest = metrics.get("latest_window_ms") if isinstance(metrics.get("latest_window_ms"), dict) else {}
            reconnect = metrics.get("reconnect_after_seq_ms") if isinstance(metrics.get("reconnect_after_seq_ms"), dict) else {}
            context = metrics.get("agent_context_ms") if isinstance(metrics.get("agent_context_ms"), dict) else {}
            print(
                f"canonical room benchmark: {result.get('status')} · "
                f"events={result.get('measured_event_count')} · agents={args.agent_count}"
            )
            print(f"- latest window p50/p95: {latest.get('p50_ms')} / {latest.get('p95_ms')} ms")
            print(f"- reconnect p50/p95: {reconnect.get('p50_ms')} / {reconnect.get('p95_ms')} ms")
            print(f"- agent context p50/p95: {context.get('p50_ms')} / {context.get('p95_ms')} ms")
        return 0 if result.get("status") == "ok" else 1

    if args.room_command in {"join", "resume"}:
        payload = {
            "room_id": args.room_id,
            "agent_id": args.agent,
            "session_id": args.session or args.agent,
            "provider_session_id": args.provider_session_id,
            "model": args.model,
            "effort": args.effort,
            "sandbox": args.sandbox,
            "permissions": args.permissions,
            "provider_kind": clean_agent_session_provider_kind(args.provider_kind or args.provider),
            "start": bool(args.start),
            "dry_run": bool(args.dry_run),
        }
        response = runtime.request_json(
            runtime.server_url(args.server, "/api/agent-sessions/resume"),
            method="POST",
            payload=payload,
        )
        if args.as_json:
            print(json.dumps(response, ensure_ascii=False, indent=2))
        else:
            participant = response.get("participant") if isinstance(response.get("participant"), dict) else {}
            process_status = response.get("process_status") or "unknown"
            print(
                f"attached Agent Session {participant.get('participant_id') or args.agent} "
                f"in {args.room_id} · process: {process_status}"
            )
        return 0

    if args.room_command == "attend":
        from agentsassemble.application.room_attendee import run_attendee_from_cli

        return run_attendee_from_cli(
            provider_id=str(args.provider),
            display_name=str(args.display_name or ""),
            workspace=str(args.workspace or ""),
            model=str(args.model or ""),
            reasoning_effort=str(args.effort or ""),
            service_tier=str(args.service_tier or ""),
            variant=str(args.variant or ""),
            permission_mode=str(args.permission_mode or "meeting_read_only"),
        )

    if args.room_command == "connector-mcp":
        from agentsassemble.providers.room_connector_mcp import (
            serve_room_connector_mcp,
        )

        serve_room_connector_mcp()
        return 0

    if args.room_command == "smoke":
        live_cli_providers = [
            runtime.clean_text(provider, limit=128)
            for provider in str(getattr(args, "providers", "") or "").split(",")
            if provider.strip()
        ]
        if live_cli_providers:
            payload = runtime.run_native_smoke(
                config_path=getattr(args, "config", str(runtime.default_live_cli_smoke_config)),
                providers=live_cli_providers,
                approve_real_provider=bool(args.approve_real_provider),
                timeout_seconds=float(getattr(args, "timeout", 120.0) or 120.0),
                latency_samples=int(getattr(args, "latency_samples", 0) or 0),
                agent_conversation=bool(getattr(args, "agent_conversation", False)),
                conversation_seconds=float(getattr(args, "conversation_seconds", 0.0) or 0.0),
                conversation_topic=str(getattr(args, "conversation_topic", "") or ""),
                verify_controls=bool(getattr(args, "verify_controls", False)),
                observe_gui_port=int(getattr(args, "observe_gui_port", 0) or 0),
            )
        elif bool(args.approve_real_provider) and args.room_smoke_command in runtime.codex_smoke_commands:
            payload = runtime.run_codex_smoke(
                args.room_smoke_command,
                approve_real_provider=True,
            )
        else:
            payload = {
                "status": "skipped" if not args.approve_real_provider else "not_run",
                "smoke": args.room_smoke_command or "live-cli",
                "requires_approval": True,
                "approved": bool(args.approve_real_provider),
                "metrics": {
                    "cold_start_ms": None,
                    "warm_turn_ms": [],
                    "time_to_turn_start_ack_ms": [],
                    "time_to_first_notification_ms": [],
                    "time_to_first_agent_delta_ms": [],
                    "time_to_message_final_ms": [],
                    "turn_completed_ms": [],
                    "provider_visible_chars": [],
                    "thread_reused": [],
                    "runtime_reused": [],
                    "runtime_profile_key": [],
                    "rss_kb_start": None,
                    "rss_kb_end": None,
                    "rss_kb_delta": None,
                    "token_usage": [],
                    "context_failures": 0,
                    "errors": [],
                    "distinct_provider_session_id": None,
                },
            }
        if args.as_json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(
                f"{payload['smoke']}: {payload['status']} "
                "(real provider smoke is opt-in and not run by unit tests)"
            )
        return 0 if payload.get("status") in {"ok", "skipped", "not_run"} else 1

    if args.room_command == "turn":
        payload = {
            "room_id": args.room_id,
            "agent_id": args.agent,
            "session_id": args.session or args.agent,
            "instruction": args.instruction,
            "dry_run": bool(args.dry_run),
        }
        response = runtime.request_json(
            runtime.server_url(args.server, "/api/agent-sessions/turn"),
            method="POST",
            payload=payload,
        )
        if args.as_json:
            print(json.dumps(response, ensure_ascii=False, indent=2))
        else:
            print(
                f"ran Agent Session turn {response.get('turn_id') or ''} in {args.room_id}: "
                f"{response.get('turn_status') or response.get('status')}"
            )
        return 0

    if args.room_command == "leave":
        payload = {"room_id": args.room_id, "participant_id": args.agent}
        response = runtime.request_json(
            runtime.server_url(args.server, "/api/room-participants/leave"),
            method="POST",
            payload=payload,
        )
        if args.as_json:
            print(json.dumps(response, ensure_ascii=False, indent=2))
        else:
            print(f"left {args.room_id}: {args.agent}")
        return 0

    return 1
