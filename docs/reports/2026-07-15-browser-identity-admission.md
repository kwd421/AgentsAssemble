# Browser Identity And Invite Admission Report

Status: implemented and verified

Date: 2026-07-15

Branch: `codex/risuai-character-personas`

Starting commit: `37b26d4`

Plan: `docs/plans/2026-07-15-browser-identity-admission.md`

## 1. Scope And Result

This slice fixes browser identity continuity around ordinary room invites and
public-origin operator access. It does not add account login.

The completed behavior is:

- the local operator is represented by one canonical user and participant;
- a valid stored room session survives opening another invite for that room;
- a known same-origin browser can reuse its server-owned profile;
- an unknown browser still receives the explicit guest profile form;
- a public-origin browser becomes the same operator only through a separate,
  short-lived, one-use pairing link;
- an ordinary guest invite never grants operator authority;
- failed or replayed pairing is visible and does not create a session.

The canonical room transport remains `/ws?ticket=...`. No second room event
store, provider-specific browser socket, polling path, or provider runtime was
added.

## 2. Corrected Baseline

The external review assumed the local host browser already resolved through
the identity database to `operator-local`. Static and local-state inspection
showed that assumption was false.

Before this slice:

- the trusted local WebSocket synthesized participant `operator-local`;
- `/api/host/claim` resolved each device credential to a newly created
  `guest-*` user and then promoted that row to operator;
- no identity row necessarily owned participant `operator-local`;
- `operator_user_id()` could follow the most recently seen legacy operator;
- room ownership could therefore drift across device-created operator rows;
- `createStartupRoute()` discarded a stored room session whenever a join token
  was present;
- the browser used a remembered local profile to decide whether to show the
  guest form, without a lookup-only server admission decision.

The development identity database inspected during investigation contained 43
users and 20 operator rows, with no user owning `operator-local`. Those counts
are evidence of the old behavior, not a migration invariant or production
assumption.

This finding changed the execution order: canonical operator identity had to
be fixed before adding cross-origin pairing. Building pairing on the old model
would have created one more privileged guest identity.

## 3. Implementation

### 3.1 Canonical local operator

`agentsassemble/identity_store.py` now defines one local operator:

```text
user_id        operator-local-user
participant_id operator-local
```

`claim_local_operator_credential()` runs inside one identity transaction. It:

- creates or updates the canonical operator row;
- attaches the host-authorized device credential to that row;
- removes operator authority from legacy operator rows without deleting them;
- migrates rooms owned by those legacy operator user IDs to the canonical
  operator user;
- rejects conflicting use of either canonical identifier.

The HTTP boundary still verifies the host token before this method is
reachable. Normal invite admission uses a different identity path and cannot
promote a device.

### 3.2 Side-effect-free invite admission

`RoomAdmissionService` owns the lookup decision used before the browser joins.
`POST /api/room-invite/admission` accepts the invite in its request body and the
device credential through `X-Device-Token`.

It can return:

- `existing_session`
- `existing_member`
- `known_user`
- `profile_required`
- `invite_invalid`
- `invite_expired`

The service validates the invite and projects safe room/profile fields. It
does not increment invite use, create users, issue sessions, or mutate
participants or memberships.

The browser now keeps a valid stored session even when a new invite URL is
opened. A known same-origin device joins with its current server profile. A
name remembered only in browser storage is a form prefill, not identity proof.

### 3.3 Explicit cross-origin operator pairing

`OperatorPairingService` and the `operator_pairings` identity table implement
the privileged cross-origin flow.

Creation requires moderator authority and a configured public URL. A pairing:

- is bound to one room and one normalized HTTP(S) target origin;
- has a 15-second minimum and 120-second maximum lifetime;
- is one-use, including under concurrent redemption;
- supersedes an older unused pairing for the same operator, room, and origin;
- persists only SHA-256 token fingerprints, never the raw token.

