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

For an existing deployment, follow the two-stage rollout below before deploying
the branch tip. The stage-1 Worker version must exist as the rollback baseline.

```bash
cd infra/identity-directory
wrangler d1 create agentsassemble-identity
# Copy the database id into wrangler.toml.
wrangler secret put RECOVERY_PEPPER
wrangler secret put IDENTITY_PEPPER
wrangler secret put GOOGLE_DESKTOP_CLIENT_ID   # Google Desktop app OAuth client
wrangler secret put GOOGLE_DESKTOP_CLIENT_SECRET
wrangler secret put GOOGLE_CLIENT_ID           # Existing Google Web OAuth client
wrangler secret put GOOGLE_WEB_CLIENT_SECRET
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
npm ci --ignore-scripts
npm test
npm run check
```

## Browser account entry

The fixed Worker origin serves the shared Rust frontend. A direct room-host URL
links here instead of receiving central credentials. App and browser use the same
startup, guest recovery and server chooser components. The shared Google button
opens standard Google authorization with `openid profile`, PKCE and tab-bound state.
`GOOGLE_CLIENT_ID` and `GOOGLE_WEB_CLIENT_SECRET` identify the existing Web OAuth
client; register the exact Worker root URL (including its trailing slash) under
Authorized redirect URIs. Keep the secret only in the Worker secret binding.
A one-use ten-minute handoff binds the device key, verifier and Google nonce. Code
exchange and ID-token verification share the native handoff owner. Only same-origin
web start/completion requests are accepted; production CORS is unchanged. Google
email claims are ignored and never stored. Native Desktop OAuth retains its client
and loopback callback. Both flows resolve the same canonical Google person.

Build the Rust frontend before deploying the Worker together with its shared assets:

```sh
npm --prefix "$RUST_CHECKOUT/frontend" run build
wrangler deploy --assets "$RUST_CHECKOUT/frontend/dist"
```

Keep the non-secret frontend `VITE_AGENTSASSEMBLE_CENTRAL_URL` equal to the deployed
Worker origin. The browser validates its central
session, lists owner/bookmark records and opens an online owned server through the
existing short connect grant. It cannot claim a host or initialize native authority.
The central bearer/signing key stay at the central origin. A host redeems the short
entry grant at `POST /v1/servers/:id/connect-grants/redeem`, using its signed
`{grant_token, origin, generation}` request. Current account/session/device, owner
relation and endpoint checks run atomically with redemption. The host then owns
its admitted workspace, device-session list and revocation; it makes no central
owner renewal calls. Logout, account removal and ownership transfer affect the next
admission, not an already connected host workspace. New page/reconnection requires
fresh central admission. There is no `owner-connections` API or persistent owner-connection table.

The host renews a still-online endpoint using signed
`POST /v1/servers/:id/endpoint/renew` with the existing
`{origin,generation,issued_at,lease_expires_at}` body. Exact origin/generation is
preserved; renewal cannot shrink or revive an expired/offline endpoint. PUT
publication and DELETE retirement advance generation. This is server discovery,
not connected-user authorization: 288 requests per online server per 24 hours at
5-minute intervals. Owner keep-alive uses 0 requests and 0 D1 writes. Admission
uses 2 requests (grant issue and redemption), with signed list/login operations
counted separately. Admission changes 4 logical rows before indexes/expiry cleanup;
D1 usage counts actual rows written, including indexed updates.

Local verification applies the nine migrations to isolated workerd D1 and runs
`node test/local_owner_connections.mjs http://127.0.0.1:8799`. The runner rejects
non-loopback URLs and uses synthetic credentials. It exercises entry redemption,
exact retry, stable endpoint generation and logout preventing the next admission.
Host lifetime/revocation and actual browser use are verified by the Rust product.
Production Worker deployment and migrations require explicit user approval.

## Server icons (backend contract, 2026-10-04)

