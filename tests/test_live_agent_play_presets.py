import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agentsassemble.gui import live_agent_turn_preset_payload
from agentsassemble.live_agent_play_presets import available_play_presets, build_play_preset_turns


def _meeting() -> dict[str, object]:
    return {
        "meeting_id": "m1",
        "meeting_mode": "free_chat",
        "roles": [
            {"id": "architect", "display_name": "Architect"},
            {"id": "critic", "display_name": "Critic"},
        ],
        "agent_bindings": [
            {"role_id": "architect", "agent_id": "agent-a"},
            {"role_id": "critic", "agent_id": "agent-b"},
        ],
    }


def _live_agents() -> list[dict[str, object]]:
    return [
        {"agent_id": "agent-a", "display_name": "Agent A", "meeting_id": "m1"},
        {"agent_id": "agent-b", "display_name": "Agent B", "meeting_id": "m1"},
    ]


class LiveAgentPlayPresetTests(unittest.TestCase):
    def test_available_presets_include_argument_round(self):
        presets = available_play_presets()

        self.assertIn("meme_debate_argument", [preset["id"] for preset in presets])

    def test_builds_preset_turns_using_bound_live_agents(self):
        result = build_play_preset_turns(
            _meeting(),
            _live_agents(),
            meeting_id="m1",
            preset_id="meme_debate_argument",
            role_ids=["critic", "architect"],
        )

        self.assertEqual(result["preset_id"], "meme_debate_argument")
        self.assertEqual(result["round_id"], "play_preset:meme_debate_argument")
        self.assertEqual(result["role_ids"], ["critic", "architect"])
        self.assertEqual([turn["agent_id"] for turn in result["turns"]], ["agent-b", "agent-a"])
        self.assertEqual([turn["turn_id"] for turn in result["turns"]], ["play_preset:meme_debate_argument:0:critic", "play_preset:meme_debate_argument:1:architect"])
        self.assertIn("심판처럼 판정하지 말고 토론자로 말해라", result["turns"][0]["content"])

    def test_gui_payload_expands_preset_without_starting_provider_processes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            (meeting_dir / "meeting.json").write_text(json.dumps(_meeting()), encoding="utf-8")
            (root / "live_agents.json").write_text(json.dumps({"agents": _live_agents()}), encoding="utf-8")

            sequence_response = {"status": "answered", "answered_count": 1, "timeout_count": 0, "skipped_count": 0, "results": []}
            with patch("agentsassemble.gui.live_agent_turn_sequence_payload", return_value=sequence_response) as sequence_payload:
                result = live_agent_turn_preset_payload(
                    root,
                    "m1",
                    {
                        "preset_id": "meme_debate_argument",
                        "role_ids": ["critic"],
                        "timeout_seconds": 8,
                        "stop_on_timeout": True,
                    },
                )

        self.assertEqual(result["preset_id"], "meme_debate_argument")
        self.assertEqual(result["status"], "answered")
        sequence_payload.assert_called_once()
        _output_root, meeting_id, payload = sequence_payload.call_args.args
        self.assertEqual(meeting_id, "m1")
        self.assertEqual(payload["timeout_seconds"], 8.0)
        self.assertTrue(payload["stop_on_timeout"])
        self.assertEqual(payload["turns"][0]["agent_id"], "agent-b")
        self.assertNotIn("command", json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
