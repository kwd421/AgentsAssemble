# Legacy Static Audit — 2026-07-28

Status: recorded compatibility risk; repair is outside the current hardening
workstream

Baseline: `539126fc`

## Scope and Use

This report records the complete static review of the tracked legacy
implementation:

- 181 files
- 47,149 lines
- `legacy/live_agent/runtime/`: 25 files, 13,896 lines
- `legacy/live_agent/cli/`: 26 files, 7,983 lines
- `legacy/meeting/`: 49 files, 9,642 lines
- other legacy code: 81 files, 15,628 lines

Every tracked file in that scope was read from line 1 through EOF. No legacy
provider, server, test suite, or destructive action was executed as part of the
audit. Findings are static control-flow and ownership findings unless an
existing test already demonstrates the behavior.

This document is evidence, not authorization to repair legacy code. The
canonical product must not add dependencies on these paths. A legacy repair
requires a separate request that identifies the still-supported caller and its
verification path.

## Exposure Qualification

The normal public tunnel does not expose the legacy mutation routes. The
default GUI bind is loopback. Several authorization findings become remotely
reachable only with the explicit unsafe non-loopback control-plane option.
They remain relevant to trusted local callers and to any deployment that uses
that unsafe option, but they are not described here as default public-internet
exposure.

## Confirmed High-Risk Findings

### Process and session ownership

1. With unsafe non-loopback control-plane exposure, an unauthenticated caller
   can register externally supplied relaunch PID/argv/cwd/host data and later
   invoke legacy stop/resume. The implementation can signal an arbitrary
   reused PID or execute stored argv with inherited server environment.
   Relevant owners:
   `legacy/live_agent/http/presence.py`,
   `legacy/live_agent/state.py`,
   `legacy/live_agent/http/self_managed.py`, and
   `legacy/live_agent/runtime/self_managed.py`.

2. Session-run reconciliation releases its lock before the recovery callback.
   The background monitor and `retry-now` can therefore execute the same
   recovering run concurrently, including provider/process/finalization side
   effects.

3. Finalization snapshots official events and then writes transcript, memory,
   and state without sharing the official-turn lock. An official reply arriving
   after the snapshot can be omitted while the meeting still becomes complete.

4. Legacy settings mutation trusts the client-supplied live-agent config path,
   writes non-atomically, has no closed permission-option validation, and
   interprets a JSON string `"false"` as true.

5. Room delete performs process/config destruction before all meeting and
   binding validation succeeds. It does not revoke the participant session as
   expel does. The compatibility HTTP delete route has no moderator gate.

6. The orphan-process sweeper matches a PID and command, waits, and later
   signals only the PID number without revalidating process identity. PID reuse
   can terminate an unrelated process.

7. A generated join brief can repeat credential-bearing server URLs while
   declaring `contains_secrets: false`.

8. Session-run persistence failure can leave a launched provider or process
   without matching durable run state.

9. Group/session stop projection can mark unrelated runs in the same meeting
   or group stopped because the update is not scoped to the affected agent.

### CLI and external entry

10. The join brief advertises `mcp serve ...` as its primary MCP entry, while
    the current parser requires `mcp --legacy-internal serve ...`. The emitted
    command exits with status 2. One test freezes the broken argv shape while a
    different test proves the same unflagged command is rejected.

11. Legacy WS engagement logic and its tests use retired values
    `free/quiet/ordered` instead of current `ambient/continuous/ordered`.
    Direct `--session-token` operation does not recover the room ID and the
    settings request does not authenticate a public-origin read, so it can fall
    back to the wrong local mode.

12. One-shot delegate execution does not treat provider success, room
    publication, and final online heartbeat as one recoverable state change.
    Publication failure can leave the agent working; heartbeat failure after
    publication can cause a retry and duplicate reply.

13. Run-group shutdown does not own its WS resident runner. Interrupt can
    return after bounded thread joins while the daemon WS worker remains able
    to read or reply.

14. `sessions --legacy-internal list` reads its index outside error handling.
    Permission errors, other I/O errors, or invalid UTF-8 escape as a traceback.

