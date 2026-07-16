"""Compatibility exports for the resident Grok CLI adapter."""

from agentsassemble.providers.grok_resident import (
    GROK_AUTH_REQUIRED,
    GROK_EMPTY_TEXT,
    GROK_JSON_PARSE_FAILURE,
    GROK_LOGIN_REQUIRED_MESSAGE,
    GROK_MISSING_SESSION_ID,
    GROK_SUBPROCESS_NONZERO,
    GROK_SUBPROCESS_TIMEOUT,
    GrokResidentCommandRunner,
    GrokResidentRuntimeError,
    GrokResidentValueError,
    clean_grok_session_id,
    default_grok_resident_command,
    grok_auth_check,
    grok_command_check,
    grok_error_category,
    grok_login_required_message,
    grok_provider_connection_check,
    parse_grok_stream_line,
)


__all__ = [
    "GROK_AUTH_REQUIRED",
    "GROK_EMPTY_TEXT",
    "GROK_JSON_PARSE_FAILURE",
    "GROK_LOGIN_REQUIRED_MESSAGE",
    "GROK_MISSING_SESSION_ID",
    "GROK_SUBPROCESS_NONZERO",
    "GROK_SUBPROCESS_TIMEOUT",
    "GrokResidentCommandRunner",
    "GrokResidentRuntimeError",
    "GrokResidentValueError",
    "clean_grok_session_id",
    "default_grok_resident_command",
    "grok_auth_check",
    "grok_command_check",
    "grok_error_category",
    "grok_login_required_message",
    "grok_provider_connection_check",
    "parse_grok_stream_line",
]
