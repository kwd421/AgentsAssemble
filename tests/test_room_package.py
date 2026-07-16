from __future__ import annotations

import unittest

import agentsassemble.bridge_stop_confirmation as compatibility_bridge_stop
import agentsassemble.room_command_uow as compatibility_command_uow
import agentsassemble.room_commands as compatibility_commands
import agentsassemble.room_context as compatibility_context
import agentsassemble.room_errors as compatibility_errors
import agentsassemble.room_event_broker as compatibility_event_broker
import agentsassemble.room_agent_lifecycle as compatibility_lifecycle
import agentsassemble.room_members as compatibility_members
import agentsassemble.room_projection as compatibility_projection
import agentsassemble.room_repository as compatibility_repository
import agentsassemble.room_types as compatibility_types
import agentsassemble.room_turn_context as compatibility_turn_context
import agentsassemble.room_turn_coordinator as compatibility_turn_coordinator
from agentsassemble.room import bridge_stop_confirmation as owned_bridge_stop
from agentsassemble.room import command_uow as owned_command_uow
from agentsassemble.room import commands as owned_commands
from agentsassemble.room import context as owned_context
from agentsassemble.room import errors as owned_errors
from agentsassemble.room import event_broker as owned_event_broker
from agentsassemble.room import agent_lifecycle as owned_lifecycle
from agentsassemble.room import moderation as owned_moderation
from agentsassemble.room import projection as owned_projection
from agentsassemble.room import repository as owned_repository
from agentsassemble.room import types as owned_types
from agentsassemble.room import turn_context as owned_turn_context
from agentsassemble.room import turn_coordinator as owned_turn_coordinator


class RoomPackageTests(unittest.TestCase):
    def test_room_turn_coordinator_root_module_exports_owned_service(self) -> None:
        for name in (
            "EnsureRoom",
            "PendingEventPartition",
            "PreparedFinalMessage",
            "ProviderLookup",
            "RecoveryScheduler",
            "RoomTurnCoordinator",
            "SessionCallback",
            "TurnFinalizationWriter",
            "TurnPacketBuilder",
            "dedupe_event_ids",
            "message_delta_text",
            "now",
            "provider_process_exited",
            "require_active_turn_phase",
            "room_message_text",
            "safe_bounded_int",
            "validate_turn_phase_transition",
        ):
            self.assertIs(
                getattr(compatibility_turn_coordinator, name),
                getattr(owned_turn_coordinator, name),
            )

    def test_room_turn_context_root_module_exports_owned_builder(self) -> None:
        for name in (
            "BoundedProviderContext",
            "DEFAULT_ROOM_TURN_MAX_PROMPT_CHARS",
            "DEFAULT_ROOM_TURN_MAX_RECENT_EVENTS",
            "MAX_MODEL_VISIBLE_MEDIA_SIZE",
            "MODEL_VISIBLE_MEDIA_REPRESENTATIONS",
            "UNSUPPORTED_MEDIA_AUDIT_NOTE",
            "_agent_turn_prompt",
            "_bound_room_turn_packet",
            "_nonnegative_int",
            "build_provider_bootstrap_input",
            "build_provider_recovery_input",
            "build_provider_turn_input",
            "build_room_turn_packet",
            "room_memory_from_session",
        ):
            self.assertIs(
                getattr(compatibility_turn_context, name),
                getattr(owned_turn_context, name),
            )

    def test_room_context_root_module_exports_owned_projection(self) -> None:
        for name in (
            "DEFAULT_ROOM_CONTEXT_CHARS",
            "DEFAULT_ROOM_CONTEXT_MESSAGES",
            "MAX_ROOM_CONTEXT_MESSAGES",
            "RoomContextWindow",
            "project_room_context",
        ):
            self.assertIs(
                getattr(compatibility_context, name),
                getattr(owned_context, name),
            )

    def test_room_agent_lifecycle_root_module_exports_owned_service(self) -> None:
        for name in (
            "AgentBridgeManager",
            "EnsureProviderSession",
            "PendingAssignment",
            "PrepareSessionReset",
            "ProviderLookup",
            "RecoveryScheduler",
            "RoomAgentLifecycle",
            "SessionCallback",
            "SessionRevoker",
            "schedule_daemon_timer",
        ):
            self.assertIs(
                getattr(compatibility_lifecycle, name),
                getattr(owned_lifecycle, name),
            )

    def test_room_members_module_reexports_owned_moderation(self) -> None:
        for name in (
            "is_room_member_muted",
            "remove_room_member",
            "set_room_member_muted",
        ):
            self.assertIs(
                getattr(compatibility_members, name),
                getattr(owned_moderation, name),
            )

    def test_bridge_stop_root_module_exports_owned_confirmation(self) -> None:
        for name in (
            "BridgeStopConfirmationError",
            "ExternalBridgeStopCoordinator",
        ):
            self.assertIs(
                getattr(compatibility_bridge_stop, name),
                getattr(owned_bridge_stop, name),
            )

    def test_room_command_uow_root_module_exports_owned_transaction(self) -> None:
        for name in (
            "RoomCommandIdempotencyConflict",
            "RoomCommandNotFinalized",
            "RoomCommandUnitOfWork",
            "command_payload_hash",
        ):
            self.assertIs(
                getattr(compatibility_command_uow, name),
                getattr(owned_command_uow, name),
            )

    def test_room_command_root_module_exports_owned_policy(self) -> None:
        for name in (
            "ROOM_COMMAND_ACTIONS",
            "ParsedRoomCommand",
            "RoomCommandValidationError",
            "capabilities_for_identity",
            "parse_room_command",
        ):
            self.assertIs(
                getattr(compatibility_commands, name),
                getattr(owned_commands, name),
            )

    def test_room_error_root_module_exports_owned_errors(self) -> None:
        self.assertIs(
            compatibility_errors.RoomCommandRejected,
            owned_errors.RoomCommandRejected,
        )

    def test_room_event_broker_root_module_exports_owned_fanout(self) -> None:
        for name in (
            "ROOM_EVENT_STREAM",
            "RoomEventBroker",
            "RoomSocketChannel",
        ):
            self.assertIs(
                getattr(compatibility_event_broker, name),
                getattr(owned_event_broker, name),
            )

    def test_room_type_root_module_exports_owned_shapes(self) -> None:
        for name in (
            "AgentSession",
            "RoomActor",
            "RoomCommand",
            "RoomEvent",
            "RoomParticipant",
            "TurnAssignment",
        ):
            self.assertIs(
                getattr(compatibility_types, name),
                getattr(owned_types, name),
            )

    def test_room_projection_root_module_exports_owned_projection(self) -> None:
        for name in (
            "PUBLIC_ACTIVITY_LABELS",
            "merged_latency",
            "public_activity",
            "public_event",
            "public_participant",
            "public_runtime_diagnostics",
            "public_session",
            "runtime_diagnostic_fields",
        ):
            self.assertIs(
                getattr(compatibility_projection, name),
                getattr(owned_projection, name),
            )

    def test_room_repository_root_module_exports_owned_contract(self) -> None:
        for name in (
            "CommandRecord",
            "EventListener",
            "EventRecord",
            "ParticipantRecord",
            "RoomRecord",
            "RoomRepository",
            "RoomTransaction",
            "SessionRecord",
        ):
            self.assertIs(
                getattr(compatibility_repository, name),
                getattr(owned_repository, name),
            )


if __name__ == "__main__":
    unittest.main()
