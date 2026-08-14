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
  tokens and raw subjects are never persisted.
- Server endpoints are accepted only when signed by the server's Ed25519 host key.
  Endpoint generations are monotonic, and leases expire automatically.
- A central login never grants room membership. Clients still authenticate directly
  to the selected engine and its room ACLs.

## Setup

```bash
cd infra/identity-directory
wrangler d1 create agentsassemble-identity
# Copy the database id into wrangler.toml.
wrangler secret put RECOVERY_PEPPER
wrangler secret put IDENTITY_PEPPER
wrangler secret put GOOGLE_CLIENT_ID   # optional until Google login is configured
wrangler d1 migrations apply agentsassemble-identity --remote
wrangler deploy
```

Use independently generated 32-byte-or-longer values for both peppers. Do not reuse
Cloudflare account API tokens, tunnel tokens, host tokens, or room credentials.

`CENTRAL_ALLOWED_ORIGINS` may contain a comma-separated allowlist of production app
origins. Loopback HTTP origins and HTTPS `*.trycloudflare.com` origins are accepted by
default for the desktop and quick-tunnel prototype. Set
`ALLOW_TRYCLOUDFLARE_ORIGINS = "false"` when a fixed production origin is available.

`ALLOWED_SERVER_HOSTS` is a comma-separated list of fixed custom hostnames allowed as
server endpoints. Quick Tunnel `*.trycloudflare.com` endpoints are accepted without
adding them to that list.

## Tests

```bash
npm test
npm run check
```
