"""Compatibility exports for admission workflow maintenance models."""
from agentsassemble.admission.maintenance import (
    TERMINAL_ADMISSION_WORKFLOW_STATUSES,
    AdmissionWorkflowSelection,
    PurgeReport,
    build_purge_report,
)

__all__ = [
    "AdmissionWorkflowSelection",
    "PurgeReport",
    "TERMINAL_ADMISSION_WORKFLOW_STATUSES",
    "build_purge_report",
]
