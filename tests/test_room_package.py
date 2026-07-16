from __future__ import annotations

import unittest

import agentsassemble.bridge_stop_confirmation as compatibility_bridge_stop
import agentsassemble.room_channels as compatibility_channels
import agentsassemble.room_command_uow as compatibility_command_uow
import agentsassemble.room_commands as compatibility_commands
import agentsassemble.room_context as compatibility_context
import agentsassemble.room_errors as compatibility_errors
import agentsassemble.room_event_broker as compatibility_event_broker
import agentsassemble.room_global_settings as compatibility_global_settings
import agentsassemble.room_agent_lifecycle as compatibility_lifecycle
import agentsassemble.room_members as compatibility_members
import agentsassemble.room_projection as compatibility_projection
import agentsassemble.room_realtime as compatibility_realtime
import agentsassemble.room_repository as compatibility_repository
import agentsassemble.room_repository_records as compatibility_repository_records
import agentsassemble.room_setting_values as compatibility_setting_values
import agentsassemble.room_settings_service as compatibility_settings_service
import agentsassemble.room_types as compatibility_types
import agentsassemble.room_turn_context as compatibility_turn_context
import agentsassemble.room_turn_coordinator as compatibility_turn_coordinator
import agentsassemble.room_user_preferences as compatibility_user_preferences
from agentsassemble.room import bridge_stop_confirmation as owned_bridge_stop
from agentsassemble.room import channels as owned_channels
from agentsassemble.room import command_uow as owned_command_uow
from agentsassemble.room import commands as owned_commands
from agentsassemble.room import context as owned_context
from agentsassemble.room import errors as owned_errors
from agentsassemble.room import event_broker as owned_event_broker
from agentsassemble.room import global_settings as owned_global_settings
from agentsassemble.room import agent_lifecycle as owned_lifecycle
from agentsassemble.room import moderation as owned_moderation
from agentsassemble.room import projection as owned_projection
from agentsassemble.room import realtime as owned_realtime
from agentsassemble.room import repository as owned_repository
from agentsassemble.room import repository_records as owned_repository_records
from agentsassemble.room import setting_values as owned_setting_values
from agentsassemble.room import settings_service as owned_settings_service
from agentsassemble.room import types as owned_types
from agentsassemble.room import turn_context as owned_turn_context
from agentsassemble.room import turn_coordinator as owned_turn_coordinator
from agentsassemble.room import user_preferences as owned_user_preferences


class RoomPackageTests(unittest.TestCase):
    def test_room_settings_service_root_module_exports_owned_service(self) -> None:
        for name in (
            "room_settings_payload",
            "update_room_settings",
        ):
            self.assertIs(
                getattr(compatibility_settings_service, name),
                getattr(owned_settings_service, name),
            )

    def test_room_repository_records_root_module_exports_owned_normalizers(self) -> None:
        for name in (
            "ACTIVE_PARTICIPANT_STATUSES",
            "PARTICIPANT_STATUSES",
            "ROOM_STATUSES",
            "SESSION_STATUSES",
            "build_room_event",
            "build_room_record",
            "clean_event_type",
            "clean_participant_id",
            "clean_room_id",
            "clean_session_id",
            "merge_participant_record",
            "merge_session_record",
            "participant_status",
            "room_status",
            "safe_media_filename",
            "session_status",
            "strip_private_event_fields",
            "update_participant_record",
            "update_session_record",
            "utc_now",
        ):
            self.assertIs(
                getattr(compatibility_repository_records, name),
                getattr(owned_repository_records, name),
            )

    def test_room_global_settings_root_module_exports_owned_record(self) -> None:
        for name in (
            "DEFAULT_CONVERSATION_MODE",
            "DEFAULT_MAX_RELAY_TURNS",
            "MAX_RELAY_TURNS",
            "MIN_RELAY_TURNS",
            "ROOM_APPEARANCE_FIELDS",
            "ROOM_CHANNEL_FIELDS",
            "ROOM_GLOBAL_SETTING_FIELDS",
            "ROOM_LABEL_LIMIT",
            "RoomGlobalAppearance",
            "RoomGlobalChannel",
            "RoomGlobalSettingsRecord",
            "default_room_global_settings",
            "merge_room_global_settings",
            "validate_room_global_settings",
        ):
            self.assertIs(
                getattr(compatibility_global_settings, name),
                getattr(owned_global_settings, name),
            )

    def test_room_user_preferences_root_module_exports_owned_record(self) -> None:
        for name in (
            "BUILTIN_CHANNEL_IDS",
            "CHANNEL_NOTIFICATION_VALUES",
            "MAX_PREFERENCE_CHANNELS",
            "READ_CURSOR_LIMIT",
            "ROOM_NOTIFICATION_VALUES",
            "ChannelPreference",
            "RoomUserPreferencesRecord",
            "default_room_user_preferences",
            "merge_room_user_preferences",
            "validate_room_user_preferences",
        ):
            self.assertIs(
                getattr(compatibility_user_preferences, name),
                getattr(owned_user_preferences, name),
            )

    def test_room_channels_root_module_exports_owned_rules(self) -> None:
        for name in (
            "CHANNEL_NAME_LIMIT",
            "CHANNEL_TYPES",
            "MAX_CHANNELS_PER_ROOM",
            "ChannelError",
            "add_channel",
            "channel_stream_filename",
            "clean_channel",
            "clean_channel_name",
            "clean_channel_type",
            "clean_channels",
            "find_channel",
            "is_channel_id",
            "remove_channel",
            "rename_channel",
            "reorder_channels",
        ):
            self.assertIs(
                getattr(compatibility_channels, name),
                getattr(owned_channels, name),
            )

    def test_room_setting_values_root_module_exports_owned_rules(self) -> None:
        for name in (
            "CONVERSATION_MODES",
            "IMAGE_URL_LIMIT",
            "ROOM_TEXT_LIMIT",
            "VALID_BANNER_PRESETS",
            "VALID_INVITE_SCOPES",
            "clean_room_asset_url",
            "clean_room_text",
            "clean_short_label",
        ):
            self.assertIs(
                getattr(compatibility_setting_values, name),
                getattr(owned_setting_values, name),
            )

    def test_room_realtime_root_module_exports_owned_controller(self) -> None:
        for name in (
            "AGENT_RUNTIME_PROFILE_KEYS",
            "AMBIENT_AGENT_RELAY_DEPTH",
            "NativeCliProviderSpec",
            "ProviderCatalog",
            "RoomCommandRejected",
            "RoomEventBroker",
            "RoomRealtimeController",
            "RoomSocketChannel",
            "default_native_cli_provider_specs",
            "validate_native_cli_provider_spec",
        ):
            self.assertIs(
                getattr(compatibility_realtime, name),
                getattr(owned_realtime, name),
            )

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
