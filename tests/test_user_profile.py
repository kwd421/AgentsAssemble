from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agentsassemble.user_profile import read_user_profile, update_user_profile


class UserProfileTests(unittest.TestCase):
    def test_user_profile_sanitizes_and_persists_discord_style_profile(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            saved = update_user_profile(
                root,
                {
                    "display_name": " SeiNel\nAdmin ",
                    "handle": " seinel.\troom ",
                    "status": "dnd",
                    "custom_status": "  작업 중\r\n",
                    "avatar_label": "SN",
                    "banner_preset": "ember",
                    "accent_color": "#5865f2",
                    "mic_muted": False,
                    "deafened": True,
                    "ignored": "not public",
                },
            )
            loaded = read_user_profile(root)

        self.assertEqual(saved["profile"]["display_name"], "SeiNel Admin")
        self.assertEqual(saved["profile"]["handle"], "seinel. room")
        self.assertEqual(saved["profile"]["status"], "dnd")
        self.assertEqual(saved["profile"]["custom_status"], "작업 중")
        self.assertEqual(saved["profile"]["avatar_label"], "SN")
        self.assertEqual(saved["profile"]["banner_preset"], "ember")
        self.assertEqual(saved["profile"]["accent_color"], "#5865f2")
        self.assertFalse(saved["profile"]["mic_muted"])
        self.assertTrue(saved["profile"]["deafened"])
        self.assertNotIn("ignored", saved["profile"])
        self.assertEqual(loaded["profile"], saved["profile"])

    def test_user_profile_partial_update_preserves_existing_identity(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            update_user_profile(
                root,
                {
                    "display_name": "SeiNel",
                    "handle": "seinel.",
                    "custom_status": "처음 상태",
                    "avatar_label": "SN",
                    "banner_preset": "midnight",
                    "accent_color": "#5865f2",
                    "mic_muted": False,
                    "deafened": False,
                },
            )
            updated = update_user_profile(root, {"mic_muted": True})

        self.assertEqual(updated["profile"]["display_name"], "SeiNel")
        self.assertEqual(updated["profile"]["custom_status"], "처음 상태")
        self.assertEqual(updated["profile"]["avatar_label"], "SN")
        self.assertEqual(updated["profile"]["banner_preset"], "midnight")
        self.assertTrue(updated["profile"]["mic_muted"])
        self.assertFalse(updated["profile"]["deafened"])


if __name__ == "__main__":
    unittest.main()
