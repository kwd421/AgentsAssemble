# Test Suite Value Audit - 2026-07-25

## Scope

This audit reviewed the complete Python test inventory after commit `fafc4b11`.
At the time of the audit the suite contained:

- 332 Python test files
- 3,699 Python test methods
- 23 frontend/runtime-oriented Python test files
- 22 Vitest files with 128 frontend behavior tests

The goal was not to reduce the count indiscriminately. The goal was to remove
tests that only made implementation claims look verified while leaving broken
user workflows undetected.

`docs/product/PACKAGE_MAP.md` and its generated codebase-map inputs were not
modified because another agent was updating that area concurrently.

## Value Rule

A test is retained when it can fail because a meaningful contract broke:

- a user-visible workflow,
- persisted state or restart recovery,
- security, permissions, or secret redaction,
- provider process lifetime or protocol behavior,
- room delivery, reconnect, cursor, or idempotency behavior,
- a real frontend interaction or render-state transition.

A test is removed or narrowed when it only checks:

- exact UI copy,
- exact documentation wording,
- a source-code string,
- a constant or numeric value introduced by the same change,
- an export or filename without exercising its consumer,
- formatting of a debug/preview string.

## Removed

Commit `fafc4b11` removed 88 shallow tests:

- `tests/test_static_ui_assets.py`: 50
- `tests/test_frontend_agent_create_flow.py`: 4
- `tests/test_frontend_roster_truth.py`: 3
- `tests/test_static_lobby_contract.py`: 2
- `tests/test_docs_architecture.py`: 21
- `tests/test_gui_launcher_app.py`: 1
- shallow methods removed from mixed frontend files: 5
- shallow methods removed from legacy parity inventory: 2

The same commit changed release health from a Python source-string check to the
actual frontend Vitest command.

This follow-up pass removes:

- the README wording test from `tests/test_agent_session_cli.py`; the retained
  tests execute the CLI and prove forbidden legacy paths are rejected;
- exact Korean button text, DM sentence, preview JSON formatting, and status
  copy assertions from `tests/test_frontend_room_invite_copy.py`.

The invite test still executes the compiled TypeScript and verifies the
security boundary that matters: an external HTTPS invite is copied, while a
missing or loopback invite never falls back to a local preview URL.

## Retained

An AST-assisted scan found 155 tests that read source or persisted files and
also use membership or regex assertions. This count is not a deletion list.
Manual sampling showed that most of these tests exercise real behavior before
inspecting an artifact.

Examples retained:

- provider stderr and token redaction;
- invite and session stores that must not persist raw credentials;
- attachment bytes and media metadata written by the real room path;
- restart recovery and durable cursor behavior;
- process manifests and stop ownership boundaries;
- migration rollback preserving original files;
- public artifact projection excluding private provider data.

These tests may inspect files, but the files are the product output or security
boundary rather than the implementation source.

## Architecture And Compatibility Tests

The suite contains 26 `test_*package.py` files and roughly 150
compatibility/export tests. They are maintenance-heavy, but deleting them in
this pass would be unsafe because they are tied to:

- `compatibility_shims.toml`,
- `SHIM_RETIREMENT.md`,
- package-map generation,
- active module-boundary refactoring.

The meaningful architecture gate in
`tests/test_package_architecture_gate.py` is retained. It detects new flat
modules, dependency-direction violations, new cycles, and unexpected shim
callers.

Recommended later cleanup:

1. Finish the current package-map work.
2. Replace per-symbol compatibility assertions with one manifest-driven shim
   contract.
3. Delete a shim test together with the shim it protects, not earlier.
4. Keep the dependency/cycle gate as the durable architecture test.

## Test Distribution Risk

The suite is still large:

- room/security: about 977 tests;
- provider/runtime: about 318;
- web/control plane: about 455;
- architecture/compatibility: about 198;
- integration/smoke: about 82;
- legacy/other: about 1,639.

The largest risk is not raw count. It is that legacy meeting/process tests make
the suite expensive while the current product depends on canonical room,
Agent Session, browser, and real-provider paths. New work should add tests only
for a demonstrated behavioral risk and should prefer removing obsolete legacy
coverage when its production path is removed.

## Verification

The first cleanup commit was verified with:

- Vitest: 22 files, 128 tests passed
- frontend-related Python behavior tests: 31 passed
- provider tests: 47 passed
- release-health and legacy-inventory tests: 23 passed
- frontend build: passed
- `git diff --check`: passed

The follow-up two-file cleanup is verified separately before its commit.

No claim is made that all 3,699 Python tests were rerun during the concurrent
package-map edit. The complete suite should be run after that user-owned work
settles so generated architecture snapshots are not overwritten or
misattributed.
