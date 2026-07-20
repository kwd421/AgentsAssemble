"""Compatibility exports for agentsassemble.diagnostics.local_resources."""

from agentsassemble.diagnostics.local_resources import (
    DEFAULT_RESOURCE_CACHE_SECONDS,
    DEFAULT_RESOURCE_PROCESS_LIMIT,
    HIGH_LOAD_PER_CPU,
    HIGH_PROCESS_CPU_PCT,
    LocalResourceMonitor,
    REDACTED_PROCESS_NAME,
    RESOURCE_PROCESS_ALLOWLIST,
    RESOURCE_PROCESS_ROLES,
    SAFE_PROCESS_NAME_PATTERN,
    SENSITIVE_PROCESS_NAME_MARKERS,
    UUIDISH_TOKEN_PATTERN,
    cached_local_resource_snapshot,
    collect_local_resource_snapshot,
)

__all__ = [
    'DEFAULT_RESOURCE_CACHE_SECONDS',
    'DEFAULT_RESOURCE_PROCESS_LIMIT',
    'HIGH_LOAD_PER_CPU',
    'HIGH_PROCESS_CPU_PCT',
    'LocalResourceMonitor',
    'REDACTED_PROCESS_NAME',
    'RESOURCE_PROCESS_ALLOWLIST',
    'RESOURCE_PROCESS_ROLES',
    'SAFE_PROCESS_NAME_PATTERN',
    'SENSITIVE_PROCESS_NAME_MARKERS',
    'UUIDISH_TOKEN_PATTERN',
    'cached_local_resource_snapshot',
    'collect_local_resource_snapshot',
]
