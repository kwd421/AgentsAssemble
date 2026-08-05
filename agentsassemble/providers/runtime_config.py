from __future__ import annotations

from dataclasses import dataclass

from agentsassemble.providers.remote_openai import remote_openai_profile
from agentsassemble.room.text import clean_room_text


class ProviderRuntimeConfigError(ValueError):
    def __init__(self, message: str, *, code: str = "provider_runtime_config_invalid") -> None:
        super().__init__(message)
        self.code = code


class BridgeConfigError(ValueError):
    def __init__(self, message: str, *, code: str = "bridge_config_invalid") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ProviderRuntimeProfile:
    provider_kind: str
    runtime_kind: str
    model: str
    reasoning_effort: str
    service_tier: str
    variant: str
    permission_mode: str
    transport: str
    max_output_tokens: int = 0
    context_contract_bytes: int = 0
    execution_harness: str = "builtin"

    @classmethod
    def parse_strict(cls, values: dict[str, object]) -> ProviderRuntimeProfile:
        return cls(
            provider_kind=_required_text(values, "provider_kind", limit=64),
            runtime_kind=_required_text(values, "runtime_kind", limit=64),
            model=_required_text(values, "model", limit=256),
            reasoning_effort=_required_text(
                values,
                "reasoning_effort",
                limit=32,
                allow_empty=True,
            ),
            service_tier=_required_text(values, "service_tier", limit=32, allow_empty=True),
            variant=_required_text(values, "variant", limit=64, allow_empty=True),
            execution_harness=(
                _required_text(values, "execution_harness", limit=32)
                if "execution_harness" in values
                else "builtin"
            ),
            permission_mode=_required_text(values, "permission_mode", limit=64),
            transport=_required_text(values, "transport", limit=64),
            max_output_tokens=(
                _required_int(values, "max_output_tokens", minimum=0)
                if "max_output_tokens" in values
                else 0
            ),
            context_contract_bytes=(
                _required_int(values, "context_contract_bytes", minimum=0)
                if "context_contract_bytes" in values
                else 0
            ),
        )

    def report_fields(self) -> dict[str, object]:
        return {
            "provider_kind": self.provider_kind,
            "runtime_kind": self.runtime_kind,
            "model": self.model,
            "reasoning_effort": self.reasoning_effort,
            "service_tier": self.service_tier,
            "variant": self.variant,
            "execution_harness": self.execution_harness,
            "permission_mode": self.permission_mode,
            "max_output_tokens": self.max_output_tokens,
            "context_contract_bytes": self.context_contract_bytes,
        }


@dataclass(frozen=True)
class ProviderRuntimeConfig:
    participant_id: str
    provider_kind: str
    runtime_kind: str
    command: tuple[str, ...]
    cwd: str
    model: str
    reasoning_effort: str
    service_tier: str
    variant: str
    permission_mode: str
    max_output_tokens: int
    context_contract_bytes: int
    transport: str
    quiet_seconds: float
    input_mode: str
    submit_newline: str
    submit_delay_seconds: float
    terminal_rows: int
    terminal_columns: int
    startup_quiet_seconds: float
    startup_timeout_seconds: float
    startup_accept_contains: str
    startup_accept_keys: str
    startup_ready_contains: str
    startup_input: str
    runtime_state_dir: str
    provider_endpoint: str
    provider_server_pid: int | None
    resume_required: bool = False
    execution_harness: str = "builtin"

    @property
    def profile(self) -> ProviderRuntimeProfile:
        return ProviderRuntimeProfile(
            provider_kind=self.provider_kind,
            runtime_kind=self.runtime_kind,
            model=self.model,
            reasoning_effort=self.reasoning_effort,
            service_tier=self.service_tier,
            variant=self.variant,
            execution_harness=self.execution_harness,
            permission_mode=self.permission_mode,
            max_output_tokens=self.max_output_tokens,
            context_contract_bytes=self.context_contract_bytes,
            transport=self.transport,
        )

    @classmethod
    def parse_strict(cls, values: dict[str, object]) -> ProviderRuntimeConfig:
        profile = ProviderRuntimeProfile.parse_strict(values)
        command_value = _required_value(values, "command")
        if not isinstance(command_value, list) or not command_value:
            raise ProviderRuntimeConfigError("Provider runtime command must be a non-empty list.")
        command = tuple(str(part) for part in command_value)
        if not command[0].strip():
            raise ProviderRuntimeConfigError("Provider runtime executable is required.")
        provider_endpoint = _required_text(values, "provider_endpoint", limit=1000, allow_empty=True)
        if (
            profile.provider_kind
            in {"opencode_server", "ollama_api", "lmstudio_api"}
            or remote_openai_profile(profile.provider_kind) is not None
        ) and not provider_endpoint:
            raise ProviderRuntimeConfigError(
                f"{profile.provider_kind} provider endpoint is required."
            )
        return cls(
            participant_id=_required_text(values, "participant_id", limit=128),
            provider_kind=profile.provider_kind,
            runtime_kind=profile.runtime_kind,
            command=command,
            cwd=_required_text(values, "cwd", limit=500),
            model=profile.model,
            reasoning_effort=profile.reasoning_effort,
            service_tier=profile.service_tier,
            variant=profile.variant,
            execution_harness=profile.execution_harness,
            permission_mode=profile.permission_mode,
            max_output_tokens=profile.max_output_tokens,
            context_contract_bytes=profile.context_contract_bytes,
            transport=profile.transport,
            quiet_seconds=_required_float(values, "quiet_seconds", minimum=0.001),
            input_mode=_required_text(values, "input_mode", limit=64),
            submit_newline=_required_raw_text(values, "submit_newline", limit=16),
            submit_delay_seconds=_required_float(values, "submit_delay_seconds", minimum=0.0),
            terminal_rows=_required_int(values, "terminal_rows", minimum=1),
            terminal_columns=_required_int(values, "terminal_columns", minimum=1),
            startup_quiet_seconds=_required_float(values, "startup_quiet_seconds", minimum=0.0),
            startup_timeout_seconds=_required_float(values, "startup_timeout_seconds", minimum=0.001),
            startup_accept_contains=_required_raw_text(
                values, "startup_accept_contains", limit=1000, allow_empty=True
            ),
            startup_accept_keys=_required_raw_text(
                values, "startup_accept_keys", limit=1000, allow_empty=True
            ),
            startup_ready_contains=_required_raw_text(
                values, "startup_ready_contains", limit=1000, allow_empty=True
            ),
            startup_input=_required_raw_text(values, "startup_input", limit=4000, allow_empty=True),
            runtime_state_dir=_required_text(values, "runtime_state_dir", limit=1000),
            provider_endpoint=provider_endpoint,
            provider_server_pid=_required_optional_int(values, "provider_server_pid"),
            resume_required=(
                _required_bool(values, "resume_required")
                if "resume_required" in values
                else False
            ),
        )


