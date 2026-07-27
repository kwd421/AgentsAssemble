"""Stdio MCP boundary for one provider session's private room portal."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path


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
    def publish_message(content: str) -> str:
        """Publish one substantive message to the shared room."""
        message = str(content or "").replace("\x00", "").strip()[:12_000]
        if not message:
            raise ValueError("A room publication cannot be empty.")
        turn = _read_turn(turn_path)
        turn_id = str(turn.get("turn_id") or "").strip()
        if not turn_id:
            raise RuntimeError("No room observation is active.")
        _write_json_atomic(
            outbox_path,
            {"turn_id": turn_id, "content": message},
        )
        _record_activity(activity_path, turn_path, "speak", turn_id=turn_id)
        return "Published to the shared room."

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
