# Agent Session Runtime Status Report

Date: 2026-07-11

Branch: `codex/risuai-character-personas`

Baseline before this work: `2d39ded Unify Agent Sessions with room participants`

## Executive summary

AgentsAssemble now has one canonical room path for server-owned local CLI agents,
invited agent bridges, browsers, and API-backed agents:

```text
Browser / Agent Bridge
        <-> canonical /ws?ticket=...
RoomRealtimeController
        <-> RoomStore (rooms.sqlite3)
        <-> persistent provider runtime
```

The default live path no longer depends on the separate `/ws/rooms/general`
protocol or a second general-room event schema. Codex, Antigravity, and OpenCode
were exercised as real persistent providers. Their room transport overhead was
small compared with direct provider response time, their provider processes were
reused across turns, and cleanup left no provider process alive.

This is not yet a fully autonomous ambient-agent system. The current server still
assigns attention/turns and sends a bounded room update to a provider session.
The next architectural decision is whether agent bridges should receive an
always-on event subscription and choose `speak`, `wait`, or `react` themselves.

## Implemented runtime architecture

### Canonical room path

- `RoomStore` is the room, participant, Agent Session, command-result, and event
  source of truth.
- Browser and agent bridge commands use the authenticated `/ws?ticket=...`
  protocol with request ACK/NACK and sequenced events.
- Agent Session participants are rendered from the canonical snapshot. The
  frontend no longer requires a synthetic legacy `LiveAgent` to display or
  control a canonical session.
- Desktop and mobile use the same `AgentSessionDetails` controls.
- `@mention` selects an intended responder but remains a public room message.
- Provider sessions receive only their bounded bootstrap or events after their
  durable cursor, not a full RoomStore dump.

### Provider runtimes and profiles

`RuntimeProfile` and its isolation key now include:

- model
- reasoning effort
- service tier
- variant
- permission mode
- workspace
- transport

Provider-native controls are discovered and exposed to the frontend. A running
session rejects profile changes; it must be stopped before reconfiguration.

Implemented provider paths:

- Codex: persistent app-server/session stream wrapper for the room attendee path.
- Antigravity: persistent native CLI with transcript JSONL extraction.
- Grok: ACP runtime profile support.
- Claude: interactive/SDK-compatible path; `claude -p` remains forbidden.
- OpenCode: one shared `opencode serve` per host and separate agent sessions.
- DeepSeek: streaming API runtime with keyring-backed credential lookup.

### Credentials and process boundaries

- DeepSeek credentials are stored through OS keyring:
  - macOS Keychain
  - Windows Credential Manager
  - Linux Secret Service
- Credential status responses return only `configured` and `source`.
- Provider keys, invite tokens, and host/session credentials are excluded from
  room events, model input, CLI argv, logs, and diagnostics.
- Provider child environments are rebuilt from an allowlist instead of inheriting
  every host `*_TOKEN` and `*_API_KEY` value.
- Remote credential mutation requires HTTPS and moderator capability.
- DeepSeek was not called against the real service because no key was configured
  during verification.

### Platform support

- POSIX terminal sessions use PTY.
- Windows terminal sessions use a conditional `pywinpty` ConPTY adapter.
- Process-tree cleanup is represented in the platform adapter contract.
- A GitHub Actions Ubuntu/Windows targeted runtime matrix was added.
- Actual Windows ConPTY execution remains unverified on this macOS host.

## Agent invite behavior

- `agent.invite.create` reuses one-use, expiring room invite semantics.
- `assemble room attend --provider <id>` reads the invite URL from hidden stdin.
- The attendee authenticates once, keeps the canonical WebSocket open, and owns a
  persistent provider session.
- Initial provider-visible context is restricted to room name, display name,
  natural-language participation rules, and bounded finalized messages.
- Server URL, token, database path, backend implementation, and project path are
  not included in provider-visible room context.

An actual Codex invite smoke returned `INVITE-CHAT-OK`. The joined participant was
recorded as an externally owned canonical agent bridge, and checks found no token,
server URL, project path, or backend-detail exposure in the provider-visible data.

## Real provider smoke results

### Warm direct-versus-room latency

Codex Luna (`gpt-5.6-luna`, low/default), three measured warm turns:

- direct TTFO p50: 1826.2 ms
- room-observed TTFO p50: 1848.0 ms
- room overhead p50: 22.7 ms
- room overhead p95: 25.4 ms
- timeout count: 0
- same provider PID across turns: yes
- marker recall: yes
- TUI noise detected: no
- alive after stop: no

Antigravity (`Gemini 3.5 Flash (Medium)`), three measured warm turns:

- direct TTFO p50: 2022.2 ms
- room-observed TTFO p50: 2061.8 ms
- room overhead p50: 18.5 ms
- room overhead p95: 50.4 ms
- timeout count: 0
- same provider PID across turns: yes
- marker recall: yes
- TUI noise detected: no
- alive after stop: no

OpenCode (`opencode-go/glm-5.2/default`):

- measured room overhead was approximately 19.5 ms in its dedicated successful run
- same provider/session across turns: yes
- marker recall: yes
- timeout count: 0
- TUI noise detected: no
- provider and bridge cleanup: verified

The measured room overhead is substantially below model inference time. The main
latency is provider response time, not local WebSocket delivery.

### Three-minute shared-room conversation

Providers: Codex Luna, Antigravity Flash, OpenCode GLM-5.2

