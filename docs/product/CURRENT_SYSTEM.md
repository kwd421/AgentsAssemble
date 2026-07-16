# Current System Orientation

Status: current starting point

Updated: 2026-07-16

Read this file before changing rooms, Agent Sessions, providers, invites,
moderation, media, or the React room UI. It is intentionally short. Follow its
links only when the requested change touches that boundary.

## Product

AgentsAssemble is a shared-room product where humans and persistent AI provider
sessions participate through one canonical room model. The primary path is not
an API-first meeting runner and not a sequence of one-shot prompt calls.

The current product surface is:

- multiple rooms, each with a `#general` channel;
- humans and agents in one participant roster;
- persistent Codex, Antigravity, Grok, Claude, OpenCode, and compatible provider
  sessions behind provider-specific adapters;
- desktop and mobile React clients using the same room protocol;
- explicit start, pause, resume, interrupt, stop, kick, leave, and delete
  lifecycle actions;
- sequenced room history, reconnect replay, bounded provider context, and
  provider-owned private conversation state.

The meeting/research/decision/archive pipeline is legacy. Do not connect new
shared-room behavior to it unless the user explicitly requests legacy work.

## Canonical Architecture

```text
Browser or Agent Bridge
        <-> ticket-authenticated /ws?ticket=...
RoomRealtimeController
        <-> RoomRepository
                <-> persistence/local/room/RoomStore / rooms.sqlite3
                    (current local default)
        <-> persistent provider adapter
```

There is one authority for each concern:

- room, room-global settings, participant, Agent Session, event, and command state:
  controller-injected `RoomRepository` (SQLite by default, or explicitly activated PostgreSQL);
- live transport: canonical ticket-authenticated WebSocket;
- browser state: canonical snapshot plus sequenced events;
- provider process: one Agent Bridge and one persistent provider adapter;
- provider-private memory: provider-owned session state;
- media bytes: room media storage referenced by safe IDs, never local paths in
  public events or model-visible metadata.

Do not add a provider-specific browser socket, parallel room event store,
polling-based live UI, or a second participant registry.

Detailed current implementation: `docs/live-cli-room-current-architecture.md`.

## Current Browser Identity And Admission

The local server operator is one canonical identity:
`operator-local-user` / `operator-local`. A host-authorized device claim binds
that browser credential to the canonical user instead of creating another
operator participant. Ordinary guest admission cannot reach that privileged
claim path.

Opening `/join?token=...` first performs a side-effect-free admission check. A
valid existing room session is preserved, a known same-origin device reuses its
server profile, and an unknown device sees the explicit guest profile form.
Preflight does not consume the invite, create a user, change membership, or
issue a session.

The mutating join uses one browser-generated request ID and a durable admission
workflow. Invite consumption and the workflow's consumed phase commit together;
identity, bounded session, participant, and membership phases can then resume
after a lost response or process restart without consuming the invite twice.
The workflow stores only invite/device/payload fingerprints and bounded public
metadata. Raw invite tokens, device credentials, and room bearer tokens are not
persisted. Reusing a request ID with different admission inputs is an explicit
`idempotency_conflict`, not a second join.

The GUI application owns one invite service, room-session service, admission
coordinator, and operator-pairing service for its lifetime. Current invite and
pairing routes use those injected owners; module-global invite/identity helpers
are compatibility-only and are not the route authority.

Cross-origin operator continuity uses a separate moderator-created `/pair`
link. It is room- and target-origin-bound, expires after at most two minutes,
and is one-use across devices. Redemption durably binds the pairing to the
consuming credential fingerprint and records claiming, retryable-failure, and
completed phases. The same device can therefore resume a partial redemption or
recover the same still-active bounded bearer after a lost response; another
device is rejected. Raw pairing, device, host, and room bearer tokens are never
stored in the pairing record or sent to the public origin. This is not account
login and does not identify a user across different AgentsAssemble servers.

Detailed implementation and verification:
`docs/reports/2026-07-15-browser-identity-admission.md`.

## Current Provider Contract

Current providers receive a server-assigned turn containing a bounded room diff
after their durable cursor. A turn reuses the existing provider process and must
not launch a one-shot CLI.

Agent Bridges passively acknowledge canonical room events without invoking the
provider, while provider context is still delivered only through a server-assigned
turn. A structured runtime may decline an assigned turn explicitly; blank or
zero-width final messages are errors, not a silence signal. `continuous` remains
the bounded legacy relay mode.

