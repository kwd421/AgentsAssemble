from __future__ import annotations

import unittest

from agentsassemble.room_engagement import resolve_engagement, should_reply_to_event


class ResolveEngagementTests(unittest.TestCase):
    def test_free_room_forces_always(self):
        # A free-flow room makes every agent reply to everyone (humans AND each
        # other), regardless of the per-agent default — bounded later by
        # chain-depth/dedup/human-priority, not by engagement mode.
        for default in ("mentioned", "human_only", "watch", "manual", "flow", "moderator_called"):
            self.assertEqual(resolve_engagement("free", default), "always")
        self.assertEqual(resolve_engagement("FREE", "mentioned"), "always")
        self.assertEqual(resolve_engagement("  free  ", "mentioned"), "always")

    def test_turn_room_keeps_agent_default(self):
        self.assertEqual(resolve_engagement("turn", "mentioned"), "mentioned")
        self.assertEqual(resolve_engagement("turn", "human_only"), "human_only")
        # Unknown / empty conversation modes are treated as turn-based.
        self.assertEqual(resolve_engagement("", "human_only"), "human_only")
        self.assertEqual(resolve_engagement("weird", "flow"), "flow")
        # Missing agent default falls back to "mentioned" (matches predicate default).
        self.assertEqual(resolve_engagement("turn", ""), "mentioned")

    def test_resolved_always_makes_agent_reply_to_a_peer_message(self):
        # End-to-end: free room → "always" → an agent replies to another agent's
        # message even without a mention (which "mentioned" would have skipped).
        peer_event = {"message": "let's split the work", "actor_type": "agent", "actor_id": "other-agent"}
        resolved = resolve_engagement("free", "mentioned")
        self.assertTrue(should_reply_to_event(resolved, peer_event, "fable", "Fable"))
        # Same event under the turn default would NOT trigger a reply.
        self.assertFalse(should_reply_to_event("mentioned", peer_event, "fable", "Fable"))


if __name__ == "__main__":
    unittest.main()
