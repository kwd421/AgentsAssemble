import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


FRONTEND_DIR = ROOT / "frontend" / "src"


def frontend_file(relative_path: str) -> str:
    return (FRONTEND_DIR / relative_path).read_text(encoding="utf-8")


class ReactLobbyContractTests(unittest.TestCase):
    def test_lobby_chat_exposes_attachment_upload_and_preview_contract(self):
        api_source = frontend_file("api.ts")
        lobby_source = frontend_file("views/LobbyView.tsx")
        live_source = frontend_file("views/LiveView.tsx")
        composer_source = frontend_file("views/components/LobbyComposer.tsx")
        attachments_source = frontend_file("views/components/LobbyAttachments.tsx")

        self.assertIn("export function uploadLobbyAttachment", api_source)
        self.assertIn("/api/attachments", api_source)
        self.assertIn("uploadLobbyAttachment(file)", composer_source)
        self.assertIn('type="file"', composer_source)
        self.assertIn("multiple", composer_source)
        self.assertIn("pendingAttachments", composer_source)
        self.assertIn("attachments: draftAttachments", composer_source)
        self.assertIn("LobbyAttachments", lobby_source)
        self.assertIn("LobbyAttachments", live_source)
        self.assertIn("openImagePreview", attachments_source)
        self.assertIn('role="dialog"', attachments_source)
        self.assertIn('aria-modal="true"', attachments_source)
        self.assertIn("selectedImage.download_url || selectedImage.url", attachments_source)

    def test_react_frontend_does_not_expose_provider_session_retry_controls(self):
        source = "\n".join(path.read_text(encoding="utf-8") for path in sorted(FRONTEND_DIR.rglob("*")) if path.suffix in {".ts", ".tsx"})

        self.assertNotIn("/api/live-agent-session-runs/retry-now", source)
        self.assertNotIn("approve_real_providers", source)
        self.assertNotIn("live-agent-auto-join-real-provider-approval", source)
