import unittest

from agentsassemble.legacy.meeting.core.phases import compact_live_research_summary


class LiveResearchSummaryTests(unittest.TestCase):
    def test_live_research_summary_keeps_feed_short_without_mutating_archive_summary(self):
        long_summary = (
            "첫 문장은 결론을 말합니다. "
            "둘째 문장은 근거를 조금 더 말합니다. "
            "셋째 문장은 화면에는 필요 없습니다. "
            "넷째 문장도 아카이브에서만 보면 됩니다."
        )

        compact = compact_live_research_summary({"summary": long_summary})

        self.assertLessEqual(len(compact), 160)
        self.assertIn("첫 문장은 결론", compact)
        self.assertIn("둘째 문장은 근거", compact)
        self.assertNotIn("셋째 문장", compact)
        self.assertEqual(
            long_summary,
            (
                "첫 문장은 결론을 말합니다. "
                "둘째 문장은 근거를 조금 더 말합니다. "
                "셋째 문장은 화면에는 필요 없습니다. "
                "넷째 문장도 아카이브에서만 보면 됩니다."
            ),
        )


if __name__ == "__main__":
    unittest.main()
