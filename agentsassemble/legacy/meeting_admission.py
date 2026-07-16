"""Legacy meeting-mode admission decisions."""
from __future__ import annotations

from typing import Any

from agentsassemble.models import AgentBinding, PermissionProfile, ProviderConfig


MEETING_UNSAFE_PERMISSIONS = frozenset(
    ("filesystem_write", "git_write", "push", "secrets", "implementation")
)


def build_admission_decisions(
    incoming_agents: list[dict[str, Any]],
    agent_bindings: list[AgentBinding],
    roles: list[Any],
    providers: dict[str, ProviderConfig],
    permissions: dict[str, PermissionProfile],
) -> list[dict[str, Any]]:
    role_ids = {role.id for role in roles}
    bindings_by_agent_id = {binding.agent_id: binding for binding in agent_bindings}
    decisions = []
    for index, request in enumerate(incoming_agents):
        decisions.append(
            _decide_request(
                request=request,
                index=index,
                role_ids=role_ids,
                bindings_by_agent_id=bindings_by_agent_id,
                providers=providers,
                permissions=permissions,
            )
        )
    return decisions


def _decide_request(
    *,
    request: dict[str, Any],
    index: int,
    role_ids: set[str],
    bindings_by_agent_id: dict[str, AgentBinding],
    providers: dict[str, ProviderConfig],
    permissions: dict[str, PermissionProfile],
) -> dict[str, Any]:
    name = str(request.get("name") or f"incoming-agent-{index + 1}")
    requested_role = request.get("requested_role")
    requested_provider = request.get("provider")
    approved_binding_agent_id = request.get("approved_binding_agent_id")
    reasons = _request_reasons(request, role_ids)
    binding = bindings_by_agent_id.get(str(approved_binding_agent_id)) if approved_binding_agent_id else None
    if binding is not None:
        reasons.extend(_binding_reasons(binding, providers, permissions))
    status = _admission_status(binding, reasons)
    return {
        "name": name,
        "requested_role": requested_role,
        "requested_provider": requested_provider,
        "approved_binding_agent_id": approved_binding_agent_id,
        "status": status,
        "execution": "bound_to_meeting_role" if status == "approved" else "not_executed",
        "effective_role_id": binding.role_id if status == "approved" and binding else None,
        "effective_provider_id": binding.provider_id if status == "approved" and binding else None,
        "permission_profile_id": binding.permission_profile_id if status == "approved" and binding else None,
        "reasons": reasons,
    }


def _request_reasons(request: dict[str, Any], role_ids: set[str]) -> list[str]:
    reasons = []
    requested_role = request.get("requested_role")
    if requested_role and requested_role not in role_ids:
        reasons.append("unknown_requested_role")
    requested_permissions = request.get("requested_permissions")
    if isinstance(requested_permissions, dict):
        unsafe = sorted(
            name for name in MEETING_UNSAFE_PERMISSIONS
            if requested_permissions.get(name)
        )
        if unsafe:
            reasons.append("requested_permissions_exceed_meeting_mode")
    return reasons


def _binding_reasons(
    binding: AgentBinding,
    providers: dict[str, ProviderConfig],
    permissions: dict[str, PermissionProfile],
) -> list[str]:
    reasons = []
    if binding.provider_id not in providers:
        reasons.append("approved_provider_not_configured")
    permission = permissions.get(binding.permission_profile_id)
    if permission is None:
        reasons.append("approved_permission_profile_not_configured")
    elif (
        permission.filesystem_write
        or permission.git_write
        or permission.push
        or permission.secrets
        or permission.implementation
    ):
        reasons.append("approved_permissions_exceed_meeting_mode")
    return reasons


def _admission_status(binding: AgentBinding | None, reasons: list[str]) -> str:
    if any(reason.startswith("unknown_") for reason in reasons):
        return "rejected"
    if binding is None:
        return "pending_host_approval"
    if reasons:
        return "rejected"
    return "approved"
