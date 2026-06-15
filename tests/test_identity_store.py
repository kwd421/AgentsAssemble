import json
import sqlite3
from contextlib import closing
import tempfile
import unittest
from pathlib import Path

from agentsassemble.identity_store import (
    IdentityBackend,
    IdentityStore,
    SqliteIdentityStore,
    identity_store_for_output_root,
    make_identity_backend,
    migrate_legacy_members_json,
    migrate_legacy_users_json,
    register_identity_backend,
    reset_identity_store_registry,
)

ROOM = "room-db-test"


class IdentityStoreTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.store = IdentityStore(self.root / "identity.db")
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(reset_identity_store_registry)


class UserCredentialTests(IdentityStoreTestCase):
    def test_same_credential_resolves_to_same_user(self):
        first = self.store.resolve_credential_user("device:abc123", display_name="페이블")
        second = self.store.resolve_credential_user("device:abc123", display_name="페이블찡")
        self.assertEqual(first["user_id"], second["user_id"])
        self.assertEqual(first["participant_id"], second["participant_id"])
        self.assertEqual(second["display_name"], "페이블찡")

    def test_blank_profile_fields_never_erase_saved_values(self):
        self.store.resolve_credential_user("device:abc123", display_name="이름", participant_type="human")
        refreshed = self.store.resolve_credential_user("device:abc123")
        self.assertEqual(refreshed["display_name"], "이름")
        self.assertEqual(refreshed["participant_type"], "human")

    def test_operator_flag_round_trip(self):
        user = self.store.resolve_credential_user("device:hostkey1")
        self.assertFalse(self.store.participant_is_operator(user["participant_id"]))
        self.assertTrue(self.store.set_user_operator(user["user_id"], True))
        self.assertTrue(self.store.participant_is_operator(user["participant_id"]))

    def test_missing_credential_returns_none(self):
        self.assertIsNone(self.store.user_for_credential("device:nope"))
        self.assertIsNone(self.store.resolve_credential_user(""))


class MembershipTests(IdentityStoreTestCase):
    def test_primary_key_makes_rejoin_update_not_duplicate(self):
        for _ in range(3):
            self.store.upsert_membership(
                {"meeting_id": ROOM, "participant_id": "guest-1", "display_name": "유령없음"}
            )
        members = self.store.list_memberships(ROOM)
        self.assertEqual(len(members), 1)

    def test_merge_keeps_saved_values_when_incoming_blank(self):
        self.store.upsert_membership(
            {
                "meeting_id": ROOM,
                "participant_id": "guest-1",
                "display_name": "이름",
                "participant_type": "human",
                "role": "human",
            }
        )
        merged = self.store.upsert_membership(
            {"meeting_id": ROOM, "participant_id": "guest-1", "status": "online"}
        )
        self.assertEqual(merged["display_name"], "이름")
        self.assertEqual(merged["participant_type"], "human")
        self.assertEqual(merged["status"], "online")

    def test_mute_state_persists_and_placeholder_created_for_live_only_participants(self):
        record = self.store.set_membership_muted(ROOM, "agent-live", True)
        self.assertTrue(record["muted"])
        self.assertEqual(record["source"], "moderation")
        self.assertTrue(self.store.membership_muted(ROOM, "agent-live"))
        self.store.set_membership_muted(ROOM, "agent-live", False)
        self.assertFalse(self.store.membership_muted(ROOM, "agent-live"))

    def test_remove_membership(self):
        self.store.upsert_membership({"meeting_id": ROOM, "participant_id": "guest-1"})
        self.assertTrue(self.store.remove_membership(ROOM, "guest-1"))
        self.assertFalse(self.store.remove_membership(ROOM, "guest-1"))
        self.assertEqual(self.store.list_memberships(ROOM), [])

    def test_upsert_requires_participant_id(self):
        with self.assertRaises(ValueError):
            self.store.upsert_membership({"meeting_id": ROOM, "display_name": "익명"})


