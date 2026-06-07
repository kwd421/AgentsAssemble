from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


CALL_RESUME_JOIN_SEMANTICS = {
    "codex_exec_resume",
    "kiro_chat_resume",
    "antigravity_conversation_resume",
    "cursor_chat_resume",
    "grok_session_resume",
    "hermes_chat_resume",
    "stateless_prompt_call",
}
RUNTIME_MANAGED_ROOM_TURN_JOIN_SEMANTICS = {
    "runtime_managed_room_turn",
}
PROVIDER_TOOL_LOOP_JOIN_SEMANTICS = {
    "mcp_tool_loop",
    "cli_tool_loop",
    "provider_tool_loop",
    "self_service_room_loop",
    "remote_bridge_room_loop",
    "native_remote_room_loop",
}
PROVIDER_PERSISTENT_JOIN_SEMANTICS = {
    "terminal_pty_prompt_bridge",
    "jsonl_live_session",
}


@dataclass(frozen=True)
class RuntimeCapabilities:
    runtime_mode: str
    runner_residency: str
    provider_residency: str
    provider_persistent: bool
    summary: str

    @classmethod
    def from_join_semantics(cls, join_semantics: object) -> RuntimeCapabilities:
        join = str(join_semantics or "").strip()
        if join in CALL_RESUME_JOIN_SEMANTICS:
            return cls(
                runtime_mode="baseline_call_resume",
                runner_residency="resident_polling_runner",
                provider_residency="per_turn_exec_resume",
                provider_persistent=False,
                summary="Baseline call/resume: runner stays alive, but the provider is invoked per turn through exec/resume.",
            )
        if join in RUNTIME_MANAGED_ROOM_TURN_JOIN_SEMANTICS:
            return cls(
                runtime_mode="runtime_managed_room_turn",
                runner_residency="resident_room_runtime",
                provider_residency="per_turn_exec_resume",
                provider_persistent=False,
                summary="Runtime-managed room turn: the runtime reads the room API, selects a turn, then invokes the provider.",
            )
        if join in PROVIDER_TOOL_LOOP_JOIN_SEMANTICS:
            return cls(
                runtime_mode="provider_tool_loop",
                runner_residency="provider_owned_tool_loop",
                provider_residency="provider_owned_tool_loop",
                provider_persistent=True,
                summary="Provider tool-loop: the provider participates through wait-next/read-since/say room tools.",
            )
        if join in PROVIDER_PERSISTENT_JOIN_SEMANTICS:
            return cls(
                runtime_mode="provider_persistent",
                runner_residency="resident_process",
                provider_residency="persistent_provider_channel",
                provider_persistent=True,
                summary="Provider process, PTY, stream, or room loop stays attached while the agent is running.",
            )
        if join == "manual_room_loop":
            return cls(
                runtime_mode="manual",
                runner_residency="manual",
                provider_residency="external_or_human",
                provider_persistent=False,
                summary="Manual participant; AgentsAssemble does not execute a provider.",
            )
        return cls(
            runtime_mode="unknown",
            runner_residency="unknown",
            provider_residency="unknown",
            provider_persistent=False,
            summary="Execution style is not proven.",
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "runtime_mode": self.runtime_mode,
            "runner_residency": self.runner_residency,
            "provider_residency": self.provider_residency,
            "provider_persistent": self.provider_persistent,
            "runtime_summary": self.summary,
        }


@dataclass(frozen=True)
class LiveSessionResult:
    message: str
    runtime_mode: str


class InvokeLiveSessionAdapter:
    """Live-session adapter for provider calls that still execute per turn."""

    runtime_mode = "baseline_call_resume"

    def __init__(self, *, command_runner: Callable[..., str]) -> None:
        self.command_runner = command_runner

    def invoke(self, command: list[str], prompt: str, *, timeout_seconds: int) -> LiveSessionResult:
        message = self.command_runner(command, prompt, timeout_seconds=timeout_seconds)
        return LiveSessionResult(message=str(message or ""), runtime_mode=self.runtime_mode)


class RuntimeManagedRoomTurnAdapter(InvokeLiveSessionAdapter):
    """Runtime-managed room-turn adapter over a per-turn provider invocation."""

    runtime_mode = "runtime_managed_room_turn"


def live_session_runtime_contract(join_semantics: object) -> dict[str, object]:
    return RuntimeCapabilities.from_join_semantics(join_semantics).to_payload()
