# Compatibility Shim Retirement

Status: generated architecture report

Generator: `python3 scripts/check_package_architecture.py --write-shim-report`

Source fingerprint: `5de94bd781468c6c`

- Tracked shims: 134
- Zero code callers: 118
- Blocked by code callers: 16
- Unexpected callers: 0

Generated package-map and retirement-report references are excluded from
documentation evidence. A zero-code-caller entry is a review candidate, not
permission to delete it; its compatibility window and export policy still apply.

## Unexpected Callers

- None

## Zero Code Callers

- `application_transaction.py` -> `agentsassemble.application.transaction`; docs: none; gate: No direct imports use agentsassemble.application_transaction for one compatibility window.
- `attachments.py` -> `agentsassemble.room.attachments`; docs: `docs/product/V1_COMPLETION_AUDIT.md`; gate: No direct imports or patches use agentsassemble.attachments for one compatibility window.
- `bridge_protocol.py` -> `agentsassemble.providers.bridge_protocol`; docs: none; gate: No direct imports use agentsassemble.bridge_protocol for one compatibility window.
- `bridge_report_tracker.py` -> `agentsassemble.providers.bridge_report_tracker`; docs: none; gate: No direct imports use agentsassemble.bridge_report_tracker for one compatibility window.
- `bridge_stop_confirmation.py` -> `agentsassemble.room.bridge_stop_confirmation`; docs: none; gate: No direct imports use agentsassemble.bridge_stop_confirmation for one compatibility window.
- `claude_transcript.py` -> `agentsassemble.providers.claude_transcript`; docs: none; gate: No direct imports use agentsassemble.claude_transcript for one compatibility window.
- `cleanup_report.py` -> `agentsassemble.diagnostics.cleanup`; docs: none; gate: No direct imports use agentsassemble.cleanup_report for one compatibility window.
- `cli_http_errors.py` -> `agentsassemble.web.cli_errors`; docs: none; gate: No direct imports use agentsassemble.cli_http_errors for one compatibility window.
- `cli_parser_core.py` -> `agentsassemble.application.cli.core`; docs: none; gate: No direct imports use agentsassemble.cli_parser_core for one compatibility window.
- `cli_parser_persona.py` -> `agentsassemble.application.cli.persona`; docs: none; gate: No direct imports use agentsassemble.cli_parser_persona for one compatibility window.
- `cli_parser_room.py` -> `agentsassemble.application.cli.room`; docs: none; gate: No direct imports use agentsassemble.cli_parser_room for one compatibility window.
- `codex_app_server_live_runtime.py` -> `agentsassemble.providers.codex_app_server_live`; docs: none; gate: No direct imports use agentsassemble.codex_app_server_live_runtime for one compatibility window.
- `codex_app_server_runtime.py` -> `agentsassemble.providers.codex_app_server`; docs: none; gate: No direct imports use agentsassemble.codex_app_server_runtime for one compatibility window.
- `codex_session_ids.py` -> `agentsassemble.providers.codex_session_ids`; docs: none; gate: No direct imports use agentsassemble.codex_session_ids for one compatibility window.
- `codex_stream.py` -> `agentsassemble.providers.codex_stream`; docs: none; gate: No direct imports use agentsassemble.codex_stream for one compatibility window.
- `deepseek_runtime.py` -> `agentsassemble.providers.deepseek`; docs: none; gate: No direct imports use agentsassemble.deepseek_runtime for one compatibility window.
- `diagnostic_report_projection.py` -> `agentsassemble.diagnostics.report_projection`; docs: none; gate: No direct imports use agentsassemble.diagnostic_report_projection for one compatibility window.
- `frontend_runtime.py` -> `agentsassemble.web.frontend_runtime`; docs: none; gate: No direct imports use agentsassemble.frontend_runtime for one compatibility window.
- `grok_acp_runtime.py` -> `agentsassemble.providers.grok_acp`; docs: none; gate: No direct imports use agentsassemble.grok_acp_runtime for one compatibility window.
- `gui_attachment_http.py` -> `agentsassemble.web.routes.attachments`; docs: none; gate: No direct imports use agentsassemble.gui_attachment_http for one compatibility window.
- `gui_mafia_http.py` -> `agentsassemble.features.mafia.routes`; docs: none; gate: No direct imports use agentsassemble.gui_mafia_http for one compatibility window.
- `gui_observability_http.py` -> `agentsassemble.web.routes.observability`; docs: `docs/product/PACKAGE_CYCLE_BASELINE.txt`; gate: No direct imports or monkeypatch targets use agentsassemble.gui_observability_http for one compatibility window.
- `gui_provider_http.py` -> `agentsassemble.web.routes.providers`; docs: none; gate: No direct imports or monkeypatch targets use agentsassemble.gui_provider_http for one compatibility window.
- `gui_public_invite_http.py` -> `agentsassemble.web.routes.public_invite`; docs: none; gate: No direct imports use agentsassemble.gui_public_invite_http for one compatibility window.
- `gui_request_security.py` -> `agentsassemble.web.security`; docs: none; gate: No direct imports use agentsassemble.gui_request_security for one compatibility window.
- `gui_response.py` -> `agentsassemble.web.response`; docs: none; gate: No direct imports use agentsassemble.gui_response for one compatibility window.
- `gui_room_agent_http.py` -> `agentsassemble.web.routes.agent_sessions`; docs: none; gate: No direct imports use agentsassemble.gui_room_agent_http for one compatibility window.
- `gui_room_invite_http.py` -> `agentsassemble.web.routes.room_invite`; docs: none; gate: No direct imports use agentsassemble.gui_room_invite_http for one compatibility window.
- `gui_room_settings_http.py` -> `agentsassemble.web.routes.room_settings`; docs: none; gate: No direct imports use agentsassemble.gui_room_settings_http for one compatibility window.
- `gui_router.py` -> `agentsassemble.web.router`; docs: none; gate: No direct imports use agentsassemble.gui_router for one compatibility window.
- `gui_side_chat_http.py` -> `agentsassemble.features.side_chat.routes`; docs: none; gate: No direct imports or monkeypatch targets use agentsassemble.gui_side_chat_http for one compatibility window.
- `gui_social_http.py` -> `agentsassemble.features.social.routes`; docs: none; gate: No direct imports use agentsassemble.gui_social_http for one compatibility window.
- `gui_static_transport.py` -> `agentsassemble.web.static`; docs: none; gate: No direct imports use agentsassemble.gui_static_transport for one compatibility window.
- `gui_ws_http.py` -> `agentsassemble.web.websocket`; docs: none; gate: No direct imports use agentsassemble.gui_ws_http for one compatibility window.
- `identity_repository_factory.py` -> `agentsassemble.identity.factory`; docs: none; gate: No direct imports use agentsassemble.identity_repository_factory for one compatibility window.
- `identity_room_preferences.py` -> `agentsassemble.persistence.local.identity.preferences`; docs: none; gate: Callers use agentsassemble.identity.preferences for shared identity rules and agentsassemble.persistence.local.identity.preferences for SQLite persistence for one compatibility window.
- `live_cli.py` -> `agentsassemble.providers.live_cli`; docs: none; gate: No direct imports use agentsassemble.live_cli for one compatibility window.
- `live_cli_output.py` -> `agentsassemble.providers.live_cli_output`; docs: none; gate: No direct imports use agentsassemble.live_cli_output for one compatibility window.
- `live_cli_transcripts.py` -> `agentsassemble.providers.live_cli_transcripts`; docs: none; gate: No direct imports use agentsassemble.live_cli_transcripts for one compatibility window.
- `live_session_adapter.py` -> `agentsassemble.providers.live_session_adapter`; docs: none; gate: No direct imports or patches use agentsassemble.live_session_adapter for one compatibility window.
- `live_session_transport.py` -> `agentsassemble.providers.live_session_transport`; docs: `docs/superpowers/plans/2026-05-17-live-agent-final-form.md`; gate: No direct imports or patches use agentsassemble.live_session_transport for one compatibility window.
- `local_resources.py` -> `agentsassemble.diagnostics.local_resources`; docs: none; gate: No direct imports or patches use agentsassemble.local_resources for one compatibility window.
- `multi_host_invites.py` -> `agentsassemble.admission.lan_invite`; docs: none; gate: No direct imports or patches use agentsassemble.multi_host_invites for one compatibility window.
- `native_cli_providers.py` -> `agentsassemble.providers.launch_specs`; docs: none; gate: No direct imports use agentsassemble.native_cli_providers for one compatibility window.
- `opencode_runtime.py` -> `agentsassemble.providers.opencode`; docs: none; gate: No direct imports use agentsassemble.opencode_runtime for one compatibility window.
- `operator_pairing.py` -> `agentsassemble.identity.pairing`; docs: none; gate: No direct imports use agentsassemble.operator_pairing for one compatibility window.
- `postgres_attention_repository.py` -> `agentsassemble.persistence.postgres.room.attention`; docs: none; gate: No direct imports use agentsassemble.postgres_attention_repository for one compatibility window.
- `postgres_identity_preferences.py` -> `agentsassemble.persistence.postgres.identity.preferences`; docs: none; gate: No direct imports use agentsassemble.postgres_identity_preferences for one compatibility window.
- `postgres_identity_roster.py` -> `agentsassemble.persistence.postgres.identity.roster`; docs: none; gate: No direct imports use agentsassemble.postgres_identity_roster for one compatibility window.
- `postgres_identity_usage.py` -> `agentsassemble.persistence.postgres.identity.usage`; docs: none; gate: No direct imports use agentsassemble.postgres_identity_usage for one compatibility window.
- `postgres_identity_users.py` -> `agentsassemble.persistence.postgres.identity.users`; docs: none; gate: No direct imports use agentsassemble.postgres_identity_users for one compatibility window.
- `postgres_room_mutations.py` -> `agentsassemble.persistence.postgres.room.mutations`; docs: none; gate: No direct imports use agentsassemble.postgres_room_mutations for one compatibility window.
- `postgres_room_queries.py` -> `agentsassemble.persistence.postgres.room.queries`; docs: none; gate: No direct imports use agentsassemble.postgres_room_queries for one compatibility window.
- `postgres_room_rows.py` -> `agentsassemble.persistence.postgres.room.rows`; docs: none; gate: No direct imports use agentsassemble.postgres_room_rows for one compatibility window.
- `process_environment.py` -> `agentsassemble.providers.process_environment`; docs: none; gate: No direct imports use agentsassemble.process_environment for one compatibility window.
- `provider_auth.py` -> `agentsassemble.providers.auth`; docs: none; gate: No direct imports use agentsassemble.provider_auth for one compatibility window.
- `provider_capabilities.py` -> `agentsassemble.providers.capabilities`; docs: none; gate: No direct imports use agentsassemble.provider_capabilities for one compatibility window.
- `provider_catalog.py` -> `agentsassemble.providers.catalog`; docs: none; gate: No direct imports use agentsassemble.provider_catalog for one compatibility window.
- `provider_model_verification.py` -> `agentsassemble.providers.model_verification`; docs: none; gate: No direct imports use agentsassemble.provider_model_verification for one compatibility window.
- `provider_runtime_config.py` -> `agentsassemble.providers.runtime_config`; docs: none; gate: No direct imports use agentsassemble.provider_runtime_config for one compatibility window.
- `provider_runtime_contracts.py` -> `agentsassemble.providers.runtime_contracts`; docs: none; gate: No direct imports use agentsassemble.provider_runtime_contracts for one compatibility window.
- `provider_runtime_factory.py` -> `agentsassemble.providers.runtime_factory`; docs: none; gate: No direct imports use agentsassemble.provider_runtime_factory for one compatibility window.
- `provider_secrets.py` -> `agentsassemble.providers.secrets`; docs: none; gate: No direct imports use agentsassemble.provider_secrets for one compatibility window.
- `provider_sessions.py` -> `agentsassemble.providers.sessions`; docs: none; gate: No direct imports use agentsassemble.provider_sessions for one compatibility window.
- `public_invite_runtime.py` -> `agentsassemble.application.public_invite_runtime`; docs: none; gate: No direct imports use agentsassemble.public_invite_runtime for one compatibility window.
- `public_tunnel.py` -> `agentsassemble.application.public_tunnel`; docs: none; gate: No direct imports or monkeypatch targets use agentsassemble.public_tunnel for one compatibility window.
- `release_health.py` -> `agentsassemble.diagnostics.release_health`; docs: `docs/product/PACKAGE_CYCLE_BASELINE.txt`; gate: No direct imports or patches use agentsassemble.release_health for one compatibility window.
- `remote_bridge_config.py` -> `agentsassemble.providers.remote_bridge_config`; docs: none; gate: No direct imports or patches use agentsassemble.remote_bridge_config for one compatibility window.
- `remote_room_client_packet.py` -> `agentsassemble.admission.remote_room_client_packet`; docs: none; gate: No direct imports or patches use agentsassemble.remote_room_client_packet for one compatibility window.
- `room_admission_coordinator.py` -> `agentsassemble.admission.coordinator`; docs: none; gate: No direct imports use agentsassemble.room_admission_coordinator for one compatibility window.
- `room_admission_saga.py` -> `agentsassemble.admission.saga`; docs: none; gate: No direct imports use agentsassemble.room_admission_saga for one compatibility window.
- `room_admission_workflow_maintenance.py` -> `agentsassemble.admission.maintenance`; docs: none; gate: No direct imports use agentsassemble.room_admission_workflow_maintenance for one compatibility window.
- `room_admission_workflow_maintenance_command.py` -> `agentsassemble.admission.maintenance_command`; docs: none; gate: No direct imports use agentsassemble.room_admission_workflow_maintenance_command for one compatibility window.
- `room_agent_bridge.py` -> `agentsassemble.providers.agent_bridge`; docs: none; gate: No direct imports use agentsassemble.room_agent_bridge and no external launch path needs its compatibility module entrypoint for one compatibility window.
- `room_agent_lifecycle.py` -> `agentsassemble.room.agent_lifecycle`; docs: none; gate: No direct imports use agentsassemble.room_agent_lifecycle for one compatibility window.
- `room_api_provider.py` -> `agentsassemble.providers.api`; docs: none; gate: No direct imports use agentsassemble.room_api_provider for one compatibility window.
- `room_attendee.py` -> `agentsassemble.application.room_attendee`; docs: none; gate: No direct imports use agentsassemble.room_attendee for one compatibility window.
- `room_bridge_process.py` -> `agentsassemble.providers.bridge_process`; docs: none; gate: No direct imports use agentsassemble.room_bridge_process for one compatibility window.
- `room_channels.py` -> `agentsassemble.room.channels`; docs: none; gate: No direct imports use agentsassemble.room_channels for one compatibility window.
- `room_command_uow.py` -> `agentsassemble.room.command_uow`; docs: none; gate: No direct imports use agentsassemble.room_command_uow for one compatibility window.
- `room_commands.py` -> `agentsassemble.room.commands`; docs: none; gate: No direct imports use agentsassemble.room_commands for one compatibility window.
- `room_context.py` -> `agentsassemble.room.context`; docs: none; gate: No direct imports use agentsassemble.room_context for one compatibility window.
- `room_errors.py` -> `agentsassemble.room.errors`; docs: none; gate: No direct imports use agentsassemble.room_errors for one compatibility window.
- `room_event_broker.py` -> `agentsassemble.room.event_broker`; docs: none; gate: No direct imports use agentsassemble.room_event_broker for one compatibility window.
- `room_friends.py` -> `agentsassemble.features.social.friends`; docs: none; gate: No direct imports use agentsassemble.room_friends for one compatibility window.
- `room_global_settings.py` -> `agentsassemble.room.global_settings`; docs: none; gate: No direct imports use agentsassemble.room_global_settings for one compatibility window.
- `room_invite_application.py` -> `agentsassemble.admission.invite_service`; docs: none; gate: No direct imports use agentsassemble.room_invite_application for one compatibility window.
- `room_invite_repository_factory.py` -> `agentsassemble.admission.repository_factory`; docs: none; gate: No direct imports use agentsassemble.room_invite_repository_factory for one compatibility window.
- `room_members.py` -> `agentsassemble.room.members`; docs: none; gate: No direct imports or patches use agentsassemble.room_members for one compatibility window.
- `room_projection.py` -> `agentsassemble.room.projection`; docs: none; gate: No direct imports use agentsassemble.room_projection for one compatibility window.
- `room_provider_sync_cursor.py` -> `agentsassemble.providers.sync_cursor`; docs: none; gate: No direct imports use agentsassemble.room_provider_sync_cursor for one compatibility window.
- `room_realtime.py` -> `agentsassemble.room.realtime`; docs: `docs/reports/2026-07-11-ai-maintainability-audit.md`; gate: No direct imports use agentsassemble.room_realtime for one compatibility window.
- `room_repository.py` -> `agentsassemble.room.repository`; docs: none; gate: No direct imports use agentsassemble.room_repository for one compatibility window.
- `room_repository_factory.py` -> `agentsassemble.application.room_repository_factory`; docs: none; gate: No direct imports or patches use agentsassemble.room_repository_factory for one compatibility window.
- `room_repository_records.py` -> `agentsassemble.room.repository_records`; docs: none; gate: No direct imports use agentsassemble.room_repository_records for one compatibility window.
- `room_session_issuer.py` -> `agentsassemble.admission.session_issuer`; docs: none; gate: No direct imports use agentsassemble.room_session_issuer for one compatibility window.
- `room_session_service.py` -> `agentsassemble.admission.session_service`; docs: none; gate: No direct imports use agentsassemble.room_session_service for one compatibility window.
- `room_setting_values.py` -> `agentsassemble.room.setting_values`; docs: none; gate: No direct imports use agentsassemble.room_setting_values for one compatibility window.
- `room_settings.py` -> `agentsassemble.room.settings`; docs: none; gate: No direct imports use agentsassemble.room_settings for one compatibility window.
- `room_settings_service.py` -> `agentsassemble.room.settings_service`; docs: none; gate: No direct imports use agentsassemble.room_settings_service for one compatibility window.
- `room_speech.py` -> `agentsassemble.room.speech`; docs: `docs/server-governed-speech-matrix.md`; gate: No direct imports use agentsassemble.room_speech for one compatibility window.
- `room_turn_context.py` -> `agentsassemble.room.turn_context`; docs: none; gate: No direct imports use agentsassemble.room_turn_context for one compatibility window.
- `room_turn_coordinator.py` -> `agentsassemble.room.turn_coordinator`; docs: none; gate: No direct imports use agentsassemble.room_turn_coordinator for one compatibility window.
- `room_types.py` -> `agentsassemble.room.types`; docs: none; gate: No direct imports use agentsassemble.room_types for one compatibility window.
- `room_user_preferences.py` -> `agentsassemble.room.user_preferences`; docs: none; gate: No direct imports use agentsassemble.room_user_preferences for one compatibility window.
- `room_votes.py` -> `agentsassemble.room.votes`; docs: none; gate: No direct imports use agentsassemble.room_votes for one compatibility window.
- `room_websocket.py` -> `agentsassemble.web.websocket_codec`; docs: none; gate: No direct imports use agentsassemble.room_websocket for one compatibility window.
- `sandbox_launcher.py` -> `agentsassemble.providers.sandbox_launcher`; docs: none; gate: No direct imports or patches use agentsassemble.sandbox_launcher for one compatibility window.
- `side_chat.py` -> `agentsassemble.features.side_chat.service`; docs: none; gate: No direct imports use agentsassemble.side_chat for one compatibility window.
- `speech_policy.py` -> `agentsassemble.providers.speech_policy`; docs: `docs/superpowers/plans/2026-05-11-local-verifiable-council-workflow.md`; gate: No direct imports or patches use agentsassemble.speech_policy for one compatibility window.
- `sqlite_attention_repository.py` -> `agentsassemble.persistence.local.room.attention`; docs: none; gate: No direct imports use agentsassemble.sqlite_attention_repository for one compatibility window.
- `sse_cadence.py` -> `agentsassemble.web.sse_cadence`; docs: none; gate: No direct imports use agentsassemble.sse_cadence for one compatibility window.
- `stable_entry.py` -> `agentsassemble.application.stable_entry`; docs: none; gate: No direct imports use agentsassemble.stable_entry for one compatibility window.
- `user_profile.py` -> `agentsassemble.features.social.profile`; docs: none; gate: No direct imports use agentsassemble.user_profile for one compatibility window.
- `voice_presence.py` -> `agentsassemble.room.voice_presence`; docs: none; gate: No direct imports or patches use agentsassemble.voice_presence for one compatibility window.
- `windows_conpty.py` -> `agentsassemble.providers.windows_conpty`; docs: none; gate: No direct imports use agentsassemble.windows_conpty for one compatibility window.
- `ws_room_client.py` -> `agentsassemble.web.room_client`; docs: none; gate: No direct imports or monkeypatch targets use agentsassemble.ws_room_client for one compatibility window.
- `ws_room_session.py` -> `agentsassemble.web.room_session`; docs: `docs/server-governed-speech-matrix.md`; gate: No direct imports use agentsassemble.ws_room_session for one compatibility window.

