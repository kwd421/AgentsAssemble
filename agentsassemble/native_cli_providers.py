from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
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
    requested_model_id: str = ""
    model_selection_kind: str = "exact"
    model_observation_policy: str = "unavailable"
    catalog_revision: str = ""
    reasoning_effort: str = ""
    service_tier: str = ""
    variant: str = ""
    permission_mode: str = "meeting_read_only"
    runtime_kind: str = "live_cli"
    transport: str = "pty"
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
    startup_ready_contains: str = ""
    startup_input: str = ""
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
                "reasoning_effort": self.reasoning_effort,
                "service_tier": self.service_tier,
                "variant": self.variant,
                "permission_mode": self.permission_mode,
                "runtime_kind": self.runtime_kind,
                "transport": self.transport,
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
                "startup_ready_contains": self.startup_ready_contains,
                "startup_input": self.startup_input,
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
    command_builder: Callable[[str, str, str, str, str], tuple[str, ...]]
    aliases: tuple[str, ...] = ()
    default_model: str = ""
    default_reasoning_effort: str = ""
    default_service_tier: str = ""
    default_variant: str = ""
    default_permission_mode: str = "meeting_read_only"
    model_observation_policy: str = "required"
    runtime_kind: str = "live_cli"
    transport: str = "pty"
    input_mode: str = "line"
    startup_accept_contains: str = ""
    startup_ready_contains: str = ""

    def make_default_spec(
        self,
        *,
        agent_id: str | None = None,
        display_name: str | None = None,
        cwd: str | Path = ".",
        default_responder: bool = True,
    ) -> NativeCliProviderSpec:
        return self.make_selected_spec(
            agent_id=agent_id,
            display_name=display_name,
            cwd=cwd,
            model=self.default_model,
            reasoning_effort=self.default_reasoning_effort,
            service_tier=self.default_service_tier,
            variant=self.default_variant,
            permission_mode=self.default_permission_mode,
            default_responder=default_responder,
        )

    def make_selected_spec(
        self,
        *,
        agent_id: str | None,
        display_name: str | None,
        cwd: str | Path,
        model: str = "",
        reasoning_effort: str = "",
        service_tier: str = "",
        variant: str = "",
        permission_mode: str = "",
        model_selection_kind: str = "exact",
        catalog_revision: str = "",
        default_responder: bool = True,
    ) -> NativeCliProviderSpec:
        selected_model = clean_lobby_text(model, limit=128)
        selected_effort = clean_lobby_text(reasoning_effort, limit=32)
        selected_service_tier = clean_lobby_text(service_tier, limit=32)
        selected_variant = clean_lobby_text(variant, limit=64)
        selected_permission = clean_lobby_text(permission_mode, limit=64)
        selected_kind = clean_lobby_text(model_selection_kind, limit=16)
        if not selected_model:
            raise ValueError(f"Provider {self.provider_id} model is required.")
        if self.default_reasoning_effort and not selected_effort:
            raise ValueError(f"Provider {self.provider_id} reasoning effort is required.")
        if self.default_service_tier and not selected_service_tier:
            raise ValueError(f"Provider {self.provider_id} service tier is required.")
        if self.default_variant and not selected_variant:
            raise ValueError(f"Provider {self.provider_id} variant is required.")
        if not selected_permission:
            raise ValueError(f"Provider {self.provider_id} permission mode is required.")
        if selected_kind not in {"exact", "alias"}:
            raise ValueError(f"Provider {self.provider_id} model selection kind is invalid.")
        return NativeCliProviderSpec(
            agent_id=clean_lobby_text(agent_id, limit=128) or self.provider_id,
            display_name=clean_lobby_text(display_name, limit=128) or self.display_name,
            command=self.command_builder(
                selected_model,
                selected_effort,
                selected_service_tier,
                selected_variant,
                selected_permission,
            ),
            cwd=str(Path(cwd).expanduser().resolve()),
            provider_kind=self.provider_kind,
            model=selected_model,
            requested_model_id=selected_model,
            model_selection_kind=selected_kind,
            model_observation_policy=self.model_observation_policy,
            catalog_revision=clean_lobby_text(catalog_revision, limit=128),
            reasoning_effort=selected_effort,
            service_tier=selected_service_tier,
            variant=selected_variant,
            permission_mode=selected_permission,
            runtime_kind=self.runtime_kind,
            transport=self.transport,
            default_responder=default_responder,
            input_mode=self.input_mode,
            startup_accept_contains=self.startup_accept_contains,
            startup_ready_contains=self.startup_ready_contains,
            startup_input="/fast\r" if self.provider_id == "claude" and selected_service_tier == "fast" else "",
        )

    def public_payload(self) -> dict[str, object]:
        return {
            "id": self.provider_id,
            "display_name": self.display_name,
            "provider_kind": self.provider_kind,
            "runtime_kind": self.runtime_kind,
            "connection_kind": "native_cli_bridge",
            "executable": self.executable,
            "default_model": self.default_model,
            "interactive": True,
            "startable": True,
        }


