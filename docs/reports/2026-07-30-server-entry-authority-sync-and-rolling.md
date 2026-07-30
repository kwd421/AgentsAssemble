# Server entry, authority, synchronization, and rolling-update investigation

Date: 2026-07-30
Status: investigation plus synchronization follow-up; rolling updates remain research only

## Scope and method

This investigation covers:

1. the visible main-chat movement immediately after entering a server;
2. whether a non-host can perform server administration;
3. whether the recently deleted servers were actually removed;
4. whether server name, banner, icon, and profile/name state converge between
   browser-local state and server-owned state;
5. what would be required before calling an update “rolling” or “zero
   downtime”.

The initial investigation observed the live server through the browser and
read-only HTTP requests. SQLite was opened with `mode=ro&immutable=1`. No server
setting change, deletion, invite change, moderation action, or provider control
was sent. The follow-up changed main-chat history presentation and the
room-directory projection, then performed one controlled server restart so the
backend response could be exercised. No rolling-update experiment was run;
that restart did not preserve active provider-process ownership.

Four existing authorization/deletion contract tests were run:

```text
python3 -m pytest -q \
  tests/test_gui_server_room_settings_http.py::RoomSettingsHttpTests::test_remote_anonymous_post_cannot_change_room_global_settings \
  tests/test_room_realtime.py::RoomRealtimeControllerTests::test_room_settings_command_is_operator_only_and_rejects_noncanonical_updates \
  tests/test_room_realtime.py::RoomRealtimeControllerTests::test_member_leave_and_owner_confirmed_delete \
  tests/test_room_deletion.py::RoomDeletionServiceTests::test_delete_requires_owner_and_exact_confirmation

4 passed in 0.97s
```

## Findings

| Check | Finding | Confidence |
| --- | --- | --- |
| Main chat moves repeatedly on entry | Reproduced twice. It renders empty, then the newest snapshot, then an automatic older-history page, scrolling to the bottom after each render. | Confirmed by live browser timing and SQLite event counts |
| Non-host server administration | An ordinary remote guest cannot manage/delete the room, moderate members, or control agents. A read-write guest can still post, roll, and vote as intended. A same-machine loopback caller is inside the local-operator trust boundary. | Confirmed in code and targeted contract tests; no destructive live exploit attempt |
| Recently deleted servers | The four most recent user deletions have complete tombstones, do not appear in the live room API, and have no room/meeting artifact directories. All 28 tombstones are complete and none has a residual room directory. | Confirmed from the live API, both SQLite stores, and the filesystem |
| Name/banner/icon/profile synchronization | Final active-room settings are server-owned and survive reload, but entry is multi-phase. A custom room icon is missing while that room is inactive and appears only after selecting the room. There is also a stale duplicate label projection in identity storage. | Mixed: final active-room persistence passes; inactive icon hydration and duplicate-label drift fail |
| Rolling update | Not implemented. The current process owns the listener, WebSockets, runtime managers, and provider process handles. Restart reconciliation deliberately disconnects active Agent Sessions. | Confirmed in current lifecycle code |

## Synchronization follow-up

The two user-visible synchronization gaps found below were fixed:

- the first WebSocket snapshot is now treated as initialization data, not as
  permission to paint a partial chat; when it contains fewer than 20 visible
  messages and older history exists, the UI stays in its loading state until
  the first backfill is incorporated;
- an already-top feed requests older history without requiring a down-then-up
  gesture, and prepend anchoring preserves the reading position;
- the room directory now projects canonical room settings for every visible
  room, so inactive room labels, topics, icons, and banners do not wait for that
  room to become active;
- the active room's WebSocket settings events update the same directory/cache
  projection; the cache remains only a startup fast path and keeps up to 128
  rooms instead of truncating the current 32-room server to 24;
- history loading ownership is room-specific, so switching rooms while an old
  backfill is pending does not block the new room.

The follow-up also hardened the synchronization contract so the same class of
drift does not remain silent:

- every canonical room-settings snapshot, directory projection, update event,
  and ACK carries one deterministic `settings_revision`;
- room-setting writers must present the revision they observed, and a stale
  writer is rejected as `settings_conflict` in the same transaction before any
  setting or event changes;
- room-global settings and custom-channel mutations can no longer bypass the
  canonical event stream through the retained HTTP routes;
- custom-channel creation now changes the canonical room-settings record
  through `room.settings.update`, rather than maintaining a second HTTP-fetched
  React state;
- the browser validates contiguous room-event sequence numbers before
  advancing its resume cursor; a gap reconnects from the last verified
  sequence;
