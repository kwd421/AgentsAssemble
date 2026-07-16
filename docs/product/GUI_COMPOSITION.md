# GUI Server Composition Inventory

Status: current composition and refactor inventory

Updated: 2026-07-14

Read this document before changing `agentsassemble/gui.py`, GUI server startup
or shutdown, HTTP route ownership, or server-scoped dependencies. The exact
API/SSE list remains in `legacy-react-parity-matrix.md`; this document classifies
those routes by product ownership and records the safe extraction order.

## Purpose

`gui.py` was 9,525 lines when this inventory started and is 6,417 lines after
the retained read/diagnostic extraction. Size alone is not the defect. The
maintainability problem is that server construction, process lifetime, route
registration, static delivery, current room behavior, and several retained
legacy products still meet in one module.

The refactor must make these questions obvious:

- Which object owns each server-scoped resource?
- Which routes are part of the canonical shared-room product?
- Which routes are active optional surfaces or compatibility contracts?
- Which routes have no known production caller and need a separate deletion
  decision?
- Which tests prove that moving a boundary did not change behavior?

This inventory does not authorize route deletion, endpoint renaming, payload
changes, frontend redesign, provider execution, or a new web framework.

## Classification

- **Current core**: required by the canonical room, invite, provider control,
  credential, or browser startup path.
- **Active optional**: used by the current React product, but not required by
  the canonical `#general` room core.
- **Compatibility**: used by CLI, MCP, smoke, old meeting/session operation, or
  an HTTP/SSE fallback still called by the current frontend. Preserve its public
  behavior while moving it.
- **Deletion candidate**: no production caller was found outside `gui.py` at
  this snapshot. Candidate status is evidence for a later compatibility change,
  not permission to delete it during composition work.

The guarded appendix in `legacy-react-parity-matrix.md` contains 159 concrete
API/SSE method-path rows: 79 have a React wrapper and 80 do not. Tests compare
that appendix with both Router registrations and the remaining handler chain.
Do not copy that exact list here and create a second route authority.

## Canonical Invariants

Every composition change must preserve these product boundaries:

1. `/ws?ticket=...` remains the only canonical live room transport.
2. `RoomRealtimeController` and every room route receive the same
   `RoomRepository` object.
3. A handler factory does not silently construct a second SQLite repository
   when PostgreSQL was selected or injected.
4. Provider processes start only after explicit operator action.
5. No route, diagnostic, snapshot, or log exposes credentials, invite tokens,
   provider-private IDs, argv, absolute workspaces, or unmanaged PIDs.
6. Agent message authors remain keyed by canonical `participant_id`. A current
   participant profile overrides the event's historical author snapshot in the
   normal timeline. After `agent.configure`, old loaded messages, later history
   pages, typing state, roster rows, and new messages must all use the current
   name and avatar without rewriting historical events.
7. Compatibility HTTP/SSE routes cannot become a second room authority or a
   provider-specific browser transport.
8. Shutdown order remains bounded and explicit; losing a server-owned handle
   must never trigger PID fallback killing.

## Server-Scoped Resource Inventory

