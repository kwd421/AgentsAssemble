# Current-Code Hardening Plan — 2026-07-28

Status: active execution ledger

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
- [ ] Resolve bridge-process creation and registration under one ownership
      boundary so duplicate children cannot be spawned.

### D. Browser behavior

- [ ] Remove left and kicked participants from canonical browser state so an
      HTTP roster refresh cannot resurrect ghost members.
- [ ] Expire a stored guest session during startup and provide a usable exit
      from the guest surface.
- [ ] Add generation or cancellation ownership to polling and Records detail
      requests.
- [ ] Roll back or reload the authoritative value after room-setting save
      failure; do not label the failed optimistic value as previously confirmed.
- [ ] Scope lobby attachments/drafts and side-chat drafts to their room.
- [ ] Use the current user profile as the friend-DM sender.
- [ ] Correct the audited stale voice cleanup, profile-save, Agent Session
      detail, retained secret, unhandled action-error, and no-op control paths
      while staying within the same focused components.

### E. Verification system

- [ ] Audit all executable Python test bodies, including support-file contract
      mixins.
- [ ] Treat deletion-only diffs and helper/setup/fixture changes as test
      changes.
- [ ] Reject tautological or implementation-only oracles that currently pass
      the quality gate.
- [ ] Inspect helper- and alias-mediated private patches and mock-only oracles.
- [ ] Add adversarial self-tests for every quality-gate bypass fixed here.
- [ ] Replace the misleading local "full suite" label with one canonical
      verification command that runs Python, PostgreSQL contracts, frontend
      unit tests, frontend build, browser E2E, generated artifacts, and
      `git diff --check`.
- [ ] Add focused mutation canaries for critical authorization, rollback,
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

## Resume Rule

After context compaction or task handoff, do not reconstruct progress from
memory. Read the latest committed version of this file, confirm `git status`,
inspect the commits listed in the progress log, and continue from the first
unchecked item. If code and this ledger disagree, treat the code and executable
tests as evidence and reconcile the ledger before doing more work.