- a settings conflict, invalid settings snapshot/ACK, event gap, invalid
  sequence, or server backpressure leaves a visible recovery notice until a
  valid canonical snapshot arrives;
- a failed authoritative room-directory refresh preserves the last local
  projection but is no longer silent: it displays a persistent synchronization
  warning instead of presenting cached metadata as confirmed server state;
- room-directory hydration is guarded by both membership and metadata
  revisions, so an older HTTP response cannot overwrite newer WebSocket room
  metadata.

Post-fix browser evidence in `room-20260711T131220`:

```text
reload immediately: loading=true, rendered messages=0, room buttons=33
after initialization: loading=false, rendered messages=30, scrollTop=1357.5
one move to the top: rendered messages=54, scrollTop=857
inactive custom room icon: data-has-image=true before selecting the room
live room directory: 32 rooms, 0 missing settings revisions
normal room entry: 0 synchronization notices, 0 browser warnings/errors
```

The 0-message state above is a loading presentation, not the empty-channel
introduction. No 12-message intermediate paint was observed.

Verification after the follow-up:

```text
frontend: 40 files passed, 238 tests passed
frontend production build: passed
full Python suite: 3919 passed, 79 skipped, 1589 subtests passed
architecture checks: passed
test-quality gate: passed
codebase-map and diff checks: passed
```

## 1. Main-chat movement on server entry

### Live measurement

The room `room-20260711T131220` was reloaded twice. Both runs produced the same
three visible states:

| Time after page load | Rendered room-event rows | Feed height | Scroll top | State |
| ---: | ---: | ---: | ---: | --- |
| 26–35 ms | 0 | 608 px | 0 px | channel introduction/empty feed |
| 118–122 ms | 12 | 844 px | 236 px | newest snapshot rendered and pinned to bottom |
| 194–206 ms | 30 | 1,966 px | 1,358 px | automatic older-history page appended and pinned to bottom again |

The bottom gap stayed at 0–1 px. This is not an error retry or an unstable CSS
animation; it is two successive data commits followed by two bottom-scroll
operations. The browser console contained no warning or error during either
run.

### Exact cause

The initial room snapshot is bounded by **200 raw room events**, not by visible
chat messages (`agentsassemble/room/snapshots.py:15-16, 75-87`). In this room,
the newest 200 raw events contain only 12 `message_final` events:

```text
seq 2481..2680: 200 raw events, 12 message_final
seq 2281..2480: 200 raw events, 18 message_final
```

The other recent events are mostly `agent_session_state`, `message_delta`,
`turn_state`, and `activity_delta`. After the first 12 messages render,
`useLobbyHistory` waits 50 ms and automatically fetches older history whenever
fewer than 20 rendered messages are present
(`frontend/src/views/lobby/useLobbyHistory.ts:20-23, 242-267`). The older page
adds 18 more messages.

The same hook scrolls to `scrollHeight` whenever the feed is pinned to latest
and visible events change
(`frontend/src/views/lobby/useLobbyHistory.ts:214-240`). Therefore the user
sees:

```text
empty/introduction → 12 messages + bottom jump → 30 messages + bottom jump
```

The owning behavior was identified by the investigation and then fixed in the
synchronization follow-up above.

### Separate entry-time movement also observed

This is not the main-chat issue above, but it compounds the feeling that the
whole screen is settling after entry:

- the browser cache stores at most 24 dock rooms
  (`frontend/src/lib/roomDockPersistence.ts:11-12, 43-63`);
- the current server has 32 active rooms;
- the room rail first used the local cache and then reconciled the server
  directory (`frontend/src/app/useRoomDirectory.ts:106-148`);
- observed room-rail buttons changed from 26 to 34, including the home/add
  controls, within about 60 ms;
- a custom room rendered with the default appearance first, then changed to
  `custom` about 70 ms later.

## 2. Non-host server control

### Canonical WebSocket commands

`capabilities_for_identity` grants `room.manage`, `room.delete`,
`participant.kick`, `participant.mute`, and `agent.control` only when the
identity is an operator (`agentsassemble/room/commands.py:68-84`).
`room.settings.update` and `room.delete` enforce those capabilities in the
command controller (`agentsassemble/room/realtime.py:590-624, 675-692,
1722-1724`).

Deletion has a second check: the operator must also match the room owner, and
the typed confirmation must exactly match the canonical room name
(`agentsassemble/room/deletion.py:45-71`;
`agentsassemble/room/realtime.py:1325-1344, 1383-1392`).

An ordinary read-write invited participant may post messages and use the room's
random/vote functions. A read-only invite cannot do those writes. Those are
room-use capabilities, not server-administration capabilities.

