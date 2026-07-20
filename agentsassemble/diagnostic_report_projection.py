"""Compatibility exports for public-safe diagnostic projection."""

from agentsassemble.diagnostics.report_projection import (
    looks_sensitive_operator_diagnostic_text,
    safe_diagnostic_report_payload,
)

__all__ = [
    "looks_sensitive_operator_diagnostic_text",
    "safe_diagnostic_report_payload",
]
