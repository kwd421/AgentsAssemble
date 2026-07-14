# Current System Orientation

Status: current starting point

Updated: 2026-07-14

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
                <-> RoomStore / rooms.sqlite3 (current local default)
        <-> persistent provider adapter
```

There is one authority for each concern:

- room, participant, Agent Session, event, and command state:
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
copy.

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
| Room persistence and sequence | `room_store.py`, `room_database.py`, `room_types.py` |
| Room storage authority and transaction contract | `room_repository.py`, `docs/product/ROOM_REPOSITORY.md` |
| Autonomous participation and durable attention | `room_attention.py`, `docs/product/ATTENTION_MODEL.md` |
| WebSocket commands and ACL | `room_commands.py`, `ws_room_session.py`, `room_realtime.py` |
| Routing and provider context | `room_routing.py`, `room_context.py`, `room_turn_context.py` |
| Fanout and bridge delivery | `room_event_broker.py`, `room_agent_bridge.py` |
| Provider catalog and settings | `native_cli_providers.py`, `provider_capabilities.py` |
| Provider catalog/credential HTTP | `gui_provider_http.py`; secret storage in `provider_secrets.py` |
| Codex app-server lifecycle | `codex_app_server_runtime.py`; compatibility exports in `agent_sessions.py` |
| Other provider process lifecycle | `room_bridge_process.py`, `live_cli.py`, provider adapter module |
| Invites and attendance | `room_invite.py`, `room_attendee.py` |
| Provider credentials | `provider_secrets.py`, provider credential routes |
| Canonical attachment upload/download HTTP | `gui_attachment_http.py`; storage in `attachments.py`, room media in `room_store.py` |
| GUI HTTP response/WebSocket transport | `gui_response.py`, `gui_ws_http.py`; composition in `gui.py` |
| GUI Host/Origin and public-route trust policy | `gui_request_security.py` |
| Durable legacy session-run monitor lifecycle | `session_run_monitor.py`; reconcile policy wiring in `gui.py` |
| Canonical room HTTP routes | `gui_room_*_http.py`; coordinator in `gui_room_http.py` |
| Legacy lobby POST/SSE compatibility | `gui_legacy_lobby_http.py`; do not attach new canonical behavior here |
| Legacy resident read-only HTTP projections | `gui_legacy_live_agent_read_http.py`; payload policy remains outside the registrar |
| Room settings HTTP | `gui_room_settings_http.py`; persistence and normalization in `room_settings.py` |
| Friends, direct-message and local-profile HTTP | `gui_social_http.py`; direct-message process callback wired in `gui.py` |
| Play Mode Mafia HTTP | `gui_mafia_http.py`; game state and rules in `mafia_game.py` |
| Side-chat storage and room scoping | `side_chat.py`; event normalization in `meeting_events.py`; HTTP/SSE routes in `gui_side_chat_http.py` |
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
