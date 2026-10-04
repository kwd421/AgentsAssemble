# AgentsAssemble Frontend

React + Vite + Tailwind v4 frontend for AgentsAssemble.
This is the default Discord-style room client for the local GUI room.

Start the backend:

```bash
python3 -m agentsassemble.cli gui --port 8765
```

`/` serves this Discord-style React room client once a production build exists;
until then the backend returns a build-required response instead of serving the
retired vanilla console. The React app is also aliased at `/app/`.

Build the React default and serve it from the same backend:

```bash
npm run build
python3 -m agentsassemble.cli gui --port 8765
```

Open http://127.0.0.1:8765/ (or the `/app/` alias).

The GUI startup banner prints the React default URL when the build is present;
otherwise it prints the build command and keeps `/` and `/app/` on the same
React-build-required contract until the build exists.

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

- Left rail: dark sidebar with channel navigation (대기실, 실황, 기록, 관리)
- Center: active channel view (chat feed, conversation, records)
- Right sidebar: participant roster with status dots (desktop only)
- Mobile: top tab bar replaces left rail, roster hidden

## Channels

- 대기실: lobby chat + Play Mode start/stop controls
- 실황: live Play Mode conversation feed (flow_events)
- 기록: meeting list + artifact viewer (readable prose, not raw pre)
- 관리: read-only health status

Play Mode start/stop does not start real provider CLIs.
