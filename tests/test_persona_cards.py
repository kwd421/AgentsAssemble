import json
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from agentsassemble.cli import build_parser, main
from agentsassemble.persona_cards import (
    active_lore_entries,
    import_risum_persona,
    load_persona_card,
    persona_card_from_risu_module,
    persona_prompt_lines,
    read_risum_module,
)


def _identity_rpack_map(path: Path) -> Path:
    path.write_bytes(bytes(range(256)) + bytes(range(256)))
    return path


def _write_fake_risum(path: Path, module: dict[str, object], *, assets: list[bytes] | None = None) -> Path:
    payload = json.dumps({"type": "risuModule", "module": module}, ensure_ascii=False).encode("utf-8")
    data = bytearray([111, 0])
    data.extend(len(payload).to_bytes(4, "little"))
    data.extend(payload)
    for asset in assets or []:
        data.append(1)
        data.extend(len(asset).to_bytes(4, "little"))
        data.extend(asset)
    data.append(0)
    path.write_bytes(bytes(data))
    return path


def _sample_risu_module() -> dict[str, object]:
    return {
        "id": "risu-module-id",
        "name": "Yanagi Persona Module",
        "description": "Mediator persona overlay.",
        "lorebook": [
            {
                "key": "Yanagi, 야나기",
                "secondkey": "",
                "comment": "keyword lore",
                "content": "Yanagi keeps a calm bureaucratic tone.",
                "alwaysActive": False,
                "selective": False,
                "useRegex": False,
                "insertorder": 20,
            },
            {
                "key": "private",
                "content": "NSFW_MARKER must remain exactly as imported.",
                "alwaysActive": True,
                "useRegex": False,
                "insertorder": 10,
            },
            {
                "key": "Yana.*",
                "content": "Regex-only lore should not activate in v1.",
                "alwaysActive": False,
                "useRegex": True,
                "insertorder": 30,
            },
        ],
        "regex": [{"script": "dangerous replacement"}],
        "trigger": [{"type": "output", "script": "dangerous trigger"}],
        "cjs": "throw new Error('do not run')",
        "lowLevelAccess": True,
        "assets": [{"name": "avatar"}],
    }


class RisuModulePersonaTests(unittest.TestCase):
    def test_read_risum_module_preserves_module_lore_and_asset_payloads(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            rpack_map = _identity_rpack_map(temp / "rpack_map.bin")
            risum = _write_fake_risum(
                temp / "persona.risum",
                _sample_risu_module(),
                assets=[b"\x89PNG\r\n\x1a\navatar"],
            )

            parsed = read_risum_module(risum, rpack_map_path=rpack_map)

        self.assertEqual(parsed.module["name"], "Yanagi Persona Module")
        self.assertEqual(parsed.module["lorebook"][1]["content"], "NSFW_MARKER must remain exactly as imported.")
        self.assertEqual(parsed.asset_payloads, [b"\x89PNG\r\n\x1a\navatar"])

    def test_persona_card_from_risu_module_preserves_content_but_marks_unsafe_features_ignored(self):
        card = persona_card_from_risu_module(_sample_risu_module(), source_name="persona.risum")

        self.assertEqual(card.display_name, "Yanagi Persona Module")
        self.assertEqual(card.description, "Mediator persona overlay.")
        self.assertEqual(card.lorebook[1].content, "NSFW_MARKER must remain exactly as imported.")
        self.assertEqual(card.ignored_features["regex"], 1)
        self.assertEqual(card.ignored_features["trigger"], 1)
        self.assertEqual(card.ignored_features["cjs"], 1)
        self.assertEqual(card.ignored_features["lowLevelAccess"], 1)

    def test_active_lore_uses_always_active_and_literal_keywords_without_running_regex(self):
        card = persona_card_from_risu_module(_sample_risu_module(), source_name="persona.risum")

        lore = active_lore_entries(card, "Yanagi walks into the room.", max_chars=1000)

        self.assertEqual([entry.content for entry in lore], [
            "NSFW_MARKER must remain exactly as imported.",
            "Yanagi keeps a calm bureaucratic tone.",
        ])

        regex_only_lore = active_lore_entries(card, "Yana.* appears literally in this room.", max_chars=1000)
        self.assertEqual([entry.content for entry in regex_only_lore], ["NSFW_MARKER must remain exactly as imported."])

    def test_lore_content_preserves_leading_and_trailing_whitespace_in_card_json(self):
        module = _sample_risu_module()
        module["lorebook"][0]["content"] = "  keep exact lore spacing\n"

        card = persona_card_from_risu_module(module, source_name="persona.risum")

        self.assertEqual(card.lorebook[0].content, "  keep exact lore spacing\n")

    def test_import_risum_persona_writes_full_card_and_assets_without_printing_lore_body(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            rpack_map = _identity_rpack_map(temp / "rpack_map.bin")
            risum = _write_fake_risum(
                temp / "persona.risum",
                _sample_risu_module(),
                assets=[b"\x89PNG\r\n\x1a\navatar"],
            )

            report = import_risum_persona(risum, output_root=temp / "out", rpack_map_path=rpack_map)
            card = load_persona_card(report.card_path)

        self.assertIn("personas", str(report.card_path))
        self.assertEqual(report.asset_count, 1)
        self.assertEqual(card.lorebook[1].content, "NSFW_MARKER must remain exactly as imported.")
        self.assertTrue(card.assets[0].path.endswith(".png"))
        self.assertNotIn("NSFW_MARKER", json.dumps(report.to_safe_dict(), ensure_ascii=False))
        self.assertNotIn("description_preview", report.to_safe_dict()["persona"])

    def test_persona_prompt_lines_include_character_context_but_not_ignored_scripts(self):
        card = persona_card_from_risu_module(_sample_risu_module(), source_name="persona.risum")

        lines = persona_prompt_lines(card, "Yanagi is mentioned in the latest room message.")
        prompt = "\n".join(lines)

        self.assertIn("Play Mode persona card", prompt)
        self.assertIn("Yanagi Persona Module", prompt)
        self.assertIn("Yanagi keeps a calm bureaucratic tone.", prompt)
        self.assertIn("NSFW_MARKER must remain exactly as imported.", prompt)
        self.assertNotIn("dangerous trigger", prompt)
        self.assertNotIn("dangerous replacement", prompt)


class PersonaCliTests(unittest.TestCase):
    def test_persona_import_parser_accepts_risum_options(self):
        args = build_parser().parse_args(
            [
                "persona",
                "import-risum",
                "--file",
                "persona.risum",
                "--output-root",
                "out",
                "--rpack-map",
                "rpack_map.bin",
                "--json",
            ]
        )

        self.assertEqual(args.command, "persona")
        self.assertEqual(args.persona_command, "import-risum")
        self.assertEqual(args.file, "persona.risum")
        self.assertEqual(args.output_root, "out")
        self.assertEqual(args.rpack_map, "rpack_map.bin")
        self.assertTrue(args.as_json)

    def test_persona_import_command_outputs_safe_report_and_writes_card(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            rpack_map = _identity_rpack_map(temp / "rpack_map.bin")
            risum = _write_fake_risum(temp / "persona.risum", _sample_risu_module())
            stdout = StringIO()

            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "persona",
                        "import-risum",
                        "--file",
                        str(risum),
                        "--output-root",
                        str(temp / "out"),
                        "--rpack-map",
                        str(rpack_map),
                        "--json",
                    ]
                )

        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["persona"]["display_name"], "Yanagi Persona Module")
        self.assertEqual(payload["lorebook_count"], 3)
        self.assertNotIn("NSFW_MARKER", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