### HTTP compatibility routes

Room-global HTTP setting changes require both room access and moderator
authority (`agentsassemble/web/routes/room_settings.py:47-69`). A remote
anonymous write was verified to return an authorization error without changing
the stored label by the targeted test above.

### Trust-boundary caveat

The desktop server deliberately treats a request as a local operator when the
server is bound to loopback, the Host is loopback, and Origin is loopback or
empty (`agentsassemble/web/router.py:181-195`). Therefore:

- a normal remote invitee is not a host and cannot administer the server;
- another person or malicious process already able to operate through the same
  local machine/loopback origin is inside the current operator boundary.

This is a local-desktop trust decision, not per-browser-account isolation. It
must be revisited if the product expects multiple mutually untrusted people to
use the same machine or if the control plane is exposed beyond loopback.

## 3. Deleted-server state

### Current counts

```text
canonical rooms: 60 total = 32 active + 28 archived
deleted-room tombstones: 28 total = 28 complete + 0 incomplete
```

The most recent deletion records were:

| Room | Deleted at (UTC) | Cleanup |
| --- | --- | --- |
| `dnd-final-175023` | 2026-07-30 03:09:08 | complete |
| `pinebrook-official-20260727-1853` | 2026-07-30 03:05:14 | complete |
| `20260517T233508Z-41f993ef` | 2026-07-30 03:04:47 | complete |
| `20260515T143002Z-a02121f2` | 2026-07-29 14:48:22 | complete |

For all four:

- absent from `/api/rooms?include_archived=true`;
- absent from the active canonical `rooms` table;
- absent from the identity room registry;
- no `.agentsassemble/rooms/<room-id>` directory;
- no `.agentsassemble/meetings/<room-id>` directory;
- no matching recent side-chat or attachment path.

Across all 28 tombstones, none has a residual `.agentsassemble/rooms/<room-id>`
directory.

The retained tombstone itself is intentional. It prevents accidental room
recreation and makes cleanup/idempotency resumable. The deletion transaction
and cleanup contract are documented in `docs/product/CURRENT_SYSTEM.md:391-397`
and implemented in `agentsassemble/room/deletion.py:72-111` and
`agentsassemble/room/deleted_cleanup.py:48-106`.

This verifies current observable persistence and artifact cleanup. It does not
prove that every third-party provider process terminated cleanly at the moment
of each historical deletion; that would have required contemporaneous process
telemetry.

## 4. Server/local synchronization

### What is server-owned

Room label, topic, short label, banner, icon, conversation mode, and channels
are one canonical room-global settings record
(`docs/product/ROOM_REPOSITORY.md:33-51`). The indexed canonical `rooms.label`
projection and `room_settings.label` currently have zero mismatches.

Agent name/avatar updates are canonical participant and Agent Session changes;
the participant, session, event, and ACK are committed together
(`docs/product/CURRENT_SYSTEM.md:355-366`). The browser gives canonical
participant/session identity precedence over the legacy local agent-profile
fallback (`frontend/src/views/components/MemberList.tsx:139-160`).

The local human profile is a different scope. It is persisted by the local
server in `.agentsassemble/user_profile.json`
(`agentsassemble/features/social/profile.py:29-45, 136-151`) and loaded through
`/api/user-profile`. The current live API and file agree. The browser also keeps
the display name in `agentsassemble.name` only as a composer compatibility
value (`frontend/src/lib/userProfileModel.ts:46-47`).

### What passed live

For `dnd-blackglass-gemini-dm-20260727-171745`:

- canonical settings return `banner_preset=custom`;
- banner and icon URLs remain in the room settings after reload;
- both attachment URLs return HTTP 200 with image content;
- selecting the room eventually renders the custom banner and icon.

The local human profile API also matched its server-side profile file after
reload.

### Pre-fix confirmed gaps

#### A. Inactive custom room icons are not hydrated

With another room active, the custom room's rail button was measured as:

```text
data-has-image=false, background-image=none, fallback text="D"
```

After selecting the custom room, the same button became:

```text
data-has-image=true,
background-image=url(/api/attachments/a946c125...),
fallback text=""
```

The reason is structural: `useRoomSettingsController` begins with an empty
appearance map and loads canonical settings for only the active room
(`frontend/src/app/useRoomSettingsController.ts:79-105, 189-214`), while
`RoomRail` expects an appearance entry for every room
(`frontend/src/views/components/RoomRail.tsx:78-100`).

So the server data is intact, but the room rail does not project it until the
room becomes active. This is a user-visible synchronization failure, not data
loss.

#### B. Appearance is multi-phase on reload

