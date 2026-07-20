import json
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from agentsassemble.cli import build_parser, main
from agentsassemble.legacy.meeting.support.memory_capsules import REQUIRED_CAPSULE_FILES, memory_capsule_gate_report


def _write_valid_capsule(root: Path, **overrides: object) -> None:
    markdown = {
        "persona.md": "# Persona\nPRIVATE-CONTENT\n",
        "memory_summary.md": "# Memory\nPRIVATE-CONTENT\n",
        "decision_history.md": "# Decisions\nPRIVATE-CONTENT\n",
        "lessons_learned.md": "# Lessons\nPRIVATE-CONTENT\n",
        "handoff.md": "# Handoff\nPRIVATE-CONTENT\n",
    }
    json_docs = {
        "evidence_index.json": {"sources": [{"id": "src-1", "url": "https://example.invalid"}]},
        "permissions.json": {
            "meeting_read": True,
            "lobby_chat": True,
            "official_turn": True,
            "implementation": False,
            "filesystem_write": False,
            "git_write": False,
            "push": False,
            "secrets": False,
        },
        "provenance.json": {
            "capsule_id": "capsule-alpha",
            "source_kind": "manual_export",
            "provider_kind": "memory_pack",
        },
    }
    docs: dict[str, object] = {**markdown, **json_docs, **overrides}
    for name, value in docs.items():
        path = root / name
        if isinstance(value, str):
            path.write_text(value, encoding="utf-8")
        else:
            path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


class MemoryCapsuleGateTests(unittest.TestCase):
    def test_valid_capsule_passes_without_leaking_paths_or_contents(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_valid_capsule(root)

            report = memory_capsule_gate_report(root)

            self.assertEqual(report["status"], "ok")
            self.assertTrue(report["meeting_influence_allowed"])
            self.assertFalse(report["execution_allowed"])
            self.assertEqual(report["capsule_path"], "[redacted]")
            self.assertEqual({item["name"] for item in report["required_files"]}, set(REQUIRED_CAPSULE_FILES))
            serialized = json.dumps(report, ensure_ascii=False)
            self.assertNotIn(temp_dir, serialized)
            self.assertNotIn("PRIVATE-CONTENT", serialized)
            self.assertNotIn("https://example.invalid", serialized)

    def test_missing_required_file_fails_gate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_valid_capsule(root)
            (root / "handoff.md").unlink()

            report = memory_capsule_gate_report(root)

            self.assertEqual(report["status"], "failed")
            self.assertFalse(report["meeting_influence_allowed"])
            checks = {check["id"]: check for check in report["checks"]}
            self.assertEqual(checks["required_files"]["status"], "failed")
            self.assertIn("handoff.md", checks["required_files"]["message"])

    def test_invalid_json_fails_without_leaking_parser_detail_or_content(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_valid_capsule(root)
            (root / "evidence_index.json").write_text('{"token": "SECRET"', encoding="utf-8")

            report = memory_capsule_gate_report(root)

            self.assertEqual(report["status"], "failed")
            checks = {check["id"]: check for check in report["checks"]}
            self.assertEqual(checks["json:evidence_index.json"]["status"], "failed")
            serialized = json.dumps(report, ensure_ascii=False)
            self.assertNotIn(temp_dir, serialized)
            self.assertNotIn("SECRET", serialized)
            self.assertNotIn("Expecting", serialized)

    def test_implementation_permissions_fail_gate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_valid_capsule(root, **{"permissions.json": {"meeting_read": True, "implementation": True}})

            report = memory_capsule_gate_report(root)

            self.assertEqual(report["status"], "failed")
            checks = {check["id"]: check for check in report["checks"]}
            self.assertEqual(checks["permissions_policy"]["status"], "failed")
            self.assertIn("implementation", checks["permissions_policy"]["message"])

    def test_raw_session_dump_extra_file_fails_gate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_valid_capsule(root)
            (root / "session.jsonl").write_text('{"hidden": "provider transcript"}\n', encoding="utf-8")

            report = memory_capsule_gate_report(root)

            self.assertEqual(report["status"], "failed")
            checks = {check["id"]: check for check in report["checks"]}
            self.assertEqual(checks["raw_session_dump"]["status"], "failed")
            self.assertIn("session.jsonl", checks["raw_session_dump"]["message"])

    def test_nested_raw_session_dump_fails_gate_without_leaking_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_valid_capsule(root)
            nested = root / "private-token-folder"
            nested.mkdir()
            (nested / "session.jsonl").write_text('{"hidden": "provider transcript"}\n', encoding="utf-8")

            report = memory_capsule_gate_report(root)

            self.assertEqual(report["status"], "failed")
            checks = {check["id"]: check for check in report["checks"]}
            self.assertEqual(checks["raw_session_dump"]["status"], "failed")
            self.assertIn("session.jsonl", checks["raw_session_dump"]["message"])
            self.assertNotIn("private-token-folder", json.dumps(report, ensure_ascii=False))

    def test_recommended_file_credential_marker_fails_gate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_valid_capsule(root)
            (root / "risk_review.md").write_text("token=SECRET\n", encoding="utf-8")

            report = memory_capsule_gate_report(root)

            self.assertEqual(report["status"], "failed")
            checks = {check["id"]: check for check in report["checks"]}
            self.assertEqual(checks["secret_markers"]["status"], "failed")
            self.assertIn("risk_review.md", checks["secret_markers"]["message"])
            self.assertNotIn("SECRET", json.dumps(report, ensure_ascii=False))

    def test_unknown_permission_names_are_counted_not_echoed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_valid_capsule(root, **{"permissions.json": {"meeting_read": True, "PRIVATE_TOKEN_GRANT": True}})

            report = memory_capsule_gate_report(root)

            self.assertEqual(report["status"], "degraded")
            self.assertFalse(report["meeting_influence_allowed"])
            self.assertEqual(report["permissions"]["unknown_true_count"], 1)
            serialized = json.dumps(report, ensure_ascii=False)
            self.assertNotIn("PRIVATE_TOKEN_GRANT", serialized)

    def test_cli_parses_memory_capsule_gate(self):
        args = build_parser().parse_args(["memory-capsule", "gate", "--path", "capsule", "--json"])

        self.assertEqual(args.command, "memory-capsule")
        self.assertEqual(args.memory_capsule_command, "gate")
        self.assertEqual(args.path, "capsule")
        self.assertTrue(args.as_json)

    def test_cli_memory_capsule_gate_prints_json_report(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_valid_capsule(root)
            stdout = StringIO()

            with patch("sys.stdout", stdout):
                exit_code = main(["memory-capsule", "gate", "--path", str(root), "--json"])

            self.assertEqual(exit_code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["status"], "ok")
            self.assertEqual(payload["capsule_path"], "[redacted]")

    def test_cli_memory_capsule_gate_returns_one_for_failed_report(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_valid_capsule(root)
            (root / "permissions.json").write_text('{"secrets": true}', encoding="utf-8")
            stdout = StringIO()

            with patch("sys.stdout", stdout):
                exit_code = main(["memory-capsule", "gate", "--path", str(root), "--json"])

            self.assertEqual(exit_code, 1)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["status"], "failed")


if __name__ == "__main__":
    unittest.main()
