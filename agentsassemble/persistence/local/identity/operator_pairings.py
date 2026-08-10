"""Security-sensitive SQLite mutations for consumed operator pairings."""
from __future__ import annotations

import sqlite3
from contextlib import closing

from agentsassemble.identity.repository import LOCAL_OPERATOR_USER_ID
from agentsassemble.room.text import clean_room_text as clean_lobby_text


class SqliteOperatorPairingsMixin:
    """Security-sensitive lookup operations for consumed pairing grants."""

    def operator_pairing_for_auth_key(
        self,
        auth_key: str,
    ) -> dict[str, object] | None:
        clean_auth_key = clean_lobby_text(auth_key, limit=128)
        if not clean_auth_key:
            return None
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM operator_pairings WHERE consumed_auth_key = ?"
                " ORDER BY used_at DESC LIMIT 1",
                (clean_auth_key,),
            ).fetchone()
        return self._operator_pairing_dict(row) if row else None


def revoke_operator_pairing_grant(
    connection: sqlite3.Connection,
    pairing_id: str,
    *,
    revoked_at: str,
) -> bool:
    """Revoke both the pairing record and the credential it granted."""
    pairing = connection.execute(
        "SELECT consumed_auth_key FROM operator_pairings WHERE pairing_id = ?",
        (pairing_id,),
    ).fetchone()
    if pairing is None:
        return False
    connection.execute(
        "UPDATE operator_pairings SET revoked_at = ?"
        " WHERE pairing_id = ? AND revoked_at = ''",
        (revoked_at, pairing_id),
    )
    consumed_auth_key = str(pairing["consumed_auth_key"] or "")
    if consumed_auth_key:
        connection.execute(
            "DELETE FROM credentials WHERE auth_key = ? AND user_id = ?",
            (consumed_auth_key, LOCAL_OPERATOR_USER_ID),
        )
    return True
