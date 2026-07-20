import unittest

from agentsassemble.legacy_lobby_commands import (
    LegacyLobbyCommandService as compatibility_lobby_service,
)
from agentsassemble.legacy_meeting_lifecycle import (
    LegacyMeetingLifecycleService as compatibility_lifecycle_service,
)
from agentsassemble.legacy_meeting_operation_projection import (
    meeting_finalize_operation_details as compatibility_finalize_projection,
)
from agentsassemble.legacy_meeting_queries import (
    LegacyMeetingQueryService as compatibility_query_service,
)
from agentsassemble.legacy_meeting_records import (
    read_meeting_record as compatibility_read_meeting_record,
)
from agentsassemble.legacy_official_rounds import (
    LegacyOfficialRoundService as compatibility_round_service,
)
from agentsassemble.legacy_official_turns import (
    LegacyOfficialTurnService as compatibility_turn_service,
)
from agentsassemble.legacy_review_checkpoint import (
    LegacyReviewCheckpointService as compatibility_checkpoint_service,
)
from agentsassemble.legacy_turn_scheduler import meeting_turn_lock as compatibility_turn_lock
from agentsassemble.legacy.meeting.lifecycle import LegacyMeetingLifecycleService
from agentsassemble.legacy.meeting.lobby_commands import LegacyLobbyCommandService
from agentsassemble.legacy.meeting.operation_projection import (
    meeting_finalize_operation_details,
)
from agentsassemble.legacy.meeting.official_rounds import LegacyOfficialRoundService
from agentsassemble.legacy.meeting.official_turns import LegacyOfficialTurnService
from agentsassemble.legacy.meeting.queries import LegacyMeetingQueryService
from agentsassemble.legacy.meeting.records import read_meeting_record
from agentsassemble.legacy.meeting.review_checkpoint import LegacyReviewCheckpointService
from agentsassemble.legacy.meeting.turn_scheduler import meeting_turn_lock


class LegacyMeetingPackageTests(unittest.TestCase):
    def test_root_modules_export_owned_implementations(self) -> None:
        pairs = (
            (compatibility_lobby_service, LegacyLobbyCommandService),
            (compatibility_lifecycle_service, LegacyMeetingLifecycleService),
            (compatibility_finalize_projection, meeting_finalize_operation_details),
            (compatibility_query_service, LegacyMeetingQueryService),
            (compatibility_read_meeting_record, read_meeting_record),
            (compatibility_round_service, LegacyOfficialRoundService),
            (compatibility_turn_service, LegacyOfficialTurnService),
            (compatibility_checkpoint_service, LegacyReviewCheckpointService),
            (compatibility_turn_lock, meeting_turn_lock),
        )
        for compatibility_export, owned_export in pairs:
            with self.subTest(export=owned_export.__name__):
                self.assertIs(compatibility_export, owned_export)


if __name__ == "__main__":
    unittest.main()
