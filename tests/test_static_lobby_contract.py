import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class StaticLobbyContractTests(unittest.TestCase):
    def test_lobby_chat_exposes_attachment_upload_and_preview_contract(self):
        source = (ROOT / "agentsassemble" / "static" / "lobby.js").read_text(encoding="utf-8")

        self.assertIn('id="lobby-attachments"', source)
        self.assertIn("uploadLobbyAttachments", source)
        self.assertIn("renderLobbyAttachments", source)
        self.assertIn("openAttachmentPreview", source)
        self.assertIn("/api/attachments", source)
        self.assertIn("lobby-attachment-preview", source)

    def test_session_run_retry_now_uses_current_real_provider_approval_checkbox(self):
        source = (ROOT / "agentsassemble" / "static" / "lobby.js").read_text(encoding="utf-8")
        start = source.index("async function retryLiveAgentSessionRunNow")
        end = source.index("async function pauseLiveAgentSessionRun", start)
        body = source[start:end]

        self.assertIn("#live-agent-auto-join-real-provider-approval", body)
        self.assertIn("approve_real_providers", body)
        self.assertIn("JSON.stringify(requestBody)", body)
        self.assertNotIn('body: "{}"', body)
