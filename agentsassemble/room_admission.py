"""Compatibility export for side-effect-free room admission preflight.

Replacement: ``agentsassemble.admission.preflight``.
Removal gate: all direct imports and monkeypatch targets use the replacement
path for one compatibility window.
"""
from agentsassemble.admission.preflight import RoomAdmissionService


__all__ = ["RoomAdmissionService"]