| Resource | Current construction and lifetime | Current problem | Phase 5.2 target |
| --- | --- | --- | --- |
| `RoomRepository` | `serve_gui()` builds it; `_make_handler()` accepts or creates one; `serve_gui()` closes the owned instance | Ownership can be split between two functions and test overrides | One application-services owner builds it once, injects the exact instance, and closes it last |
| Identity backend | `_make_handler()` configures global paths; `GuiDeps.identities` lazily opens by output root | Hidden global configuration and lazy construction obscure ownership | Build one explicit identity backend and inject it into `GuiDeps` |
| Invite state | `_make_handler()` calls `configure_room_invite_store()` | Global path mutation occurs during handler type construction | One invite service/configuration step owned by application services |
| `LiveAgentProcessSupervisor` | Usually built in `serve_gui()`, but `_make_handler()` can also build it; monitor starts/stops in `serve_gui()` | Construction and lifecycle are separated and tests patch both sites | One owner constructs it; application start/close owns monitor lifecycle |
| `LiveAgentSessionRunController` | Built in `serve_gui()` or `_make_handler()` | Same split ownership as the process supervisor | One injected instance shared by monitor and compatibility services |
| `LiveAgentSessionRunMonitor` | Built and started/stopped by `serve_gui()`; read routes accept it | Its `default_server` is assigned only after bind | Application services expose a post-bind `start(server_url)` step and bounded `close()` |
| `LiveAgentFlowSupervisor` | Built in `_make_handler()` unless injected | Hidden handler-factory lifetime | Explicit active-optional service in the container |
| `PublicTunnelManager` | Built in `serve_gui()` or `_make_handler()`; started/stopped by `serve_gui()` | Handler creation may create an unowned manager | One application-owned manager; routes only issue commands to it |
| `WsTicketStore` | Built inside `_make_handler()` and captured by closures | Correct lifetime but invisible to diagnostics and tests except through routes | Explicit application-owned ephemeral ticket service |
| `NativeCliBridgeProcessManager` | Built inside `_make_handler()` only when the realtime controller is not injected | Its ownership is implicit behind controller construction | Build beside the controller and retain an opaque owned handle through application close |
| `RoomRealtimeController` | Built inside `_make_handler()` or injected; `serve_gui()` discovers it on the handler class to close it | A generated HTTP handler type doubles as a service locator | Explicit application-owned controller; handler receives it but does not own it |
| `Router` and `GuiDeps` | Built in `_make_handler()` and captured by the generated handler | Reasonable request composition, but `GuiDeps` still carries untyped compatibility callables | Keep per-handler composition; replace only proven service-locator fields with typed services |
| React dist root | Resolved in `_make_handler()` | Configuration is mixed with service construction | Keep as immutable handler configuration |
| Legacy mutation services | Built during route registration from process/session dependencies | Stateless wrappers are valid, but construction is buried in the factory | Construct from explicit application services during route registration |

`ThreadingHTTPServer` remains owned by `serve_gui()`. The service container must
not bind a port itself. Binding produces the local server URL needed by the
session-run monitor, tunnel manager, and bridge ticket issuer, so startup has a
deliberate pre-bind construction step and post-bind activation step.

## Current Application Services Boundary

Phase 5.2 introduced `GuiApplicationServices` in
`agentsassemble/gui_application.py`. `_build_gui_application_services()` is the
single composition function used by both `serve_gui()` and the compatibility
`_make_handler()` test/helper surface.

The object now retains the exact instances for:

- the selected `RoomRepository`;
- the explicit identity backend injected into `GuiDeps`;
- the configured invite-state path;
- one `FileAttachmentStore` injected into attachment routes;
- legacy process and session-run supervision;
- the disabled legacy flow supervisor;
- public tunnel state;
- the single-use WebSocket ticket store;
- the native Agent Bridge manager owned through `RoomRealtimeController`; and
- the canonical `RoomRealtimeController`.

`start(server_url)` runs only after HTTP bind. It preserves this order:

1. legacy process monitor;
2. local URL assignment for tunnel and session monitor;
3. explicit legacy group autostart callback, when configured;
4. session-run monitor;
5. explicitly requested public tunnel.

`shutdown(transport_close=...)` is idempotent and attempts every cleanup even
if an earlier close fails. It stops the session monitor, tunnel, legacy process
supervisor, and canonical realtime controller; closes the HTTP transport; then
closes the owned room repository. Injected resources carry explicit ownership
flags and are not closed by the container.

Invite/session token implementation still uses `room_invite.py`'s
server-lifetime global state. Phase 5.2 centralizes its one path-configuration
step but does not disguise that global implementation behind a pass-through
object. Replacing that authority belongs to the hosted-deployment decision
gate, not this composition-only refactor.

The handler class retains `room_realtime_controller`, `room_repository`, and
`gui_deps` attributes for compatibility tests and fixtures, but also exposes
`application_services`. Production shutdown no longer discovers its controller
through the generated handler type.

Host, origin, CORS, and public-route trust policy now belongs to
`agentsassemble/web/security.py`. `gui_request_security.py` remains a temporary
compatibility export; current request handling imports the owned web path.

## Router-Owned Route Families

These families already have a clear module owner and should not move back into
`gui.py`.

