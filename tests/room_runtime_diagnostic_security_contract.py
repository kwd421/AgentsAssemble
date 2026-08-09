import json

from agentsassemble.persistence.local.room.repository import RoomStore


class RoomRuntimeDiagnosticSecurityContract:
    def test_runtime_credentials_never_reach_room_state_events_or_websocket(self):
        secret = "server-owned-runtime-credential-918273645"
        self.manager.sensitive_values[("general", "codex")] = (secret,)
        self._use_continuous_routing()
        self._command("req-start-secret", "agent.start", {"agent_id": "codex"})
        identity, channel = self._connect_bridge()
        self._command(
            "req-prompt-secret",
            "message.send",
            {"content": "@codex fail safely"},
        )
        assignment = next(
            message
            for message in channel.drain()
            if message.get("op") == "turn.assign"
        )
        self._command(
            "req-activity-secret",
            "activity.update",
            {
                "turn_id": assignment["turn_id"],
                "category": "command",
                "status": "running",
                "activity_title": f"credential {secret}",
                "activity_detail": f"provider echoed {secret}",
            },
            identity,
        )
        result = self._command(
            "req-failed-secret",
            "turn.failed",
            {
                "turn_id": assignment["turn_id"],
                "message": f"provider failed with {secret}",
                "diagnostics": {
                    "stderr_tail": f"stderr echoed {secret}",
                    "terminal_tail": f"terminal echoed {secret}",
                    "provider_session_resume_error": f"resume echoed {secret}",
                },
            },
            identity,
        )
        store = RoomStore(self.root)
        persisted = {
            "session": store.session("general", "codex"),
            "events": store.read_events("general"),
            "response": result,
            "websocket": self.host_channel.drain(),
        }
        serialized = json.dumps(persisted, ensure_ascii=False)
        self.assertNotIn(secret, serialized)
        self.assertIn("[redacted]", serialized)
