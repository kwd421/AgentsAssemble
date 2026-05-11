import unittest
from types import SimpleNamespace

from agentsassemble.meeting_phases import compact_spoken_message, run_debate_phase
from agentsassemble.models import CouncilConfig, MeetingRound, Role, RoundTurnControl


class RecordingRoundAdapter:
    def __init__(self):
        self.calls = []

    def run_round(self, role, session, round_name, prompt, public_context):
        self.calls.append((role.id, public_context["current_turn"]["turn_index"]))
        return {
            "role_id": role.id,
            "display_name": role.display_name,
            "round": round_name,
            "position": role.id,
            "content": f"{role.display_name} speaks.",
            "confidence": "medium",
        }


class DebateTurnControlTests(unittest.TestCase):
    def test_selected_role_turn_control_uses_declared_speaker_order(self):
        roles = [
            Role("role_a", "A", "a lens", "a focus"),
            Role("role_b", "B", "b lens", "b focus"),
            Role("role_c", "C", "c lens", "c focus"),
        ]
        round_definition = MeetingRound(
            "round_1",
            "Round 1",
            "Round 1",
            "Speak in selected order.",
            "own_research",
            turn_control=RoundTurnControl(selection="selected_roles", speaker_role_ids=["role_c", "role_a"]),
        )
        config = CouncilConfig("topic", "topic", "question", "question", roles, rounds=[round_definition])
        adapters = {role.id: RecordingRoundAdapter() for role in roles}
        resolved_agents = {role.id: SimpleNamespace(adapter=adapters[role.id]) for role in roles}
        sessions = {role.id: {"role_id": role.id} for role in roles}
        research_records = [{"role_id": role.id, "summary": role.id} for role in roles]

        rounds = run_debate_phase(config, sessions, resolved_agents, research_records, {}, lambda _: None)

        self.assertEqual([message["role_id"] for message in rounds[0]["messages"]], ["role_c", "role_a"])
        self.assertEqual([message["turn_id"] for message in rounds[0]["messages"]], ["round_1:0:role_c", "round_1:1:role_a"])
        self.assertEqual(rounds[0]["turn_control"]["skipped_role_ids"], ["role_b"])
        self.assertEqual(adapters["role_c"].calls, [("role_c", 0)])
        self.assertEqual(adapters["role_a"].calls, [("role_a", 1)])
        self.assertEqual(adapters["role_b"].calls, [])

    def test_compact_spoken_message_caps_research_dumps(self):
        content = " ".join([f"{index}번째 근거입니다." for index in range(1, 12)])

        compact = compact_spoken_message(content)

        self.assertLessEqual(len([part for part in compact.split(".") if part.strip()]), 6)
        self.assertLessEqual(len(compact), 560)


if __name__ == "__main__":
    unittest.main()