| Owner module | Classification | Responsibility | Primary evidence |
| --- | --- | --- | --- |
| `gui_ws_http.py` | Current core | Single-use WebSocket ticket issue | `tests/test_ws_room_session.py`, `tests/test_ws_room_client.py` |
| `gui_attachment_http.py` | Current core | Safe attachment upload/download and room media reference | `tests/test_gui_server_room_routes.py` |
| `gui_provider_http.py` | Current core | Provider catalog, local provider-login command, and redacted DeepSeek credential status/mutation; login execution/audit lives in `provider_login.py` | `tests/test_gui_server_provider_http.py`, `tests/test_live_agent_frontend_create.py` |
| `gui_public_invite_http.py` | Current core | Host-gated public URL and tunnel control | `tests/test_public_invite_http.py` |
| `gui_room_invite_http.py` | Current core | Host claim, invite admission, companion invite, leave/revoke | `tests/test_room_invite.py`, `tests/test_public_invite.py` |
| `gui_room_settings_http.py` | Current core | Repository-owned room-global settings | `tests/test_gui_server_room_settings_http.py` |
| `gui_room_lifecycle_http.py` | Mixed current/compatibility | Room directory/lifecycle plus HTTP/SSE history compatibility | `tests/test_gui_server_room_routes.py`, `tests/test_gui_server_streams_http.py` |
| `gui_room_moderation_media_http.py` | Mixed current/optional/compatibility | Roster compatibility, moderation HTTP, custom channels, voice presence | `tests/test_gui_server_room_routes.py`, `tests/test_room_channels_http.py` |
| `gui_room_agent_http.py` | Compatibility | Pre-canonical Agent Session HTTP create/resume/turn controls | `tests/test_agent_session_cli.py`, `tests/test_live_agent_session_agent_controls.py` |
| `gui_legacy_lobby_http.py` | Compatibility | HTTP lobby write/SSE plus explicit promotion and governed remote-bridge commands; command policy lives in `legacy_lobby_commands.py` | `tests/test_gui_server_streams_http.py`, `tests/test_gui_server_lobby_social.py`, `tests/test_lobby_promotion.py` |
| `gui_legacy_meeting_http.py` | Compatibility | Legacy meeting list/detail, lifecycle, workroom queue, and meeting SSE transport | `tests/test_gui_server_meeting_payload.py`, `tests/test_gui_server_discovery_workroom.py`, `tests/test_gui_server_streams_http.py` |
| `gui_legacy_meeting_lifecycle_http.py` | Compatibility | Retained meeting start/finalize HTTP commands; domain execution and operation audit live in `legacy_meeting_lifecycle.py`, with bounded audit projection in `legacy_meeting_operation_projection.py` | `tests/test_gui_legacy_meeting_lifecycle_http.py`, `tests/test_gui_server_session_lifecycle.py`, `tests/test_gui_server_moderation_finalization.py` |
| `gui_legacy_review_checkpoint_http.py` | Compatibility | Resident review-checkpoint request, sequential reply wait, non-official artifact creation, and prompt-free operation audit; orchestration lives in `legacy_review_checkpoint.py` | `tests/test_gui_legacy_review_checkpoint_http.py`, `tests/test_legacy_review_checkpoint.py`, `tests/test_gui_server_moderation_finalization.py`, `tests/test_live_agent_review_checkpoints.py` |
| `gui_legacy_official_turn_http.py` | Compatibility | Official-turn request, verified-reply call, and ordered sequence commands; execution/audit live in `legacy_official_turns.py` and per-meeting locking in `legacy_turn_scheduler.py` | `tests/test_gui_legacy_official_turn_http.py`, `tests/test_legacy_official_turns.py`, `tests/test_gui_server_turns.py` |
| `gui_legacy_official_round_http.py` | Compatibility | Official round, remaining-round batch, preset expansion, progress persistence, and optional finalization; policy/audit live in `legacy_official_rounds.py` and share `legacy_turn_scheduler.py` | `tests/test_gui_legacy_official_round_http.py`, `tests/test_legacy_official_rounds.py`, `tests/test_gui_server_turns.py`, `tests/test_gui_server_moderation_finalization.py` |
| `gui_side_chat_http.py` | Active optional | Separate side-chat history and SSE | `tests/test_frontend_side_chat_runtime.py` |
| `gui_social_http.py` | Active optional | Local profile, friends, and friend DM | `tests/test_gui_server_social_http.py`, `tests/test_room_social_flows.py` |
| `gui_mafia_http.py` | Active optional | Mafia game state and actions | `tests/test_gui_server_mafia_http.py`, `tests/test_mafia_game.py` |
| `gui_observability_http.py` | Active optional | Read-only local resources and release-health projections | `tests/test_gui_server_health.py` |
| `gui_live_agent_flow_http.py` | Compatibility | Legacy Play/flow supervisor controls | `tests/test_gui_server_session_lifecycle.py` |
| `gui_legacy_live_agent_read_http.py` | Compatibility | Legacy resident room/return-packet reads plus diagnostic history and health/readiness projections | `tests/test_gui_legacy_live_agent_read_http.py`, `tests/test_gui_server_room_payload.py`, `tests/test_gui_server_session_runs.py` |
| `gui_legacy_live_agent_presence_http.py` | Compatibility | Resident registration, heartbeat metadata, and graceful leave; state/audit behavior lives in `legacy_live_agent_presence.py` | `tests/test_gui_legacy_live_agent_presence_http.py`, `tests/test_legacy_live_agent_presence.py`, `tests/test_gui_server_roster.py`, `tests/test_gui_server_lobby_social.py` |
| `gui_legacy_live_agent_engagement_http.py` | Compatibility | Resident engagement-mode mutation and bounded operation audit; policy lives in `legacy_live_agent_engagement.py` | `tests/test_gui_legacy_live_agent_engagement_http.py`, `tests/test_legacy_live_agent_engagement.py`, `tests/test_gui_server_room_payload.py`, `tests/test_cli_timeout_presence.py` |
| `gui_legacy_live_agent_join_brief_http.py` | Compatibility | Side-effect-free external-resident entry packet; request mapping and packet policy live in `live_agent_join_brief.py` | `tests/test_gui_legacy_live_agent_join_brief_http.py`, `tests/test_gui_server_roster.py`, `tests/test_cli_timeout_presence.py` |
| `gui_legacy_provider_health_http.py` | Compatibility | Provider runtime-config validation and optional bounded probe dispatch with public-safe diagnostic projection; report policy lives in `provider_health.py` | `tests/test_gui_legacy_provider_health_http.py`, `tests/test_gui_server_streams_http.py`, `tests/test_provider_health.py` |
| `gui_legacy_codex_session_http.py` | Compatibility | Retained Codex meeting-session invite/join transport; config, pre-round lock, session ensure/restart, and safe operation audit live in `legacy_codex_session_compat.py` | `tests/test_gui_legacy_codex_session_http.py`, `tests/test_legacy_codex_session_compat.py`, `tests/test_gui_server_meeting_payload.py`, `tests/test_cli_timeout_core.py` |
| `gui_legacy_live_agent_probe_http.py` | Compatibility | Bounded resident reply probe with timeout normalization and prompt-free operation audit; execution policy lives in `legacy_live_agent_probe.py` | `tests/test_gui_legacy_live_agent_probe_http.py`, `tests/test_legacy_live_agent_probe.py`, `tests/test_gui_server_health_probes.py`, `tests/test_cli_timeout_diagnostics.py` |
| `gui_legacy_live_agent_speech_http.py` | Compatibility | Resident lobby and friend-DM replies; idempotency, flow conflict, governed append, heartbeat, and smoke redaction behavior lives in `legacy_live_agent_speech.py` | `tests/test_gui_legacy_live_agent_speech_http.py`, `tests/test_legacy_live_agent_speech.py`, `tests/test_gui_server_lobby_social.py`, `tests/test_gui_server_real_session_smoke.py`, `tests/test_turn_serialization.py` |
| `gui_legacy_live_agent_official_reply_http.py` | Compatibility | Verified resident official/review replies, idempotent append, official artifact/shared-memory refresh, heartbeat, and bounded audit; policy lives in `legacy_live_agent_official_reply.py` | `tests/test_gui_legacy_live_agent_official_reply_http.py`, `tests/test_legacy_live_agent_official_reply.py`, `tests/test_gui_server_moderation_finalization.py`, `tests/test_gui_server_turns.py` |
| `gui_legacy_live_agent_session_http.py` | Compatibility | Legacy resident-session mutations | `tests/test_gui_legacy_live_agent_session_http.py`, `tests/test_legacy_live_agent_session_service.py` |
| `gui_legacy_live_agent_process_http.py` | Compatibility | Legacy process-group mutations | `tests/test_gui_legacy_live_agent_process_http.py`, `tests/test_legacy_live_agent_process_service.py` |
| `gui_legacy_live_agent_session_run_http.py` | Compatibility | Durable legacy session-run controls | `tests/test_gui_legacy_live_agent_session_run_http.py`, `tests/test_legacy_live_agent_session_run_service.py` |
| `gui_legacy_live_agent_self_managed_http.py` | Compatibility | Retained stop/resume controls for same-host self-managed residents; execution and audit live in `live_agent_self_managed.py` | `tests/test_gui_legacy_live_agent_self_managed_http.py`, `tests/test_live_agent_self_managed.py` |
| `gui_legacy_live_agent_room_session_http.py` | Compatibility | Frontend-created session deletion across owned process group, generated config, meeting binding, and roster | `tests/test_gui_legacy_live_agent_room_session_http.py`, `tests/test_live_agent_room_admin.py` |
| `gui_legacy_live_agent_preflight_http.py` | Compatibility | Configuration-only resident preflight and redacted diagnostics | `tests/test_gui_legacy_live_agent_preflight_http.py`, `tests/test_gui_server_preflight.py` |
| `gui_legacy_live_agent_discovery_http.py` | Compatibility | Local CLI discovery and generated resident config bundles | `tests/test_gui_legacy_live_agent_discovery_http.py`, `tests/test_live_agent_discovery.py` |
| `gui_legacy_live_agent_smoke_http.py` | Compatibility | Basic, official-round, durable session, and approval-gated real-provider smoke execution | `tests/test_gui_legacy_live_agent_smoke_http.py`, `tests/test_gui_server_smoke_routes.py`, `tests/test_gui_server_real_session_smoke.py` |
| `gui_legacy_live_agent_readiness_http.py` | Compatibility | Aggregate readiness orchestration over health, smoke, and bounded resident probes | `tests/test_gui_legacy_live_agent_readiness_http.py`, `tests/test_gui_server_readiness_probes.py` |

