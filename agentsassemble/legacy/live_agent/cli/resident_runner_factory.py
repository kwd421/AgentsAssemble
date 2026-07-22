"""Provider-to-runner selection for retained resident commands."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agentsassemble.legacy.live_agent.cli.resident_runtime import (
    ApiCatalogCommandRunner,
    resident_workspace_cwd,
)
from agentsassemble.legacy.live_agent.cli.resident_session_runners import (
    JsonlLiveSessionCommandRunner,
    TerminalLiveSessionCommandRunner,
)
from agentsassemble.live_agent_runner import (
    RemoteBridgeResidentCommandRunner,
    ResidentAgentConfig,
)
from agentsassemble.providers.antigravity_resident import AntigravityResidentCommandRunner
from agentsassemble.providers.codex_resident import CodexResidentCommandRunner
from agentsassemble.providers.cursor_resident import CursorResidentCommandRunner
from agentsassemble.providers.grok_resident import GrokResidentCommandRunner
from agentsassemble.providers.hermes_resident import HermesResidentCommandRunner
from agentsassemble.providers.kiro_resident import KiroResidentCommandRunner


def command_runner_for_config(
    config: ResidentAgentConfig,
    *,
    output_root: str = "",
    local_cli_runner_factory: Callable[[], Any],
):
    if config.connection_kind == "self_service":
        raise ValueError(
            "self_service residents are supervised directly and do not use prompt-injection command runners."
        )
    if config.connection_kind == "api_call":
        return ApiCatalogCommandRunner(config, output_root=output_root)
    cwd = resident_workspace_cwd(config)
    live_session_runners = {
        "codex_live_session": CodexResidentCommandRunner,
        "kiro_live_session": KiroResidentCommandRunner,
        "cursor_live_session": CursorResidentCommandRunner,
        "grok_live_session": GrokResidentCommandRunner,
        "antigravity_live_session": AntigravityResidentCommandRunner,
        "hermes_live_session": HermesResidentCommandRunner,
    }
    if config.connection_kind == "live_session" and config.provider_kind in live_session_runners:
        return live_session_runners[config.provider_kind](config, cwd=cwd)
    if config.provider_kind == "claude_code" and config.connection_kind == "terminal_session":
        from agentsassemble.providers.claude_resident import (
            claude_answer_ready,
            extract_claude_terminal_message,
        )

        return TerminalLiveSessionCommandRunner(
            idle_timeout_seconds=max(float(config.terminal_idle_timeout or 0.0), 1.0),
            cwd=cwd,
            message_extractor=extract_claude_terminal_message,
            ready_predicate=claude_answer_ready,
            submit_newline="\r",
            submit_settle_seconds=0.4,
            warmup_idle_seconds=1.5,
            stream_config=config if getattr(config, "stream_thinking", False) else None,
            permission_mode=str(getattr(config, "permission_option", "") or ""),
            fast_mode=bool(getattr(config, "fast_mode", False)),
        )
    if config.connection_kind == "live_session":
        return JsonlLiveSessionCommandRunner()
    if config.connection_kind == "terminal_session":
        return TerminalLiveSessionCommandRunner(
            idle_timeout_seconds=config.terminal_idle_timeout,
            cwd=cwd,
        )
    if config.connection_kind == "remote_bridge":
        return RemoteBridgeResidentCommandRunner(config)
    return local_cli_runner_factory()
