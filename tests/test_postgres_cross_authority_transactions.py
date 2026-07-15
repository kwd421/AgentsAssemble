from __future__ import annotations

import hashlib
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, quote, urlsplit
from uuid import uuid4


_POSTGRES_DSN = os.environ.get("AGENTSASSEMBLE_TEST_POSTGRES_DSN", "").strip()
_POSTGRES_AVAILABLE = all(
    importlib.util.find_spec(name) is not None
    for name in ("alembic", "psycopg", "psycopg_pool", "sqlalchemy")
)

if _POSTGRES_AVAILABLE:
    import psycopg
    from psycopg import sql

    from agentsassemble.identity_store import LOCAL_OPERATOR_PARTICIPANT_ID
    from agentsassemble.operator_pairing import OperatorPairingService
    from agentsassemble.postgres_application_database import PostgresApplicationDatabase
    from agentsassemble.postgres_identity_repository import PostgresIdentityRepository
    from agentsassemble.postgres_invite_repository import PostgresInviteSessionRepository
    from agentsassemble.postgres_room_repository import PostgresRoomRepository
    from agentsassemble.postgres_room_schema import (
        POSTGRES_ROOM_AUTHORITY_ID,
        upgrade_postgres_room_schema,
    )
    from agentsassemble.room_admission_coordinator import RoomAdmissionCoordinator
    from agentsassemble.room_invite_application import InviteApplicationService
    from agentsassemble.room_session_service import RoomSessionService
    from agentsassemble.room_users import device_auth_key


