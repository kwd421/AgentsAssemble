# Plan: codebase map — visual finish + unified search

Executor-facing plan. Written so an agent with no prior context can pick this
up. Every task is independent unless marked otherwise; do them in order.

## Non-negotiable rules

- The page is generated. **Never edit `docs/product/CODEBASE_MAP.html` or
  `.json` by hand.** Edit `scripts/generate_codebase_map.py`, then run
  `python3 scripts/generate_codebase_map.py` to regenerate both.
- After every change run `python3 -m unittest tests.test_codebase_map`
  (13 tests). One of them executes the page's own JS in jsdom
  (`CodebaseMapRenderSmokeTests`) — if any section renders empty or a JS error
  fires, it fails. Trust it over screenshots.
- The HTML must stay **fully self-contained**: no external scripts, fonts,
  images, or fetches. A test enforces this. Emoji/inline SVG data URIs are fine.
- The Python file embeds JS/CSS in a plain string template. **Backslashes in JS
  regexes must be doubled** (`/\\s+/` not `/\s+/`) or Python raises
  SyntaxWarning/SyntaxError. Check with:
  `python3 -W error::SyntaxWarning -c "compile(open('scripts/generate_codebase_map.py').read(),'x','exec')"`
- If a new/edited file under `docs/` mentions module names like
  `agentsassemble.live_agents`, the generated `docs/product/SHIM_RETIREMENT.md`
  goes stale and `tests/test_package_architecture_gate.py` fails. Fix by
  running `python3 scripts/check_package_architecture.py --write-shim-report`
  and committing the refreshed report.
- Before every commit: `git status --short | grep -iE "wrangler|secret|account.json|\.env"`
  must be empty. Do not commit `.claude/`, `output/`, `.playwright-cli/`,
  `.superpowers/`.
- One commit per task. Do not restructure code the task does not name.

## Current state (already committed, do not redo)

- `b0d914a1` hero (count-up figure, aurora, grain, fact pills) + edge light
  pulse on package selection.
- `77aa2e7b` OKLCH token design system (`@layer`, light/dark), package cards
  with docstring/magnitude bar/health tone, View Transitions on tabs, legend as
  anchored popover, on-canvas zoom cluster (⌘/Ctrl+wheel zoom, drag pan).
- `d684eabc` map generator itself: Sugiyama layout, Health tab, UML tab,
  jsdom smoke test. Serve for preview with launch config `CodebaseMap`
  (port 8781) or just `open docs/product/CODEBASE_MAP.html`.

Design language now in force: OKLCH tokens in `:root` (`--primary`, `--ok`,
`--warn`, `--danger`, `--violet`), colour means **health only**, size/bar means
**magnitude**, motion tokens `--dur-*`/`--ease*`, everything respects
`prefers-reduced-motion`.

## Task V-1 — de-duplicate the Architecture tab (small)

The hero now states lines/modules/files, but the old `.statgrid` right under it
repeats the same four numbers. In the Architecture section markup keep the
statgrid **only** for figures the hero does not carry (map packages, import
cycles, package edges) or remove it entirely — pick whichever reads better in a
screenshot. Update `CodebaseMapRenderSmokeTests` if you remove the `stats`
container (it asserts `stats` renders non-empty; change the assertion to
whatever container remains).

Verify: regenerate, run tests, screenshot Architecture top in dark and light
(`colorScheme` both) — no duplicated numbers, no layout gap where the grid was.

## Task V-2 — light-mode pass on the hero (small)

The aurora opacities (`.aurora`, `.aurora i:nth-child(n)`) were tuned on dark.
On light the blobs may look washed or invisible. Add a
`@media (prefers-color-scheme: light)` override adjusting `.aurora` opacity
(likely lower, ~0.35) and, if the gradient text lacks contrast, deepen the
`.hero-figure` gradient end colour. Do not touch dark values.

Verify: screenshots of the hero in both schemes; the count must stay legible.

## Task V-3 — stagger reveals (small, optional polish)

Package cards in the graph and finding cards in Health appear all at once.
Findings already reveal on scroll via `animation-timeline: view()`. Add a
load-time stagger for `.hero-facts span` and the flowstrip rows: an
`animation-delay: calc(var(--i) * 40ms)` pattern with `--i` set inline per
element by the JS that renders them. Guard with
`@media (prefers-reduced-motion: no-preference)`.

## Task S-1 — unified search (the main remaining feature)

One search across everything, opened with `/` and a header button, replacing
nothing (the Module Explorer filter and the Connections jump stay).

Data: build the index once in JS from what is already in `D`:

- `modules` (name, doc, pkg) → action `showModule(name)`
- `feFiles` (path, doc, group) → action `showFrontend(path)`
- `D.packages` (id, doc) → action `switchView("graph"); selectPackage(id, false)`
- `D.class_graph.nodes` (name, module) → action
  `switchView("classes")` then reuse the existing per-node click logic — factor
  the body of `g.onclick` in `drawClasses` into a named function
  `focusClass(id)` and call it.

UI: a dialog-like overlay (`<div class="cmdk" hidden>` near the drawer):
input on top, result list under it, max ~12 rows, each row shows kind badge
(module / file / package / class), name, and one-line doc. Style with existing
tokens (surface, border, shadow-3, backdrop-filter) — it should look like the
legend popover, wider.

Behaviour:

- `/` (when not typing in an input) and a header button open it, `Escape`
  closes, arrows move selection, Enter runs the action and closes.
- Scoring: case-insensitive; rank exact substring in name > substring in
  doc; shorter names first. A simple two-pass filter is fine — no fuzzy
  library, nothing external.
- Keep it dumb and fast: precompute a lowercase haystack string per entry.

Verify: extend the jsdom smoke test — dispatch a `/` keydown, assert the
overlay opens; type `providers`, assert result rows > 0; Enter, assert the
drawer opened (`#drawer.open`). Screenshot open state in dark mode.

## Task S-2 — keyboard hint line (tiny, after S-1)

Add `/ search` to the zoomhint or a one-line footer hint so the shortcut is
discoverable. Regenerate, tests.

## Explicitly out of scope (do not start)

- **xyflow / React / single-file bundling (path C).** Recorded decision: only
  worth it if we accept relaxing the byte-identical determinism test
  (`test_committed_codebase_map_matches_source_tree`) to a data-only
  comparison, because a JS bundler breaks byte-stability across versions. Needs
  the user's sign-off first.
- Three.js: rejected — 2D layered diagram gains nothing from WebGL and the
  self-containment test forbids external scripts (inlining ~600KB is the cost).
- Tree tab: deliberately removed (Module Explorer covers it); do not
  reintroduce.

## Known environment quirks

- The in-app browser pane sometimes renders 0×0 or times out on screenshots;
  resize the window (`resize_window`) and retry, or trust the jsdom smoke test.
- `read_console_messages` misses load-time JS errors; the jsdom test catches
  them.
- Port 8781 static server config lives in `.claude/launch.json`
  (`CodebaseMap`), serving `docs/product/`.
