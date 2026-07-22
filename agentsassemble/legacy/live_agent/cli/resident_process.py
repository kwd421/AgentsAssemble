"""Process environment and lifecycle helpers for legacy residents."""
from __future__ import annotations

import os
import shlex
import signal
import subprocess
import sys
import threading
from collections.abc import Callable, Mapping
from typing import Any

from agentsassemble.live_agent_runner import ResidentAgentConfig


def self_service_process_env(
    config: ResidentAgentConfig,
    *,
    environ: Mapping[str, str] | None = None,
    executable: str | None = None,
) -> dict[str, str]:
    env = dict(os.environ if environ is None else environ)
    command_env = self_service_room_command_env(config, executable=executable)
    env.update(
        {
            "AGENTSASSEMBLE_SERVER": config.server,
            "AGENTSASSEMBLE_AGENT_ID": config.agent_id,
            "AGENTSASSEMBLE_DISPLAY_NAME": config.display_name,
            "AGENTSASSEMBLE_PROVIDER_KIND": config.provider_kind,
            "AGENTSASSEMBLE_CONNECTION_KIND": config.connection_kind,
            "AGENTSASSEMBLE_MEETING_ID": config.meeting_id,
            "AGENTSASSEMBLE_ENGAGEMENT_MODE": config.engagement_mode,
            "AGENTSASSEMBLE_MAX_CHAIN_DEPTH": str(config.max_chain_depth),
            "AGENTSASSEMBLE_POLL_INTERVAL": str(config.poll_interval),
            "AGENTSASSEMBLE_HEARTBEAT_INTERVAL": str(config.heartbeat_interval),
            "AGENTSASSEMBLE_LEGACY_INTERNAL": "1",
        }
    )
    env.update(command_env)
    return env


def self_service_room_command_env(
    config: ResidentAgentConfig,
    *,
    executable: str | None = None,
) -> dict[str, str]:
    base = [executable or sys.executable, "-m", "agentsassemble.cli", "live-agent", "--legacy-internal"]
    identity = ["--server", config.server, "--agent-id", config.agent_id]
    return {
        "AGENTSASSEMBLE_ROOM_COMMAND": shlex.join([*base, "room", *identity]),
        "AGENTSASSEMBLE_WAIT_NEXT_COMMAND": shlex.join(
            [
                *base,
                "wait-next",
                *identity,
                "--max-chain-depth",
                str(config.max_chain_depth),
                "--poll-interval",
                str(config.poll_interval),
                "--json",
            ]
        ),
        "AGENTSASSEMBLE_WAIT_ROOM_EVENT_COMMAND": shlex.join(
            [
                *base,
                "wait-room-event",
                *identity,
                "--max-chain-depth",
                str(config.max_chain_depth),
                "--poll-interval",
                str(config.poll_interval),
                "--json",
            ]
        ),
        "AGENTSASSEMBLE_WAIT_OFFICIAL_TURN_COMMAND": shlex.join(
            [
                *base,
                "wait-official-turn",
                *identity,
                "--poll-interval",
                str(config.poll_interval),
                "--json",
            ]
        ),
        "AGENTSASSEMBLE_SAY_COMMAND_TEMPLATE": shlex.join(
            [
                *base,
                "say",
                *identity,
                "--source-event-id",
                "{source_event_id}",
                "--auto-chain-depth",
                "{auto_chain_depth}",
                "--",
                "{message}",
            ]
        ),
        "AGENTSASSEMBLE_OFFICIAL_REPLY_COMMAND_TEMPLATE": shlex.join(
            [
                *base,
                "official-reply",
                *identity,
                "--meeting-id",
                "{meeting_id}",
                "--source-event-id",
                "{source_event_id}",
                "--",
                "{message}",
            ]
        ),
        "AGENTSASSEMBLE_DM_REPLY_COMMAND_TEMPLATE": shlex.join(
            [
                *base,
                "dm-reply",
                *identity,
                "--source-event-id",
                "{source_event_id}",
                "--",
                "{message}",
            ]
        ),
        "AGENTSASSEMBLE_HEARTBEAT_COMMAND_TEMPLATE": shlex.join(
            [
                *base,
                "heartbeat",
                *identity,
                "--status",
                "{status}",
                "--last-error={last_error}",
                "--last-attention={last_attention}",
                "--last-reply-at={last_reply_at}",
                "--last-observed-event-id={last_observed_event_id}",
                "--last-observed-live-event-id={last_observed_live_event_id}",
                "--last-observed-dm-event-id={last_observed_dm_event_id}",
                "--json",
            ]
        ),
        "AGENTSASSEMBLE_LEAVE_COMMAND": shlex.join([*base, "leave", *identity, "--json"]),
    }


def self_service_exit_error(return_code: int) -> str:
    return f"Self-service command exited with return code {return_code}."


def terminate_process(
    process: Any,
    *,
    send_stop_signal: Callable[[Any, int | None], None],
    send_kill_signal: Callable[[Any, int | None], None],
    term_signal: int | None,
    kill_signal: int | None,
) -> None:
    if process.poll() is not None:
        return
    send_stop_signal(process, term_signal)
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        send_kill_signal(process, kill_signal)
        process.wait(timeout=1)


def send_process_stop_signal(
    process: Any,
    stop_signal: int | None,
    *,
    force: bool,
    process_group_pid: int | None,
) -> None:
    if process_group_pid is not None and stop_signal is not None:
        try:
            os.killpg(process_group_pid, stop_signal)
            return
        except ProcessLookupError:
            return
        except OSError:
            pass
    try:
        if force:
            process.kill()
        else:
            process.terminate()
    except ProcessLookupError:
        return


def process_group_pid(process: Any, *, process_groups_supported: bool) -> int | None:
    if not process_groups_supported:
        return None
    pgid = getattr(process, "_agentsassemble_process_group_pid", None)
    return pgid if isinstance(pgid, int) and pgid > 0 else None


def supports_process_groups() -> bool:
    return hasattr(os, "killpg") and hasattr(os, "setsid")


def stop_signal(name: str, *, signal_module=signal) -> int | None:
    value = getattr(signal_module, name, None)
    return value if isinstance(value, int) else None


def install_resident_shutdown_signal_handlers(
    on_shutdown: Callable[[], None],
    *,
    signal_module=signal,
    threading_module=threading,
) -> Callable[[], None]:
    sigterm = stop_signal("SIGTERM", signal_module=signal_module)
    if sigterm is None or threading_module.current_thread() is not threading_module.main_thread():
        return lambda: None

    previous_handlers = {}

    def handle_shutdown(signum, frame):
        del signum, frame
        on_shutdown()
        raise KeyboardInterrupt()

    try:
        previous_handlers[sigterm] = signal_module.signal(sigterm, handle_shutdown)
    except (OSError, RuntimeError, ValueError):
        return lambda: None

    def restore_signal_handlers() -> None:
        for signum, previous_handler in previous_handlers.items():
            try:
                signal_module.signal(signum, previous_handler)
            except (OSError, RuntimeError, ValueError):
                pass

    return restore_signal_handlers