`gui_room_http.py` is a compatibility coordinator and re-export surface. It
registers the room subdomains and retains historical service names. It is not
a service catalog for new code.

The coordinator's runtime seams are explicit in `RoomRouteAdapters`. Route
registration captures one adapter bundle instead of resolving `_late_*`
handler wrappers, so tests and alternate hosts can inject the process/turn
boundary without depending on generated handler internals.

## Routes Still In The Handler Chain

The generated handler dispatches the Router first, then executes these retained
families directly.

| Family | Classification | Why it remains reachable | Next action |
| --- | --- | --- | --- |
| `/ws` | Current core composition | Authenticated protocol upgrade is a transport concern | Keep the thin upgrade branch in the final handler |
| `/`, `/app/*`, `/join`, `/pair`, guarded React assets | Current core composition | `ReactStaticTransport` owns SPA/bootstrap delivery as one side-effect boundary | Keep static behavior out of domain route registrars |
| Seven retired exact API paths | Compatibility tombstones | No supported frontend or CLI caller remains; each returns `410 legacy_route_retired` from `gui_retired_http.py` | Retain through the first tagged `v0.1.x` release; audit for removal in `v0.2` or later |

## Retired Exact Routes

The 2026-07-15 caller audit found no production frontend or CLI caller for the
seven routes below. Their implementations and direct handler wiring were
removed. `gui_retired_http.py` now owns explicit `410 Gone` tombstones so an
obsolete external caller fails visibly instead of receiving a false success.

