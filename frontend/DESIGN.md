# AgentsAssemble React Frontend Design

## Goal

Build a Discord-inspired meeting client, not a Discord clone. The interface should
feel like a live room where agents are visibly present, the lobby is a prep room,
the live tab is the active conversation, records are the archive, and admin tools
are secondary.

## Reference Structure

Discord-like structure to borrow:

- Left navigation stays dark and persistent.
- The active channel title is clear at the top of the conversation.
- Messages are dense, grouped, and occupy the center without becoming lonely.
- The participant list is visible on desktop by default.
- Bottom composer/control surface is compact and always anchored.
- Mobile collapses side panels but keeps primary room actions reachable.

Product-specific differences:

- Use "servers/channels" as metaphor only; do not add fake servers.
- Keep Play Mode informal and visually separate from official records.
- Do not make admin/diagnostic actions look like normal room conversation.
- Do not invent backend behavior or fake agent output.

## Visual System

Theme: "dark room shell, focused conversation canvas."

- Shell background: near-black slate.
- Channel panel: dark gray with clear active states.
- Conversation canvas: Discord-like dark surface, not white.
- Roster panel: slightly darker than chat to frame participants.
- Accent: blurple-inspired blue for active controls, with green only for live/online.
- Typography: system UI stack, small dense controls, readable message text.
- Radius: mostly 6-10px; no oversized pill/card decoration.
- Motion: subtle only; live dots may pulse, message list should not jump.

## Layout

Desktop:

- 64px server rail.
- 248px channel/prep rail.
- Flexible chat/live/records area.
- 240px roster panel visible by default.

Lobby:

- Header shows room name, online count, and current flow status.
- Top prep strip shows online agents as compact chips.
- Main feed shows lobby messages grouped like chat.
- Bottom control bar starts/stops Play Mode.

Live:

- Header emphasizes current live topic and remaining time.
- Feed shows flow-scoped events with action metadata as small badges.
- Empty live state should still feel like a room waiting for speech.

Records:

- Meeting list should be scannable, dense, and archived.
- Detail view keeps artifact tabs compact.

Admin:

- Drawer/panel tone. Clearly secondary and quieter than room tabs.

## Acceptance Checks

- At 1280px desktop, all four regions are visible without horizontal overflow.
- The default view does not look like an empty white chat page.
- Roster is visible by default on desktop.
- Lobby and live look meaningfully different.
- `npm run build` passes.
- `git diff --check` passes.
- Browser screenshots are inspected for desktop and mobile.
