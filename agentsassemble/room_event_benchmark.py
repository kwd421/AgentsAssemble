"""Compatibility exports for agentsassemble.legacy.diagnostics.room_event_benchmark."""

from agentsassemble.legacy.diagnostics.room_event_benchmark import (
    BENCHMARK_SCHEMA_VERSION,
    RoomEventBenchmarkOptions,
    RoomEventHttpHandlerFactory,
    SCHEDULER_ANCHOR_IMPROVEMENT_FLOOR,
    SCHEDULER_IMBALANCE_MARGIN,
    SCHEDULER_LATENCY_CALLS,
    SCHEDULER_LATENCY_EVENT_COUNT,
    SCHEDULER_P99_LATENCY_CEILING_MS,
    SSE_SAMPLE_TIMEOUT_SECONDS,
    flow_scheduler_comparison,
    flow_speaking_distribution,
    run_room_event_benchmark,
)

__all__ = [
    'BENCHMARK_SCHEMA_VERSION',
    'RoomEventBenchmarkOptions',
    'RoomEventHttpHandlerFactory',
    'SCHEDULER_ANCHOR_IMPROVEMENT_FLOOR',
    'SCHEDULER_IMBALANCE_MARGIN',
    'SCHEDULER_LATENCY_CALLS',
    'SCHEDULER_LATENCY_EVENT_COUNT',
    'SCHEDULER_P99_LATENCY_CEILING_MS',
    'SSE_SAMPLE_TIMEOUT_SECONDS',
    'flow_scheduler_comparison',
    'flow_speaking_distribution',
    'run_room_event_benchmark',
]
