"""Heartbeat payload normalization for retained live-agent commands."""
from __future__ import annotations

import argparse
import re

from agentsassemble.legacy.live_agent.state import (
    PRESENCE_ATTENTION_REDACTED,
    SAFE_PRESENCE_ATTENTION_CODES,
)
from agentsassemble.legacy.meeting.core.events import clean_lobby_text


def heartbeat_payload(args: argparse.Namespace) -> dict[str, object]:
    payload = {"status": args.status}
    optional_fields = {
        "last_error": getattr(args, "last_error", None),
        "last_attention": getattr(args, "last_attention", None),
        "last_reply_at": getattr(args, "last_reply_at", None),
        "last_observed_event_id": getattr(args, "last_observed_event_id", None),
        "last_observed_live_event_id": getattr(args, "last_observed_live_event_id", None),
        "last_observed_dm_event_id": getattr(args, "last_observed_dm_event_id", None),
    }
    for key, value in optional_fields.items():
        if value is None or is_unreplaced_template_placeholder(value):
            continue
        if key == "last_attention":
            attention = clean_heartbeat_attention(value)
            if attention:
                payload[key] = attention
            continue
        payload[key] = value
    return payload


def clean_heartbeat_attention(value: object) -> str:
    text = clean_lobby_text(value, limit=128)
    if not text:
        return ""
    if text in SAFE_PRESENCE_ATTENTION_CODES:
        return text
    return PRESENCE_ATTENTION_REDACTED


def is_unreplaced_template_placeholder(value: object) -> bool:
    if not isinstance(value, str):
        return False
    return bool(re.fullmatch(r"\{[A-Za-z0-9_]+\}", value.strip()))
