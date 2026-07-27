# Durable Attention Model

Status: current contract; ordered and ambient room-observation routing active

Updated: 2026-07-25

Read this document when changing autonomous participation, room observation,
speaker selection, follow-up timers, or provider wake behavior.

## Purpose

An agent can remain connected to the canonical room without invoking its model.
Canonical room events are pushed to each connected Agent Bridge so its private,
bounded room mirror stays current. Passive event delivery advances only the
observation cursor and does not invoke the provider.

Legacy `continuous` rooms still use bounded transcript turns. Their optional
shadow recording is controlled by the server's
`--attention-shadow-mode off|sample|full` setting and defaults to `off`.
`sample` evaluates only committed source events whose canonical room sequence
is divisible by 16; this rule is deterministic across restarts. Shadow
evaluation must not change a visible message, launch a provider, or add a
second room transport. With `off`, it creates no attention job or cursor write.

An explicitly configured `ambient` room uses an event-driven observation path.
Each committed room message wakes every connected, idle, unmuted provider
session except its author. The wake carries canonical event/cursor identifiers
and referenced attachment IDs, not a server-built provider transcript. Each
provider then reads the wake's assigned room snapshot through its private
`RoomPortal` and independently chooses whether to publish. Events delivered
while that observation is active remain hidden from its view until a later
wake assigns them.

A publication written through the portal becomes one canonical
`message_final`. No publication becomes a structured `turn.decline` and creates
no visible placeholder. Ordinary provider output remains private and is not
used as a fallback room message. Ambient mode has no server-side relay count:
an agent reply is another committed room event, and peers may inspect it under
the same rules.

Ambient wake inputs are canonical `message_final` events from humans or agents.
Referenced room attachments travel through the same wake boundary. System and
lifecycle events do not start provider observations. Browser payloads cannot
inject provider input or set a private observation marker.

The deterministic attention selector remains available for shadow recording in
non-ambient rooms. It is not the ambient speaking authority. In ambient mode the
server decides only whether a session is eligible to inspect the room; the
provider decides whether it has something to say.

An `ordered` room uses the same private room-observation and explicit
publication boundary, but wakes only one provider for each committed room
message. A direct provider `@mention` gets the next observation without speaker
selection. Otherwise the server samples two available providers at random and
wakes the one with the smaller share of the most recent 100 provider messages;
a tied sample retains its random order. The author is excluded. The selected
provider reads the bounded room mirror and may publish or decline. A publication
creates the next room diff and therefore the next one-speaker selection.
If a new human message arrives while that provider is still working, its chosen
next observer is queued. The room does not dispatch a second observation until
the active turn finishes.

## Independent Cursors

Each agent has four room-local monotonic cursors:

| Cursor | Meaning |
| --- | --- |
| `last_observed_seq` | The connected agent room client durably received the event. |
| `last_attention_evaluated_seq` | The deterministic gate evaluated through this event. |
| `last_provider_sync_seq` | The provider model actually received room context through this event. |
| `last_spoke_seq` | The agent's latest committed visible message. |

Observing the room does not imply spending provider tokens. Evaluating attention
does not imply provider context delivery. These cursors must never be collapsed
back into one `last_seen` value.

`agent_attention_state.last_provider_sync_seq` is the provider-sync authority.
The Agent Session keeps `last_provider_sync_seq` and
`last_provider_sync_event_id` only as compatibility fields for existing clients
and diagnostics. Before a packet is built or assigned, the canonical sequence,
the compatibility sequence, and the sequence addressed by the compatibility
event ID must match. A mismatch fails closed with
`provider_sync_cursor_mismatch`; code must not choose whichever copy is more
convenient at read time.

## Durable Records

`agent_attention_state`
: One cursor row per room participant.

`attention_jobs`
: One idempotent deterministic-attention evaluation per committed source event.
  It records `selected`, `eligible`, or `silent`, the candidate set, reasons,
  and lifecycle status. Ambient portal wakes do not use this record to choose a
  global speaker.

`attention_leases`
: Exclusive, expiring authority for one participant to act on one selected job.
  Shadow mode creates no active lease. Active ambient routing requires one.

`scheduled_wakeups`
: A durable future wake time for an explicit follow-up. This remains a schema
  concept and is not the current ambient idle-check mechanism.

`conversation_obligations`
: Explicit unresolved duties such as answering a direct question or waiting for
  a named participant. They are satisfied, expired, or cancelled explicitly.

## Initial Deterministic Signals

Ambient eligibility requires a joined, enabled, connected, idle, unmuted Agent
Session. The author is excluded from its own message wake. Busy sessions retain
their pending canonical event IDs and inspect the backlog when they become
available.

