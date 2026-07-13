import unittest

from agentsassemble.room_floor_policy import (
    continuous_floor_targets,
    evaluate_agent_floor_eligibility,
)


class RoomFloorPolicyTests(unittest.TestCase):
    def test_floor_eligibility_reports_the_first_blocking_reason(self):
        joined = {"status": "joined", "muted": False}
        idle = {"status": "attached", "enabled": True, "runtime_status": "idle"}

        cases = (
            ({}, idle, False, True, "participant_not_joined"),
            ({"status": "detached"}, idle, False, True, "participant_not_joined"),
            ({"status": "joined", "muted": True}, idle, False, True, "participant_muted"),
            (joined, idle, True, True, "participant_muted"),
            (joined, {}, False, True, "session_missing"),
            (joined, {**idle, "status": "unavailable"}, False, True, "session_not_attached"),
            (joined, {**idle, "enabled": False}, False, True, "session_disabled"),
            (joined, {**idle, "runtime_status": "busy"}, False, True, "runtime_busy"),
            (joined, idle, False, False, "bridge_disconnected"),
            (joined, idle, False, True, "eligible"),
        )

        for participant, session, muted, connected, expected in cases:
            with self.subTest(expected=expected):
                result = evaluate_agent_floor_eligibility(
                    participant,
                    session,
                    member_muted=muted,
                    bridge_connected=connected,
                )
                self.assertEqual(result.reason_code, expected)
                self.assertEqual(result.eligible, expected == "eligible")

    def test_continuous_floor_rotates_to_one_eligible_speaker(self):
        result = continuous_floor_targets(
            provider_ids=["alpha", "bravo", "charlie"],
            actor_id="bravo",
            routed_targets=["alpha", "charlie"],
            eligible_agent_ids=["alpha", "charlie"],
            content="다음 의견을 이어가",
        )

        self.assertEqual(result, ("charlie",))

    def test_continuous_floor_starts_with_first_sorted_agent_for_human_message(self):
        result = continuous_floor_targets(
            provider_ids=["charlie", "alpha", "bravo"],
            actor_id="operator-local",
            routed_targets=["charlie", "alpha", "bravo"],
            eligible_agent_ids=["charlie", "alpha", "bravo"],
            content="주제를 시작해",
        )

        self.assertEqual(result, ("alpha",))

    def test_explicit_mentions_preserve_routed_targets_without_floor_filtering(self):
        mentioned = continuous_floor_targets(
            provider_ids=["alpha", "bravo"],
            actor_id="operator-local",
            routed_targets=["bravo"],
            eligible_agent_ids=[],
            content="@BRAVO 직접 답해줘",
        )
        everyone = continuous_floor_targets(
            provider_ids=["alpha", "bravo"],
            actor_id="operator-local",
            routed_targets=["alpha", "bravo"],
            eligible_agent_ids=[],
            content="@all 모두 답해줘",
        )

        self.assertEqual(mentioned, ("bravo",))
        self.assertEqual(everyone, ("alpha", "bravo"))

    def test_continuous_floor_returns_empty_when_no_routed_agent_is_eligible(self):
        result = continuous_floor_targets(
            provider_ids=["alpha", "bravo"],
            actor_id="operator-local",
            routed_targets=["alpha", "bravo"],
            eligible_agent_ids=[],
            content="이어가",
        )

        self.assertEqual(result, ())


if __name__ == "__main__":
    unittest.main()
