# AgentsAssemble React Frontend Design

## Goal

Make the room feel like a chat client a person can just *use* — not an operator
dashboard. Borrow the calm, dense, friendly layout of Discord: a narrow server
rail, a channel sidebar, a central message column, and a compact member list.
Show only what a participant or watcher needs; keep technical/operator truth in
details, tooltips, and the admin surface.

This React Discord-style room client is the canonical room-client frontend. It
is inspired by Discord, not branded as Discord. It replaces the earlier
operator-console and retired vanilla GUI surfaces with one concrete React/Vite
room-client surface.

## Accepted Direction

- Discord-style dark theme: near-black server rail, dark gray sidebar/member
  list, slightly lighter chat column, blurple accent, green/yellow/red presence.
- Fixed full-height app shell. The page/body is never the desktop scroll
  surface; every long list scrolls inside its own region.
- Four text channels: `로비`, `실황`, `작전판`, `아카이브`.
- One navigation surface (the channel sidebar). No duplicate command strips,
  no top tab bar.
- Honest, quiet presentation: no neon HUD, no hero banners, no big meters, no
  decorative card grids, no provider jargon on default surfaces.

## Product Boundaries (unchanged)

- The frontend reads existing HTTP/SSE state and calls only existing APIs. It
  does not invent provider execution, admission, or official-record behavior.
- Real human inputs are the lobby composer (`/api/lobby`), the unofficial
  side-chat composer (`/api/side-chat`), and the mafia controls. Unfinished
  controls are hidden rather than shown as fake disabled buttons.
- Play Mode / side-chat chatter stays visually distinct from official record:
  agent turns render as the official timeline; human side-chat is muted and
  tagged `사이드챗`, never promoted to official.
- Attachments use public metadata only (`is_image`/`url`/`download_url`); raw
  bytes never reach event UI.
- Provider/context/admission truth is derived from safe roster fields and shown
  behind a per-member `<details>` in the member list, not as inline jargon.
- Work Mode and Play Mode record boundaries are preserved; there is no promote
  action.

## Layout

Shell (`App.tsx`):

- **Server rail** (72px): room/home mark → lobby, the room button, and a
  settings gear (admin) pinned to the bottom.
- **Channel sidebar** (240px): room name + live status header; the `#` channel
  list; a footer user area with local presence, backend status, and profile
  controls. No legacy badge, fallback link, or second operator console is shown.
- **Central column**: each channel renders a `ChannelHeader` (name, topic,
  member-list toggle) + an internally scrolling body + a sticky composer where
  the channel is writable.
- **Member list** (240px, right): grouped 온라인/오프라인 roster with presence
  dots; collapses below `xl` and via the header toggle.

Channels:

- **로비**: lobby chat + composer, a compact meeting/topic/mode start-stop bar,
  and a collapsed CLI invite (Join Brief / LAN Invite) disclosure.
- **실황**: official agent timeline interleaved with unofficial side-chat, a
  side-chat composer, the latest-jump control, and the mafia panel when a game
  is active.
- **작전판**: read-only lifecycle synthesis (current step, next action, role
  admission/permission summary, attention) plus the workroom queue panel.
- **아카이브**: meeting list + selected meeting's canonical final artifacts and
  rendered content.

## Visual System

- Tokens drive everything: `server-rail`, `sidebar`, `chat-bg`, `accent`
  (blurple), `online`/`idle`/`danger`, `text-primary/secondary/muted`, `line`.
- `.dc-*` classes own the shell (rail/sidebar/channel/members/user-area).
- The legacy `.ops-*` class names are kept but flattened to calm dark surfaces
  so views render without per-element churn; neon grid/scanline/radar/hero/meter
  effects are removed.
- Motion is limited to a small presence pulse, disabled under reduced motion.

## Known Gaps (out of scope for this pass)

The participant/invite surfaces are intentionally honest about what exists today:

- **Join Brief** (admin → 외부 참여) generates a host-approved entry packet for a
  manually-run resident via `/api/live-agent-join-brief`. It does **not** start a
  provider, perform remote admission, or authenticate a remote client. The UI
  labels this (`provider 시작 아님`, `not_started_by_join_brief`, host-approval).
- **LAN Invite** is a CLI-only PoC (HMAC entry proof) shown as command skeletons
  with env secret refs. There is no in-app token issuance, no relay/WebRTC, no
  internet exposure.
- The affordance is labeled **로컬·신뢰 네트워크 전용** and notes that remote
  admission and authentication do not exist yet.

Real backend auth, remote admission, and provider execution are not implemented
here and are not claimed complete. The UI stays honest rather than implying them.

## Acceptance Checks

- `npm run build` passes (`tsc && vite build`).
- `git diff --check` passes.
- At 1440px the rail + sidebar + chat + member list fit without horizontal
  overflow; at narrow widths the member list collapses and nothing overflows.
- Lobby and Live scroll internally; the body is not the scroll surface.
- No dashboard clutter strings on default surfaces (live status, 핵심 포인트,
  빠른 작업, lifecycle exposition, room insights, status meters).
- Side-chat reads as unofficial; attachments expose public metadata only.
- Tests in `tests/test_react_ui_contracts.py` and `tests/test_frontend_*.py` lock
  these invariants; server route tests ensure retired legacy paths no longer
  serve the old console.
- Browser screenshots inspected for desktop and narrow widths.
