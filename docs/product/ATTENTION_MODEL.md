# Durable Attention Model

Status: current contract; opt-in ambient routing active

Updated: 2026-07-14

Read this document when changing autonomous participation, room observation,
speaker selection, follow-up timers, or provider wake behavior.

## Purpose

An agent can remain connected to the canonical room without invoking its model.
Room events wake a local coordinator, not every provider. The coordinator may
select one participant, mark several as merely eligible for later policy, or
select nobody. Silence must cost zero remote-provider calls and zero provider
tokens.

`ordered` and legacy `continuous` rooms still use their existing routing.
Optional shadow recording is controlled by the server's
`--attention-shadow-mode off|sample|full` setting and defaults to `off`.
`sample` evaluates only committed source events whose canonical room sequence
is divisible by 16; this rule is deterministic across restarts. Shadow
evaluation must not change a visible message, launch a provider, or add a
second room transport. With `off`, it creates no attention job or cursor write.

An explicitly configured `ambient` room uses the same durable evaluation to
select and lease at most one speaker for each committed message. A plain human
message may start a chain, and a committed agent reply may hand off to one other
eligible agent. The initial agent-to-agent chain budget is two relays. A named
target that is unavailable is reported as unavailable; another provider is not
silently substituted.

Ambient wake inputs are limited to committed text messages from a human or
agent, or a server-authored event carrying `trusted_ambient_trigger: true`.
Direct mentions, replies, and room questions remain public selection signals.
Votes, system/lifecycle kinds, empty text, and unsupported media-only events
produce a durable silent decision and do not wake a provider. Browser payloads
cannot set the trusted marker through `message.send`.

When enabled, the current shadow policy selects one connected direct mention,
reply, or next-speaker target; marks multiple direct targets, `@all`, or a
room-wide question as eligible; and marks messages without a strong signal as
silent. Its durable decision and each candidate's evaluation cursor commit
together.

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

## Durable Records

`agent_attention_state`
: One cursor row per room participant.

`attention_jobs`
: One idempotent evaluation per committed source event. It records `selected`,
  `eligible`, or `silent`, the candidate set, reasons, and lifecycle status.

`attention_leases`
: Exclusive, expiring authority for one participant to act on one selected job.
  Shadow mode creates no active lease. Active ambient routing requires one.

`scheduled_wakeups`
: A durable future wake time for an explicit follow-up. The scheduler blocks
  until the nearest wake time; it does not poll every fraction of a second.

`conversation_obligations`
: Explicit unresolved duties such as answering a direct question or waiting for
  a named participant. They are satisfied, expired, or cancelled explicitly.

## Initial Deterministic Signals

Strong positive signals are a direct public mention, direct reply, explicit
next-speaker invitation, unresolved direct question, and due follow-up. Negative
signals are self-authored events, paused or disconnected sessions, cooldown,
recent speaking share, exhausted chain budget, and unsupported media-only input.

An `@mention` selects who may answer; it does not hide the public message from
other participants. A room-wide question produces one fair selected speaker in
ambient mode or an `eligible` shadow result elsewhere. Ambient handoff may wake
one agent for ordinary agent output until the chain budget is exhausted.

The Agent Bridge acknowledges the highest canonical event sequence delivered
to it with `room.observed`. This advances only `last_observed_seq`; it does not
invoke the provider or spend provider tokens. Provider context advances only
after an assigned turn completes or declines.

## State and Commit Rules

1. Source room event commit happens first.
2. Attention state/job writes happen in a room repository transaction.
3. Duplicate evaluation of the same `(room_id, source_seq)` returns the durable
   existing result rather than creating another job.
4. A lease is claimable by at most one worker and has an explicit expiry.
5. Provider assignment occurs only after a selected job owns an active lease.
6. Completing, declining, failing, or expiring a turn closes or reschedules the
   lease explicitly; blank visible messages are never control flow.
7. Rollback publishes no attention event and advances no cursor.

SQLite and PostgreSQL implement the same attention transaction contract.
PostgreSQL is explicitly configured rather than inferred, and hosted
multi-worker operation still requires deployment-level lease and failover
verification.

## Current Limits

- Ambient selection is deterministic and event-driven; there is no periodic
  provider polling.
- Ambient active evaluation does not depend on the shadow-recording setting.
- Scheduled follow-ups and conversation obligations are durable schema concepts
  but are not active wake sources yet.
- Pair cooldowns, per-room token budgets, and panel policies are not active.
- Provider-native media delivery remains incomplete, so ambient routing must not
  claim an agent viewed an attachment it did not receive.