Provider controls are fail-closed. A cold browser snapshot may show catalog
loading state, but cannot create a session until native discovery or an explicit
static provider manifest produces a revision. Discovery completion is pushed on
the canonical room WebSocket; `agent.create` must present that revision and the
server validates every selected control against it.

Room-global settings are repository-owned. The strict record contains label,
topic, appearance, conversation mode, bounded relay count, and custom channels.
Room notification mode, per-channel notification mode, and read cursors are
strict user-owned identity rows (`identity.db` locally or PostgreSQL in hosted
mode); two users in the same room never share them. Runtime settings reads do
not consult `room_settings.json`. Existing
legacy globals and user preferences each require their separate explicit
migration described in `docs/product/ROOM_REPOSITORY.md`.

Canonical `message.send` uses one room transaction for participant validation,
the visible `message_final`, its ACK, and the idempotency record. Repository
listeners publish and route that event only after commit, so a failed command
result write cannot leave a visible message or provider turn behind.

Profile-only `agent.configure`, canonical participant mute, and the durable
part of participant leave use the same command transaction boundary. Agent
name/avatar changes update participant, Agent Session, `participant_updated`,
and ACK together. Compatibility roster synchronization, voice cleanup, token
revocation, and other process/network effects run only after commit; canonical
participant mute state takes precedence over an older compatibility roster
copy. The browser resolves old and new messages, roster/detail state, and typing
labels from the current participant by stable `participant_id`; an explicitly
empty canonical avatar clears event-time and legacy local avatar fallbacks.

A successful provider `message.final` commits its visible answer,
`turn_finished`, attention spoke/provider-sync cursors, active lease release,
idle session transition, cleared inflight input, model observation, and command
ACK in one room transaction. Failed ACK recording rolls the entire provider
final back. Event publication, session-state publication, and assignment of the
next pending turn happen only after commit; a duplicate final request resolves
from its durable ACK before active-turn validation.

Server-owned `agent.start` and `agent.stop` persist a private lifecycle intent
before touching the provider process. If process launch or shutdown succeeds
but the final session write fails, retry reuses the manager's session-owned
handle or completes the already-applied stop instead of launching or stopping a
second process. External stop confirmation records the applied effect before
releasing the waiting lifecycle command. Lifecycle intent IDs and owned handles
remain server-private.

`participant.kick` prepares a private participant-scoped moderation intent,
then performs process/session/connection cleanup, and finally commits the
canonical `kicked` state, one `participant_kicked` event, and the command ACK in
one room transaction. If ACK persistence fails, retry observes the applied
cleanup marker and does not stop the provider a second time. Moderation intent
state is excluded from browser and Agent Bridge participant snapshots.

`room.delete` stops owned provider sessions before deleting canonical room
state. The deletion transaction retains a tombstone-scoped command identity,
payload hash, ACK, room name, and cleanup status after ordinary room command
records are removed. Invite, identity, listener, provider-registry, file, and
socket cleanup is idempotent and resumable from a pending tombstone. Only the
same principal/request/payload can resume or deduplicate that delete; a
different request receives `room_deleted`.

An event-driven deterministic attention gate can record durable `selected`,
`eligible`, or `silent` decisions. Shadow recording for existing `ordered` and
`continuous` rooms is server-configured as `off | sample | full` and defaults to
`off`; `sample` records only canonical source sequences divisible by 16. A room
explicitly set to `ambient` uses active evaluation independently of that shadow
setting and acquires one durable lease to wake one fair eligible speaker at a
time, with an initial two-relay agent chain limit and no silent provider
substitution. Votes, system/lifecycle events, empty text, and unsupported
media-only events do not wake providers. Current contract:
`docs/product/ATTENTION_MODEL.md`; supporting research:
`docs/reports/autonomous-room-participation-research.md`.

For a selected ambient speaker, the evaluation cursor, attention job and
lease, and the Agent Session's pending source/job/lease fields commit together.
A failed pending-session write cannot leave a leased job without the input that
lease authorizes.

Attention lease claim checks the persisted expiry. An elapsed active lease is
expired and replaced in the same transaction; a rollback restores the prior
lease, while an unexpired lease held by another worker remains exclusive.

Server startup also runs bounded attention reconciliation. Missing or terminal
job references are cleared, orphan jobs and leases are cancelled, elapsed
leases become pending work, and removed participants cannot retain selected
work. Repairs emit a durable audit event and appear in active-attention
diagnostics; an unexpired lease from another generation is not stolen.

