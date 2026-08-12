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
from state import restore_simulation  # noqa: E402


class PluginServer:
    def __init__(self) -> None:
        self.sim = ColonySimulation(seed=1)
        self._running = True
        self._lock = threading.RLock()
        self._ticker: threading.Thread | None = None
        self._speed_changed_at_revision = 0

    def handle(self, message: dict[str, object]) -> None:
        message_type = str(message.get("type") or "")
        if message_type == "plugin.stop":
            self._running = False
            return
        if message_type == "plugin.start":
            initial_state = message.get("initial_state")
            if isinstance(initial_state, dict) and initial_state:
                try:
                    restore_simulation(self.sim, initial_state)
                except (KeyError, TypeError, ValueError) as error:
                    self._emit_error("restore_failed", str(error))
            # A restarted process cannot safely accept a speed command from a
            # snapshot older than the state it restored.
            self._speed_changed_at_revision = self.sim.revision
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
            # Agent turns are scheduled from one shared snapshot. Distinct
            # providers may therefore return after an earlier colonist has
            # already advanced the global simulation revision. Room routing
            # guarantees one active observation per assigned colonist, so a
            # stale global revision must not discard an unrelated colonist's
            # decision. Interactive host commands remain strict optimistic
            # writes and fail on any stale revision.
            revision_conflict = False
            if command == "set_speed":
                try:
                    observed_revision = int(revision)
                except ValueError:
                    revision_conflict = True
                else:
                    revision_conflict = not (
                        self._speed_changed_at_revision
                        <= observed_revision
                        <= self.sim.revision
                    )
            elif command not in {"agent_turn", "model_error"}:
                revision_conflict = revision != str(self.sim.revision)
            if revision_conflict:
                self._emit_error(
                    "revision_conflict",
                    f"Stale revision {revision}; current {self.sim.revision}",
                    command_id=str(message.get("id") or ""),
                )
                return
            agent_wakes: list[dict[str, object]] = []
            if command == "set_speed":
                self.sim.set_speed(int(args.get("speed", 0)))
                self._speed_changed_at_revision = self.sim.revision
            elif command == "step":
                agent_wakes = self.sim.step(int(args.get("steps") or 1))
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
            elif command == "agent_turn":
                colonist_id = str(args.get("colonist_id") or "")
                act = args.get("act") if isinstance(args.get("act"), dict) else None
                speech = str(args.get("speak") or "").strip()[:500]
                if act is not None:
                    self.sim.apply_act(
                        colonist_id,
                        str(act.get("action") or ""),
                        act.get("action_args")
                        if isinstance(act.get("action_args"), dict)
                        else {},
                    )
                if speech:
                    self.sim.events.append(
                        {
                            "tick": self.sim.tick,
                            "kind": "colonist_speech",
                            "colonist_id": colonist_id,
                            "text": speech,
                        }
                    )
                    if act is None:
                        self.sim.revision += 1
                if act is None and not speech:
                    raise ValueError("An agent turn requires an action or speech.")
            else:
                self._emit_error("unknown_command", f"Unknown plugin command: {command}")
                return
            event: dict[str, object] = {
                "type": "plugin.delta",
                "payload": self.sim.snapshot(),
                "id": message.get("id"),
            }
            if agent_wakes:
                event["agent_wakes"] = agent_wakes
            self._emit(event)
        except Exception as error:  # noqa: BLE001 - process boundary
            self._emit_error("command_failed", str(error), command_id=str(message.get("id") or ""))

    def _ensure_ticker(self) -> None:
        if self._ticker is not None:
            return

        def loop() -> None:
            while self._running:
                with self._lock:
                    if self.sim.speed > 0:
                        agent_wakes = self.sim.step(1)
                        event: dict[str, object] = {
                            "type": "plugin.delta",
                            "payload": self.sim.snapshot(),
                        }
                        if agent_wakes:
                            event["agent_wakes"] = agent_wakes
                        self._emit(event)
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
