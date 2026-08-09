from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import cast
from unittest import TestCase

from agentsassemble.room.repository import RoomRepository


class RoomWriteBudgetRepositoryContractMixin:
    """Atomic room-wide write admission shared by every repository backend."""

    repository: RoomRepository

    def _write_budget_test_case(self) -> TestCase:
        return cast(TestCase, self)

    def test_room_write_budget_reservation_is_atomic_across_callers(self) -> None:
        self.repository.create_room("write-budget-room")

        def reserve(payload_bytes: int) -> bool:
            return self.repository.reserve_room_write_budget(
                "write-budget-room",
                window_started_at=1_000,
                command_limit=1,
                payload_byte_limit=1_000,
                payload_bytes=payload_bytes,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            admitted = list(executor.map(reserve, (10, 20)))

        case = self._write_budget_test_case()
        case.assertEqual(sorted(admitted), [False, True])
        case.assertFalse(reserve(1))
        case.assertTrue(
            self.repository.reserve_room_write_budget(
                "write-budget-room",
                window_started_at=1_060,
                command_limit=1,
                payload_byte_limit=1_000,
                payload_bytes=30,
            )
        )
        self.repository.create_room("write-budget-payload-room")
        case.assertTrue(
            self.repository.reserve_room_write_budget(
                "write-budget-payload-room",
                window_started_at=2_000,
                command_limit=10,
                payload_byte_limit=15,
                payload_bytes=10,
            )
        )
        case.assertFalse(
            self.repository.reserve_room_write_budget(
                "write-budget-payload-room",
                window_started_at=2_000,
                command_limit=10,
                payload_byte_limit=15,
                payload_bytes=6,
            )
        )
