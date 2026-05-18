import unittest

from agentsassemble.live_agent_rounds import (
    build_official_round_turns,
    completed_official_round_ids,
    remaining_official_round_ids,
    template_round_ids,
)


def _meeting() -> dict[str, object]:
    return {
        "meeting_id": "m1",
        "roles": [
            {"id": "architect", "display_name": "Architect"},
            {"id": "critic", "display_name": "Critic"},
        ],
        "meeting_template": {
            "rounds": [
                {
                    "id": "round_1",
                    "instruction": "Template instruction",
                    "turn_control": {"selection": "all_roles"},
                },
                {
                    "id": "round_2",
                    "instruction": "Selected instruction",
                    "turn_control": {"selection": "selected_roles", "speaker_role_ids": ["critic"]},
                },
            ]
        },
        "agent_bindings": [
            {"role_id": "architect", "agent_id": "agent-a"},
            {"role_id": "critic", "agent_id": "agent-b"},
        ],
    }


def _live_agents() -> list[dict[str, object]]:
    return [
        {"agent_id": "agent-a", "display_name": "Agent A", "meeting_id": "m1"},
        {"agent_id": "agent-b", "display_name": "Agent B", "meeting_id": "m1"},
    ]


class LiveAgentRoundTurnBuilderTests(unittest.TestCase):
    def test_builds_all_role_round_in_meeting_role_order(self):
        result = build_official_round_turns(
            _meeting(),
            _live_agents(),
            meeting_id="m1",
            round_id="round_1",
            instruction="Operator instruction",
        )

        self.assertEqual(result["round_id"], "round_1")
        self.assertEqual(result["role_ids"], ["architect", "critic"])
        self.assertEqual(
            result["turns"],
            [
                {
                    "agent_id": "agent-a",
                    "role_id": "architect",
                    "display_name": "Architect",
                    "content": "Operator instruction",
                    "turn_id": "round_1:0:architect",
                    "turn_index": 0,
                },
                {
                    "agent_id": "agent-b",
                    "role_id": "critic",
                    "display_name": "Critic",
                    "content": "Operator instruction",
                    "turn_id": "round_1:1:critic",
                    "turn_index": 1,
                },
            ],
        )

    def test_uses_template_selected_roles_and_instruction_without_override(self):
        result = build_official_round_turns(
            _meeting(),
            _live_agents(),
            meeting_id="m1",
            round_id="round_2",
        )

        self.assertEqual(result["role_ids"], ["critic"])
        self.assertEqual(result["turns"][0]["agent_id"], "agent-b")
        self.assertEqual(result["turns"][0]["content"], "Selected instruction")
        self.assertEqual(result["turns"][0]["turn_id"], "round_2:0:critic")

    def test_role_override_preserves_explicit_order(self):
        result = build_official_round_turns(
            _meeting(),
            _live_agents(),
            meeting_id="m1",
            round_id="round_1",
            role_ids=["critic", "architect"],
        )

        self.assertEqual(result["role_ids"], ["critic", "architect"])
        self.assertEqual([turn["agent_id"] for turn in result["turns"]], ["agent-b", "agent-a"])
        self.assertEqual([turn["turn_id"] for turn in result["turns"]], ["round_1:0:critic", "round_1:1:architect"])

    def test_rejects_missing_bound_live_agent(self):
        with self.assertRaisesRegex(ValueError, "Live agent agent-b was not found"):
            build_official_round_turns(
                _meeting(),
                [{"agent_id": "agent-a", "meeting_id": "m1"}],
                meeting_id="m1",
                round_id="round_1",
            )

    def test_rejects_duplicate_role_override(self):
        with self.assertRaisesRegex(ValueError, "Duplicate official round role"):
            build_official_round_turns(
                _meeting(),
                _live_agents(),
                meeting_id="m1",
                round_id="round_1",
                role_ids=["critic", "critic"],
            )

    def test_template_round_ids_preserve_template_order(self):
        self.assertEqual(template_round_ids(_meeting()), ["round_1", "round_2"])

    def test_completed_official_round_ids_reads_live_state_progress(self):
        meeting = {
            **_meeting(),
            "debate_rounds": [
                {"id": "round_1", "status": "answered"},
                {"round": "round_2", "status": "answered"},
                {"id": "draft_round", "status": "draft"},
                {"id": "", "status": "answered"},
            ],
        }

        self.assertEqual(completed_official_round_ids(meeting), {"round_1", "round_2"})

    def test_remaining_official_round_ids_skip_completed_and_apply_bound(self):
        meeting = {
            **_meeting(),
            "debate_rounds": [{"id": "round_1", "status": "answered"}],
        }

        self.assertEqual(remaining_official_round_ids(meeting), ["round_2"])
        self.assertEqual(remaining_official_round_ids(meeting, max_rounds=1), ["round_2"])
        self.assertEqual(remaining_official_round_ids({**meeting, "debate_rounds": [{"id": "round_1", "status": "draft"}]}), ["round_1", "round_2"])
        self.assertEqual(
            remaining_official_round_ids(
                {**meeting, "debate_rounds": [{"id": "round_1", "status": "answered"}, {"id": "round_2", "status": "answered"}]}
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main()
