# Current-Code Hardening Plan — 2026-07-28

Status: complete — all mandatory executable verification boundaries passed

Owner boundary: canonical shared-room product only

Legacy boundary: record findings, do not repair or extend legacy code in this
workstream

## Why This File Exists

This plan is durable task state. It must remain sufficient to resume the work
after chat compaction, a new Codex task, or a different maintainer taking over.
Do not rely on chat history for scope, completion, or verification claims.

Before continuing this plan, read:

1. `AGENTS.md`
2. `docs/product/CURRENT_SYSTEM.md`
3. this file
4. the closest implementation and behavioral tests for the next unchecked item
5. the detailed current topic document named by `CURRENT_SYSTEM.md` when that
   item crosses a room, provider, persistence, security, or GUI boundary

The separate legacy record is
`docs/reports/2026-07-28-legacy-static-audit.md`.

## Immediate Goal

Repair the confirmed current-product defects found by the 2026-07-28 static
audit, strengthen the tests that allowed those defects to remain green, and
make one reproducible verification command cover the actual release boundary.

## Non-Goals

- Do not repair, refactor, or extend code below `agentsassemble/legacy/`.
- Do not reconnect canonical shared-room behavior to the legacy
  meeting/resident pipeline.
- Do not launch real providers merely to prove unit or integration behavior.
- Do not redesign the room UI while correcting state and persistence behavior.
- Do not count a source-string, symbol, callback, or mock-interaction assertion
  as proof of a user workflow.

## Required Working Method

For every defect:

1. Add or identify a behavioral test that fails for the audited defect.
2. Exercise the real boundary that owns the behavior.
3. Make the smallest current-path correction.
4. Run the targeted test.
5. Run the broader verification required by the affected boundary.
6. Record the command and outcome in this ledger before marking the item done.

Tests for failure-sensitive behavior must cover the relevant failure point, not
only the successful result. Applicable cases include cancellation, timeout,
partial persistence, retry, duplicate requests, response-order inversion,
restart, reconnect, room switching, and refresh.

## Confirmed Current-Path Work

### A. Security and transport

- [x] Reject multiline or otherwise ambiguous Antigravity permission commands
      before terminal auto-approval.
- [x] Bound and validate HTTP JSON `Content-Length`, including negative and
      non-numeric values.
- [x] Bound the aggregate size and fragment count of one WebSocket message.
- [x] Make `WsTicketStore` thread-safe.
- [x] Close partially constructed WebSocket clients and upgrade channels on
      failure.
- [x] Validate remote-bridge transport security rather than recommending a
      bearer token over non-loopback cleartext HTTP.

### B. Canonical state, lifecycle, and persistence

- [x] Reconcile attention in a way that cannot cancel valid work merely because
      related record families were truncated independently.
- [x] Import and execute valid persona probability blocks without `NameError`.
- [x] Kick provider-backed participants by stable participant/session identity,
      not mutable display role.
- [x] Make Agent Session creation compensate or resume safely after partial
      participant/session/event persistence.
- [x] Prevent stale callbacks from recreating canonical state after SQLite room
      deletion.
- [x] Make SQLite participant/room mutation and lifecycle event recording match
      the repository transaction contract.
- [x] Preserve real SSE heartbeat cadence during an idle room stream.
- [x] Scope side-chat history before applying its retention limit.
- [x] Match `@all` as a mention token rather than an arbitrary substring.
- [x] Parse persisted boolean values with the existing strict boolean parser.
- [x] Serialize stable-public-URL publication so an old retry cannot overwrite
      a newer URL.

### C. Provider correctness

- [x] Do not stage or publish a Grok ACP outbox write unless an actual
      `allow_once` option was selected.
- [x] Establish output-file freshness before accepting Codex adapter or
      resident output.
- [x] Correlate OpenCode completion and assistant messages to the current turn.
- [x] On PTY/ConPTY timeout, explicitly interrupt or invalidate the provider
      turn so late output cannot become a later turn.
- [x] Drain resident stdout and stderr concurrently to avoid pipe deadlock.
- [x] Resolve bridge-process creation and registration under one ownership
      boundary so duplicate children cannot be spawned.

### D. Browser behavior

- [x] Publish canonical membership change state for human invite admission so
      already-connected browsers refresh when a guest joins.
- [x] Remove left and kicked participants from canonical browser state so an
      HTTP roster refresh cannot resurrect ghost members.
- [x] Expire a stored guest session during startup and provide a usable exit
      from the guest surface.
- [x] Add generation or cancellation ownership to polling and Records detail
      requests.
