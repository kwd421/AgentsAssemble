# Live Room UX Stabilization Plan

## Summary

Stabilize the live room operator experience in small reviewed slices. Each slice
must be verified, committed, and pushed before the next slice starts.

This pass keeps the current vanilla HTML/CSS/JS frontend. It does not introduce
React, Vite, or Tailwind.

## Slices

1. Save this execution plan as the baseline and do not include unrelated dirty
   files in the commit.
2. Checkpoint the existing Kiro frontend polish in the frontend worktree after a
   minimal static verification.
3. Fix natural-language text wrapping so names, decimals, versions, weights, and
   ellipses do not split mid-token, while still allowing long technical strings
   to wrap safely.
4. Reduce live-tab refresh flicker by avoiding full panel replacement for simple
   incoming live events.
5. Add Play Mode meeting presets that enqueue official turn requests without
   starting real provider CLIs.
6. Formalize pending-turn cancellation so meetings can be finalized without fake
   agent replies.
7. Update operating documentation and run final verification.

## Verification

Use the cheapest reliable check after each slice:

- `git diff --check`
- `node --check agentsassemble/static/*.js`
- targeted Python unit tests for live-agent behavior
- browser smoke checks for frontend behavior
- final `python3 -m unittest discover -s tests`
- final `python3 -m compileall -q agentsassemble`

## Boundaries

- Do not commit unclear dirty files such as local CLI state or lockfile debris
  unless a later explicit slice owns them.
- Do not start real Claude, Kiro, Antigravity, or other provider CLIs as part of
  verification.
- Keep Work Mode and Play Mode separated. Play Mode material becomes official
  only through an explicit product path.