class UnsupportedNativeCliProvider(ValueError):
    pass


class StoredProviderProfileError(ValueError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


def native_cli_provider_spec_from_stored_session_strict(
    session: dict[str, object],
) -> NativeCliProviderSpec:
    agent_id = clean_lobby_text(session.get("participant_id") or session.get("session_id"), limit=128)
    definition = native_cli_provider_definition(session.get("provider_kind"))
    if not agent_id or definition is None:
        raise StoredProviderProfileError(
            "Stored Agent Session provider profile is incomplete.",
            code="profile_incomplete",
        )
    required = {
        "display_name": clean_lobby_text(session.get("display_name"), limit=128),
        "workspace": clean_lobby_text(session.get("workspace"), limit=500),
        "model": clean_lobby_text(session.get("model"), limit=128),
        "permission_mode": clean_lobby_text(session.get("permission_mode"), limit=64),
        "runtime_profile_key": clean_lobby_text(session.get("runtime_profile_key"), limit=128),
    }
    if any(not value for value in required.values()):
        raise StoredProviderProfileError(
            "Stored Agent Session provider profile is incomplete.",
            code="profile_incomplete",
        )
    for field, default in (
        ("reasoning_effort", definition.default_reasoning_effort),
        ("service_tier", definition.default_service_tier),
        ("variant", definition.default_variant),
    ):
        if default and not clean_lobby_text(session.get(field), limit=64):
            raise StoredProviderProfileError(
                f"Stored Agent Session is missing required {field}.",
                code="profile_incomplete",
            )
    stored_runtime_kind = clean_lobby_text(session.get("runtime_kind"), limit=64)
    stored_transport = clean_lobby_text(session.get("transport"), limit=64)
    if stored_runtime_kind != definition.runtime_kind:
        raise StoredProviderProfileError(
            "Stored Agent Session provider definition changed.",
            code="provider_definition_changed",
        )
    stored_command = tuple(str(part) for part in list(session.get("command_configured") or []))
    if not stored_command:
        raise StoredProviderProfileError(
            "Stored Agent Session command profile is incomplete.",
            code="profile_incomplete",
        )
    spec = definition.make_selected_spec(
        agent_id=agent_id,
        display_name=required["display_name"],
        cwd=required["workspace"],
        model=required["model"],
        reasoning_effort=clean_lobby_text(session.get("reasoning_effort"), limit=32),
        service_tier=clean_lobby_text(session.get("service_tier"), limit=32),
        variant=clean_lobby_text(session.get("variant"), limit=64),
        permission_mode=required["permission_mode"],
        model_selection_kind=clean_lobby_text(session.get("model_selection_kind"), limit=16)
        or "exact",
        catalog_revision=clean_lobby_text(session.get("catalog_revision"), limit=128),
    )
    profile_matches = (
        stored_transport == definition.transport
        and spec.runtime_profile_key() == required["runtime_profile_key"]
    )
    legacy_grok_transport_profile = (
        definition.provider_id == "grok"
        and stored_transport == "pty"
        and spec.command == stored_command
        and replace(spec, transport="pty").runtime_profile_key() == required["runtime_profile_key"]
    )
    if stored_transport != definition.transport and not legacy_grok_transport_profile:
        raise StoredProviderProfileError(
            "Stored Agent Session provider definition changed.",
            code="provider_definition_changed",
        )
    if spec.command != stored_command or not (profile_matches or legacy_grok_transport_profile):
        raise StoredProviderProfileError(
            "Stored Agent Session profile must be migrated before it can be reused.",
            code="profile_migration_required",
        )
    return spec


def _codex_command(
    model: str,
    effort: str,
    service_tier: str,
    _variant: str,
    permission_mode: str,
) -> tuple[str, ...]:
    if not model:
        raise ValueError("Codex model is required.")
    approval, sandbox = _codex_permissions(permission_mode)
    command = [
        "codex",
        "--no-alt-screen",
        "--ask-for-approval",
        approval,
        "--sandbox",
        sandbox,
        "--model",
        model,
    ]
    if effort:
        command.extend(("-c", f'model_reasoning_effort="{effort}"'))
    if service_tier and service_tier != "default":
        command.extend(("-c", f'service_tier="{service_tier}"'))
    return tuple(command)


def _antigravity_command(
    model: str,
    _effort: str,
    _service_tier: str,
    _variant: str,
    permission_mode: str,
) -> tuple[str, ...]:
    if not model:
        raise ValueError("Antigravity model is required.")
    command = ["agy"]
    if model:
        command.extend(("--model", model))
    if permission_mode == "workspace_write":
        command.extend(("--mode", "accept-edits"))
    else:
        command.extend(("--mode", "plan", "--sandbox"))
    return tuple(command)


def _grok_command(
    model: str,
    effort: str,
    _service_tier: str,
    _variant: str,
    _permission_mode: str,
) -> tuple[str, ...]:
    command = ["grok"]
    if model:
        command.extend(("--model", model))
    if effort:
        command.extend(("--reasoning-effort", effort))
    command.extend(("agent", "stdio"))
    return tuple(command)


def _claude_command(
    model: str,
    effort: str,
    service_tier: str,
    _variant: str,
    permission_mode: str,
) -> tuple[str, ...]:
    if not model:
        raise ValueError("Claude model is required.")
    command = [
        "claude",
        "--model",
        model,
    ]
    if effort:
        command.extend(("--effort", effort))
    command.extend(("--permission-mode", _claude_permission_mode(permission_mode), "--tools", "", "--safe-mode"))
    del service_tier  # Fast is applied as the interactive /fast startup command by the bridge runtime.
    return tuple(command)


def _codex_permissions(permission_mode: str) -> tuple[str, str]:
    if permission_mode == "workspace_write":
        return "on-request", "workspace-write"
    return "never", "read-only"


def _claude_permission_mode(permission_mode: str) -> str:
    return "acceptEdits" if permission_mode == "workspace_write" else "plan"


def _opencode_command(
    _model: str,
    _effort: str,
    _service_tier: str,
    _variant: str,
    _permission_mode: str,
) -> tuple[str, ...]:
    return ("opencode",)


def _deepseek_command(
    _model: str,
    _effort: str,
    _service_tier: str,
    _variant: str,
    _permission_mode: str,
) -> tuple[str, ...]:
    return ("deepseek-api",)


NATIVE_CLI_PROVIDER_CATALOG: tuple[NativeCliProviderDefinition, ...] = (
    NativeCliProviderDefinition(
        provider_id="codex",
        display_name="Codex Luna",
        provider_kind="codex_live_session",
        executable="codex",
        command_builder=_codex_command,
        aliases=("codex_live_session",),
        default_model="gpt-5.6-luna",
        default_reasoning_effort="low",
        default_service_tier="default",
        model_observation_policy="required",
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
        default_model="Gemini 3.5 Flash (Medium)",
        model_observation_policy="required",
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
        default_model="grok-4.5",
        model_observation_policy="required",
        transport="acp_stdio",
    ),
    NativeCliProviderDefinition(
        provider_id="claude",
        display_name="Claude Code",
        provider_kind="claude_code",
        executable="claude",
        command_builder=_claude_command,
        aliases=("claude_code",),
        default_model="claude-haiku-4-5",
        default_reasoning_effort="high",
        default_service_tier="default",
        model_observation_policy="required",
        input_mode="bracketed_paste",
        startup_accept_contains="Quick safety check",
        startup_ready_contains="plan mode on",
    ),
)

STRUCTURED_PROVIDER_CATALOG: tuple[NativeCliProviderDefinition, ...] = (
    NativeCliProviderDefinition(
        provider_id="opencode",
        display_name="OpenCode",
        provider_kind="opencode_server",
        executable="opencode",
        command_builder=_opencode_command,
        aliases=("opencode_server",),
        default_model="opencode-go/glm-5.2",
        model_observation_policy="required",
        runtime_kind="opencode",
        transport="http",
    ),
    NativeCliProviderDefinition(
        provider_id="deepseek",
        display_name="DeepSeek API",
        provider_kind="deepseek_api",
        executable="",
        command_builder=_deepseek_command,
        aliases=("deepseek_api",),
        default_model="deepseek-v4-flash",
        default_reasoning_effort="high",
        default_variant="thinking",
        model_observation_policy="required",
        runtime_kind="api",
        transport="https",
    ),
)

PROVIDER_CATALOG = (*NATIVE_CLI_PROVIDER_CATALOG, *STRUCTURED_PROVIDER_CATALOG)

_PROVIDER_BY_ALIAS = {
    alias.casefold(): definition
    for definition in PROVIDER_CATALOG
    for alias in (definition.provider_id, definition.provider_kind, definition.executable, *definition.aliases)
}


def native_cli_provider_definition(value: object) -> NativeCliProviderDefinition | None:
    return _PROVIDER_BY_ALIAS.get(clean_lobby_text(value, limit=64).casefold())


def native_cli_provider_catalog_payload() -> list[dict[str, object]]:
    return [definition.public_payload() for definition in PROVIDER_CATALOG]


def default_native_cli_provider_specs(*, workspace: str | Path = ".") -> list[NativeCliProviderSpec]:
    return [definition.make_default_spec(cwd=workspace) for definition in NATIVE_CLI_PROVIDER_CATALOG]


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
    workspace = clean_lobby_text(
        payload.get("workspace") or payload.get("workspace_path") or payload.get("cwd"),
        limit=500,
    )
    if not workspace:
        raise ValueError("Native CLI Agent Session workspace is required.")
    spec = definition.make_selected_spec(
        agent_id=agent_id,
        display_name=display_name,
        cwd=workspace,
        model=clean_lobby_text(payload.get("model") or payload.get("model_id"), limit=128),
        reasoning_effort=clean_lobby_text(
            payload.get("reasoning_effort") or payload.get("effort"), limit=32
        ),
        service_tier=clean_lobby_text(payload.get("service_tier"), limit=32),
        variant=clean_lobby_text(payload.get("variant"), limit=64),
        permission_mode=clean_lobby_text(
            payload.get("permission_mode") or payload.get("permission_option"), limit=64
        ),
        model_selection_kind=clean_lobby_text(payload.get("model_selection_kind"), limit=16)
        or "exact",
        catalog_revision=clean_lobby_text(payload.get("catalog_revision"), limit=128),
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
    command = tuple(str(part) for part in list(payload.get("command") or []))
    model = clean_lobby_text(payload.get("model"), limit=128)
    if not command:
        raise ValueError(f"live CLI provider {agent_id} command is required")
    if not model:
        raise ValueError(f"live CLI provider {agent_id} model is required")
    reasoning_effort = clean_lobby_text(payload.get("reasoning_effort") or payload.get("effort"), limit=32)
    service_tier = clean_lobby_text(payload.get("service_tier"), limit=32)
    variant = clean_lobby_text(payload.get("variant"), limit=64)
    permission_mode = clean_lobby_text(payload.get("permission_mode"), limit=64)
    if definition is not None and definition.default_reasoning_effort and not reasoning_effort:
        raise ValueError(f"live CLI provider {agent_id} reasoning effort is required")
    if definition is not None and definition.default_service_tier and not service_tier:
        raise ValueError(f"live CLI provider {agent_id} service tier is required")
    if definition is not None and definition.default_variant and not variant:
        raise ValueError(f"live CLI provider {agent_id} variant is required")
    if not permission_mode:
        raise ValueError(f"live CLI provider {agent_id} permission mode is required")
    cwd = clean_lobby_text(payload.get("cwd"), limit=500)
    if not cwd:
        raise ValueError(f"live CLI provider {agent_id} cwd is required")
    model_selection_kind = clean_lobby_text(payload.get("model_selection_kind"), limit=16) or "exact"
    if model_selection_kind not in {"exact", "alias"}:
        raise ValueError(f"live CLI provider {agent_id} model selection kind is invalid")
    if "--model" in command:
        model_index = command.index("--model")
        command_model = command[model_index + 1] if model_index + 1 < len(command) else ""
        if command_model != model:
            raise ValueError(f"live CLI provider {agent_id} command model does not match its profile")
    spec = NativeCliProviderSpec(
        agent_id=agent_id,
        display_name=clean_lobby_text(payload.get("display_name"), limit=128)
        or (definition.display_name if definition else agent_id),
        command=command,
        cwd=str(Path(cwd).expanduser().resolve()),
        provider_kind=clean_lobby_text(payload.get("provider_kind"), limit=64)
        or (definition.provider_kind if definition else f"{agent_id}_live_session"),
        model=model,
        requested_model_id=model,
        model_selection_kind=model_selection_kind,
        model_observation_policy=(
            definition.model_observation_policy if definition else "unavailable"
        ),
        catalog_revision=clean_lobby_text(payload.get("catalog_revision"), limit=128),
        reasoning_effort=reasoning_effort,
        service_tier=service_tier,
        variant=variant,
        permission_mode=permission_mode,
        runtime_kind=clean_lobby_text(payload.get("runtime_kind"), limit=32)
        or (definition.runtime_kind if definition else "live_cli"),
        transport=clean_lobby_text(payload.get("transport"), limit=32)
        or (definition.transport if definition else "pty"),
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
        startup_ready_contains=str(
            payload.get("startup_ready_contains")
            or (definition.startup_ready_contains if definition else "")
        ),
        turn_timeout_seconds=max(0.1, float(turn_timeout_seconds)),
    )
    validate_native_cli_provider_spec(spec)
    return spec


def validate_native_cli_provider_spec(spec: NativeCliProviderSpec) -> None:
    if not spec.command or not str(spec.command[0]).strip():
        raise ValueError("Native CLI Agent Session command is required.")
    executable = Path(spec.command[0]).name.casefold()
    if executable == "grok" and spec.normalized_provider_kind() == "grok_live_session":
        command_parts = {str(part).casefold() for part in spec.command[1:]}
        if not {"agent", "stdio"}.issubset(command_parts):
            raise ValueError("Grok Agent Sessions require grok agent stdio; PTY fallback is disabled.")
    if spec.permission_mode not in {"meeting_read_only", "workspace_write"}:
        raise ValueError(f"Unsupported native CLI permission mode: {spec.permission_mode}")
    if spec.model_observation_policy not in {"required", "unavailable"}:
        raise ValueError(
            f"Unsupported model observation policy: {spec.model_observation_policy}"
        )
    is_claude = executable == "claude" or spec.normalized_provider_kind() == "claude_code"
    if is_claude:
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