class MigrationTests(IdentityStoreTestCase):
    def test_legacy_members_json_imported_once_when_db_empty(self):
        legacy = {
            "members": [
                {
                    "meeting_id": ROOM,
                    "participant_id": "guest-old",
                    "display_name": "옛사람",
                    "role": "human",
                    "participant_type": "human",
                    "muted": True,
                    "source": "room_invite",
                }
            ]
        }
        (self.root / "room_members.json").write_text(json.dumps(legacy), encoding="utf-8")
        store = identity_store_for_output_root(self.root)
        members = store.list_memberships(ROOM)
        self.assertEqual(len(members), 1)
        self.assertEqual(members[0]["display_name"], "옛사람")
        self.assertTrue(members[0]["muted"])

    def test_legacy_users_json_import(self):
        users_json = self.root / "users.json"
        users_json.write_text(
            json.dumps(
                {
                    "users": {
                        "device:feedbeef": {
                            "user_id": "u-feedbeef",
                            "participant_id": "guest-feed",
                            "auth_provider": "device",
                            "display_name": "이전유저",
                            "participant_type": "human",
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        imported = migrate_legacy_users_json(self.store, users_json)
        self.assertEqual(imported, 1)
        user = self.store.user_for_credential("device:feedbeef")
        self.assertEqual(user["user_id"], "u-feedbeef")
        self.assertEqual(user["participant_id"], "guest-feed")

    def test_corrupt_or_missing_legacy_files_are_ignored(self):
        self.assertEqual(migrate_legacy_members_json(self.store, self.root / "absent.json"), 0)
        bad = self.root / "bad.json"
        bad.write_text("{nope", encoding="utf-8")
        self.assertEqual(migrate_legacy_users_json(self.store, bad), 0)


class SchemaConstraintTests(IdentityStoreTestCase):
    def test_credentials_cascade_when_user_deleted(self):
        user = self.store.resolve_credential_user("device:cascade1")
        # `with sqlite3.connect(...)` commits but does NOT close — wrap in
        # closing() so the connection is released (no ResourceWarning).
        with closing(sqlite3.connect(self.store.db_path)) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("DELETE FROM users WHERE user_id = ?", (user["user_id"],))
            connection.commit()
        self.assertIsNone(self.store.user_for_credential("device:cascade1"))


class UsageAccountingTests(IdentityStoreTestCase):
    def _ev(self, **kw):
        base = {
            "user_id": "u-1", "participant_id": "guest-1", "meeting_id": ROOM,
            "provider": "openrouter", "model": "llama-3.3-70b:free",
            "input_tokens": 100, "output_tokens": 40, "cost_owner": "free",
        }
        base.update(kw)
        return base

    def test_record_and_aggregate(self):
        self.store.record_usage(self._ev())
        self.store.record_usage(self._ev(input_tokens=50, output_tokens=10))
        s = self.store.usage_summary()
        self.assertEqual(s["events"], 2)
        self.assertEqual(s["input_tokens"], 150)
        self.assertEqual(s["output_tokens"], 50)

    def test_summary_filters_by_user_and_meeting(self):
        self.store.record_usage(self._ev(user_id="u-a", meeting_id="room-a"))
        self.store.record_usage(self._ev(user_id="u-b", meeting_id="room-b"))
        self.assertEqual(self.store.usage_summary(user_id="u-a")["events"], 1)
        self.assertEqual(self.store.usage_summary(meeting_id="room-b")["events"], 1)
        self.assertEqual(self.store.usage_summary()["events"], 2)

    def test_by_model_breakdown(self):
        self.store.record_usage(self._ev(model="A", input_tokens=10, output_tokens=0))
        self.store.record_usage(self._ev(model="B", input_tokens=200, output_tokens=100))
        rows = self.store.usage_summary()["by_model"]
        self.assertEqual(rows[0]["model"], "B")  # ordered by total tokens desc
        self.assertEqual({r["model"] for r in rows}, {"A", "B"})

    def test_garbage_token_counts_coerced_to_zero(self):
        self.store.record_usage(self._ev(input_tokens="oops", output_tokens=None))
        s = self.store.usage_summary()
        self.assertEqual(s["input_tokens"], 0)
        self.assertEqual(s["output_tokens"], 0)

    def test_estimated_flag_recorded_and_counted(self):
        self.store.record_usage(self._ev())                       # authoritative (no estimated)
        self.store.record_usage(self._ev(estimated=True))         # estimated
        s = self.store.usage_summary()
        self.assertEqual(s["events"], 2)
        self.assertEqual(s["estimated_events"], 1)

    def test_estimated_column_added_to_preexisting_db(self):
        # A usage_events table created WITHOUT the estimated column must gain it
        # via the additive migration (proves schema evolution doesn't break).
        from contextlib import closing as _closing
        path = self.root / "old.db"
        with _closing(sqlite3.connect(path)) as c:
            c.execute(
                "CREATE TABLE usage_events (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " created_at TEXT, user_id TEXT, participant_id TEXT, meeting_id TEXT,"
                " provider TEXT, model TEXT, input_tokens INTEGER, output_tokens INTEGER, cost_owner TEXT)"
            )
            c.commit()
        store = SqliteIdentityStore(path)  # _ensure_schema runs the migration
        store.record_usage(self._ev(estimated=True))
        self.assertEqual(store.usage_summary()["estimated_events"], 1)

    def test_since_filter(self):
        self.store.record_usage(self._ev(created_at="2026-06-01T00:00:00+00:00"))
        self.store.record_usage(self._ev(created_at="2026-06-15T00:00:00+00:00"))
        self.assertEqual(self.store.usage_summary(since="2026-06-10T00:00:00+00:00")["events"], 1)


class BackendAbstractionTests(IdentityStoreTestCase):
    def test_sqlite_store_satisfies_backend_protocol(self):
        self.assertIsInstance(self.store, IdentityBackend)
        self.assertIs(SqliteIdentityStore, IdentityStore)

    def test_make_identity_backend_builds_sqlite(self):
        backend = make_identity_backend("sqlite", db_path=self.root / "made.db")
        self.assertIsInstance(backend, IdentityBackend)
        # functions through the contract
        user = backend.resolve_credential_user("device:made1", display_name="만든이")
        self.assertEqual(user["display_name"], "만든이")

    def test_unregistered_backend_raises_clear_error(self):
        with self.assertRaises(NotImplementedError) as ctx:
            make_identity_backend("postgres")
        self.assertIn("postgres", str(ctx.exception))
        self.assertIn("register_identity_backend", str(ctx.exception))

    def test_a_custom_backend_can_be_registered_and_selected(self):
        # Proves the swap point: a non-sqlite backend slots in by registration,
        # and consumers selecting via make_identity_backend get it unchanged.
        sentinel = self.store
        register_identity_backend("memory-test", lambda **_: sentinel)
        try:
            self.assertIs(make_identity_backend("memory-test"), sentinel)
        finally:
            # don't leak the test backend into the global registry
            from agentsassemble import identity_store as mod
            mod._BACKEND_FACTORIES.pop("memory-test", None)


if __name__ == "__main__":
    unittest.main()