- [x] Roll back or reload the authoritative value after room-setting save
      failure; do not label the failed optimistic value as previously confirmed.
- [x] Scope lobby attachments/drafts and side-chat drafts to their room.
- [x] Use the current user profile as the friend-DM sender.
- [x] Correct the audited stale voice cleanup, profile-save, Agent Session
      detail, retained secret, unhandled action-error, and no-op control paths
      while staying within the same focused components.

### E. Verification system

- [x] Audit all executable Python test bodies, including support-file contract
      mixins.
- [x] Treat deletion-only diffs and helper/setup/fixture changes as test
      changes.
- [x] Reject tautological or implementation-only oracles that currently pass
      the quality gate.
- [x] Inspect helper- and alias-mediated private patches and mock-only oracles.
- [x] Add adversarial self-tests for every quality-gate bypass fixed here.
- [x] Replace the misleading local "full suite" label with one canonical
      verification command that runs Python, PostgreSQL contracts, frontend
      unit tests, frontend build, browser E2E, generated artifacts, and
      `git diff --check`.
- [x] Add focused mutation canaries for critical authorization, rollback,
      room-scoping, and response-order contracts.

## Completion Gate

This plan is not complete until all of the following are recorded as passing:

```text
targeted regression tests for every checked item
full Python suite
mandatory PostgreSQL contracts
frontend unit tests
frontend production build
browser-visible room workflow tests
critical mutation canaries
generated-artifact verification
git diff --check
final diff review against this checklist
```

A test count is not completion evidence. A check is evidence only for the
behavior it actually executes and observes.

## Durable Progress Log

Update this section after each coherent commit. Keep entries short and include
the commit, completed checklist items, exact verification commands, and any
remaining limitation.