- `POST /api/demo`
- `GET /api/provider-sessions`
- `GET /api/codex-sessions` (the distinct `/invite` and `/join` routes remain)
- `GET /api/live-agent-create/options`
- `POST /api/live-agent-create/check`
- `POST /api/live-agent-create`
- `POST /api/live-agent-room/expel`

`tests/test_gui_route_ownership.py` limits the exact paths left in the handler
chain to transport and static delivery. New API behavior must register on the
`Router`; retired paths must not regain implementation behind their tombstone.

### Compatibility decision

Decision date: 2026-07-15.

Keep all seven `410 Gone` tombstones through the first tagged `v0.1.x`
release. The repository had no existing Git tag when this decision was made,
so the compatibility window has not yet elapsed. Removal is permitted no
earlier than `v0.2`, and only after a fresh frontend, CLI, documentation, and
external-caller audit confirms that no supported caller depends on the stable
`legacy_route_retired` response.

During the retention window:

- do not restore behavior behind these paths;
- do not add new product callers;
- preserve the `410`, stable error code, and replacement guidance; and
- treat final removal as a separate compatibility change with its own tests
  and release note.

## Extraction Order

### Phase 5.2 - Application services

1. Add one typed server-scoped application-services object.
2. Construct repository, identity backend, process/session services, ticket
   store, bridge manager, realtime controller, flow supervisor, and tunnel
   manager exactly once.