@dataclass(frozen=True)
class CanonicalBridgeLaunchConfig:
    room_id: str
    session_id: str
    turn_timeout_seconds: float
    runtime_profile_key: str
    credential_stdin: bool
    runtime: ProviderRuntimeConfig

    @classmethod
    def parse_strict(cls, values: dict[str, object]) -> CanonicalBridgeLaunchConfig:
        try:
            runtime = ProviderRuntimeConfig.parse_strict(values)
        except ProviderRuntimeConfigError as error:
            raise BridgeConfigError(str(error)) from error
        return cls(
            room_id=_bridge_required_text(values, "room_id", limit=128),
            session_id=_bridge_required_text(values, "session_id", limit=128),
            turn_timeout_seconds=_bridge_required_float(
                values,
                "turn_timeout_seconds",
                minimum=0.001,
            ),
            runtime_profile_key=_bridge_required_text(values, "runtime_profile_key", limit=256),
            credential_stdin=_bridge_required_bool(values, "credential_stdin"),
            runtime=runtime,
        )


def _required_value(values: dict[str, object], key: str) -> object:
    if key not in values:
        raise ProviderRuntimeConfigError(f"Provider runtime config is missing {key}.")
    return values[key]


def _required_bool(values: dict[str, object], key: str) -> bool:
    value = _required_value(values, key)
    if not isinstance(value, bool):
        raise ProviderRuntimeConfigError(f"Provider runtime config {key} must be a boolean.")
    return value


def _required_text(
    values: dict[str, object],
    key: str,
    *,
    limit: int,
    allow_empty: bool = False,
) -> str:
    value = clean_room_text(_required_value(values, key), limit=limit)
    if not value and not allow_empty:
        raise ProviderRuntimeConfigError(f"Provider runtime config {key} is required.")
    return value


def _required_raw_text(
    values: dict[str, object],
    key: str,
    *,
    limit: int,
    allow_empty: bool = False,
) -> str:
    value = _required_value(values, key)
    if not isinstance(value, str) or "\x00" in value or len(value) > limit:
        raise ProviderRuntimeConfigError(
            f"Provider runtime config {key} must be valid text up to {limit} characters."
        )
    if not value and not allow_empty:
        raise ProviderRuntimeConfigError(f"Provider runtime config {key} is required.")
    return value


def _required_float(values: dict[str, object], key: str, *, minimum: float) -> float:
    value = _required_value(values, key)
    if isinstance(value, bool):
        raise ProviderRuntimeConfigError(f"Provider runtime config {key} must be a number.")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as error:
        raise ProviderRuntimeConfigError(f"Provider runtime config {key} must be a number.") from error
    if parsed < minimum:
        raise ProviderRuntimeConfigError(f"Provider runtime config {key} must be at least {minimum}.")
    return parsed


def _required_int(values: dict[str, object], key: str, *, minimum: int) -> int:
    value = _required_value(values, key)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ProviderRuntimeConfigError(
            f"Provider runtime config {key} must be an integer of at least {minimum}."
        )
    return value


def _required_optional_int(values: dict[str, object], key: str) -> int | None:
    value = _required_value(values, key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ProviderRuntimeConfigError(
            f"Provider runtime config {key} must be a positive integer or null."
        )
    return value


def _bridge_required_value(values: dict[str, object], key: str) -> object:
    if key not in values:
        raise BridgeConfigError(f"Agent Bridge config is missing {key}.")
    return values[key]


def _bridge_required_text(values: dict[str, object], key: str, *, limit: int) -> str:
    value = clean_room_text(_bridge_required_value(values, key), limit=limit)
    if not value:
        raise BridgeConfigError(f"Agent Bridge config {key} is required.")
    return value


def _bridge_required_float(values: dict[str, object], key: str, *, minimum: float) -> float:
    value = _bridge_required_value(values, key)
    if isinstance(value, bool):
        raise BridgeConfigError(f"Agent Bridge config {key} must be a number.")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as error:
        raise BridgeConfigError(f"Agent Bridge config {key} must be a number.") from error
    if parsed < minimum:
        raise BridgeConfigError(f"Agent Bridge config {key} must be at least {minimum}.")
    return parsed


def _bridge_required_bool(values: dict[str, object], key: str) -> bool:
    value = _bridge_required_value(values, key)
    if not isinstance(value, bool):
        raise BridgeConfigError(f"Agent Bridge config {key} must be a boolean.")
    return value
