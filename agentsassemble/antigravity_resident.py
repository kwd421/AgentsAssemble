"""Compatibility exports for the resident Antigravity CLI adapter."""

from agentsassemble.providers.antigravity_resident import (
    ANTIGRAVITY_AUTH_REQUIRED,
    ANTIGRAVITY_BACKEND_ERROR,
    ANTIGRAVITY_EMPTY_REPLY,
    ANTIGRAVITY_LOGIN_REQUIRED_MESSAGE,
    ANTIGRAVITY_MISSING_CONVERSATION_ID,
    ANTIGRAVITY_SUBPROCESS_NONZERO,
    ANTIGRAVITY_SUBPROCESS_TIMEOUT,
    AntigravityResidentCommandRunner,
    AntigravityResidentRuntimeError,
    AntigravityResidentValueError,
    antigravity_auth_check,
    antigravity_command_check,
    antigravity_error_category,
    antigravity_provider_connection_check,
    clean_antigravity_conversation_id,
    default_antigravity_resident_command,
)


__all__ = [
    "ANTIGRAVITY_AUTH_REQUIRED",
    "ANTIGRAVITY_BACKEND_ERROR",
    "ANTIGRAVITY_EMPTY_REPLY",
    "ANTIGRAVITY_LOGIN_REQUIRED_MESSAGE",
    "ANTIGRAVITY_MISSING_CONVERSATION_ID",
    "ANTIGRAVITY_SUBPROCESS_NONZERO",
    "ANTIGRAVITY_SUBPROCESS_TIMEOUT",
    "AntigravityResidentCommandRunner",
    "AntigravityResidentRuntimeError",
    "AntigravityResidentValueError",
    "antigravity_auth_check",
    "antigravity_command_check",
    "antigravity_error_category",
    "antigravity_provider_connection_check",
    "clean_antigravity_conversation_id",
    "default_antigravity_resident_command",
]
