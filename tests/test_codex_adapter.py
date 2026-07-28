import subprocess
import tempfile
import unittest
from pathlib import Path
from subprocess import TimeoutExpired

from agentsassemble.providers.adapters.codex import CodexAdapter
from agentsassemble.models import get_research_depth, ResearchSteering, Role


class CodexAdapterTests(unittest.TestCase):
    def test_repeated_round_does_not_reuse_previous_output_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            meeting_dir = Path(temp_dir)
            (meeting_dir / "roles" / "speaker").mkdir(parents=True)
            calls = 0

            def fake_runner(command, input, text, capture_output, timeout, check):
                nonlocal calls
                calls += 1
                output_path = Path(command[command.index("--output-last-message") + 1])
                if calls == 1:
                    output_path.write_text('{"content":"previous reply"}', encoding="utf-8")
                    stdout = ""
                else:
                    stdout = '{"content":"current reply"}'
                return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

            adapter = CodexAdapter(command_runner=fake_runner)
            role = Role("speaker", "Speaker", "Speaker", "discussion")
            session = adapter.start_session(role, {"meeting_dir": str(meeting_dir)})

            first = adapter.run_round(role, session, "round_1", "first", {})
            second = adapter.run_round(role, session, "round_1", "second", {})

            self.assertEqual(first["content"], "previous reply")
            self.assertEqual(second["content"], "current reply")

    def test_codex_research_uses_output_last_message_and_records_session_id(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            meeting_dir = Path(temp_dir)
            role_dir = meeting_dir / "roles" / "lore_lawyer"
            role_dir.mkdir(parents=True)
            seen_inputs = []

            def fake_runner(command, input, text, capture_output, timeout, check):
                seen_inputs.append(input)
                output_path = Path(command[command.index("--output-last-message") + 1])
                output_path.write_text(
                    '{"queries":["q"],"sources":[],"summary":"s","confidence":"medium","uncertainty":"u","claim_evidence":[]}',
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout="session id: 019e0346-f384-74f2-914e-c95f535edf46\n",
                    stderr="",
                )

            adapter = CodexAdapter(command_runner=fake_runner)
            role = Role("lore_lawyer", "설정충", "Canon Analyst", "canon")
            session = adapter.start_session(role, {"meeting_dir": str(meeting_dir)})

            research = adapter.run_research(
                role,
                session,
                "Question?",
                get_research_depth("standard"),
                ResearchSteering(),
            )

            self.assertEqual(research["summary"], "s")
            self.assertEqual(research["research_depth"]["name"], "standard")
            self.assertEqual(session["session_id"], "019e0346-f384-74f2-914e-c95f535edf46")
            self.assertEqual(research["codex"]["returncode"], 0)
            self.assertIn("codex", research["codex"]["command"])
            self.assertIn("--search", research["codex"]["command"])
            self.assertIn("--sandbox", research["codex"]["command"])
            self.assertIn("read-only", research["codex"]["command"])
            self.assertIn("--ignore-rules", research["codex"]["command"])
            self.assertIn("Minimum sources: 12", seen_inputs[0])
            self.assertIn("Minimum claim_evidence items: 6", seen_inputs[0])
            self.assertIn("Default stance: investigate freely", seen_inputs[0])
            self.assertIn("MUST exactly match one sources[].url", seen_inputs[0])

    def test_codex_research_accepts_user_steering_without_forcing_conclusion(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            meeting_dir = Path(temp_dir)
            (meeting_dir / "roles" / "lore_lawyer").mkdir(parents=True)
            seen_inputs = []

            def fake_runner(command, input, text, capture_output, timeout, check):
                seen_inputs.append(input)
                output_path = Path(command[command.index("--output-last-message") + 1])
                output_path.write_text(
                    '{"queries":["q"],"sources":[],"summary":"s","confidence":"medium","uncertainty":"u","claim_evidence":[]}',
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            adapter = CodexAdapter(command_runner=fake_runner)
            role = Role("lore_lawyer", "설정충", "Canon Analyst", "canon")
            session = adapter.start_session(role, {"meeting_dir": str(meeting_dir)})

            research = adapter.run_research(
                role,
                session,
                "Question?",
                get_research_depth("smoke"),
                ResearchSteering(stance="user_leaning", prompt="아오키지 우세설을 더 자세히 조사"),
            )

            self.assertEqual(research["research_steering"]["stance"], "user_leaning")
            self.assertIn("아오키지 우세설", seen_inputs[0])
            self.assertIn("do not force the conclusion", seen_inputs[0])

    def test_codex_round_calls_do_not_use_search(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            meeting_dir = Path(temp_dir)
            (meeting_dir / "roles" / "lore_lawyer").mkdir(parents=True)
            seen_commands = []

            def fake_runner(command, input, text, capture_output, timeout, check):
                seen_commands.append(command)
                output_path = Path(command[command.index("--output-last-message") + 1])
                output_path.write_text('{"content":"c","confidence":"medium"}', encoding="utf-8")
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            adapter = CodexAdapter(command_runner=fake_runner)
            role = Role("lore_lawyer", "설정충", "Canon Analyst", "canon")
            session = adapter.start_session(role, {"meeting_dir": str(meeting_dir)})

            adapter.run_round(role, session, "round_1", "Prompt", {})

            self.assertNotIn("--search", seen_commands[0])

    def test_codex_round_prompt_separates_research_from_spoken_message(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            meeting_dir = Path(temp_dir)
            (meeting_dir / "roles" / "lore_lawyer").mkdir(parents=True)
            seen_inputs = []

            def fake_runner(command, input, text, capture_output, timeout, check):
                seen_inputs.append(input)
                output_path = Path(command[command.index("--output-last-message") + 1])
                output_path.write_text('{"content":"짧은 발언","confidence":"medium"}', encoding="utf-8")
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            adapter = CodexAdapter(command_runner=fake_runner)
            role = Role("lore_lawyer", "설정충", "Canon Analyst", "canon")
            session = adapter.start_session(role, {"meeting_dir": str(meeting_dir)})

            adapter.run_round(role, session, "round_2", "반박", {"round_1": [{"display_name": "공식이뭘알아"}]})

            self.assertIn("Research is raw material, not your spoken message", seen_inputs[0])
            self.assertIn("4-8 Korean sentences", seen_inputs[0])
            self.assertIn("at most 2 short paragraphs", seen_inputs[0])
            self.assertIn("reference at least one previous speaker by name", seen_inputs[0])
            self.assertIn("held|qualified|reframed|revised|conceded", seen_inputs[0])
            self.assertIn("stance_delta", seen_inputs[0])
            self.assertIn("changed_by", seen_inputs[0])

    def test_codex_round_returns_stance_and_persona_dynamics(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            meeting_dir = Path(temp_dir)
            (meeting_dir / "roles" / "fanboard_skeptic").mkdir(parents=True)

            def fake_runner(command, input, text, capture_output, timeout, check):
                output_path = Path(command[command.index("--output-last-message") + 1])
                output_path.write_text(
                    '{"content":"그 근거는 인정인데 확정은 오바임.","position":"아카이누 우세 단 키자루 보류","stance_status":"qualified","stance_delta":"minor","changed_by":["show_me_the_feats"],"change_reason":"직접 전적 근거는 받아들였지만 미공개 전력 변수는 남음","remaining_resistance":"압도적 최강 확정은 거부","emotion":{"tone":"reluctant","friction":0.62,"stubbornness":0.81,"respect":0.55,"engagement":0.8},"change_conditions":["키자루 전력 공개"],"confidence":"medium"}',
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            adapter = CodexAdapter(command_runner=fake_runner)
            role = Role(
                "fanboard_skeptic",
                "만갤러",
                "Skeptical Critic",
                "반례",
                personality={"stubbornness": 0.85, "conflict_style": "teasing_skeptic"},
            )
            session = adapter.start_session(role, {"meeting_dir": str(meeting_dir)})

            message = adapter.run_round(role, session, "round_2", "반박", {})

            self.assertEqual(message["stance_status"], "qualified")
            self.assertEqual(message["stance_delta"], "minor")
            self.assertEqual(message["changed_by"], ["show_me_the_feats"])
            self.assertEqual(message["emotion"]["tone"], "reluctant")
            self.assertGreater(message["emotion"]["stubbornness"], 0.8)

    def test_codex_timeout_becomes_low_confidence_research(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            meeting_dir = Path(temp_dir)
            (meeting_dir / "roles" / "lore_lawyer").mkdir(parents=True)

            def fake_timeout_runner(command, input, text, capture_output, timeout, check):
                error = TimeoutExpired(command, timeout)
                error.stderr = b"byte stderr"
                raise error

            adapter = CodexAdapter(command_runner=fake_timeout_runner, timeout_seconds=1)
            role = Role("lore_lawyer", "설정충", "Canon Analyst", "canon")
            session = adapter.start_session(role, {"meeting_dir": str(meeting_dir)})

            research = adapter.run_research(
                role,
                session,
                "Question?",
                get_research_depth("smoke"),
                ResearchSteering(),
            )

            self.assertEqual(research["confidence"], "low")
            self.assertTrue(research["codex"]["timed_out"])
            self.assertIsInstance(research["codex"]["stderr"], str)
            self.assertIn("timed out", research["summary"])

    def test_codex_parser_extracts_json_from_wrapped_text(self):
        parsed = CodexAdapter._parse_json_object('Here is JSON:\n```json\n{"winner":"Akainu"}\n```\nThanks')

        self.assertEqual(parsed, {"winner": "Akainu"})

    def test_codex_synthesis_retries_unparseable_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            meeting_dir = Path(temp_dir)
            (meeting_dir / "roles" / "moderator").mkdir(parents=True)
            calls = []

            def fake_runner(command, input, text, capture_output, timeout, check):
                calls.append(input)
                output_path = Path(command[command.index("--output-last-message") + 1])
                if len(calls) == 1:
                    output_path.write_text("Winner is probably Akainu, but this is not JSON.", encoding="utf-8")
                else:
                    output_path.write_text(
                        '{"winner":"Akainu","ranking":["Akainu"],"confidence":"medium","caveats":[],"summary":"s","tasks":{}}',
                        encoding="utf-8",
                    )
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            adapter = CodexAdapter(command_runner=fake_runner)
            session = {"meeting_dir": str(meeting_dir), "role_id": "moderator"}

            synthesis = adapter.synthesize(session, "Question?", {"evidence_gate": {"status": "warn"}})

            self.assertEqual(synthesis["winner"], "Akainu")
            self.assertEqual(len(calls), 2)
            self.assertIn("strict JSON only", calls[1])
            self.assertIn("Public council context", calls[1])
            self.assertIn("evidence_gate", calls[1])
            self.assertIn("repair", synthesis["codex"])

    def test_codex_synthesis_fallback_is_not_empty_when_repair_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            meeting_dir = Path(temp_dir)
            (meeting_dir / "roles" / "moderator").mkdir(parents=True)

            def fake_runner(command, input, text, capture_output, timeout, check):
                output_path = Path(command[command.index("--output-last-message") + 1])
                output_path.write_text("", encoding="utf-8")
                return subprocess.CompletedProcess(command, 1, stdout="", stderr="failed")

            adapter = CodexAdapter(command_runner=fake_runner)
            session = {"meeting_dir": str(meeting_dir), "role_id": "moderator"}

            synthesis = adapter.synthesize(
                session,
                "Question?",
                {
                    "evidence_gate": {
                        "status": "warn",
                        "total_supported_claims": 2,
                        "total_unsupported_claims": 1,
                        "total_weak_claims": 1,
                        "total_verifier_rejected_claims": 0,
                    },
                    "research_summaries": [
                        {"role_id": "lore_lawyer", "confidence": "low", "summary": "아카이누 근거가 가장 강함."}
                    ],
                },
            )

            self.assertEqual(synthesis["winner"], "Undetermined")
            self.assertEqual(synthesis["fallback"], "local_synthesis")
            self.assertEqual(synthesis["status"], "degraded")
            self.assertNotIn("Evidence Gate status is warn", synthesis["summary"])
            self.assertEqual(synthesis["diagnostics"]["evidence_gate"]["status"], "warn")
            self.assertEqual(synthesis["diagnostics"]["evidence_gate"]["supported"], 2)

    def test_codex_synthesis_fallback_uses_repeated_round_positions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            meeting_dir = Path(temp_dir)
            (meeting_dir / "roles" / "moderator").mkdir(parents=True)

            def fake_runner(command, input, text, capture_output, timeout, check):
                output_path = Path(command[command.index("--output-last-message") + 1])
                output_path.write_text("", encoding="utf-8")
                return subprocess.CompletedProcess(command, 1, stdout="", stderr="failed")

            adapter = CodexAdapter(command_runner=fake_runner)
            session = {"meeting_dir": str(meeting_dir), "role_id": "moderator"}

            synthesis = adapter.synthesize(
                session,
                "Question?",
                {
                    "evidence_gate": {"status": "warn", "total_supported_claims": 6},
                    "rounds": {
                        "round_1": [
                            {"role_id": "lore_lawyer", "position": "사카즈키/아카이누가 최강 후보 1위"},
                            {"role_id": "show_me_the_feats", "position": "전투 결과상 Akainu 우세"},
                            {"role_id": "fanboard_skeptic", "position": "아카이누 1순위지만 압살은 보류"},
                        ]
                    },
                },
            )

            self.assertEqual(synthesis["winner"], "Sakazuki / Akainu")
            self.assertEqual(synthesis["ranking"], ["Sakazuki / Akainu"])
            self.assertEqual(synthesis["confidence"], "medium")
            self.assertIn("보수적으로 판정", synthesis["caveats"][0])
            self.assertEqual(synthesis["status"], "degraded")


if __name__ == "__main__":
    unittest.main()
