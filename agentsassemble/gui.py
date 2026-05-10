from __future__ import annotations

import json
import mimetypes
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from agentsassemble.meeting import run_demo_meeting
from agentsassemble.meeting_events import append_lobby_event_to_file, read_live_events, read_lobby_events
from agentsassemble.adapters import default_provider_registry

TAB_LABELS = {"lobby": "로비", "live": "실황", "board": "작전판", "archive": "아카이브"}
TABS = ["lobby", "live", "board", "archive"]
STALE_RUNNING_SECONDS = 300


def list_meetings(output_root: Path, now: float | None = None) -> list[dict[str, object]]:
    meetings_dir = output_root / "meetings"
    if not meetings_dir.exists():
        return []

    meetings = []
    for meeting_dir in meetings_dir.iterdir():
        record_path = meeting_dir / "meeting.json"
        live_path = meeting_dir / "live_state.json"
        if not record_path.exists() and not live_path.exists():
            continue
        try:
            source_path = record_path if record_path.exists() else live_path
            meeting = json.loads(source_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        meeting = _with_inferred_live_status(
            meeting,
            meeting_dir,
            has_final_record=record_path.exists(),
            now=now,
        )
        stat = source_path.stat()
        meetings.append(
            {
                "meeting_id": meeting.get("meeting_id", meeting_dir.name),
                "topic": meeting.get("topic", ""),
                "question": meeting.get("question", ""),
                "created_at": meeting.get("audit_metadata", {}).get("created_at", ""),
                "live_status": meeting.get("live_status", "complete" if record_path.exists() else "unknown"),
                "path": str(meeting_dir),
                "mtime": stat.st_mtime,
            }
        )
    return sorted(meetings, key=lambda item: item["mtime"], reverse=True)


def build_meeting_payload(meeting_dir: Path, now: float | None = None) -> dict[str, object]:
    meeting_path = meeting_dir / "meeting.json"
    live_path = meeting_dir / "live_state.json"
    meeting = json.loads((meeting_path if meeting_path.exists() else live_path).read_text(encoding="utf-8"))
    meeting = _with_inferred_live_status(
        meeting,
        meeting_dir,
        has_final_record=meeting_path.exists(),
        now=now,
    )
    artifacts = {
        name: _read_optional(meeting_dir / name)
        for name in ("agenda.md", "transcript.md", "decision.md", "meeting.json")
    }
    tasks = {
        task_path.name: task_path.read_text(encoding="utf-8")
        for task_path in sorted((meeting_dir / "tasks").glob("*.md"))
    }
    return_packets = {
        packet_path.name: packet_path.read_text(encoding="utf-8")
        for packet_path in sorted((meeting_dir / "return_packets").glob("*.md"))
    }
    research = {}
    research_json = {}
    research_root = meeting_dir / "private_research"
    if research_root.exists():
        for research_path in sorted(research_root.glob("*/research.md")):
            research[f"{research_path.parent.name}/research.md"] = research_path.read_text(encoding="utf-8")
        for research_path in sorted(research_root.glob("*/research.json")):
            try:
                research_json[research_path.parent.name] = json.loads(research_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                research_json[research_path.parent.name] = {"error": "Research JSON could not be parsed."}
    return {
        "tabs": TABS,
        "tab_labels": TAB_LABELS,
        "meeting": meeting,
        "artifacts": artifacts,
        "tasks": tasks,
        "return_packets": return_packets,
        "research": research,
        "research_json": research_json,
        "live_events": read_live_events(meeting_dir),
    }


def _with_inferred_live_status(
    meeting: dict[str, object],
    meeting_dir: Path,
    has_final_record: bool,
    now: float | None = None,
) -> dict[str, object]:
    if has_final_record or meeting.get("live_status") != "running":
        return meeting
    latest_mtime = _latest_live_mtime(meeting_dir)
    if latest_mtime is None:
        return meeting
    if (now if now is not None else time.time()) - latest_mtime < STALE_RUNNING_SECONDS:
        return meeting
    inferred = dict(meeting)
    inferred["live_status"] = "stalled"
    inferred["stalled_reason"] = "No live meeting update has been observed recently."
    inferred["last_live_update_mtime"] = latest_mtime
    return inferred


def _latest_live_mtime(meeting_dir: Path) -> float | None:
    mtimes = [
        path.stat().st_mtime
        for path in (meeting_dir / "live_state.json", meeting_dir / "live_events.jsonl")
        if path.exists()
    ]
    return max(mtimes) if mtimes else None


def serve_gui(host: str = "127.0.0.1", port: int = 8765, output_root: Path | None = None) -> None:
    root = output_root or Path(".agentsassemble")
    handler = _make_handler(root)
    server = ThreadingHTTPServer((host, port), handler)
    print(f"AgentsAssemble GUI: http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping AgentsAssemble GUI")
    finally:
        server.server_close()


def _read_optional(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def read_lobby(output_root: Path, limit: int = 80) -> list[dict[str, object]]:
    return read_lobby_events(output_root / "lobby.jsonl", limit=limit)


def append_lobby_event(output_root: Path, event: dict[str, object]) -> dict[str, object]:
    return append_lobby_event_to_file(output_root / "lobby.jsonl", event)


def provider_catalog_payload() -> dict[str, object]:
    return {"providers": default_provider_registry().catalog()}


def _make_handler(output_root: Path) -> type[BaseHTTPRequestHandler]:
    static_root = Path(__file__).parent / "static"

    class AgentsAssembleHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            if path == "/":
                self._send_file(static_root / "index.html", "text/html; charset=utf-8")
                return
            if path.startswith("/static/"):
                rel = path.removeprefix("/static/")
                static_path = _safe_static_path(static_root, rel)
                if static_path is None:
                    self._send_error(HTTPStatus.NOT_FOUND, "File not found")
                    return
                self._send_file(static_path)
                return
            if path == "/api/meetings":
                self._send_json({"meetings": list_meetings(output_root)})
                return
            if path == "/api/lobby":
                self._send_json({"events": read_lobby(output_root)})
                return
            if path == "/api/providers":
                self._send_json(provider_catalog_payload())
                return
            if path == "/api/meetings/latest":
                meetings = list_meetings(output_root)
                if not meetings:
                    self._send_json({"meeting": None})
                    return
                self._send_json(build_meeting_payload(Path(str(meetings[0]["path"]))))
                return
            if path.startswith("/api/meetings/"):
                meeting_id = unquote(path.removeprefix("/api/meetings/"))
                meeting_dir = output_root / "meetings" / meeting_id
                if not meeting_dir.exists():
                    self._send_error(HTTPStatus.NOT_FOUND, "Meeting not found")
                    return
                self._send_json(build_meeting_payload(meeting_dir))
                return
            self._send_error(HTTPStatus.NOT_FOUND, "Not found")

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/api/demo":
                result = run_demo_meeting(adapter_name="mock", output_root=output_root)
                self._send_json({"meeting_id": result.meeting_id, "path": str(result.meeting_dir)})
                return
            if parsed.path == "/api/lobby":
                length = int(self.headers.get("Content-Length", "0") or "0")
                try:
                    payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
                except json.JSONDecodeError:
                    self._send_error(HTTPStatus.BAD_REQUEST, "Invalid JSON")
                    return
                event = append_lobby_event(output_root, payload if isinstance(payload, dict) else {})
                self._send_json({"event": event, "events": read_lobby(output_root)})
                return
            self._send_error(HTTPStatus.NOT_FOUND, "Not found")

        def log_message(self, format: str, *args: object) -> None:
            return

        def _send_file(self, path: Path, content_type: str | None = None) -> None:
            if not path.exists() or not path.is_file():
                self._send_error(HTTPStatus.NOT_FOUND, "File not found")
                return
            guessed = content_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            data = path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", guessed)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _send_json(self, payload: dict[str, object]) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _send_error(self, status: HTTPStatus, message: str) -> None:
            data = json.dumps({"error": message}, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return AgentsAssembleHandler


def _safe_static_path(static_root: Path, relative_path: str) -> Path | None:
    root = static_root.resolve()
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate
