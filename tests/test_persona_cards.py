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
    PersonaImportReport,
    render_persona_prompt,
    read_risum_module,
    replace_persona_variables,
    scan_persona_lore,
)


class PersonaCardForTests:
    @staticmethod
    def basic(**overrides):
        from agentsassemble.persona_cards import PersonaCard

        return PersonaCard(
            id=overrides.pop("id", "persona-test"),
            display_name=overrides.pop("display_name", "Tsukishiro Yanagi"),
            **overrides,
        )

    @staticmethod
    def with_lore(entries, **overrides):
        from agentsassemble.persona_cards import PersonaLoreEntry

        lore_settings = overrides.pop("lore_settings", {})
        ignored_payloads = overrides.pop("ignored_payloads", {})
        card = PersonaCardForTests.basic(
            lorebook=[PersonaLoreEntry.from_dict(entry) for entry in entries],
            lore_settings=lore_settings,
            ignored_payloads=ignored_payloads,
            **overrides,
        )
        return card


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

    def test_scan_persona_lore_supports_second_key_priority_budget_and_sticky_state(self):
        card = PersonaCardForTests.with_lore(
            [
                {
                    "key": "always",
                    "content": "Always active lore.",
                    "always_active": True,
                    "insert_order": 5,
                    "priority": 1,
                },
                {
                    "key": "Yanagi",
                    "secondkey": "meeting",
                    "content": "Selective lore wins.",
                    "selective": True,
                    "insert_order": 10,
                    "priority": 90,
                },
                {
                    "key": "quiet",
                    "content": "@@keep_activate_after_match\nSticky lore.",
                    "insert_order": 20,
                    "priority": 80,
                },
                {
                    "key": "huge",
                    "content": "x" * 400,
                    "always_active": True,
                    "insert_order": 30,
                    "priority": 0,
                },
            ],
            lore_settings={"token_budget": 80, "scan_depth": 2},
        )

        first = scan_persona_lore(card, ["Yanagi enters the meeting.", "The room is quiet."], state={})
        second = scan_persona_lore(card, ["No keyword here."], state=first.state)

        self.assertEqual([entry.content for entry in first.entries], [
            "Always active lore.",
            "Selective lore wins.",
            "Sticky lore.",
        ])
        self.assertNotIn("x" * 100, "\n".join(entry.content for entry in first.entries))
        self.assertIn("Sticky lore.", [entry.content for entry in second.entries])
        self.assertEqual(first.state["sticky_lore"]["2"], True)

    def test_scan_persona_lore_honors_case_sensitive_and_ignores_regex_entries(self):
        card = PersonaCardForTests.with_lore(
            [
                {
                    "key": "Yanagi",
                    "content": "Case-sensitive lore.",
                    "case_sensitive": True,
                    "insert_order": 10,
                },
                {
                    "key": "/Yana.*/",
                    "content": "Regex lore should stay out.",
                    "use_regex": True,
                    "insert_order": 20,
                },
            ]
        )

        lower = scan_persona_lore(card, ["yanagi lowercase"], state={})
        exact = scan_persona_lore(card, ["Yanagi exact"], state={})

        self.assertEqual(lower.entries, [])
        self.assertEqual([entry.content for entry in exact.entries], ["Case-sensitive lore."])

    def test_scan_persona_lore_budget_prefers_higher_priority_without_reordering_output(self):
        card = PersonaCardForTests.with_lore(
            [
                {
                    "key": "low",
                    "content": "@@dont_activate_after_match\n" + ("low " * 80),
                    "always_active": True,
                    "insert_order": 1,
                    "priority": 0,
                },
                {
                    "key": "high",
                    "content": "High priority lore.",
                    "always_active": True,
                    "insert_order": 2,
                    "priority": 100,
                },
            ],
            lore_settings={"token_budget": 40},
        )

        scan = scan_persona_lore(card, "anything", state={})

        self.assertEqual([entry.content for entry in scan.entries], ["High priority lore."])
        self.assertEqual(scan.state["cooldown_lore"], {})

    def test_scan_persona_lore_zero_budget_disables_lore_and_invalid_probability_is_ignored(self):
        zero_budget_card = PersonaCardForTests.with_lore(
            [
                {
                    "key": "always",
                    "content": "Should not fit.",
                    "always_active": True,
                }
            ],
            lore_settings={"token_budget": 0},
        )
        invalid_probability_card = PersonaCardForTests.with_lore(
            [
                {
                    "key": "Yanagi",
                    "content": "@@probability maybe\nStill active.",
                    "insert_order": 1,
                }
            ]
        )

        self.assertEqual(scan_persona_lore(zero_budget_card, "Yanagi", state={}).entries, [])
        self.assertEqual(
            [entry.content for entry in scan_persona_lore(invalid_probability_card, "Yanagi", state={}).entries],
            ["Still active."],
        )

    def test_scan_persona_lore_honors_recursive_scanning_flag_and_depth_cap(self):
        entries = []
        for index in range(12):
            entries.append(
                {
                    "key": f"trigger-{index}",
                    "content": f"trigger-{index + 1}",
                    "insert_order": index,
                }
            )
        non_recursive = PersonaCardForTests.with_lore(entries, lore_settings={"scan_depth": 12, "recursive_scanning": False})
        recursive = PersonaCardForTests.with_lore(entries, lore_settings={"scan_depth": 12, "recursive_scanning": True})

        self.assertEqual(
            [entry.content for entry in scan_persona_lore(non_recursive, "trigger-0", state={}).entries],
            ["trigger-1"],
        )
        self.assertEqual(len(scan_persona_lore(recursive, "trigger-0", state={}).entries), 8)

    def test_scan_persona_lore_honors_full_word_matching_and_partial_override(self):
        full_word = PersonaCardForTests.with_lore(
            [
                {
                    "key": "cat",
                    "content": "Full word only.",
                },
                {
                    "key": "dog",
                    "content": "@@match_partial_word\nPartial override.",
                },
            ],
            lore_settings={"full_word_matching": True},
        )

        concat = scan_persona_lore(full_word, "concatenate dogma", state={})
        exact = scan_persona_lore(full_word, "cat dogma", state={})

        self.assertEqual([entry.content for entry in concat.entries], ["Partial override."])
        self.assertEqual([entry.content for entry in exact.entries], ["Full word only.", "Partial override."])

    def test_scan_persona_lore_supports_activation_decorators_and_cooldown_state(self):
        card = PersonaCardForTests.with_lore(
            [
                {
                    "key": "late",
                    "content": "@@activate_only_after 2\nLate lore.",
                    "insert_order": 1,
                },
                {
                    "key": "even",
                    "content": "@@activate_only_every 2\nEven lore.",
                    "insert_order": 2,
                },
                {
                    "key": "once",
                    "content": "@@dont_activate_after_match\nOne-shot lore.",
                    "insert_order": 3,
                },
            ],
            lore_settings={"scan_depth": 2},
        )

        first = scan_persona_lore(card, ["late even once"], state={})
        second = scan_persona_lore(card, ["late even once", "again"], state=first.state)

        self.assertEqual([entry.content for entry in first.entries], ["One-shot lore."])
        self.assertEqual([entry.content for entry in second.entries], ["Late lore.", "Even lore."])
        self.assertEqual(first.state["cooldown_lore"]["2"], True)

    def test_replace_persona_variables_only_replaces_safe_identity_subset(self):
        card = PersonaCardForTests.basic(display_name="Tsukishiro Yanagi")

        rendered = replace_persona_variables(
            "Hello {{char}} <bot> {{user}} {{persona}} {{slot::mood}} {{setvar::x::1}}",
            card,
            user_name="Seinel",
            persona="operator persona",
            variables={"mood": "dry"},
        )

        self.assertIn("Tsukishiro Yanagi", rendered)
        self.assertIn("Seinel", rendered)
        self.assertIn("operator persona", rendered)
        self.assertIn("dry", rendered)
        self.assertIn("{{setvar::x::1}}", rendered)

    def test_persona_card_preserves_operator_approved_speech_style_capsule(self):
        from agentsassemble.persona_cards import PersonaCard

        card = PersonaCard.from_dict(
            {
                "id": "yanagi",
                "display_name": "Tsukishiro Yanagi",
                "description": "RAW_DESCRIPTION_MARKER",
                "personality": "RAW_PERSONALITY_MARKER",
                "speech_style": {
                    "tone": "차분하지만 직설적",
                    "cadence": "짧은 문장, 결론 먼저",
                    "collaboration_style": "반박할 때도 근거를 붙임",
                    "do": ["한국어로 자연스럽게 말함"],
                    "do_not": ["공식 산출물에 역할극 행동 묘사를 넣지 않음"],
                },
            }
        )

        self.assertEqual(card.speech_style["tone"], "차분하지만 직설적")
        self.assertEqual(card.to_dict()["speech_style"]["cadence"], "짧은 문장, 결론 먼저")
        self.assertEqual(card.safe_summary()["speech_style"], {"configured": True, "do": 1, "do_not": 1})

    def test_render_persona_prompt_orders_blocks_without_raw_ignored_payloads(self):
        card = PersonaCardForTests.with_lore(
            [
                {
                    "key": "Yanagi",
                    "content": "Active lore.",
                    "always_active": True,
                    "insert_order": 1,
                }
            ],
            system_prompt="System as {{char}}.",
            description="Description body.",
            personality="Personality body.",
            scenario="Scenario body.",
            example_messages="{{char}}: example",
            first_message="First hello.",
            post_history_instructions="Post history.",
            ignored_payloads={"trigger": [{"script": "dangerous trigger body"}]},
        )

        rendered = render_persona_prompt(
            card,
            recent_messages=["Yanagi arrives."],
            user_name="Seinel",
            mode="on",
            surface="play_speech",
        )
        text = "\n".join(rendered.lines)

        self.assertLess(text.index("System as Tsukishiro Yanagi."), text.index("Description body."))
        self.assertLess(text.index("Scenario body."), text.index("Active lore."))
        self.assertLess(text.index("Active lore."), text.index("Tsukishiro Yanagi: example"))
        self.assertLess(text.index("First hello."), text.index("Post history."))
        self.assertNotIn("dangerous trigger body", text)

    def test_render_persona_prompt_omits_card_bodies_on_artifact_surface(self):
        card = PersonaCardForTests.with_lore(
            [
                {
                    "key": "Yanagi",
                    "content": "RAW_LORE_MARKER",
                    "always_active": True,
                    "insert_order": 1,
                }
            ],
            system_prompt="RAW_SYSTEM_MARKER",
            description="RAW_DESCRIPTION_MARKER",
            personality="RAW_PERSONALITY_MARKER",
            scenario="RAW_SCENARIO_MARKER",
            example_messages="RAW_EXAMPLE_MARKER",
            first_message="RAW_GREETING_MARKER",
            post_history_instructions="RAW_POST_HISTORY_MARKER",
        )

        rendered = render_persona_prompt(
            card,
            recent_messages="Yanagi",
            mode="on",
            surface="artifact",
        )
        text = "\n".join(rendered.lines)

        self.assertIn("artifact surface", text)
        self.assertEqual(rendered.scan.entries, [])
        for marker in (
            "RAW_SYSTEM_MARKER",
            "RAW_DESCRIPTION_MARKER",
            "RAW_PERSONALITY_MARKER",
            "RAW_SCENARIO_MARKER",
            "RAW_LORE_MARKER",
            "RAW_EXAMPLE_MARKER",
            "RAW_GREETING_MARKER",
            "RAW_POST_HISTORY_MARKER",
        ):
            self.assertNotIn(marker, text)

    def test_render_persona_prompt_work_speech_only_uses_safe_capsule(self):
        card = PersonaCardForTests.with_lore(
            [
                {
                    "key": "Yanagi",
                    "content": "RAW_LORE_MARKER",
                    "always_active": True,
                }
            ],
            personality="RAW_PERSONALITY_MARKER",
            scenario="RAW_SCENARIO_MARKER",
            speech_style={
                "tone": "차분하지만 직설적",
                "cadence": "짧은 문장, 결론 먼저",
                "collaboration_style": "반박할 때도 근거를 붙임",
                "do": ["한국어로 자연스럽게 말함"],
                "do_not": ["공식 산출물에 역할극 행동 묘사를 넣지 않음"],
            },
        )

        rendered = render_persona_prompt(
            card,
            recent_messages="Yanagi",
            mode="work_speech_only",
            surface="work_speech",
        )
        text = "\n".join(rendered.lines)

        self.assertIn("Character speech style", text)
        self.assertIn("Tsukishiro Yanagi", text)
        self.assertIn("차분하지만 직설적", text)
        self.assertIn("짧은 문장, 결론 먼저", text)
        self.assertIn("반박할 때도 근거를 붙임", text)
        self.assertIn("한국어로 자연스럽게 말함", text)
        self.assertEqual(rendered.scan.entries, [])
        self.assertNotIn("RAW_PERSONALITY_MARKER", text)
        self.assertNotIn("RAW_SCENARIO_MARKER", text)
        self.assertNotIn("RAW_LORE_MARKER", text)

    def test_render_persona_prompt_uses_selected_first_message_index(self):
        card = PersonaCardForTests.basic(
            first_message="Default greeting.",
            alternate_greetings=["Alt zero.", "Alt one."],
        )

        rendered = render_persona_prompt(
            card,
            first_message_index=2,
            surface="play_speech",
        )
        text = "\n".join(rendered.lines)

        self.assertIn("Alt one.", text)
        self.assertNotIn("Default greeting.", text)

    def test_risu_module_import_preserves_lore_runtime_settings(self):
        module = _sample_risu_module()
        module["scanDepth"] = 4
        module["tokenBudget"] = 123
        module["recursiveScanning"] = True
        module["fullWordMatching"] = True

        card = persona_card_from_risu_module(module, source_name="persona.risum")

        self.assertEqual(
            card.lore_settings,
            {
                "scan_depth": 4,
                "token_budget": 123,
                "recursive_scanning": True,
                "full_word_matching": True,
            },
        )

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

    def test_persona_scan_and_render_parser_accept_runtime_options(self):
        scan_args = build_parser().parse_args(
            [
                "persona",
                "scan",
                "--card",
                "persona.json",
                "--context",
                "Yanagi meeting",
                "--json",
            ]
        )
        render_args = build_parser().parse_args(
            [
                "persona",
                "render",
                "--card",
                "persona.json",
                "--context",
                "Yanagi meeting",
                "--mode",
                "work_speech_only",
                "--surface",
                "artifact",
                "--user",
                "Seinel",
                "--persona",
                "operator",
                "--slot",
                "mood=dry",
                "--json",
            ]
        )

        self.assertEqual(scan_args.persona_command, "scan")
        self.assertEqual(render_args.persona_command, "render")
        self.assertEqual(render_args.mode, "work_speech_only")
        self.assertEqual(render_args.surface, "artifact")
        self.assertEqual(render_args.slot, ["mood=dry"])

    def test_live_agent_persona_smoke_parser_accepts_card_and_mode(self):
        args = build_parser().parse_args(
            [
                "live-agent",
                "persona-smoke",
                "--card",
                "persona.json",
                "--output-root",
                "out",
                "--meeting-id",
                "persona-smoke",
                "--character-mode",
                "work_speech_only",
                "--context",
                "Yanagi enters.",
                "--json",
            ]
        )

        self.assertEqual(args.live_agent_command, "persona-smoke")
        self.assertEqual(args.card, "persona.json")
        self.assertEqual(args.output_root, "out")
        self.assertEqual(args.character_mode, "work_speech_only")

    def test_persona_scan_and_render_commands_show_runtime_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            card_path = temp / "yanagi.json"
            card = PersonaCardForTests.with_lore(
                [
                    {
                        "key": "Yanagi",
                        "content": "Yanagi remembers room protocol.",
                        "insert_order": 1,
                    }
                ],
                system_prompt="Stay as {{char}} for {{user}}.",
            )
            card_path.write_text(json.dumps(card.to_dict(), ensure_ascii=False), encoding="utf-8")
            scan_stdout = StringIO()
            render_stdout = StringIO()

            with patch("sys.stdout", scan_stdout):
                scan_exit = main(
                    [
                        "persona",
                        "scan",
                        "--card",
                        str(card_path),
                        "--context",
                        "Yanagi enters.",
                        "--json",
                    ]
                )
            with patch("sys.stdout", render_stdout):
                render_exit = main(
                    [
                        "persona",
                        "render",
                        "--card",
                        str(card_path),
                        "--context",
                        "Yanagi enters.",
                        "--user",
                        "Seinel",
                        "--json",
                    ]
                )

        self.assertEqual(scan_exit, 0)
        self.assertEqual(render_exit, 0)
        scan_payload = json.loads(scan_stdout.getvalue())
        render_payload = json.loads(render_stdout.getvalue())
        self.assertEqual(scan_payload["active_lore"][0]["content"], "Yanagi remembers room protocol.")
        self.assertIn("Stay as Tsukishiro Yanagi for Seinel.", "\n".join(render_payload["lines"]))

    def test_live_agent_persona_smoke_command_writes_safe_fake_meeting(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            card_path = temp / "yanagi.json"
            card = PersonaCardForTests.with_lore(
                [
                    {
                        "key": "Yanagi",
                        "content": "RAW_LORE_SECRET_MARKER",
                        "insert_order": 1,
                    }
                ],
                description="RAW_CARD_DESCRIPTION_MARKER",
            )
            card_path.write_text(json.dumps(card.to_dict(), ensure_ascii=False), encoding="utf-8")
            stdout = StringIO()

            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "persona-smoke",
                        "--card",
                        str(card_path),
                        "--output-root",
                        str(temp / "out"),
                        "--meeting-id",
                        "persona-smoke",
                        "--context",
                        "Yanagi enters.",
                        "--json",
                    ]
                )

            self.assertEqual(exit_code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["status"], "ok")
            self.assertEqual(payload["persona"]["display_name"], "Tsukishiro Yanagi")
            self.assertEqual(payload["persona_artifact_contract"]["status"], "pass")
            self.assertGreater(payload["persona_artifact_contract"]["artifact_count"], 0)
            self.assertTrue((temp / "out" / "meetings" / "persona-smoke" / "meeting.json").exists())
            meeting = json.loads((temp / "out" / "meetings" / "persona-smoke" / "meeting.json").read_text(encoding="utf-8"))
            self.assertEqual(meeting["persona_artifact_contract"]["status"], "pass")
            self.assertEqual(meeting["event_log"][-1]["kind"], "persona_artifact_contract")
            self.assertNotIn("RAW_LORE_SECRET_MARKER", stdout.getvalue())
            self.assertNotIn("RAW_CARD_DESCRIPTION_MARKER", stdout.getvalue())

    def test_live_agent_persona_smoke_sanitizes_card_id_before_writing_store(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            card_path = temp / "unsafe.json"
            card = PersonaCardForTests.basic(id="../../escaped")
            card_path.write_text(json.dumps(card.to_dict(), ensure_ascii=False), encoding="utf-8")
            stdout = StringIO()

            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "persona-smoke",
                        "--card",
                        str(card_path),
                        "--output-root",
                        str(temp / "out"),
                        "--meeting-id",
                        "persona-smoke",
                        "--json",
                    ]
                )

            self.assertEqual(exit_code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["persona"]["id"], "escaped")
            self.assertTrue((temp / "out" / "persona-smoke" / "persona-smoke" / "personas" / "escaped" / "card.json").exists())
            self.assertFalse((temp / "card.json").exists())
            meeting = json.loads((temp / "out" / "meetings" / "persona-smoke" / "meeting.json").read_text(encoding="utf-8"))
            self.assertEqual(meeting["character_mode"]["agents"][0]["card_id"], "escaped")

    def test_live_agent_persona_smoke_uses_character_mode_card_id_sanitizer(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            card_path = temp / "unicode.json"
            card = PersonaCardForTests.basic(id="야나기")
            card_path.write_text(json.dumps(card.to_dict(), ensure_ascii=False), encoding="utf-8")
            stdout = StringIO()

            with patch("sys.stdout", stdout):
                exit_code = main(
                    [
                        "live-agent",
                        "persona-smoke",
                        "--card",
                        str(card_path),
                        "--output-root",
                        str(temp / "out"),
                        "--meeting-id",
                        "persona-smoke",
                        "--json",
                    ]
                )

            self.assertEqual(exit_code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["persona"]["id"], "persona-smoke-card")
            self.assertEqual(payload["status"], "ok")
            meeting = json.loads((temp / "out" / "meetings" / "persona-smoke" / "meeting.json").read_text(encoding="utf-8"))
            self.assertEqual(meeting["character_mode"]["agents"][0]["card_id"], "persona-smoke-card")

    def test_live_agent_persona_smoke_refuses_to_overwrite_existing_meeting(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            out = temp / "out"
            existing = out / "meetings" / "persona-smoke"
            existing.mkdir(parents=True)
            (existing / "meeting.json").write_text('{"meeting_id":"keep-me"}', encoding="utf-8")
            card_path = temp / "yanagi.json"
            card = PersonaCardForTests.basic(id="yanagi")
            card_path.write_text(json.dumps(card.to_dict(), ensure_ascii=False), encoding="utf-8")
            stderr = StringIO()

            with patch("sys.stderr", stderr):
                exit_code = main(
                    [
                        "live-agent",
                        "persona-smoke",
                        "--card",
                        str(card_path),
                        "--output-root",
                        str(out),
                        "--meeting-id",
                        "persona-smoke",
                        "--json",
                    ]
                )

            self.assertEqual(exit_code, 2)
            self.assertIn("already exists", stderr.getvalue())
            self.assertEqual(json.loads((existing / "meeting.json").read_text(encoding="utf-8"))["meeting_id"], "keep-me")

    def test_persona_safe_report_omits_raw_source_paths_urls_and_tags(self):
        card = PersonaCardForTests.basic(
            tags=["nsfw", "private-tag"],
            source={"kind": "ccv3", "source_name": "/Users/me/private-yana.json", "url": "https://realm.example/private"},
        )
        report = PersonaImportReport(
            card=card,
            card_path=Path("/Users/me/.agentsassemble/personas/yana/card.json"),
            source_path="/Users/me/Downloads/private-yana.json",
        )

        payload_text = json.dumps(report.to_safe_dict(), ensure_ascii=False)

        self.assertIn('"kind": "ccv3"', payload_text)
        self.assertIn('"tag_count": 2', payload_text)
        self.assertNotIn("/Users/me", payload_text)
        self.assertNotIn("realm.example/private", payload_text)
        self.assertNotIn("nsfw", payload_text.lower())
        self.assertNotIn("private-tag", payload_text)

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
