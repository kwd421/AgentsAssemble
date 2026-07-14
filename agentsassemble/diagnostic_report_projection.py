"""Public-safe projection for operator diagnostic reports."""

from __future__ import annotations

from agentsassemble.legacy_live_agent_process_control import looks_sensitive_process_control_error


def safe_diagnostic_report_payload(report: dict[str, object]) -> dict[str, object]:
    safe = dict(report)
    has_failed_config_load = _diagnostic_report_has_failed_config_load(safe)
    if has_failed_config_load or _diagnostic_report_exposes_sensitive_config_path(safe):
        safe["config_path"] = "[redacted]"
    checks = safe.get("checks")
    if isinstance(checks, list):
        safe["checks"] = [
            _safe_diagnostic_check_payload(check, redact_config_load=has_failed_config_load)
            for check in checks
        ]
    return safe


def looks_sensitive_operator_diagnostic_text(message: str) -> bool:
    return looks_sensitive_process_control_error(message)


def _diagnostic_report_has_failed_config_load(report: dict[str, object]) -> bool:
    checks = report.get("checks")
    if not isinstance(checks, list):
        return False
    return any(
        isinstance(check, dict) and check.get("id") == "config_load" and check.get("status") == "failed"
        for check in checks
    )


def _diagnostic_report_exposes_sensitive_config_path(report: dict[str, object]) -> bool:
    if report.get("status") != "failed":
        return False
    config_path = str(report.get("config_path") or "")
    return bool(config_path and looks_sensitive_operator_diagnostic_text(config_path))


def _safe_diagnostic_check_payload(check: object, *, redact_config_load: bool) -> object:
    if not isinstance(check, dict):
        return check
    safe = dict(check)
    message = str(safe.get("message") or "")
    if (
        redact_config_load
        and safe.get("id") == "config_load"
        and safe.get("status") == "failed"
    ) or looks_sensitive_operator_diagnostic_text(message):
        safe["message"] = "Config load failed: details redacted."
    return safe
