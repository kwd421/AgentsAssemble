from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from agentsassemble.meeting_events import clean_lobby_text


@dataclass(frozen=True)
class NativeCliProviderSpec:
    agent_id: str
    display_name: str
    command: tuple[str, ...]
    cwd: str = "."
    provider_kind: str = ""
    model: str = ""
    default_responder: bool = True
    quiet_seconds: float = 4.0
    input_mode: str = "line"
    submit_newline: str = "\r"
    submit_delay_seconds: float = 0.1
    terminal_rows: int = 40
    terminal_columns: int = 120
    startup_quiet_seconds: float = 1.0
    startup_timeout_seconds: float = 20.0
    startup_accept_contains: str = ""
    startup_accept_keys: str = "\r"
    turn_timeout_seconds: float = 180.0

    def normalized_provider_kind(self) -> str:
        return clean_lobby_text(self.provider_kind, limit=64) or f"{self.agent_id}_live_session"

    def runtime_profile_key(self) -> str:
        profile = json.dumps(
            {
                "provider_kind": self.normalized_provider_kind(),
                "command": list(self.command),
                "cwd": str(Path(self.cwd).expanduser().resolve()),
                "model": self.model,
                "quiet_seconds": self.quiet_seconds,
                "input_mode": self.input_mode,
                "submit_newline": self.submit_newline,
                "submit_delay_seconds": self.submit_delay_seconds,
                "terminal_rows": self.terminal_rows,
                "terminal_columns": self.terminal_columns,
                "startup_quiet_seconds": self.startup_quiet_seconds,
                "startup_timeout_seconds": self.startup_timeout_seconds,
                "startup_accept_contains": self.startup_accept_contains,
                "startup_accept_keys": self.startup_accept_keys,
                "turn_timeout_seconds": self.turn_timeout_seconds,
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(profile.encode("utf-8")).hexdigest()[:20]


@dataclass(frozen=True)
class NativeCliProviderDefinition:
    provider_id: str
    display_name: str
    provider_kind: str
    executable: str
    command_builder: Callable[[str], tuple[str, ...]]
    aliases: tuple[str, ...] = ()
    default_model: str = ""
    input_mode: str = "line"
    startup_accept_contains: str = ""

    def make_spec(
        self,
        *,
        agent_id: str | None = None,
        display_name: str | None = None,
        cwd: str | Path = ".",
        model: str = "",
        default_responder: bool = True,
    ) -> NativeCliProviderSpec:
        selected_model = clean_lobby_text(model, limit=128) or self.default_model
        return NativeCliProviderSpec(
            agent_id=clean_lobby_text(agent_id, limit=128) or self.provider_id,
            display_name=clean_lobby_text(display_name, limit=128) or self.display_name,
            command=self.command_builder(selected_model),
            cwd=str(Path(cwd).expanduser().resolve()),
            provider_kind=self.provider_kind,
            model=selected_model,
            default_responder=default_responder,
            input_mode=self.input_mode,
            startup_accept_contains=self.startup_accept_contains,
        )

    def public_payload(self) -> dict[str, object]:
        return {
            "id": self.provider_id,
            "display_name": self.display_name,
            "provider_kind": self.provider_kind,
            "runtime_kind": "live_cli",
            "connection_kind": "native_cli_bridge",
            "executable": self.executable,
            "default_model": self.default_model,
            "interactive": True,
            "startable": True,
        }


class UnsupportedNativeCliProvider(ValueError):
    pass


def _codex_command(model: str) -> tuple[str, ...]:
    return (
        "codex",
        "--no-alt-screen",
        "--ask-for-approval",
        "never",
        "--sandbox",
        "read-only",
        "--model",
        model or "gpt-5.3-codex-spark",
    )


def _antigravity_command(_model: str) -> tuple[str, ...]:
    return ("agy", "--sandbox")


def _grok_command(_model: str) -> tuple[str, ...]:
    return ("grok", "--no-alt-screen", "--permission-mode", "plan")


def _claude_command(model: str) -> tuple[str, ...]:
    return (
        "claude",
        "--model",
        model or "haiku",
        "--permission-mode",
        "plan",
        "--tools",
        "--safe-mode",
    )


NATIVE_CLI_PROVIDER_CATALOG: tuple[NativeCliProviderDefinition, ...] = (
    NativeCliProviderDefinition(
        provider_id="codex",
        display_name="Codex Spark",
        provider_kind="codex_live_session",
        executable="codex",
        command_builder=_codex_command,
        aliases=("codex_live_session",),
        default_model="gpt-5.3-codex-spark",
        input_mode="bracketed_paste",
        startup_accept_contains="Do you trust",
    ),
    NativeCliProviderDefinition(
        provider_id="antigravity",
        display_name="Antigravity CLI",
        provider_kind="antigravity_live_session",
        executable="agy",
        command_builder=_antigravity_command,
        aliases=("agy", "antigravity_live_session"),
        input_mode="bracketed_paste",
        startup_accept_contains="Do you trust",
    ),
    NativeCliProviderDefinition(
        provider_id="grok",
        display_name="Grok CLI",
        provider_kind="grok_live_session",
        executable="grok",
        command_builder=_grok_command,
        aliases=("grok_live_session",),
    ),
    NativeCliProviderDefinition(
        provider_id="claude",
        display_name="Claude Haiku",
        provider_kind="claude_code",
        executable="claude",
        command_builder=_claude_command,
        aliases=("claude_code",),
        default_model="haiku",
        input_mode="bracketed_paste",
        startup_accept_contains="Do you trust",
    ),
)

_PROVIDER_BY_ALIAS = {
    alias.casefold(): definition
    for definition in NATIVE_CLI_PROVIDER_CATALOG
    for alias in (definition.provider_id, definition.provider_kind, definition.executable, *definition.aliases)
}


def native_cli_provider_definition(value: object) -> NativeCliProviderDefinition | None:
    return _PROVIDER_BY_ALIAS.get(clean_lobby_text(value, limit=64).casefold())


def native_cli_provider_catalog_payload() -> list[dict[str, object]]:
    return [definition.public_payload() for definition in NATIVE_CLI_PROVIDER_CATALOG]


def default_native_cli_provider_specs(*, workspace: str | Path = ".") -> list[NativeCliProviderSpec]:
    return [definition.make_spec(cwd=workspace) for definition in NATIVE_CLI_PROVIDER_CATALOG]


def native_cli_provider_spec_from_payload(payload: dict[str, object]) -> NativeCliProviderSpec:
    provider_value = payload.get("provider_id") or payload.get("provider_kind") or payload.get("provider")
    definition = native_cli_provider_definition(provider_value)
    if definition is None:
        provider = clean_lobby_text(provider_value, limit=64) or "unknown"
        raise UnsupportedNativeCliProvider(
            f"Provider {provider} is not available as a native CLI Agent Session."
        )
    display_name = clean_lobby_text(payload.get("display_name"), limit=64) or definition.display_name
    explicit_agent_id = clean_lobby_text(payload.get("agent_id") or payload.get("participant_id"), limit=128)
    agent_id = explicit_agent_id or _slug_agent_id(f"{definition.provider_id}-{display_name}")
    workspace = clean_lobby_text(payload.get("workspace") or payload.get("workspace_path") or payload.get("cwd"), limit=500)
    spec = definition.make_spec(
        agent_id=agent_id,
        display_name=display_name,
        cwd=workspace or ".",
        model=clean_lobby_text(payload.get("model") or payload.get("model_id"), limit=128),
    )
    validate_native_cli_provider_spec(spec)
    return spec


def native_cli_provider_spec_from_config(
    payload: dict[str, object],
    *,
    turn_timeout_seconds: float,
) -> NativeCliProviderSpec:
    agent_id = clean_lobby_text(payload.get("id") or payload.get("agent_id"), limit=128)
    if not agent_id:
        raise ValueError("live CLI provider id is required")
    definition = native_cli_provider_definition(
        payload.get("provider_id") or payload.get("provider_kind") or agent_id
    )
    command = tuple(str(part) for part in list(payload.get("command") or []) if str(part))
    model = clean_lobby_text(payload.get("model"), limit=128)
    if not model and "--model" in command:
        index = command.index("--model")
        model = command[index + 1] if index + 1 < len(command) else ""
    if not command and definition is not None:
        command = definition.command_builder(model or definition.default_model)
    if not command:
        raise ValueError(f"live CLI provider {agent_id} command is required")
    spec = NativeCliProviderSpec(
        agent_id=agent_id,
        display_name=clean_lobby_text(payload.get("display_name"), limit=128)
        or (definition.display_name if definition else agent_id),
        command=command,
        cwd=str(Path(str(payload.get("cwd") or ".")).expanduser().resolve()),
        provider_kind=clean_lobby_text(payload.get("provider_kind"), limit=64)
        or (definition.provider_kind if definition else f"{agent_id}_live_session"),
        model=model or (definition.default_model if definition else ""),
        default_responder=bool(payload.get("default_responder", False)),
        quiet_seconds=_float_value(payload.get("quiet_seconds"), 4.0),
        input_mode=clean_lobby_text(payload.get("input_mode"), limit=64)
        or (definition.input_mode if definition else "line"),
        submit_newline=str(payload.get("submit_newline") or "\r"),
        submit_delay_seconds=_float_value(payload.get("submit_delay_seconds"), 0.1),
        terminal_rows=_int_value(payload.get("terminal_rows"), 40),
        terminal_columns=_int_value(payload.get("terminal_columns"), 120),
        startup_quiet_seconds=_float_value(payload.get("startup_quiet_seconds"), 1.0),
        startup_timeout_seconds=_float_value(payload.get("startup_timeout_seconds"), 20.0),
        startup_accept_contains=str(
            payload.get("startup_accept_contains")
            or (definition.startup_accept_contains if definition else "")
        ),
        startup_accept_keys=str(payload.get("startup_accept_keys") or "\r"),
        turn_timeout_seconds=max(0.1, float(turn_timeout_seconds)),
    )
    validate_native_cli_provider_spec(spec)
    return spec


def validate_native_cli_provider_spec(spec: NativeCliProviderSpec) -> None:
    if not spec.command:
        raise ValueError("Native CLI Agent Session command is required.")
    executable = Path(spec.command[0]).name.casefold()
    is_claude = executable == "claude" or spec.normalized_provider_kind() == "claude_code"
    if not is_claude:
        return
    forbidden = [part for part in spec.command[1:] if part in {"-p", "--print"} or part.startswith("--print=")]
    if forbidden:
        raise ValueError("Claude Code Agent Sessions require interactive mode; print mode is forbidden.")


def _slug_agent_id(value: str) -> str:
    slug = re.sub(r"[^a-z0-9._-]+", "-", str(value or "").casefold()).strip("-")
    return slug[:96] or "agent-session"


def _float_value(value: object, default: float) -> float:
    try:
        return float(value) if value not in (None, "") else default
    except (TypeError, ValueError):
        return default


def _int_value(value: object, default: int) -> int:
    try:
        return int(value) if value not in (None, "") else default
    except (TypeError, ValueError):
        return default
