"""Execution for the explicit one-shot API provider CLI lane."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agentsassemble.persistence.local.identity.registry import (
    identity_store_for_output_root,
)
from agentsassemble.providers import api as room_api_provider
from agentsassemble.providers import catalog as provider_catalog


def run_api_call_command(args: argparse.Namespace) -> int:
    """Read a prompt on stdin, call a configured API model, and print its reply."""

    if getattr(args, "catalog", False):
        print(json.dumps(provider_catalog.catalog_payload(), ensure_ascii=False, indent=2))
        return 0

    prompt = sys.stdin.read()
    if not prompt.strip():
        print("error: empty prompt on stdin", file=sys.stderr)
        return 2

    store = None
    if args.output_root:
        try:
            store = identity_store_for_output_root(Path(args.output_root))
        except (OSError, ValueError):
            # Usage accounting is intentionally best-effort for this legacy one-shot lane.
            store = None

    try:
        text = room_api_provider.run_api_call(
            args.provider,
            args.model,
            prompt,
            store=store,
            user_id=args.user_id,
            participant_id=args.participant_id,
            meeting_id=args.meeting_id,
            system=args.system,
            key_source=args.key_source,
            timeout=args.timeout,
        )
    except room_api_provider.ApiProviderError as error:
        print(f"error[{error.category}]: {error}", file=sys.stderr)
        return 2
    print(text)
    return 0
