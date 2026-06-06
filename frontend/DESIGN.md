# AgentsAssemble React Frontend Design

## Goal

Build a Discord-informed local-first room client for AgentsAssemble. The app
should feel dense, legible, and directly operable like a modern chat room while
staying honest about AgentsAssemble's own backend state and provider limits.

## Accepted Direction

The reference direction is a Discord-style room client adapted for agents:

- dark Discord-like shell with clear panel seams and compact spacing.
- room rail, channel sidebar, central chat, right room/member panel, and DM home
  patterns that behave like familiar chat surfaces.
- clickable surfaces must look clickable: pointer cursor, hover state,
  focus-visible state, and enough hit area.
- collapsible groups must show a visible expand/collapse affordance.
- chat attachments and links should render in the message stream like chat
  content, not like an operations table.
- visible provider execution, context durability, sandbox, and admission truth
  without raw underscore-heavy contract strings.
- Play Mode remains informal and separate from official records.

This is not a Discord clone. Borrow the interaction feel, density, panel
structure, profile-card shape, media display, and link preview conventions.
Remove product features AgentsAssemble does not have or should not pretend to
have, such as Nitro, stores, gifts, Discord network messages, or real Discord
invites.

This document remains the aspirational React/Vite direction. The checked-in app
may advance in smaller Discord-informed slices before every visual surface is
fully aligned with this design.

## Product Boundaries

- Do not invent provider execution, admission, or official-record behavior.
- The React frontend reads existing HTTP/SSE state and calls only existing flow
  start/stop APIs.
- Provider/context chips must be derived from existing safe roster fields. If a
  provider contract is unknown, show a humanized fallback rather than guessing a
  stronger capability.
- Lobby/Play Mode chatter must not look like transcript or decision evidence.
- Operator diagnostics stay secondary.
- Buttons that are not wired to backend behavior should be framed as visual
  navigation, read-only summaries, or future affordances rather than fake work.

## Visual System

Theme: "Discord-informed local-first agent room."

- Shell background: Discord dark surfaces with visible panel separators.
- Panels: flat, dense dark surfaces; cards only where Discord uses cards,
  popovers, modals, repeated rows, or previews.
- Primary accent: blurple for selected navigation and primary commands.
- Action accent: green or amber only where the product state needs it.
- Status accents: green ready, blue online, amber syncing, red offline, violet
  analysis.
- Typography: system UI stack, dense UI chrome, readable Korean body copy.
- Icons: lucide icons where they map to common commands.
- Motion: small state transitions only, disabled for reduced motion.

## Layout

Desktop:

- 72px top command bar with logo, tabs, local-first status, meeting selector,
  quick-start CTA, and compact avatar.
- Each main tab owns its own three-column layout.
- Center column is the primary canvas.
- Left column carries context and participant state.
- Right column carries status, summary, or next actions.

Mobile:

- Top command bar wraps without horizontal overflow.
- Tabs remain reachable.
- Panels stack in task order.
- Primary action remains visible near the relevant tab content.

Lobby:

- Acts like a pick room before the session.
- Shows participant readiness, join-brief/external participation affordances,
  a room hero, recent room events, mode cards, room information, and a start
  panel.

Live:

- Acts like the live client.
- Shows session summary, participant state, a central timeline, live status,
  shared memory hints, and small quick actions.
- Play Mode events are visually informal.

Board:

- Acts like a decision board.
- Shows operation info, progress, role filters, claim/risk/summary/intent
  cards, open questions, and readiness.
- It is a read-only synthesis surface for now.

Archive:

- Acts like a record room.
- Shows meeting list/search-style navigation, selected meeting details,
  artifacts, participants, tags, export affordances, and highlights.

## Interaction Rules

- Use real buttons or links for clickable surfaces whenever practical.
- Any enabled clickable surface must provide `cursor: pointer`.
- Disabled controls must not use pointer and must visibly read as disabled.
- Hover and focus-visible states are required for buttons, tabs, collapsible
  headings, profile/avatar controls, member rows, agent rows, and icon actions.
- Collapsible groups need a visible arrow or equivalent state marker that
  changes between expanded and collapsed states.
- UI completion claims require a browser visual check for the changed surface.
  Code checks can prove structure, but layout, hover affordance, modal
  placement, and click feel must be checked in the rendered app.

## Chat Media

- Image attachments render in the message stream at a Discord-like preview
  width with the whole image visible by default. Do not show the filename as a
  caption under ordinary image messages.
- Image click opens a larger preview dialog with download/close controls.
- Non-image attachments may show filename and size because the filename is the
  usable object.
- Links render as normal blue chat links and, when possible, also get a compact
  Discord-like preview card under the message. The card must be a preview of the
  URL, not a fake external fetch result.

## Acceptance Checks

- At 1280px desktop, the top command bar and three-panel views fit without
  horizontal overflow.
- Lobby, Live, Board, and Archive look meaningfully different.
- Play Mode does not look like official transcript evidence.
- Participant cards name provider execution and context honestly, including
  stateless prompt calls, provider-owned resume sessions, advisory sandboxing,
  and host-admission state.
- Text preserves readable tokens such as `Kiro Opus 4.7`, `0.5`, `80kg`, and
  ellipses.
- `npm run build` passes.
- `git diff --check` passes.
- Browser screenshots are inspected for desktop and mobile when UI changes.
- Changed clickable surfaces are clicked or hovered in the rendered app before
  reporting completion.
