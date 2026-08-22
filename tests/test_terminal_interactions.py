import unittest

from agentsassemble.providers.runtime_contracts import AdapterContractError
from agentsassemble.providers.terminal_interactions import (
    AntigravityRoomPortalInteraction,
    is_safe_room_portal_command,
)


class AntigravityRoomPortalInteractionTests(unittest.TestCase):
    def test_search_and_collaboration_commands_are_approved_without_shell_expansion(self):
        policy = AntigravityRoomPortalInteraction()
        policy.begin_turn()

        for command in (
            "agentsassemble-room participants",
            "agentsassemble-room search 'deployment failure' all",
            "agentsassemble-room search-context lobby event-42",
            "agentsassemble-room vote-create 'Which route?' '[\"north\",\"south\"]' 30",
            "agentsassemble-room vote-cast vote-1 north",
            "agentsassemble-room vote-summary vote-1",
            "agentsassemble-room choose '[\"north\",\"south\"]'",
        ):
            with self.subTest(command=command):
                output = (
                    f"Requesting permission for: {command}\n"
                    "Do you want to proceed?"
                ).encode()
                self.assertEqual(policy.response_for(output), b"\x1b[B\r")

        self.assertFalse(
            is_safe_room_portal_command(
                "agentsassemble-room search \"$HOME\" all"
            )
        )
        self.assertFalse(
            is_safe_room_portal_command(
                "agentsassemble-room search 'deployment failure' all && whoami"
            )
        )

    def test_exact_room_read_receives_one_time_terminal_approval(self):
        policy = AntigravityRoomPortalInteraction()
        policy.begin_turn()
        prompt = b"\n".join(
            [
                b"Requesting permission for:",
                b"   agentsassemble-room read",
                "🔓 Allow sandbox bypass for command execution?".encode(),
                b"> 1. Yes",
                b"  2. Yes, and always allow in this conversation for commands that start with 'agentsassemble-room'",
            ]
        )

        self.assertEqual(policy.response_for(prompt), b"\x1b[B\r")
        self.assertEqual(policy.response_for(prompt), b"")
        self.assertEqual(policy.describe()["room_portal_permission_approval_count"], 1)

    def test_bounded_room_dice_roll_receives_terminal_approval(self):
        policy = AntigravityRoomPortalInteraction()
        policy.begin_turn()
        prompt = b"\n".join(
            [
                b"Requesting permission for:",
                b"   agentsassemble-room roll '1d20+4'",
                b"Do you want to proceed?",
            ]
        )

        self.assertEqual(policy.response_for(prompt), b"\x1b[B\r")

        policy.begin_turn()
        unsafe_prompt = b"\n".join(
            [
                b"Requesting permission for:",
                b"   agentsassemble-room roll '1d20+4' && whoami",
                b"Do you want to proceed?",
            ]
        )
        with self.assertRaises(AdapterContractError):
            policy.response_for(unsafe_prompt)

        policy.begin_turn()
        unbounded_prompt = b"\n".join(
            [
                b"Requesting permission for:",
                b"   agentsassemble-room roll 999d9999",
                b"Do you want to proceed?",
            ]
        )
        with self.assertRaises(AdapterContractError):
            policy.response_for(unbounded_prompt)

    def test_shell_chaining_is_rejected_instead_of_receiving_terminal_approval(self):
        policy = AntigravityRoomPortalInteraction()
        policy.begin_turn()
        prompt = b"\n".join(
            [
                b"Requesting permission for:",
                b"   agentsassemble-room read && rm -rf .",
                b"Do you want to proceed?",
            ]
        )

        with self.assertRaises(AdapterContractError) as raised:
            policy.response_for(prompt)

        self.assertEqual(raised.exception.code, "unexpected_provider_permission_request")
        self.assertEqual(policy.describe()["room_portal_permission_rejection_count"], 1)

    def test_multiline_command_cannot_extend_an_approved_room_command(self):
        policy = AntigravityRoomPortalInteraction()
        policy.begin_turn()
        prompt = b"\n".join(
            [
                b"Requesting permission for:",
                b"   agentsassemble-room speak 'Approved room message.'",
                b"   whoami",
                b"Do you want to proceed?",
            ]
        )

        with self.assertRaises(AdapterContractError) as raised:
            policy.response_for(prompt)

        self.assertEqual(raised.exception.code, "unexpected_provider_permission_request")
        self.assertEqual(policy.describe()["room_portal_permission_approval_count"], 0)

    def test_markdown_is_safe_only_inside_single_quoted_room_message(self):
        policy = AntigravityRoomPortalInteraction()
        policy.begin_turn()
        safe_prompt = b"\n".join(
            [
                b"Requesting permission for:",
                b"   agentsassemble-room speak 'The `yellow block` looks like an overlay.'",
                b"Do you want to proceed?",
            ]
        )

        self.assertEqual(policy.response_for(safe_prompt), b"\x1b[B\r")

        policy.begin_turn()
        unsafe_prompt = b"\n".join(
            [
                b"Requesting permission for:",
                b'   agentsassemble-room speak "The `yellow block` looks like an overlay."',
                b"Do you want to proceed?",
            ]
        )
        with self.assertRaises(AdapterContractError):
            policy.response_for(unsafe_prompt)

    def test_targeted_room_publication_requires_a_safe_agent_id(self):
        policy = AntigravityRoomPortalInteraction()
        safe_prompt = b"\n".join(
            [
                b"Requesting permission for:",
                b"   agentsassemble-room speak-to sonnet 'Your turn.'",
                b"Do you want to proceed?",
            ]
        )

        self.assertEqual(policy.response_for(safe_prompt), b"\x1b[B\r")

        policy.begin_turn()
        unsafe_prompt = b"\n".join(
            [
                b"Requesting permission for:",
                b"   agentsassemble-room speak-to ../sonnet 'Your turn.'",
                b"Do you want to proceed?",
            ]
        )
        with self.assertRaises(AdapterContractError):
            policy.response_for(unsafe_prompt)

    def test_truncated_room_message_is_rejected_instead_of_guessing_hidden_text(self):
        policy = AntigravityRoomPortalInteraction()
        policy.begin_turn()
        prompt = b"\n".join(
            [
                b"Requesting permission for:",
                b'   agentsassemble-room speak "The **red door** is marked [17]. Its clock',
                b'   face is cracked (at midnight), and this is',
                "   ⋯ (2 lines hidden)".encode(),
                b"Do you want to proceed?",
            ]
        )

        with self.assertRaises(AdapterContractError) as raised:
            policy.response_for(prompt)

        self.assertEqual(raised.exception.code, "unexpected_provider_permission_request")
        self.assertEqual(policy.describe()["room_portal_permission_rejection_count"], 1)


if __name__ == "__main__":
    unittest.main()
