import unittest

from agentsassemble.meeting_events import LobbyEvent
from agentsassemble.room_engagement import should_reply_to_event
from agentsassemble.room_votes import vote_summary


def _event(payload):
    return LobbyEvent.from_payload(payload).to_dict()


class VoteEventModelTests(unittest.TestCase):
    def test_vote_event_gets_vote_id_and_readable_announcement(self):
        event = _event(
            {
                "name": "호스트",
                "kind": "vote",
                "vote_question": "오늘 게임 뭐할까?",
                "vote_options": ["끝말잇기", "마피아", "퀴즈"],
            }
        )
        self.assertEqual(event["kind"], "vote")
        self.assertEqual(event["vote_id"], event["id"])
        self.assertEqual(event["vote_options"], ["끝말잇기", "마피아", "퀴즈"])
        self.assertIn("📊 투표: 오늘 게임 뭐할까?", event["message"])
        self.assertIn("1. 끝말잇기", event["message"])

    def test_vote_without_two_options_downgrades_to_message(self):
        event = _event({"name": "호스트", "kind": "vote", "vote_question": "?", "vote_options": ["하나"]})
        self.assertEqual(event["kind"], "message")
        self.assertNotIn("vote_id", event)

    def test_vote_cast_requires_vote_id_and_choice(self):
        ok = _event({"name": "유저", "kind": "vote_cast", "vote_id": "v1", "vote_choice": "마피아"})
        self.assertEqual(ok["kind"], "vote_cast")
        self.assertEqual(ok["message"], "🗳️ 마피아")
        broken = _event({"name": "유저", "kind": "vote_cast", "vote_choice": "마피아"})
        self.assertEqual(broken["kind"], "message")

    def test_vote_options_deduplicate_and_cap(self):
        event = _event(
            {
                "name": "호스트",
                "kind": "vote",
                "vote_question": "Q",
                "vote_options": ["A", "a", "B"] + [f"opt{i}" for i in range(20)],
            }
        )
        self.assertEqual(event["vote_options"][:2], ["A", "B"])
        self.assertEqual(len(event["vote_options"]), 10)

    def test_agents_never_chat_reply_to_ballots(self):
        ballot = _event({"name": "유저", "kind": "vote_cast", "vote_id": "v1", "vote_choice": "1"})
        for mode in ("always", "mentioned", "human_only", "flow"):
            self.assertFalse(should_reply_to_event(mode, ballot, "agent-a", "Agent A"))


class VoteSummaryTests(unittest.TestCase):
    def _poll_and_casts(self):
        poll = _event(
            {
                "name": "호스트",
                "actor_id": "",
                "kind": "vote",
                "vote_question": "야식?",
                "vote_options": ["치킨", "피자"],
            }
        )
        vote_id = poll["vote_id"]
        casts = [
            _event({"name": "철수", "actor_id": "u-cheolsu", "kind": "vote_cast", "vote_id": vote_id, "vote_choice": "치킨"}),
            _event({"name": "영희", "actor_id": "u-younghee", "kind": "vote_cast", "vote_id": vote_id, "vote_choice": "2"}),
            _event({"name": "봇", "actor_id": "agent-bot", "kind": "vote_cast", "vote_id": vote_id, "vote_choice": "피자"}),
        ]
        return poll, casts, vote_id

    def test_tallies_count_latest_cast_per_voter(self):
        poll, casts, vote_id = self._poll_and_casts()
        recast = _event(
            {"name": "철수", "actor_id": "u-cheolsu", "kind": "vote_cast", "vote_id": vote_id, "vote_choice": "피자"}
        )
        summary = vote_summary([poll, *casts, recast], vote_id)
        self.assertEqual(summary["question"], "야식?")
        self.assertEqual(summary["tallies"], {"치킨": 0, "피자": 3})
        self.assertEqual(summary["total_votes"], 3)
        self.assertIn("철수", summary["voters"]["피자"])

    def test_numeric_choice_resolves_to_option(self):
        poll, casts, vote_id = self._poll_and_casts()
        summary = vote_summary([poll, *casts], vote_id)
        self.assertEqual(summary["tallies"], {"치킨": 1, "피자": 2})

    def test_invalid_choice_and_unknown_vote_are_handled(self):
        poll, casts, vote_id = self._poll_and_casts()
        junk = _event(
            {"name": "장난", "actor_id": "u-junk", "kind": "vote_cast", "vote_id": vote_id, "vote_choice": "99"}
        )
        summary = vote_summary([poll, *casts, junk], vote_id)
        self.assertEqual(summary["total_votes"], 3)
        with self.assertRaises(ValueError):
            vote_summary([poll], "missing-vote")

    def test_casts_before_poll_or_other_votes_ignored(self):
        poll, casts, vote_id = self._poll_and_casts()
        stray = _event(
            {"name": "딴곳", "actor_id": "u-x", "kind": "vote_cast", "vote_id": "other-vote", "vote_choice": "치킨"}
        )
        summary = vote_summary([stray, poll, *casts], vote_id)
        self.assertEqual(summary["total_votes"], 3)


if __name__ == "__main__":
    unittest.main()
