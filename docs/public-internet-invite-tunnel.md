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

1. Configure a host token. In the React GUI, use **호스트 토큰 생성** before
   exposing the room. Operators can still pre-set `AGENTSASSEMBLE_HOST_TOKEN`
   when they want the token to come from the shell/environment.

2. Configure a public URL. In the GUI, use **공개 링크 열기** to start a
   Cloudflare quick tunnel when `cloudflared` is installed, or paste an existing
   public URL manually. Operators can still pre-set `AGENTSASSEMBLE_PUBLIC_URL`
   for fixed tunnel deployments.

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
export AGENTSASSEMBLE_PUBLIC_URL="https://random-words.trycloudflare.com"
export AGENTSASSEMBLE_HOST_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"

python3 -m agentsassemble.cli gui --host 127.0.0.1 --port 8765
```

GUI-only path:

1. Start the GUI on loopback: `python3 -m agentsassemble.cli gui --host 127.0.0.1 --port 8765`
2. Open the React lobby invite panel.
3. Click **호스트 토큰 생성**.
4. Click **공개 링크 열기**. If `cloudflared` is missing, install it or paste a
   tunnel URL into **공개 주소 직접 입력**.
5. Click **초대 링크 생성** and send the generated `/join?token=...` link.
   If the GUI shows only the local/dev preview URL, the public URL is not
   configured yet and that preview URL is not a secure external invite.

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

- **AGENTSASSEMBLE_HOST_TOKEN is required** for public exposure. Without it,
  host-gated endpoints (create invite, list sessions, revoke) reject all
  requests when `AGENTSASSEMBLE_PUBLIC_URL` is set. Host token may be omitted
  only for local/LAN dev mode where no public URL is configured. The GUI can
  bootstrap a server-lifetime host token before a public URL is configured; it
  does not reveal an existing environment token.
- Invite tokens are single-use, time-limited (default 10 minutes), and revocable.
- Session tokens expire after 1 hour.
- Invite/session state is stored locally under
  `.agentsassemble/room-invite-state.json` inside the GUI output root. The store
  keeps invite secret material plus token/nonce fingerprints so single-use,
  revocation, and active guest sessions survive a server restart without
  writing raw session tokens or host tokens.
- Externally shared invites should use the generated `/join?token=...` URL. The
  legacy `?guest=1&room=...` URL is a local/dev preview surface; it does not
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
# 2. Generate or enter the host token
# 3. Start a Cloudflare tunnel or paste a public tunnel URL
# 4. Create an invite — a public join link is generated
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
