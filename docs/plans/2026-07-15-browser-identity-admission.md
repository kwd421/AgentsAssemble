# Browser Identity And Invite Admission

Status: implemented and verified

Created: 2026-07-15

Starting branch: `codex/risuai-character-personas`

Starting commit: `37b26d4`

Completed: 2026-07-15

Implementation commits:

- `fe1ad53` Canonicalize local operator identity
- `5eb17cf` Add side-effect-free invite admission
- `4ad75e5` Preserve browser identity during invite admission
- `6fa1c61` Add one-time operator origin pairing
- `2bb0c8f` Connect public browsers through operator pairing
- `12107b3` Verify cross-origin operator pairing
- `f4e943b` Document browser admission routes
- `b1b19a9` Stabilize room settings hook test

Completion evidence and intentional deviations are recorded in
`docs/reports/2026-07-15-browser-identity-admission.md`.

## Immediate Goal

Make browser invite admission preserve an already known local identity without
turning a normal guest invite into login or host authentication.

The user-visible acceptance contract is:

- opening an invite from the same origin and known device does not ask for the
  same profile again;
- an existing valid session for that room is reused without consuming the
  invite;
- the local server operator remains one canonical participant,
  `operator-local`, instead of accumulating `guest-*` operator identities;
- a different origin remains a different credential until the operator uses a
  separate, short-lived pairing link;
- an ordinary invite never grants operator authority;
- account login, passwords, OAuth, and hosted account recovery are not added.

## Sources Of Truth

Read these after a context reset:

1. `docs/product/CURRENT_SYSTEM.md`
2. This plan
3. `docs/product/OPERATING_MODEL.md`
4. `docs/product/ROOM_REPOSITORY.md`
5. `agentsassemble/identity_store.py`, `room_users.py`, `room_invite.py`, and
   `gui_room_invite_http.py`
6. `frontend/src/app/useRoomAdmission.ts`, `lib/roomGuestSession.ts`, and the
   closest behavioral tests

`/Users/seinel/Downloads/review.md` is review evidence, not repository
authority. Its admission plan is corrected below where it assumed the current
host device already maps to `operator-local`.

## Confirmed Baseline

The current implementation has two different host identities:

- the trusted local browser WebSocket uses the synthetic participant
  `operator-local`;
- `/api/host/claim` resolves each device credential to a new `guest-*` user and
  sets `is_operator=1` on that user.

The current local identity database therefore contains multiple operator users
whose participant IDs are `guest-*`, while no identity user owns
`operator-local`. `operator_user_id()` selects the most recently seen operator,
so room ownership can drift between device rows.

The invite startup path also has two confirmed gaps:

- `createStartupRoute()` discards a stored room session whenever `/join?token`
  is present;
- `useRoomAdmission()` decides whether to show the profile form from a locally
  remembered guest profile, not a side-effect-free server identity lookup.

Device tokens live in `localStorage`. They are stable per browser origin, not
per physical browser. `http://127.0.0.1:8765` and a Cloudflare HTTPS origin are
separate credentials.

## Identity Contract For This Slice

### Local operator

The local server has exactly one canonical operator identity:

```text
user_id        operator-local-user
participant_id operator-local
```

Host-token-authorized device claims attach a credential to that user. They do
not create another operator user. Existing legacy operator rows are not treated
as independent authorities after canonicalization.

Canonicalization must be transactional and conservative:

- preserve legacy user rows and historical room events;
- move only credentials presented through a host-authorized claim or an
  explicit pairing redemption;
- update room owner IDs that point to prior operator users;
- do not match or merge by display name, IP address, user agent, or browser
  fingerprint;
- do not expose auth keys, raw device tokens, host tokens, or pairing tokens in
  API responses, events, diagnostics, or logs.

### Guest device

A reusable human invite may map a device credential to a stable non-operator
user. A lookup-only preflight may recognize that user but must not create a
user, consume an invite, issue a session, or mutate membership.

### Cross-origin pairing

Cross-origin continuity requires an explicit operator action. A pairing claim:

- is separate from an ordinary room invite;
- is one-use and expires after at most two minutes;
- is bound to the canonical operator user, one room, and the configured public
  origin;
- stores only a non-reversible token fingerprint;
- attaches the redeeming device credential to the canonical operator user;
- issues a bounded room session for `operator-local` without exposing the host
  token to the public origin.

Pairing is not account login and does not establish identity on another server.

