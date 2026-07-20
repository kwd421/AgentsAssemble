import json
import tempfile
import unittest
from pathlib import Path

from agentsassemble.legacy.meeting.support.task_scope_report import (
    build_task_scope_report,
    render_task_scope_report_markdown,
    write_task_scope_report,
)
from agentsassemble.legacy.meeting.support.artifact_packets import build_return_packet
from agentsassemble.legacy.meeting.support.artifacts import write_public_artifacts


def _meeting_with_tasks(tasks: dict[str, str]) -> dict[str, object]:
    return {
        "meeting_id": "scope-m1",
        "roles": [
            {"id": "architect", "display_name": "Architect"},
            {"id": "critic", "display_name": "Critic"},
            {"id": "implementer", "display_name": "Implementer"},
        ],
        "moderator_synthesis": {"tasks": tasks},
        "artifacts": {},
    }


class TaskScopeReportTests(unittest.TestCase):
    def test_build_report_detects_exact_file_overlap_across_roles(self):
        report = build_task_scope_report(
            _meeting_with_tasks(
                {
                    "architect": "Review agentsassemble/gui.py and docs/live-agent-ops.md.",
                    "critic": "Check tests around agentsassemble/gui.py before implementation.",
                    "implementer": "Own agentsassemble/live_agent_runner.py.",
                }
            ),
            now_iso="2026-05-29T00:00:00+00:00",
        )

        self.assertEqual(report["summary"], "scope_overlap_evidence")
        self.assertEqual(report["overlap_count"], 1)
        self.assertEqual(
            report["overlaps"],
            [
                {
                    "kind": "file",
                    "token": "agentsassemble/gui.py",
                    "role_ids": ["architect", "critic"],
                    "display_names": ["Architect", "Critic"],
                }
            ],
        )
        self.assertEqual(report["candidate_count_total"], 4)
        self.assertFalse(report["advisory"]["implementation_approval"])
        self.assertFalse(report["advisory"]["filesystem_write"])

    def test_build_report_drops_absolute_paths_urls_and_plain_prose(self):
        task = (
            "한국어 설명만 있고 /Users/seinel/secret.py 와 ~/private/file.py, "
            "C:\\secret\\file.py, https://example.com/src/foo.py, i.e. 같은 토큰은 버린다. "
            "남기는 것은 src/foo.py 뿐이다."
        )
        meeting = _meeting_with_tasks({"architect": task, "critic": "No file scope.", "implementer": ""})
        meeting["roles"][0]["display_name"] = "/Users/seinel/private-name"
        report = build_task_scope_report(meeting, now_iso="2026-05-29T00:00:00+00:00")
        encoded = json.dumps(report, ensure_ascii=False)

        self.assertIn("src/foo.py", encoded)
        self.assertNotIn("/Users/seinel/secret.py", encoded)
        self.assertNotIn("/Users/seinel/private-name", encoded)
        self.assertNotIn("~/private/file.py", encoded)
        self.assertNotIn("C:\\secret\\file.py", encoded)
        self.assertNotIn("https://example.com/src/foo.py", encoded)
        self.assertNotIn("i.e.", encoded)
        self.assertEqual(report["overlap_count"], 0)

    def test_build_report_drops_dot_segment_paths_from_text_and_structured_scope(self):
        meeting = _meeting_with_tasks(
            {
                "architect": "Reject ../secret.py src/../secret.py ./local.py but keep safe/file.py",
                "critic": "Review safe/file.py",
                "implementer": "",
            }
        )
        meeting["moderator_synthesis"]["task_scope"] = {
            "implementer": {
                "files": ["../other.py", "src/../other.py", "./also.py", "safe/other.py"],
            }
        }

        report = build_task_scope_report(meeting, now_iso="2026-05-29T00:00:00+00:00")
        encoded = json.dumps(report, ensure_ascii=False)

        self.assertIn("safe/file.py", encoded)
        self.assertIn("safe/other.py", encoded)
        self.assertNotIn("../secret.py", encoded)
        self.assertNotIn("src/../secret.py", encoded)
        self.assertNotIn("./local.py", encoded)
        self.assertNotIn("../other.py", encoded)
        self.assertNotIn("src/../other.py", encoded)
        self.assertNotIn("./also.py", encoded)

    def test_build_report_drops_scheme_less_url_like_paths(self):
        meeting = _meeting_with_tasks(
            {
                "architect": (
                    "Reject github.com/org/repo/src/foo.py, github.com./org/repo/src/bar.py, "
                    "www.example.com/path/file.py, and www.example.com./path/bar.py; keep safe/file.py"
                ),
                "critic": "Review safe/file.py",
                "implementer": "",
            }
        )
        meeting["moderator_synthesis"]["task_scope"] = {
            "implementer": {
                "files": [
                    "github.com/org/repo/other.py",
                    "github.com./org/repo/other2.py",
                    "www.example.com/path/other.py",
                    "www.example.com./path/other2.py",
                    "safe/other.py",
                ],
            }
        }

        report = build_task_scope_report(meeting, now_iso="2026-05-29T00:00:00+00:00")
        encoded = json.dumps(report, ensure_ascii=False)

        self.assertIn("safe/file.py", encoded)
        self.assertIn("safe/other.py", encoded)
        self.assertNotIn("github.com/org/repo/src/foo.py", encoded)
        self.assertNotIn("github.com./org/repo/src/bar.py", encoded)
        self.assertNotIn("www.example.com/path/file.py", encoded)
        self.assertNotIn("www.example.com./path/bar.py", encoded)
        self.assertNotIn("github.com/org/repo/other.py", encoded)
        self.assertNotIn("github.com./org/repo/other2.py", encoded)
        self.assertNotIn("www.example.com/path/other.py", encoded)
        self.assertNotIn("www.example.com./path/other2.py", encoded)

    def test_build_report_dedupes_caps_candidates_and_distinguishes_dir_from_file(self):
        tasks = {
            "architect": " ".join(f"src/file_{index}.py" for index in range(40)) + " src/file_0.py",
            "critic": "Review tests/ and src/file_0.py",
            "implementer": "Review src/ and tests/",
        }

        report = build_task_scope_report(_meeting_with_tasks(tasks), now_iso="2026-05-29T00:00:00+00:00")
        architect = next(role for role in report["roles"] if role["role_id"] == "architect")
        overlaps = {(overlap["kind"], overlap["token"]): overlap for overlap in report["overlaps"]}

        self.assertEqual(architect["candidate_count"], 32)
        self.assertTrue(architect["truncated"])
        self.assertIn(("file", "src/file_0.py"), overlaps)
        self.assertIn(("dir", "tests/"), overlaps)
        self.assertNotIn(("file", "src/"), overlaps)

    def test_markdown_is_advisory_and_does_not_render_task_bodies_or_absolute_paths(self):
        meeting = _meeting_with_tasks(
            {
                "architect": "Long task body with private words should not be copied; touch src/foo.py only.",
                "critic": "Also inspect src/foo.py.",
            }
        )
        report = build_task_scope_report(meeting, now_iso="2026-05-29T00:00:00+00:00")

        markdown = render_task_scope_report_markdown(report)

        self.assertIn("# Task Scope Report", markdown)
        self.assertIn("Advisory only", markdown)
        self.assertIn("src/foo.py", markdown)
        self.assertNotIn("Long task body with private words", markdown)
        self.assertNotRegex(markdown, r"(?m)^\s*/Users/")

    def test_write_task_scope_report_writes_files_and_meeting_summary(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            meeting_dir = Path(temp_dir) / "meeting"
            meeting_dir.mkdir()
            meeting = _meeting_with_tasks(
                {
                    "architect": "Own src/foo.py",
                    "critic": "Review src/foo.py",
                }
            )

            report = write_task_scope_report(meeting_dir, meeting, now_iso="2026-05-29T00:00:00+00:00")

            self.assertEqual(report["overlap_count"], 1)
            self.assertTrue((meeting_dir / "task_scope_report.md").exists())
            self.assertTrue((meeting_dir / "task_scope_report.json").exists())
            self.assertEqual(meeting["artifacts"]["task_scope_report"], "task_scope_report.md")
            self.assertEqual(meeting["artifacts"]["task_scope_report_json"], "task_scope_report.json")
            self.assertEqual(meeting["task_scope_report"]["overlap_count"], 1)
            self.assertNotIn("roles", meeting["task_scope_report"])

    def test_return_packet_checklist_references_report_only_when_overlap_exists(self):
        meeting = _meeting_with_tasks(
            {
                "architect": "Own src/foo.py",
                "critic": "Review src/foo.py",
                "implementer": "No overlapping path.",
            }
        )
        report = build_task_scope_report(meeting, now_iso="2026-05-29T00:00:00+00:00")
        meeting["task_scope_report"] = {
            "version": report["version"],
            "summary": report["summary"],
            "overlap_count": report["overlap_count"],
            "candidate_count_total": report["candidate_count_total"],
        }

        packet = build_return_packet(meeting, {"id": "architect", "display_name": "Architect"})

        self.assertIn(
            "Review task_scope_report.md for overlapping file scope before editing files.",
            packet["handoff_checklist"],
        )

        meeting["task_scope_report"] = {"overlap_count": 0}
        packet = build_return_packet(meeting, {"id": "architect", "display_name": "Architect"})

        self.assertNotIn(
            "Review task_scope_report.md for overlapping file scope before editing files.",
            packet["handoff_checklist"],
        )

    def test_public_artifacts_write_overlap_advisory_into_return_packets(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            meeting_dir = Path(temp_dir) / "meeting"
            meeting_dir.mkdir()
            meeting = _meeting_with_tasks(
                {
                    "architect": "Own src/foo.py",
                    "critic": "Review src/foo.py",
                    "implementer": "No overlapping path.",
                }
            )
            meeting["moderator_synthesis"].update(
                {
                    "winner": "Undetermined",
                    "confidence": "low",
                    "summary": "Pending user decision.",
                    "caveats": [],
                    "ranking": ["Undetermined"],
                }
            )
            meeting.update(
                {
                    "question": "How should implementation be split?",
                    "display_question": "How should implementation be split?",
                    "decision_status": {"status": "pending_user", "next_actions": []},
                    "decision_gate": {"status": "needs_user_decision", "can_finalize": False, "reasons": []},
                    "evidence_gate": {"status": "not_applicable"},
                    "debate_rounds": [],
                    "room_chat": [],
                    "memory_input": {"research_summaries": []},
                    "follow_up": {"parent_meeting_id": None, "note": None},
                }
            )

            write_public_artifacts(meeting_dir, meeting, transcript_text="# Transcript\n")

            packet = json.loads((meeting_dir / "return_packets" / "architect.json").read_text(encoding="utf-8"))

        self.assertIn(
            "Review task_scope_report.md for overlapping file scope before editing files.",
            packet["handoff_checklist"],
        )
