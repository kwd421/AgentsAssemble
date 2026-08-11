#!/usr/bin/env python3
"""RimWorld plugin server process: JSONL over stdin/stdout."""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sim import ColonySimulation  # noqa: E402


class PluginServer:
    def __init__(self) -> None:
        self.sim = ColonySimulation(seed=1)
        self._running = True
        self._lock = threading.RLock()
        self._ticker: threading.Thread | None = None

    def handle(self, message: dict[str, object]) -> None:
        message_type = str(message.get("type") or "")
        if message_type == "plugin.stop":
            self._running = False
            return
        if message_type == "plugin.start":
            self._ensure_ticker()
            self._emit(
                {
                    "type": "plugin.snapshot",
                    "payload": self.sim.snapshot(),
                }
            )
            return
        if message_type != "plugin.command":
            self._emit_error("unsupported_command", f"Unsupported message type: {message_type}")
            return
        command = str(message.get("command") or "")
        args = message.get("args") if isinstance(message.get("args"), dict) else {}
        revision = str(message.get("revision") or "")
        try:
            if command == "snapshot":
                payload = self.sim.snapshot()
                self._emit({"type": "plugin.snapshot", "payload": payload, "id": message.get("id")})
                return
            if not revision:
                self._emit_error(
                    "revision_required",
                    "A current simulation revision is required for state changes.",
                    command_id=str(message.get("id") or ""),
                )
                return
            if revision != str(self.sim.revision):
                self._emit_error(
                    "revision_conflict",
                    f"Stale revision {revision}; current {self.sim.revision}",
                    command_id=str(message.get("id") or ""),
                )
                return
            if command == "set_speed":
                self.sim.set_speed(int(args.get("speed", 0)))
            elif command == "step":
                self.sim.step(int(args.get("steps") or 1))
            elif command == "act":
                self.sim.apply_act(
                    str(args.get("colonist_id") or ""),
                    str(args.get("action") or ""),
                    args.get("action_args")
                    if isinstance(args.get("action_args"), dict)
                    else {},
                )
            elif command == "model_error":
                self.sim.mark_model_error(
                    str(args.get("colonist_id") or ""),
                    str(args.get("message") or "provider error"),
                )
            elif command == "clear_wait":
                self.sim.clear_wait(str(args.get("colonist_id") or ""))
            else:
                self._emit_error("unknown_command", f"Unknown plugin command: {command}")
                return
            self._emit(
                {
                    "type": "plugin.delta",
                    "payload": self.sim.snapshot(),
                    "id": message.get("id"),
                }
            )
        except Exception as error:  # noqa: BLE001 - process boundary
            self._emit_error("command_failed", str(error), command_id=str(message.get("id") or ""))

    def _ensure_ticker(self) -> None:
        if self._ticker is not None:
            return

        def loop() -> None:
            while self._running:
                with self._lock:
                    if self.sim.speed > 0:
                        self.sim.step(1)
                        self._emit({"type": "plugin.delta", "payload": self.sim.snapshot()})
                time.sleep(0.2)

        self._ticker = threading.Thread(target=loop, name="rimworld-ticker", daemon=True)
        self._ticker.start()

    def _emit(self, payload: dict[str, object]) -> None:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    def _emit_error(self, code: str, message: str, *, command_id: str = "") -> None:
        self._emit(
            {
                "type": "plugin.error",
                "code": code,
                "message": message,
                "command_id": command_id,
            }
        )


def main() -> int:
    server = PluginServer()
    for raw in sys.stdin:
        line = raw[:-1] if raw.endswith("\n") else raw
        if line.endswith("\r"):
            line = line[:-1]
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            server._emit_error("invalid_json", "Plugin command was not valid JSON.")
            continue
        if not isinstance(message, dict):
            continue
        with server._lock:
            server.handle(message)
        if not server._running:
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
