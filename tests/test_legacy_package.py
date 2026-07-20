from __future__ import annotations

import unittest

import agentsassemble.legacy_live_agent_engagement as compatibility_engagement
import agentsassemble.legacy_live_agent_diagnostics as compatibility_diagnostics
import agentsassemble.legacy_live_agent_presence as compatibility_presence
import agentsassemble.legacy_live_agent_presence_projection as compatibility_presence_projection
import agentsassemble.legacy_live_agent_preflight as compatibility_preflight
import agentsassemble.legacy_live_agent_probe as compatibility_probe
import agentsassemble.legacy_live_agent_process_control as compatibility_process_control
import agentsassemble.legacy_live_agent_process_projection as compatibility_process_projection
import agentsassemble.legacy_live_agent_process_service as compatibility_process_service
import agentsassemble.legacy_live_agent_session_control as compatibility_session_control
import agentsassemble.legacy_live_agent_session_projection as compatibility_session_projection
import agentsassemble.legacy_live_agent_session_run_service as compatibility_session_run_service
import agentsassemble.legacy_live_agent_session_service as compatibility_session_service
from agentsassemble.legacy.live_agent import engagement as owned_engagement
from agentsassemble.legacy.live_agent import diagnostics as owned_diagnostics
from agentsassemble.legacy.live_agent import presence as owned_presence
from agentsassemble.legacy.live_agent import presence_projection as owned_presence_projection
from agentsassemble.legacy.live_agent import preflight as owned_preflight
from agentsassemble.legacy.live_agent import probe as owned_probe
from agentsassemble.legacy.live_agent import process_control as owned_process_control
from agentsassemble.legacy.live_agent import process_projection as owned_process_projection
from agentsassemble.legacy.live_agent import process_service as owned_process_service
from agentsassemble.legacy.live_agent import session_control as owned_session_control
from agentsassemble.legacy.live_agent import session_projection as owned_session_projection
from agentsassemble.legacy.live_agent import session_run_service as owned_session_run_service
from agentsassemble.legacy.live_agent import session_service as owned_session_service


class LegacyPackageTests(unittest.TestCase):
    def test_live_agent_diagnostics_root_module_exports_owned_service(self) -> None:
        for name in (
            "LegacyLiveAgentDiagnosticQueryService",
            "live_agent_operations_payload",
            "live_agent_session_readiness_payload",
            "session_process_groups_snapshot",
            "session_run_readiness_overlay",
        ):
            with self.subTest(name=name):
                self.assertIs(
                    getattr(compatibility_diagnostics, name),
                    getattr(owned_diagnostics, name),
                )

    def test_live_agent_engagement_root_module_exports_owned_service(self) -> None:
        self.assertIs(
            compatibility_engagement.LegacyLiveAgentEngagementService,
            owned_engagement.LegacyLiveAgentEngagementService,
        )
        self.assertIs(
            compatibility_engagement.update_live_agent_engagement_payload,
            owned_engagement.update_live_agent_engagement_payload,
        )

    def test_live_agent_preflight_root_module_exports_owned_service(self) -> None:
        self.assertIs(
            compatibility_preflight.LegacyLiveAgentPreflightService,
            owned_preflight.LegacyLiveAgentPreflightService,
        )
        self.assertIs(
            compatibility_preflight.live_agent_preflight_payload,
            owned_preflight.live_agent_preflight_payload,
        )

    def test_live_agent_presence_root_modules_export_owned_service(self) -> None:
        for name in (
            "LegacyLiveAgentPresenceService",
            "connect_live_agent_payload",
            "live_agent_heartbeat_payload",
            "live_agent_leave_payload",
        ):
            with self.subTest(name=name):
                self.assertIs(
                    getattr(compatibility_presence, name),
                    getattr(owned_presence, name),
                )
        for name in ("leave_operation_details", "registration_operation_details"):
            with self.subTest(name=name):
                self.assertIs(
                    getattr(compatibility_presence_projection, name),
                    getattr(owned_presence_projection, name),
                )

    def test_live_agent_probe_root_module_exports_owned_service(self) -> None:
        for name in (
            "LegacyLiveAgentProbeService",
            "live_agent_probe_payload",
            "probe_timeout_seconds",
        ):
            with self.subTest(name=name):
                self.assertIs(
                    getattr(compatibility_probe, name),
                    getattr(owned_probe, name),
                )

    def test_live_agent_process_root_modules_export_owned_services(self) -> None:
        for name in (
            "looks_sensitive_process_control_error",
            "process_bulk_offline_operation_details",
            "process_offline_operation_details",
            "process_start_error_message",
            "process_stop_running_operation_status",
        ):
            with self.subTest(name=name):
                self.assertIs(
                    getattr(compatibility_process_control, name),
                    getattr(owned_process_control, name),
                )
        for name in (
            "LegacyLiveAgentProcessMutationService",
            "LegacyProcessMutationActions",
            "LegacyProcessMutationError",
        ):
            with self.subTest(name=name):
                self.assertIs(
                    getattr(compatibility_process_service, name),
                    getattr(owned_process_service, name),
                )

    def test_live_agent_process_projection_root_module_exports_owned_service(self) -> None:
        for name in (
            "agent_connection_evidence",
            "live_agent_processes_payload",
            "parse_public_timestamp",
            "process_payload_with_agent_connection_evidence",
        ):
            with self.subTest(name=name):
                self.assertIs(
                    getattr(compatibility_process_projection, name),
                    getattr(owned_process_projection, name),
                )

    def test_live_agent_session_policy_root_modules_export_owned_services(self) -> None:
        for name in (
            "session_start_operation_status",
            "session_ensure_operation_summary",
            "session_start_error_details",
            "session_stop_error_message",
        ):
            with self.subTest(name=name):
                self.assertIs(
                    getattr(compatibility_session_control, name),
                    getattr(owned_session_control, name),
                )
        for name in (
            "session_check_operation_details",
            "session_start_operation_details",
            "session_stop_operation_details",
        ):
            with self.subTest(name=name):
                self.assertIs(
                    getattr(compatibility_session_projection, name),
                    getattr(owned_session_projection, name),
                )

    def test_live_agent_session_root_modules_export_owned_services(self) -> None:
        for compatibility, owned, names in (
            (
                compatibility_session_service,
                owned_session_service,
                (
                    "LegacyLiveAgentSessionMutationService",
                    "LegacySessionMutationActions",
                    "LegacySessionMutationError",
                ),
            ),
            (
                compatibility_session_run_service,
                owned_session_run_service,
                (
                    "LegacyLiveAgentSessionRunMutationService",
                    "LegacySessionRunActions",
                    "LegacySessionRunMutationError",
                ),
            ),
        ):
            for name in names:
                with self.subTest(module=owned.__name__, name=name):
                    self.assertIs(
                        getattr(compatibility, name),
                        getattr(owned, name),
                    )


if __name__ == "__main__":
    unittest.main()
