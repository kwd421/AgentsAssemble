import json
import base64
import struct
import tempfile
import unittest
import zipfile
import zlib
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from agentsassemble.cli import build_parser, main
from agentsassemble.persona_cards import (
    active_lore_entries,
    import_ccv3_persona,
    import_charx_persona,
    import_risum_persona,
    load_persona_card,
    persona_card_from_ccv3,
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


def _sample_ccv3_card() -> dict[str, object]:
    return {
        "spec": "chara_card_v3",
        "spec_version": "3.0",
        "data": {
            "name": "Tsukishiro Yanagi",
            "description": "Calm local mediator. NSFW_MARKER must stay stored.",
            "system_prompt": "Stay as {{char}}.",
            "personality": "Dry, precise, warm.",
            "scenario": "A quiet planning room.",
            "first_mes": "어서 오세요.",
            "alternate_greetings": ["다른 인사입니다."],
            "group_only_greetings": ["그룹 인사입니다."],
            "mes_example": "{{char}}: 확인했습니다.",
            "post_history_instructions": "Maintain continuity.",
            "creator_notes": "creator private notes",
            "tags": ["test", "yanagi"],
            "creator": "tester",
            "character_version": "1.2",
            "nickname": "Yanagi",
            "source": ["realm:test-card"],
            "creation_date": 1779800000,
            "modification_date": 1779800100,
            "character_book": {
                "scan_depth": 5,
                "token_budget": 700,
                "recursive_scanning": True,
                "extensions": {"risu_fullWordMatching": True},
                "entries": [
                    {
                        "keys": ["Yanagi"],
                        "secondary_keys": ["meeting"],
                        "content": "Yanagi remembers room protocol.",
                        "enabled": True,
                        "insertion_order": 30,
                        "case_sensitive": False,
                        "constant": False,
                        "selective": True,
                        "use_regex": False,
                        "name": "protocol",
                        "priority": 50,
                    },
                    {
                        "keys": ["/Yana.*/"],
                        "content": "Regex lore is preserved but skipped in v1.",
                        "enabled": True,
                        "insertion_order": 40,
                        "constant": False,
                        "selective": False,
                        "use_regex": True,
                        "comment": "regex",
                    },
                ],
            },
            "assets": [
                {"type": "icon", "uri": "ccdefault:", "name": "main", "ext": "png"},
                {"type": "emotion", "uri": "__asset:avatar", "name": "smile", "ext": "png"},
            ],
            "extensions": {
                "risuai": {
                    "triggerscript": [{"script": "do not run"}],
                    "customScripts": [{"script": "do not run regex"}],
                    "lowLevelAccess": True,
                    "risuRealmImportId": "realm-id-1",
                    "defaultVariables": "{{slot::mood}}",
                },
                "vendor_unknown": {"keep": True},
            },
        },
    }


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def _write_fake_png_with_text(path: Path, chunks: dict[str, str]) -> Path:
    data = bytearray(b"\x89PNG\r\n\x1a\n")
    data.extend(_png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)))
    for key, value in chunks.items():
        data.extend(_png_chunk(b"tEXt", key.encode("latin-1") + b"\x00" + value.encode("latin-1")))
    data.extend(_png_chunk(b"IEND", b""))
    path.write_bytes(bytes(data))
    return path


