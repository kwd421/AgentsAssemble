import unittest

from agentsassemble.providers.launch_specs import NativeCliProviderSpec
from agentsassemble.room_routing import route_message_targets


def _providers():
    return {
        "codex": NativeCliProviderSpec("codex", "Codex", ("codex",), default_responder=True),
        "grok": NativeCliProviderSpec("grok", "Grok", ("grok",), default_responder=False),
    }


def _event(content, *, actor_id="human", actor_type="human", relay_depth=0):
    return {
        "content": content,
        "actor": {"participant_id": actor_id, "participant_type": actor_type},
        "relay_depth": relay_depth,
    }


class RoomRoutingPolicyTests(unittest.TestCase):
    def test_mentions_and_all_override_default_responders(self):
        mentioned = route_message_targets(
            _event("@grok answer"),
            _providers(),
            max_agent_relay_depth=2,
        )
        all_agents = route_message_targets(
            _event("@all answer"),
            _providers(),
            max_agent_relay_depth=2,
        )

        self.assertEqual(mentioned.targets, ("grok",))
        self.assertEqual(all_agents.targets, ("codex", "grok"))

    def test_plain_human_message_uses_only_default_responders(self):
        decision = route_message_targets(
            _event("hello room"),
            _providers(),
            max_agent_relay_depth=2,
        )

        self.assertEqual(decision.targets, ("codex",))

    def test_agent_relay_never_targets_self_and_stops_at_depth_limit(self):
        allowed = route_message_targets(
            _event("@all continue", actor_id="codex", actor_type="agent", relay_depth=1),
            _providers(),
            max_agent_relay_depth=2,
        )
        blocked = route_message_targets(
            _event("@all continue", actor_id="codex", actor_type="agent", relay_depth=2),
            _providers(),
            max_agent_relay_depth=2,
        )

        self.assertEqual(allowed.targets, ("grok",))
        self.assertEqual(blocked.targets, ())

    def test_continuous_mode_relays_plain_agent_messages_until_depth_limit(self):
        providers = {
            **_providers(),
            "peer": NativeCliProviderSpec("peer", "Peer", ("peer",), default_responder=True),
        }
        allowed = route_message_targets(
            _event("계속 이야기하자", actor_id="codex", actor_type="agent", relay_depth=2),
            providers,
            max_agent_relay_depth=6,
            relay_agent_messages=True,
        )
        blocked = route_message_targets(
            _event("마지막 발언", actor_id="peer", actor_type="agent", relay_depth=6),
            providers,
            max_agent_relay_depth=6,
            relay_agent_messages=True,
        )
        self.assertEqual(allowed.targets, ("peer",))
        self.assertEqual(blocked.targets, ())


if __name__ == "__main__":
    unittest.main()
