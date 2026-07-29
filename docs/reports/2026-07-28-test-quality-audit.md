# Python Test-Quality Audit — 2026-07-28

Status: current inventory and debt record

Scope: every Python file below `tests/`, including contract mixins whose
filenames do not begin with `test_`

Legacy boundary: findings against `agentsassemble/legacy/` are recorded here;
legacy production code is not repaired by the current hardening workstream.

## Inventory

The final AST inventory for this change contains:

- 351 Python files below `tests/`;
- 3,931 executable `test_*` function bodies;
- 30 of those bodies in the support mixins
  `identity_repository_contract.py` and `room_repository_contract.py`.

The support mixins were read in full. Their tests execute backend-neutral
identity and room-repository contracts against the concrete SQLite and
PostgreSQL suites. They cover durable identity, membership, usage aggregation,
transaction ordering, rollback, command idempotency, room deletion,
visibility, attention leases, and media projection. They are not filename or
symbol-presence tests and remain part of the inventory.

Inventory command:

```text
parse every tests/**/*.py file with ast;
count FunctionDef/AsyncFunctionDef nodes whose names start with test_
```

## Gate Defects Found And Corrected

The previous changed-test gate had six demonstrated blind spots:

1. `--all` selected only `test_*.py` and omitted executable support mixins.
2. Deletion-only changes disappeared from selection.
3. Helper, setup, fixture, and import changes could alter a test without
   selecting its test body.
4. Private patches hidden behind import aliases, target-string aliases,
   `setUp`, or same-class helpers were not found.
5. Mock-only assertions hidden behind helpers, assertion aliases, or
   `call_args`-style observation fields were accepted.
6. Stable tautologies and production-source reads through a path variable were
   accepted.

Each bypass now has an adversarial gate self-test. The source-path case was
first observed failing before the detector was corrected.

The initial exact-Korean-copy detector also produced 98 warnings, most of
which were room-message payloads, terminal-rendering fixtures, or persisted
domain data rather than UI-copy-only tests. It now rejects a test only when its
oracles are limited to a copy-shaped value such as a label, title, placeholder,
or actionable user error. Adversarial coverage proves that Korean protocol
data and a test with a separate behavioral oracle remain accepted.

## Confirmed Shallow Tests Corrected

- Removed two Python tests that read and transpiled production TypeScript only
  to inspect archive and release-health behavior.
- Replaced those checks with Vitest tests that import and execute the real
  `RecordsView` and release-health modules.
- Removed the provider-auth test whose only oracle was one exact Korean login
  paragraph. The retained auth tests exercise classification and the
  provider-specific result.
- Added the missing result oracle to the API-lane validator test.
- Reworked the Grok ACP tests so the real `RoomPortal` publication and
  permission counters are observed; two narrow exceptions remain for capturing
  the ACP JSON-RPC response at its transport seam.
- Reworked the realtime shutdown test to verify a real issued room session
  remains valid after server close.
- Reworked the PostgreSQL constructor test to query a fresh real schema instead
  of asserting only that a migration mock was not called.

PostgreSQL completion evidence was obtained on 2026-07-29 with an isolated
PostgreSQL 17.10 cluster and a disposable Python 3.14.6 virtual environment
installed from `.[postgres]`. The mandatory
`python -m tests.run_postgres_contracts` runner passed all 108 selected
contracts in 3.928 seconds with no skips. The temporary server, database, and
virtual environment were removed after the run.

## Residual Findings

Running the improved full audit with the reviewed exception file reports 410
existing findings:

| Rule | Count | Disposition |
| --- | ---: | --- |
| `private_patch` | 315 | Existing seam debt. Of these, 260 patch the CLI JSON request seam. They often have additional state/result oracles, so the count is not a deletion list. New or changed tests must use a public seam or receive a test-specific reviewed exception. |
| `symbol_only` | 94 | Predominantly compatibility/package contracts retained during the active shim and package-map migration. Replace them with a manifest-driven shim contract and retire them together with their shims. |
| `mock_only` | 1 | `test_session_smoke_recover_killer_uses_non_graceful_signal` covers legacy live-agent smoke code. Recorded only under the legacy boundary. |
| `exact_ui_copy` | 0 | The one confirmed copy-only current test was removed. |
| `source_text` | 0 | The two confirmed production-source tests were replaced. |
| `no_oracle` | 0 | The confirmed missing oracle was corrected. |
| `tautological` | 0 | The gate now rejects stable tautologies. |

These counts do **not** prove that all 3,929 tests are meaningful. They prove
that the enumerated static anti-patterns were applied to every executable
Python test body, and they leave the residual debt explicit instead of
silently treating a green aggregate count as quality.

Full-audit command:

```text
python3 scripts/check_test_quality.py --all
```

The command is expected to remain nonzero until the recorded private-seam,
compatibility, and legacy debt is retired or individually justified. The
changed-test gate is the admission control for new work:

```text
python3 scripts/check_test_quality.py --base <review-base>
```

## Release Verification Boundary

`make test` is now described accurately as the Python suite. It is not the
complete release boundary.

The canonical local command is:

```text
make verify
```

It runs, in order:

1. the changed-test quality gate;
2. generated-artifact checks;
3. the full Python suite;
4. mandatory, no-skip PostgreSQL contracts;
5. frontend unit tests;
6. frontend production build and canonical browser workflows;
7. focused authorization, rollback, room-scope, and response-order canaries;
8. `git diff --check`.

The four canaries exercise observable failure contracts rather than source
strings:

- unauthorized Agent Session mutations return forbidden and create no
  participant;
- a failed invite-session replacement is absent both in memory and after
  repository reopen;
- side-chat retention is applied after room scoping;
- late OpenCode events from the previous turn cannot complete the current
  turn.