15. Claude legacy PTY fast-mode toggling swallows all errors and does not
    validate an acknowledgement, so saved/UI state can disagree with the real
    provider. This is recorded as conditional because the implementation labels
    the toggle best-effort.

16. Discovery writes its multi-file session bundle sequentially. A later write
    failure leaves a partial bundle; no atomic bundle contract is implemented.

### Meeting, record, and artifact integrity

17. Lobby promotion indexes events globally by event ID and does not require
    the promoted event to belong to the target meeting.

18. `Role.id` is not validated before being used in artifact and memory paths.
    A crafted ID can escape the intended role directory.

19. The remote lobby command accepts a raw meeting ID and uses it in an output
    path. Existing absolute or parent-traversal meeting records can redirect
    writes outside the intended meeting directory.

20. Evidence support accepts mere occurrence of a URL in provider output as
    proof that the source supports the claim.

21. Research-phase retry wraps both the provider call and artifact write. A
    local artifact-write failure can invoke the provider again and duplicate
    cost or external effects.

22. If starting a later role session fails, sessions already started for
    earlier roles are not cleaned up.

23. The runner records an artifact as written before the actual filesystem
    write succeeds.

24. Demo meeting execution has no top-level failure cleanup and no durable
    failed-state transition covering all started roles and partially written
    artifacts.

25. A valid JSON meeting record containing a list instead of an object reaches
    callers that assume `.get()` and crashes them.

26. One malformed meeting record can break the complete listing instead of
    isolating that record.

27. Meeting detail reads every artifact and private research file without an
    aggregate size bound.

28. Lifecycle and pending-turn projections use only the latest 200 events and
    can report the wrong state for longer meetings.

29. Live-state writes are non-atomic.

30. Shared-memory heuristics treat any `decision:` substring or question-like
    sentence as a decision/question, including negated or explanatory text.

31. Moderation kick catches resident-expel failure and still returns
    `status: "kicked"`.

### Cursor, observation, and persistence

32. Malformed state JSON is treated as an empty roster and can then be
    overwritten, losing the original state. Unknown heartbeat senders can
    create ghost agents. Process-local locking and fixed temporary paths do not
    protect multi-process writers.

33. Observation health compares against a global latest lobby event and only a
    short tail, so quiet rooms and restarts can be reported as behind or
    unhealthy incorrectly.

34. Speech updates heartbeat/cursor state before all reply validation, and a
    nonexistent source event ID can bypass intended ordering checks.

35. Codex session index writes are non-atomic; duplicate bindings overwrite;
    switching provider can retain an arbitrary prior model.

36. Official-reply serialization is process-local and does not protect
    multi-process execution.

37. The legacy room resident keeps an unbounded seen set, advances its cursor
    before the consumer handles the yielded event, and exits permanently on a
    provider exception.

38. Several discovery/session bundles are multi-file and non-atomic.

39. The blocking MCP tools advance or clear cursor/pending state before the
    corresponding say, official reply, DM, or acknowledgement succeeds.
    Timeout paths may also fast-forward the cursor, and non-finite timeout
    values are not rejected consistently.

40. Migration code silently skips invalid JSON and treats any existing message
    as a reason to skip an entire room import, which can leave partially
    migrated rooms permanently incomplete.

## Existing Tests That Do Not Refute These Findings

The common omissions are concurrent calls, write failure after an external
effect, PID reuse, malformed-but-valid JSON shapes, long histories, restart
boundaries, and actual execution of generated commands. Several tests assert
the exact compatibility payload or the intended call sequence without
executing the consumer that rejects it.

These tests remain legacy compatibility evidence. This report does not direct
their removal. Delete or change them only together with an explicitly retired
legacy contract.

## Freeze Rule

- New canonical code must not import from `agentsassemble.legacy`.
- New GUI or provider behavior must not call legacy HTTP mutation routes.
- Do not repair a legacy item merely to make a current feature depend on it.
- If a legacy caller must remain supported, open a separate bounded change
  naming that caller, the compatibility contract, and the real verification
  path.
- Removing a legacy path requires caller inventory across GUI, CLI, docs,
  release health, and tests.
