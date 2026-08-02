"""Public account identity derived independently from device credentials."""
from __future__ import annotations

import hashlib
from uuid import NAMESPACE_URL, uuid5

from agentsassemble.room.text import clean_room_text


_ACCOUNT_NAMESPACE = uuid5(NAMESPACE_URL, "https://agentsassemble.app/accounts")


class AccountLinkConflict(ValueError):
    """An external account or device credential already belongs elsewhere."""

    def __init__(self, message: str, *, code: str = "account_link_conflict") -> None:
        super().__init__(message)
        self.code = code


def external_account_identity(provider: object, subject: object) -> tuple[str, str]:
    """Return a stable public account id and a non-reversible lookup key."""

    clean_provider = clean_room_text(provider, limit=32).lower()
    clean_subject = str(subject or "").strip()
    if not clean_provider or not clean_subject or len(clean_subject) > 512:
        raise ValueError("External account identity is incomplete.")
    fingerprint = hashlib.sha256(
        f"{clean_provider}\0{clean_subject}".encode("utf-8")
    ).hexdigest()
    account_id = f"acct-{uuid5(_ACCOUNT_NAMESPACE, fingerprint)}"
    return account_id, fingerprint


__all__ = ["AccountLinkConflict", "external_account_identity"]
