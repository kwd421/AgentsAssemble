from agentsassemble.persistence.local.room.repository import RoomStore
from agentsassemble.providers.launch_specs import native_cli_provider_definition
from agentsassemble.room.errors import RoomCommandRejected


class RoomAgentCreationRetryContract:
    def test_agent_create_retry_finishes_the_same_session_after_start_failure(self):
        payload = {
            "provider_id": "claude",
            "display_name": "Claude Retry",
            "workspace": str(self.root),
            "model": "claude-haiku-4-5",
            "start": True,
        }
        self.manager.start_errors.append(RuntimeError("temporary launch failure"))

        with self.assertRaises(RoomCommandRejected) as failed:
            self._command("req-create-retry", "agent.create", payload)
        self.assertEqual(failed.exception.code, "runtime_start_failed")

        retried = self._command("req-create-retry", "agent.create", payload)
        session = retried["result"]["agent_session"]
        matching_events = [
            event
            for event in RoomStore(self.root).read_events("general")
            if event.get("type") == "agent_session_created"
            and event.get("participant_id") == session["participant_id"]
        ]

        self.assertTrue(retried["accepted"])
        self.assertEqual(self.manager.starts, [("general", session["participant_id"])])
        self.assertEqual(len(matching_events), 1)

    def test_agent_create_assigns_stable_unique_identity_independent_of_display_name(self):
        payload = {
            "provider_id": "claude",
            "display_name": "Claude Opus 5",
            "workspace": str(self.root),
            "model": "claude-haiku-4-5",
        }

        first = self._command("req-create-claude-first", "agent.create", payload)
        repeated = self._command("req-create-claude-first", "agent.create", payload)
        second = self._command("req-create-claude-second", "agent.create", payload)

        first_session = first["result"]["agent_session"]
        repeated_session = repeated["result"]["agent_session"]
        second_session = second["result"]["agent_session"]
        self.assertEqual(repeated_session["session_id"], first_session["session_id"])
        self.assertTrue(repeated["deduplicated"])
        self.assertNotEqual(second_session["session_id"], first_session["session_id"])
        matching_sessions = [
            session
            for session in RoomStore(self.root).sessions("general")
            if session["display_name"] == "Claude Opus 5"
        ]
        self.assertEqual(len(matching_sessions), 2)

    def test_readd_retry_finishes_the_same_reactivation_after_start_failure(self):
        definition = native_cli_provider_definition("codex")
        self.assertIsNotNone(definition)
        spec = definition.make_selected_spec(
            agent_id="codex",
            display_name="Codex",
            cwd=self.root,
            model="gpt-5.6-luna",
            reasoning_effort="low",
            service_tier="default",
            permission_mode="meeting_read_only",
        )
        self.controller.configure_stopped_provider_profile("general", spec)
        self._command("req-kick-before-readd-retry", "participant.kick", {"participant_id": "codex"})
        self.manager.start_errors.append(RuntimeError("temporary reactivation failure"))
        payload = {"agent_id": "codex", "start": True}

        with self.assertRaises(RoomCommandRejected) as failed:
            self._command("req-readd-retry", "agent.readd", payload)
        self.assertEqual(failed.exception.code, "runtime_start_failed")

        retried = self._command("req-readd-retry", "agent.readd", payload)
        event_types = [event["type"] for event in RoomStore(self.root).read_events("general")]

        self.assertTrue(retried["accepted"])
        self.assertEqual(retried["result"]["status"], "readded")
        self.assertEqual(self.manager.starts, [("general", "codex")])
        self.assertEqual(event_types.count("agent_session_reactivated"), 1)


__all__ = ["RoomAgentCreationRetryContract"]
