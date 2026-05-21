#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from datetime import UTC, datetime


DEFAULT_MESSAGE = "Custom self-service agent saw the room event."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Example AgentsAssemble self-service room loop.")
    parser.add_argument("--message", default=DEFAULT_MESSAGE)
    parser.add_argument("--once", action="store_true", help="Handle at most one visible event, then exit.")
    parser.add_argument("--wait-timeout", type=float, default=1.0)
    parser.add_argument("--command-timeout", type=float, default=5.0)
    args = parser.parse_args(argv)

    poll_interval = max(_float_env("AGENTSASSEMBLE_POLL_INTERVAL", 0.5), 0.05)
    default_meeting_id = os.environ.get("AGENTSASSEMBLE_MEETING_ID", "")

    while True:
        wait = _run([*_command_from_env("AGENTSASSEMBLE_WAIT_NEXT_COMMAND"), "--timeout", str(args.wait_timeout)], args.command_timeout)
        if wait.returncode != 0:
            if _heartbeat_cursor_only_observation(wait.stdout, command_timeout=args.command_timeout) and args.once:
                return 0
            if args.once:
                return wait.returncode or 1
            time.sleep(poll_interval)
            continue

        payload = _json_object(wait.stdout)
        if payload.get("status") != "event":
            if args.once:
                return 0
            time.sleep(poll_interval)
            continue

        handled = _handle_event(
            payload,
            message=args.message,
            default_meeting_id=default_meeting_id,
            command_timeout=args.command_timeout,
        )
        if args.once:
            return 0 if handled else 1
        time.sleep(poll_interval)


def _handle_event(payload: dict[str, object], *, message: str, default_meeting_id: str, command_timeout: float) -> bool:
    action = str(payload.get("action") or "")
    source_event_id = str(payload.get("source_event_id") or "")
    if not source_event_id:
        return False

    if action == "official_turn":
        meeting_id = str(payload.get("meeting_id") or default_meeting_id)
        if not meeting_id:
            return False
        _heartbeat("working", last_observed_live_event_id=source_event_id, command_timeout=command_timeout)
        reply_command = _replace_tokens(
            _command_from_env("AGENTSASSEMBLE_OFFICIAL_REPLY_COMMAND_TEMPLATE"),
            {
                "{meeting_id}": meeting_id,
                "{source_event_id}": source_event_id,
                "{message}": message,
            },
        )
        reply = _run(_insert_before_message_separator(reply_command, "--json"), command_timeout)
        if reply.returncode == 0:
            _heartbeat(
                "online",
                last_error="",
                last_reply_at=_now(),
                last_observed_live_event_id=source_event_id,
                command_timeout=command_timeout,
            )
            return True
        _heartbeat("error", last_error="official reply failed", last_observed_live_event_id=source_event_id, command_timeout=command_timeout)
        return False

    if action == "lobby":
        auto_chain_depth = str(payload.get("auto_chain_depth") or "1")
        _heartbeat("working", last_observed_event_id=source_event_id, command_timeout=command_timeout)
        say_command = _replace_tokens(
            _command_from_env("AGENTSASSEMBLE_SAY_COMMAND_TEMPLATE"),
            {
                "{source_event_id}": source_event_id,
                "{auto_chain_depth}": auto_chain_depth,
                "{message}": message,
            },
        )
        reply = _run(_insert_before_message_separator(say_command, "--json"), command_timeout)
        if reply.returncode == 0:
            _heartbeat(
                "online",
                last_error="",
                last_reply_at=_now(),
                last_observed_event_id=source_event_id,
                command_timeout=command_timeout,
            )
            return True
        _heartbeat("error", last_error="lobby reply failed", last_observed_event_id=source_event_id, command_timeout=command_timeout)
        return False

    if action == "return_packet":
        read_command = _command_list(payload.get("read_command"))
        if not read_command:
            _heartbeat(
                "error",
                last_error="return packet read command missing",
                last_observed_live_event_id=source_event_id,
                command_timeout=command_timeout,
            )
            return False
        try:
            packet = _run(read_command, command_timeout)
        except (OSError, ValueError, subprocess.SubprocessError):
            _heartbeat(
                "error",
                last_error="return packet read failed",
                last_observed_live_event_id=source_event_id,
                command_timeout=command_timeout,
            )
            return False
        if packet.returncode != 0:
            _heartbeat(
                "error",
                last_error="return packet read failed",
                last_observed_live_event_id=source_event_id,
                command_timeout=command_timeout,
            )
            return False
        ack_command = _command_list(payload.get("ack_command"))
        if ack_command:
            ack = _run(ack_command, command_timeout)
            if ack.returncode == 0:
                return True
            _heartbeat(
                "error",
                last_error="return packet ack failed",
                last_observed_live_event_id=source_event_id,
                command_timeout=command_timeout,
            )
            return False
        _heartbeat(
            "online",
            last_error="",
            last_observed_live_event_id=source_event_id,
            command_timeout=command_timeout,
        )
        return True

    if action == "observe_lobby":
        ack_command = _command_list(payload.get("ack_command"))
        if ack_command:
            ack = _run(ack_command, command_timeout)
            if ack.returncode == 0:
                return True
            _heartbeat(
                "error",
                last_error="lobby observation ack failed",
                last_observed_event_id=source_event_id,
                command_timeout=command_timeout,
            )
            return False
        _heartbeat(
            "online",
            last_error="",
            last_observed_event_id=source_event_id,
            command_timeout=command_timeout,
        )
        return True

    return False


