# Public Internet Invite: Tunnel Setup

This document explains how to expose your local AgentsAssemble room server to
the public internet so a friend can join via invite link.

## Overview

AgentsAssemble runs a local HTTP server (default `127.0.0.1:8765`). To allow
internet access, you need a tunnel service that gives your local server a public
URL. The React invite panel can prepare the host token, remember a public URL,
and start a Cloudflare quick tunnel when `cloudflared` is already installed.
It does not install tunnel software, start provider CLIs, or grant filesystem
access.

## Requirements

1. Configure a host token. Operators can pass `--host-token` to the GUI command,
   pre-set `AGENTSASSEMBLE_HOST_TOKEN`, or let the local operator UI create a
   server-lifetime runtime token before public exposure. The status endpoint
   never returns an existing host token.

2. Configure a public URL. Operators can pass `--public-url`, pre-set
   `AGENTSASSEMBLE_PUBLIC_URL`, paste an existing public URL into the invite
   modal, or start a Cloudflare quick tunnel from the modal when `cloudflared`
   is installed.

3. If a reverse proxy is managed outside AgentsAssemble, configure
   `AGENTSASSEMBLE_TRUSTED_PROXY_TOKEN` on the server and have that proxy add
   the same value as `X-AgentsAssemble-Proxy-Token` on origin requests. The
   token belongs at the proxy boundary, never in browser code or a public join
   link. The proxy must also remove any client-supplied copies of both that
   header and `X-AgentsAssemble-Client-IP`, then set
   `X-AgentsAssemble-Client-IP` to the verified remote client address. The
   GUI-managed Cloudflare quick tunnel is registered and origin-authenticated
   by the server process and does not need this manual proxy token.

## Cloudflare Tunnel (Recommended)

Free, no account required for quick tunnels:

```bash
# Install: brew install cloudflared (macOS) or see https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/

# Start the tunnel (temporary, random subdomain)
cloudflared tunnel --url http://127.0.0.1:8765
```

Cloudflared will print a public URL like `https://random-words.trycloudflare.com`.
Set that as your public URL:

```bash
python3 -m agentsassemble.cli gui \
  --host 127.0.0.1 \
  --port 8765 \
  --public-url "https://random-words.trycloudflare.com" \
  --host-token "$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
```

GUI-only path:

1. Start the GUI on loopback: `python3 -m agentsassemble.cli gui --host 127.0.0.1 --port 8765`
2. Open the React lobby invite panel.
3. In **친구에게 보낼 보안 초대 링크**, either paste a public URL and click
   **설정**, or click **터널 시작**. If `cloudflared` is missing, install it or
   paste a tunnel URL manually.
4. Click **링크 생성** and send only the generated `/join?token=...` link.
   The main secure invite field stays empty until a non-local public URL exists.
   `127.0.0.1`, `localhost`, and `0.0.0.0` are shown only inside the collapsed
   **로컬/dev 미리보기** section, which is labeled **친구에게 보내지 마세요**.

You can also ask the GUI to start a Cloudflare quick tunnel immediately:

```bash
python3 -m agentsassemble.cli gui \
  --host 127.0.0.1 \
  --port 8765 \
  --start-public-tunnel
```

If `--public-url` or `--start-public-tunnel` is used without a host token, the
server creates a runtime token and prints it only to the local console.

## Terminal-Free Launcher

The local **Open AgentsAssemble Room.app** launcher starts the same loopback GUI
on port `8765` without requiring a terminal. By default it also prepares a
local host token and starts a Cloudflare quick tunnel when `cloudflared` is
installed, then opens the room only after `/api/public-invite/status` reports a
running public URL. This keeps the no-terminal path from accidentally starting a
plain local-only server that cannot generate friend-shareable `/join?token=...`
links.

Launcher notes:

- The host token is stored locally in `.agentsassemble/host-token` with
  user-only file permissions.
- If a GUI server is already running on `127.0.0.1:8765`, the launcher asks that
  server to start its public tunnel instead of starting another server.
