"""Declarative remote OpenAI-compatible providers and their room runtime."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from urllib.request import Request, urlopen

from agentsassemble.providers.openai_compatible import (
    OpenAICompatibleApiRuntime,
    UrlOpen,
)
from agentsassemble.providers.room_portal import RoomPortal
from agentsassemble.room.text import clean_room_text


@dataclass(frozen=True)
class RemoteOpenAIProfile:
    provider_id: str
    display_name: str
    provider_kind: str
    base_url: str
    default_model: str
    credential_env: str
    static_models: tuple[tuple[str, str], ...] = ()
    reasoning_efforts: tuple[str, ...] = ()
    default_reasoning_effort: str = ""
    variants: tuple[tuple[str, str], ...] = ()
    default_variant: str = ""
    discovery_path: str = ""
    request_headers: tuple[tuple[str, str], ...] = ()
    max_output_tokens: int = 0


REMOTE_OPENAI_PROFILES = (
    RemoteOpenAIProfile(
        provider_id="deepseek",
        display_name="DeepSeek",
        provider_kind="deepseek_api",
        base_url="https://api.deepseek.com",
        default_model="deepseek-v4-flash",
        credential_env="DEEPSEEK_API_KEY",
        static_models=(
            ("deepseek-v4-flash", "DeepSeek V4 Flash"),
            ("deepseek-v4-pro", "DeepSeek V4 Pro"),
        ),
        reasoning_efforts=("high", "max"),
        default_reasoning_effort="high",
        variants=(("thinking", "사용"), ("non_thinking", "사용 안 함")),
        default_variant="thinking",
        max_output_tokens=4096,
    ),
    RemoteOpenAIProfile(
        provider_id="cerebras",
        display_name="Cerebras",
        provider_kind="cerebras_api",
        base_url="https://api.cerebras.ai/v1",
        default_model="gpt-oss-120b",
        credential_env="CEREBRAS_API_KEY",
        static_models=(("gpt-oss-120b", "GPT OSS 120B"),),
        reasoning_efforts=("low", "medium", "high"),
        default_reasoning_effort="low",
        request_headers=(("X-Cerebras-Version-Patch", "2"),),
        max_output_tokens=4096,
    ),
    RemoteOpenAIProfile(
        provider_id="openrouter",
        display_name="OpenRouter",
        provider_kind="openrouter_api",
        base_url="https://openrouter.ai/api/v1",
        default_model="openai/gpt-4.1-mini",
        credential_env="OPENROUTER_API_KEY",
        discovery_path="/models?supported_parameters=tools&sort=most-popular",
        request_headers=(
            ("HTTP-Referer", "http://127.0.0.1:8765/"),
            ("X-Title", "AgentsAssemble"),
        ),
        max_output_tokens=4096,
    ),
    RemoteOpenAIProfile(
        provider_id="vercel",
        display_name="Vercel AI Gateway",
        provider_kind="vercel_ai_gateway",
        base_url="https://ai-gateway.vercel.sh/v1",
        default_model="openai/gpt-5.4-mini",
        credential_env="AI_GATEWAY_API_KEY",
        discovery_path="/models",
        max_output_tokens=4096,
    ),
)

_BY_ID = {profile.provider_id: profile for profile in REMOTE_OPENAI_PROFILES}
_BY_KIND = {profile.provider_kind: profile for profile in REMOTE_OPENAI_PROFILES}
REMOTE_OUTPUT_TOKEN_OPTIONS = (1024, 2048, 4096, 8192, 16384)


def remote_openai_profiles() -> tuple[RemoteOpenAIProfile, ...]:
    return REMOTE_OPENAI_PROFILES


def remote_openai_profile(value: object) -> RemoteOpenAIProfile | None:
    key = str(value or "").strip().casefold()
    return _BY_ID.get(key) or _BY_KIND.get(key)


def remote_openai_endpoint(provider_kind: object) -> str:
    profile = remote_openai_profile(provider_kind)
    return profile.base_url if profile is not None else ""


def remote_openai_credential_ids() -> tuple[str, ...]:
    return tuple(profile.provider_id for profile in REMOTE_OPENAI_PROFILES)


def discover_remote_openai_models(
    profile: RemoteOpenAIProfile,
    *,
    api_key: str = "",
    timeout_seconds: float = 8.0,
    opener: UrlOpen = urlopen,
) -> list[dict[str, object]]:
    """Read a gateway's public model catalog and retain room-tool-capable text models."""

    if not profile.discovery_path:
        return []
    headers = {
        "Accept": "application/json",
        "User-Agent": "AgentsAssemble/1.0",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = Request(
        f"{profile.base_url}{profile.discovery_path}",
        headers=headers,
        method="GET",
    )
    with opener(request, timeout=max(1.0, float(timeout_seconds))) as response:
        payload = json.load(response)
    entries = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        raise ValueError(f"{profile.display_name} returned an invalid model catalog.")
    return [
        option
        for entry in entries
        if isinstance(entry, dict)
        and (option := _gateway_model_option(entry)) is not None
    ]


def remote_openai_catalog_payload(
    profile: RemoteOpenAIProfile,
    *,
    discovered_models: list[dict[str, object]] | None = None,
    discovery_error: str = "",
    discovery_error_code: str = "",
) -> dict[str, object]:
    options = (
        list(discovered_models or [])
        if profile.discovery_path
        else [
            # A profile declares one reasoning-effort list for every model it
            # serves, so the relation is global. Without the scope the catalog
            # validator rejects any effort the user picks -- including the
            # profile's own default -- and the provider cannot be created at
            # all. The discovery path already sets this in _gateway_model_option.
            _model_option(model_id, label, relation_scope="global")
            for model_id, label in profile.static_models
        ]
    )
    default_model = (
        profile.default_model
        if any(option["value"] == profile.default_model for option in options)
        else str(options[0]["value"]) if options else profile.default_model
    )
    controls: list[dict[str, object]] = []
    if options:
        controls.append(
            _control("model", "모델", options, default_model, kind="combobox")
        )
        if profile.reasoning_efforts:
            controls.append(
                _control(
                    "reasoning_effort",
                    "추론 강도",
                    [_option(value) for value in profile.reasoning_efforts],
                    profile.default_reasoning_effort,
                )
            )
        if profile.variants:
            controls.append(
                _control(
                    "variant",
                    "Thinking",
                    [_option(value, label) for value, label in profile.variants],
                    profile.default_variant,
                )
            )
        if profile.max_output_tokens:
            controls.append(
                _control(
                    "max_output_tokens",
                    "최대 응답 길이",
                    [
                        _option(str(value), f"{value:,} 토큰")
                        for value in REMOTE_OUTPUT_TOKEN_OPTIONS
                    ],
                    str(profile.max_output_tokens),
                )
            )
    ready = bool(options)
    return {
        "id": profile.provider_id,
        "display_name": profile.display_name,
        "provider_kind": profile.provider_kind,
        "runtime_kind": "api",
        "catalog_group": "api",
        "connection_kind": "native_cli_bridge",
        "executable": "",
        "default_model": default_model,
        "interactive": True,
        "available": True,
        "startable": ready,
        "discovery_status": "ready" if ready else "failed",
        "discovery_error": "" if ready else discovery_error,
        "discovery_error_code": "" if ready else discovery_error_code,
        "catalog_source": "discovered" if profile.discovery_path else "static_manifest",
        "fixed_values": {"permission_mode": "meeting_read_only"},
        "controls": controls,
        "workspace_required": False,
    }


class RemoteOpenAICompatibleRuntime(OpenAICompatibleApiRuntime):
    """One room runtime shared by fixed HTTPS OpenAI-compatible profiles."""

    def __init__(
        self,
        agent_id: str,
        *,
        profile: RemoteOpenAIProfile,
        api_key: str,
        model: str,
        reasoning_effort: str = "",
        variant: str = "",
        max_output_tokens: int = 0,
        base_url: str = "",
        opener: UrlOpen = urlopen,
        room_portal: RoomPortal | None = None,
    ) -> None:
        request_payload: dict[str, object] = {}
        include_reasoning = False
        output_limit = max_output_tokens or profile.max_output_tokens
        if output_limit not in REMOTE_OUTPUT_TOKEN_OPTIONS:
            raise ValueError(f"Unsupported max_output_tokens: {output_limit}")
        if output_limit > 0:
            request_payload["max_tokens"] = output_limit
        if profile.provider_id == "deepseek":
            request_payload["thinking"] = {
                "type": "disabled" if variant == "non_thinking" else "enabled"
            }
            include_reasoning = True
        super().__init__(
            agent_id,
            api_key=api_key,
            provider_name=profile.display_name,
            model=model,
            allowed_models=frozenset({model}),
            reasoning_effort=reasoning_effort,
            allowed_reasoning_efforts=frozenset(
                profile.reasoning_efforts or ("",)
            ),
            base_url=base_url or profile.base_url,
            message_source=f"{profile.provider_id}_sse",
            variant=variant,
            include_reasoning_in_messages=include_reasoning,
            request_payload=request_payload,
            request_headers=dict(profile.request_headers),
            opener=opener,
            room_portal=room_portal,
        )


def _gateway_model_option(entry: dict[str, object]) -> dict[str, object] | None:
    model_id = clean_room_text(entry.get("id"), limit=128)
    supported = {
        str(value).strip()
        for value in list(entry.get("supported_parameters") or [])
    }
    architecture = entry.get("architecture")
    input_modalities = (
        set(architecture.get("input_modalities") or [])
        if isinstance(architecture, dict)
        else {"text"}
    )
    if not model_id or "tools" not in supported or "text" not in input_modalities:
        return None
    pricing = entry.get("pricing")
    metadata: dict[str, object] = {
        "selection_kind": "exact",
        "relation_scope": "global",
        "family": _model_family(model_id),
        "compatibility_evidence": "provider_catalog",
    }
    context = entry.get("context_length") or entry.get("context_window")
    if isinstance(context, int) and context > 0:
        metadata["description"] = f"Context {context:,}"
    if _is_free_pricing(pricing):
        metadata["pricing"] = "free"
    return _model_option(
        model_id,
        clean_room_text(entry.get("name"), limit=160) or model_id,
        **metadata,
    )


def _is_free_pricing(pricing: object) -> bool:
    if not isinstance(pricing, dict):
        return False
    fields = (
        (pricing.get("prompt"), pricing.get("completion"))
        if "prompt" in pricing or "completion" in pricing
        else (pricing.get("input"), pricing.get("output"))
    )
    if any(value is None for value in fields):
        return False
    try:
        return all(Decimal(str(value)) == 0 for value in fields)
    except InvalidOperation:
        return False


def _model_family(model_id: str) -> str:
    owner, separator, _model = model_id.partition("/")
    return owner.replace("-", " ").title() if separator else ""


def _option(value: str, label: str = "", **metadata: object) -> dict[str, object]:
    return {
        "value": value,
        "label": label or value,
        "metadata": {"selection_kind": "exact", **metadata},
    }


def _model_option(value: str, label: str = "", **metadata: object) -> dict[str, object]:
    return _option(value, label, **metadata)


def _control(
    key: str,
    label: str,
    options: list[dict[str, object]],
    default_value: str,
    *,
    kind: str = "select",
) -> dict[str, object]:
    return {
        "key": key,
        "label": label,
        "kind": kind,
        "options": options,
        "default_value": default_value,
    }
