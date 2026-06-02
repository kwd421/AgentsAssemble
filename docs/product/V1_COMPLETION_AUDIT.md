# AgentsAssemble v1 Completion Audit

This is a requirement-by-requirement completion audit for the v1 direction. It
records concrete evidence (commands and results) from the current worktree, a
pass/weak/operator-verified status per requirement, and the remaining known
limits. It is evidence, not a feature wishlist.

- Audit date: 2026-05-31
- Branch: `codex/risuai-character-personas`
- Relevant commits: `70417c2` (default React route flip), `874e6af` (React
  console polish), plus this audit doc.
- Status legend:
  - `pass`: proven by a current automated test or reproducible local command.
  - `operator-verified`: correct in code/tests, but the final check (browser
    rendering, real provider login) is human/operator-confirmed and cannot be
    asserted headlessly here.

Run the broad proof set with `python3 -m agentsassemble.cli release-health run`
(7/7 passed on the audit date; `unittest_gui_and_live_agent_smoke` ~167s).

## Requirement Matrix

| # | Requirement | Status | Primary evidence |
| --- | --- | --- | --- |
| 1 | React is the default operator surface; legacy fallback routes are retired | pass + operator-verified | route curl + tests below; browser console errors operator/Codex-verified |
| 2 | UI polish is clean, not intimidating, no external font/network dependency | pass | `index.css` external-ref grep + `npm run build` |
| 3 | Scheduler fairness prevents first-speaker anchoring / unfair dominance | pass | `room-benchmark` off-vs-on numbers + fairness tests |
| 4 | Agent-owned room model; honest persistent-vs-stateless labels | pass | roster-truth/agent-label/live-agent tests + docs test |
| 5 | File/image chat: upload/download, thumbnails, larger preview, safe metadata | pass | attachment round-trip test + `LobbyAttachments.tsx` |
| 6 | RisuAI persona mode: character style allowed, work artifacts professional, no raw-lore leakage | pass | persona artifact-contract + persona-card tests |
| 7 | Low-latency/event benchmark surfaces with reproducible metrics | pass | `room-benchmark` numeric + release-health projection |
| 8 | `uv.lock` / generated debris documented and not committed | pass | `git check-ignore` + `git ls-files` |
| 9 | Docs reflect shipped behavior and known limits | pass | `test_docs_architecture` + updated flip docs + this audit |
| 10 | Verification/commit/push discipline | pass | `release-health run` 7/7 + clean `git status` |

## Evidence

### 1. React default surface, retired legacy routes

```text
$ python3 -m agentsassemble.cli gui --port 8902 ...   # then curl:
/        -> 200  react (id="root")
/app/    -> 200  react (/app/assets/ refs)
/legacy/ -> 404  retired legacy namespace
/static/base.css -> 404
/app/assets/index-DHpFvw0x.js -> 200
```

- `agentsassemble/gui.py` `do_GET` `/` serves the React index when
  `frontend_dist_status(react_app_root).static_available`, else returns the
  React build-required response; legacy static routes are no longer public
  fallback assets.
- Tests: `tests/test_gui_server.py::GuiServerTests::test_root_and_app_serve_react_when_build_available`,
  `::test_root_reports_missing_react_build_without_serving_legacy_console`,
  `::test_react_app_preview_route_reports_missing_dist_without_crashing`.
- `frontend-info` reports `is_default_entry_point: true`.
- Independent check: Codex verified the current-code server on port 8899 — `/`
  and `/app/` serve React root/assets, retired static routes do not serve the
  old console, and browser console errors were 0.

### 2. UI polish, no external font/network dependency

```text
$ grep -nE "@import|https?:|googleapis|gstatic|fonts\.|url\(" frontend/src/index.css
1:@import "tailwindcss";        # build-time directive only; no runtime fetch
$ cd frontend && npm run build  # PASS
```

- The only `@import` is the build-time Tailwind directive; there is no Google
  Fonts / gstatic / external `url()` reference. The `Inter` body font was
  replaced with a local Korean-friendly `--font-body` stack (Pretendard → Apple
  SD Gothic → system), keeping local-first/offline behavior.
- Refinements (`874e6af`): local type stack, calmer grid/scanline, CSS-only
  entrance reveal (`prefers-reduced-motion` guarded), micro-interactions, and an
  accessible `:focus-visible` ring. Component `.tsx` source and backend/API were
  not touched. Tests: `tests/test_static_ui_assets.py` (44 pass).

### 3. Scheduler fairness / no first-speaker anchoring

```text
$ python3 -m agentsassemble.cli live-agent room-benchmark --json
scheduler OFF: first_speaker_share=0.668  imbalance_ratio=8.15  (agent-0 = 334/500)
scheduler ON : first_speaker_share=0.20   imbalance_ratio=1.0   (100/100/100/100/100)
```

- Off-vs-on is the before/after: the fairness scheduler drops first-speaker
  anchor share from 0.668 to 0.20 (1/N for 5 agents) and imbalance from 8.15 to
  1.0. Selection logic: `agentsassemble/live_agent_flow.py`
  `_next_fair_flow_speaker` (least-count + least-recent LRU tie-break + min-gap),
  used by `agentsassemble/live_agent_runner.py`.
- Tests: `tests/test_live_agent_flow.py`, `tests/test_room_event_benchmark.py`
  (26 pass), plus runner fairness tests in `tests/test_live_agent_runner.py`.

### 4. Agent-owned room model + honest persistent-vs-stateless labels

