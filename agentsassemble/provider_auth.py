"""Compatibility exports for provider authentication guidance."""

from agentsassemble.providers.auth import (
    provider_auth_error_message,
    provider_login_required_message,
)


__all__ = ["provider_auth_error_message", "provider_login_required_message"]
