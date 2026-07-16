"""Compatibility exports for the resident Hermes CLI adapter."""

from agentsassemble.providers.hermes_resident import (
    HERMES_EMPTY_REPLY,
    HERMES_MISSING_SESSION_ID,
    HERMES_SUBPROCESS_NONZERO,
    HERMES_SUBPROCESS_TIMEOUT,
    HermesResidentCommandRunner,
    HermesResidentRuntimeError,
    HermesResidentValueError,
    clean_hermes_session_id,
    default_hermes_resident_command,
    hermes_command_check,
    hermes_error_category,
    hermes_provider_connection_check,
)


__all__ = [
    "HERMES_EMPTY_REPLY",
    "HERMES_MISSING_SESSION_ID",
    "HERMES_SUBPROCESS_NONZERO",
    "HERMES_SUBPROCESS_TIMEOUT",
    "HermesResidentCommandRunner",
    "HermesResidentRuntimeError",
    "HermesResidentValueError",
    "clean_hermes_session_id",
    "default_hermes_resident_command",
    "hermes_command_check",
    "hermes_error_category",
    "hermes_provider_connection_check",
]