| Commit | Completed scope | Verification | Remaining limitation |
| --- | --- | --- | --- |
| `82ce4c66` | Plan and legacy audit record | documentation review | implementation not started |
| `6ed536be` | Antigravity multiline command rejection; bounded HTTP JSON bodies; bounded WebSocket aggregate messages | `python3 -m unittest tests.test_terminal_interactions tests.test_gui_router tests.test_room_websocket tests.test_web_transport_package`; `python3 -m unittest tests.test_gui_server tests.test_gui_server_room_routes tests.test_ws_room_session tests.test_antigravity_resident` (524 tests); `python3 scripts/check_test_quality.py --base HEAD`; `make generated-artifacts-check`; `git diff --check` | full-suite and browser verification remain completion-gate work |
| `5ee8926f` | Durable test-admission rule in `AGENTS.md` | instruction and diff review; `make generated-artifacts` | static quality-gate bypasses remain separate checklist work |
| `026f84c3` | Thread-safe single-use WS tickets; client socket cleanup; server realtime-channel cleanup | red verification on five lifecycle regressions; `python3 -m unittest tests.test_ws_room_session tests.test_ws_room_client tests.test_ws_endpoint tests.test_room_websocket tests.test_web_transport_package` (95 tests); `python3 scripts/check_test_quality.py --base HEAD`; `git diff --check` | full-suite and browser verification remain completion-gate work |
| `59aa3cc2` | HTTPS-or-loopback remote bridge policy in execution and diagnostics; secure examples | red verification for adapter request and diagnostic Bearer probe; 621 focused/integration tests; `python3 scripts/check_test_quality.py --base HEAD`; `make generated-artifacts-check`; `git diff --check` | bundled bridge itself remains loopback HTTP and requires SSH forwarding or an HTTPS terminator for remote use |
| `4d8557de` | Preserve valid attention jobs and leases when bounded session-reference scanning truncates | red verification showed `leased` work becoming `cancelled`; 232 attention/repository/realtime tests (40 PostgreSQL-environment skips); `python3 scripts/check_test_quality.py --base HEAD`; `git diff --check` | mandatory live PostgreSQL contract run remains completion-gate work |
| `8b248baa` | Execute valid deterministic persona probability decorators | red verification reproduced `NameError`; 60 persona rendering/artifact/finalization tests; `python3 scripts/check_test_quality.py --base HEAD`; `git diff --check` | full suite remains completion-gate work |
| `ee21c513` | Stop and unregister provider-backed participants by canonical Agent Session identity rather than mutable role | red verification showed both provider effects skipped after a role change; 146 kick/realtime/native-E2E tests; `python3 scripts/check_test_quality.py --base HEAD`; `git diff --check` | full suite remains completion-gate work |
| `072678f4` | Atomic participant/session/attention/event creation for provider Agent Sessions; post-commit registry publication | red event-write fault injection left no durable or in-memory partial state and retry succeeded; 217 provider-session/agent-create/realtime/native-E2E/repository tests (38 PostgreSQL-environment skips); `python3 scripts/check_test_quality.py --base HEAD`; `git diff --check` | mandatory live PostgreSQL contract run remains completion-gate work |
| `d23b5323` | Reject SQLite transactions that attempt to restore canonical state after room deletion | red verification showed a stale transaction recreating participant, session, and event rows after deletion; 190 deletion/repository/realtime tests; `python3 scripts/check_test_quality.py --base HEAD`; `git diff --check` | full suite remains completion-gate work |
| `ce742cfe` | Atomically commit SQLite participant/room lifecycle state, related session detachment, and lifecycle events | real SQLite trigger failures reproduced split durable state before the fix; 152 repository/lifecycle tests (38 PostgreSQL-environment skips); `python3 scripts/check_test_quality.py --base HEAD`; `make generated-artifacts-check`; `git diff --check` | mandatory live PostgreSQL contract run remains completion-gate work |
| `d94fec0c` | Enforce the actual idle-room SSE heartbeat cadence while preserving standalone frame-generator compatibility | red HTTP timing verification measured a 0.007-second heartbeat instead of the configured one-second cadence; 128 SSE/Agent Session/room-route tests plus a 591-test expansion with one corrected stale secure-endpoint fixture; `python3 scripts/check_test_quality.py --base HEAD`; `make generated-artifacts-check`; `git diff --check` | full suite remains completion-gate work |
| `3ae54e0d` | Apply side-chat retention after room scoping using a bounded newest-first scan | red file-backed verification showed room-a history disappearing behind room-b's newer rows; 45 side-chat/HTTP/frontend/deletion tests; `python3 scripts/check_test_quality.py --base HEAD`; `make generated-artifacts-check`; `git diff --check` | side chat remains an explicitly separate JSONL feature, not canonical room events |
| `32e237a7` | Match `@all` through one token-boundary rule shared by routing, continuous-floor, and attention policies | red policy verification showed `user@allow.example` broadcasting to all providers and bypassing floor eligibility; 56 routing/floor/attention tests; `python3 scripts/check_test_quality.py --base HEAD`; `make generated-artifacts-check`; `git diff --check` | provider-specific mention aliases retain their existing matching rules |
| `7112a81b` | Parse persisted resident `fast_mode` and `stream_thinking` values with the existing strict boolean parser | red JSON-load verification showed the string `"false"` enabling fast mode; 165 resident-runner tests; `python3 scripts/check_test_quality.py --base HEAD`; `make generated-artifacts-check`; `git diff --check` | CLI argparse booleans remain native booleans and intentionally keep direct conversion |
| `78afc805` | Serialize stable-entry KV publication and discard retries superseded by a newer tunnel URL | red controlled wrangler ordering published `new` then overwrote it with `old`; corrected ordering ends on the latest URL; 19 stable-entry/tunnel/package tests; `python3 scripts/check_test_quality.py --base HEAD`; `make generated-artifacts-check`; `git diff --check` | publication remains asynchronous and best-effort when wrangler itself never succeeds |
| `24e63d85` | Gate Grok ACP RoomPortal outbox staging on selection of a concrete `allow_once` permission option | red real-outbox verification showed denied content persisted before the fix; 74 Grok ACP/RoomPortal/bridge/resident tests; `python3 scripts/check_test_quality.py --base HEAD`; `make generated-artifacts-check`; `git diff --check` | provider permission schemas without an allow-once option now reject the write rather than guessing |
| `0680846e` | Clear Codex output targets before every adapter, resumed-adapter, resident, and streaming-resident call | red repeated-turn verification returned the prior reply through all three non-streaming paths; a controlled streaming mutation did the same; 294 adapter/resident/continuity tests; `python3 scripts/check_test_quality.py --base HEAD`; `make generated-artifacts-check`; `git diff --check` | Codex stdout remains the compatibility fallback only when this call did not create its output file |
| `cdfe18cd` | Correlate OpenCode SSE activity, assistant messages, idle completion, and message-history fallback to a generated request message ID | red delayed-event verification failed before prompt submission because no correlation ID existed; corrected path ignored the prior turn and returned only the current parent-linked reply; 234 OpenCode/provider/room-runtime tests; `python3 scripts/check_test_quality.py --base HEAD`; `make generated-artifacts-check`; `git diff --check` | the real OpenCode provider was not launched, per this plan's provider-launch non-goal; protocol fields were verified against installed v1.17.18 and its official schema |
| `005d074d` | Invalidate POSIX PTY and Windows ConPTY sessions after a turn timeout so late output cannot cross into the next turn | red real-PTY and controlled-ConPTY verification returned the first turn's late output as the second reply before the fix; 180 actual PTY/transcript/bridge/provider tests; `python3 scripts/check_test_quality.py --base HEAD`; `make generated-artifacts-check`; `git diff --check` | timed-out terminal sessions intentionally restart and lose in-process conversational state rather than risk cross-turn output |
| `6f82f3e0` | Drain resident Codex and Grok stderr concurrently with stdout while preserving complete diagnostics for existing authentication classification | red real-subprocess verification timed out after each child filled stderr before writing its streamed reply; corrected paths returned in 0.3 seconds and retained early authentication markers across large diagnostics; 216 resident/stream/runner/continuity tests; `python3 scripts/check_test_quality.py --base HEAD`; `make generated-artifacts-check`; `git diff --check` | diagnostic retention intentionally remains equivalent to the previous full stderr read; this change removes the pipe deadlock without changing error classification |
| `7f77c2f9` | Serialize same-session Agent Bridge creation and registration under a ref-counted per-session launch owner | red concurrent start verification created two child processes and leaked the overwritten one; corrected concurrent callers share one handle while unrelated sessions remain independent; 217 bridge/lifecycle/realtime/native-E2E/package tests; `python3 scripts/check_test_quality.py --base HEAD`; `make generated-artifacts-check`; `git diff --check` | direct manager shutdown concurrent with an in-progress start is outside the application lifecycle, whose controller lock closes only after active commands finish |
| `745f0ed5` | Remove left and kicked participants from incremental and snapshot-backed canonical browser state | red browser verification showed a kicked participant disappear immediately and return after reload; corrected path stayed absent across both boundaries; 186 frontend unit tests; production-build Playwright kick workflow; `make generated-artifacts-check`; `git diff --check` | human invite admission still lacked the canonical membership event needed for an already-open browser to discover the initial join |
| `395b841a` | Publish one canonical participant-join event with invite admission and preserve idempotency across workflow retry | red durable-state test found zero join events and the real browser failed to show the admitted guest without reload; corrected path recorded one event across retry and refreshed the open roster immediately; 63 focused Python tests plus 13 subtests; 186 frontend tests; production-build Playwright join/kick/reload workflow; `python3 scripts/check_test_quality.py --base HEAD`; `make generated-artifacts-check`; `git diff --check` | mandatory live PostgreSQL contract run remains completion-gate work |
| `eb06cb3f` | Expire a stale persisted guest bearer during startup and expose a connection-independent exit from the locked guest surface | red hook and production-browser verification left the stale bearer stored and exposed no expiry exit before the fix; corrected startup removed the bearer, rendered expiry state, and returned to the ordinary root; 187 frontend tests; 8 production-build canonical Playwright scenarios; `make generated-artifacts-check`; `git diff --check` | an expired guest cannot authenticate a final participant-leave command, so expiry exit intentionally clears the local surface without pretending that server-side leave succeeded |
| `96e7d69d` | Give polling and Records detail requests latest-generation ownership so superseded responses cannot replace current state | three deferred-response regressions failed before the fix: prior fetcher replacement, overlapping poll refresh, and Records A-to-B selection; corrected paths published only the current response; 190 frontend tests; 8 production-build canonical Playwright scenarios; `make generated-artifacts-check`; `git diff --check` | Records is not currently exposed by the lobby-only channel list; that separate no-op navigation path remains in the later audited-control checklist item |
| `a8be3386` | Restore complete last-confirmed room settings after optimistic canonical save failure, including queued-write rollback ownership | red routing and appearance failures retained unsaved values; a controlled queued-write mutation restored the initial value instead of the latest successful write; corrected paths restored the latest server-confirmed snapshot; 193 frontend tests; 8 production-build canonical Playwright scenarios; `python3 scripts/check_test_quality.py --base HEAD`; `make generated-artifacts-check`; `git diff --check` | the controller has no server revision field for ordering an external writer against a local in-flight write; local serialized writes and current canonical responses are covered |
| `ad430a9e` | Scope lobby text and attachment drafts plus side-chat and thread drafts by room and context across server switches | red component regressions showed room A text in room B; production-browser verification then exposed side-chat draft loss when the panel unmounted on room switch; corrected ownership preserved A, isolated B, and restored A through the actual UI; 195 frontend tests; 9 production-build canonical Playwright scenarios; `python3 scripts/check_test_quality.py --base HEAD`; `make generated-artifacts-check`; `git diff --check` | unsent drafts intentionally remain in-memory browser state and do not survive a full page reload |
| `1ea53091` | Use the current canonical user profile as the sender identity for ordinary and room-invite friend DMs | red component verification showed the hardcoded historical sender name; production-browser verification saved a new profile and observed that name at the DM HTTP boundary; 196 frontend tests; 10 production-build canonical Playwright scenarios; `python3 scripts/check_test_quality.py --base HEAD`; `make generated-artifacts-check`; `git diff --check` | the production browser fixture does not launch a real Agent Session, so delivery was held at the canonical DM HTTP boundary rather than claiming provider delivery |
| `c9fcb692` | Leave the exact successful voice-channel connection when its view changes or unmounts; surface heartbeat failures | controlled browser mutation left one durable server presence after navigating back to `#general`; restored cleanup removed it; 196 frontend tests; 11 production-build canonical Playwright scenarios; `python3 scripts/check_test_quality.py --base HEAD`; `make generated-artifacts-check`; `git diff --check` | voice channels currently expose governed presence and heartbeat only; audio transport remains intentionally deferred |
| `280b1078` | Keep the confirmed profile and composer identity until save succeeds; preserve the failed draft and editor for retry | red production-browser verification showed a failed save closing the editor and displaying the rejected optimistic name; corrected UI retained the server-confirmed identity while the durable profile stayed unchanged; 196 frontend tests; 12 production-build canonical Playwright scenarios; `python3 scripts/check_test_quality.py --base HEAD`; `make generated-artifacts-check`; `git diff --check` | quick status/audio changes now wait for server confirmation instead of presenting an unsaved optimistic state |
| `cd22769d` | Refresh an open Agent Session detail editor when its canonical model, reasoning, speed, variant, or permission changes | red rendered-GUI regression kept the prior model after a same-session canonical update; corrected panel displayed all four changed controls; 197 frontend tests; production build; 12 canonical Playwright scenarios; `python3 scripts/check_test_quality.py --base HEAD`; `make generated-artifacts-check`; `git diff --check` | the browser fixture exposes only its fake provider, so the cross-provider runtime-profile update was verified at the rendered component boundary rather than by launching or configuring a real provider |
| `d090346b` | Clear an unsaved DeepSeek credential whenever the Agent Session creation modal closes or leaves that provider | red rendered-GUI regression reopened the modal with the rejected secret still present; corrected UI reopened with an empty password field; 198 frontend tests; production build; 12 canonical Playwright scenarios; `python3 scripts/check_test_quality.py --base HEAD`; `make generated-artifacts-check`; `git diff --check` | Homebrew Node became unusable during verification; repository tests passed with the bundled Node runtime and all canonical Playwright scenarios passed with Cursor's Node 22 runtime without changing the repository or system |
| `46532560` | Surface failed DeepSeek credential deletion and session-only moderation without closing the dialog or leaking rejected promises | both rendered-GUI regressions emitted unhandled rejections and left no retry feedback before the fix; corrected dialogs retained confirmed state, showed the failure, and re-enabled the action; 200 frontend tests; production build; 12 canonical Playwright scenarios; `python3 scripts/check_test_quality.py --base HEAD`; `make generated-artifacts-check`; `git diff --check` | secure-store and moderation failure ownership was verified at the rendered component boundary with rejected API callbacks; no real provider was launched |
| `751dfc62` | Remove inactive room controls and disable thought-visibility input when its owner callback is absent | four hidden legacy channel choices and an enabled no-op thought checkbox failed rendered-component regressions before the fix; 202 frontend tests; production build; 12 canonical Playwright scenarios | the first browser rerun hit one transient socket disconnect; the targeted scenario and subsequent full run passed |
| `7b65d0a1` | Audit every Python test body; harden changed-test selection and oracle detection; replace production-source tests with real Vitest imports; add one complete verification command and four critical canaries; increase the GUI connection backlog after a real browser asset-reset failure | 19 adversarial gate self-tests; changed-test gate passed for 9 files; final committed-state run passed 3,988 Python tests with 79 PostgreSQL-environment skips; 207 frontend tests; production build; 12 canonical Playwright scenarios passed twice after the backlog correction; four mutation canaries passed; generated artifacts and `git diff --check` passed after regeneration; isolated PostgreSQL 17.10 verification then passed all 108 mandatory contracts without skips | The temporary PostgreSQL cluster and `.[postgres]` Python environment were removed after verification; no persistent local database configuration was introduced |

## Resume Rule

After context compaction or task handoff, do not reconstruct progress from
memory. Read the latest committed version of this file, confirm `git status`,
inspect the commits listed in the progress log, and continue from the first
unchecked item. If code and this ledger disagree, treat the code and executable
tests as evidence and reconcile the ledger before doing more work.