3. Keep `_make_handler()` compatibility overrides temporarily, but adapt them
   into the same services object instead of maintaining a second construction
   path.
4. Add explicit `start(server_url)` and idempotent `close()` ordering.
5. Prove startup failure closes only owned resources and an injected resource
   is neither replaced nor double-closed.

### Phase 5.3 - Retained read and diagnostic behavior

1. Completed: meeting read/lifecycle/workroom/SSE projections now live behind
   `LegacyMeetingQueryService`. `legacy_meeting_records.py` owns path
   validation, final/live record selection, progress merging, and legacy agent
   admission projection; `gui_legacy_meeting_http.py` owns the six public GET
   routes. `agentsassemble.gui` re-exports the historical query function names
   for compatibility without owning their implementation.
2. Completed: room and return-packet reads now belong to
   `LegacyLiveAgentQueryService`; their dynamic GET routes are Router-owned.
   `lobby_queries.py` owns the shared append-only lobby history reads used by
   this service and other compatibility paths. `LegacyLiveAgentDiagnosticQueryService`
   now owns operation history, process-event history, durable session-run
   listing, readiness overlays, and process-list projection.
   `legacy_live_agent_process_projection.py` owns the connection evidence shared
   by process reads and start/stop/restart responses. The diagnostic service
   also owns readiness/session-check projection and process-reason enrichment.
   `LegacyLiveAgentRosterQueryService` owns roster filters, safe projections,
   quota-field removal, and host-authored admission evidence. The Router calls
   it directly instead of receiving a free-function callback.
   `LegacyLiveAgentHealthQueryService` owns the aggregate health response;
   observation cursor/event policy and durable session-run/monitor policy live
   in separate focused modules. The Router calls this service directly and no
   longer receives process, monitor, or health callbacks. Configuration-only
   preflight now belongs to `LegacyLiveAgentPreflightService`, with its POST
   route on Router and shared report redaction in
   `diagnostic_report_projection.py`. Local CLI discovery and generated config/
   bundle writes belong to `LegacyLiveAgentDiscoveryService`; its POST route
   and safe audit projection are Router-owned. Credential-free basic and
   official-round smoke now share `LegacyLiveAgentSmokeService`; their Router
   routes preserve the basic smoke `409` contract-failure mapping, transport
   `502`, official-round `502`, and bounded operation audits. Credential-free
   durable session smoke is now on the same service and Router, with bounded
   soak validation and a fixed redacted `502` failure contract. Real-provider
   session smoke is also Router-owned but keeps its explicit approval and
   three-config gate, allowlisted response, degraded status, and redacted
   failures. Aggregate readiness now belongs to
   `LegacyLiveAgentReadinessService`; execution policy is separate from the
   allowlisted response and operation-audit projection, and its Router route
   receives the same health and smoke service instances as the direct routes.
3. Register their routes on `Router`; preserve methods, paths, authorization,
   status codes, redaction, and payloads.
4. Move a helper only with the route/service that owns its reason to change.
5. Leave deletion candidates in place.

### Phase 5.4 - Thin composition

The final `gui.py` may retain configuration, dependency construction, route
registration, WebSocket upgrade, React static delivery, and server
start/shutdown. Success is measured by ownership and tests, not a target line
count. `do_GET`, `do_POST`, and `do_DELETE` should become trust check, Router or
transport dispatch, then 404; domain behavior must not remain in those methods.

## Verification Gates

Run the cheapest relevant tests after each move and the full set before Phase 5
completion:

- Route ownership: `tests.test_gui_route_ownership` and
  `tests.test_legacy_react_parity_inventory`.
- Service lifetime: `tests.test_gui_server_server_lifecycle` and
  `tests.test_gui_room_repository_injection`.
- Canonical room: `tests.test_room_realtime`, `tests.test_room_native_cli_e2e`,
  `tests.test_ws_room_session`, and `tests.test_ws_room_client`.
