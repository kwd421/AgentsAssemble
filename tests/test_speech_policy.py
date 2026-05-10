import unittest

from agentsassemble.speech_policy import ROUND_RESPONSE_SCHEMA, ROUND_SPEECH_POLICY


class SpeechPolicyTests(unittest.TestCase):
    def test_policy_separates_visible_speech_from_system_status(self):
        self.assertIn("Do not narrate system status", ROUND_SPEECH_POLICY)
        self.assertIn("I maintained my stance", ROUND_SPEECH_POLICY)
        self.assertIn("Keep status fields in JSON fields", ROUND_SPEECH_POLICY)

    def test_policy_requires_conversational_response_not_research_dump(self):
        self.assertIn("Research is raw material", ROUND_SPEECH_POLICY)
        self.assertIn("Do not dump research notes", ROUND_SPEECH_POLICY)
        self.assertIn("reference at least one previous speaker by name", ROUND_SPEECH_POLICY)
        self.assertIn('"content"', ROUND_RESPONSE_SCHEMA)


if __name__ == "__main__":
    unittest.main()
