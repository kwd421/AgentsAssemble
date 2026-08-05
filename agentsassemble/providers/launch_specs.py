"""Provider launch specifications, command construction, and profile validation."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from agentsassemble.persona_cards.selection import persona_spec_kwargs, validate_persona_spec
from agentsassemble.providers.claude_command import claude_interactive_command
from agentsassemble.providers.launch_profile import NativeCliProviderSpec
from agentsassemble.providers.remote_openai import remote_openai_profiles
from agentsassemble.room.text import clean_room_text


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
    default_execution_harness: str = "builtin"
    default_permission_mode: str = "meeting_read_only"
    default_max_output_tokens: int = 0
    catalog_exact_model_allows_empty_reasoning_effort: bool = False
    model_observation_policy: str = "required"
    runtime_kind: str = "live_cli"
    transport: str = "pty"
    reported_transports: tuple[str, ...] = ("pty", "conpty")
    input_mode: str = "line"
    startup_quiet_seconds: float = 1.0
    startup_accept_contains: str = ""
    startup_accept_keys: str = "\r"
    startup_ready_contains: str = ""
    login_command: tuple[str, ...] = ()
    login_flow: str = ""
    catalog_group: str = "subscription"
    workspace_required: bool = True

    def reasoning_effort_is_required(
        self,
        *,
        model_selection_kind: str,
        catalog_revision: str,
    ) -> bool:
        if not self.default_reasoning_effort:
            return False
        return not (
            self.catalog_exact_model_allows_empty_reasoning_effort
            and model_selection_kind == "exact"
            and bool(catalog_revision)
        )

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
            execution_harness=self.default_execution_harness,
            permission_mode=self.default_permission_mode,
            max_output_tokens=self.default_max_output_tokens,
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
        execution_harness: str = "",
        permission_mode: str = "",
        max_output_tokens: int = 0,
        provider_endpoint: str = "",
        persona_card_id: str = "",
        persona_card_summary: dict[str, object] | None = None,
        model_selection_kind: str = "exact",
        catalog_revision: str = "",
        default_responder: bool = True,
    ) -> NativeCliProviderSpec:
        selected_model = clean_room_text(model, limit=128)
        selected_effort = clean_room_text(reasoning_effort, limit=32)
        if self.provider_id == "antigravity":
            selected_model, inferred_effort = _split_antigravity_model(
                selected_model,
            )
            selected_effort = selected_effort or inferred_effort
        selected_service_tier = clean_room_text(service_tier, limit=32)
        if self.provider_id == "cursor":
            selected_model, inferred_effort, inferred_fast = split_cursor_model(
                selected_model,
            )
            selected_effort = selected_effort or inferred_effort
            if inferred_fast and not selected_service_tier:
                selected_service_tier = "fast"
        selected_variant = clean_room_text(variant, limit=64)
        selected_execution_harness = (
            clean_room_text(execution_harness, limit=32)
            or self.default_execution_harness
        )
        if selected_execution_harness not in {"builtin", "codex", "claude"}:
            raise ValueError(
                f"Provider {self.provider_id} execution harness is invalid."
            )
        if self.runtime_kind != "api" and selected_execution_harness != "builtin":
            raise ValueError(
                f"Provider {self.provider_id} does not support an alternate execution harness."
            )
        selected_permission = clean_room_text(permission_mode, limit=64)
        selected_max_output_tokens = int(
            max_output_tokens or self.default_max_output_tokens
        )
        if selected_max_output_tokens < 0:
            raise ValueError(
                f"Provider {self.provider_id} maximum output tokens cannot be negative."
            )
        selected_kind = clean_room_text(model_selection_kind, limit=16)
        selected_catalog_revision = clean_room_text(catalog_revision, limit=128)
        if self.provider_id == "cursor" and selected_model == "auto":
            selected_kind = "alias"
        if not selected_model:
            raise ValueError(f"Provider {self.provider_id} model is required.")
        if (
            self.reasoning_effort_is_required(
                model_selection_kind=selected_kind,
                catalog_revision=selected_catalog_revision,
            )
            and not selected_effort
        ):
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
            agent_id=clean_room_text(agent_id, limit=128) or self.provider_id,
            display_name=clean_room_text(display_name, limit=128) or self.display_name,
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
            requested_model_id=_requested_model_id(
                self.provider_id,
                selected_model,
                selected_effort,
                selected_service_tier,
            ),
            model_selection_kind=selected_kind,
            model_observation_policy=self.model_observation_policy,
            catalog_revision=selected_catalog_revision,
            reasoning_effort=selected_effort,
            service_tier=selected_service_tier,
            variant=selected_variant,
            execution_harness=selected_execution_harness,
            permission_mode=selected_permission,
            max_output_tokens=selected_max_output_tokens,
            provider_endpoint=clean_room_text(provider_endpoint, limit=1000),
            persona_card_id=clean_room_text(persona_card_id, limit=80),
            persona_card_summary=dict(persona_card_summary or {}),
            runtime_kind=self.runtime_kind,
            transport=self.transport,
            default_responder=default_responder,
            input_mode=self.input_mode,
            startup_quiet_seconds=self.startup_quiet_seconds,
            startup_accept_contains=self.startup_accept_contains,
            startup_accept_keys=self.startup_accept_keys,
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
            "login_available": bool(self.login_command),
            "login_label": (
                f"{self.display_name} 로그인"
                if self.login_command
                else ""
            ),
            "login_flow": self.login_flow if self.login_command else "",
            "catalog_group": self.catalog_group,
            "workspace_required": self.workspace_required,
        }


class UnsupportedNativeCliProvider(ValueError):
    pass


class StoredProviderProfileError(ValueError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


_LEGACY_CLAUDE_STARTUP_READY_CONTAINS = "plan mode on"


def native_cli_provider_spec_from_stored_session_strict(
    session: dict[str, object],
) -> NativeCliProviderSpec:
    agent_id = clean_room_text(session.get("participant_id") or session.get("session_id"), limit=128)
    definition = native_cli_provider_definition(session.get("provider_kind"))
    if not agent_id or definition is None:
        raise StoredProviderProfileError(
            "Stored Agent Session provider profile is incomplete.",
            code="profile_incomplete",
        )
    required = {
        "display_name": clean_room_text(session.get("display_name"), limit=128),
        "workspace": clean_room_text(session.get("workspace"), limit=500),
        "model": clean_room_text(session.get("model"), limit=128),
        "permission_mode": clean_room_text(session.get("permission_mode"), limit=64),
        "runtime_profile_key": clean_room_text(session.get("runtime_profile_key"), limit=128),
    }
    if any(not value for value in required.values()):
        raise StoredProviderProfileError(
            "Stored Agent Session provider profile is incomplete.",
            code="profile_incomplete",
        )
    model_selection_kind = (
        clean_room_text(session.get("model_selection_kind"), limit=16) or "exact"
    )
    catalog_revision = clean_room_text(session.get("catalog_revision"), limit=128)
    for field, default in (
        (
            "reasoning_effort",
            (
                definition.default_reasoning_effort
                if definition.reasoning_effort_is_required(
                    model_selection_kind=model_selection_kind,
                    catalog_revision=catalog_revision,
                )
                else ""
            ),
        ),
        ("service_tier", definition.default_service_tier),
        ("variant", definition.default_variant),
    ):
        if default and not clean_room_text(session.get(field), limit=64):
            raise StoredProviderProfileError(
                f"Stored Agent Session is missing required {field}.",
                code="profile_incomplete",
            )
    stored_runtime_kind = clean_room_text(session.get("runtime_kind"), limit=64)
    stored_transport = clean_room_text(session.get("transport"), limit=64)
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
        reasoning_effort=clean_room_text(session.get("reasoning_effort"), limit=32),
        service_tier=clean_room_text(session.get("service_tier"), limit=32),
        variant=clean_room_text(session.get("variant"), limit=64),
        execution_harness=(
            clean_room_text(session.get("execution_harness"), limit=32)
            or "builtin"
        ),
        permission_mode=required["permission_mode"],
        max_output_tokens=_nonnegative_int(
            session.get("max_output_tokens"),
            field="max_output_tokens",
        ),
        provider_endpoint=clean_room_text(
            session.get("provider_endpoint"),
            limit=1000,
        ),
        model_selection_kind=model_selection_kind,
        catalog_revision=catalog_revision,
        **persona_spec_kwargs(session),
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
    legacy_claude_startup_profile = (
        definition.provider_id == "claude"
        and stored_transport == definition.transport
        and spec.command == stored_command
        and replace(
            spec,
            startup_ready_contains=_LEGACY_CLAUDE_STARTUP_READY_CONTAINS,
        ).runtime_profile_key()
        == required["runtime_profile_key"]
    )
    previous_claude_command = _previous_claude_command(spec)
    claude_room_portal_upgrade_profile = (
        definition.provider_id == "claude"
        and stored_transport == definition.transport
        and stored_command == previous_claude_command
        and replace(
            spec,
            command=previous_claude_command,
        ).runtime_profile_key()
        == required["runtime_profile_key"]
    )
    previous_grok_command = _previous_grok_command(spec)
    grok_cli_option_order_upgrade_profile = (
        definition.provider_id == "grok"
        and stored_transport == definition.transport
        and stored_command == previous_grok_command
        and replace(
            spec,
            command=previous_grok_command,
        ).runtime_profile_key()
        == required["runtime_profile_key"]
    )
    reported_transport_overwrite_profile = (
        stored_transport in definition.reported_transports
        and spec.command == stored_command
        and spec.runtime_profile_key() == required["runtime_profile_key"]
    )
    if stored_transport != definition.transport and not (
        legacy_grok_transport_profile or reported_transport_overwrite_profile
    ):
        raise StoredProviderProfileError(
            "Stored Agent Session provider definition changed.",
            code="provider_definition_changed",
        )
    exact_stored_profile = spec.command == stored_command and (
        profile_matches
        or legacy_grok_transport_profile
        or legacy_claude_startup_profile
        or reported_transport_overwrite_profile
    )
    if not exact_stored_profile and not (
        claude_room_portal_upgrade_profile or grok_cli_option_order_upgrade_profile
    ):
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
    effort: str,
    _service_tier: str,
    _variant: str,
    permission_mode: str,
) -> tuple[str, ...]:
    if not model:
        raise ValueError("Antigravity model is required.")
    command = ["agy"]
    if model:
        command.extend(("--model", _antigravity_effective_model(model, effort)))
    if permission_mode == "workspace_write":
        command.extend(("--mode", "accept-edits"))
    else:
        command.append("--sandbox")
    return tuple(command)


def _antigravity_effective_model(model: str, effort: str) -> str:
    clean_model = clean_room_text(model, limit=128)
    clean_effort = clean_room_text(effort, limit=32).casefold()
    if clean_effort and not clean_model.casefold().endswith(f"-{clean_effort}"):
        return f"{clean_model}-{clean_effort}"
    return clean_model


def _split_antigravity_model(model: str) -> tuple[str, str]:
    clean_model = clean_room_text(model, limit=128)
    display_match = re.fullmatch(
        r"(.+?)\s+\((Low|Medium|High)\)",
        clean_model,
        flags=re.IGNORECASE,
    )
    if display_match:
        base = re.sub(
            r"[^a-z0-9.]+",
            "-",
            display_match.group(1).casefold(),
        ).strip("-")
        return base, display_match.group(2).casefold()
    slug_match = re.fullmatch(
        r"(.+?)-(low|medium|high)",
        clean_model,
        flags=re.IGNORECASE,
    )
    if slug_match:
        return slug_match.group(1), slug_match.group(2).casefold()
    return clean_model, ""


def _requested_model_id(provider_id: str, model: str, effort: str, service_tier: str = "") -> str:
    if provider_id == "antigravity":
        return _antigravity_effective_model(model, effort)
    if provider_id == "cursor":
        return _cursor_effective_model(model, effort, service_tier)
    return model


def _grok_command(
    model: str,
    effort: str,
    _service_tier: str,
    _variant: str,
    permission_mode: str,
) -> tuple[str, ...]:
    command = ["grok"]
    if permission_mode == "workspace_write":
        command.extend(("--permission-mode", "acceptEdits"))
    command.append("agent")
    if model:
        command.extend(("--model", model))
    if effort:
        command.extend(("--reasoning-effort", effort))
    command.append("stdio")
    return tuple(command)


def _previous_grok_command(spec: NativeCliProviderSpec) -> tuple[str, ...]:
    if spec.normalized_provider_kind() != "grok_live_session":
        return ()
    command = ["grok"]
    if spec.model:
        command.extend(("--model", spec.model))
    if spec.reasoning_effort:
        command.extend(("--reasoning-effort", spec.reasoning_effort))
    command.extend(("agent", "stdio"))
    return tuple(command)


def _claude_command(
    model: str,
    effort: str,
    service_tier: str,
    _variant: str,
    permission_mode: str,
) -> tuple[str, ...]:
    command = claude_interactive_command(
        executable="claude",
        model=model,
        reasoning_effort=effort,
        permission_mode=permission_mode,
        workspace_write_mode="acceptEdits",
    )
    del service_tier  # Fast is applied as the interactive /fast startup command by the bridge runtime.
    return tuple(command)


def _previous_claude_command(spec: NativeCliProviderSpec) -> tuple[str, ...]:
    if spec.normalized_provider_kind() != "claude_code":
        return ()
    command = ["claude", "--model", spec.model]
    if spec.reasoning_effort:
        command.extend(("--effort", spec.reasoning_effort))
    if spec.permission_mode == "workspace_write":
        command.extend(("--permission-mode", "acceptEdits"))
    command.extend(("--tools", "", "--safe-mode"))
    return tuple(command)


def _cursor_command(
    model: str,
    effort: str,
    service_tier: str,
    _variant: str,
    permission_mode: str,
) -> tuple[str, ...]:
    if not model:
        raise ValueError("Cursor model is required.")
    command = [
        "cursor-agent",
        "--model",
        _cursor_effective_model(model, effort, service_tier),
        "--sandbox",
        "enabled",
    ]
    if permission_mode == "meeting_read_only":
        command.extend(("--mode", "ask"))
    return tuple(command)


# Cursor bakes reasoning effort and the "fast" flag into its model slugs, e.g.
# ``gpt-5.6-luna-high-fast`` or ``claude-opus-4-8-thinking-xhigh``. Cursor uses a
# couple of orderings (``base-thinking-high`` and ``base-medium-thinking``), so we
# peel recognised trailing tokens off the end and treat everything between the
# base and the optional ``-fast`` as the reasoning suffix. We surface a base
# model / reasoning / fast trio in the UI and recombine here so the CLI still
# receives the exact slug Cursor advertised.
_CURSOR_PLAIN_EFFORT = "default"
# An agent already running on the joiner's own machine, connected over the
# room WebSocket. It has no launch spec here because this server never
# starts it: the participant owns the process and the provider account.
EXTERNAL_AGENT_PROVIDER_KIND = "external_agent"


_CURSOR_EFFORT_TOKENS = frozenset(
    {
        "thinking",
        "minimal",
        "none",
        "low",
        "medium",
        "high",
        "extra",
        "xhigh",
        "max",
        "ultra",
    }
)


def split_cursor_model(model: str) -> tuple[str, str, bool]:
    """Split a Cursor slug into (base_model, reasoning_effort, fast)."""

    clean = clean_room_text(model, limit=128)
    fast = False
    if clean.casefold().endswith("-fast"):
        clean = clean[: -len("-fast")]
        fast = True
    parts = clean.split("-")
    cut = len(parts)
    while cut > 1 and parts[cut - 1].casefold() in _CURSOR_EFFORT_TOKENS:
        cut -= 1
    base = "-".join(parts[:cut])
    effort = "-".join(parts[cut:])
    if not base:
        return clean, "", fast
    return base, effort, fast


def _cursor_effective_model(model: str, effort: str, service_tier: str) -> str:
    clean_model = clean_room_text(model, limit=128)
    result = clean_model
    clean_effort = clean_room_text(effort, limit=32).casefold()
    if clean_effort and clean_effort != _CURSOR_PLAIN_EFFORT:
        marker = f"-{clean_effort}"
        if not result.casefold().endswith(marker):
            result = f"{result}{marker}"
    if clean_room_text(service_tier, limit=32).casefold() == "fast" and not result.casefold().endswith("-fast"):
        result = f"{result}-fast"
    return result


def _codex_permissions(permission_mode: str) -> tuple[str, str]:
    if permission_mode == "workspace_write":
        return "on-request", "workspace-write"
    return "never", "read-only"


def _opencode_command(
    _model: str,
    _effort: str,
    _service_tier: str,
    _variant: str,
    _permission_mode: str,
) -> tuple[str, ...]:
    return ("opencode",)


def _remote_openai_command(
    _model: str,
    _effort: str,
    _service_tier: str,
    _variant: str,
    _permission_mode: str,
) -> tuple[str, ...]:
    return ("server-owned-api",)


def _ollama_command(
    _model: str,
    _effort: str,
    _service_tier: str,
    _variant: str,
    _permission_mode: str,
) -> tuple[str, ...]:
    return ("ollama-openai",)


def _lmstudio_command(
    _model: str,
    _effort: str,
    _service_tier: str,
    _variant: str,
    _permission_mode: str,
) -> tuple[str, ...]:
    return ("lmstudio-openai",)


NATIVE_CLI_PROVIDER_CATALOG: tuple[NativeCliProviderDefinition, ...] = (
    NativeCliProviderDefinition(
        provider_id="codex",
        display_name="Codex",
        provider_kind="codex_live_session",
        executable="codex",
        command_builder=_codex_command,
        aliases=("codex_live_session",),
        default_model="gpt-5.6-luna",
        default_reasoning_effort="low",
        default_service_tier="default",
        model_observation_policy="required",
        reported_transports=("stdio_jsonl",),
        input_mode="bracketed_paste",
        startup_accept_contains="Do you trust",
        login_command=("codex", "login"),
        login_flow="browser_oauth",
    ),
    NativeCliProviderDefinition(
        provider_id="antigravity",
        display_name="Antigravity",
        provider_kind="antigravity_live_session",
        executable="agy",
        command_builder=_antigravity_command,
        aliases=("agy", "antigravity_live_session"),
        default_model="gemini-3.6-flash",
        default_reasoning_effort="medium",
        catalog_exact_model_allows_empty_reasoning_effort=True,
        model_observation_policy="required",
        input_mode="bracketed_paste",
        startup_quiet_seconds=5.0,
        startup_accept_contains="Do you trust",
        login_command=("agy",),
        login_flow="interactive_terminal",
    ),
    NativeCliProviderDefinition(
        provider_id="grok",
        display_name="Grok",
        provider_kind="grok_live_session",
        executable="grok",
        command_builder=_grok_command,
        aliases=("grok_live_session",),
        default_model="grok-4.5",
        model_observation_policy="required",
        transport="acp_stdio",
        reported_transports=("acp_stdio",),
        login_command=("grok", "login"),
        login_flow="browser_oauth",
    ),
    NativeCliProviderDefinition(
        provider_id="claude",
        display_name="Claude",
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
        login_command=("claude", "auth", "login"),
        login_flow="browser_oauth",
    ),
    NativeCliProviderDefinition(
        provider_id="cursor",
        display_name="Cursor",
        provider_kind="cursor_live_session",
        executable="cursor-agent",
        command_builder=_cursor_command,
        aliases=("cursor-agent", "cursor_live_session"),
        default_model="auto",
        model_observation_policy="unavailable",
        input_mode="bracketed_paste",
        startup_accept_contains="Do you trust",
        startup_accept_keys="a",
        startup_ready_contains="Plan, search, build anything",
        login_command=("cursor-agent", "login"),
        login_flow="browser_oauth",
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
        reported_transports=("http_sse",),
        login_command=("opencode", "auth", "login"),
        login_flow="interactive_terminal",
    ),
    *(
        NativeCliProviderDefinition(
            provider_id=profile.provider_id,
            display_name=profile.display_name,
            provider_kind=profile.provider_kind,
            executable="",
            command_builder=_remote_openai_command,
            aliases=(profile.provider_kind,),
            default_model=profile.default_model,
            default_reasoning_effort=profile.default_reasoning_effort,
            default_variant=profile.default_variant,
            default_max_output_tokens=profile.max_output_tokens,
            model_observation_policy="required",
            runtime_kind="api",
            transport="https",
            reported_transports=("https_sse",),
            catalog_group="api",
            workspace_required=False,
        )
        for profile in remote_openai_profiles()
    ),
    NativeCliProviderDefinition(
        provider_id="ollama",
        display_name="Ollama",
        provider_kind="ollama_api",
        executable="ollama",
        command_builder=_ollama_command,
        aliases=("ollama_api",),
        default_model="nemotron-3-super:cloud",
        model_observation_policy="required",
        runtime_kind="api",
        transport="http",
        reported_transports=("http_sse",),
        catalog_group="subscription",
        workspace_required=False,
    ),
    NativeCliProviderDefinition(
        provider_id="lmstudio",
        display_name="LM Studio",
        provider_kind="lmstudio_api",
        executable="lms",
        command_builder=_lmstudio_command,
        aliases=("lmstudio_api", "lm_studio"),
        default_model="gemma-4-e4b-it",
        model_observation_policy="required",
        runtime_kind="api",
        transport="http",
        reported_transports=("http_sse",),
        catalog_group="local",
        workspace_required=False,
    ),
)

PROVIDER_CATALOG = (*NATIVE_CLI_PROVIDER_CATALOG, *STRUCTURED_PROVIDER_CATALOG)

_PROVIDER_BY_ALIAS = {
    alias.casefold(): definition
    for definition in PROVIDER_CATALOG
    for alias in (definition.provider_id, definition.provider_kind, definition.executable, *definition.aliases)
}


def native_cli_provider_definition(value: object) -> NativeCliProviderDefinition | None:
    return _PROVIDER_BY_ALIAS.get(clean_room_text(value, limit=64).casefold())


def native_cli_provider_catalog_payload() -> list[dict[str, object]]:
    return [definition.public_payload() for definition in PROVIDER_CATALOG]


def default_native_cli_provider_specs(*, workspace: str | Path = ".") -> list[NativeCliProviderSpec]:
    return [definition.make_default_spec(cwd=workspace) for definition in NATIVE_CLI_PROVIDER_CATALOG]


def native_cli_provider_spec_from_payload(payload: dict[str, object]) -> NativeCliProviderSpec:
    provider_value = payload.get("provider_id") or payload.get("provider_kind") or payload.get("provider")
    definition = native_cli_provider_definition(provider_value)
    if definition is None:
        provider = clean_room_text(provider_value, limit=64) or "unknown"
        raise UnsupportedNativeCliProvider(
            f"Provider {provider} is not available as a native CLI Agent Session."
        )
    display_name = clean_room_text(payload.get("display_name"), limit=64) or definition.display_name
    explicit_agent_id = clean_room_text(payload.get("agent_id") or payload.get("participant_id"), limit=128)
    agent_id = explicit_agent_id or _slug_agent_id(f"{definition.provider_id}-{display_name}")
    workspace = clean_room_text(
        payload.get("workspace") or payload.get("workspace_path") or payload.get("cwd"),
        limit=500,
    )
    requested_permission = clean_room_text(
        payload.get("permission_mode") or payload.get("permission_option"),
        limit=64,
    )
    if not workspace and (
        definition.workspace_required
        or (
            definition.runtime_kind == "api"
            and (
                requested_permission == "workspace_write"
                or clean_room_text(payload.get("execution_harness"), limit=32)
                not in {"", "builtin"}
            )
        )
    ):
        raise ValueError("Native CLI Agent Session workspace is required.")
    if not workspace:
        workspace = str(Path.cwd())
    spec = definition.make_selected_spec(
        agent_id=agent_id,
        display_name=display_name,
        cwd=workspace,
        model=clean_room_text(payload.get("model") or payload.get("model_id"), limit=128),
        reasoning_effort=clean_room_text(
            payload.get("reasoning_effort") or payload.get("effort"), limit=32
        ),
        service_tier=clean_room_text(payload.get("service_tier"), limit=32),
        variant=clean_room_text(payload.get("variant"), limit=64),
        execution_harness=clean_room_text(
            payload.get("execution_harness"),
            limit=32,
        ),
        permission_mode=requested_permission,
        max_output_tokens=_nonnegative_int(
            payload.get("max_output_tokens"),
            field="max_output_tokens",
        ),
        provider_endpoint=clean_room_text(
            payload.get("provider_endpoint"),
            limit=1000,
        ),
        model_selection_kind=clean_room_text(payload.get("model_selection_kind"), limit=16)
        or "exact",
        catalog_revision=clean_room_text(payload.get("catalog_revision"), limit=128),
        **persona_spec_kwargs(payload),
    )
    validate_native_cli_provider_spec(spec)
    return spec


def native_cli_provider_spec_from_config(
    payload: dict[str, object],
    *,
    turn_timeout_seconds: float,
) -> NativeCliProviderSpec:
    agent_id = clean_room_text(payload.get("id") or payload.get("agent_id"), limit=128)
    if not agent_id:
        raise ValueError("live CLI provider id is required")
    definition = native_cli_provider_definition(
        payload.get("provider_id") or payload.get("provider_kind") or agent_id
    )
    command = tuple(str(part) for part in list(payload.get("command") or []))
    model = clean_room_text(payload.get("model"), limit=128)
    if not command:
        raise ValueError(f"live CLI provider {agent_id} command is required")
    if not model:
        raise ValueError(f"live CLI provider {agent_id} model is required")
    reasoning_effort = clean_room_text(payload.get("reasoning_effort") or payload.get("effort"), limit=32)
    service_tier = clean_room_text(payload.get("service_tier"), limit=32)
    variant = clean_room_text(payload.get("variant"), limit=64)
    execution_harness = (
        clean_room_text(payload.get("execution_harness"), limit=32)
        or "builtin"
    )
    permission_mode = clean_room_text(payload.get("permission_mode"), limit=64)
    if definition is not None and definition.default_reasoning_effort and not reasoning_effort:
        raise ValueError(f"live CLI provider {agent_id} reasoning effort is required")
    if definition is not None and definition.default_service_tier and not service_tier:
        raise ValueError(f"live CLI provider {agent_id} service tier is required")
    if definition is not None and definition.default_variant and not variant:
        raise ValueError(f"live CLI provider {agent_id} variant is required")
    if not permission_mode:
        raise ValueError(f"live CLI provider {agent_id} permission mode is required")
    cwd = clean_room_text(payload.get("cwd"), limit=500)
    if not cwd:
        raise ValueError(f"live CLI provider {agent_id} cwd is required")
    model_selection_kind = clean_room_text(payload.get("model_selection_kind"), limit=16) or "exact"
    if model_selection_kind not in {"exact", "alias"}:
        raise ValueError(f"live CLI provider {agent_id} model selection kind is invalid")
    if "--model" in command:
        model_index = command.index("--model")
        command_model = command[model_index + 1] if model_index + 1 < len(command) else ""
        if command_model != model:
            raise ValueError(f"live CLI provider {agent_id} command model does not match its profile")
    spec = NativeCliProviderSpec(
        agent_id=agent_id,
        display_name=clean_room_text(payload.get("display_name"), limit=128)
        or (definition.display_name if definition else agent_id),
        command=command,
        cwd=str(Path(cwd).expanduser().resolve()),
        provider_kind=clean_room_text(payload.get("provider_kind"), limit=64)
        or (definition.provider_kind if definition else f"{agent_id}_live_session"),
        model=model,
        requested_model_id=model,
        model_selection_kind=model_selection_kind,
        model_observation_policy=(
            definition.model_observation_policy if definition else "unavailable"
        ),
        catalog_revision=clean_room_text(payload.get("catalog_revision"), limit=128),
        reasoning_effort=reasoning_effort,
        service_tier=service_tier,
        variant=variant,
        execution_harness=execution_harness,
        permission_mode=permission_mode,
        max_output_tokens=_nonnegative_int(
            payload.get("max_output_tokens"),
            field="max_output_tokens",
        ),
        provider_endpoint=clean_room_text(
            payload.get("provider_endpoint"),
            limit=1000,
        ),
        runtime_kind=clean_room_text(payload.get("runtime_kind"), limit=32)
        or (definition.runtime_kind if definition else "live_cli"),
        transport=clean_room_text(payload.get("transport"), limit=32)
        or (definition.transport if definition else "pty"),
        default_responder=bool(payload.get("default_responder", False)),
        quiet_seconds=_float_value(payload.get("quiet_seconds"), 4.0),
        input_mode=clean_room_text(payload.get("input_mode"), limit=64)
        or (definition.input_mode if definition else "line"),
        submit_newline=str(payload.get("submit_newline") or "\r"),
        submit_delay_seconds=_float_value(payload.get("submit_delay_seconds"), 0.1),
        **persona_spec_kwargs(payload),
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
    if spec.execution_harness not in {"builtin", "codex", "claude"}:
        raise ValueError(
            f"Unsupported execution harness: {spec.execution_harness}"
        )
    if spec.runtime_kind != "api" and spec.execution_harness != "builtin":
        raise ValueError(
            "Alternate execution harnesses are available only for API and Local providers."
        )
    if spec.model_observation_policy not in {"required", "unavailable"}:
        raise ValueError(
            f"Unsupported model observation policy: {spec.model_observation_policy}"
        )
    validate_persona_spec(spec.persona_card_id, spec.persona_card_summary)
    is_claude = executable == "claude" or spec.normalized_provider_kind() == "claude_code"
    if is_claude:
        forbidden = [part for part in spec.command[1:] if part in {"-p", "--print"} or part.startswith("--print=")]
        if forbidden:
            raise ValueError("Claude Code Agent Sessions require interactive mode; print mode is forbidden.")
    is_cursor = executable == "cursor-agent" or spec.normalized_provider_kind() == "cursor_live_session"
    if is_cursor:
        forbidden = [part for part in spec.command[1:] if part in {"-p", "--print"} or part.startswith("--print=")]
        if forbidden:
            raise ValueError("Cursor Agent Sessions require interactive mode; print mode is forbidden.")


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


def _nonnegative_int(value: object, *, field: str) -> int:
    if value in (None, ""):
        return 0
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a non-negative integer.")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field} must be a non-negative integer.") from error
    if parsed < 0:
        raise ValueError(f"{field} must be a non-negative integer.")
    return parsed
