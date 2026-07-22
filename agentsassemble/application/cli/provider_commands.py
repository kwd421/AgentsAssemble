"""Execution for current provider diagnostics CLI commands."""
from __future__ import annotations

import argparse
import json
import sys

from agentsassemble.diagnostics.cli import DiagnosticCliRuntime, run_provider_health_command


def run_providers_command(
    args: argparse.Namespace,
    *,
    runtime: DiagnosticCliRuntime,
) -> int:
    try:
        if args.providers_command == "health":
            return run_provider_health_command(args, runtime=runtime)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 1