Agent Bridges report received room progress through coalesced `room.observed`
checkpoints. The server keeps the greatest acknowledged sequence atomically;
equal or stale retries do nothing, future sequences are rejected, and these
high-frequency checkpoints do not fill the general command-result table. The
bridge changes its local cursor only after ACK and flushes pending progress on
graceful disconnect. Its one-second socket read timeout is a local deadline,
not room polling and not a provider invocation. `room.observed` also bypasses
the controller lifecycle lock and implicit room creation so a remote-stop
confirmation cannot deadlock behind its own final observation flush.

`agent_attention_state.last_provider_sync_seq` is the canonical record of room
context actually delivered to a provider. Agent Session
`last_provider_sync_seq` and `last_provider_sync_event_id` remain compatibility
copies, but normal packet construction and turn assignment read the canonical
cursor and require exact parity with both compatibility fields. New sessions
initialize both records together and turn completion advances them in one room
transaction. Startup performs a bounded, audited compatibility migration; a
nonzero divergence advances to the monotonic maximum and marks the session
`recovery_required`, while an invalid or future cursor remains blocked instead
of being silently substituted.

## Current Media Boundary

The browser can upload and render room attachments. Media events and safe media
IDs are durable. Provider-native multimodal delivery is incomplete: do not claim
an agent viewed an image merely because the browser displayed it or a manifest
listed its filename.

A completed media path must:

1. bind media IDs to the triggering room message;
2. select media only for the provider receiving attention;
3. use the provider's declared native capability;
4. avoid public local paths and reusable credentials;
5. report unsupported media honestly.

## Non-Negotiable Safety

- Discovery and configuration do not authorize provider execution.
- A real provider starts only after an explicit operator action.
- Do not use `claude -p`.
- Do not use `codex exec resume --last`.
- Do not put secrets, tokens, provider IDs, local absolute paths, raw argv,
  hidden reasoning, or backend internals into room messages or provider prompts.
- External provider-reported PIDs are diagnostics only; never kill them as local
  server-owned processes.
- Do not inherit arbitrary host credentials into provider child environments.
- Do not describe PTY screen scraping as a structured provider protocol.
- Do not claim real smoke success without running the real provider path.
- Direct non-loopback GUI bind is disabled by default. Public access uses a
  loopback bind plus the authenticated tunnel; unsafe direct exposure requires
  an explicit operator flag and is not a production deployment mode.
- Do not push, delete user data, expose a tunnel, or mutate credentials without
  the user's explicit request.

Detailed product policy: `docs/product/OPERATING_MODEL.md`.

## Primary Module Map

