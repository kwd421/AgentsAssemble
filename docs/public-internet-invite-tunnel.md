# Public Internet Invite: Tunnel Setup

This document explains how to expose your local AgentsAssemble room server to
the public internet so a friend can join via invite link.

## Overview

AgentsAssemble runs a local HTTP server (default `127.0.0.1:8765`). To allow
internet access, you need a tunnel service that gives your local server a public
URL. The server itself does NOT automatically open any public tunnel.

## Requirements

1. Set `AGENTSASSEMBLE_HOST_TOKEN` — a secret string that gates invite
   creation, session listing, and invite revocation. Without this, anyone who
   discovers your public URL can create invites.

2. Set `AGENTSASSEMBLE_PUBLIC_URL` — the public base URL of your tunnel (e.g.,
   `https://my-room.trycloudflare.com`). This is used to generate join links
   that guests can open directly.

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
  only for local/LAN dev mode where no public URL is configured.
- Invite tokens are single-use, time-limited (default 10 minutes), and revocable.
- Session tokens expire after 1 hour.
- The server does NOT start provider CLIs, expose secrets, or grant filesystem
  access through the invite flow.
- The tunnel exposes the full GUI control plane. Bind to `127.0.0.1` and rely on
  the tunnel for public access rather than binding to `0.0.0.0`.
- For production use, consider Cloudflare Access or similar zero-trust overlay
  on top of the tunnel.

## Quick Start

```bash
# Terminal 1: Start tunnel
cloudflared tunnel --url http://127.0.0.1:8765

# Terminal 2: Start server with auth
export AGENTSASSEMBLE_HOST_TOKEN="my-secret-host-token"
export AGENTSASSEMBLE_PUBLIC_URL="https://your-tunnel-url.trycloudflare.com"
python3 -m agentsassemble.cli gui --host 127.0.0.1 --port 8765

# In the React UI:
# 1. Go to the invite panel, enter your host token
# 2. Create an invite — a public join link is generated
# 3. Send the link to your friend
# 4. Friend opens the link, enters a display name, and joins
```

## Limitations (v1)

- No relay/WebRTC — requires a working tunnel for connectivity.
- No persistent auth — server restart invalidates all sessions and invites.
- No TLS termination — the tunnel handles TLS; local traffic is plain HTTP.
- No rate limiting — a determined attacker could brute-force invite tokens
  (mitigated by short TTL and single-use).
- The tunnel exposes the entire GUI, not just invite endpoints. Use host token
  to protect sensitive operations.
