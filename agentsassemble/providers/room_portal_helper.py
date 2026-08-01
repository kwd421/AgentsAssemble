"""Standalone terminal helper installed into provider-private room state."""

from __future__ import annotations

import functools
import os
import shutil
import subprocess
import sys
from pathlib import Path


@functools.lru_cache(maxsize=1)
def helper_interpreter() -> str:
    """Resolve an interpreter that remains usable inside provider sandboxes."""
    candidates = ("/usr/bin/python3", sys.executable, shutil.which("python3") or "")
    for candidate in candidates:
        if not candidate or not os.path.isabs(candidate):
            continue
        try:
            completed = subprocess.run(
                [candidate, "-c", "import json, os, pathlib, re, secrets, sys"],
                capture_output=True,
                timeout=10.0,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if completed.returncode == 0:
            return candidate
    return "/usr/bin/env python3"


def helper_script() -> str:
    """Return the helper with its shebang pinned to the resolved interpreter."""
    _, _, body = _HELPER_SCRIPT.partition("\n")
    return f"#!{helper_interpreter()}\n{body}"


_HELPER_SCRIPT = r"""#!/usr/bin/env python3
import json
import os
import re
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VIEW = ROOT / "current.md"
TURN = ROOT / "turn.json"
OUTBOX = ROOT / "outbox.json"
MEDIA = ROOT / "media.json"
ACTIVITY = ROOT / "activity.jsonl"

def fail(message):
    print(message, file=sys.stderr)
    raise SystemExit(2)

def atomic_json(path, payload):
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
    try:
        temporary.chmod(0o600)
    except OSError:
        pass
    os.replace(temporary, path)

def audit(operation, turn_id="", details=None):
    observed_through_seq = 0
    if not turn_id:
        try:
            turn = json.loads(TURN.read_text(encoding="utf-8"))
            turn_id = str(turn.get("turn_id") or "")
            observed_through_seq = int(turn.get("input_up_to_seq") or 0)
        except (OSError, json.JSONDecodeError):
            turn_id = ""
    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "operation": operation,
        "turn_id": turn_id,
        "observed_through_seq": observed_through_seq if operation == "read" else 0,
    }
    if operation in {"roll_dice", "choose_random"}:
        payload["result_id"] = f"result-{secrets.token_hex(16)}"
    if details:
        payload["details"] = details
    with ACTIVITY.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    try:
        ACTIVITY.chmod(0o600)
    except OSError:
        pass

command = sys.argv[1] if len(sys.argv) > 1 else "help"
if command == "read":
    content = VIEW.read_text(encoding="utf-8")
    audit("read")
    sys.stdout.write(content)
elif command in {"speak", "speak-to"}:
    target_agent_id = ""
    content_start = 2
    if command == "speak-to":
        if len(sys.argv) < 4 or re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", sys.argv[2]) is None:
            fail("usage: agentsassemble-room speak-to <agent-id> '<message>'")
        target_agent_id = sys.argv[2]
        content_start = 3
    content = " ".join(sys.argv[content_start:]).strip() if len(sys.argv) > content_start else sys.stdin.read().strip()
    if not content:
        fail("room message is empty")
    turn = json.loads(TURN.read_text(encoding="utf-8"))
    turn_id = str(turn.get("turn_id") or "")
    if not turn_id:
        fail("no room observation is active")
    atomic_json(
        OUTBOX,
        {
            "turn_id": turn_id,
            "content": content[:12000],
            "target_agent_id": target_agent_id,
        },
    )
    audit("speak", turn_id, {"target_agent_id": target_agent_id} if target_agent_id else None)
elif command == "media":
    attachment_id = sys.argv[2] if len(sys.argv) > 2 else ""
    index = json.loads(MEDIA.read_text(encoding="utf-8")).get("media", {})
    item = index.get(attachment_id)
    if not isinstance(item, dict) or not item.get("path"):
        fail("media is unavailable")
    audit("media")
    print(item["path"])
elif command == "roll":
    if len(sys.argv) != 3:
        fail("usage: agentsassemble-room roll '<NdS+M>'")
    match = re.fullmatch(r"\s*(\d{0,3})d(\d{1,4})([+-]\d{1,5})?\s*", sys.argv[2], re.IGNORECASE)
    if match is None:
        fail("dice notation must look like d20, 2d6, or 1d20+3")
    count = int(match.group(1) or 1)
    sides = int(match.group(2))
    modifier = int(match.group(3) or 0)
    if not 1 <= count <= 100 or not 2 <= sides <= 1000 or not -100000 <= modifier <= 100000:
        fail("dice notation is out of range")
    rolls = [secrets.randbelow(sides) + 1 for _ in range(count)]
    notation = f"{count}d{sides}" + (f"{modifier:+d}" if modifier else "")
    result = {
        "notation": notation,
        "rolls": rolls,
        "modifier": modifier,
        "total": sum(rolls) + modifier,
    }
    audit("roll_dice", details=result)
    print(json.dumps(result, ensure_ascii=False))
elif command == "help":
    print("agentsassemble-room read | speak [text] | speak-to <agent-id> [text] | media <id> | roll '<NdS+M>'")
else:
    fail("unknown command")
"""


def windows_helper_wrapper() -> str:
    executable = str(Path(sys.executable).resolve()).replace("%", "%%")
    return (
        "@echo off\r\n"
        f'"{executable}" "%~dp0\\agentsassemble_room_helper.py" %*\r\n'
    )
