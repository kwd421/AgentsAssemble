# AgentsAssemble identity directory

This Worker is the Phase 1 central control plane. It stores only central identities,
device credentials, known server identities, and short-lived server endpoint leases.
Room lists, messages, attachments, provider sessions, host tokens, room bearer tokens,
and invite credentials remain on each AgentsAssemble engine.

## Security model

- Central `person_id` values are random and are not local room participant IDs.
- Browser sessions require both an opaque bearer token and a signature from a
  non-exportable P-256 device key. Replayed signed requests are rejected.
- Guest recovery codes contain 160 random bits, are displayed only to the client,
  and are stored in D1 only as an HMAC verifier. A successful recovery rotates the
  code and revokes prior sessions for that device.
- Google subjects are stored only as an HMAC keyed by `IDENTITY_PEPPER`; Google ID
  tokens, raw subjects and email addresses are never persisted. Verified name/photo
  URL metadata seeds display defaults; image bytes belong to each local engine.
- Desktop Google login opens Google's standard installed-app authorization page,
  returns through an exact loopback callback, and exchanges the one-time Google
  authorization code with PKCE. The browser URL alone cannot claim the resulting
  central session, and users do not copy a confirmation code. The `openid profile`
  scopes supply identity and display defaults; no email or contacts scope is used.
- Signed sessions can revoke every other session or delete their central identity.
  Account deletion cascades through devices, sessions, recovery state, and owned
  server registrations.
- Server endpoints are accepted only when signed by the server's Ed25519 host key.
  Endpoint generations are monotonic, leases expire automatically, and deleting a
  server registration immediately invalidates its old host key. Each central identity
  can own at most 20 registered servers.
- A central login never grants room membership. Clients still authenticate directly
  to the selected engine and its room ACLs.
- Public Quick Tunnel origins are valid server endpoints, but are not trusted central
  login origins in production. This prevents a server page from receiving central
  session or recovery responses.

## Setup

```bash
cd infra/identity-directory
wrangler d1 create agentsassemble-identity
# Copy the database id into wrangler.toml.
wrangler secret put RECOVERY_PEPPER
wrangler secret put IDENTITY_PEPPER
wrangler secret put GOOGLE_DESKTOP_CLIENT_ID   # Google Desktop app OAuth client
wrangler secret put GOOGLE_DESKTOP_CLIENT_SECRET
wrangler d1 migrations apply agentsassemble-identity --remote
wrangler deploy
```

Use independently generated 32-byte-or-longer values for both peppers. Do not reuse
Cloudflare account API tokens, tunnel tokens, host tokens, or room credentials.
The Desktop OAuth client ID is public. Keep its client secret only in the Worker
secret binding; never include it in the app, browser authorization URL, or logs.
Both bindings must be configured before native Google login is advertised.

Native login requests `openid profile`. Only the verified Google subject identifies
an account; its name and HTTPS Google profile-photo URL seed display defaults.
Migration `0004_google_profile.sql` adds nullable `persons.avatar_url`: NULL means
that an older Google placeholder has not received verified profile metadata;
an empty string means the verified account has no supplied photo. First import
fills the metadata once. Later login never overwrites it. No email scope is used.
The Rust profile owner fetches and canonicalizes the photo once into its existing
local avatar storage; edited local profiles remain authoritative.

Explicit desktop logout makes the next login allocate a fresh durable device
credential instead of reusing a slot permanently bound to the previous account.
This preserves the old guest/account and its recovery path. The bundled local
operator may claim this server's directory registration for the newly signed-in
account using a distinct `AA-HOST-CLAIM-1` proof and signed device request.
Claims require the exact previously registered host key and a single-use nonce;
ordinary registration still rejects a different owner or substituted key. One D1
transaction changes the owner relation, keeps the old account's bookmark and
preserves the endpoint. The existing 20-server bound also guards concurrent claims.
No local rooms/messages, room permissions or Google identities are merged.

`CENTRAL_ALLOWED_ORIGINS` is the exact comma-separated allowlist for trusted bundled
client origins. Loopback HTTP origins are also accepted so the local desktop engine
can complete first-run setup. Production keeps
`ALLOW_TRYCLOUDFLARE_ORIGINS = "false"`; switch it on only for an isolated development
experiment, never for the public identity Worker.

`ALLOWED_SERVER_HOSTS` is a comma-separated list of fixed custom hostnames allowed as
server endpoints. Quick Tunnel `*.trycloudflare.com` endpoints remain accepted without
adding them to that list. Endpoint acceptance and central-login CORS are deliberately
separate policies.

## Tests

```bash
npm test
npm run check
```
