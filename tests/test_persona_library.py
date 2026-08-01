from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agentsassemble.persona_cards import PersonaCard, save_persona_card
from agentsassemble.persona_cards.library import (
    PersonaSelectionError,
    list_persona_assets,
    resolve_persona_selection,
)


class PersonaLibraryTests(unittest.TestCase):
    def test_library_classifies_cards_and_modules_without_exposing_body_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            save_persona_card(
                root / "personas" / "guide" / "card.json",
                PersonaCard(
                    id="guide",
                    display_name="Guide",
                    description="private card body",
                    source={"kind": "ccv3"},
                ),
            )
            save_persona_card(
                root / "personas" / "lore" / "card.json",
                PersonaCard(
                    id="lore",
                    display_name="Lore module",
                    description="private module body",
                    source={"kind": "risu_module"},
                ),
            )

            payload = list_persona_assets(root)

            self.assertEqual(
                [(item["id"], item["asset_kind"]) for item in payload],
                [("guide", "card"), ("lore", "module")],
            )
            self.assertNotIn("private card body", repr(payload))
            self.assertNotIn("private module body", repr(payload))

    def test_selection_allows_api_and_local_providers_but_rejects_subscription_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            save_persona_card(
                root / "personas" / "guide" / "card.json",
                PersonaCard(id="guide", display_name="Guide", source={"kind": "ccv3"}),
            )

            api_selection = resolve_persona_selection(root, "deepseek", "guide")
            local_selection = resolve_persona_selection(root, "lmstudio", "guide")

            self.assertEqual(api_selection["id"], "guide")
            self.assertEqual(local_selection["id"], "guide")
            with self.assertRaises(PersonaSelectionError) as raised:
                resolve_persona_selection(root, "codex", "guide")
            self.assertEqual(raised.exception.code, "persona_provider_unsupported")


if __name__ == "__main__":
    unittest.main()
