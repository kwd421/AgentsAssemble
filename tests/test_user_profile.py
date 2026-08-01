from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agentsassemble.features.social.profile import read_user_profile, update_user_profile
from agentsassemble.identity.repository import LOCAL_OPERATOR_USER_ID
from agentsassemble.persistence.local.identity.repository import IdentityStore
from agentsassemble.persistence.local.room.repository import RoomStore


def _profile_dependencies(root: Path) -> tuple[IdentityStore, RoomStore]:
    identities = IdentityStore(root / "identity.db")
    identities.claim_local_operator_credential(
        "device:test-operator",
        display_name="SeiNel",
    )
    return identities, RoomStore(root)


class UserProfileTests(unittest.TestCase):
    def test_user_profile_sanitizes_and_persists_discord_style_profile(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            identities, rooms = _profile_dependencies(root)
            saved = update_user_profile(
                root,
                {
                    "display_name": " SeiNel\nAdmin ",
                    "handle": " seinel.\troom ",
                    "status": "dnd",
                    "custom_status": "  작업 중\r\n",
                    "avatar_label": "SN",
                    "avatar_image_url": "/api/attachments/abcDEF12?view=1",
                    "banner_preset": "ember",
                    "accent_color": "#5865f2",
                    "mic_muted": False,
                    "deafened": True,
                    "ignored": "not public",
                },
                identities=identities,
                rooms=rooms,
                user_id=LOCAL_OPERATOR_USER_ID,
            )
            loaded = read_user_profile(
                root,
                identities=identities,
                user_id=LOCAL_OPERATOR_USER_ID,
            )

        self.assertEqual(saved["profile"]["display_name"], "SeiNel Admin")
        self.assertEqual(saved["profile"]["handle"], "seinel. room")
        self.assertEqual(saved["profile"]["status"], "dnd")
        self.assertEqual(saved["profile"]["custom_status"], "작업 중")
        self.assertEqual(saved["profile"]["avatar_label"], "SN")
        self.assertEqual(saved["profile"]["avatar_image_url"], "/api/attachments/abcDEF12?view=1")
        self.assertEqual(saved["profile"]["banner_preset"], "ember")
        self.assertEqual(saved["profile"]["accent_color"], "#5865f2")
        self.assertFalse(saved["profile"]["mic_muted"])
        self.assertTrue(saved["profile"]["deafened"])
        self.assertNotIn("ignored", saved["profile"])
        self.assertEqual(loaded["profile"], saved["profile"])

    def test_user_profile_partial_update_preserves_existing_identity(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            identities, rooms = _profile_dependencies(root)
            update_user_profile(
                root,
                {
                    "display_name": "SeiNel",
                    "handle": "seinel.",
                    "custom_status": "처음 상태",
                    "avatar_label": "SN",
                    "avatar_image_url": "/api/attachments/profile_123?view=1",
                    "banner_preset": "midnight",
                    "accent_color": "#5865f2",
                    "mic_muted": False,
                    "deafened": False,
                },
                identities=identities,
                rooms=rooms,
                user_id=LOCAL_OPERATOR_USER_ID,
            )
            updated = update_user_profile(
                root,
                {"mic_muted": True},
                identities=identities,
                rooms=rooms,
                user_id=LOCAL_OPERATOR_USER_ID,
            )

        self.assertEqual(updated["profile"]["display_name"], "SeiNel")
        self.assertEqual(updated["profile"]["custom_status"], "처음 상태")
        self.assertEqual(updated["profile"]["avatar_label"], "SN")
        self.assertEqual(updated["profile"]["avatar_image_url"], "/api/attachments/profile_123?view=1")
        self.assertEqual(updated["profile"]["banner_preset"], "midnight")
        self.assertTrue(updated["profile"]["mic_muted"])
        self.assertFalse(updated["profile"]["deafened"])

    def test_user_profile_rejects_external_avatar_image_urls(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            identities, rooms = _profile_dependencies(root)
            saved = update_user_profile(
                root,
                {
                    "avatar_label": "SN",
                    "avatar_image_url": "https://discord.example/avatar.png",
                },
                identities=identities,
                rooms=rooms,
                user_id=LOCAL_OPERATOR_USER_ID,
            )
            updated = update_user_profile(
                root,
                {"avatar_image_url": "/private/tmp/avatar.png"},
                identities=identities,
                rooms=rooms,
                user_id=LOCAL_OPERATOR_USER_ID,
            )

        self.assertEqual(saved["profile"]["avatar_image_url"], "")
        self.assertEqual(updated["profile"]["avatar_image_url"], "")


if __name__ == "__main__":
    unittest.main()
