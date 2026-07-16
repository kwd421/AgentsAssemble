from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

import agentsassemble.room_invite_repository as compatibility_repository
from agentsassemble.persistence.local.admission import repository as owned_repository


class LocalAdmissionPersistencePackageTests(unittest.TestCase):
    def test_root_module_exports_owned_local_adapters(self) -> None:
        self.assertIs(
            compatibility_repository.MemoryInviteSessionRepository,
            owned_repository.MemoryInviteSessionRepository,
        )
        self.assertIs(
            compatibility_repository.JsonInviteSessionRepository,
            owned_repository.JsonInviteSessionRepository,
        )
        self.assertEqual(
            compatibility_repository.ROOM_INVITE_STORE_SCHEMA,
            owned_repository.ROOM_INVITE_STORE_SCHEMA,
        )

    def test_compatibility_and_owned_paths_share_the_json_schema(self) -> None:
        now = datetime.now(UTC)
        invite = {
            "invite_id": "invite-1",
            "agent_id": "guest",
            "display_name": "Guest",
            "meeting_id": "room-a",
            "invite_scope": "room",
            "participant_type": "human",
            "client_type": "browser",
            "provider_kind": "manual",
            "created_by_user_id": "owner-a",
            "join_code_fingerprint": "join-invite-1",
            "join_nonce": "nonce-invite-1",
            "permission_mode": "participant",
            "max_uses": 1,
            "use_count": 0,
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(minutes=10)).isoformat(),
            "revoked": False,
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "invite-state.json"
            compatibility = compatibility_repository.JsonInviteSessionRepository(path)
            compatibility.save_invite(invite)
            owned = owned_repository.JsonInviteSessionRepository(path)

        self.assertEqual(owned.invite("invite-1"), invite)


if __name__ == "__main__":
    unittest.main()
