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
  server registrations, unless a retained relationship restricts deletion (C3a below).
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
record the rollback version under the C3a floor rules in that runbook.

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
npm run dry-run:ci
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
old images. Successful account/server deletion cascades the blob; a C3a RESTRICT
failure rolls back the whole deletion, including the blob. D1's delete change count
includes child rows, so registration deletion accepts a positive count rather than
misreporting a successful cascade as 404. Only the current canonical owner may edit,
even during a host ownership claim. No frontend source, room icon or host authority
changes are part of this feature.

## Cleanup and abuse rollout

Do not deploy `d54aebc7` or `8e2bfb37`: their scheduled cleanup is unsafe.
Deploy the owner-budget-corrected successor of `e8c62941` directly. No history
is rewritten. Git hashes are not Cloudflare Worker version IDs; record the
historical `25fad46a` production version separately for the cleanup rollout;
the C3a runbook below supersedes that rollback target.

### Shared UTC-day cleanup and creation budgets

The [D1 Free allowance](https://developers.cloudflare.com/d1/platform/pricing/)
is 100,000 rows written/day across the account; index maintenance also counts.
Cleanup reserves at most **10,000 writes/day (10%)**, leaving at least 90,000
of that allowance for other work. This is not a guarantee of remaining account
quota: other databases, normal requests, cascades and migrations also consume it.

Migration `0010_daily_maintenance_budget.sql` adds a cleanup singleton, six fixed creation-budget rows,
nonce/session-purpose columns and atomic admission triggers. This migration is
still pending production application; do not overwrite an already-applied 0010. Cleanup claims the UTC day with one
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
| AUTH | 700 | Google-verified person recovery/completion counters (3 each) and sessions (7) |
| ANONYMOUS | 700 | Guest creation/recovery including sessions, Google starts, invalid credentials; counters/handoffs (3), sessions (7) |
| GENERAL | 700 | Other signed device/host nonces, including ownership claims (3) |
| ENDPOINT | 4,800 | Accepted endpoint nonces (3) and daily server/host counters (3 each) |
| OWNER_GRANT | 800 | Verified owner request nonce (3) and grant row (5) |
| OWNER_REDEEM | 300 | Signed grant-redemption host nonce (3) |
| Total | **8,000** | Below >=9,993/day cleanup capacity |

Only the server-derived exact method/path selects nonce purpose, after signature
verification and the existing purpose gates; grant requests also verify ownership
before nonce insertion. Headers/body fields cannot choose a pool. Nonce uniqueness
still spans all purposes. Unrelated activity cannot spend either owner reserve.
New expiring rows charge their weight atomically; precision-counter UPSERT
updates, including later rate-limit denials, create no new cleanup debt and do
not charge the shared pool. Failed statements roll back their reservation. Other-purpose
nonces committed before a later validation/grant failure are still charged.
ENDPOINT validates the body first, then batches both daily source reservations,
the guarded endpoint mutation and nonce insertion. A zero-row mutation aborts
via the nonce NOT NULL constraint, and replay/cap failures roll everything back.
The six global ledger rows never expire or grow. Total successful expiry debt
stays <=8,000/day, leaving >=1,993/day for backlog when cleanup succeeds.
Long-lived rows and failed runs still delay reclamation.

AUTH and ANONYMOUS each reserve cleanup units against separate durable
**200/source and 100/person per UTC day** caps. The trusted `CF-Connecting-IP`
header is normalized to an **IPv6 /64** network in both coarse and durable keys;
IPv4 addresses are unchanged. Equivalent IPv6 spellings share the same key.
Durable network keys are HMACs; missing/invalid IPv6 headers share `unknown`.
`X-Forwarded-For`, flow type, device IDs and rotated recovery codes cannot reset
the network allowance. Anonymous and verified precision/source counters have
separate namespaces, so a guest flood cannot consume the verified recovery
source allowance even behind the same IP.

Guest creation, guest recovery and their issued sessions, Google start/handoff
creation and invalid recovery/Google credentials spend only ANONYMOUS. A valid
recovery owner is resolved before any expiring write. Only the server-owned
`persons.identity_kind = 'google'`, established after Google token verification
(signature, issuer, audience, expiry and nonce), qualifies recovery for AUTH.
A self-minted guest code proves possession, not a verified identity; client input
cannot select this kind. Google completion and its session issuance also use
AUTH. No new pool, migration or increased budget is needed: both existing
700-unit pools remain finite aggregate backstops.
The shared per-minute AUTH edge gate still throttles anonymous and verified
traffic from the same network; these reservations isolate durable daily spending.

Source counters are UTC-day rows in `rate_limits`, use its existing bounded
cleanup queue, and include their own three-unit expiry cost in the selected
pool. Each protected auth write batches person then IP reservations with the
actual insert; any cap failure rolls back the entire batch, including new
source rows and global charges. A read before the batch estimates new-row debt;
racing first uses may conservatively reserve extra source units, but never extra
shared debt. A midnight race fails closed and the next request can retry in the
new UTC day. No permanent IP ledger is introduced.

ENDPOINT additionally admits at most **320 accepted calls per server ID AND per
host signing-key fingerprint per UTC day**, before spending shared nonce capacity.
The exact D1 counters are independent of edge location. Re-registering a deleted
server or reusing a key for another server cannot reset them. Their UTC-day rows
survive registration deletion, then expire through the existing cleanup queue.
Malformed JSON, invalid generation/origin/lease, stale generations, mismatched or
expired renewals, replay and capacity denial commit neither nonce nor endpoint
mutation nor daily reservation. The transaction rechecks the registered host key
and guards against a batch crossing UTC midnight.

With no retries or failed attempts, the owner pools support **100 grant issuances
and 100 redemptions/day**, hence **100 complete owner entries** across the entire
directory. ENDPOINT's 4,800 units cover nonces plus six counter units per distinct
server/key pair per day:

| Continuously online hosts | Five-minute calls/day | Cleanup units/day including counters | Shared pool spare calls/day |
| ---: | ---: | ---: | ---: |
| 3 | 864 | 2,610 | 730 |
| 4 | 1,152 | 3,480 | 440 |
| 5 | 1,440 | 4,350 | 150 |

The pool margin is also constrained by each server/key's 320-call ceiling:
288 scheduled calls leave **32 accepted extra calls per host**. Five hosts can
share the 150-call pool margin (30 each). Initial publication, shutdown and
accepted retries use this margin; invalid or stale requests do not. Existing
600-second host leases and the 16-active-grant/session constraint still apply.
**Follow-up:** increasing heartbeat and lease intervals together could reduce
daily endpoint debt and allow a different balance; that needs a separately
reviewed Rust host change. This release does not change the host cadence.

**Exhaustion experience:** each pool and durable source cap returns HTTP 429,
`temporary_capacity_exhausted`, with “Daily temporary storage capacity reached.
Retry after 00:00 UTC.” There is no borrowing, refund, or unlimited-entry guarantee.

- ANONYMOUS exhaustion blocks guest creation/recovery and new Google starts. AUTH
  exhaustion blocks verified recovery/Google session issuance. The screens
  display the capacity message. A spent daily source blocks only that pool
  for its network/person; unrelated sources may proceed while shared capacity
  remains. These daily allowances reset at UTC midnight.
- OWNER_GRANT/OWNER_REDEEM callers receive the JSON error. This checkout has no
  integrated connect-grant frontend consumer. Retries and failed signed requests
  reduce practical capacity below 100 entries.
- ENDPOINT hosts currently log only `HTTP 429` and retry with backoff. After the
  last 600-second lease expires, they appear offline until a successful retry
  after UTC midnight. Reserved owner grants still need a live endpoint.
- GENERAL exhaustion during remembered-session startup may fall back silently
  or display cached data with a generic central-connectivity warning rather
  than the capacity message.

Isolation protects grant/redeem for an already valid session and live endpoint;
platform account-wide quotas can still stop all purposes. Rejected inserts
persist neither the row nor their counter increment.

### 감수하는 제품 제약

무료 플랜의 유한한 하루 몫은 많은 IP(/64 네트워크 포함)와 계정을 가진
공격자에게 고갈될 수 있다. 그 경우 해당 몫을 쓰는 새 로그인·게스트 생성은
UTC 자정까지 실패한다. IPv6 정규화와 익명/검증 예산 분리는 공격 비용을
높이고 피해 범위를 제한하지만 사람 확인이나 Sybil 공격 방지를 보장하지 않는다.
스스로 만든 게스트는 유효한 복구 코드를 제시해도 검증된 사용자가 아니다.
게스트 생성·복구·세션 발급은 ANONYMOUS 몫을 공유하며, 공격으로 이 몫이
소진되면 정상 게스트의 복구도 그날 UTC 자정까지 실패할 수 있다.
Google의 새 로그인 시작도 같은 제약을 받는다. 이미 시작한 handoff의
Google 검증·로그인 완료와 검증된 person의 복구·세션 발급은 AUTH에 격리된다.
Google 계정 자체의 대량 확보까지 방지하는 사람 확인 수단은 아니다.

ANONYMOUS/AUTH 고갈은 이미 방에 연결된 세션과 소유자 입장 예약 몫
(OWNER_GRANT/OWNER_REDEEM)에 영향을 주지 않는다. 소유자의 새 입장에는
여전히 유효한 세션과 살아 있는 endpoint가 필요하다. ENDPOINT나 Cloudflare
계정 전체 할당량의 고갈은 별도 장애이며, 모든 연결 가능성을 보장하지 않는다.

후속 대책은 [Cloudflare Turnstile 무료 사람 확인](https://developers.cloudflare.com/turnstile/plans/),
커스텀 도메인의 WAF 정책, 실제 사용량에 맞는 유료 플랜이다. 이번 변경에서는
이 대책을 활성화하거나 운영 배포하지 않는다.

### Member binding compatibility floor (C3a)

C3a has no migration or terminal server-ID blocking. Account/server deletion
remains physical when no retained relationship exists; an FK RESTRICT failure
returns 409 `deletion_restricted` and rolls back the whole DELETE/cascade.
Successful deletion accepts `changes >= 1`, including FK cascade and trigger writes.

C3c-1a introduces a fresh random registration epoch for each new registration.
Central relationships bind to `server_incarnation = (server_id, host-key fingerprint,
non-reusable registration epoch)`: a deleted `server_id` may register again, but
its new incarnation must inherit no previous relationship, grant, or enrollment.

C3b/C3c retained member relationships referencing persons, servers, or cascade
children such as `person_servers` must use `ON DELETE RESTRICT`.
Member-owned records must not hold FKs to cleanup-owned `sessions` or
`server_connect_grants`; copy required provenance as values instead (M2).
If a future design retains revoked server rows, all server-limit checks must
count only live rows; C3a does not retain revoked rows (M3).

Local verification: `npm test`, `node test/local_migrations.mjs /path/to/wrangler/package.json`,
and `node test/local_member_floor.mjs /path/to/wrangler/package.json` exercise
signed deletion/re-registration, future RESTRICT fixtures, atomic rollback and
real local D1 deletion metadata. C3a added no member table, route, UI, or epoch.

### Server incarnation epoch: C3c-1a expand

Migration **0011_server_registration_epoch.sql** adds `servers.registration_epoch`
and backfills existing rows once with `lower(hex(randomblob(16)))` (128 random bits).
The Worker supplies a 128-bit Web Crypto random value, encoded as unpadded base64url,
on a new-row INSERT. Treat the returned value as opaque: backfilled hex and new
base64url epochs are both valid. Idempotent registration, metadata updates and
ownership claims preserve the stored epoch. A losing concurrent INSERT discards
its candidate and returns the stored winner's epoch; it never rotates the row.
Registration/claim responses and each bootstrap server projection expose
`registration_epoch`.

During the migration/deployment gap or rollback, the compatibility BEFORE INSERT
trigger replaces an epoch-less legacy INSERT with one random-epoch INSERT and
uses `RAISE(IGNORE)` to skip the original. It performs no follow-up UPDATE and
does not increase D1 `rows_written` (verified on local D1). Legacy C3a registration
does not consume the direct INSERT change count or RETURNING result; the following
owner-relationship statement still executes in the same batch. New Worker INSERTs
already supply epoch and bypass this trigger. Preserve this compatibility path
until a later contract release retires old writers; do not repurpose it for
INSERT consumers that require RETURNING.

For subsequent server mutations, send the observed epoch as the top-level JSON
string field `registration_epoch`, including the JSON body of server DELETE.
This applies to registration metadata/ownership claim, endpoint publish, renew,
offline, connect-grant creation/redemption, name and icon changes, bookmark
creation/deletion, and server deletion.
Initial registration omits the field because only Central allocates the epoch.
An explicit null/empty value is invalid, not a legacy request. A stale epoch
returns **409 `incarnation_conflict`**; refresh bootstrap and discard the stale
operation rather than retargeting it to the replacement incarnation.

Canonical transcripts use LF separators, no trailing LF, and UTF-8:

```text
Host endpoint/renew/offline/redeem (unchanged AA-HOST-1):
AA-HOST-1\nMETHOD\npathname\ntimestamp\nnonce\nbase64url(SHA256(raw_body))

Device-signed owner requests (unchanged AA-DEVICE-1):
AA-DEVICE-1\nMETHOD\npathname\ntimestamp\nnonce\nbase64url(SHA256(raw_body))\nbase64url(SHA256(token))\ndevice_id

Registration proof when registration_epoch is supplied:
AA-HOST-REGISTER-2\nserver_id\nowner_person_id\nissued_at\nnonce\nregistration_epoch

Ownership-claim proof when registration_epoch is supplied:
AA-HOST-CLAIM-2\nserver_id\nowner_person_id\nissued_at\nnonce\nregistration_epoch
```

For host/device requests, `raw_body` includes `registration_epoch`: its existing
body digest signs the epoch without another header or signature operation.
The registration proof is nested in that device-signed body and independently
binds the top-level epoch in its v2 host transcript. Without the field, registration
proofs retain the v1 prefix and omit the final epoch line. HTTP methods are uppercase;
hashes and signatures remain unpadded base64url with the existing key algorithms.

The authority SQL writes include the supplied epoch, including related claim/icon
writes and host nonce admission. A pre-read is not the fence: a replacement between
verification and mutation cannot pass the SQL predicate. Existing dependent rows
(endpoints, grants, host nonces, directory relationships and icons) are still
deleted through their server FK cascades. Rate-limit buckets remain keyed by
server ID/fingerprint and survive server deletion; epoch does not reset a budget.
No additional request, polling, dependency, signature verification or steady-state
D1 write is introduced. Production CPU time is not established by local tests.

**Expand only:** omitted epochs retain the old request behavior, including its
old-incarnation replay exposure. This is not the member-data rollout barrier.
Next deploy epoch-aware hosts/clients, then perform a separately reviewed contract
change requiring epoch at every applicable authority boundary. Member binding,
enrollment, synchronization/cursors and retained relationship schemas remain out
of scope; their contracts must bind the full incarnation before rollout.

Local verification adds `test/server_epoch.test.mjs`,
`test/epoch_migration.test.mjs`, and
`node test/local_epoch.mjs /path/to/wrangler/package.json` (isolated Miniflare D1).
The regression oracles cover delayed DELETE, exact endpoint replay, all mutation
fences, legacy workflows, preserved epochs, concurrent registration and backfill.

### 배포 런북

- **C3c-1a: migration 0011 first, then this Worker code.** The new code directly
  selects/writes the new column and is not safe on a pre-0011 database. The schema
  addition and legacy INSERT trigger preserve old Worker registration during the
  gap without an extra write. Confirm 0001–0010 are already applied.
- Record the full deployed Worker version corresponding to **`8c190b48`** (C3a,
  Git `79a181cd`) as the rollback target. Roll back code only; retain 0011 and its
  assigned epochs. A code rollback restores legacy behavior and removes the new
  enforcement, so do not roll out member data or contract while rolled back.
- After deploying, verify legacy registration/bootstrap and epoch-aware mutation
  responses, and record the new Worker version. Host rollout and contract follow
  in separate reviewed releases (expand → deploy → contract).
- No deployment or remote migration is performed by this implementation. The
  historical cleanup commands below are not the C3c-1a deployment procedure.

#### Historical cleanup rollout (0009/0010; not the C3a deployment procedure)

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
7. For this historical cleanup rollout, if rollback is needed, disable cron again, wait **15 minutes plus completion
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
npm run dry-run:ci
wrangler deploy --dry-run --assets "$RUST_CHECKOUT/frontend/dist"
node test/local_cleanup.mjs "$(npm root -g)/wrangler/package.json"
node test/local_abuse.mjs "$(npm root -g)/wrangler/package.json"
node test/local_auth_capacity.mjs "$(npm root -g)/wrangler/package.json"
node test/local_admission_isolation.mjs "$(npm root -g)/wrangler/package.json"
```

Migration 0010 SQL compatibility verification (Wrangler 4.98.0): the installed
`wrangler-dist/cli.js` exports `unstable_splitSqlQuery`. The original SQL splits
into 10 chunks instead of 17: `END)` does not match its compound-statement end
rule (`\sEND[;\s]$`), merging the last eight triggers. Local D1 accepts those
multi-statement chunks, masking this problem. The remote migration path instead
sends the entire SQL plus its migration-record INSERT to D1's query API; it does
not call this client splitter. Therefore the reported remote `incomplete input`
is consistent with SQL parsing incompatibility, but a remote server-side
`CASE ... END;` split has not been directly established.

0010 now uses `SELECT RAISE(...) WHERE ...` and separates remaining CASE tokens
from parentheses, leaving `END;` only at trigger boundaries. All ten triggers
split independently (17 statements total). Migrations 0001–0009 have no CASE
expressions; their only trigger is 0002's plain `SELECT RAISE(...)` guard and
they are unchanged. No production migration or remote command was run.

Run the additional splitter regression with the same installed Wrangler package
used by the other local workerd checks:

```sh
node test/local_migrations.mjs /path/to/node_modules/wrangler/package.json
```

It uses the exported splitter without depending on private bundle layout. Each
chunk is prepared separately in SQLite, then source rejection, budget charges,
failed-write rollback and the complete resulting schema are checked. The test
failed on the original 0010 because an over-limit source INSERT was accepted;
it passes after the SQL change. This is a separate local check, not part of the
77-test Node suite. The local cleanup, abuse, admission-isolation and full-day
auth-capacity workerd/D1 regressions also pass with the changed migration.

Current revision: **77/77 Node tests**, syntax check and `npm run dry-run:ci`
pass. CI generates a temporary config beside `wrangler.toml` that omits only
`[assets]`, preserves relative Worker/D1 paths, bundles with `--dry-run`, and then
removes the config. This proves Worker/config validity, not the separately built
Rust frontend assets; production must still supply the matching `--assets` path.
No Rust checkout is read or changed by this CI check.

The guest-recovery Sybil regression creates 24 guests through HTTP and spends
their returned recovery codes from distinct /64s until guest recovery receives
`429 temporary_capacity_exhausted`. AUTH debt stays unchanged; an unrelated
Google-verified person's recovery and already-started Google login complete,
and both new sessions authenticate signed bootstrap requests. Restoring the
c62bdd27 unconditional recovery promotion fails this regression. The existing
76 tests remain, with verified-owner fixtures now established by Google token
verification instead of guest creation.

Local workerd/D1 (Wrangler 4.98.0) repeats the attack: 24 guests, 16 successful
recoveries, 8 capacity denials, and verified login/recovery both HTTP 200.
Google token exchange uses a locally signed fixture, not a live Google account;
signature/issuer/audience/expiry/nonce verification executes in the Worker.
There is no public Google recovery-code issuance endpoint: the test seeds only
a recovery credential for the person created by that verified login. This
validates existing recovery handling without introducing an issuance feature.

The prior seven admission regressions failed on 851a6b50 and pass after the fix:
IPv6 /64 rotation at coarse/D1 boundaries; guest and both Google-start floods
preserving verified recovery/session issuance; exact host/server daily admission;
and invalid/stale/replayed endpoint transaction rollback. Five normal hosts still
complete 1,440 scheduled calls, 150 spare calls and 100 complete owner entries.
Local workerd/D1 also reproduces /64 throttling, anonymous exhaustion followed by
successful recovery, 100 invalid endpoints leaving no state, and one host
stopping at **320 calls / 966 units** while an unrelated host remains online.
The 966 units include two three-unit counters and 320 three-unit nonces.
Full-day workerd checks measure **198 anonymous source units** over 14,400
attempts and **99 AUTH person units** over 1,440 attempts, preserving unrelated
login and rolling denied batches back. The runner reuses loopback connections
after Miniflare's per-request socket reset exhausted macOS ephemeral ports.
Removing either source cap, the midnight guard, either endpoint dimension, the
stale-mutation guard or guest-session pool isolation makes its regression fail;
reducing ENDPOINT to 3,000 fails the five-host day. All mutations were restored.
Cleanup and owner-isolation workerd checks pass. No production migration,
deployment or Rust change was made.

Historical verification (the previous 69-test revision): full-day regressions
covered 14,400 attempts from one IP, 1,440 rotating-IP recoveries for one person,
UTC reset/transaction rollover and five hosts with 160 spare calls before daily
endpoint source counters were added. Earlier mutation evidence below describes
that revision, not an additional current production check.

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

The earlier 64-test baseline, syntax check and Wrangler 4.98.0 asset-backed dry-run passed.
The new signed-HTTP regression failed against the original shared ledger at
owner issuance (429 instead of 201); it now exhausts AUTH/GENERAL/ENDPOINT and
successfully issues and redeems a new owner grant. It also checks that each owner
reserve eventually denies with 429. Local workerd/D1 reproduces non-owner pool
exhaustion and owner success. Temporarily routing the device or host nonce back
to GENERAL makes workerd fail at issue (429/201) or redeem (429/200), respectively;
all mutations were restored. Cron-off and cron-on trigger dry-runs also pass.
Bare `wrangler deploy --dry-run` fails without the required assets directory;
provide matching `--assets` as above, or use `npm run dry-run:ci` for the
Worker-only CI check. The generated trigger configs include that directory
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

The IP gate normalizes IPv6 to /64 (IPv4 unchanged) and runs before body parsing, signature verification, D1 reads, precision
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
Follow the C3a deployment runbook for code-only deployment and `e93c5c82` rollback.
The historical `25fad46a` cleanup rollback requires cleanup disabled; never deploy
the historical unsafe cleanup versions.

Run the local abuse integration check as well:

```sh
node test/local_abuse.mjs "$(npm root -g)/wrangler/package.json"
node test/local_auth_capacity.mjs "$(npm root -g)/wrangler/package.json"
node test/local_admission_isolation.mjs "$(npm root -g)/wrangler/package.json"
```

Current local abuse integration: 220 coarse-limit denials write zero rows.
Owner issuance and redemption work after GENERAL coarse-limiter saturation
and exhaustion of all three non-owner creation pools. The initial owner smoke
covers endpoint renewal before ENDPOINT exhaustion. Cleanup removes all 200
expired grants plus eligible authentication counters in that fixture.
See the all-queue, index-inclusive budget verification above for daily capacity.

## W1/W2 member admission grants

`POST /v1/servers/{server_id}/member-preview` supplies the canonical target for
pre-consent display. It requires the same Bearer session and AA-DEVICE-1 signature
as member-grant issuance and uses GENERAL IP/account/session/device limits.
Request: `{"registration_epoch":"..."}` (required; no other fields).
HTTP 200: `{"server_id":"...","label":"...","endpoint_origin":"https://host.example","endpoint_generation":1}`.
A missing/revoked server or epoch mismatch returns 409 `incarnation_conflict`;
a missing/offline/expired endpoint returns 409 `server_endpoint_unavailable`.
Missing session or device proof returns 401; malformed input returns 400 and
request limits return 429. No grant, relationship or server state is changed;
standard signature replay protection still records its request nonce.
The preview is a snapshot, not admission authority; issuance/redeem recheck it.

`GET /member-join` (also `HEAD`) serves the shared SPA index on the central origin,
using the same self-only script/connect policy, `frame-ancestors 'none'`, and
`Cache-Control: no-store` as `/`. The frontend owns the consent UI.

`POST /v1/servers/{server_id}/member-grants` requires the existing central
session bearer and device signature. JSON body (both fields required):
`{"registration_epoch":"...","challenge_hash":"..."}`. The challenge hash is
the host challenge's SHA-256 encoded as unpadded base64url (43 characters).
Success is HTTP 201 with
`{"grant_token":"aamg1.…","server_id":"...","registration_epoch":"...","endpoint_origin":"https://host.example","endpoint_generation":1,"expires_at":123}`;
timestamps are Unix seconds. Lifetime is at most 300 seconds, clipped to the
central session deadline and endpoint lease. Active means unused and unexpired: the atomic caps
are 16 per person and 4 per session+server. A used or expired grant frees a slot.
The server registration must be live with the supplied epoch. Issuance atomically
selects and stores its online, unexpired endpoint. Missing, offline or expired
endpoints yield 409 `server_endpoint_unavailable`. Redemption rechecks the live
endpoint and requires its origin and generation to match the stored grant.

`POST /v1/servers/{server_id}/member-grants/redeem` requires the registered
Ed25519 host key. JSON body (all fields required):
`{"registration_epoch":"...","challenge_hash":"...","grant_token":"aamg1.…"}`.
Success is HTTP 200 with
`{"person_id":"...","issuer":"https://central.example","display_name":"..."}`.
The name is an issue-time snapshot capped at 80 Unicode characters. `issuer`
is the origin of the central redeem URL; hosts must pin their configured central
origin, use it consistently, and compare the returned issuer to it before binding.
This response proves identity only; the host still owns invite and room admission.

Signing uses UTF-8, newline-separated fields with no trailing newline. Hashes and
signatures are unpadded base64url; hash the exact transmitted JSON bytes, not a
re-serialized object. The epoch, challenge hash and token are therefore body-bound.

```text
Device (ECDSA P-256 / SHA-256):
AA-DEVICE-1\nPOST\n/v1/servers/{server_id}/member-grants\n{timestamp}\n{nonce}\n{SHA256(body)}\n{SHA256(session_token)}\n{device_id}
Host (Ed25519):
AA-HOST-1\nPOST\n/v1/servers/{server_id}/member-grants/redeem\n{timestamp}\n{nonce}\n{SHA256(body)}
```

Device headers: `Authorization: Bearer {session_token}`, `x-aa-device-id`,
`x-aa-timestamp`, `x-aa-nonce`, `x-aa-signature`. Host headers:
`x-aa-host-timestamp`, `x-aa-host-nonce`, `x-aa-host-signature`. Timestamps are
Unix seconds (300-second clock tolerance); nonces are 16–128 base64url characters
and must be fresh on each request. Both member routes use GENERAL limiters and
creation debt. Existing owner grants keep their prefix, routes and reusable
semantics, with explicit owner-kind predicates. The shared cleanup queue and
five-write grant cost remain unchanged; no indexes or free-tier limits increase.

Invalid issuance fields or a missing/invalid epoch yield 400; invalid redemption
fields, reused/expired grants or revoked identity yield 401; a missing/revoked
host registration may yield 404; an epoch conflict or
unavailable issuance yields 409 (`incarnation_conflict` / `member_grant_unavailable` / `server_endpoint_unavailable`).
Limits yield 429 (`rate_limited` / `temporary_capacity_exhausted`). A lost redeem
response requires a fresh host challenge and grant via explicit user retry.
No member list, membership projection, host binding transaction, or frontend flow
is implemented by W1.

Deployment runbook: apply expand-only migration `0012_member_grants.sql` after
0011, then deploy this Worker, then enable the host/frontend member flow.
Code rollback target is `a79140a9`; retain 0012 and its kind-aware trigger, and
disable member entry on clients. Old inserts still default to owner; do not down-migrate.

Verification: `npm test` covers member and existing owner cases;
`node test/local_member_grants.mjs /path/to/wrangler/package.json` exercises signed
HTTP on workerd/D1 (parallel caps, route binding, single-use, snapshot and logout).
`node test/local_migrations.mjs /path/to/wrangler/package.json` verifies the 4.98
SQL splitter. Pre-implementation member requests failed at HTTP 404. Controlled
removal of single-use, live-session, host-key, challenge, kind and cap predicates,
and changes to budget dispatch/default kind each made the behavioral tests fail;
all mutations were restored. No remote migration or deployment was performed.

## Profile-derived server names (2026-10-06)

The Rust host owns `{local operator profile name}의 {hardware model}` (unknown model:
`컴퓨터`). `servers.label` remains the automatic default; a nonempty owner
`person_servers.alias` remains an explicit fixed name, even if equal to a past
automatic name. Empty aliases migrate on the next host registration/name sync.
Ambiguous nonempty historical aliases are preserved, never guessed from text.
The existing POST `/v1/servers/:id/name` supports `reset_default: true` with the
same owner, epoch and observed-name guards, clearing only that owner's alias.
Bootstrap supplies owner-only `default_name` and `name_is_default`; member preview
uses the current owner's alias or host default, never the invite fragment.

Additive migration 0013 stores the latest host profile `name_revision`. New host
registration carries the optional revision; older registrations remain accepted
and cannot replace a revisioned default. Signed host PUT on the existing `/name`
resource updates only the default with a nondecreasing profile revision and exact
registration epoch. AA-HOST-1, nonce replay and GENERAL abuse limits apply; no new
authentication mechanism. Same revision must have the same name; stale writes fail.
The host serializes delivery, uses its existing directory change notification and
bounded retry policy, and retains the durable profile as retry source after restart.
Default labels may use up to 400 UTF-16 units to preserve the full profile/model;
manual aliases retain the existing 80-unit limit. No credentials or model serial
numbers are included. No deployment or production migration is authorized here.
