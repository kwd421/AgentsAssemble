"""Legacy resident CLI command coordination."""
from __future__ import annotations

import argparse
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentsassemble.live_agent_runner import ResidentAgentConfig


@dataclass(frozen=True)
class LegacyResidentCliRuntime:
    config_from_args: Callable[[argparse.Namespace], ResidentAgentConfig]
    load_group_configs: Callable[..., list[ResidentAgentConfig]]
    setup_error: Callable[[ResidentAgentConfig], str]
    run_ws_resident: Callable[[argparse.Namespace, ResidentAgentConfig], int]
    supervisor_factory: Callable[..., Any]
    command_runner_for_config: Callable[..., Any]
    live_agent_runner_factory: Callable[..., Any]
    request_json: Callable[..., dict[str, object]]
    sleep: Callable[[float], None]
    install_shutdown_handlers: Callable[[Callable[[], None]], Callable[[], None]]
    close_command_runner: Callable[[Any], None]
    group_config_errors: Callable[[list[ResidentAgentConfig]], dict[str, str]]
    validate_config: Callable[[ResidentAgentConfig], None]
    run_ws_group_resident: Callable[[ResidentAgentConfig], int]
    should_heartbeat_worker_error: Callable[[ResidentAgentConfig, BaseException], bool]
    heartbeat_worker_error: Callable[[ResidentAgentConfig, BaseException], None]


def run_legacy_resident_command(
    args: argparse.Namespace,
    *,
    runtime: LegacyResidentCliRuntime,
) -> int:
    config = runtime.config_from_args(args)
    setup_error = runtime.setup_error(config)
    if setup_error:
        raise ValueError(f"{config.agent_id}: {setup_error}")
    if str(getattr(args, "transport", "http") or "http") == "ws":
        return runtime.run_ws_resident(args, config)
    if config.connection_kind == "self_service":
        runner = runtime.supervisor_factory(
            config,
            request_json=runtime.request_json,
            sleep_fn=runtime.sleep,
        )
        replies = 0
        restore_signal_handlers = lambda: None
        try:
            restore_signal_handlers = runtime.install_shutdown_handlers(runner.close)
            replies = runner.run()
        except KeyboardInterrupt:
            runner.close()
        finally:
            restore_signal_handlers()
        print(f"Self-service resident agent stopped after posting {replies} parent-managed replies")
        return 0
    command_runner = runtime.command_runner_for_config(
        config,
        output_root=str(getattr(args, "output_root", "") or ""),
    )
    runner = runtime.live_agent_runner_factory(
        config,
        request_json=runtime.request_json,
        command_runner=command_runner,
        sleep_fn=runtime.sleep,
        self_relaunch=True,
    )
    replies = 0
    restore_signal_handlers = lambda: None
    try:
        restore_signal_handlers = runtime.install_shutdown_handlers(
            lambda: runtime.close_command_runner(command_runner)
        )
        replies = runner.run()
    except KeyboardInterrupt:
        runtime.close_command_runner(command_runner)
    finally:
        restore_signal_handlers()
        runtime.close_command_runner(command_runner)
    print(f"Resident agent stopped after posting {replies} replies")
    return 0


def run_legacy_resident_group_command(
    args: argparse.Namespace,
    *,
    runtime: LegacyResidentCliRuntime,
) -> int:
    configs = runtime.load_group_configs(
        Path(args.config),
        max_ticks_override=args.max_ticks,
        server_override=args.server,
    )
    config_errors = runtime.group_config_errors(configs)
    if config_errors:
        for agent_id, error in config_errors.items():
            print(f"{agent_id}: {error}", file=sys.stderr)
        return 2
    stop_event = threading.Event()
    results: dict[str, int] = {}
    errors: dict[str, str] = {}
    active_command_runners: list[object] = []
    active_command_runners_lock = threading.Lock()

    def sleep(seconds: float) -> None:
        stop_event.wait(seconds)

    def close_active_command_runners() -> None:
        with active_command_runners_lock:
            runners_to_close = list(active_command_runners)
        for active_runner in runners_to_close:
            runtime.close_command_runner(active_runner)

    def shutdown_group() -> None:
        stop_event.set()
        close_active_command_runners()

    def run_agent(config: ResidentAgentConfig) -> None:
        command_runner = None
        try:
            runtime.validate_config(config)
            transport = str(getattr(config, "transport", "http") or "http")
            if transport == "ws":
                if config.connection_kind == "self_service":
                    raise ValueError(f"{config.agent_id}: self_service does not support ws transport.")
                results[config.agent_id] = runtime.run_ws_group_resident(config)
                return
            if config.connection_kind == "self_service":
                command_runner = runtime.supervisor_factory(
                    config,
                    request_json=runtime.request_json,
                    sleep_fn=sleep,
                    stop_event=stop_event,
                    isolate_process_group=False,
                )
            else:
                command_runner = runtime.command_runner_for_config(config)
            with active_command_runners_lock:
                active_command_runners.append(command_runner)
            if config.connection_kind == "self_service":
                results[config.agent_id] = command_runner.run()
            else:
                runner = runtime.live_agent_runner_factory(
                    config,
                    request_json=runtime.request_json,
                    command_runner=command_runner,
                    sleep_fn=sleep,
                    stop_event=stop_event,
                )
                results[config.agent_id] = runner.run()
        except BaseException as error:  # surfaced through CLI status in integration use
            if isinstance(error, KeyboardInterrupt):
                shutdown_group()
                return
            if stop_event.is_set():
                return
            errors[config.agent_id] = str(error)
            if runtime.should_heartbeat_worker_error(config, error):
                runtime.heartbeat_worker_error(config, error)
        finally:
            if command_runner is not None:
                runtime.close_command_runner(command_runner)
                with active_command_runners_lock:
                    if command_runner in active_command_runners:
                        active_command_runners.remove(command_runner)

    threads = [threading.Thread(target=run_agent, args=(config,), daemon=True) for config in configs]
    stagger_seconds = max(0.0, float(getattr(args, "launch_stagger_seconds", 0.0) or 0.0))
    started_threads: list[threading.Thread] = []
    restore_signal_handlers = lambda: None
    try:
        restore_signal_handlers = runtime.install_shutdown_handlers(shutdown_group)
        for index, thread in enumerate(threads):
            if index > 0 and stagger_seconds > 0:
                sleep(stagger_seconds)
                if stop_event.is_set():
                    break
            thread.start()
            started_threads.append(thread)
        for thread in started_threads:
            thread.join()
    except KeyboardInterrupt:
        shutdown_group()
        for thread in started_threads:
            thread.join(timeout=5)
    finally:
        restore_signal_handlers()
    if errors:
        for agent_id, error in errors.items():
            print(f"{agent_id}: {error}", file=sys.stderr)
        return 2
    total = sum(results.values())
    summary = ", ".join(f"{config.agent_id}={results.get(config.agent_id, 0)}" for config in configs)
    print(f"Resident group stopped after posting {total} replies ({summary})")
    return 0