def _heartbeat_cursor_only_observation(stdout: str, *, command_timeout: float) -> bool:
    payload = _json_object(stdout)
    if payload.get("status") != "timeout":
        return False
    lobby_cursor = str(payload.get("last_observed_event_id") or "")
    live_cursor = str(payload.get("last_observed_live_event_id") or "")
    if not lobby_cursor and not live_cursor:
        return False
    _heartbeat(
        "online",
        last_error="",
        last_observed_event_id=lobby_cursor,
        last_observed_live_event_id=live_cursor,
        command_timeout=command_timeout,
    )
    return True


def _command_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    command = [str(part) for part in value]
    return command if command else []


def _command_from_env(name: str) -> list[str]:
    value = os.environ.get(name, "")
    if not value:
        raise RuntimeError(f"Missing required self-service command template: {name}")
    return shlex.split(value)


def _replace_tokens(command: list[str], replacements: dict[str, str]) -> list[str]:
    replaced = []
    for part in command:
        for token, value in replacements.items():
            part = part.replace(token, value)
        replaced.append(part)
    return replaced


def _insert_before_message_separator(command: list[str], *args: str) -> list[str]:
    marker = command.index("--") if "--" in command else len(command)
    return [*command[:marker], *args, *command[marker:]]


def _run(command: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, timeout=timeout)


def _json_object(text: str) -> dict[str, object]:
    try:
        payload = json.loads(text or "{}")
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _heartbeat(
    status: str,
    *,
    command_timeout: float,
    last_error: str = "",
    last_reply_at: str = "",
    last_observed_event_id: str = "",
    last_observed_live_event_id: str = "",
) -> None:
    try:
        command = _replace_tokens(
            _command_from_env("AGENTSASSEMBLE_HEARTBEAT_COMMAND_TEMPLATE"),
            {
                "{status}": status,
                "{last_error}": last_error,
                "{last_reply_at}": last_reply_at,
                "{last_observed_event_id}": last_observed_event_id,
                "{last_observed_live_event_id}": last_observed_live_event_id,
            },
        )
        _run(command, command_timeout)
    except Exception:
        return


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _now() -> str:
    return datetime.now(UTC).isoformat()


if __name__ == "__main__":
    sys.exit(main())
