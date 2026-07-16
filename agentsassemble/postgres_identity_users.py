"""Compatibility exports for PostgreSQL identity user persistence.

Replacement: ``agentsassemble.persistence.postgres.identity.users``.
Removal gate: all direct imports use the replacement path for one compatibility
window.
"""
from agentsassemble.persistence.postgres.identity.users import (
    claim_local_operator_credential,
    consume_operator_pairing,
    count_users,
    create_operator_pairing,
    get_user,
    operator_pairing_for_fingerprint,
    operator_user_id,
    pairing_from_row,
    resolve_credential_user,
    revoke_operator_pairing,
    set_user_operator,
    update_operator_pairing_redemption,
    user_for_credential,
    user_for_participant,
    user_from_row,
)

__all__ = [
    "claim_local_operator_credential",
    "consume_operator_pairing",
    "count_users",
    "create_operator_pairing",
    "get_user",
    "operator_pairing_for_fingerprint",
    "operator_user_id",
    "pairing_from_row",
    "resolve_credential_user",
    "revoke_operator_pairing",
    "set_user_operator",
    "update_operator_pairing_redemption",
    "user_for_credential",
    "user_for_participant",
    "user_from_row",
]