Topic: an old undersea station receiving tomorrow's distress signal at midnight

- actual duration: 182.514 seconds
- public agent turns: 42
- speaker cycles: 14
- each provider turn count including control checks: 15
- all agents saw full peer context after warmup: yes
- agent output `@mention` count: 0
- group TTFO p50: 3673.0 ms
- group TTFO p95: 4864.8 ms
- group completion p50: 3774.2 ms
- group completion p95: 6195.4 ms
- pause/resume backlog check: passed for all three providers
- same provider and bridge PID after pause/resume: passed
- kick and explicit re-add checks: passed
- unexpected extra turns while quiet: 0
- TUI residue: 0
- alive after final stop: false for every provider

Evidence file:

`.agentsassemble/rooms/general/smoke/native_cli_20260711T002923Z_fb4c52.json`

An earlier Antigravity run timed out before transcript extraction was corrected.
The subsequent strict transcript run passed with zero timeout. The report keeps
this distinction rather than presenting every historical run as successful.

## Frontend behavior

- The existing multi-room shell remains in place.
- The fixed top Agent Session panel was removed.
- Human and agent participants appear in the same roster.
- Agent detail exposes provider-native settings and start, pause, resume,
  interrupt, stop, and kick controls.
- Canonical participants from the WebSocket snapshot are merged into the roster;
  a legacy `LiveAgent` record is not required.
- The mobile channel-information panel can open the same canonical Agent Session
  controls used on desktop.
- New empty rooms show only `#general`; stage-log, work-board, and records are not
  generated as empty default channels.
- The DeepSeek secret input is cleared after submission and is not written to
  localStorage.

Playwright verified one persistent fake CLI session started on desktop, streamed a
clean room message, then continued through diagnostics, pause, backlog delivery,
resume, and stop after switching to a 390x844 mobile viewport.

## Legacy room data incident and repair

### What happened

Legacy meeting transcripts remained under `.agentsassemble/meetings`, while the
new UI read canonical `rooms.sqlite3` events. Several visible rooms therefore had
only a canonical `room_created` event and appeared empty even though their legacy
meeting artifacts contained real discussion.

### Repair

`assemble room migrate-legacy-messages --dry-run|--apply` was added.

- Source priority: official `live_events.jsonl`, then structured transcript
  heading fallback.
- Imported data: official message and synthesis records only.
- Excluded data: startup status, research-progress noise, and terminal UI output.
- Preserved data: actor, display name, original timestamp, round, turn id, and a
  deterministic legacy source id.
- Idempotence: already imported source ids are skipped.
- Safety: source fingerprint must match the dry run, and both SQLite databases are
  backed up before apply.

Applied result:

- rooms imported: 11
- messages imported: 101
- legacy participants registered: 47
- remaining migration candidates after apply: 0

Import backup:

`.agentsassemble/backups/legacy-message-import-20260711T013932Z`

Four user-confirmed obsolete rooms were removed. Initial deletion exposed another
bug: an open browser with stale local room state could reconnect and implicitly
recreate a deleted room. Canonical `deleted_rooms` tombstones now prevent stale
WebSocket clients from recreating deleted IDs. Restart and browser reconnect were
tested; all four stayed absent.

Deletion backup:

`.agentsassemble/backups/manual-room-delete-20260711T014737Z`

## Empty-room policy

Empty-room pruning was a one-time operator cleanup and is not a product command. Room lifecycle now uses explicit participant leave and owner-confirmed server deletion.
The temporary `prune-empty` CLI and its product-facing tests were removed. Canonical
room deletion retains a tombstone so stale browser state cannot recreate the room.

## Verification

- `python3 -m unittest discover -s tests -t .`
  - 2801 tests passed
- targeted migration/prune/realtime tests
  - 36 tests passed before the final tombstone addition
- tombstone and migration tests
  - 6 tests passed
- `npm --prefix frontend test`
  - 4 files, 14 tests passed
- `npm --prefix frontend run test:e2e`
  - canonical desktop-to-mobile session scenario passed
- `npm --prefix frontend run build`
  - passed; Vite reported only the existing large-chunk warning
- `git diff --check`
  - passed
- changed-diff secret scan
  - no real credential found; the only match was the fixed test value `sk-private`

## Known gaps and next decisions

1. DeepSeek is implemented but has not passed a real API smoke with a user key.
2. ConPTY has fake-adapter and CI coverage but no real Windows-host smoke yet.
3. Claude was not included in the final real-provider group run because its local
   quota was exhausted at the time.
4. The current room remains server-attention-driven. Provider sessions persist,
   but each response still receives a bounded `Room update` and `Your turn`
   instruction. A more autonomous model would keep an agent bridge subscribed to
   room events and let it choose `speak`, `wait`, or `react` under server-enforced
   budgets.
5. Media events need a capability-aware bridge contract: original image/PDF for
   capable providers, OCR/text/keyframes for others, and no local path or signed
   credential leakage.
6. Legacy and experimental meeting endpoints remain in the repository. They are
   not the canonical Agent Session path but still increase maintenance surface.
7. The frontend production bundle is about 525 kB minified and still triggers the
   Vite chunk-size warning.

## Current running state

At report time the GUI is running at:

`http://127.0.0.1:8765`

After restart and stale-browser reconnect, the four deleted room IDs remained
absent from `/api/rooms`, absent from canonical rooms, and present only as four
deletion tombstones.
