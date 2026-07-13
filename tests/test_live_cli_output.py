import unittest

from agentsassemble.live_cli_output import extract_live_cli_terminal_message, filter_live_cli_terminal_text


class LiveCliOutputExtractionTests(unittest.TestCase):
    def test_codex_tui_chrome_is_not_part_of_message(self):
        raw = (
            "› #general human: @codex 자유주제\n"
            "⠋ AgentsAssemble•Working(0s • esc to interrupt)"
            "›Run /review on my current changes gpt-5.5 high · ~/Projects/AgentsAssemble\n"
            "⠙ AgentsAssemble  • 좋아, 첫 주제는 협업할 때 사람이 편해지는 순간으로 가자.\n"
            "›Run /review on my current changes gpt-5.5 high · ~/Projects/AgentsAssemble"
        ).encode()

        message = extract_live_cli_terminal_message(raw)

        self.assertEqual(message, "좋아, 첫 주제는 협업할 때 사람이 편해지는 순간으로 가자.")
        self.assertNotIn("Working", message)
        self.assertNotIn("AgentsAssemble", message)
        self.assertNotIn("Run /review", message)

    def test_antigravity_tui_chrome_is_filtered_from_message(self):
        text = (
            "Gemini 3.5 Flash (Medium) ⣻ Generating... ───────── > esc to cancel\n"
            "보여주는 게 핵심이라고 봐요. 그래야 사람도 결정적인 순간에만 피드백을 줄 수 있으니까요.\n"
            "? for shortcuts Gemini 3.5 Flash (Medium)"
        )

        message = filter_live_cli_terminal_text(text)

        self.assertIn("보여주는 게 핵심이라고 봐요.", message)
        self.assertIn("피드백을 줄 수 있으니까요.", message)
        self.assertNotIn("Generating", message)
        self.assertNotIn("Gemini", message)
        self.assertNotIn("shortcuts", message)

    def test_grok_tui_thinking_and_status_are_filtered_from_message(self):
        text = (
            "Thinking - Grok Setup for #general Short Natural Re… - grok\n"
            "I need to continue naturally from the last person's message.\n"
            "❙ought for8.2s 그렇지. 특히2:45PM Responding… 0.0s3 에이전트가 "
            "\"이건 내가 혼자1 결정하기 애매해\" 하고 먼저4 손을 들고 멈추는 게 중요해."
            " Turncompletedin9.9s .shortcuts"
        )

        message = filter_live_cli_terminal_text(text)

        self.assertIn("그렇지.", message)
        self.assertIn("에이전트가", message)
        self.assertIn("결정하기 애매해", message)
        self.assertNotIn("Thinking", message)
        self.assertNotIn("Responding", message)
        self.assertNotIn("shortcuts", message)
        self.assertNotIn("I need to", message)

    def test_workspace_trust_prompt_variants_are_terminal_chrome(self):
        for prompt in (
            "Do you trust the contents of this project?",
            "Do you trust the contents of this directory?",
        ):
            with self.subTest(prompt=prompt):
                self.assertEqual(filter_live_cli_terminal_text(prompt), "")


if __name__ == "__main__":
    unittest.main()
