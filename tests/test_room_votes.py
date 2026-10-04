import unittest

from agentsassemble.room.votes import (
    legacy_vote_summary,
    normalize_vote_definition,
    vote_poll,
    vote_summary,
)


def _canonical_event(
    *,
    event_id: str,
    seq: int,
    kind: str,
    participant_id: str,
    display_name: str,
    vote_id: str = "",
    vote_question: str = "",
    vote_options: list[str] | None = None,
    vote_choice: str = "",
) -> dict[str, object]:
    event: dict[str, object] = {
        "v": 1,
        "id": event_id,
        "seq": seq,
        "created_at": f"2026-01-01T00:00:{seq:02d}+00:00",
        "room_id": "general",
        "type": "message_final",
        "actor": {
            "participant_id": participant_id,
            "participant_type": "human",
        },
        "display_name": display_name,
        "message_kind": kind,
    }
    if vote_id:
        event["vote_id"] = vote_id
    if vote_question:
        event["vote_question"] = vote_question
    if vote_options is not None:
        event["vote_options"] = vote_options
    if vote_choice:
        event["vote_choice"] = vote_choice
    return event


class VoteDefinitionTests(unittest.TestCase):
    def test_definition_normalizes_duplicate_options(self) -> None:
        question, options = normalize_vote_definition(
            " 오늘 게임 뭐할까? ",
            [" 끝말잇기 ", "끝말잇기", "마피아"],
        )

        self.assertEqual(question, "오늘 게임 뭐할까?")
        self.assertEqual(options, ["끝말잇기", "마피아"])

    def test_vote_poll_requires_the_matching_canonical_poll_event(self) -> None:
        poll = _canonical_event(
            event_id="vote-1",
            seq=1,
            kind="vote",
            participant_id="host-1",
            display_name="호스트",
            vote_question="야식?",
            vote_options=["치킨", "피자"],
        )

        self.assertEqual(vote_poll(poll, "vote-1"), poll)
        with self.assertRaises(ValueError):
            vote_poll({**poll, "message_kind": "message"}, "vote-1")
        with self.assertRaises(ValueError):
            vote_poll(poll, "vote-2")


