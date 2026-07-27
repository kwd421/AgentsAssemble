"""Stdio MCP boundary for one provider session's private room portal."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from uuid import uuid4

from agentsassemble.providers.room_random import (
    choose_random as choose_random_result,
)
from agentsassemble.providers.room_random import roll_dice as roll_dice_result
from agentsassemble.room.text import clean_room_text


def serve_room_portal_mcp(root: str | Path) -> None:
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as error:  # pragma: no cover - deployment dependency check
        raise RuntimeError("The room portal MCP server requires mcp>=1,<2.") from error

    portal_root = Path(root).expanduser().resolve()
    view_path = portal_root / "current.md"
    turn_path = portal_root / "turn.json"
    outbox_path = portal_root / "outbox.json"
    activity_path = portal_root / "activity.jsonl"
    server = FastMCP("AgentsAssemble Room Portal", json_response=True)

    @server.tool()
    def read_discussion() -> str:
        """Read the finalized messages currently visible in the shared room."""
        text = view_path.read_text(encoding="utf-8")
        _record_activity(activity_path, turn_path, "read")
        return text

    @server.tool()
    def publish_message(content: str, next_agent_id: str = "") -> str:
        """Publish one substantive message, optionally handing the floor to one agent."""
        message = str(content or "").replace("\x00", "").strip()[:12_000]
        if not message:
            raise ValueError("A room publication cannot be empty.")
        target_agent_id = clean_room_text(next_agent_id, limit=128)
        turn = _read_turn(turn_path)
        turn_id = str(turn.get("turn_id") or "").strip()
        if not turn_id:
            raise RuntimeError("No room observation is active.")
        _write_json_atomic(
            outbox_path,
            {
                "turn_id": turn_id,
                "content": message,
                "target_agent_id": target_agent_id,
            },
        )
        _record_activity(activity_path, turn_path, "speak", turn_id=turn_id)
        return "Published to the shared room."

    @server.tool()
    def roll_dice(notation: str, reason: str = "") -> dict[str, object]:
        """Roll bounded NdS±M dice using server-side randomness."""
        result = roll_dice_result(notation)
        _record_activity(
            activity_path,
            turn_path,
            "roll_dice",
            details={
                **result,
                "reason": clean_room_text(reason, limit=200),
            },
        )
        return result

    @server.tool()
    def choose_random(options: list[str], reason: str = "") -> dict[str, object]:
        """Choose one item from 2 to 50 options using server-side randomness."""
        result = choose_random_result(options)
        _record_activity(
            activity_path,
            turn_path,
            "choose_random",
            details={
                **result,
                "reason": clean_room_text(reason, limit=200),
            },
        )
        return result

    server.run(transport="stdio")


def room_portal_mcp_settings(root: str | Path) -> dict[str, object]:
    import sys

    return {
        "command": sys.executable,
        "args": [
            "-m",
            "agentsassemble.providers.room_portal_mcp",
            "--root",
            str(Path(root).expanduser().resolve()),
        ],
        "cwd": str(Path(__file__).resolve().parents[2]),
    }


def _read_turn(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _record_activity(
    activity_path: Path,
    turn_path: Path,
    operation: str,
    *,
    turn_id: str = "",
    details: dict[str, object] | None = None,
) -> None:
    turn = _read_turn(turn_path)
    active_turn_id = turn_id or str(turn.get("turn_id") or "").strip()
    observed_through_seq = (
        _safe_nonnegative_int(turn.get("input_up_to_seq"))
        if operation == "read"
        else 0
    )
    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "operation": operation,
        "turn_id": active_turn_id,
        "observed_through_seq": observed_through_seq,
    }
    if operation in {"roll_dice", "choose_random"}:
        payload["result_id"] = f"result-{uuid4().hex}"
    if details:
        payload["details"] = details
    with activity_path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    activity_path.chmod(0o600)


def _safe_nonnegative_int(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.chmod(0o600)
    os.replace(temporary, path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    args = parser.parse_args()
    serve_room_portal_mcp(args.root)


if __name__ == "__main__":
    main()
