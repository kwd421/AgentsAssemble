# Current System Orientation

Status: current starting point

Updated: 2026-07-11

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
        <-> RoomStore / rooms.sqlite3
        <-> persistent provider adapter
```

There is one authority for each concern:

- room, participant, Agent Session, event, and command state: `RoomStore`;
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

Known limitation: the current contract effectively means `turn assigned -> one
visible reply`. It does not yet support passive observation or a clean
`stay silent` decision. `continuous` mode is bounded automatic relay, not true
autonomous participation.

The researched next direction is an event-driven attention boundary that can
select nobody, keep unselected models asleep, and assign a provider turn only
when an agent should speak. Research, not yet implementation authority:
`docs/reports/autonomous-room-participation-research.md`.

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
- Do not push, delete user data, expose a tunnel, or mutate credentials without
  the user's explicit request.

Detailed product policy: `docs/product/OPERATING_MODEL.md`.

## Primary Module Map

| Change | Start here |
| --- | --- |
| Room persistence and sequence | `room_store.py`, `room_database.py`, `room_types.py` |
| WebSocket commands and ACL | `room_commands.py`, `ws_room_session.py`, `room_realtime.py` |
| Routing and provider context | `room_routing.py`, `room_context.py`, `room_turn_context.py` |
| Fanout and bridge delivery | `room_event_broker.py`, `room_agent_bridge.py` |
| Provider catalog and settings | `native_cli_providers.py`, `provider_capabilities.py` |
| Codex app-server lifecycle | `codex_app_server_runtime.py`; compatibility exports in `agent_sessions.py` |
| Other provider process lifecycle | `room_bridge_process.py`, `live_cli.py`, provider adapter module |
| Invites and attendance | `room_invite.py`, `room_attendee.py` |
| Provider credentials | `provider_secrets.py`, provider credential routes |
| GUI HTTP response/WebSocket transport | `gui_response.py`, `gui_ws_http.py`; composition in `gui.py` |
| Canonical room HTTP routes | `gui_room_*_http.py`; coordinator in `gui_room_http.py` |
| CLI parser registration | `cli_parser_common.py`, `cli_parser_*.py`; dispatch in `cli.py` |
| React room state | `frontend/src/App.tsx`, `frontend/src/app/`, `useCanonicalRoom.ts` |
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