- Derived contract labels `join_semantics` and `context_durability` live in
  `agentsassemble/live_agents.py` and `agentsassemble/live_agent_context.py`;
  the React surface humanizes them in `frontend/src/lib/agentLabels.ts`
  (`stateless`, `process-lifetime`, provider-managed resume, self-service, etc.).
  `sandbox_enforcement` is reported alongside.
- Labels are re-derived from the meeting record and never trusted from
  caller-supplied presence; invite (`join-brief`), self-service, MCP participant,
  and provider session paths carry the same honest contract.
- Tests: `tests/test_frontend_roster_truth.py`,
  `tests/test_frontend_agent_labels.py`, `tests/test_live_agents.py` (35 pass);
  `tests/test_docs_architecture.py::...::test_operating_model_records_room_first_agent_owned_context`.

### 5. File/image chat round-trip

```text
$ python3 -m unittest tests.test_gui_server.GuiServerTests.test_attachment_upload_sanitizes_and_downloads_image
OK (1 test)
```

- `agentsassemble/attachments.py` stores uploads under
  `<output-root>/attachments/<id>/`, returns safe metadata only (`id`,
  `filename`, `content_type`, `size`, `is_image`, view/download URLs), guards
  path traversal, and keeps raw bytes/base64/absolute paths out of events.
- `frontend/src/views/components/LobbyAttachments.tsx` renders image thumbnails
  (`is_image`), a click-to-view larger preview modal (`크게 보기`, focus trap,
  Esc, download), and a download link for every attachment.

### 6. RisuAI persona mode: style allowed, artifacts professional, no leakage

```text
$ python3 -m unittest tests.test_persona_artifact_contract tests.test_persona_cards
OK (48 tests)
```

- `tests/test_persona_artifact_contract.py::...::test_scan_persona_artifact_text_reports_safe_codes_without_raw_card_text`
  asserts the safe report contains the `raw_card_text` violation code but does
  **not** contain the raw markers `RAW_LORE_SECRET_MARKER` /
  `RAW_CARD_DESCRIPTION_MARKER` — i.e., violation codes/counts only, no raw lore.
- Per `docs/product/OPERATING_MODEL.md`, persona context feeds only Play Mode
  `flow` decisions; Work Mode speech uses the reviewed `speech_style` capsule
  (`work_speech_only`), never raw lore/scenario/NSFW body. A stateful persona
  resident does not answer official Work Mode turns from the same flow loop.

### 7. Benchmark surfaces

```text
$ python3 -m agentsassemble.cli live-agent room-benchmark --json
live_append_ms p99 = 0.38 ms
live_read_after_cursor_ms p99 = 0.93 ms
+ flow_scheduler_comparison (off vs on, see #3)
```

- `agentsassemble/room_event_benchmark.py` reports append/read/tail latency,
  speaking-distribution imbalance, and first-speaker anchor share. A safe
  `benchmark_summary` is lifted into `release-health run --check
  room_event_benchmark`. Numbers are same-machine regression tripwires, not SLAs.

### 8. Stray / generated debris

```text
$ git check-ignore uv.lock frontend/dist
uv.lock
frontend/dist
$ git ls-files --error-unmatch uv.lock   -> not tracked
$ git ls-files frontend/dist | wc -l     -> 0
```

- `uv.lock` is a local artifact from a one-off `uv` run. `pyproject.toml` has no
  `[tool.uv]` (deps = `mcp` only) and no doc uses uv, so it is not part of the
  setuptools workflow. It is gitignored and untracked, so it cannot be
  accidentally committed; it is intentionally left in place as local
  developer state (not deleted). `frontend/dist` is likewise gitignored.

### 9. Docs

- The default-route flip is documented in `README.md`, `frontend/README.md`,
  `docs/product/OPERATING_MODEL.md`, `docs/product/legacy-react-parity-matrix.md`,
  `docs/product/V0_1_RELEASE_CHECKLIST.md`, and `docs/live-agent-ops.md`.
- Tests: `tests/test_docs_architecture.py`,
  `tests/test_legacy_react_parity_inventory.py` (cross-reference + label + route
  evidence).

### 10. Verification / commit / push discipline

```text
$ python3 -m agentsassemble.cli release-health run
summary: passed 7, failed 0, skipped 0, total 7
  node_check_static, unittest_static_ui_assets, unittest_docs_architecture,
  unittest_mcp_server, unittest_gui_and_live_agent_smoke (~167s),
  compileall_package, git_diff_check
$ git status --short   -> clean
```

- Coherent slices committed and pushed to
  `origin/codex/risuai-character-personas`: `70417c2`, `874e6af`. `git diff
  --check` is clean.

## Remaining Known Limits

- Browser-rendered visual parity and runtime console behavior of the four React
  surfaces (`로비`, `실황`, `작전판`, `아카이브`) are operator-verified, not
  asserted headlessly here. Codex independently confirmed 0 browser console
  errors on the current code (port 8899).
- A fresh clone returns a React-build-required response at `/` until
  `npm --prefix frontend run build` produces `frontend/dist` (gitignored by
  design); after a build, `/` serves React.
- Benchmark numbers are same-machine regression tripwires, not portable SLAs.
- Real provider CLI execution (Codex/Kiro/Cursor/Grok/Claude) still requires
  explicit operator approval and provider-specific smokes; this audit covers the
  local-first control plane and fake/self-service proofs, not paid provider runs.

## Audit Conclusion

All ten v1 requirements have current per-requirement evidence (automated tests
and/or reproducible commands) in this worktree. No code gap was exposed by the
audit; the deliverable is this tracked evidence record. Outstanding items are
the operator-verified browser checks and provider-approval boundaries listed
above, which are inherent product limits rather than missing v1 work.
