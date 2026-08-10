from __future__ import annotations

import copy
from collections.abc import Callable

from agentsassemble.providers.harness_registry import catalog_harness_options


ExecutableResolver = Callable[[str], str | None]


def add_native_harness_controls(
    payload: dict[str, object],
    *,
    provider_id: str,
    resolver: ExecutableResolver,
) -> dict[str, object]:
    """Describe installed native harnesses without probing or starting them."""
    del provider_id
    result = copy.deepcopy(payload)
    fixed_values = dict(result.get("fixed_values") or {})
    controls = list(result.get("controls") or [])
    options = catalog_harness_options(resolver=resolver)
    fixed_values.pop("execution_harness", None)
    controls = [
        control
        for control in controls
        if not (
            isinstance(control, dict)
            and control.get("key") == "execution_harness"
        )
    ]
    if len(options) == 1:
        fixed_values["execution_harness"] = "builtin"
    else:
        insertion = 1 if controls and controls[0].get("key") == "model" else 0
        controls.insert(
            insertion,
            {
                "key": "execution_harness",
                "label": "작업 하네스",
                "kind": "select",
                "options": options,
                "default_value": "builtin",
            },
        )
    result["fixed_values"] = fixed_values
    result["controls"] = controls
    return result


def add_native_harness_catalog_controls(
    providers: list[dict[str, object]],
    *,
    resolver: ExecutableResolver,
) -> list[dict[str, object]]:
    """Decorate only API/Local providers with installed harness choices."""
    return [
        add_native_harness_controls(
            provider,
            provider_id=str(provider.get("id") or ""),
            resolver=resolver,
        )
        if provider.get("runtime_kind") == "api"
        else provider
        for provider in providers
    ]


__all__ = [
    "add_native_harness_catalog_controls",
    "add_native_harness_controls",
]
