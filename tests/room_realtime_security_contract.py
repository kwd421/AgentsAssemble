"""Cross-boundary security regressions for the room realtime controller."""

from __future__ import annotations

from agentsassemble.providers.launch_specs import NativeCliProviderSpec
from agentsassemble.room.errors import RoomCommandRejected
from agentsassemble.room.realtime import RoomRealtimeController
from agentsassemble.room.write_budget import RoomWriteBudget, RoomWriteBudgetPolicy


_HOST_IDENTITY = {
    "agent_id": "operator-local",
    "display_name": "Host",
    "participant_type": "human",
    "client_type": "browser",
    "invite_scope": "read_write",
    "meeting_id": "general",
    "operator": True,
}


def _provider_spec() -> NativeCliProviderSpec:
    return NativeCliProviderSpec(
        agent_id="codex",
        display_name="Codex",
        command=("codex",),
        cwd=".",
        default_responder=True,
    )


class RoomRealtimeSecurityContract:
    def test_common_write_budget_rejects_new_writes_but_keeps_deduplication_and_stop(self):
        self.controller.close()
        self.controller = RoomRealtimeController(
            self.root,
            **self.room_access.controller_kwargs(),
            providers=[_provider_spec()],
            bridge_manager=self.manager,
            recovery_scheduler=self.recovery_scheduler,
            provider_catalog=self.provider_catalog,
            write_budget_policy=RoomWriteBudgetPolicy(
                window_seconds=60.0,
                max_commands_per_window=2,
                max_payload_bytes_per_window=10_000,
            ),
        )
        self.host_channel = self.controller.connect(_HOST_IDENTITY)

        first = self._command("budget-first", "message.send", {"content": "first"})
        self._command("budget-second", "message.send", {"content": "second"})
        before_rejection = self.controller.store.latest_event_sequence("general")

        with self.assertRaises(RoomCommandRejected) as rejected:
            self._command("budget-third", "message.send", {"content": "third"})

        repeated = self._command("budget-first", "message.send", {"content": "first"})
        self.manager.running.discard(("general", "codex"))
        self.controller.store.update_session_fields(
            "general",
            "codex",
            runtime_status="stopped",
            bridge_handle_id="",
        )
        stopped = self._command("budget-stop", "agent.stop", {"agent_id": "codex"})
        self.assertEqual(rejected.exception.code, "write_budget_exceeded")
        self.assertEqual(
            self.controller.store.latest_event_sequence("general"),
            before_rejection,
        )
        self.assertTrue(repeated["deduplicated"])
        self.assertEqual(repeated["result"], first["result"])
        self.assertTrue(stopped["accepted"])

    def test_turn_write_budget_survives_budget_object_recreation(self):
        self._use_continuous_routing()
        self._command("turn-budget-start", "agent.start", {"agent_id": "codex"})
        identity, channel = self._connect_bridge()
        self._command(
            "turn-budget-prompt",
            "message.send",
            {"content": "@codex stream within a bounded turn"},
        )
        assignment = next(
            message for message in channel.drain() if message.get("op") == "turn.assign"
        )
        policy = RoomWriteBudgetPolicy(
            window_seconds=60.0,
            max_commands_per_window=100,
            max_payload_bytes_per_window=100_000,
            max_turn_stream_commands=2,
            max_turn_stream_bytes=10_000,
        )
        self.controller._write_budget = RoomWriteBudget(self.controller.store, policy=policy)
        for index in range(2):
            self._command(
                f"turn-budget-delta-{index}",
                "message.delta",
                {"turn_id": assignment["turn_id"], "content": f"part-{index}"},
                identity,
            )
        self.controller._write_budget = RoomWriteBudget(self.controller.store, policy=policy)
        before_rejection = self.controller.store.latest_event_sequence("general")

        with self.assertRaises(RoomCommandRejected) as rejected:
            self._command(
                "turn-budget-delta-overflow",
                "message.delta",
                {"turn_id": assignment["turn_id"], "content": "must not persist"},
                identity,
            )
        self.assertEqual(
            self.controller.store.latest_event_sequence("general"),
            before_rejection,
        )

        completed = self._command(
            "turn-budget-final",
            "message.final",
            {"turn_id": assignment["turn_id"], "content": "bounded final"},
            identity,
        )
        self.assertEqual(rejected.exception.code, "turn_write_budget_exceeded")
        self.assertGreater(
            self.controller.store.latest_event_sequence("general"),
            before_rejection,
        )
        self.assertEqual(completed["result"]["event"]["content"], "bounded final")

    def test_observation_turn_rejects_direct_streaming_and_unstaged_final(self):
        identity, channel = self._connect_bridge("codex")
        channel.drain()
        self.controller.store.update_room_settings(
            "general",
            {"conversation_mode": "ambient"},
        )
        self._command(
            "observation-publication-source",
            "message.send",
            {"content": "방 도구를 통해서만 답해 줘"},
        )
        wake = next(
            message for message in channel.drain() if message.get("op") == "room.wake"
        )
        before = self.controller.store.latest_event_sequence("general")

        with self.assertRaises(RoomCommandRejected) as delta_rejected:
            self._command(
                "observation-direct-delta",
                "message.delta",
                {"turn_id": wake["turn_id"], "content": "직접 스트림"},
                identity,
            )
        with self.assertRaises(RoomCommandRejected) as final_rejected:
            self._command(
                "observation-direct-final",
                "message.final",
                {
                    "turn_id": wake["turn_id"],
                    "content": "직접 최종 답변",
                    "observed_through_seq": wake["input_up_to_seq"],
                },
                identity,
            )

        self.assertEqual(delta_rejected.exception.code, "observation_publication_required")
        self.assertEqual(
            final_rejected.exception.code,
            "observation_completion_content_forbidden",
        )
        self.assertEqual(self.controller.store.latest_event_sequence("general"), before)
        self.assertEqual(
            self.controller.store.session("general", "codex")["active_turn_id"],
            wake["turn_id"],
        )

    def test_observation_final_uses_server_owned_portal_content_not_bridge_payload(self):
        identity, channel = self._connect_bridge("codex")
        channel.drain()
        self.controller.store.update_room_settings(
            "general",
            {"conversation_mode": "ambient"},
        )
        self._command(
            "staged-publication-source",
            "message.send",
            {"content": "짧게 답해 줘"},
        )
        wake = next(
            message for message in channel.drain() if message.get("op") == "room.wake"
        )
        self.manager.set_room_portal_publication(
            "general",
            "codex",
            wake["turn_id"],
            {
                "turn_id": wake["turn_id"],
                "content": "도구로 게시한 답변",
                "observed_through_seq": wake["input_up_to_seq"],
            },
        )
        with self.assertRaises(RoomCommandRejected) as tampered:
            self._command(
                "tampered-observation-publication",
                "message.final",
                {
                    "turn_id": wake["turn_id"],
                    "content": "브리지가 바꿔치기하려는 답변",
                    "observed_through_seq": wake["input_up_to_seq"],
                },
                identity,
            )
        completed = self._command(
            "commit-observation-publication",
            "message.final",
            {
                "turn_id": wake["turn_id"],
                "observed_through_seq": wake["input_up_to_seq"],
            },
            identity,
        )

        self.assertEqual(tampered.exception.code, "observation_completion_content_forbidden")
        self.assertEqual(completed["result"]["event"]["content"], "도구로 게시한 답변")
        self.assertEqual(
            completed["result"]["event"]["message_source"],
            "room_portal",
        )

    def test_bridge_cannot_stage_an_observation_publication_over_room_protocol(self):
        identity, channel = self._connect_bridge("codex")
        channel.drain()
        self.controller.store.update_room_settings(
            "general",
            {"conversation_mode": "ambient"},
        )
        self._command("raw-stage-source", "message.send", {"content": "짧게 답해 줘"})
        wake = next(
            message for message in channel.drain() if message.get("op") == "room.wake"
        )

        with self.assertRaises(RoomCommandRejected) as rejected:
            self._command(
                "raw-stage-attempt",
                "room.publication.stage",
                {
                    "turn_id": wake["turn_id"],
                    "content": "RoomPortal을 거치지 않은 답변",
                    "observed_through_seq": wake["input_up_to_seq"],
                },
                identity,
            )

        self.assertEqual(rejected.exception.code, "unknown_action")

    def test_server_redacts_a_credential_from_all_bridge_publication_boundaries(self):
        self._use_continuous_routing()
        self._command("split-secret-start", "agent.start", {"agent_id": "codex"})
        identity, channel = self._connect_bridge("codex")
        self._command(
            "split-secret-source",
            "message.send",
            {"content": "진행 상황을 보여 줘"},
        )
        assignment = next(
            message for message in channel.drain() if message.get("op") == "turn.assign"
        )
        secret = "ordinary-looking-credential-918273645"
        self.manager.sensitive_values[("general", "codex")] = (secret,)

        for index, content in enumerate(("prefix ", secret[:15], secret[15:], " suffix")):
            self._command(
                f"split-secret-delta-{index}",
                "message.delta",
                {"turn_id": assignment["turn_id"], "content": content},
                identity,
            )
        self._command(
            "split-secret-final",
            "message.final",
            {
                "turn_id": assignment["turn_id"],
                "content": f"완료 {secret}",
            },
            identity,
        )
        request = self._command(
            "split-secret-provider-request",
            "provider.request.open",
            {
                "provider_request_id": "request-with-secret",
                "request_kind": "permission",
                "response_kind": "option",
                "title": f"Allow {secret}?",
                "options": [
                    {"id": "allow", "label": f"Allow {secret}"},
                    {"id": "deny", "label": "Deny"},
                ],
            },
            identity,
        )
        events = self.controller.store.read_events("general")
        deltas = [
            event["content"]
            for event in events
            if event.get("type") == "message_delta"
            and event.get("turn_id") == assignment["turn_id"]
        ]
        final = next(
            event
            for event in events
            if event.get("type") == "message_final"
            and event.get("turn_id") == assignment["turn_id"]
        )
        provider_request = request["result"]["event"]["provider_request"]
        reconstructed = "".join(deltas)

        self.assertNotIn(secret, reconstructed)
        self.assertIn("[redacted]", reconstructed)
        self.assertNotIn(secret, final["content"])
        self.assertIn("[redacted]", final["content"])
        self.assertNotIn(secret, str(provider_request))
        self.assertIn("[redacted]", str(provider_request))


__all__ = ["RoomRealtimeSecurityContract"]
