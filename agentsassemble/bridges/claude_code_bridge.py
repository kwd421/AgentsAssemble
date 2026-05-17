from __future__ import annotations

import argparse
import json
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http import HTTPStatus
from typing import Any, Callable


Runner = Callable[..., subprocess.CompletedProcess[str]]


def run_bridge_request(
    payload: dict[str, Any],
    command: str = "claude",
    runner: Runner = subprocess.run,
    timeout_seconds: int = 300,
) -> dict[str, Any]:
    prompt = str(payload.get("prompt") or "")
    completed = runner(
        [command, "-p"],
        input=prompt,
        text=True,
        capture_output=True,
        timeout=timeout_seconds,
        check=False,
    )
    metadata = {
        "bridge": "claude_code",
        "command": f"{command} -p",
        "returncode": completed.returncode,
        "stderr": completed.stderr or "",
        "role_id": (payload.get("role") or {}).get("id"),
        "step": payload.get("step"),
    }
    if completed.returncode != 0:
        return {
            "text": f"Claude Code bridge failed with return code {completed.returncode}.",
            "metadata": metadata,
        }
    return {"text": completed.stdout or "", "metadata": metadata}


def serve_bridge(host: str, port: int, token: str | None, command: str) -> None:
    require_bridge_token(token)
    server = ThreadingHTTPServer((host, port), _handler(token=token, command=command))
    print(f"AgentsAssemble Claude Code bridge: http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping AgentsAssemble Claude Code bridge")
    finally:
        server.server_close()


def _handler(token: str | None, command: str) -> type[BaseHTTPRequestHandler]:
    class ClaudeCodeBridgeHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path != "/agentsassemble/health":
                self._send_error(HTTPStatus.NOT_FOUND, "Not found")
                return
            if not token or self.headers.get("Authorization") != f"Bearer {token}":
                self._send_error(HTTPStatus.UNAUTHORIZED, "Unauthorized")
                return
            self._send_json(
                {
                    "status": "ok",
                    "bridge": "claude_code",
                    "health_endpoint": "/agentsassemble/health",
                    "run_endpoint": "/agentsassemble/run",
                }
            )

        def do_POST(self) -> None:
            if self.path != "/agentsassemble/run":
                self._send_error(HTTPStatus.NOT_FOUND, "Not found")
                return
            if not token or self.headers.get("Authorization") != f"Bearer {token}":
                self._send_error(HTTPStatus.UNAUTHORIZED, "Unauthorized")
                return
            length = int(self.headers.get("Content-Length", "0") or "0")
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
            except json.JSONDecodeError:
                self._send_error(HTTPStatus.BAD_REQUEST, "Invalid JSON")
                return
            if not isinstance(payload, dict):
                self._send_error(HTTPStatus.BAD_REQUEST, "Payload must be an object")
                return
            result = run_bridge_request(payload, command=command)
            self._send_json(result)

        def log_message(self, format: str, *args: object) -> None:
            return

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

    return ClaudeCodeBridgeHandler


def require_bridge_token(token: str | None) -> None:
    if not token:
        raise ValueError("Claude Code bridge requires --token. Do not expose a bridge without authentication.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agentsassemble-claude-bridge")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8777)
    parser.add_argument("--token", required=True)
    parser.add_argument("--command", default="claude")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    serve_bridge(host=args.host, port=args.port, token=args.token, command=args.command)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
