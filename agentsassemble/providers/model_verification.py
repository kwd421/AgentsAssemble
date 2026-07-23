"""Provider-reported model identity verification policy."""

from __future__ import annotations

import re


_CLAUDE_RELEASE_MODEL = re.compile(r"^claude-(?:haiku|sonnet|opus)-\d+-\d+$")
_CLAUDE_SNAPSHOT_SUFFIX = re.compile(r"^\d{8}$")
_CLAUDE_PROVIDER_KINDS = frozenset({"claude", "claude_code"})
_ANTIGRAVITY_PROVIDER_KINDS = frozenset({"antigravity", "antigravity_live_session"})


def model_verification_status(
    *,
    requested_model_id: str,
    observed_model_id: str,
    selection_kind: str,
    observation_policy: str,
    provider_kind: str = "",
) -> str:
    """Classify the provider-reported model without hiding the reported ID."""

    if not observed_model_id:
        return "pending" if observation_policy == "required" else "unavailable"
    if selection_kind == "alias":
        return "resolved_alias"
    if requested_model_id and observed_model_id == requested_model_id:
        return "verified"
    if _is_claude_snapshot_for_release(
        provider_kind=provider_kind,
        requested_model_id=requested_model_id,
        observed_model_id=observed_model_id,
    ):
        return "verified_provider_revision"
    if _is_antigravity_display_for_exact_model(
        provider_kind=provider_kind,
        requested_model_id=requested_model_id,
        observed_model_id=observed_model_id,
    ):
        return "verified_provider_display"
    return "mismatch"


def model_observation_matches(
    *,
    requested_model_id: str,
    observed_model_id: str,
    selection_kind: str,
    provider_kind: str = "",
) -> bool:
    if not requested_model_id or not observed_model_id:
        return False
    return model_verification_status(
        requested_model_id=requested_model_id,
        observed_model_id=observed_model_id,
        selection_kind=selection_kind,
        observation_policy="required",
        provider_kind=provider_kind,
    ) != "mismatch"


def _is_claude_snapshot_for_release(
    *,
    provider_kind: str,
    requested_model_id: str,
    observed_model_id: str,
) -> bool:
    if provider_kind not in _CLAUDE_PROVIDER_KINDS:
        return False
    if not _CLAUDE_RELEASE_MODEL.fullmatch(requested_model_id):
        return False
    prefix = f"{requested_model_id}-"
    return observed_model_id.startswith(prefix) and bool(
        _CLAUDE_SNAPSHOT_SUFFIX.fullmatch(observed_model_id.removeprefix(prefix))
    )


def _is_antigravity_display_for_exact_model(
    *,
    provider_kind: str,
    requested_model_id: str,
    observed_model_id: str,
) -> bool:
    if provider_kind not in _ANTIGRAVITY_PROVIDER_KINDS:
        return False
    return _model_identity_tokens(requested_model_id) == _model_identity_tokens(observed_model_id)


def _model_identity_tokens(value: str) -> tuple[str, ...]:
    return tuple(re.findall(r"[a-z0-9]+", value.casefold()))
