from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request


_BODY_LIMIT = 128_000


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--endpoint", default="")
    parser.add_argument("--token", default="")
    args = parser.parse_args(argv)
    endpoint = args.endpoint or os.environ.get("AGENTSASSEMBLE_ANTIGRAVITY_HOOK_ENDPOINT", "")
    token = args.token or os.environ.get("AGENTSASSEMBLE_ANTIGRAVITY_HOOK_TOKEN", "")
    if not endpoint or not token:
        sys.stdout.write(
            json.dumps(
                {
                    "decision": "ask",
                    "reason": "AgentsAssemble room session is not connected.",
                }
            )
        )
        return 0
    try:
        payload = sys.stdin.buffer.read(_BODY_LIMIT + 1)
        if not payload or len(payload) > _BODY_LIMIT:
            raise ValueError("invalid hook input")
        request = urllib.request.Request(
            endpoint,
            data=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=620) as response:
            result = json.loads(response.read(_BODY_LIMIT).decode("utf-8"))
        if not isinstance(result, dict):
            raise ValueError("invalid hook response")
    except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError) as error:
        result = {"decision": "deny", "reason": f"AgentsAssemble hook unavailable: {error}"}
    sys.stdout.write(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