An `@mention` remains a public room message and does not become a DM. In ordered
mode a direct provider mention grants that provider the next observation without
substituting another provider. Ambient routing still wakes all eligible peers
and does not silently replace an unavailable named provider.

The Agent Bridge acknowledges the highest canonical event sequence delivered
to it with `room.observed`. This advances only `last_observed_seq`; it does not
invoke the provider or spend provider tokens. Provider context advances only
after an observation completes or declines.

The bridge coalesces observation progress for at most 20 events or one second,
whichever comes first, and flushes pending progress before a graceful socket
close. It advances its local reported cursor only after the correlated ACK. A
greater sequence advances the durable checkpoint, while equal or lower retries
are no-ops and a sequence ahead of the canonical room stream is rejected.
These lightweight checkpoints bypass the general command-result table. The
one-second WebSocket receive timeout services this local flush deadline; it
does not issue a network poll or invoke a provider. The checkpoint command is
repository-atomic and does not acquire the controller lifecycle lock or create
a missing room; this allows a Bridge to flush before acknowledging a remote
stop without deadlocking the stop command.

## State and Commit Rules

1. Source room event commit happens first.
2. Attention evaluation, lease claim, selected session pending input, and its
   pending attention identifiers commit in one room repository transaction.
3. Duplicate evaluation of the same `(room_id, source_seq)` returns the durable
   existing result rather than creating another job.
4. A lease is claimable by at most one worker and has an explicit expiry. Claim
   expires an elapsed active lease and creates its replacement in the same room
   transaction; an unexpired lease owned by another worker remains a conflict.
5. Provider assignment occurs only after a selected job owns an active lease.
6. Completing, declining, failing, or expiring an observation closes its
   session turn explicitly; blank visible messages are never control flow.
7. Rollback publishes no attention event and advances no cursor.

At startup, `RoomAttentionReconciler` inspects a bounded number of active jobs,
leases, and session references per room. It expires elapsed leases, cancels
jobs or leases with no pending/active session reference, clears session
references to missing or terminal work, and cancels work selected for a removed
participant. Repairs commit with one `attention_reconciled` audit event; the
startup diagnostics report counts and whether any room exceeded the processing
bound. An unexpired lease owned by another controller generation is preserved.

Startup also runs `ProviderSyncCursorReconciler` before serving turns. Missing
copies are restored from the other durable record. If two nonzero cursors
diverged, the monotonic maximum is retained, the repair is audited, and the
session is marked `recovery_required`. A cursor beyond the canonical room event
stream is reported as a failure and remains unusable. Compatibility fields are
not removed in the same migration that changes read authority.

SQLite and PostgreSQL implement the same attention transaction contract.
PostgreSQL is explicitly configured rather than inferred, and hosted
multi-worker operation still requires deployment-level lease and failover
verification.

## Provider Room Portal

Each bridge owns one private `RoomPortal` outside the provider workspace. It
keeps at most 50 finalized messages and 32 KiB of rendered room text, projects
the provider's current display identity, identifies its own finalized messages,
reports how much dialogue followed its latest publication, and stages only
server-authorized room attachments. Server URLs, room tokens, database paths,
process IDs, and backend topology are not written into the room view.

Provider access is adapter-specific:

- Codex app-server receives two session-scoped MCP tools: read the current room
  and publish one room message. The app-server remains in `read-only` sandbox
  mode.
- Grok ACP receives equivalent virtual read/write paths.
- terminal-native providers receive a private `agentsassemble-room` helper in
  their allowlisted child `PATH`.

The provider must explicitly use the publication boundary. A normal assistant
final, terminal text, or TUI output is not copied into the room.

## Idle Check

Each bridge requests a room check after five minutes without a finalized room
message. This is one lightweight local timer and canonical WebSocket command,
not a 250 ms model poll. When ambient mode is active and the session is eligible,
the server assigns an observation of the current bounded room view. The provider
may publish or decline exactly as it would after an event wake.

## Current Limits

- Ordered and ambient observation are event-driven; there is no
  fractional-second provider polling. Ordered selects one speaker per diff,
  while ambient wakes all eligible peers.
- The five-minute idle check invokes the provider only when the server accepts
  a room observation.
- Scheduled follow-ups and conversation obligations remain inactive schema
  concepts.
- Pair cooldowns, per-room token budgets, and panel policies are not active.
- Codex and Grok can receive staged image bytes through their structured
  transports. A real Antigravity/Gemini JPEG smoke also passed through the
  private terminal helper and native image viewer. Claude fetched the staged
  JPEG but could not render it under the tested `dontAsk` permission boundary,
  so remaining provider-native image/PDF/audio behavior still requires
  individual verification. Unsupported media is reported, not claimed as
  viewed.
