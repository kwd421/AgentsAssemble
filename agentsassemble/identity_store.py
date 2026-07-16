"""Compatibility exports for identity contracts and local SQLite persistence."""
from agentsassemble.identity.repository import (
    IdentityBackend,
    LOCAL_OPERATOR_PARTICIPANT_ID,
    LOCAL_OPERATOR_USER_ID,
    OPERATOR_PAIRING_REDEMPTION_STATUSES,
    PARTICIPANT_TYPES,
    device_auth_key,
    normalize_participant_type,
)
from agentsassemble.persistence.local.identity.migration import (
    migrate_legacy_members_json,
    migrate_legacy_users_json,
)
from agentsassemble.persistence.local.identity.registry import (
    default_identity_db_path,
    identity_store_at,
    identity_store_for_output_root,
    make_identity_backend,
    register_identity_backend,
    register_identity_store_for_output_root,
    reset_identity_store_registry,
    unregister_identity_store_for_output_root,
)
from agentsassemble.persistence.local.identity.repository import (
    IDENTITY_DB_FILENAME,
    IdentityStore,
    SqliteIdentityStore,
)

__all__ = [
    "IDENTITY_DB_FILENAME",
    "IdentityBackend",
    "IdentityStore",
    "LOCAL_OPERATOR_PARTICIPANT_ID",
    "LOCAL_OPERATOR_USER_ID",
    "OPERATOR_PAIRING_REDEMPTION_STATUSES",
    "PARTICIPANT_TYPES",
    "SqliteIdentityStore",
    "default_identity_db_path",
    "device_auth_key",
    "identity_store_at",
    "identity_store_for_output_root",
    "make_identity_backend",
    "migrate_legacy_members_json",
    "migrate_legacy_users_json",
    "normalize_participant_type",
    "register_identity_backend",
    "register_identity_store_for_output_root",
    "reset_identity_store_registry",
    "unregister_identity_store_for_output_root",
]
