# AgentsAssemble Frontend

React + Vite + Tailwind v4 frontend for AgentsAssemble.
This is the default operator console for the local GUI room.

Start the backend:

```bash
python3 -m agentsassemble.cli gui --port 8765
```

`/` serves this React Discord-style room client once a production build exists.
There is no dependency-light vanilla fallback anymore: `/legacy/`,
`/legacy/static/*`, and `/static/*` are retired frontend paths. The same React
app is also aliased at `/app/`. The React-only integration and its
operator-verified browser-parity caveat live in
`docs/product/react-room-client-integration.md`.

Build the React room client and serve it from the same backend:

```bash
npm run build
python3 -m agentsassemble.cli gui --port 8765
```

Open http://127.0.0.1:8765/ (or the `/app/` alias).

The GUI startup banner prints the React room-client URL when the build is
present; otherwise it prints the build command and the server reports the
missing React build instead of serving an old console.

## Prerequisites

- Node.js 20.19+ or newer
- The AgentsAssemble GUI backend running

## Run

```bash
cd frontend
npm install --ignore-scripts
npm run dev
```

Open http://localhost:5173

## Backend Proxy

Vite dev server proxies `/api/*` to `http://127.0.0.1:8765` by default.

Override with env:

```bash
AGENTSASSEMBLE_API_TARGET=http://127.0.0.1:8765 npm run dev
```

Start the backend:

```bash
python3 -m agentsassemble.cli gui --port 8765
```

Read-only launch guidance:

```bash
python3 -m agentsassemble.cli frontend-info
```

## Build

```bash
npm run build
```

Output: `frontend/dist/`

## Layout

Discord-inspired shell (see `DESIGN.md`):

- Server/room rail: narrow far-left column with the room mark and a settings
  (admin) gear.
- Channel sidebar: room name + status, the `#` channel list, and a footer user
  area with the local user profile controls. There is no legacy UI badge or
  fallback link in the room client.
- Central column: channel header + internally scrolling messages/content + a
  sticky composer where the channel is writable.
- Member list: compact roster with presence dots, collapsible on narrow
  screens.

## Channels

- 로비: lobby chat + composer, with a compact meeting/topic/mode start-stop bar
- 실황: official agent timeline + unofficial side-chat composer (and mafia)
- 작전판: read-only meeting lifecycle synthesis (view only)
- 아카이브: meeting list + final artifact viewer (readable prose, not raw pre)

Admin (read-only health, release-health, resources, and the CLI-only external-agent
invite: join brief / LAN invite) is reachable from the rail gear. Play Mode
start/stop does not start real provider CLIs.

Play Mode start/stop does not start real provider CLIs.
