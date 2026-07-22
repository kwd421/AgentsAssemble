"""Execution for retained meeting CLI commands."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agentsassemble.legacy.meeting.support.lobby_promotion import (
    promote_lobby_events_to_official,
)


def run_lobby_command(args: argparse.Namespace) -> int:
    if args.lobby_command == "promote":
        try:
            payload = promote_lobby_events_to_official(
                Path(args.output_root),
                args.meeting_id,
                list(args.lobby_event_ids),
                reason=args.reason,
            )
        except ValueError as error:
            print(str(error), file=sys.stderr)
            return 2
        if args.as_json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(
                "Promoted "
                f"{len(payload.get('promoted_event_ids') or [])} lobby event(s) into meeting {payload['meeting_id']}."
            )
        return 0
    return 1
