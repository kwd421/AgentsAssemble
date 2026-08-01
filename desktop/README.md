# AgentsAssemble Desktop

The Tauri 2 desktop application opens its own connection screen before any room
runtime is available. A user can then:

- start a private room runtime on this computer;
- start the same local runtime and publish invitations from the room UI; or
- connect the client to an existing cloud-hosted AgentsAssemble server.

Local and host modes use the same loopback-only canonical server. Hosting is an
explicit room action that publishes an authenticated invitation; it never
changes the control plane to an unauthenticated network bind.

## Run locally

```sh
make desktop-deps
make desktop-dev
```

`desktop-dev` builds the React application, packages the Python room runtime,
and opens the connection screen. The local runtime stores its rooms and
identity data below the operating system's application-data directory. A
runtime started by the desktop application is its child and stops when the
application exits. An already-running valid AgentsAssemble service on
`http://127.0.0.1:8765/` is reused and is not stopped by the client.

An explicit cloud target can be prefilled for development or automation:

```sh
npm --prefix desktop run dev -- -- --server https://room.example.com
```

`AGENTSASSEMBLE_SERVER_URL` provides the same override when no `--server`
argument is present. Only an HTTP(S) origin without credentials, query,
fragment, or non-root path is accepted.

## Build installers

The build machine needs Node.js, Rust, and `uv`. The build command pins
PyInstaller for the bundled Python sidecar, generates the platform-specific
Tauri sidecar name, and then builds the native installer:

```sh
make desktop-build
```

Sidecars are built natively. A Windows installer must therefore be produced on
Windows and a macOS installer on macOS.

## Security boundary

The bundled connection screen has only two native commands: start the owned
loopback runtime and open a validated server origin. After navigation, server
content cannot invoke those commands or navigate the top-level webview to a
different origin. Provider and credential operations remain behind the
canonical server rather than becoming ambient desktop-webview privileges.