## Execution Order

### Commit 1 - Record corrected identity and admission plan

- Add this document.
- No behavior change.
- Verify with `git diff --check`.

### Commit 2 - Canonicalize local operator credentials

- Add explicit identity-store operations for one canonical local operator and
  credential attachment.
- Make `/api/host/claim` bind the device to that user.
- Preserve old users; update legacy operator-owned room owner IDs.
- Return only public user/participant/operator fields.
- Make the host WebSocket and frontend use the shared participant constant.

Behavior tests:

- repeated host claims keep one user and participant;
- claims from two device tokens attach to the same user;
- a credential previously attached to a legacy operator is safely rebound;
- a normal guest credential never becomes operator;
- room ownership no longer follows the most recently seen legacy operator;
- no token or auth-key material appears in returned data.

### Commit 3 - Add side-effect-free invite admission preflight

- Add `RoomAdmissionService` as the owner of invite validation plus identity
  projection.
- Add `POST /api/room-invite/admission`.
- Pass the device token in `X-Device-Token`, not the JSON body.
- Return only:
  `existing_session | existing_member | known_user | profile_required |
  pairing_required | invite_invalid | invite_expired` and safe room/profile
  fields.
- Do not change invite use count, nonce state, user count, memberships, or
  sessions.

### Commit 4 - Preserve existing sessions and auto-admit known devices

- Stop discarding a stored session solely because the URL has an invite token.
- If a valid stored session is for the invite room, remove the invite from the
  URL and keep the session without consuming the invite.
- Otherwise run preflight before displaying the profile form.
- Known same-origin users join with their saved server profile.
- Unknown devices still see the explicit guest profile form.
- Keep session transport separate from authority: a paired operator session
  uses bearer authentication while normal guests remain restricted.

### Commit 5 - Add explicit public-origin operator pairing

- Add pairing create/redeem/revoke storage under the identity authority.
- Add host-gated create and public redeem endpoints.
- Add `공개 주소에서 나로 열기` beside, but not inside, ordinary invite
  sharing.
- Add a dedicated `/pair?token=...` startup path.
- Clear the raw pairing token from browser URL and memory immediately after
  redemption.
- Never put the host token in the pairing URL or public-origin storage.

### Commit 6 - Browser and protocol verification

Behavior tests and Playwright must cover:

1. same-origin known guest: no form and no duplicate participant;
2. same-origin operator: `operator-local`, no guest duplicate;
3. existing same-room session: invite not consumed;
4. different origin without pairing: guest form remains;
5. paired origin: same canonical operator and moderator capability;
6. incognito/unknown credential: new guest;
7. same display name with another credential: separate user;
8. reload and reconnect: same participant and session behavior;
9. ordinary guest invite: never operator;
10. expired/reused/revoked pairing: explicit rejection.

Use two real origins (`127.0.0.1` and `public.localhost`) for cross-origin
browser coverage. Exact `localhost` is deliberately rejected by the external
invite URL guard, so the E2E fixture uses the loopback-resolving
`public.localhost` host without weakening that production rule. Direct backend
calls are supporting evidence, not a substitute for the frontend flow.

### Commit 7 - Review report

- Record the corrected baseline, implementation, data handling, tests, browser
  smoke, remaining limitations, and every intentional deviation from the
  external review.
- State explicitly that login is not implemented.
- Do not claim hosted multi-instance identity support or production readiness.

## Verification Gates

Run the cheapest targeted test after each commit, then before completion run:

```text
python3 -m unittest tests.test_identity_store tests.test_host_account
python3 -m unittest tests.test_room_invite tests.test_public_invite_http
python3 -m unittest tests.test_ws_endpoint tests.test_room_realtime
python3 -m unittest discover -s tests -t .
npm --prefix frontend test
npm --prefix frontend run build
Playwright same-origin and two-origin admission smoke
git diff --check
```

If the full suite exposes an unrelated existing failure, report the exact
command and failure. Do not replace a failed identity path with a guest fallback
or silently create another user.

## Explicit Non-Goals

- Email/password, Google, GitHub, Apple, or other account login.
- Identity continuity between different AgentsAssemble servers.
- Hosted multi-instance identity/invite PostgreSQL migration.
- IP, name, User-Agent, or fingerprint-based identity guessing.
- Provider runtime, autonomous discussion, media delivery, or model changes.
- Rewriting historical room events to change their actor IDs.