Redemption requires the browser device credential and exact target origin. If
an `Origin` header is present, the server compares it with the origin declared
in the body before redemption. Successful consumption atomically binds the
credential to `operator-local-user`, upserts canonical participant state, and
issues a bounded room bearer session for participant `operator-local`.

The pairing route is separate from `/join`. It never consumes a guest invite
and never puts the host token in the URL, response session, browser storage, or
public-origin request.

### 3.4 Frontend behavior

The invite modal now has two visibly separate actions:

- ordinary secure guest invite;
- `공개 주소에서 나로 열기` for the current operator only.

The pairing token is removed from the address immediately when `/pair` starts,
and the React state drops the raw token after success or failure. A successful
paired session is stored as `agentId: operator-local` and `operator: true`.

The first Playwright run exposed an error-display bug: after a replayed token
was removed from the URL, the pairing panel also disappeared. Rendering now
uses the pairing procedure state rather than raw-token presence, so
`pairing_already_used` remains visible while no session is created.

Invite creation uses the moderator request helper, not a host-token-only
helper. This is intentional: the local console authenticates with its host
token, while a correctly paired public browser authenticates with its bounded
operator bearer session. Both are checked by the same server moderation
boundary.

## 4. Security And Data Boundaries

Verified boundaries:

- raw host, device, pairing, invite, and session tokens are not returned in
  identity projections or room events;
- the raw pairing token is absent from `identity.db`;
- the pairing URL contains no host token;
- device credentials are sent in `X-Device-Token` for lookup and redemption;
- pairing creation is moderator-only;
- redemption is room-, origin-, expiry-, and one-use-bound;
- wrong-origin redemption does not consume the valid pairing;
- concurrent redemption admits exactly one browser;
- an ordinary guest credential cannot become operator;
- matching display names are not used to merge identities;
- no IP address, User-Agent, or browser fingerprint is used as identity proof.

The browser device token remains origin-scoped by Web Storage. Therefore
`127.0.0.1`, a tunnel origin, and another AgentsAssemble server are different
credentials until an explicit pairing is completed.

## 5. Intentional Deviations From The Review Or Initial Plan

### Canonicalization came first

The review's proposed admission flow assumed a canonical operator mapping that
did not exist. The implementation first repaired that authority boundary rather
than layering pairing over legacy `guest-*` operators.

### E2E uses `public.localhost`

The initial plan named `127.0.0.1` and `localhost` as the two browser origins.
The product intentionally rejects exact `localhost` as an externally shareable
invite URL. Weakening that security rule for a test would be wrong.

The E2E fixture therefore uses:

```text
host console   http://127.0.0.1:8898
public browser http://public.localhost:8898
```

Chromium resolves `public.localhost` to loopback, while it remains a distinct
origin and passes the external-link shape check. This tests the real
cross-origin browser path without opening a tunnel.

### No login system was added

Email/password, OAuth, account recovery, multi-server identity, and hosted
session synchronization remain out of scope by explicit product decision.
Existing guest invite sessions still exist; they are room-scoped admission,
not account login.

### No external-tunnel smoke was opened

The browser test exercises two origins and the real HTTP/WebSocket server on
loopback. It does not publish a Cloudflare tunnel or send credentials outside
the machine. That destructive/external side effect was neither necessary nor
authorized for this verification.

## 6. Commit Record

| Commit | Purpose |
| --- | --- |
| `26bfb81` | Record the corrected identity/admission plan. |
| `fe1ad53` | Introduce and migrate to the canonical local operator identity. |
| `5eb17cf` | Add lookup-only invite admission and behavior tests. |
| `4ad75e5` | Preserve valid browser sessions and auto-admit known devices. |
| `6fa1c61` | Add one-time, fingerprint-only operator pairing storage/service. |
| `2bb0c8f` | Connect `/pair` and moderator-capable public browser UI. |
| `12107b3` | Add two-origin Playwright coverage and retain visible replay errors. |
| `f4e943b` | Synchronize route ownership and React API inventory contracts. |
| `b1b19a9` | Remove a callback-identity loop that made a hook test flaky. |

