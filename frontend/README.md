# AgentsAssemble Frontend

React + Vite + Tailwind v4 frontend for AgentsAssemble.
Discord-inspired live room UI. Separate from the default vanilla backend/operator console.

The default entry point is still:

```bash
python3 -m agentsassemble.cli gui --port 8765
```

This React/Vite surface is opt-in until API/SSE parity and fallback behavior are verified.
The vanilla legacy console namespace is now explicit: the same dependency-light
operator console is available at `/` and `/legacy/`; the default entry point is unchanged.
The parity gate for any future default-route change lives in
`docs/product/legacy-react-parity-matrix.md`.

When a production build exists, the Python GUI can also serve the React preview
from the same backend at `/app/`:

```bash
npm run build
python3 -m agentsassemble.cli gui --port 8765
```

Open http://127.0.0.1:8765/app/

This preview route is still opt-in. `/` remains the vanilla console and
`/legacy/` remains the fallback until the parity matrix supports a deliberate
default flip.

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