- Set `AGENTSASSEMBLE_PUBLIC_TUNNEL=0` before launching only when you
  intentionally want local-only development mode.
- The launcher still opens the local operator URL
  `http://127.0.0.1:8765/`; friend links must be generated from the invite modal
  as public `/join?token=...` URLs.

## ngrok

```bash
# Install: brew install ngrok (macOS) or see https://ngrok.com/download
# Requires free account for auth token

ngrok http 8765
```

Use the `https://xxxx.ngrok-free.app` URL as `AGENTSASSEMBLE_PUBLIC_URL`.

## Tailscale Funnel

If you already use Tailscale:

```bash
tailscale funnel 8765
```

## Security Notes

- **A host token is required** for public invite creation and public tunnel
  management. The local operator UI may bootstrap a server-lifetime token only
  from a trusted loopback request; public guests cannot generate one.
- Forwarded HTTPS and client-IP headers are trusted only for the currently
  server-managed Cloudflare tunnel or a reverse proxy authenticated with
  `AGENTSASSEMBLE_TRUSTED_PROXY_TOKEN`. Merely setting a public URL or sending
  `X-Forwarded-Proto` from loopback does not establish proxy provenance. A
  manually launched `cloudflared` process is not equivalent to the
  GUI-managed tunnel because it does not carry the process-lifetime origin
  credential.
- External human invites require a configured public URL. `/api/room-invite/create`
  returns an error instead of producing a local `127.0.0.1` join URL when a
  public URL is missing.
- Invite tokens are single-use, time-limited (default 10 minutes), and revocable.
- Session tokens expire after 1 hour.
- Invite/session state is stored locally under
  `.agentsassemble/room-invite-state.json` inside the GUI output root. The store
  keeps invite secret material plus token/nonce fingerprints so single-use,
  revocation, and active guest sessions survive a server restart without
  writing raw session tokens or host tokens.
- Externally shared invites must use the generated public `/join?token=...` URL.
  The legacy `?guest=1&room=...` URL is a local/dev preview surface; it does not
  issue an authenticated guest session and is treated as read-only.
- Read-only invite scope is enforced by the server session policy. A read-only
  guest can read the room, but `/api/room/say` and companion AI invite creation
  are rejected for that session.
- Room rosters may show all visible people and AI agents, including AI that
  another participant brought in, but provider/subscription quota fields are
  viewer-scoped. Guests receive quota fields only for their own admitted
  `agent_id` and companion `${agent_id}-ai`; hosts see local host-owned agent
  quota but not remote-owned `native_remote_room_client` or `remote_bridge`
  quota.
- The server does NOT start provider CLIs, expose secrets, or grant filesystem
  access through the invite flow. Companion AI packets are generated for the
  authenticated guest's current meeting only, ignore client-supplied room URLs,
  and state `provider_execution: not_started_by_invite`.
- The tunnel exposes the full GUI control plane. Bind to `127.0.0.1` and rely on
  the tunnel for public access rather than binding to `0.0.0.0`.
- For production use, consider Cloudflare Access or similar zero-trust overlay
  on top of the tunnel.

## Quick Start

```bash
python3 -m agentsassemble.cli gui --host 127.0.0.1 --port 8765

# In the React UI:
# 1. Go to the invite panel
# 2. Start a Cloudflare tunnel or paste a public tunnel URL
# 3. Create an invite — a public /join?token=... link is generated
# 5. Friend opens the link, enters a display name, and joins
```

## Limitations (v1)

- No relay/WebRTC — requires a working tunnel for connectivity.
- No account login or durable user identity — the local store only preserves
  invite/session admission state for this GUI output root until expiry,
  revocation, or deletion of the store file.
- No TLS termination — the tunnel handles TLS; local traffic is plain HTTP.
- No rate limiting — a determined attacker could brute-force invite tokens
  (mitigated by short TTL and single-use).
- The tunnel exposes the entire GUI, not just invite endpoints. Use host token
  to protect sensitive operations.
