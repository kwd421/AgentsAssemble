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

    def test_runtime_credential_cannot_be_reassembled_from_activity_fields_or_events(self):
        secret = "ordinary-looking-credential-918273645"
        split_at = len(secret) // 2
        first_half, second_half = secret[:split_at], secret[split_at:]
        self.manager.sensitive_values[("general", "codex")] = (secret,)
        self._use_continuous_routing()
        self._command("req-start-split-secret", "agent.start", {"agent_id": "codex"})
        identity, channel = self._connect_bridge()
        self._command(
            "req-prompt-split-secret",
            "message.send",
            {"content": "@codex inspect safely"},
        )
        assignment = next(
            message
            for message in channel.drain()
            if message.get("op") == "turn.assign"
        )

        self._command(
            "req-activity-split-fields",
            "activity.update",
            {
                "turn_id": assignment["turn_id"],
                "category": "command",
                "status": "running",
                "activity_title": first_half,
                "activity_detail": second_half,
            },
            identity,
        )
        self._command(
            "req-activity-split-event-one",
            "activity.update",
            {
                "turn_id": assignment["turn_id"],
                "category": "reasoning",
                "status": "running",
                "activity_detail": first_half,
            },
            identity,
        )
        self._command(
            "req-activity-split-event-two",
            "activity.update",
            {
                "turn_id": assignment["turn_id"],
                "category": "reasoning",
                "status": "running",
                "activity_detail": second_half,
            },
            identity,
        )

        activities = [
            event
            for event in RoomStore(self.root).read_events("general")
            if event.get("type") == "activity_delta"
        ]
        split_field_event = activities[-3]
        self.assertNotEqual(
            f"{split_field_event.get('activity_title', '')}"
            f"{split_field_event.get('activity_detail', '')}",
            secret,
        )
        self.assertNotEqual(
            "".join(str(event.get("activity_detail") or "") for event in activities[-2:]),
            secret,
        )
        websocket_activities = [
            event
            for message in self.host_channel.drain()
            for event in list(message.get("events") or [])
            if isinstance(event, dict) and event.get("type") == "activity_delta"
        ]
        self.assertNotIn(
            secret,
            "".join(
                str(event.get("activity_title") or "")
                + str(event.get("activity_detail") or "")
                for event in websocket_activities
            ),
        )
