"""Canonical execution-harness registry for API/Local providers.

Public values::

    execution_harness = builtin | codex | claude | opencode | pi

The registry owns install discovery metadata, capability flags, catalog
options, and runtime construction for non-builtin harnesses. Unsupported
capabilities are reported explicitly and never silently fall back.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from agentsassemble.room.text import clean_room_text


ExecutableResolver = Callable[[str], str | None]
HarnessRuntimeFactory = Callable[..., Any]

PUBLIC_HARNESS_IDS = ("builtin", "codex", "claude", "opencode", "pi")
ALTERNATE_HARNESS_IDS = frozenset({"codex", "claude", "opencode", "pi"})


@dataclass(frozen=True)
class HarnessDefinition:
    """One selectable execution harness and its supported product surface."""

    id: str
    label: str
    description: str
    executable_names: tuple[str, ...] = ()
    supports_model_gateway: bool = False
    supports_tool_events: bool = False
    supports_public_reasoning: bool = False
    supports_approvals: bool = False
    supports_choices: bool = False
    supports_interrupt: bool = False
    supports_resume: bool = False
    supports_session_persistence: bool = False
    supports_compaction_events: bool = False
    catalog_only_when_installed: bool = True

    def is_installed(self, resolver: ExecutableResolver | None = None) -> bool:
        if self.id == "builtin":
            return True
        if not self.executable_names:
            return False
        resolve = resolver or shutil.which
        return any(bool(resolve(name)) for name in self.executable_names)

    def resolve_executable(self, resolver: ExecutableResolver | None = None) -> str | None:
        resolve = resolver or shutil.which
        for name in self.executable_names:
            path = resolve(name)
            if path:
                return path
        return None

    def unsupported_capabilities(self) -> tuple[str, ...]:
        flags = (
            ("model_gateway", self.supports_model_gateway),
            ("tool_events", self.supports_tool_events),
            ("public_reasoning", self.supports_public_reasoning),
            ("approvals", self.supports_approvals),
            ("choices", self.supports_choices),
            ("interrupt", self.supports_interrupt),
            ("resume", self.supports_resume),
            ("session_persistence", self.supports_session_persistence),
            ("compaction_events", self.supports_compaction_events),
        )
        return tuple(name for name, supported in flags if not supported)

    def catalog_option(self) -> dict[str, object]:
        metadata: dict[str, object] = {
            "description": self.description,
            "supports_model_gateway": self.supports_model_gateway,
            "supports_tool_events": self.supports_tool_events,
            "supports_public_reasoning": self.supports_public_reasoning,
            "supports_approvals": self.supports_approvals,
            "supports_choices": self.supports_choices,
            "supports_interrupt": self.supports_interrupt,
            "supports_resume": self.supports_resume,
            "supports_session_persistence": self.supports_session_persistence,
            "supports_compaction_events": self.supports_compaction_events,
        }
        unsupported = self.unsupported_capabilities()
        if unsupported:
            metadata["unsupported"] = list(unsupported)
        return {
            "value": self.id,
            "label": self.label,
            "metadata": metadata,
        }


_BUILTIN = HarnessDefinition(
    id="builtin",
    label="기본",
    description="프로바이더 기본 API 런타임으로 작업합니다.",
    catalog_only_when_installed=False,
    supports_interrupt=True,
    supports_session_persistence=True,
)

_CODEX = HarnessDefinition(
    id="codex",
    label="Codex",
    description="Codex의 파일·명령·승인 하네스를 사용합니다.",
    executable_names=("codex",),
    supports_model_gateway=True,
    supports_tool_events=True,
    supports_public_reasoning=True,
    supports_approvals=True,
    supports_choices=True,
    supports_interrupt=True,
    supports_resume=True,
    supports_session_persistence=True,
    supports_compaction_events=True,
)

_CLAUDE = HarnessDefinition(
    id="claude",
    label="Claude Code",
    description="Claude Code의 파일·명령·승인 하네스를 사용합니다.",
    executable_names=("claude",),
    supports_model_gateway=True,
    supports_tool_events=True,
    supports_public_reasoning=True,
    supports_approvals=True,
    supports_choices=True,
    supports_interrupt=True,
    supports_resume=True,
    supports_session_persistence=True,
    supports_compaction_events=False,
)

_OPENCODE = HarnessDefinition(
    id="opencode",
    label="OpenCode",
    description="OpenCode serve 구조화 이벤트로 파일·명령·승인을 처리합니다.",
    executable_names=("opencode",),
    supports_model_gateway=True,
    supports_tool_events=True,
    supports_public_reasoning=True,
    supports_approvals=True,
    supports_choices=True,
    supports_interrupt=True,
    supports_resume=True,
    supports_session_persistence=True,
    supports_compaction_events=True,
)

_PI = HarnessDefinition(
    id="pi",
    label="Pi",
    description="Pi JSONL RPC로 도구 이벤트를 수신합니다. PTY 화면 파싱은 사용하지 않습니다.",
    executable_names=("pi",),
    supports_model_gateway=True,
    supports_tool_events=True,
    supports_public_reasoning=True,
    supports_approvals=False,
    supports_choices=False,
    supports_interrupt=True,
    supports_resume=True,
    supports_session_persistence=True,
    supports_compaction_events=True,
)

_REGISTRY: Mapping[str, HarnessDefinition] = {
    definition.id: definition
    for definition in (_BUILTIN, _CODEX, _CLAUDE, _OPENCODE, _PI)
}


def harness_definition(value: object) -> HarnessDefinition | None:
    harness_id = clean_room_text(value, limit=32).casefold()
    return _REGISTRY.get(harness_id)


def require_harness_definition(value: object) -> HarnessDefinition:
    definition = harness_definition(value)
    if definition is None:
        raise ValueError(f"Unsupported execution harness: {value!r}")
    return definition


def is_public_harness_id(value: object) -> bool:
    return harness_definition(value) is not None


def alternate_harness_ids() -> frozenset[str]:
    return ALTERNATE_HARNESS_IDS


def catalog_harness_options(
    *,
    resolver: ExecutableResolver | None = None,
) -> list[dict[str, object]]:
    """Return catalog select options for installed harnesses plus builtin."""

    options: list[dict[str, object]] = [_BUILTIN.catalog_option()]
    for harness_id in ("codex", "claude", "opencode", "pi"):
        definition = _REGISTRY[harness_id]
        if definition.catalog_only_when_installed and not definition.is_installed(resolver):
            continue
        options.append(definition.catalog_option())
    return options


def create_alternate_harness_runtime(
    *,
    harness: str,
    agent_id: str,
    runtime_kind: str,
    provider_kind: str,
    provider_endpoint: str,
    credential: str,
    model: str,
    reasoning_effort: str,
    permission_mode: str,
    service_tier: str,
    workspace: str,
    runtime_state_dir: str,
    environment: dict[str, str] | None,
    room_portal,
    request_headers: tuple[tuple[str, str], ...] = (),
    variant: str = "",
    max_output_tokens: int = 0,
    context_contract_bytes: int = 256_000,
):
    """Build a non-builtin harness runtime. Builtin is handled by the API path."""

    definition = require_harness_definition(harness)
    if definition.id == "builtin":
        raise ValueError("builtin harness is not created through the alternate registry path")
    if definition.id in {"codex", "claude"}:
        from agentsassemble.providers.native_harness import create_codex_or_claude_harness

        return create_codex_or_claude_harness(
            agent_id=agent_id,
            harness=definition.id,
            runtime_kind=runtime_kind,
            provider_kind=provider_kind,
            provider_endpoint=provider_endpoint,
            credential=credential,
            model=model,
            reasoning_effort=reasoning_effort,
            permission_mode=permission_mode,
            service_tier=service_tier,
            workspace=workspace,
            runtime_state_dir=runtime_state_dir,
            environment=environment,
            room_portal=room_portal,
            request_headers=request_headers,
            variant=variant,
            max_output_tokens=max_output_tokens,
            context_contract_bytes=context_contract_bytes,
        )
    if definition.id == "opencode":
        from agentsassemble.providers.harness_opencode import create_opencode_harness_runtime

        return create_opencode_harness_runtime(
            agent_id=agent_id,
            runtime_kind=runtime_kind,
            provider_kind=provider_kind,
            provider_endpoint=provider_endpoint,
            credential=credential,
            model=model,
            reasoning_effort=reasoning_effort,
            permission_mode=permission_mode,
            workspace=workspace,
            runtime_state_dir=runtime_state_dir,
            environment=environment,
            room_portal=room_portal,
            request_headers=request_headers,
            variant=variant,
            max_output_tokens=max_output_tokens,
            context_contract_bytes=context_contract_bytes,
        )
    if definition.id == "pi":
        from agentsassemble.providers.harness_pi import create_pi_harness_runtime

        return create_pi_harness_runtime(
            agent_id=agent_id,
            runtime_kind=runtime_kind,
            provider_kind=provider_kind,
            provider_endpoint=provider_endpoint,
            credential=credential,
            model=model,
            reasoning_effort=reasoning_effort,
            permission_mode=permission_mode,
            workspace=workspace,
            runtime_state_dir=runtime_state_dir,
            environment=environment,
            room_portal=room_portal,
            request_headers=request_headers,
            variant=variant,
            max_output_tokens=max_output_tokens,
            context_contract_bytes=context_contract_bytes,
        )
    raise ValueError(f"No runtime factory for harness: {definition.id}")


__all__ = [
    "ALTERNATE_HARNESS_IDS",
    "HarnessDefinition",
    "PUBLIC_HARNESS_IDS",
    "alternate_harness_ids",
    "catalog_harness_options",
    "create_alternate_harness_runtime",
    "harness_definition",
    "is_public_harness_id",
    "require_harness_definition",
]