| Change | Start here |
| --- | --- |
| GUI server composition, route ownership, and shutdown | stable entrypoint in `gui.py`; lifecycle container in `application/gui.py`; cross-authority transaction contract in `application/transaction.py`; root compatibility exports retained; `docs/product/GUI_COMPOSITION.md` |
| Room persistence and sequence | local SQLite owner in `persistence/local/room/`; PostgreSQL owner in `persistence/postgres/room/`; compatibility exports in `room_store.py`, `room_database.py`, and `sqlite_attention_repository.py`; event types in `room/types.py` with compatibility export in `room_types.py` |
| Room storage authority and transaction contract | repository protocol in `room/repository.py`; shared record normalization/private-field stripping in `room/repository_records.py`; command transaction in `room/command_uow.py`; root compatibility exports retained; `docs/product/ROOM_REPOSITORY.md` |
| Room settings contracts and custom channel model | primitives in `room/setting_values.py` and `room/channels.py`; canonical room-wide record in `room/global_settings.py`; user notification/read record in `room/user_preferences.py`; repository/identity composition in `room/settings_service.py`; root compatibility exports retained |
| Autonomous participation and durable attention | `room_attention.py`, `docs/product/ATTENTION_MODEL.md` |
| WebSocket commands and ACL | `room/commands.py` with compatibility export in `room_commands.py`; `ws_room_session.py`, controller in `room/realtime.py` with compatibility export in `room_realtime.py` |
| Room-scoped configured provider registry | `room/provider_registry.py`; composed by `room/realtime.py` |
| Provider participant and Agent Session persistence | `room/provider_sessions.py`; composed by `room/realtime.py` |
| Capability-projected room snapshot and bounded history read model | `room/snapshots.py`; composed by `room/realtime.py` |
| Browser and Agent Bridge connection membership transitions | `room/connections.py`; composed by `room/realtime.py` |
| Agent Bridge ready/health report validation and canonical session updates | `room/bridge_reports.py`; composed by `room/realtime.py` |
| Server-restart Agent Session ownership reconciliation | `room/startup_reconciliation.py`; composed by `room/realtime.py` |
| Agent display-name/avatar canonical update and provider registry sync | `room/agent_profiles.py`; composed by `room/realtime.py` |
| Stopped Agent Session runtime-profile validation and replacement | `room/agent_runtime_profiles.py`; composed by `room/realtime.py` |
| Catalog-validated server-owned Agent Session creation | `room/agent_creation.py`; composed by `room/realtime.py` |
| Stopped server-owned Agent Session reactivation | `room/agent_reactivation.py`; composed by `room/realtime.py` |
| Canonical human message validation and append | `room/messages.py`; composed by `room/realtime.py` |
| Governed legacy/public room speech identity and safety policy | `room/speech.py`; compatibility export in `room_speech.py` |
| Canonical participant mute transaction and post-commit runtime synchronization | `room/member_mute.py`; composed by `room/realtime.py` |
| Canonical participant leave transaction and delayed access revocation | `room/participant_leave.py`; composed by `room/realtime.py` |
| Retryable participant kick intent, external cleanup, and final transaction | `room/participant_kick.py`; composed by `room/realtime.py` |
| Room-delete owner/name validation, Agent Session cleanup, and tombstone command resumption | `room/deletion.py`; composed by `room/realtime.py` |
| Deleted-room invite/session/identity/listener/provider/file/socket cleanup and tombstone completion | `room/deleted_cleanup.py`; composed by `room/realtime.py` |
| Room history and lifecycle HTTP | `web/routes/room_history.py`, `web/routes/room_lifecycle.py`; legacy `/api/room/ensure` composition remains in `gui_room_lifecycle_http.py` |
| Room roster and member HTTP | canonical mute/kick compatibility writes in `room/moderation.py`; retained roster/presence projection in `room_members.py`; HTTP in `web/routes/room_members.py`; retained resident kick and optional channel/voice composition remains in `gui_room_moderation_media_http.py` |
| Routing and provider context | `room_routing.py`; bounded room projection in `room/context.py`; turn packet assembly in `room/turn_context.py`; compatibility exports in `room_context.py` and `room_turn_context.py`; provider delivery cursor parity in `providers/sync_cursor.py` with compatibility export in `room_provider_sync_cursor.py` |
| Fanout and bridge delivery | `room/event_broker.py` with compatibility export in `room_event_broker.py`; provider-side delivery in `providers/agent_bridge.py`, executable composition in `application/agent_bridge_entrypoint.py`; compatibility export in `room_agent_bridge.py` |
| Cleanup diagnostics | bounded aggregation and secret-redacted failure output in `diagnostics/cleanup.py`; compatibility export in `cleanup_report.py` |
| Agent Session compatibility HTTP | `web/routes/agent_sessions.py`; compatibility export in `gui_room_agent_http.py` |
| Provider catalog and settings | `providers/launch_specs.py`, `providers/capabilities.py`; compatibility exports in `native_cli_providers.py` and `provider_capabilities.py` |
| Provider catalog/credential HTTP | `web/routes/providers.py`; compatibility export in `gui_provider_http.py`; secret storage in `provider_secrets.py` |
| Codex app-server lifecycle | `providers/codex_app_server.py`; compatibility exports in `codex_app_server_runtime.py` and `agent_sessions.py` |
| Agent Session lifecycle and provider process ownership | room state orchestration in `room/agent_lifecycle.py` with compatibility export in `room_agent_lifecycle.py`; OS process ownership in `providers/bridge_process.py`, `providers/agent_bridge.py`, `providers/live_cli.py`, and the provider adapter module; compatibility export in `room_bridge_process.py` |
| Provider turn coordination | pending input, active turn phase, delta/final commit, and recovery in `room/turn_coordinator.py`; compatibility export in `room_turn_coordinator.py` |
| Invites, browser admission, and operator-origin pairing | invite policy/application service in `admission/invite_service.py` with compatibility exports in `room_invite_application.py`; process-local facade in `room_invite.py`; preflight owner in `admission/preflight.py` with compatibility export in `room_admission.py`; session lifecycle in `admission/session_issuer.py` and `admission/session_service.py`; durable mutation and compensation in `admission/coordinator.py` and `admission/saga.py`, all with root compatibility exports; pairing in `identity/pairing.py` with compatibility exports in `operator_pairing.py`; HTTP in `web/routes/room_invite.py` with root compatibility export; native attendee in `room_attendee.py`; browser flow in `frontend/src/app/useRoomAdmission.ts` |
| Invite/session persistence | contracts and fail-closed default in `admission/repository.py`; durable workflow allowlist in `admission/workflow_record.py`; explicit terminal-workflow selection/reporting in `admission/maintenance.py` and CLI boundary in `admission/maintenance_command.py`; local memory/JSON owner in `persistence/local/admission/`; hosted owner in `persistence/postgres/admission/`; root compatibility exports retained; selection in `room_invite_repository_factory.py` |
| Identity, credential, membership compatibility, preference, and usage persistence | storage-independent contract and normalization in `identity/repository.py` and `identity/preferences.py`; backend selection in `identity/factory.py`; process-scoped compatibility binding and local fallback in `application/room_users.py` with root export in `room_users.py`; local SQLite implementation, cache/binding registry, and one-time JSON import in `persistence/local/identity/`; hosted owner in `persistence/postgres/identity/`; compatibility exports in `identity_store.py`, `identity_room_preferences.py`, `identity_repository_factory.py`, and `postgres_identity_*.py` |
| Provider credentials | `provider_secrets.py`, provider credential routes |
| Canonical attachment upload/download HTTP | `web/routes/attachments.py` with compatibility export in `gui_attachment_http.py`; storage in `attachments.py`, room media in `persistence/local/room/repository.py` or the selected `RoomRepository` |
| GUI HTTP routing, response, static delivery, and WebSocket transport | route/request-context owner in `web/router.py`; response owner in `web/response.py`; static owner in `web/static.py`; ticket and WebSocket upgrade owner in `web/websocket.py`; root compatibility exports retained; composition in `gui.py` |
| GUI Host/Origin and public-route trust policy | owner in `web/security.py`; compatibility exports in `gui_request_security.py` |
| Durable legacy session-run monitor lifecycle | thread lifecycle and diagnostics in `application/session_run_monitor.py` with root compatibility export; reconcile policy wiring in `gui.py` |
| Canonical room HTTP routes | `gui_room_*_http.py`; coordinator in `gui_room_http.py` |
| Legacy lobby POST/SSE compatibility | `gui_legacy_lobby_http.py`; do not attach new canonical behavior here |
| Legacy meeting read/lifecycle/workroom/SSE compatibility | `gui_legacy_meeting_http.py`, query projection in `legacy_meeting_queries.py`, record semantics in `legacy_meeting_records.py` |
| Legacy resident room/return-packet reads | `legacy_live_agent_queries.py`, `gui_legacy_live_agent_read_http.py` |
| Legacy resident diagnostic histories | `legacy_live_agent_diagnostics.py`, `gui_legacy_live_agent_read_http.py` |
| Legacy resident process/connection projections | `legacy_live_agent_process_projection.py`, `legacy_live_agent_diagnostics.py` |
| Legacy resident readiness | `legacy_live_agent_diagnostics.py`, `gui_legacy_live_agent_read_http.py` |
| Legacy resident roster and admission projections | `legacy_live_agent_roster_queries.py`, `gui_legacy_live_agent_read_http.py` |
| Legacy resident health aggregation | `legacy_live_agent_health_queries.py`; observation cursor/event policy in `legacy_live_agent_observation_health.py`; durable run/monitor policy in `legacy_live_agent_session_run_health.py` |
| Legacy resident preflight | `legacy_live_agent_preflight.py`, safe response projection in `diagnostic_report_projection.py`, HTTP in `gui_legacy_live_agent_preflight_http.py` |
| Legacy resident local CLI discovery | `legacy_live_agent_discovery.py`, HTTP in `gui_legacy_live_agent_discovery_http.py` |
| Remaining legacy resident smoke compatibility | `gui.py`; classify and extract one verified family at a time |
| Room-global settings | `room_global_settings.py`, `room_settings_service.py`, repository methods; HTTP in `web/routes/room_settings.py` with root compatibility export |
| User-owned room notification/read preferences | validation in `room_user_preferences.py`; local persistence in `identity_room_preferences.py`; hosted persistence in `postgres_identity_preferences.py`; composition in `room_settings_service.py` |
| Legacy room-global settings migration | source inspection in `legacy_room_settings_source.py`; atomic SQLite migration in `room_settings_migration.py` |
| Legacy user preference migration | source inspection in `legacy_room_preferences_source.py`; explicit target-user migration in `room_preferences_migration.py` |
| Friends, direct-message and local-profile HTTP | `features/social/routes.py`; root compatibility export in `gui_social_http.py`; direct-message process callback wired in `gui.py` |
| Play Mode Mafia HTTP | `features/mafia/routes.py`; root compatibility export in `gui_mafia_http.py`; game state and rules in `mafia_game.py` |
| Side-chat storage and room scoping | `side_chat.py`; event normalization in `meeting_events.py`; HTTP/SSE routes in `features/side_chat/routes.py` with root compatibility export in `gui_side_chat_http.py` |
| CLI parser registration | `cli_parser_common.py`, `cli_parser_*.py`; dispatch in `cli.py` |
| Canonical React transport and sequenced history | `frontend/src/useCanonicalRoom.ts`, `frontend/src/roomSocketClient.ts` |
| React room composition | `frontend/src/App.tsx`; domain state belongs in focused hooks under `frontend/src/app/` |
| Room directory cache and hydration | `frontend/src/app/useRoomDirectory.ts`, `frontend/src/lib/roomDockModel.ts` |
| Room members, settings, channels, invites, and side chat | `frontend/src/app/useRoomMembers.ts`, `useRoomSettingsController.ts`, `useRoomChannels.ts`, `useRoomInviteController.ts`, `useRoomSideChat.ts` |
| Typing versus visible agent activity policy | `frontend/src/lib/roomTypingIndicators.ts`, `agentActivityPreferences.ts` |
| Friends directory and DM selection | `frontend/src/app/useFriendsDirectory.ts`, `frontend/src/views/FriendsView.tsx` |
| Active Play Mode Mafia game lifecycle | `frontend/src/app/useActiveMafiaGame.ts`; presentation in `App.tsx` and `LiveView.tsx` |
| Frontend API client | `frontend/src/api/`; compatibility barrel in `frontend/src/api.ts` |
| Message and roster UI | `frontend/src/views/LobbyView.tsx`, `frontend/src/views/components/member/` |