@unittest.skipUnless(
    _POSTGRES_AVAILABLE and _POSTGRES_DSN,
    "AGENTSASSEMBLE_TEST_POSTGRES_DSN and the postgres extra are required",
)
class PostgresCrossAuthorityTransactionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema_name = f"agentsassemble_uow_{uuid4().hex[:12]}"
        with psycopg.connect(_POSTGRES_DSN, autocommit=True) as connection:
            connection.execute(
                sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(cls.schema_name))
            )
        cls.test_dsn = _dsn_with_search_path(_POSTGRES_DSN, cls.schema_name)
        upgrade_postgres_room_schema(cls.test_dsn)
        with psycopg.connect(cls.test_dsn) as connection:
            connection.execute(
                """INSERT INTO room_repository_authority(
                       authority_id, activated_at, source_backend, source_checksum
                   ) VALUES(%s, NOW(), %s, %s)""",
                (POSTGRES_ROOM_AUTHORITY_ID, "test", "cross-authority"),
            )
        cls._temporary_directory = tempfile.TemporaryDirectory()

    @classmethod
    def tearDownClass(cls) -> None:
        try:
            with psycopg.connect(_POSTGRES_DSN, autocommit=True) as connection:
                connection.execute(
                    sql.SQL("DROP SCHEMA {} CASCADE").format(
                        sql.Identifier(cls.schema_name)
                    )
                )
        finally:
            cls._temporary_directory.cleanup()

    def setUp(self) -> None:
        with psycopg.connect(self.test_dsn) as connection:
            connection.execute("TRUNCATE TABLE deleted_rooms, rooms CASCADE")
            connection.execute(
                """TRUNCATE TABLE
                       identity_usage_events,
                       identity_room_user_preferences,
                       identity_room_registry,
                       identity_memberships,
                       identity_operator_pairings,
                       identity_credentials,
                       identity_users,
                       room_admission_workflows,
                       room_access_sessions,
                       room_invite_used_nonces,
                       room_invites,
                       room_invite_authority
                   RESTART IDENTITY CASCADE"""
            )
        self.database = PostgresApplicationDatabase(self.test_dsn)
        self.rooms = PostgresRoomRepository(
            database=self.database,
            output_root=Path(self._temporary_directory.name),
        )
        self.identities = PostgresIdentityRepository(database=self.database)
        self.invite_repository = PostgresInviteSessionRepository(database=self.database)
        self.invites = InviteApplicationService(
            self.invite_repository,
            public_url=lambda: "https://room.example",
        )
        self.sessions = RoomSessionService(
            self.invite_repository,
            token_prefix="aas1",
            ttl_seconds=3600,
            token_key=self.invites.signing_secret,
        )

    def tearDown(self) -> None:
        self.invite_repository.close()
        self.identities.close()
        self.rooms.close()
        self.database.close()

    def test_admission_failure_rolls_back_every_authority_before_retry(self) -> None:
        self.rooms.create_room("room-a", label="Room A")
        invite = self.invites.create(
            room_url="http://127.0.0.1:8765",
            meeting_id="room-a",
            display_name="Guest",
            max_uses=2,
        )
        coordinator = RoomAdmissionCoordinator(
            invites=self.invites,
            sessions=self.sessions,
            identities=self.identities,
            rooms=self.rooms,
            transaction_boundary=self.database,
        )
        arguments = {
            "invite_token": str(invite["join_code"]),
            "request_id": "postgres-admission-failure",
            "display_name": "Known Guest",
            "device_token": "known-device-token",
        }

        with patch.object(
            self.identities,
            "upsert_membership",
            side_effect=RuntimeError("injected membership failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "injected membership failure"):
                coordinator.admit(**arguments)

        stored_invite = self.invite_repository.invite(str(invite["invite_id"]))
        self.assertEqual(stored_invite["use_count"], 0)
        self.assertEqual(self.invite_repository.list_sessions(), [])
        self.assertEqual(self.rooms.participants("room-a"), [])
        self.assertEqual(self.identities.list_memberships("room-a"), [])
        self.assertEqual(self.identities.count_users(), 0)
        with self.database.connection() as connection:
            workflow = connection.execute(
                "SELECT record_json FROM room_admission_workflows"
            ).fetchone()["record_json"]
        self.assertEqual(workflow["status"], "failed_retryable")

        admitted = coordinator.admit(**arguments)

        self.assertEqual(admitted["status"], "admitted")
        self.assertEqual(
            self.invite_repository.invite(str(invite["invite_id"]))["use_count"],
            1,
        )
        self.assertEqual(len(self.invite_repository.list_sessions()), 1)
        self.assertEqual(len(self.rooms.participants("room-a")), 1)
        self.assertEqual(len(self.identities.list_memberships("room-a")), 1)

    def test_pairing_claim_survives_but_failed_completion_rolls_back_other_writes(self) -> None:
        self.rooms.create_room("room-a", label="Room A")
        self.identities.claim_local_operator_credential(
            device_auth_key("local-operator-device"),
            display_name="SeiNel",
        )
        service = OperatorPairingService(
            identities=self.identities,
            rooms=self.rooms,
            sessions=self.sessions,
            transaction_boundary=self.database,
            token_factory=lambda: "fixed-pairing-secret",
        )
        created = service.create(
            room_id="room-a",
            public_url="https://public.example",
        )
        token = parse_qs(urlsplit(str(created["pairing_url"])).query)["token"][0]
        update = self.identities.update_operator_pairing_redemption

        def fail_completion(**kwargs: object) -> dict[str, object] | None:
            if kwargs.get("status") == "completed":
                raise RuntimeError("injected completion failure")
            return update(**kwargs)

        with patch.object(
            self.identities,
            "update_operator_pairing_redemption",
            side_effect=fail_completion,
        ):
            with self.assertRaisesRegex(RuntimeError, "injected completion failure"):
                service.redeem(
                    pairing_token=token,
                    device_token="public-origin-device",
                    request_origin="https://public.example",
                )

        fingerprint = hashlib.sha256(token.encode("utf-8")).hexdigest()
        pairing = self.identities.operator_pairing_for_fingerprint(fingerprint)
        self.assertEqual(pairing["redemption_status"], "failed_retryable")
        self.assertEqual(self.invite_repository.list_sessions(), [])
        self.assertEqual(
            self.rooms.participant("room-a", LOCAL_OPERATOR_PARTICIPANT_ID),
            {},
        )
        self.assertEqual(self.identities.list_memberships("room-a"), [])

        other_device = service.redeem(
            pairing_token=token,
            device_token="different-public-device",
            request_origin="https://public.example",
        )
        resumed = service.redeem(
            pairing_token=token,
            device_token="public-origin-device",
            request_origin="https://public.example",
        )

        self.assertEqual(other_device["reason"], "pairing_already_used")
        self.assertEqual(resumed["status"], "admitted")
        self.assertEqual(len(self.invite_repository.list_sessions()), 1)
        self.assertEqual(len(self.identities.list_memberships("room-a")), 1)


def _dsn_with_search_path(dsn: str, schema_name: str) -> str:
    separator = "&" if "?" in dsn else "?"
    option = quote(f"-csearch_path={schema_name}", safe="")
    return f"{dsn}{separator}options={option}"


if __name__ == "__main__":
    unittest.main()