def _ccv3_chunk_value(card: dict[str, object]) -> str:
    return base64.b64encode(json.dumps(card, ensure_ascii=False).encode("utf-8")).decode("ascii")


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
    def test_persona_card_from_ccv3_preserves_core_fields_but_safe_summary_hides_bodies(self):
        card = persona_card_from_ccv3(_sample_ccv3_card(), source_name="yanagi.json")

        self.assertEqual(card.display_name, "Tsukishiro Yanagi")
        self.assertEqual(card.description, "Calm local mediator. NSFW_MARKER must stay stored.")
        self.assertEqual(card.system_prompt, "Stay as {{char}}.")
        self.assertEqual(card.alternate_greetings, ["다른 인사입니다."])
        self.assertEqual(card.group_only_greetings, ["그룹 인사입니다."])
        self.assertEqual(card.post_history_instructions, "Maintain continuity.")
        self.assertEqual(card.extra["nickname"], "Yanagi")
        self.assertEqual(card.lore_settings["scan_depth"], 5)
        self.assertEqual(card.lorebook[0].secondkey, "meeting")
        self.assertTrue(card.lorebook[1].use_regex)
        self.assertEqual(card.ignored_features["trigger"], 1)
        self.assertEqual(card.ignored_features["customScripts"], 1)
        self.assertEqual(card.ignored_features["lowLevelAccess"], 1)
        self.assertNotIn("NSFW_MARKER", json.dumps(card.safe_summary(), ensure_ascii=False))

    def test_import_ccv3_json_writes_full_card_without_printing_lore_body(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            source = temp / "yanagi.json"
            source.write_text(json.dumps(_sample_ccv3_card(), ensure_ascii=False), encoding="utf-8")

            report = import_ccv3_persona(source, output_root=temp / "out")
            card = load_persona_card(report.card_path)

        self.assertEqual(card.display_name, "Tsukishiro Yanagi")
        self.assertEqual(card.lorebook[0].content, "Yanagi remembers room protocol.")
        self.assertEqual(card.extra["extensions"]["vendor_unknown"], {"keep": True})
        self.assertNotIn("NSFW_MARKER", json.dumps(report.to_safe_dict(), ensure_ascii=False))

    def test_import_ccv3_png_prefers_ccv3_chunk_and_copies_embedded_assets(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            png = _write_fake_png_with_text(
                temp / "yanagi.png",
                {
                    "chara": _ccv3_chunk_value({"spec": "old", "data": {"name": "Old"}}),
                    "ccv3": _ccv3_chunk_value(_sample_ccv3_card()),
                    "chara-ext-asset_:avatar": base64.b64encode(b"\x89PNG\r\n\x1a\navatar").decode("ascii"),
                },
            )

            report = import_ccv3_persona(png, output_root=temp / "out")
            card = load_persona_card(report.card_path)

        self.assertEqual(card.display_name, "Tsukishiro Yanagi")
        self.assertEqual(report.asset_count, 2)
        self.assertTrue(any(asset.path.endswith(".png") for asset in card.assets))
        self.assertEqual(card.source["container"], "png")

    def test_import_charx_reads_card_and_embedded_asset_without_fetching_remote_uri(self):
        card = _sample_ccv3_card()
        card["data"]["assets"] = [
            {"type": "emotion", "uri": "embeded://assets/emotion/images/smile.png", "name": "smile", "ext": "png"},
            {"type": "background", "uri": "https://example.com/private.png", "name": "remote", "ext": "png"},
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            charx = temp / "yanagi.charx"
            with zipfile.ZipFile(charx, "w") as archive:
                archive.writestr("card.json", json.dumps(card, ensure_ascii=False))
                archive.writestr("assets/emotion/images/smile.png", b"\x89PNG\r\n\x1a\nsmile")

            report = import_charx_persona(charx, output_root=temp / "out")
            loaded = load_persona_card(report.card_path)

        self.assertEqual(loaded.display_name, "Tsukishiro Yanagi")
        self.assertEqual(report.asset_count, 1)
        self.assertEqual(loaded.ignored_features["remote_asset_uri"], 1)
        self.assertNotIn("example.com/private", json.dumps(report.to_safe_dict(), ensure_ascii=False))

    def test_import_charx_preserves_embedded_module_ignored_features_and_lore_override(self):
        card = _sample_ccv3_card()
        card["data"]["extensions"]["risuai"] = {}
        module = _sample_risu_module()
        module["lorebook"] = [
            {
                "key": "module",
                "content": "Module lore overrides card lore.",
                "alwaysActive": True,
                "useRegex": False,
                "insertorder": 1,
            }
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            rpack_map = _identity_rpack_map(temp / "rpack_map.bin")
            module_file = _write_fake_risum(temp / "module.risum", module)
            charx = temp / "yanagi.charx"
            with patch.dict("os.environ", {"RISUAI_RPACK_MAP": str(rpack_map)}):
                with zipfile.ZipFile(charx, "w") as archive:
                    archive.writestr("card.json", json.dumps(card, ensure_ascii=False))
                    archive.writestr("module.risum", module_file.read_bytes())

                report = import_charx_persona(charx, output_root=temp / "out")
                loaded = load_persona_card(report.card_path)

        self.assertEqual([entry.content for entry in loaded.lorebook], ["Module lore overrides card lore."])
        self.assertEqual(loaded.ignored_features["trigger"], 1)
        self.assertEqual(loaded.ignored_features["cjs"], 1)
        self.assertEqual(loaded.ignored_features["lowLevelAccess"], 1)
        self.assertIn("embedded_module", loaded.ignored_payloads)
        self.assertNotIn("dangerous trigger", json.dumps(report.to_safe_dict(), ensure_ascii=False))

    def test_import_charx_reads_only_referenced_embedded_assets_and_rejects_unsafe_paths(self):
        card = _sample_ccv3_card()
        card["data"]["assets"] = [
            {"type": "emotion", "uri": "embeded://assets/emotion/images/smile.png", "name": "smile", "ext": "png"},
            {"type": "emotion", "uri": "embeded:///absolute.png", "name": "absolute", "ext": "png"},
            {"type": "emotion", "uri": "embeded://assets//bad.png", "name": "empty-segment", "ext": "png"},
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            charx = temp / "yanagi.charx"
            with zipfile.ZipFile(charx, "w") as archive:
                archive.writestr("card.json", json.dumps(card, ensure_ascii=False))
                archive.writestr("assets/emotion/images/smile.png", b"\x89PNG\r\n\x1a\nsmile")
                archive.writestr("unused/huge.bin", b"x" * 1024)

            report = import_charx_persona(charx, output_root=temp / "out")
            loaded = load_persona_card(report.card_path)

        self.assertEqual(report.asset_count, 1)
        self.assertEqual(loaded.assets[0].metadata["name"], "smile")
        self.assertEqual(loaded.ignored_features["unsafe_asset_uri"], 2)

    def test_import_charx_records_unreadable_embedded_module_instead_of_silently_dropping_it(self):
        card = _sample_ccv3_card()
        card["data"]["extensions"]["risuai"] = {}
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            charx = temp / "yanagi.charx"
            with zipfile.ZipFile(charx, "w") as archive:
                archive.writestr("card.json", json.dumps(card, ensure_ascii=False))
                archive.writestr("module.risum", b"not-a-risu-module")

            report = import_charx_persona(charx, output_root=temp / "out")
            loaded = load_persona_card(report.card_path)

        self.assertEqual(loaded.ignored_features["embedded_module_unreadable"], 1)
        self.assertEqual(loaded.ignored_payloads["embedded_module_unreadable"]["reason"], "unreadable")
        self.assertNotIn("not-a-risu-module", json.dumps(report.to_safe_dict(), ensure_ascii=False))

    def test_ccv3_unknown_fields_and_risuai_extensions_are_preserved_without_execution(self):
        card = _sample_ccv3_card()
        card["top_level_unknown"] = {"must": "stay"}
        card["data"]["unknown_data_field"] = {"also": "stay"}
        card["data"]["extensions"]["risuai"]["unrecognizedRisuThing"] = {"keep": True}

        loaded = persona_card_from_ccv3(card, source_name="unknowns.json")

        self.assertEqual(loaded.extra["unknown_card_fields"]["top_level_unknown"], {"must": "stay"})
        self.assertEqual(loaded.extra["unknown_data_fields"]["unknown_data_field"], {"also": "stay"})
        self.assertEqual(loaded.extra["risuai_extensions"]["unrecognizedRisuThing"], {"keep": True})

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
    def test_persona_import_parser_accepts_ccv3_and_charx_options(self):
        ccv3_args = build_parser().parse_args(
            [
                "persona",
                "import-ccv3",
                "--file",
                "persona.png",
                "--output-root",
                "out",
                "--json",
            ]
        )
        charx_args = build_parser().parse_args(
            [
                "persona",
                "import-charx",
                "--file",
                "persona.charx",
                "--output-root",
                "out",
                "--json",
            ]
        )

        self.assertEqual(ccv3_args.persona_command, "import-ccv3")
        self.assertEqual(charx_args.persona_command, "import-charx")
        self.assertTrue(ccv3_args.as_json)
        self.assertTrue(charx_args.as_json)

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

    def test_persona_import_ccv3_command_outputs_safe_report_and_writes_card(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            source = temp / "yanagi.json"
            source.write_text(json.dumps(_sample_ccv3_card(), ensure_ascii=False), encoding="utf-8")
            stdout = StringIO()

            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "persona",
                        "import-ccv3",
                        "--file",
                        str(source),
                        "--output-root",
                        str(temp / "out"),
                        "--json",
                    ]
                )

        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["persona"]["display_name"], "Tsukishiro Yanagi")
        self.assertEqual(payload["lorebook_count"], 2)
        self.assertNotIn("NSFW_MARKER", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
