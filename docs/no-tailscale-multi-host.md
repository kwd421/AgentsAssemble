# No-Tailscale Multi-Host Design

This is the Phase 5 design track for inviting agents from another machine
without depending on Tailscale. It is not a claim that internet-scale remote
meetings are finished.

## Goal

Support a future flow where a host runs the AgentsAssemble GUI room on a LAN
address, creates a bounded invite for one approved agent identity, and gives
that invite to a remote owner. The remote owner runs a native room client on
their own machine. That client registers, observes room events, and posts lobby
or official replies through normal room APIs.

The first checked-in proof is intentionally small: `live-agent lan-invite`
creates and verifies an HMAC-SHA256 token scoped to one room URL, meeting id,
agent id, display name, provider kind, expiry, and nonce. It does not start
provider CLIs, open the room to the internet, create a relay, or solve NAT
traversal.

## LAN Invite Token Mode

`LAN invite token mode` is for same-network access only:

```bash
python3 -m agentsassemble.cli live-agent lan-invite create \
  --server http://192.168.1.50:8765 \
  --meeting-id resident-m1 \
  --agent-id friend-claude \
  --display-name "Friend Claude" \
  --provider-kind claude_code \
  --secret-ref env:AGENTSASSEMBLE_LAN_INVITE_SECRET \
  --ttl-seconds 600 \
  --json
```

The token payload declares:

- `mode: lan_invite_token`
- `client_kind: native_remote_room_client`
- `room_url`, `meeting_id`, and one approved agent identity.
- `admission.identity_proof: hmac_sha256_invite_token`.
- `admission.provider_execution: not_started_by_invite`.
- `admission.remote_http_bridge: false`.

Verification is local and non-mutating:

```bash
python3 -m agentsassemble.cli live-agent lan-invite verify \
  --token "$AGENTSASSEMBLE_LAN_INVITE_TOKEN" \
  --secret-ref env:AGENTSASSEMBLE_LAN_INVITE_SECRET \
  --expected-meeting-id resident-m1 \
  --expected-agent-id friend-claude \
  --json
```

The token must not be placed in URLs, public operation logs, safe roster fields,
meeting artifacts, or copied command examples with real values. The PoC supports
`env:` secret references so examples can avoid literal secrets. Verification
also rejects tokens that do not carry a meeting id and agent id, and it can
compare those claims against the host-approved expected identity.

## Remote Agent Admission And Identity Proof

`remote agent admission and identity proof` has two layers:

- Host admission: the host chooses the meeting id, agent id, provider kind,
  engagement policy, and whether that identity is allowed into the room.
- Invite proof: a future remote client presents the signed token so the host can
  verify the token signature, expiry, meeting id, and agent id before accepting
  the registration.

This proof is admission evidence, not provider execution approval. It does not
authorize Claude Code, Cursor, Codex, Grok, Hermes, OpenClaw, or any other real
CLI to launch. Provider execution still needs the explicit operator approval
rules recorded in `docs/product/OPERATING_MODEL.md`.

## Naming

Keep these two concepts separate:

`remote_http_bridge` means the host calls a friend-owned HTTP bridge such as
`POST /agentsassemble/run`. The bridge owner controls the remote session, and
the host sees bridge health/run responses.

`native_remote_room_client` means the remote machine itself joins the room API,
polls or streams room events, and posts replies as the admitted agent identity.
The host is not invoking a remote prompt execution endpoint. The first LAN
invite PoC uses this name so bridge fallback and bridge-free room participation
do not blur together.

The future resident connection kind may be named `remote_room_client`, but it
should not be accepted by `run-group` until admission, token expiry/revocation,
and authenticated room endpoints are implemented.

## Relay And WebRTC Candidates

LAN-only mode was the first step; public tunnel v1 now exists (see
`docs/public-internet-invite-tunnel.md`) but it does not solve NAT traversal,
relay, WebRTC, mobile networks, or durable auth beyond server-memory tokens.

Relay mode:

- Easier operational model: both host and remote client connect outward to a
  rendezvous or relay service.
- Easier auth and revocation: the relay can enforce room admission and token
  expiry centrally.
- Costs more trust and maintenance: relay operator sees metadata and must be
  secured, monitored, and paid for.

WebRTC data channel:

- Better direct-session feel when peers can connect.
- Requires signaling, ICE, STUN/TURN, reconnection behavior, and a fallback when
  direct connectivity fails.
- Harder to audit than a simple room HTTP API unless message signing and event
  persistence stay host-owned.

Recommended order:

1. Stabilize single-host resident sessions.
2. Add LAN invite admission with signed, expiring tokens.
3. Add native remote room client registration against authenticated room APIs.
4. Add relay as the first internet proof.
5. Experiment with WebRTC only after signaling, TURN fallback, and audit logging
   are specified.

## Current Non-Goals

- Do not bind the GUI to `0.0.0.0` automatically.
- Do not start provider CLIs from invite generation or verification.
- Do not mark invite verification as proof of provider login, billing, model
  availability, context quality, or sandbox enforcement.
- Do not claim relay or WebRTC readiness from the LAN token PoC.
- Do not treat `remote_http_bridge` as the bridge-free native room client path.
- Web invite v1 (`/api/room-invite/create`, `/join`, `/say`, `/leave`,
  `/revoke`, `/invites`) is now implemented for both LAN and public internet
  use via tunnel. Host-gated endpoints require `AGENTSASSEMBLE_HOST_TOKEN`
  (env) and `X-Host-Token` header. When `AGENTSASSEMBLE_PUBLIC_URL` is set,
  invite creation returns a full `join_url` guests can open directly.
  Invites are single-use, time-limited, and revocable by the host.
  Session tokens are in-memory only; there is no durable auth store, no
  persistent revocation beyond server-memory leave/expiry, no relay/WebRTC/NAT
  traversal, and no provider CLI start. `/api/room/say` enforces session
  identity (name, actor_id, side, kind) from the authenticated session, not
  from client-supplied fields. See `docs/public-internet-invite-tunnel.md` for
  tunnel setup instructions.
