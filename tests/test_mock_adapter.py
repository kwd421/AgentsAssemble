import unittest

from agentsassemble.providers.adapters.mock import MockAdapter
from agentsassemble.models import ResearchSteering, Role, get_research_depth


class MockAdapterTests(unittest.TestCase):
    def test_mock_round_speaks_from_research_instead_of_dumping_it(self):
        adapter = MockAdapter()
        role = Role("lore_lawyer", "설정충", "Canon Analyst", "canon")
        session = adapter.start_session(role, {"meeting_id": "m1"})
        research = adapter.run_research(role, session, "질문", get_research_depth("smoke"), ResearchSteering())

        message = adapter.run_round(role, session, "round_1", "첫 주장", {"own_research": research})

        self.assertLessEqual(len([sentence for sentence in message["content"].split(".") if sentence.strip()]), 5)
        self.assertNotIn("근거는 " + research["summary"], message["content"])
        self.assertIn("내 판단은", message["content"])
        self.assertIn("불확실", message["content"])


if __name__ == "__main__":
    unittest.main()
