class RoomDeferredCleanupContract:
    def test_controller_close_cancels_deferred_member_access_cleanup(self):
        first = self._member_identity("leaving-first")
        second = self._member_identity("leaving-second")
        self.controller.connect(first)
        self.controller.connect(second)

        self._command("leave-before-close", "participant.leave", {}, first)
        self.assertTrue(
            self.controller.store.participant("general", "leaving-first")[
                "access_cleanup_pending"
            ]
        )
        self.recovery_scheduler.run_next()
        self.assertFalse(
            self.controller.store.participant("general", "leaving-first")[
                "access_cleanup_pending"
            ]
        )

        self._command("leave-at-close", "participant.leave", {}, second)
        self.assertTrue(
            self.controller.store.participant("general", "leaving-second")[
                "access_cleanup_pending"
            ]
        )
        self.controller.close()
        self.recovery_scheduler.run_next()

        self.assertTrue(
            self.controller.store.participant("general", "leaving-second")[
                "access_cleanup_pending"
            ]
        )

    @staticmethod
    def _member_identity(participant_id):
        return {
            "agent_id": participant_id,
            "display_name": participant_id,
            "participant_type": "human",
            "client_type": "browser",
            "invite_scope": "read_write",
            "meeting_id": "general",
            "operator": False,
        }


__all__ = ["RoomDeferredCleanupContract"]