The implementation was intentionally split by responsibility: plan,
identity authority, admission decision, browser behavior, pairing storage,
pairing UI, browser verification, route documentation, and test stability.

## 7. Verification

### Targeted backend

```text
python3 -m unittest tests.test_identity_store tests.test_host_account \
  tests.test_operator_pairing tests.test_room_admission \
  tests.test_public_invite_http
```

Result: 69 tests passed.

Additional route/inventory regression run:

```text
python3 -m unittest tests.test_gui_route_ownership \
  tests.test_legacy_react_parity_inventory \
  tests.test_static_ui_assets.StaticUiAssetTests.test_react_discord_room_sidebar_uses_real_invite_and_context_actions
```

Result: 12 tests passed.

### Full backend

```text
python3 -m unittest discover -s tests -t .
```

Result: 3,377 tests passed, 39 skipped, 399.907 seconds.

The suite still prints pre-existing `ResourceWarning` lines for some unclosed
test database handles and intentional cleanup-failure diagnostics. They do not
change the successful exit status and were not introduced or hidden by this
slice.

### Frontend unit tests

```text
npm --prefix frontend test
```

Result after stabilization: 21 files and 113 tests passed. The complete suite
was run three consecutive times with the same result and no unhandled errors.

An initial full run had 113 passing assertions but failed on a post-teardown
React scheduler error. The room-settings hook test passed newly allocated
`vi.fn()` callbacks on every render, repeatedly invalidating an effect. Commit
`b1b19a9` gives those callbacks stable identities; the failure was corrected,
not ignored or retried away.

### Build

```text
npm --prefix frontend run build
```

Result: passed. Vite reports the existing minified chunk-size warning for the
approximately 708KB main JS chunk.

### Browser E2E

```text
npm --prefix frontend run test:e2e
```

Result: 2 Playwright tests passed in 4.9 seconds.

The browser assertions cover:

1. host UI creates an ordinary invite and a separate operator pairing link;
2. a fresh public-origin context opening the ordinary invite sees the profile
   form;
3. a fresh public-origin context redeeming pairing receives participant
   `operator-local`, `operator: true`, and canonical room access;
4. the pairing query is removed from the browser URL;
5. a second fresh context replaying the link sees
   `pairing_already_used` and stores no room session;
6. desktop and mobile views control the same canonical Agent Session;
7. streaming, profile rename/avatar propagation, reload, pause/resume backlog,
   and room deletion remain covered by the existing canonical-room E2E.

The Agent Session used by this deterministic E2E is the repository's fake
interactive CLI fixture. This report does not mislabel it as a real provider
smoke; provider execution was outside this identity/admission slice.

### Repository hygiene

```text
git diff --check
```

Result: passed before each implementation commit and at final verification.

## 8. Remaining Limitations

- There is no account login, password, OAuth, recovery, or cross-server user
  identity.
- Pairing establishes trust only for one AgentsAssemble server and one browser
  origin.
- The paired bearer session is room-bounded; it is not a reusable global host
  credential.
- A real public tunnel/mobile-network smoke was not run in this slice.
- The main frontend bundle still exceeds Vite's 500KB warning threshold.
- Existing Python test cleanup warnings should be handled in a separate test
  hygiene change rather than mixed into identity authority code.

## 9. Reviewer Checklist

A reviewer should verify these invariants rather than only checking that a
link opens:

- `/api/host/claim` cannot create another operator participant;
- ordinary `/join` admission cannot call the privileged credential-claim path;
- preflight is byte-for-byte side-effect-free for invite state in its tests;
- pairing records contain fingerprints, not raw tokens;
- target-origin and one-use checks occur before credential attachment;
- paired browser authority comes from its bounded bearer session, never a host
  token copied to the public origin;
- replay/expiry/revocation are explicit failures with no fallback guest or
  operator session;
- login remains explicitly deferred.
