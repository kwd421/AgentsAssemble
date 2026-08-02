from __future__ import annotations

from agentsassemble.identity.repository import (
    IdentityBackend,
    LOCAL_OPERATOR_PARTICIPANT_ID,
    LOCAL_OPERATOR_USER_ID,
)
from agentsassemble.identity.accounts import AccountLinkConflict


class IdentityRepositoryContractMixin:
    """Behavior shared by local SQLite and hosted PostgreSQL identity stores."""

    repository: IdentityBackend

    def test_credential_identity_is_stable_and_blank_updates_do_not_erase(self) -> None:
        first = self.repository.resolve_credential_user(
            "device:contract-alpha",
            display_name="Alpha",
            avatar_image_url="https://example.invalid/a.png",
            participant_type="human",
        )
        second = self.repository.resolve_credential_user("device:contract-alpha")

        self.assertEqual(first["user_id"], second["user_id"])
        self.assertEqual(first["participant_id"], second["participant_id"])
        self.assertEqual(second["display_name"], "Alpha")
        self.assertEqual(second["avatar_image_url"], "https://example.invalid/a.png")

    def test_user_profiles_are_isolated_and_update_the_public_identity(self) -> None:
        first = self.repository.resolve_credential_user(
            "device:profile-alpha",
            user_id="profile-user-alpha",
            participant_id="profile-participant-alpha",
            display_name="Alpha",
        )
        second = self.repository.resolve_credential_user(
            "device:profile-bravo",
            user_id="profile-user-bravo",
            participant_id="profile-participant-bravo",
            display_name="Bravo",
        )

        saved = self.repository.update_user_profile(
            str(first["user_id"]),
            {
                "display_name": "Alpha Updated",
                "avatar_image_url": "/api/attachments/alpha_123?view=1",
                "custom_status": "ready",
            },
        )

        self.assertEqual(
            self.repository.user_profile(str(first["user_id"])),
            saved,
        )
        self.assertIsNone(self.repository.user_profile(str(second["user_id"])))
        refreshed = self.repository.get_user(str(first["user_id"]))
        self.assertEqual(refreshed["display_name"], "Alpha Updated")
        self.assertEqual(
            refreshed["avatar_image_url"],
            "/api/attachments/alpha_123?view=1",
        )

    def test_external_account_link_is_explicit_and_cannot_steal_an_identity(self) -> None:
        first = self.repository.resolve_credential_user(
            "device:account-alpha",
            user_id="account-user-alpha",
            participant_id="account-participant-alpha",
        )
        second = self.repository.resolve_credential_user(
            "device:account-bravo",
            user_id="account-user-bravo",
            participant_id="account-participant-bravo",
        )

        linked = self.repository.connect_external_account(
            str(first["user_id"]),
            account_id="acct-contract-google",
            provider="google",
            subject_fingerprint="subject-contract-google",
            display_name="Account Alpha",
            email="alpha@example.invalid",
            avatar_image_url="https://example.invalid/account.png",
            connected_at="2026-08-02T00:00:00+00:00",
        )

        self.assertEqual(linked["account_id"], "acct-contract-google")
        self.assertEqual(linked["user_id"], first["user_id"])
        self.assertEqual(
            self.repository.external_account_for_user(str(first["user_id"])),
            linked,
        )
        self.assertEqual(
            self.repository.user_for_external_account(
                "google",
                "subject-contract-google",
            )["user_id"],
            first["user_id"],
        )

        with self.assertRaises(AccountLinkConflict):
            self.repository.connect_external_account(
                str(second["user_id"]),
                account_id="acct-contract-google",
                provider="google",
                subject_fingerprint="subject-contract-google",
                connected_at="2026-08-02T00:01:00+00:00",
            )

        rebound = self.repository.bind_credential_to_user(
            str(first["user_id"]),
            auth_key="device:account-new-device",
            provider="device",
            used_at="2026-08-02T00:02:00+00:00",
        )
        self.assertEqual(rebound["user_id"], first["user_id"])
        self.assertEqual(
            self.repository.user_for_credential("device:account-new-device")["user_id"],
            first["user_id"],
        )

        with self.assertRaises(AccountLinkConflict):
            self.repository.bind_credential_to_user(
                str(first["user_id"]),
                auth_key="device:account-bravo",
                provider="device",
                used_at="2026-08-02T00:03:00+00:00",
            )
        self.assertEqual(
            self.repository.user_for_credential("device:account-bravo")["user_id"],
            second["user_id"],
        )

        self.assertTrue(
            self.repository.disconnect_external_account(str(first["user_id"]))
        )
        self.assertIsNone(
            self.repository.external_account_for_user(str(first["user_id"]))
        )
        self.assertIsNone(
            self.repository.user_for_external_account(
                "google",
                "subject-contract-google",
            )
        )
        self.assertEqual(
            self.repository.user_for_credential("device:account-alpha")["user_id"],
            first["user_id"],
        )
        relinked = self.repository.connect_external_account(
            str(second["user_id"]),
            account_id="acct-contract-google",
            provider="google",
            subject_fingerprint="subject-contract-google",
            display_name="Account Bravo",
            connected_at="2026-08-02T00:04:00+00:00",
        )
        self.assertEqual(relinked["user_id"], second["user_id"])

    def test_operator_claim_pairing_and_consumption_share_one_identity(self) -> None:
        claimed = self.repository.claim_local_operator_credential(
            "device:operator-alpha",
            display_name="Operator",
        )
        self.assertEqual(claimed["user_id"], LOCAL_OPERATOR_USER_ID)
        self.assertEqual(claimed["participant_id"], LOCAL_OPERATOR_PARTICIPANT_ID)

        pairing = self.repository.create_operator_pairing(
            pairing_id="pairing-contract",
            token_fingerprint="fingerprint-contract",
            room_id="room-contract",
            target_origin="https://room.example",
            created_at="2026-07-15T00:00:00+00:00",
            expires_at="2026-07-15T01:00:00+00:00",
        )
        self.assertEqual(pairing["user_id"], LOCAL_OPERATOR_USER_ID)
        result = self.repository.consume_operator_pairing(
            token_fingerprint="fingerprint-contract",
            target_origin="https://room.example",
            auth_key="device:operator-bravo",
            used_at="2026-07-15T00:30:00+00:00",
        )
        self.assertEqual(result["status"], "consumed")
        self.assertEqual(
            self.repository.user_for_credential("device:operator-bravo")["user_id"],
            LOCAL_OPERATOR_USER_ID,
        )
        self.assertEqual(
            self.repository.consume_operator_pairing(
                token_fingerprint="fingerprint-contract",
                target_origin="https://room.example",
                auth_key="device:operator-bravo",
                used_at="2026-07-15T00:35:00+00:00",
            )["status"],
            "resumed",
        )
        self.assertEqual(
            self.repository.consume_operator_pairing(
                token_fingerprint="fingerprint-contract",
                target_origin="https://room.example",
                auth_key="device:operator-charlie",
                used_at="2026-07-15T00:40:00+00:00",
            )["status"],
            "already_used",
        )
        completed = self.repository.update_operator_pairing_redemption(
            pairing_id="pairing-contract",
            auth_key="device:operator-bravo",
            status="completed",
            completed_at="2026-07-15T00:36:00+00:00",
            session_fingerprint="session-fingerprint-contract",
        )
        self.assertEqual(completed["redemption_status"], "completed")
        self.assertEqual(completed["session_fingerprint"], "session-fingerprint-contract")

    def test_membership_merge_mute_and_remove(self) -> None:
        self.repository.upsert_membership(
            {
                "meeting_id": "room-contract",
                "participant_id": "guest-contract",
                "display_name": "Guest",
                "participant_type": "human",
            }
        )
        merged = self.repository.upsert_membership(
            {
                "meeting_id": "room-contract",
                "participant_id": "guest-contract",
                "display_name": "",
                "status": "online",
            }
        )
        self.assertEqual(merged["display_name"], "Guest")
        self.assertEqual(merged["status"], "online")
        self.assertTrue(
            self.repository.set_membership_muted(
                "room-contract",
                "guest-contract",
                True,
            )["muted"]
        )
        self.assertTrue(self.repository.membership_muted("room-contract", "guest-contract"))
        self.assertTrue(self.repository.remove_membership("room-contract", "guest-contract"))
        self.assertIsNone(self.repository.get_membership("room-contract", "guest-contract"))

    def test_room_registry_preferences_and_delete_are_consistent(self) -> None:
        user = self.repository.resolve_credential_user("device:preference-contract")
        room = self.repository.upsert_room(
            room_id="room-contract",
            owner_id=user["user_id"],
            label="Contract Room",
            origin="contract",
        )
        self.assertEqual(room["label"], "Contract Room")
        updated = self.repository.update_room_preferences(
            user["user_id"],
            "room-contract",
            {"notifications": "mute"},
        )
        self.assertEqual(updated["notifications"], "mute")
        self.assertEqual(
            self.repository.room_preferences(user["user_id"], "room-contract"),
            updated,
        )
        self.assertTrue(self.repository.delete_room("room-contract"))
        self.assertIsNone(self.repository.get_room("room-contract"))
        self.assertEqual(
            self.repository.room_preferences(user["user_id"], "room-contract"),
            {"notifications": "mentions", "channel_settings": {}},
        )

    def test_usage_aggregation_filters_and_tracks_estimates(self) -> None:
        self.repository.record_usage(
            {
                "created_at": "2026-07-15T00:00:00+00:00",
                "user_id": "user-a",
                "meeting_id": "room-a",
                "provider": "provider-a",
                "model": "model-a",
                "input_tokens": 20,
                "output_tokens": 5,
            }
        )
        self.repository.record_usage(
            {
                "created_at": "2026-07-15T00:10:00+00:00",
                "user_id": "user-b",
                "meeting_id": "room-b",
                "provider": "provider-b",
                "model": "model-b",
                "input_tokens": 40,
                "output_tokens": 10,
                "estimated": True,
            }
        )
        summary = self.repository.usage_summary()
        self.assertEqual(summary["events"], 2)
        self.assertEqual(summary["input_tokens"], 60)
        self.assertEqual(summary["estimated_events"], 1)
        self.assertEqual(self.repository.usage_summary(user_id="user-a")["events"], 1)
