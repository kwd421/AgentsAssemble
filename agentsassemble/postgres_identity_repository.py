"""PostgreSQL implementation of the complete IdentityBackend contract."""
from __future__ import annotations

from datetime import UTC, datetime

from psycopg.rows import dict_row

from agentsassemble.postgres_connection_pool import (
    BoundedPostgresConnectionPool,
    PoolFactory,
    PostgresPoolSettings,
)
from agentsassemble.postgres_identity_preferences import (
    read_room_preferences,
    update_room_preferences,
)
from agentsassemble.postgres_identity_roster import (
    count_memberships,
    delete_room,
    get_membership,
    get_room,
    list_memberships,
    list_rooms,
    remove_membership,
    set_membership_muted,
    set_room_archived,
    touch_room,
    upsert_membership,
    upsert_room,
)
from agentsassemble.postgres_identity_usage import record_usage, usage_summary
from agentsassemble.postgres_identity_users import (
    claim_local_operator_credential,
    consume_operator_pairing,
    count_users,
    create_operator_pairing,
    get_user,
    operator_pairing_for_fingerprint,
    operator_user_id,
    resolve_credential_user,
    revoke_operator_pairing,
    set_user_operator,
    update_operator_pairing_redemption,
    user_for_credential,
    user_for_participant,
)
from agentsassemble.room_user_preferences import RoomUserPreferencesRecord