class VoteSummaryTests(unittest.TestCase):
    def _poll_and_casts(
        self,
    ) -> tuple[dict[str, object], list[dict[str, object]], str]:
        vote_id = "vote-1"
        poll = _canonical_event(
            event_id=vote_id,
            seq=1,
            kind="vote",
            participant_id="host-1",
            display_name="호스트",
            vote_question="야식?",
            vote_options=["치킨", "피자"],
        )
        casts = [
            _canonical_event(
                event_id="cast-1",
                seq=2,
                kind="vote_cast",
                participant_id="u-cheolsu",
                display_name="철수",
                vote_id=vote_id,
                vote_choice="치킨",
            ),
            _canonical_event(
                event_id="cast-2",
                seq=3,
                kind="vote_cast",
                participant_id="u-younghee",
                display_name="영희",
                vote_id=vote_id,
                vote_choice="2",
            ),
            _canonical_event(
                event_id="cast-3",
                seq=4,
                kind="vote_cast",
                participant_id="agent-bot",
                display_name="봇",
                vote_id=vote_id,
                vote_choice="피자",
            ),
        ]
        return poll, casts, vote_id

    def test_tallies_count_latest_cast_per_stable_participant(self) -> None:
        poll, casts, vote_id = self._poll_and_casts()
        recast = _canonical_event(
            event_id="cast-4",
            seq=5,
            kind="vote_cast",
            participant_id="u-cheolsu",
            display_name="철수",
            vote_id=vote_id,
            vote_choice="피자",
        )

        summary = vote_summary(
            [poll, *casts, recast],
            vote_id,
            viewer_participant_id="u-cheolsu",
        )

        self.assertEqual(summary["question"], "야식?")
        self.assertEqual(summary["tallies"], {"치킨": 0, "피자": 3})
        self.assertEqual(summary["total_votes"], 3)
        self.assertEqual(summary["own_choice"], "피자")
        self.assertNotIn("voters", summary)
        self.assertNotIn("voter_ids", summary)

    def test_latest_withdrawal_removes_only_that_participants_ballot(self) -> None:
        poll, casts, vote_id = self._poll_and_casts()
        withdrawal = _canonical_event(
            event_id="withdraw-1",
            seq=5,
            kind="vote_withdraw",
            participant_id="u-cheolsu",
            display_name="철수",
            vote_id=vote_id,
        )

        summary = vote_summary(
            [poll, *casts, withdrawal],
            vote_id,
            viewer_participant_id="u-cheolsu",
        )

        self.assertEqual(summary["tallies"], {"치킨": 0, "피자": 2})
        self.assertEqual(summary["total_votes"], 2)
        self.assertEqual(summary["own_choice"], "")

    def test_same_display_names_remain_distinct_voters(self) -> None:
        poll, _casts, vote_id = self._poll_and_casts()
        first = _canonical_event(
            event_id="same-name-1",
            seq=2,
            kind="vote_cast",
            participant_id="guest-a",
            display_name="민지",
            vote_id=vote_id,
            vote_choice="치킨",
        )
        second = _canonical_event(
            event_id="same-name-2",
            seq=3,
            kind="vote_cast",
            participant_id="guest-b",
            display_name="민지",
            vote_id=vote_id,
            vote_choice="피자",
        )

        summary = vote_summary([poll, first, second], vote_id)

        self.assertEqual(summary["total_votes"], 2)
        self.assertEqual(summary["tallies"], {"치킨": 1, "피자": 1})
        self.assertNotIn("민지", repr(summary))
        self.assertNotIn("guest-a", repr(summary))
        self.assertNotIn("guest-b", repr(summary))

    def test_ballot_without_stable_participant_id_is_not_counted(self) -> None:
        poll, _casts, vote_id = self._poll_and_casts()
        unnamed_actor = _canonical_event(
            event_id="missing-id",
            seq=2,
            kind="vote_cast",
            participant_id="",
            display_name="민지",
            vote_id=vote_id,
            vote_choice="치킨",
        )

        summary = vote_summary([poll, unnamed_actor], vote_id)

        self.assertEqual(summary["total_votes"], 0)

    def test_invalid_choice_and_unknown_vote_are_handled(self) -> None:
        poll, casts, vote_id = self._poll_and_casts()
        junk = _canonical_event(
            event_id="invalid-choice",
            seq=5,
            kind="vote_cast",
            participant_id="u-junk",
            display_name="장난",
            vote_id=vote_id,
            vote_choice="99",
        )

        summary = vote_summary([poll, *casts, junk], vote_id)

        self.assertEqual(summary["total_votes"], 3)
        with self.assertRaises(ValueError):
            vote_summary([poll], "missing-vote")

    def test_casts_before_poll_or_for_other_votes_are_ignored(self) -> None:
        poll, casts, vote_id = self._poll_and_casts()
        early = _canonical_event(
            event_id="early",
            seq=0,
            kind="vote_cast",
            participant_id="u-early",
            display_name="먼저",
            vote_id=vote_id,
            vote_choice="치킨",
        )
        stray = _canonical_event(
            event_id="stray",
            seq=5,
            kind="vote_cast",
            participant_id="u-x",
            display_name="딴곳",
            vote_id="other-vote",
            vote_choice="치킨",
        )

        summary = vote_summary([early, stray, poll, *casts], vote_id)

        self.assertEqual(summary["total_votes"], 3)


class RetainedVoteCompatibilityTests(unittest.TestCase):
    def test_legacy_ballots_without_actor_ids_keep_name_based_tallies(self) -> None:
        events = [
            {
                "id": "legacy-vote",
                "kind": "vote",
                "name": "호스트",
                "vote_id": "legacy-vote",
                "vote_question": "야식?",
                "vote_options": ["치킨", "피자"],
            },
            {
                "id": "legacy-cast-1",
                "kind": "vote_cast",
                "name": "민지",
                "vote_id": "legacy-vote",
                "vote_choice": "치킨",
            },
            {
                "id": "legacy-cast-2",
                "kind": "vote_cast",
                "name": "민지",
                "vote_id": "legacy-vote",
                "vote_choice": "피자",
            },
        ]

        summary = legacy_vote_summary(
            events,
            "legacy-vote",
            viewer_participant_id="legacy-name:민지",
        )

        self.assertEqual(summary["tallies"], {"치킨": 0, "피자": 1})
        self.assertEqual(summary["own_choice"], "피자")
        self.assertNotIn("voters", summary)
        self.assertNotIn("voter_ids", summary)


if __name__ == "__main__":
    unittest.main()
