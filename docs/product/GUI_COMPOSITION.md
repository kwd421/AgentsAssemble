# GUI Composition

Status: current composition map for the local React GUI and canonical room runtime.

## Entrypoints and ownership

- `agentsassemble/gui.py` is the stable composition entrypoint.
- `agentsassemble/application/gui_runtime.py` owns startup, HTTP binding, rolling restart, and shutdown.
- `agentsassemble/application/gui_factory.py` builds the server-scoped service graph.
- `agentsassemble/application/gui.py` owns `GuiApplicationServices` and its lifecycle.
- `agentsassemble/web/gui_server.py` adapts HTTP and WebSocket requests to the composed services.

The selected `RoomRepository`, identity backend, invite repository, public
invite runtime, media store, WebSocket ticket store, provider bridge manager,
and `RoomRealtimeController` are constructed once and shared. Owned resources
are closed by the application service graph; injected resources are closed only
when their ownership flag says so.

## Current transport

`/` and `/app/` serve the React application. `/ws?ticket=...` is the canonical
live room transport. HTTP routes provide bounded setup, history, media,
identity, recovery, provider, and administrative operations. There is no
meeting console, resident process supervisor, lobby POST/SSE fallback, or
legacy route family.

`agentsassemble/web/routes/gui.py` registers the current route families:

- accounts, Google login callback, and identity recovery;
- canonical room creation, history, Agent Sessions, lifecycle, members, media,
  settings, and invite admission;
- attachments and personas;
- provider catalog, usage, login, credentials, and local workspace selection;
- public invite/tunnel administration;
- runtime status, side chat, saved friends/profile, observability, and Mafia.

## Invariants

1. Browser and Agent Bridge room activity uses the canonical WebSocket command path.
2. Routes and `RoomRealtimeController` share the same repository instance.
3. Public requests pass Host/Origin policy before route authorization.
4. Provider credentials and local process controls remain local-host or explicitly host-gated.
5. Provider processes start only after an explicit operator action.
6. Room history advances only through contiguous canonical sequence numbers.
7. Shutdown is bounded, idempotent, and uses owned process handles rather than PID guessing.
8. The frontend never falls back to retired HTTP/SSE room writers when the socket is unavailable.

## Frontend composition

`frontend/src/App.tsx` composes focused controllers under `frontend/src/app/`.
`frontend/src/useCanonicalRoom.ts` and `frontend/src/roomSocketClient.ts` own the
sequenced room connection. Views render state and emit actions; they do not own
server-authoritative room settings or provider process state.

Agent creation and control use canonical Agent Sessions. Saved friends are a
local directory/profile feature; the removed direct-message runtime is not a
fallback transport for room invitations or agent commands.

## Verification

Composition changes should run the targeted backend tests, the frontend build
and test suite, the full Python suite, `make architecture-check`, and generated
map checks. GUI behavior changes also require an application smoke or visual
inspection when the environment permits it.
