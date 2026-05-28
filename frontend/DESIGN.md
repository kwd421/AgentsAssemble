# AgentsAssemble React Frontend Design

## Goal

Build a neon operations console for AgentsAssemble. The app should feel like a
local-first agent room preparing, running, reviewing, and archiving live
sessions. It should look special without making backend behavior look more
powerful than it is.

## Accepted Direction

The reference direction is a sci-fi operations room:

- dark navy/black shell.
- cyan glass panel borders with angular corners.
- gold quick-start and next-step actions.
- compact left/center/right command-console layout.
- four first-class tabs: `로비`, `실황`, `작전판`, `아카이브`.
- visible participant readiness and room status.
- Play Mode remains informal and separate from official records.

This is not a Discord clone anymore. Borrow only the useful density and
real-time readability; the visual language is now "neon mission control."

This document remains the aspirational React/Vite direction. The current
checked-in app may advance in smaller launch-clarity slices before every visual
surface is fully aligned with this design.

## Product Boundaries

- Do not invent provider execution, admission, or official-record behavior.
- The React frontend reads existing HTTP/SSE state and calls only existing flow
  start/stop APIs.
- Lobby/Play Mode chatter must not look like transcript or decision evidence.
- Operator diagnostics stay secondary.
- Buttons that are not wired to backend behavior should be framed as visual
  navigation, read-only summaries, or future affordances rather than fake work.

## Visual System

Theme: "local-first holographic operations console."

- Shell background: near-black blue with subtle grid/radar glow.
- Panels: translucent midnight blue, 1px cyan border, clipped corners.
- Primary accent: electric cyan.
- Action accent: amber/gold.
- Status accents: green ready, blue online, amber syncing, red offline, violet
  analysis.
- Typography: system UI stack, dense UI chrome, readable Korean body copy.
- Icons: lucide icons plus simple CSS hex badges; no external image dependency.
- Motion: small pulse/scan effects only, disabled for reduced motion.

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

## Acceptance Checks

- At 1280px desktop, the top command bar and three-panel views fit without
  horizontal overflow.
- Lobby, Live, Board, and Archive look meaningfully different.
- Play Mode does not look like official transcript evidence.
- Text preserves readable tokens such as `Kiro Opus 4.7`, `0.5`, `80kg`, and
  ellipses.
- `npm run build` passes.
- `git diff --check` passes.
- Browser screenshots are inspected for desktop and mobile.
