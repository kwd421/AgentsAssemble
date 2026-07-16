"""Room command and policy errors."""

from __future__ import annotations


class RoomCommandRejected(ValueError):
    def __init__(self, message: str, *, code: str = "rejected") -> None:
        super().__init__(message)
        self.code = code