The directory owns server icon images, separate from room appearance and local
profile photos. The current Rust contract's Central server icons section records acceptance.
One bounded PNG per registration is stored in D1; bootstrap returns only its
versioned reference. Existing rows have an empty icon. Owner-only observed-value
editing and device-signed image retrieval use existing central authentication.
Production application of migration 0008 and Worker deployment are separate from
local implementation/verification.


All icon requests use the existing signed central device headers (Bearer session,
`x-aa-device-id`, `x-aa-timestamp`, `x-aa-nonce`, `x-aa-signature`). Do not send host
room credentials or put credentials in URLs.

- `POST /v1/servers/:server_id/icon`, JSON:
  `{"icon":"data:image/png;base64,...","expected_icon":""}`. To remove, send
  `icon: ""` and the currently observed reference as `expected_icon`.
- Success 200: `{"server_id":"...","icon":"/v1/servers/.../icon/<sha256>.png"}`;
  removal returns `icon: ""`. The same desired bytes/removal can be retried with a
  new signed nonce. Stale different edits/non-owners/bookmarks return 409
  `server_icon_conflict`; replayed signed requests retain `replayed_request`.
- `GET /v1/bootstrap` includes `servers[].icon` for owners and bookmarks, including
  offline servers. Empty string means no icon. This is a reference relative to the
  fixed central origin, independent of the selected host endpoint.
- Signed `GET` of the exact returned reference returns `image/png`. Fetch it with
  central credentials and display a local object URL; the shared web response's
  image CSP permits `blob:`. A plain `<img src=reference>` has no signed headers.
  Response is `no-store`/`nosniff`; missing/replaced/removed/inaccessible references
  return 404 `server_icon_not_found`. No authentication returns 401.
- Upload exactly 512x512 static, noninterlaced 8-bit RGB/RGBA PNG, maximum 1,100,000
  file bytes. Fixed-size canvas colour/density metadata and one uncompressed
  pre-IDAT Exif chunk (8–4096 bytes, as produced by WebKit) are accepted; APNG,
  compressed/text metadata, external URLs and other formats are rejected. Invalid
  input returns 400 `invalid_server_icon`; file oversize returns 413
  `server_icon_too_large`, HTTP body oversize returns 413 `request_too_large`.

Migration `0008_server_icons.sql` preserves existing registrations with `icon: ""`.
Reference and bounded current PNG change atomically; replacement does not accumulate
old images. Account/server deletion cascades the blob. D1's delete change count
includes child rows, so registration deletion accepts a positive count rather than
misreporting a successful cascade as 404. Only the current canonical owner may edit,
even during a host ownership claim. No frontend source, room icon or host authority
changes are part of this feature.

## Two-stage cleanup and abuse rollout

### Stage 1: bounded daily cleanup (rollback baseline)

Deploy stage-1 commit `d54aebc7` before enabling rate-limit bindings. Keep the existing single
cron `17 3 * * *` (03:17 UTC). Migration `0009_bounded_cleanup.sql` adds only
session expiry/revocation indexes; retain it on code rollback. No rate-limit
bindings or purpose limiter code are present in this stage. Existing D1 precision
limits remain. Grant requests no longer delete the global expired-grant backlog.

`cleanup.js` processes six queues: device nonces, host nonces, precision counters,
Google handoffs, grants and sessions. Each round selects at most 15,000 candidates
per queue, with at most eight rounds: **120,000 candidates/day/table**, at most
720,000 logical deletes per invocation. Stop early when a round deletes nothing.
Session candidates with nonce/grant children are skipped to prevent unbounded
foreign-key cascades; authentication already rejects expired/revoked credentials.
Migration 0009 and existing expiry/window indexes support candidate selection.

