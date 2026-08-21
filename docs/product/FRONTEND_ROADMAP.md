# Frontend Roadmap

Status: prioritized future work, not current implementation authority

Last updated: 2026-08-21

Read when: selecting the next frontend slice. Start implementation from
`CURRENT_SYSTEM.md`, the owning component/hook, and the closest behavioral
tests. The feature inventory and current verification evidence live in
`FRONTEND_FEATURE_MATRIX.md`.

## Product Goal

Make the supported room client feel trustworthy before making it broader. A
user should be able to identify themselves, enter a room, add an agent, converse,
recover from failure, and understand which actions are local, public, billable,
or destructive without guessing.

## P0 — Fix Misleading Or Blocking Workflows

### Reliable workspace selection

- Make the native picker appear in front of the initiating window.
- Treat cancel as a normal result, not a raw backend failure.
- Time out a lost picker and restore the add-agent form.
- Show a human error with a retry action.
- Verify Grok and OpenCode creation end to end using only the visible form.

Exit evidence: a clean browser/desktop session can select, cancel, retry, create,
and start a workspace-bound agent without HTTP fallback or process cleanup.

### Explicit public-tunnel consent

- Separate “create invite” from “open this room to the internet”.
- Show the exposure method, scope, expiry, and stop control before activation.
- Keep local-only invite generation local by default.
- Confirm that closing public access invalidates or clearly expires the public
  path.

Exit evidence: no public tunnel starts until the user confirms the exact
exposure action, and the UI proves when it is closed.

### Honest feature labelling

- Label mute/headset and voice-channel controls as presence previews until real
  audio exists.
- Label gift/GIF/sticker helpers as local text descriptions.
- Replace raw backend identifiers such as `workspace_picker_failed` with useful
  user-facing recovery copy while retaining structured diagnostics.

## P1 — Complete Core Collaboration

### Real search and message actions

- Implement channel match filtering or next/previous navigation.
- Add empty, loading, error, and cleared-query behavior.
- Make pin, thread-open, and message context actions keyboard accessible.
- Verify pin/unpin persistence and thread reply projection.

Exit evidence: known messages can be found, pinned, and discussed in a thread
after reload without scanning the full transcript.

### Repeatable multi-client release smoke

- Create disposable host and guest identities in isolated data roots.
- Cover read-only and read/write invites, side-chat visibility, room messages,
  settings revisions, reconnect, and leave.
- Add a gated provider smoke for one API agent, one live CLI agent, and exact
  OpenCode free Hy3.
- Record only safe booleans/counts; never store prompts, credentials, recovery
  codes, invite secrets, or provider output in release artifacts.

Exit evidence: one command or documented release procedure proves the primary
host/guest/provider journey at user-visible boundaries.

### Account and recovery completion

- Exercise Google success, cancel, denial, expired state, wrong account, and
  desktop return flow against a non-production directory.
- Exercise recovery on a clean device profile and make the active identity
  visible before destructive account actions.
- Make spinners bounded and always offer cancel/retry.

## P2 — Finish Secondary Surfaces

- Complete attachment upload/render/download and rejection states.
- Complete vote creation, voting, close, and reload behavior.
- Verify provider pause/resume, stop/edit/restart, usage, and diagnostics without
  stale state.
- Complete saved friends, previous-session import, DM exchange, and presence.
- Add mobile/narrow-screen manual and automated coverage.
- Run the primary smoke inside signed macOS and mobile wrappers, not only Chrome.

## Later — Only After The Core Is Trustworthy

- Real voice/media sessions with an explicit permission and transport model.
- Rich GIF, sticker, and gift integrations if they serve the room product.
- Broader themes, animations, and polish that do not obscure state or authority.
- Additional provider-specific controls after the shared lifecycle remains
  consistent across API, CLI, and OpenCode paths.

## Roadmap Hygiene

Every roadmap item needs a named owner, a user-visible exit condition, and a
verification boundary before implementation. Move completed facts into
`FRONTEND_FEATURE_MATRIX.md`; do not leave finished work described only as a
future promise here.