- Legacy route families: the `test_gui_legacy_live_agent_*` and
  `test_legacy_live_agent_*_service` modules.
- Meeting/stream families: `tests.test_gui_server_meeting_payload` and
  `tests.test_gui_server_streams_http`.
- Optional UI families: provider, invite, social, side-chat, Mafia, and
  observability route tests.
- Identity regression: frontend `useCanonicalRoom` and
  `roomEventProjection` tests plus the browser agent-profile scenario.
- Final: full Python discovery, frontend Vitest, production build, Playwright,
  `compileall`, and `git diff --check`.

## Context Reset Brief

After a context reset, read `CURRENT_SYSTEM.md`, the active room-correctness
plan, and this file. Phase 5.3 meeting reads, room/return-packet reads, durable
diagnostic histories, process connection projections, readiness, roster/
admission projections, health aggregation, preflight, discovery, all four
direct smoke routes, and aggregate readiness are complete. Begin Phase 5.4 by
moving one typed route family per verified commit. Legacy lobby promotion and
remote-bridge commands are already Router-owned through
`LegacyLobbyCommandService`; they remain compatibility behavior and are not
canonical ambient routing. Current provider login is also Router-owned through
`ProviderLoginService`, separately from the legacy check/create branches. Do
not move the seven deletion
candidates while doing that work. Do not infer that a route is
obsolete merely because it is not called by React; CLI, MCP, smoke, and legacy
meeting clients are real compatibility consumers. The canonical identity
invariant still requires an Agent Session name/avatar update to reproject old
loaded messages, later history pages, typing state, roster, and new messages by
`participant_id`. A user-visible screenshot has disproved the earlier claim
that this contract is fully covered. Follow-up inspection proved that canonical
SQLite state, the WebSocket snapshot, and a freshly loaded current bundle all
resolve the renamed participant as `Makima`; the screenshot tab was still
executing an older hashed bundle. Follow-up code inspection found a separate
avatar truthiness bug: empty canonical avatars could revive event-time or
localStorage images. That fallback is removed, typing names prefer the current
participant, and the real Playwright settings flow now covers image selection,
crop/apply, canonical save, old-message reprojection, roster/detail projection,
and reload. Self-managed resident stop/resume is Router-owned through
`LegacySelfManagedAgentService`; delete-session is a separate Router boundary
through `LegacyLiveAgentRoomSessionService` because it owns server
process-group/config/meeting deletion rather than self-managed process
signaling. Retained meeting start/finalize are also Router-owned through
`LegacyMeetingLifecycleService`. Review-checkpoint readiness, sequential wait,
non-official artifact creation, and bounded audit are Router-owned through
`LegacyReviewCheckpointService`; shared sequential result normalization lives
in `legacy_turn_results.py`. Official-turn request/call/sequence are also
Router-owned through `LegacyOfficialTurnService`, while the per-meeting
reentrant lock shared with rounds and Codex join lives in
`legacy_turn_scheduler.py`. Round/rounds/preset scheduling, progress,
finalization, and prompt-free auditing are Router-owned through
`LegacyOfficialRoundService`. The retained handler no longer owns a legacy
meeting mutation route. Resident registration, heartbeat, and graceful leave
are also Router-owned through `LegacyLiveAgentPresenceService`; heartbeat keeps
its existing no-operation-audit policy while registration and leave retain
bounded audits. Phase 5.4 continues with a fresh inventory of the remaining
fixed compatibility paths and Codex compatibility families. Engagement-mode
mutation is Router-owned separately through
`LegacyLiveAgentEngagementService`, preserving its previous/current-mode audit.
Bounded reply probes are Router-owned through
`LegacyLiveAgentProbeService`, preserving timeout caps, result-only auditing,
and the existing `404/400` contract. Ordinary lobby and friend-DM replies are
Router-owned through `LegacyLiveAgentSpeechService`; its explicit dependencies
preserve the shared lobby lock, governed append, mute policy, and real-session
smoke redaction without importing `gui.py`. Official/review replies are
Router-owned through `LegacyLiveAgentOfficialReplyService`; reply verification,
idempotency, official artifact refresh, shared-memory projection, heartbeat,
and bounded audit no longer live in the generated handler. Do not push unless
the user explicitly asks.
