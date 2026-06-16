from __future__ import annotations

import unittest

from agentsassemble.room_engagement import (
    resolve_engagement,
    room_uses_floor,
    should_reply_to_event,
    should_yield_for_floor,
)


class ResolveEngagementTests(unittest.TestCase):
    def test_free_and_ordered_force_always(self):
        # Both "free" and "ordered" make every agent want to react (humans AND
        # peers); ordered is then spaced out by the floor, not the engagement mode.
        for mode in ("free", "ordered", "FREE", "  ordered  "):
            self.assertEqual(resolve_engagement(mode, "mentioned"), "always")

    def test_quiet_room_is_mentioned(self):
        # quiet (the cheap default) → speak only when @called, ignoring agent default.
        self.assertEqual(resolve_engagement("quiet", "always"), "mentioned")
        self.assertEqual(resolve_engagement("quiet", "human_only"), "mentioned")

    def test_unknown_mode_falls_back_to_agent_default(self):
        self.assertEqual(resolve_engagement("", "human_only"), "human_only")
        self.assertEqual(resolve_engagement("weird", "flow"), "flow")
        self.assertEqual(resolve_engagement("turn", ""), "mentioned")  # legacy → handled upstream

    def test_room_uses_floor_only_for_ordered(self):
        self.assertTrue(room_uses_floor("ordered"))
        for mode in ("free", "quiet", "", "weird"):
            self.assertFalse(room_uses_floor(mode))

    def test_resolved_always_makes_agent_reply_to_a_peer_message(self):
        peer_event = {"message": "let's split the work", "actor_type": "agent", "actor_id": "other-agent"}
        self.assertTrue(should_reply_to_event(resolve_engagement("free", "mentioned"), peer_event, "fable", "Fable"))
        self.assertFalse(should_reply_to_event("mentioned", peer_event, "fable", "Fable"))


class FloorYieldTests(unittest.TestCase):
    def _msg(self, actor_id, *, human=False):
        event = {"kind": "message", "message": "x", "actor_id": actor_id, "name": actor_id}
        event["actor_type"] = "human" if human else "agent"
        return event

    def test_no_double_speak(self):
        # If the last message was mine, I yield (no speaking twice in a row).
        events = [self._msg("a"), self._msg("b"), self._msg("a")]
        self.assertTrue(should_yield_for_floor(events, "a", "A"))

    def test_behind_speaker_does_not_yield(self):
        # b spoke twice, a once → a is behind → a speaks (no yield).
        events = [self._msg("b"), self._msg("a"), self._msg("b")]
        self.assertFalse(should_yield_for_floor(events, "a", "A"))

    def test_ahead_speaker_yields(self):
        # a spoke twice, b once, last is b → a is ahead → a yields, b's turn.
        events = [self._msg("a"), self._msg("a"), self._msg("b")]
        self.assertTrue(should_yield_for_floor(events, "a", "A"))

    def test_tied_speaker_after_peer_may_speak(self):
        # a,b tied and last was b → a not ahead → a speaks (round-robin continues).
        events = [self._msg("a"), self._msg("b"), self._msg("a"), self._msg("b")]
        self.assertFalse(should_yield_for_floor(events, "a", "A"))

    def test_human_message_never_gates_agents(self):
        # A fresh human message → an agent that hasn't spoken is behind → speaks.
        events = [self._msg("a"), self._msg("human-1", human=True)]
        self.assertFalse(should_yield_for_floor(events, "b", "B"))


if __name__ == "__main__":
    unittest.main()