class PostgresIdentityRepository:
    """Hosted identity authority backed by the room PostgreSQL database."""

    def __init__(
        self,
        dsn: str,
        *,
        pool_settings: PostgresPoolSettings | None = None,
        pool_factory: PoolFactory | None = None,
    ) -> None:
        self._pool = BoundedPostgresConnectionPool(
            dsn,
            connection_kwargs={"row_factory": dict_row},
            settings=pool_settings,
            pool_factory=pool_factory,
        )

    def __repr__(self) -> str:
        return "PostgresIdentityRepository(configured=True)"

    def count_users(self) -> int:
        with self._pool.connection() as connection:
            return count_users(connection)

    def count_memberships(self) -> int:
        with self._pool.connection() as connection:
            return count_memberships(connection)

    def user_for_credential(self, auth_key: str) -> dict[str, object] | None:
        with self._pool.connection() as connection:
            return user_for_credential(connection, auth_key)

    def get_user(self, user_id: str) -> dict[str, object] | None:
        with self._pool.connection() as connection:
            return get_user(connection, user_id)

    def user_for_participant(self, participant_id: str) -> dict[str, object] | None:
        with self._pool.connection() as connection:
            return user_for_participant(connection, participant_id)

    def resolve_credential_user(
        self,
        auth_key: str,
        *,
        provider: str = "device",
        user_id: str = "",
        participant_id: str = "",
        display_name: str = "",
        avatar_image_url: str = "",
        participant_type: str = "",
    ) -> dict[str, object] | None:
        with self._pool.connection() as connection, connection.transaction():
            return resolve_credential_user(
                connection,
                auth_key,
                provider=provider,
                user_id=user_id,
                participant_id=participant_id,
                display_name=display_name,
                avatar_image_url=avatar_image_url,
                participant_type=participant_type,
                now=_now(),
            )

    def set_user_operator(self, user_id: str, is_operator: bool) -> bool:
        with self._pool.connection() as connection, connection.transaction():
            return set_user_operator(connection, user_id, is_operator)

    def claim_local_operator_credential(
        self,
        auth_key: str,
        *,
        provider: str = "device",
        display_name: str = "",
    ) -> dict[str, object] | None:
        with self._pool.connection() as connection, connection.transaction():
            return claim_local_operator_credential(
                connection,
                auth_key,
                provider=provider,
                display_name=display_name,
                now=_now(),
            )

    def create_operator_pairing(
        self,
        *,
        pairing_id: str,
        token_fingerprint: str,
        room_id: str,
        target_origin: str,
        created_at: str,
        expires_at: str,
    ) -> dict[str, object]:
        with self._pool.connection() as connection, connection.transaction():
            return create_operator_pairing(
                connection,
                pairing_id=pairing_id,
                token_fingerprint=token_fingerprint,
                room_id=room_id,
                target_origin=target_origin,
                created_at=created_at,
                expires_at=expires_at,
            )

    def operator_pairing_for_fingerprint(
        self,
        token_fingerprint: str,
    ) -> dict[str, object] | None:
        with self._pool.connection() as connection:
            return operator_pairing_for_fingerprint(connection, token_fingerprint)

    def consume_operator_pairing(
        self,
        *,
        token_fingerprint: str,
        target_origin: str,
        auth_key: str,
        used_at: str,
    ) -> dict[str, object]:
        with self._pool.connection() as connection, connection.transaction():
            return consume_operator_pairing(
                connection,
                token_fingerprint=token_fingerprint,
                target_origin=target_origin,
                auth_key=auth_key,
                used_at=used_at,
            )

    def update_operator_pairing_redemption(
        self,
        *,
        pairing_id: str,
        auth_key: str,
        status: str,
        completed_at: str = "",
        session_fingerprint: str = "",
        failure_code: str = "",
    ) -> dict[str, object] | None:
        with self._pool.connection() as connection, connection.transaction():
            return update_operator_pairing_redemption(
                connection,
                pairing_id=pairing_id,
                auth_key=auth_key,
                status=status,
                completed_at=completed_at,
                session_fingerprint=session_fingerprint,
                failure_code=failure_code,
            )

    def revoke_operator_pairing(self, pairing_id: str, *, revoked_at: str) -> bool:
        with self._pool.connection() as connection, connection.transaction():
            return revoke_operator_pairing(
                connection,
                pairing_id,
                revoked_at=revoked_at,
            )

    def participant_is_operator(self, participant_id: str) -> bool:
        user = self.user_for_participant(participant_id)
        return bool(user and user.get("is_operator"))

    def operator_user_id(self) -> str:
        with self._pool.connection() as connection:
            return operator_user_id(connection)

    def list_memberships(self, meeting_id: str = "") -> list[dict[str, object]]:
        with self._pool.connection() as connection:
            return list_memberships(connection, meeting_id)

    def get_membership(
        self,
        meeting_id: str,
        participant_id: str,
    ) -> dict[str, object] | None:
        with self._pool.connection() as connection:
            return get_membership(connection, meeting_id, participant_id)

    def upsert_membership(self, record: dict[str, object]) -> dict[str, object]:
        with self._pool.connection() as connection, connection.transaction():
            return upsert_membership(connection, record, now=_now())

    def remove_membership(self, meeting_id: str, participant_id: str) -> bool:
        with self._pool.connection() as connection, connection.transaction():
            return remove_membership(connection, meeting_id, participant_id)

    def set_membership_muted(
        self,
        meeting_id: str,
        participant_id: str,
        muted: bool,
    ) -> dict[str, object]:
        with self._pool.connection() as connection, connection.transaction():
            return set_membership_muted(
                connection,
                meeting_id,
                participant_id,
                muted,
                now=_now(),
            )

    def membership_muted(self, meeting_id: str, participant_id: str) -> bool:
        member = self.get_membership(meeting_id, participant_id)
        return bool(member and member.get("muted"))

    def upsert_room(
        self,
        *,
        room_id: str,
        owner_id: str = "",
        label: str = "",
        origin: str = "",
    ) -> dict[str, object]:
        with self._pool.connection() as connection, connection.transaction():
            return upsert_room(
                connection,
                room_id=room_id,
                owner_id=owner_id,
                label=label,
                origin=origin,
                now=_now(),
            )

    def list_rooms(
        self,
        *,
        owner_id: str = "",
        include_archived: bool = False,
    ) -> list[dict[str, object]]:
        with self._pool.connection() as connection:
            return list_rooms(
                connection,
                owner_id=owner_id,
                include_archived=include_archived,
            )

    def get_room(self, room_id: str) -> dict[str, object] | None:
        with self._pool.connection() as connection:
            return get_room(connection, room_id)

    def set_room_archived(self, room_id: str, archived: bool) -> bool:
        with self._pool.connection() as connection, connection.transaction():
            return set_room_archived(connection, room_id, archived)

    def touch_room(self, room_id: str) -> None:
        with self._pool.connection() as connection, connection.transaction():
            touch_room(connection, room_id, now=_now())

    def delete_room(self, room_id: str) -> bool:
        with self._pool.connection() as connection, connection.transaction():
            return delete_room(connection, room_id)

    def room_preferences(
        self,
        user_id: str,
        room_id: str,
    ) -> RoomUserPreferencesRecord:
        with self._pool.connection() as connection:
            return read_room_preferences(connection, user_id, room_id)

    def update_room_preferences(
        self,
        user_id: str,
        room_id: str,
        updates: dict[str, object],
    ) -> RoomUserPreferencesRecord:
        with self._pool.connection() as connection, connection.transaction():
            return update_room_preferences(
                connection,
                user_id,
                room_id,
                updates,
                now=_now(),
            )

    def record_usage(self, event: dict[str, object]) -> None:
        with self._pool.connection() as connection, connection.transaction():
            record_usage(connection, event, now=_now())

    def usage_summary(
        self,
        *,
        user_id: str = "",
        meeting_id: str = "",
        since: str = "",
    ) -> dict[str, object]:
        with self._pool.connection() as connection:
            return usage_summary(
                connection,
                user_id=user_id,
                meeting_id=meeting_id,
                since=since,
            )

    def clear(self) -> None:
        """Testing helper; production deletion remains domain-specific."""
        with self._pool.connection() as connection, connection.transaction():
            connection.execute(
                """TRUNCATE TABLE
                       identity_usage_events,
                       identity_room_user_preferences,
                       identity_room_registry,
                       identity_memberships,
                       identity_operator_pairings,
                       identity_credentials,
                       identity_users
                   RESTART IDENTITY CASCADE"""
            )

    def close(self) -> None:
        self._pool.close()

    def public_diagnostics(self) -> dict[str, object]:
        return {"backend": "postgresql", "pool": self._pool.public_diagnostics()}


def _now() -> str:
    return datetime.now(UTC).isoformat()
