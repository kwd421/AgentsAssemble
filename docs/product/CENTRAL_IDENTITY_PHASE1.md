# Central identity and server directory — Phase 1

Phase 1 keeps rooms, messages, attachments, provider sessions, and room ACLs on each local AgentsAssemble engine. Cloudflare stores only central people, bound devices, server identities, bookmarks, sessions, and expiring endpoint leases.

## Startup contract

1. On desktop, the native window stays hidden while the loopback engine starts, then opens directly on the login or room UI. The launcher does not show a separate local-engine loading screen. `App` still waits for startup identity resolution and a local room-directory readiness check before rendering cached room rows.
2. With no central session, the user chooses Google, a new guest, or an existing guest recovery code.
3. A newly created or recovered guest receives a rotated 160-bit recovery code. The application continues only after explicit confirmation that the code was stored safely.
4. Once a remembered session exists, central downtime does not block the local engine. A rejected or expired central session does require login again.
5. Central login never grants room membership. Each selected engine continues to enforce its own invite, session, and ACL contract.

Desktop Google login uses the system browser, a loopback callback, and PKCE. The
app opens Google's standard installed-app authorization endpoint directly. Google
returns its short-lived authorization code to the exact local callback that started
the attempt; the central Worker exchanges it and issues a session only when the app
presents the matching PKCE verifier. The request uses only the `openid` scope, so it
does not request name, email, profile, birthday, or contacts. Users do not copy a
confirmation code, and the app does not poll the public Worker for browser completion.
Clients without the native loopback callback do not expose Google login until they
gain an equivalent platform callback boundary; there is no confirmation-code fallback.

Special invite, operator-pairing, and room-recovery URLs bypass the normal startup identity boundary so an invited user is not trapped behind an unrelated account screen.

## Server ownership and endpoint publication

- The engine reuses its durable local `server_id` and stores one persistent Ed25519 host key with owner-only permissions.
- `/api/server-info` exposes only server identity metadata. It exposes no rooms, people, tokens, or local paths.
- A central server row can be claimed only with a short-lived host-key signature issued by the local-operator-only registration-proof route. Reading public server metadata is insufficient.
- The host signs every endpoint update. Generations are monotonic. Normal tunnel shutdown sends an immediate offline update; a lease handles crashes or network loss.

## Configuration

Set `VITE_AGENTSASSEMBLE_CENTRAL_URL` while building the frontend and set `AGENTSASSEMBLE_CENTRAL_URL` for the local engine. Optional defaults are a 300-second heartbeat and a 600-second lease. Worker/D1 deployment and secrets are documented in `infra/identity-directory/README.md`.

Cloudflare API tokens, room bearer tokens, host tokens, recovery-code plaintext, and Google token plaintext must never be placed in D1 or frontend build variables.
