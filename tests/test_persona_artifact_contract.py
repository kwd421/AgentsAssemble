import json
import tempfile
import unittest
from pathlib import Path

from agentsassemble.legacy.meeting.support.persona_artifact_contract import (
    apply_persona_artifact_contract_report,
    scan_persona_artifact_text,
)
from agentsassemble.persona_cards import PersonaCard, PersonaLoreEntry, save_persona_card


class PersonaArtifactContractTests(unittest.TestCase):
    def test_scan_persona_artifact_text_reports_safe_codes_without_raw_card_text(self):
        card = PersonaCard(
            id="yanagi",
            display_name="Yanagi",
            description="RAW_CARD_DESCRIPTION_MARKER",
            lorebook=[PersonaLoreEntry(key="secret", content="RAW_LORE_SECRET_MARKER")],
            ignored_features={"low_level_access": 1},
        )

        report = scan_persona_artifact_text(
            "{{char}} writes *smiles softly* then leaks RAW_LORE_SECRET_MARKER and NSFW_MARKER low_level_access.",
            surface="decision",
            cards=[card],
        )

        codes = {violation["code"] for violation in report["violations"]}
        self.assertEqual(report["status"], "violation")
        self.assertIn("unreplaced_variable", codes)
        self.assertIn("roleplay_narration", codes)
        self.assertIn("raw_card_text", codes)
        self.assertIn("nsfw_marker", codes)
        self.assertIn("ignored_feature_name", codes)
        serialized = json.dumps(report, ensure_ascii=False)
        self.assertNotIn("RAW_LORE_SECRET_MARKER", serialized)
        self.assertNotIn("RAW_CARD_DESCRIPTION_MARKER", serialized)

    def test_scan_persona_artifact_text_catches_prompt_markers_and_lore_sentences(self):
        card = PersonaCard(
            id="yanagi",
            display_name="Yanagi",
            lorebook=[
                PersonaLoreEntry(
                    key="river",
                    content=(
                        "The first sentence is harmless setup. "
                        "The hidden river password is blue glass under the moon. "
                        "The third sentence is extra context."
                    ),
                )
            ],
        )

        report = scan_persona_artifact_text(
            "Character speech style: Persona id: yanagi. The hidden river password is blue glass under the moon.",
            surface="decision",
            cards=[card],
        )

        codes = {violation["code"] for violation in report["violations"]}
        self.assertEqual(report["status"], "violation")
        self.assertIn("character_badge", codes)
        self.assertIn("raw_card_text", codes)
        self.assertNotIn("blue glass", json.dumps(report, ensure_ascii=False))

    def test_scan_persona_artifact_text_allows_ordinary_markdown_emphasis(self):
        report = scan_persona_artifact_text(
            "This is *critical* for the release.",
            surface="decision",
            cards=[],
        )

        self.assertEqual(report["status"], "pass")

    def test_scan_persona_artifact_text_catches_common_roleplay_actions(self):
        report = scan_persona_artifact_text(
            "*smirks* then *frowns at the report* and *rolls her eyes*.",
            surface="decision",
            cards=[],
        )

        codes = {violation["code"] for violation in report["violations"]}
        self.assertEqual(report["status"], "violation")
        self.assertIn("roleplay_narration", codes)

    def test_scan_persona_artifact_text_allows_emphasized_technical_actions(self):
        report = scan_persona_artifact_text(
            "The review *raises concerns* and *points to* the failing test.",
            surface="decision",
            cards=[],
        )

        self.assertEqual(report["status"], "pass")

    def test_apply_report_scans_artifact_files_and_keeps_report_safe(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_root = Path(temp_dir)
            meeting_dir = output_root / "meetings" / "resident-m1"
            persona_dir = output_root / "personas" / "yanagi"
            shared_dir = meeting_dir / "shared_memory"
            delegate_dir = meeting_dir / "delegate_packets"
            return_dir = meeting_dir / "return_packets"
            shared_dir.mkdir(parents=True)
            delegate_dir.mkdir()
            return_dir.mkdir()
            save_persona_card(
                persona_dir / "card.json",
                PersonaCard(
                    id="yanagi",
                    display_name="Yanagi",
                    lorebook=[
                        PersonaLoreEntry(key="secret", content="RAW_LORE_SECRET_MARKER"),
                        PersonaLoreEntry(key="json-secret", content="JSON_LORE\nSECRET_MARKER"),
                    ],
                    ignored_features={"low_level_access": 1},
                ),
            )
            (meeting_dir / "transcript.md").write_text("Official {{user}} text.\n", encoding="utf-8")
            (meeting_dir / "decision.md").write_text("Decision has RAW_LORE_SECRET_MARKER.\n", encoding="utf-8")
            (meeting_dir / "room-log.md").write_text("*smiles softly* is free chat, not an official artifact.\n", encoding="utf-8")
            (shared_dir / "rolling-summary.md").write_text("Summary has *waves*.\n", encoding="utf-8")
            (delegate_dir / "architect.json").write_text('{"note":"Persona id: yanagi"}\n', encoding="utf-8")
            (return_dir / "architect.json").write_text(
                json.dumps({"note": "Decision has JSON_LORE\nSECRET_MARKER."}, ensure_ascii=False),
                encoding="utf-8",
            )
            (return_dir / "architect.md").write_text("Return packet mentions low_level_access.\n", encoding="utf-8")
            meeting = {
                "meeting_id": "resident-m1",
                "event_log": [],
                "character_mode": {
                    "version": 1,
                    "agents": [
                        {
                            "agent_id": "agent-a",
                            "card_id": "yanagi",
                            "mode": "work_speech_only",
                            "source_path": "personas/yanagi/card.json",
                            "ignored_features": {"low_level_access": 1},
                        }
                    ],
                },
            }

            report = apply_persona_artifact_contract_report(meeting_dir, meeting)

        self.assertEqual(report["status"], "violation")
        self.assertEqual(meeting["persona_artifact_contract"]["status"], "violation")
        self.assertEqual(meeting["event_log"][-1]["kind"], "persona_artifact_contract")
        artifact_paths = {artifact["path"] for artifact in report["artifacts"]}
        self.assertIn("transcript.md", artifact_paths)
        self.assertIn("shared_memory/rolling-summary.md", artifact_paths)
        self.assertIn("delegate_packets/architect.json", artifact_paths)
        self.assertIn("return_packets/architect.md", artifact_paths)
        self.assertIn("return_packets/architect.json", artifact_paths)
        self.assertNotIn("room-log.md", artifact_paths)
        serialized = json.dumps(report, ensure_ascii=False)
        self.assertIn("raw_card_text", serialized)
        self.assertNotIn("RAW_LORE_SECRET_MARKER", serialized)

    def test_apply_report_flags_unavailable_active_card_without_path_leak(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_root = Path(temp_dir)
            meeting_dir = output_root / "meetings" / "resident-m1"
            meeting_dir.mkdir(parents=True)
            (meeting_dir / "decision.md").write_text("Clean decision.\n", encoding="utf-8")
            meeting = {
                "meeting_id": "resident-m1",
                "event_log": [],
                "character_mode": {
                    "version": 1,
                    "agents": [
                        {
                            "agent_id": "agent-a",
                            "card_id": "missing-card",
                            "mode": "on",
                            "source_path": "../outside/card.json",
                        }
                    ],
                },
            }

            report = apply_persona_artifact_contract_report(meeting_dir, meeting)

        self.assertEqual(report["status"], "violation")
        self.assertEqual(report["card_issues"][0]["code"], "persona_card_unavailable")
        serialized = json.dumps(report, ensure_ascii=False)
        self.assertNotIn("../outside", serialized)

    def test_apply_report_ignores_off_mode_persona_ignored_features(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_root = Path(temp_dir)
            meeting_dir = output_root / "meetings" / "resident-m1"
            active_dir = output_root / "personas" / "active"
            meeting_dir.mkdir(parents=True)
            save_persona_card(active_dir / "card.json", PersonaCard(id="active", display_name="Active"))
            (meeting_dir / "decision.md").write_text("Decision mentions low_level_access as plain text.\n", encoding="utf-8")
            meeting = {
                "meeting_id": "resident-m1",
                "event_log": [],
                "character_mode": {
                    "version": 1,
                    "agents": [
                        {
                            "agent_id": "agent-a",
                            "card_id": "active",
                            "mode": "on",
                            "source_path": "personas/active/card.json",
                        },
                        {
                            "agent_id": "agent-b",
                            "card_id": "off-card",
                            "mode": "off",
                            "ignored_features": {"low_level_access": 1},
                        },
                    ],
                },
            }

            report = apply_persona_artifact_contract_report(meeting_dir, meeting)

        self.assertEqual(report["status"], "pass")