The invocation uses at most 48 D1 statements (eight batches of six), below the
[Free plan limit of 50 queries/invocation](https://developers.cloudflare.com/d1/platform/limits/).
Only result metadata crosses back to JavaScript; there is no per-row JS loop or
unbounded drain/retry loop. This is a finite work budget, not a measurement of
production CPU time. D1 errors propagate; a failed batch rolls back, earlier
batches remain committed, and the next daily run retries the remaining backlog.

120,000 exceeds the Free D1 ceiling of 100,000 new logical rows/day across the
whole database and one host's 288 endpoint-renewal nonces/day. This is scheduling
capacity, not guaranteed free throughput: inserts, deletes, updates and index
maintenance share the write quota. Even ignoring indexes, sustainable creation
plus deletion is at most 50,000 temporary rows/day; actual headroom is lower.
Quota exhaustion, SQL timeouts or CPU limits can stop cleanup early. Observe
production CPU, D1 usage and expiry backlog before increasing traffic. Local
SQLite/workerd verification does not establish the production CPU ceiling.

### Deployment and rollback order

1. Record the deployed Worker version and migration state. Apply additive 0009,
   deploy `d54aebc7` with the daily cron only, and verify owner flows and cleanup.
   Record its Worker version ID as the rollback baseline.
2. Only after that baseline is deployed and verified, deploy stage 2 with purpose
   limiter code and bindings. A more frequent cron is optional, not required for
   the stated daily capacity.
3. Roll back stage 2 only to the recorded stage-1 (`d54aebc7`) Worker version. Never roll back
   to `25fad46a`, `4420d55d`, or any version before stage 1. Cron triggers are
   [managed separately from Worker versions](https://developers.cloudflare.com/workers/configuration/cron-triggers/);
   code rollback does not restore their previous schedule. Keep migration 0009.

The historical `25fad46a` owner protocol remains the compatibility reference,
not an operational rollback target. No production deployment or remote migration
is performed by local verification.

### Verification

```sh
npm test
npm run check
wrangler deploy --dry-run --assets "$RUST_CHECKOUT/frontend/dist"
```

The daily regression seeds 120,001 expired host nonces and checks that one
scheduled invocation removes 120,000 and retains one. It also observes deletion
of expired grants, survival of 600 live child nonces and a usable live session.
Against `4420d55d` it failed with only 479 total deletes; bounded daily cleanup
must pass without changing the owner protocol or Rust repository.

Stage-1 local verification: 46 tests pass; syntax check and Wrangler 4.98.0
deployment dry-run pass without any rate-limit binding. The isolated workerd/D1
owner smoke passes entry/retry, endpoint generation and logout checks. A backlog
of 120,001 expired precision counters loses 120,000 in one scheduled invocation,
retaining one (local metadata: 120,000 rows_written, 480,088 rows_read).

### Stage 2: purpose abuse limits (C2)

The Worker uses [Cloudflare Workers Rate Limiting bindings](https://developers.cloudflare.com/workers/runtime-apis/bindings/rate-limit/).
All periods are 60 seconds; each row below has its own IP namespace and, where
applicable, a separate authenticated-actor namespace. Namespace IDs 260501–260509
are proposed for this Worker and must not be reused by other deployments/purposes.
Account-wide uniqueness is **unverified**: Wrangler 4.98.0 `whoami` confirmed
account access, but its CLI exposes no account-wide Worker/rate-limit-namespace
inventory command. Local configuration contains nine distinct IDs; this does
not prove absence of collisions with other Workers. The operator must check
account-wide bindings before production stage 2. `wrangler.toml` is the budget authority.

| Purpose | Routes | Per IP/minute | Per authenticated key/minute |
| --- | --- | ---: | ---: |
| AUTH | guest, recovery, native/web Google start and exchange/complete | 10 | — |
| GENERAL | bootstrap, bookmarks, registration, profile/account operations, unknown APIs | 120 | 60 |
| OWNER_GRANT | POST `/v1/servers/:id/connect-grants` | 60 | 12 |
| OWNER_REDEEM | POST `/v1/servers/:id/connect-grants/redeem` | 120 | 60 |
| ENDPOINT | PUT/DELETE endpoint and POST endpoint/renew | 120 | 6 |

The IP gate runs before body parsing, signature verification, D1 reads, precision
counters and nonce insertion. It uses only Cloudflare's `CF-Connecting-IP`;
`X-Forwarded-For` cannot rotate its key. Missing edge IPs share `unknown` (including
local development). Public static assets, health/config and CORS preflight have
no D1 work and bypass these gates. Existing D1 precision limits, including Google
start's 20/hour, remain after the coarse gate.

After successful device proof, account (`person_id`), session and device each
have an independent key in that purpose's actor namespace. Grant issuance also
checks current server ownership before charging the server key. A signed
non-owner targeting somebody else's grant route cannot spend that owner's
server actor budget or write a nonce; it still spends its own authenticated
account/session/device allowance before the ownership read. Host proof gates the host-key fingerprint
and server ID independently before `host_request_nonces`. Claimed IDs or invalid
signatures never charge an authenticated subject. Current SQL checks at actual
grant insertion/redemption remain authoritative against racing logout, owner
transfer and endpoint changes.

A denial returns the existing JSON error envelope with 429 `rate_limited`.
A missing binding, exception or malformed limiter result returns 503
`abuse_limiter_unavailable`, with no D1 fallback and no nonce/counter write.
Only the selected purpose fails closed. Owner issue, redeem and endpoint never
call AUTH/GENERAL bindings, nor one another's bindings. There is currently no
member admission API: signed bookmark activity and unimplemented member API
paths use GENERAL. A future member admission route must stay outside all three
owner purposes and preserve these isolation tests. General activity can exhaust
its own bootstrap/bookmark allowance; it cannot exhaust an owner API allowance.
Attackers directly targeting an owner API can still exhaust its IP allowance,
including for legitimate clients behind the same NAT.

These are per-Cloudflare-location, eventually consistent abuse limits, not a
global quota reservation or exact billing counter. Rotating IPs/locations and
aggregate traffic can still exhaust shared Worker/D1 daily quotas; exhausting
those platform quotas affects all purposes. No application binding can prevent
the Worker invocation itself from counting. The free-plan operating budget here
is 100,000 Worker requests/day, 100,000 D1 rows written/day and 10 ms CPU/request;
this change does not claim to prove the production CPU ceiling. The unchanged
0.1.x host retry loop can settle at about 32 seconds during failure; ENDPOINT's
6/minute host/server budget admits that steady retry cadence, while bounding
bursts. No Rust client or retry policy was changed.

#### Grant retry decision

Issuance does not reuse an existing row, even for the same session/device/server/
origin/generation. Only `secret_hash` is persisted, so the Worker cannot recover
and return the existing token. Storing plaintext, reversibly encrypted tokens or
an isolate-local token cache would introduce secret custody and inconsistent
retry behavior; none is added. Clients already holding a valid token may retry
redemption through the existing atomic authority check until expiry.

Instead, preserve the atomic maximum of 16 unexpired grants per session across
all servers, with a maximum 300-second TTL also clipped to session/endpoint
expiry. Add the OWNER_GRANT 12/minute per account/session/device/server allowance
and 60/minute IP allowance. The 16-row bound remains exact under concurrent
issuance even if the approximate rate limiter overshoots or traffic moves
between Cloudflare locations. Reaching that bound retains 409
`connect_grant_capacity`; successful token shape, status and response fields
remain unchanged. Expired rows are excluded from capacity without hot-path
cleanup.


Stage 2 retains the single daily cron and the stage-1 cleanup capacity. It adds
only the purpose limiter integration/bindings and their tests. Do not deploy it
until the stage-1 Worker version has been recorded and verified. Rollback target
is that version of `d54aebc7`, never an earlier commit.

Run the local abuse integration check as well:

```sh
node test/local_abuse.mjs "$(npm root -g)/wrangler/package.json"
```

Stage-2 local verification: 62 tests, syntax check and deployment dry-run pass.
The workerd/D1 owner smoke passes; 220 requests blocked across member-path,
signed grant and login abuse write zero rows after allowances are exhausted.
Owner issuance, redemption and endpoint renewal still work after GENERAL
saturation. Cleanup removes all 200 expired grants in the abuse fixture
(200 rows_written, 825 rows_read), and the separate daily backlog probe again
removes 120,000 of 120,001 expired counters. These are local measurements;
production CPU time and account-wide namespace uniqueness remain unverified.