## Blocked

- `canonical_room_benchmark.py`; callers: `tests/test_canonical_room_benchmark.py`; docs: none
- `identity_store.py`; callers: `tests/test_local_identity_persistence_package.py`; docs: `docs/plans/2026-07-15-browser-identity-admission.md`, `docs/reports/2026-07-15-browser-identity-admission.md`, `docs/rooms-as-server-objects-spec.md`
- `live_cli_smoke.py`; callers: `tests/test_live_cli_smoke.py`; docs: none
- `mafia_game.py`; callers: `tests/test_mafia_game.py`; docs: none
- `postgres_application_database.py`; callers: `tests/test_postgres_application_database.py`, `tests/test_postgres_cross_authority_transactions.py`; docs: none
- `postgres_connection_pool.py`; callers: `tests/test_postgres_application_database.py`, `tests/test_postgres_connection_pool.py`; docs: none
- `postgres_identity_repository.py`; callers: `tests/test_postgres_cross_authority_transactions.py`, `tests/test_postgres_identity_repository.py`; docs: none
- `postgres_invite_repository.py`; callers: `tests/test_postgres_cross_authority_transactions.py`, `tests/test_postgres_invite_repository.py`; docs: none
- `postgres_room_repository.py`; callers: `tests/test_postgres_cross_authority_transactions.py`, `tests/test_postgres_room_repository.py`; docs: none
- `postgres_room_schema.py`; callers: `tests/test_postgres_application_database.py`, `tests/test_postgres_cross_authority_transactions.py`, `tests/test_postgres_identity_repository.py`, `tests/test_postgres_invite_repository.py`, +3; docs: none
- `room_admission.py`; callers: `tests/test_room_admission.py`; docs: none
- `room_database.py`; callers: `tests/test_room_attention_reconciliation.py`, `tests/test_room_turn_coordinator.py`, `tests/test_room_unification.py`; docs: none
- `room_invite.py`; callers: `tests/test_gui_router.py`, `tests/test_host_account.py`; docs: `docs/server-governed-speech-matrix.md`
- `room_invite_repository.py`; callers: `tests/test_local_admission_persistence_package.py`; docs: none
- `room_native_cli_smoke.py`; callers: `tests/test_room_native_cli_e2e.py`; docs: none
- `room_store.py`; callers: `tests/test_gui_room_repository_injection.py`, `tests/test_gui_server_room_settings_http.py`, `tests/test_local_room_persistence_package.py`, `tests/test_operator_pairing.py`, +17; docs: none
