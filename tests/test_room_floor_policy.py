import unittest

from agentsassemble.room_floor_policy import (
    continuous_floor_targets,
    evaluate_agent_floor_eligibility,
    ordered_floor_target,
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

    def test_ordered_floor_samples_two_and_chooses_the_less_frequent_speaker(self):
        sampled = []

        def sample(values, count):
            sampled.append((list(values), count))
            return ["charlie", "alpha"]

        result = ordered_floor_target(
            provider_ids=["alpha", "bravo", "charlie"],
            actor_id="human",
            direct_targets=[],
            eligible_agent_ids=["alpha", "bravo", "charlie"],
            message_counts={"alpha": 2, "bravo": 0, "charlie": 7},
            random_sample=sample,
        )

        self.assertEqual(sampled, [(["alpha", "bravo", "charlie"], 2)])
        self.assertEqual(result, ("alpha",))

    def test_ordered_floor_direct_mention_bypasses_sampling_and_availability(self):
        result = ordered_floor_target(
            provider_ids=["alpha", "bravo"],
            actor_id="human",
            direct_targets=["bravo"],
            eligible_agent_ids=[],
            message_counts={"alpha": 0, "bravo": 10},
            random_sample=lambda _values, _count: self.fail("mention must not sample"),
        )

        self.assertEqual(result, ("bravo",))

    def test_ordered_floor_can_exclude_the_previous_speaker_from_general_selection(self):
        sampled = []

        def sample(values, count):
            sampled.append((list(values), count))
            return list(values)

        result = ordered_floor_target(
            provider_ids=["alpha", "bravo", "charlie"],
            actor_id="human",
            direct_targets=[],
            eligible_agent_ids=["alpha", "bravo", "charlie"],
            message_counts={"alpha": 0, "bravo": 1, "charlie": 2},
            previous_speaker_id="alpha",
            exclude_previous_speaker=True,
            random_sample=sample,
        )

        self.assertEqual(sampled, [(["bravo", "charlie"], 2)])
        self.assertEqual(result, ("bravo",))

    def test_ordered_floor_keeps_the_previous_speaker_when_no_alternative_is_eligible(self):
        result = ordered_floor_target(
            provider_ids=["alpha", "bravo"],
            actor_id="human",
            direct_targets=[],
            eligible_agent_ids=["alpha"],
            message_counts={"alpha": 1, "bravo": 0},
            previous_speaker_id="alpha",
            exclude_previous_speaker=True,
            random_sample=lambda values, _count: list(values),
        )

        self.assertEqual(result, ("alpha",))

    def test_ordered_floor_direct_mention_overrides_previous_speaker_exclusion(self):
        result = ordered_floor_target(
            provider_ids=["alpha", "bravo"],
            actor_id="human",
            direct_targets=["alpha"],
            eligible_agent_ids=["bravo"],
            message_counts={"alpha": 10, "bravo": 0},
            previous_speaker_id="alpha",
            exclude_previous_speaker=True,
            random_sample=lambda _values, _count: self.fail("mention must not sample"),
        )

        self.assertEqual(result, ("alpha",))

    def test_ordered_floor_uses_the_final_mention_as_the_handoff(self):
        result = ordered_floor_target(
            provider_ids=["dm", "luna", "sonnet"],
            actor_id="dm",
            direct_targets=["luna", "sonnet"],
            eligible_agent_ids=["luna", "sonnet"],
            message_counts={"luna": 1, "sonnet": 0},
        )

        self.assertEqual(result, ("sonnet",))


if __name__ == "__main__":
    unittest.main()
