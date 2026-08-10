"""Security-sensitive SQLite mutations for consumed operator pairings."""
from __future__ import annotations

import sqlite3

from agentsassemble.identity.repository import LOCAL_OPERATOR_USER_ID


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