Read the nearest tests before changing behavior. Prefer behavioral tests over
source-string assertions.

## Verification Ladder

Run the cheapest check that matches the change, then broaden for shared paths.

```text
Targeted Python test module
Targeted Vitest or Playwright flow
python3 -m unittest discover -s tests -t .
npm --prefix frontend test
npm --prefix frontend run build
git diff --check
```

For provider or room lifecycle changes, add a fake persistent-provider test and
run the real provider smoke only with explicit approval. For UI workflows, use
the browser-visible flow rather than proving only that a backend function works.

## Documentation Map

| Document | Status | Read when |
| --- | --- | --- |
| `docs/product/PACKAGE_MAP.md` | generated current inventory | Moving modules, checking ownership/import direction, or removing compatibility paths |
| `docs/product/PACKAGE_CYCLES.md` | generated current cycle report | Changing imports around GUI observability, release health, resident providers, or live-agent runner |
| `docs/live-cli-room-current-architecture.md` | current implementation | Changing canonical room protocol, state, lifecycle, or provider bridge |
| `docs/product/OPERATING_MODEL.md` | current detailed policy | Changing security, memory, official-record, or mode boundaries |
| `docs/product/RUNTIME_OWNERSHIP.md` | current ownership map | Changing provider process, Agent Session, recovery, or legacy resident ownership |
| `docs/provider-architecture.md` | mixed provider reference | Changing provider families or legacy provider adapters |
| `docs/live-session-room-model.md` | mixed design history | Changing legacy room semantics or tracing why a rule exists |
| `docs/live-agent-ops.md` | legacy/operator reference | Operating or modifying legacy resident commands |
| `docs/roadmap.md` | future direction | Planning only, never as implementation permission |
| `docs/reports/` | evidence and research | Checking past smoke results, incidents, or proposals |

## Keep This File Useful

Update this file only when the active product boundary, canonical authority,
module ownership, safety contract, or known primary limitation changes. Do not
append incident history, command catalogs, smoke transcripts, or speculative
roadmap items here.
