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

For an existing deployment, follow the deployment runbook below. Deploy the corrected tip directly;
record the production Worker version corresponding to `25fad46a` for rollback.

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

Local verification applies all ten migrations to isolated workerd D1 and runs
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

## Cleanup and abuse rollout

Do not deploy `d54aebc7` or `8e2bfb37`: their scheduled cleanup is unsafe.
Deploy the owner-budget-corrected successor of `e8c62941` directly. No history
is rewritten. Git hashes are not Cloudflare Worker version IDs; record the
actual `25fad46a` production version separately for direct rollback.

### Shared UTC-day cleanup and creation budgets

The [D1 Free allowance](https://developers.cloudflare.com/d1/platform/pricing/)
is 100,000 rows written/day across the account; index maintenance also counts.
Cleanup reserves at most **10,000 writes/day (10%)**, leaving at least 90,000
of that allowance for other work. This is not a guarantee of remaining account
quota: other databases, normal requests, cascades and migrations also consume it.

Migration `0010_daily_maintenance_budget.sql` adds a cleanup singleton, five fixed creation-budget rows,
nonce-purpose columns and six atomic insert-admission triggers. Cleanup claims the UTC day with one
conditional UPDATE. Concurrent calls, repeated cron delivery, isolate restarts
and retries cannot reserve it twice. Failure burns the unused daily allowance;
there is no same-day retry/reclaim. Missing `meta.rows_written` stops the run.
The SQL day guard prevents a delayed invocation from starting deletes on a later
UTC day. Keep the single `17 3 * * *` cron; do not increase its frequency.

Each chunk selects at most 100 rows. All queues share the same remaining budget,
charged at `max(meta.rows_written, deleted rows × schema write cost)`. The schema
floor is necessary because local workerd reports logical deletes without index
maintenance. Before each DELETE its LIMIT is reduced to fit the remaining
worst-case cost, so the final chunk cannot overshoot. Session deletion excludes
all nonce/grant parents before LIMIT; foreign-key cascades cannot bypass this
bound. Nonempty queues rotate; empty queues stop consuming query slots.

| Queue | Table + maximum index writes per deleted row |
| --- | ---: |
| Device nonces | 3 |
| Host nonces | 3 |
| Precision rate counters | 3 |
| Google handoffs | 3 |
| Connect grants | 5 |
| Sessions (including revoked partial index) | 7 |

There are at most 49 D1 statements (one claim + 48 chunks), within the
[50-query Free invocation limit](https://developers.cloudflare.com/d1/platform/limits/).
With eligible backlog and the current schema, at least 9,993 deletion writes
can be used after the one-write day claim before the remaining budget cannot fit a row. Even the cheapest
3-write rows require only 34 full/partial chunks; six queue exhaustion checks
still fit the statement cap. SQL/time/quota failures can reduce that throughput.
Changing indexes, triggers or cascades requires rechecking these costs and the
all-queue regression. Do not refund the ledger or manually invoke old cleanup.

Per-IP/per-actor limiters alone cannot match that capacity: one location can
admit 12 grant requests/minute = 17,280/day (before the separate 16-active-grant
constraint), or 6 endpoint calls/minute = 8,640 host nonces/day = **25,920 cleanup
writes**. Accounts, IPs and locations multiply these rates. Lowering only an
individual account limit would not bound total generation.

The database-wide admission ceiling remains **8,000 eventual cleanup writes/UTC
day**, partitioned into non-borrowing purpose pools:

| Purpose | Daily deletion-write budget | Charged work |
| --- | ---: | --- |
| AUTH | 1,400 | Precision counters, Google handoffs (3 each), sessions (7) |
| GENERAL | 1,400 | Other signed device/host nonces, including ownership claims (3) |
| ENDPOINT | 3,000 | Endpoint publish/renew/delete host nonces (3) |
| OWNER_GRANT | 1,600 | Verified owner request nonce (3) and grant row (5) |
| OWNER_REDEEM | 600 | Signed grant-redemption host nonce (3) |
| Total | **8,000** | Below >=9,993/day cleanup capacity |

Only the server-derived exact method/path selects nonce purpose, after signature
verification and the existing purpose gates; grant requests also verify ownership
before nonce insertion. Headers/body fields cannot choose a pool. Nonce uniqueness
still spans all purposes. Unrelated activity cannot spend either owner reserve.
Each INSERT attempt, including an UPSERT, charges its weight atomically; failed
statements roll back their reservation. A nonce already committed before a later
validation/grant failure is still charged. The five ledger rows never expire or
grow; each admitted attempt adds one counter UPDATE (at most 2,666/day).
Total successful expiry debt stays <=8,000/day, leaving >=1,993/day for backlog
when cleanup succeeds. Long-lived rows and failed runs still delay reclamation.

With no retries or failed attempts, the owner pools support **200 grant issuances
and 200 redemptions/day** across the entire directory. ENDPOINT supports 1,000
calls/day: a host renewing every five minutes uses 864 units, so three such hosts
fit before other endpoint calls; four do not. Existing burst limits, endpoint
leases and the 16-active-grant/session constraint still apply.

**Explicit product constraint:** the reserves are finite and shared among owners.
Exhausting OWNER_GRANT or OWNER_REDEEM returns HTTP 429
`temporary_capacity_exhausted` until 00:00 UTC, even for a legitimate owner.
There is no borrowing, refund, or unlimited-entry guarantee. Isolation protects
grant/redeem for an already valid session and live endpoint; exhausted AUTH can
block a fresh login, exhausted ENDPOINT can prevent lease renewal, and platform
account-wide quotas can stop all purposes. Rejected inserts persist neither the
row nor their counter increment.

### 배포 런북

This is an operator runbook only; this change performs no production deployment
or remote migration. Run the commands from the exact reviewed, owner-budget-fixed
successor of `e8c62941` in `infra/identity-directory`. Do not use a moving/unreviewed
HEAD. Set `IDENTITY_ASSETS` to its matching, already-built frontend assets; these
commands do not build or modify the Rust checkout.

1. Record the production Worker version ID corresponding to **`25fad46a`**,
   applied migrations **0001–0008**, account-wide D1 usage and existing cron.
   Confirm namespace IDs **260501–260509** do not share another Worker's counters.
   Use `wrangler deployments list`, `wrangler versions list` and
   `wrangler versions view <version-id>` plus deployment records to establish the
   Git/version mapping. Stop if that rollback version cannot be identified.
2. Outside the **03:17 UTC** window, disable cron with **`crons = []`**. Create
   two configs beside the original so relative source/migration paths
   remain valid. Both include the assets directory because even trigger-only
   commands validate the assets configuration:

   ```sh
   export IDENTITY_ASSETS=/absolute/path/to/reviewed/frontend/dist
   export IDENTITY_ROLLBACK_VERSION=recorded-25fad46a-worker-version-id
   python3 - <<'PY'
   from pathlib import Path
   import json, os
   source = Path('wrangler.toml').read_text()
   assets = Path(os.environ['IDENTITY_ASSETS']).resolve(strict=True)
   assert assets.is_dir()
   assert source.count('[assets]') == 1
   source = source.replace('[assets]', '[assets]\ndirectory = ' + json.dumps(str(assets)))
   cron = 'crons = ["17 3 * * *"]'
   assert source.count(cron) == 1
   with Path('wrangler.cleanup-on.toml').open('x') as output:
       output.write(source)
   with Path('wrangler.cleanup-off.toml').open('x') as output:
       output.write(source.replace(cron, 'crons = []'))
   PY
   wrangler triggers deploy --config wrangler.cleanup-off.toml --dry-run
   wrangler triggers deploy --config wrangler.cleanup-off.toml
   ```

   `triggers deploy` updates schedules without deploying new Worker code. Verify
   the config preserves the current production routes/domains before applying it;
   that command can manage those too. Confirm no cron remains in the dashboard,
   wait the **full 15 minutes** for propagation, then confirm in Workers invocation
   logs/metrics that no scheduled invocation is still active. Wait for completion
   if one remains; do not proceed merely because the timer elapsed. See
   [Cron Trigger propagation](https://developers.cloudflare.com/workers/configuration/cron-triggers/).
3. Confirm only **0009 and 0010** are pending and that the account has headroom
   for 0009's two session-index builds (plus 0010's additive schema work):

   ```sh
   wrangler d1 migrations list agentsassemble-identity --remote --config wrangler.cleanup-off.toml
   ```

   0010 was not applied in production and is corrected in this release. Stop if
   an earlier 0010 is already recorded; do not reapply or overwrite its history.
4. Apply **0009, then 0010**. With exactly those two pending, Wrangler applies
   them in order; inspect the prompt before confirming:

   ```sh
   wrangler d1 migrations apply agentsassemble-identity --remote --config wrangler.cleanup-off.toml
   wrangler d1 migrations list agentsassemble-identity --remote --config wrangler.cleanup-off.toml
   ```

   A [failed migration rolls back](https://developers.cloudflare.com/d1/wrangler-commands/),
   while earlier successful migrations remain applied. Stop on failure.
5. Deploy the **corrected tip directly**, with cron still off, matching assets,
   and all nine verified limiter bindings. **Never deploy `d54aebc7` or `8e2bfb37`.**

   ```sh
   wrangler deploy --dry-run --config wrangler.cleanup-off.toml --assets "$IDENTITY_ASSETS"
   wrangler deploy --config wrangler.cleanup-off.toml --assets "$IDENTITY_ASSETS"
   ```

6. Restore **exactly one `17 3 * * *`** trigger with the generated on config:

   ```sh
   wrangler triggers deploy --config wrangler.cleanup-on.toml
   ```

   Confirm the resulting schedule, smoke-test login and owner grant/redeem (also
   endpoint renewal), and verify the next scheduled run stays within **10,000
   indexed writes**. Local logical-delete metadata alone is not billing evidence.
   Do not increase cron frequency or manually clear the daily claim.
7. If rollback is needed, disable cron again, wait **15 minutes plus completion
   of any active invocation**, then roll back **directly to recorded `25fad46a`**:

   ```sh
   wrangler triggers deploy --config wrangler.cleanup-off.toml
   # Confirm no triggers; wait 15 minutes and confirm no active scheduled run.
   wrangler rollback "$IDENTITY_ROLLBACK_VERSION" --config wrangler.cleanup-off.toml
   ```

   Retain **0009/0010**, and keep cleanup **disabled for the entire time `25fad46a`
   is active**. Cron configuration is separate from code rollback: recheck that
   it remains empty. Old code defaults nonce spending to GENERAL and may report
   capacity denial as 409/500; owner isolation is guaranteed only by the fixed
   release. Smoke-test below capacity, restore the budget-fixed release with the
   off config, then re-enable only the original cron. **No `d54aebc7` intermediate
   hop.** A future limiter-free rollback build must include corrected cleanup
   and be separately verified; the historical unsafe versions are not one.

Keep both local configs for the rollout/rollback window; do not commit them.
Only `wrangler.cleanup-on.toml` re-enables cleanup. All remote commands above are operator
steps, not part of local verification.

### Verification

```sh
npm test
npm run check
wrangler deploy --dry-run --assets "$RUST_CHECKOUT/frontend/dist"
node test/local_cleanup.mjs "$(npm root -g)/wrangler/package.json"
node test/local_abuse.mjs "$(npm root -g)/wrangler/package.json"
```

Budget regression against the original cleanup failed after 120,999 logical
changes. The workerd/D1 all-queue regression also fails its 10,000-write bound
with the original cleanup. With the fix, 2,533 logical deletes + one claim report
**2,534 local rows_written; 10,000 including schema index costs**. Two concurrent calls admit only one cleanup; three same-day
retries each write zero; a retained previous-day claim allows new progress.
The local runtime undercounts index writes, so 2,534 is not a production billing
estimate. Live nonce children and their expired parent survive; all six expired
queues make progress. The creation-cap regression observes HTTP 429 and no
persisted nonce; temporarily raising the SQL ceiling to 800,000 makes it fail
with HTTP 200. The mutation is restored, not committed.

64 Node tests, syntax check and Wrangler 4.98.0 asset-backed dry-run pass.
The new signed-HTTP regression failed against the original shared ledger at
owner issuance (429 instead of 201); it now exhausts AUTH/GENERAL/ENDPOINT and
successfully issues and redeems a new owner grant. It also checks that each owner
reserve eventually denies with 429. Local workerd/D1 reproduces non-owner pool
exhaustion and owner success. Temporarily routing the device or host nonce back
to GENERAL makes workerd fail at issue (429/201) or redeem (429/200), respectively;
all mutations were restored. Cron-off and cron-on trigger dry-runs also pass.
Bare `wrangler deploy --dry-run` fails without the required assets directory;
provide `--assets` as above. The generated trigger configs include that directory
because `triggers deploy` validates it too. Local owner
entry/retry, endpoint generation/renewal and logout smoke pass. An isolated
workerd build of `25fad46a` also passes that owner smoke with corrected
0009/0010 retained and no scheduled cleanup invocation. The separate
abuse integration still records zero writes for 220 coarse-limit denials.
Local workerd tests do not establish production CPU, quota availability or
account-wide rate-limit namespace uniqueness. Rust source/build outputs are
read-only inputs to dry-run, and no Rust repository is modified.

### Purpose abuse limits (C2)

The Worker uses [Cloudflare Workers Rate Limiting bindings](https://developers.cloudflare.com/workers/runtime-apis/bindings/rate-limit/).
All periods are 60 seconds; each row below has its own IP namespace and, where
applicable, a separate authenticated-actor namespace. Namespace IDs 260501–260509
are proposed for this Worker and must not be reused by other deployments/purposes.
Account-wide uniqueness is **unverified**: Wrangler 4.98.0 `whoami` confirmed
account access, but its CLI exposes no account-wide Worker/rate-limit-namespace
inventory command. Local configuration contains nine distinct IDs; this does
not prove absence of collisions with other Workers. The operator must check
account-wide bindings before production deployment. `wrangler.toml` owns burst
limits; migration 0010 owns daily creation budgets.

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
its own coarse bootstrap/bookmark allowance without spending an owner coarse
allowance. Admitted activity uses the matching non-borrowing daily creation pool.
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
6/minute host/server budget admits that steady retry cadence while bounding
bursts, until the ENDPOINT daily creation allowance is exhausted. No Rust client or retry policy was changed.

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


The corrected release preserves all purpose limiters and bounded cleanup.
Follow the deployment runbook for direct deployment and direct `25fad46a` rollback
with cleanup disabled; never deploy the historical unsafe cleanup versions.

Run the local abuse integration check as well:

```sh
node test/local_abuse.mjs "$(npm root -g)/wrangler/package.json"
```

Current local abuse integration: 220 coarse-limit denials write zero rows.
Owner issuance and redemption work after GENERAL coarse-limiter saturation
and exhaustion of all three non-owner creation pools. The initial owner smoke
covers endpoint renewal before ENDPOINT exhaustion. Cleanup removes all 200
expired grants plus eligible authentication counters in that fixture.
See the all-queue, index-inclusive budget verification above for daily capacity.
