# AgentsAssemble Desktop

The Tauri 2 desktop application opens immediately and starts its bundled local
room runtime in the background. There is no connection-mode chooser. Local use
and hosting are the same room state: the room remains local until its operator
explicitly starts public access from the room UI, and returns to local-only when
public access is stopped.

## Run locally

```sh
make desktop-deps
make desktop-dev
```

`desktop-dev` builds the React application, packages the Python room runtime,
and opens the room cache plus startup progress screen. Cached local and remote
room summaries appear before the local runtime is ready; unavailable remote
rooms remain visible as disconnected instead of being deleted by the local
server directory refresh. The local runtime stores its rooms and
identity data below the operating system's application-data directory. A
runtime started by the desktop application is its child and stops when the
application exits. An already-running valid AgentsAssemble service on
`http://127.0.0.1:8765/` is reused and is not stopped by the client.

## Build installers

The build machine needs Node.js, Rust, and `uv`. The build command pins
PyInstaller for the bundled Python sidecar, generates the platform-specific
Tauri sidecar name, and then builds the native installer:

```sh
make desktop-build
```

Sidecars are built natively. A Windows installer must therefore be produced on
Windows and a macOS installer on macOS.

## Signed releases and desktop updates

Ordinary development builds do not contact an update service. The startup
screen reports the updater as unconfigured internally and continues directly
to the local runtime. A release build enables signed updater artifacts only
when its endpoint and signing material are supplied through the environment:

```sh
AGENTSASSEMBLE_UPDATE_ENDPOINT=https://releases.example/latest.json \
AGENTSASSEMBLE_UPDATE_PUBLIC_KEY='...' \
TAURI_SIGNING_PRIVATE_KEY='...' \
npm --prefix desktop run build:release
```

`TAURI_SIGNING_PRIVATE_KEY_PASSWORD` may also be supplied when the updater key
is encrypted. None of these values is accepted on argv or stored in the
repository. macOS release builds use an installed `Developer ID Application`
identity and submit the resulting DMG through the Keychain notary profile named
by `AGENTSASSEMBLE_NOTARY_PROFILE` (default: `seinel-notary`). Windows release
artifacts must be built on Windows with that platform's signing setup.

At application startup, a configured updater checks before the bundled room
runtime starts. An available signed release shows download progress, installs,
and restarts the application. A temporarily unavailable update service does not
block local/offline rooms; malformed or unsigned update artifacts still fail
closed in the updater verifier.

## Security boundary

The bundled startup screen can start the owned loopback runtime, open its
validated origin, and read the bounded room-summary cache. The native client
refreshes local room summaries after the runtime is ready and again before a
graceful shutdown. The navigated room webview has no native cache-writing or
runtime-lifecycle privilege. Provider and credential operations remain behind
the canonical server rather than becoming ambient desktop-webview privileges.
