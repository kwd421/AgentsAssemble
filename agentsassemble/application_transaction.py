"""Application transaction boundary shared by cross-authority workflows."""
from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Protocol


class ApplicationTransactionBoundary(Protocol):
    """Provide one transaction connection to every participating repository."""

    def transaction(self) -> AbstractContextManager[object]: ...
