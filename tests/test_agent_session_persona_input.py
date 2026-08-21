import tempfile
import unittest
from pathlib import Path

from agentsassemble.persistence.local.room.repository import RoomStore
from agentsassemble.persona_cards import PersonaCard, save_persona_card
from agentsassemble.room.turn_context import build_room_turn_packet


class AgentSessionPersonaInputTests(unittest.TestCase):
    def test_api_session_provider_input_includes_its_selected_bot_card(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_root = Path(directory)
            save_persona_card(
                output_root / "personas" / "night-guide" / "card.json",
                PersonaCard(
                    id="night-guide",
                    display_name="Night Guide",
                    system_prompt="Keep the party together and speak in short lantern metaphors.",
                ),
            )
            store = RoomStore(output_root)
            store.create_room("room-a", label="Night Council")
            store.upsert_participant(
                "room-a",
                {
                    "participant_id": "agent-a",
                    "display_name": "Guide",
                    "participant_type": "agent",
                    "status": "joined",
                },
            )
            store.upsert_session(
                "room-a",
                {
                    "session_id": "session-a",
                    "participant_id": "agent-a",
                    "provider_kind": "deepseek_api",
                    "status": "attached",
                },
            )
            store.upsert_session(
                "room-a",
                {
                    **store.session("room-a", "session-a"),
                    "persona_card_id": "night-guide",
                    "persona_card": {
                        "id": "night-guide",
                        "display_name": "Night Guide",
                        "asset_kind": "card",
                    },
                },
            )
            store.append_event(
                "room-a",
                "message_final",
                participant_id="human",
                content="The bridge is dark.",
            )

            packet = build_room_turn_packet(
                output_root,
                room_id="room-a",
                participant_id="agent-a",
                session_id="session-a",
                instruction="Answer the room.",
            )

        self.assertTrue(packet["persona_context_included"])
        self.assertEqual(packet["persona_card_id"], "night-guide")
        self.assertIn("Night Guide", packet["provider_input"])
        self.assertIn("short lantern metaphors", packet["provider_input"])
        self.assertIn("The bridge is dark.", packet["provider_input"])


if __name__ == "__main__":
    unittest.main()
