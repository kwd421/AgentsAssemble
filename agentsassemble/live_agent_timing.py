from __future__ import annotations

import math


DEFAULT_LIVE_AGENT_POLL_INTERVAL = 0.25
MIN_LIVE_AGENT_IMMEDIATE_SLEEP = 0.01


def live_agent_poll_sleep_seconds(poll_interval: object) -> float:
    try:
        parsed = float(poll_interval)
    except (TypeError, ValueError):
        return MIN_LIVE_AGENT_IMMEDIATE_SLEEP
    if not math.isfinite(parsed) or parsed <= 0:
        return MIN_LIVE_AGENT_IMMEDIATE_SLEEP
    return parsed
