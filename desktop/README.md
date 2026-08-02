# AgentsAssemble Native Clients

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

## iOS and Android

The mobile applications open without starting a Python server. They show the
sanitized room-directory cache offline and connect to a room using an HTTP(S)
server, invite, or recovery link entered directly or scanned as a QR code.
Google account handoff continues in the system browser. A successfully opened
server may update only its own cached room summaries; bearer and session fields
are removed before native persistence.

The native Xcode and Gradle workspaces are generated build products and remain
ignored. Initialize them once after installing the official Tauri mobile
prerequisites, then produce keyless development builds:

```sh
make mobile-ios-init
make mobile-ios-build
make mobile-ios-release

make mobile-android-init
make mobile-android-build
make mobile-android-release
```

The build helper deliberately resolves `rustc` and `cargo` through `rustup`, so
a Homebrew Rust installation cannot silently compile against the wrong mobile
standard library. Android uses `ANDROID_HOME`, `NDK_HOME`, and `JAVA_HOME` when
provided, with macOS development defaults only when they are absent. The iOS
simulator output and Android debug APK need no signing secret.

The iOS release target uses App Store Connect export and the configured Apple
team `DRUFU8Q688`. It requires the matching distribution identity and
provisioning profile in Xcode. The Android release target emits a signed AAB
after these environment-only inputs are supplied:

```text
ANDROID_UPLOAD_KEYSTORE
ANDROID_UPLOAD_KEY_ALIAS
ANDROID_UPLOAD_STORE_PASSWORD
ANDROID_UPLOAD_KEY_PASSWORD
```

The generated Gradle project receives only a reference to the committed
signing policy; no password or keystore is written into the repository. Store
upload credentials and the final publish action remain external. No hosted room
URL is baked into either app: local-public and future cloud servers are selected
by their invite or recovery links.

## Security boundary

The bundled desktop startup screen can start the owned loopback runtime, open
its validated origin, and read the bounded room-summary cache. The native
client refreshes local room summaries after the runtime is ready and again
before a graceful shutdown. A room webview may refresh sanitized summaries only
for its selected same-origin server and has no native runtime-lifecycle
privilege. Provider and credential operations remain behind the canonical
server rather than becoming ambient native-webview privileges.
