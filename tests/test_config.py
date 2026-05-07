import unittest

from agentsassemble.config import load_council_config


class ConfigTests(unittest.TestCase):
    def test_load_demo_council_config(self):
        config = load_council_config()

        self.assertEqual(config.topic, "One Piece admiral strength debate")
        self.assertEqual(config.display_topic, "원피스 3대장 최강자 토론")
        self.assertEqual(config.display_question, "원피스 3대장 중 누가 제일 센가?")
        self.assertEqual([role.id for role in config.roles], ["lore_lawyer", "show_me_the_feats", "fanboard_skeptic"])
        self.assertEqual(config.roles[0].personality["preset"], "pedantic_lore_nerd")
        self.assertIn("dcinside", config.roles[2].source_preferences[0])


if __name__ == "__main__":
    unittest.main()
