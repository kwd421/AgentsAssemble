import unittest

from agentsassemble.meeting_phases import build_short_reaction


class ShortReactionTests(unittest.TestCase):
    def test_reaction_uses_natural_korean_subject_particle(self):
        reaction = build_short_reaction(
            "round_2",
            [
                {"display_name": "헬창전략가", "role_id": "gym_tactics_bro", "stance_status": "held"},
                {"display_name": "운동장갤러", "role_id": "playground_skeptic", "stance_status": "held"},
            ],
        )

        self.assertNotIn("이/가", reaction["content"])
        self.assertIn("운동장갤러가", reaction["content"])


if __name__ == "__main__":
    unittest.main()