The active custom room was observed changing from `default` at 20 ms to
`custom` at 89 ms. The final result is correct, but the intermediate default
paint is visible and contributes to entry flicker.

#### C. The identity room registry carries stale duplicate labels

There are 53 label mismatches among rooms present in both canonical room
storage and identity storage. Examples include:

```text
canonical: Pinebrook Live D&D
identity:  새 회의실

canonical: pinebrook-official-20260727-1858
identity:  Peril in Pinebrook — 공식 플레이
```

The live room-list route merges the identity registry first and then lets the
canonical RoomRepository overwrite the final label
(`agentsassemble/web/routes/room_history.py:115-165`). The final UI therefore
uses the documented canonical source, but the stale duplicate projection makes
entry reconciliation harder to reason about and can expose an old label in an
intermediate or compatibility path.

No identity-database data repair or authority migration was performed. The
canonical room repository remains authoritative, and the final directory
projection now carries that canonical record from initial hydration onward.

## 5. Rolling-update research

### Current state

AgentsAssemble currently starts one `ThreadingHTTPServer`, binds the public
port directly, starts process/session/tunnel services, and blocks in
`serve_forever()` (`agentsassemble/application/gui_runtime.py:131, 177-221`).
There is no stable front proxy, alternate-port generation, readiness gate,
traffic switch, connection drain, or rollback controller.

More importantly, the server process owns provider process handles, bridge
leases, WebSockets, attention runtime, and background monitors. Startup
reconciliation explicitly changes every active runtime session to
`disconnected` because those handles cannot survive a server restart
(`agentsassemble/room/startup_reconciliation.py:16-79`).

Therefore the current system supports neither a rolling backend replacement
nor zero-downtime Agent Sessions. Rebuilding frontend assets without restarting
Python is not a rolling backend update.

### What “rolling” would require here

A safe target has at least these independent contracts:

1. **Stable ingress owner.** A small supervisor/gateway owns port 8765 while
   versioned backends listen on internal ports or inherited sockets.
2. **Readiness before traffic.** A new backend must open repositories, validate
   schema compatibility, initialize services, and pass a real readiness probe
   before receiving requests.
3. **Atomic traffic switch and rollback.** New connections move to the new
   backend only after readiness; failed readiness leaves the old backend
   serving.
4. **HTTP/WebSocket drain.** The old backend stops accepting new work but keeps
   existing requests and WebSockets until completion or a bounded deadline.
   Reconnecting room clients must resume from durable room sequence.
5. **Expand/contract persistence compatibility.** Old and new versions overlap,
   so both must understand the database schema and event format during the
   overlap. Destructive migrations cannot be part of the switch.
6. **One owner for each side effect.** Scheduled wakeups, attention jobs,
   process monitors, public tunnels, and provider lifecycle commands cannot run
   concurrently in two active owners without a lease/fencing contract.
7. **Agent Session continuity.** True zero-downtime requires moving provider
   process/bridge ownership outside the replaceable web process or implementing
   an explicit lease handoff. Without that work, only the web surface could
   roll; active Agent Sessions would still disconnect.
8. **Versioned static assets.** HTML and hashed assets must remain compatible
   while old tabs and new tabs coexist.

For this cross-platform desktop product, an application-owned supervisor and
stable local gateway is more portable than assuming nginx or systemd. That is
an architectural direction to evaluate, not a decision recorded by this note.

### Official reference points

- Kubernetes defines a rolling update as bringing up a new revision while
  controlling old/new availability with readiness, `maxUnavailable`, and
  `maxSurge`:
  <https://kubernetes.io/docs/concepts/workloads/controllers/deployment/>
- nginx's graceful reload starts new workers, closes old listen sockets, and
  lets old workers finish existing clients before exit:
  <https://nginx.org/en/docs/control.html>
- systemd socket activation demonstrates a stable listener passing socket file
  descriptors to service processes:
  <https://www.freedesktop.org/software/systemd/man/latest/systemd-socket-activate.html>
- Python documents `http.server` as a basic server that is not recommended for
  production; it does not provide a rolling orchestration contract:
  <https://docs.python.org/3/library/http.server.html>
- SQLite WAL permits readers and a writer concurrently on the same host but
  still has only one writer and requires checkpoint/locking discipline:
  <https://www.sqlite.org/wal.html>

## Remaining decision points

Main-chat batching and inactive-room appearance hydration are complete. The
remaining choices are separate:

1. whether to remove the non-authoritative identity-room label projection or
   retain it only as a legacy compatibility field;
2. whether rolling work initially targets only HTTP/UI availability or also
   promises uninterrupted Agent Sessions.
