# Durable Attention Model

Status: current contract; shadow evaluation only

Updated: 2026-07-14

Read this document when changing autonomous participation, room observation,
speaker selection, follow-up timers, or provider wake behavior.

## Purpose

An agent can remain connected to the canonical room without invoking its model.
Room events wake a local coordinator, not every provider. The coordinator may
select one participant, mark several as merely eligible for later policy, or
select nobody. Silence must cost zero remote-provider calls and zero provider
tokens.

Shadow mode records decisions while the existing `ordered` or `continuous`
routing still controls real turns. It must not change a visible message, launch
a provider, or add a second room transport.

Current shadow policy selects one connected direct mention/reply/next-speaker
target, marks multiple direct targets, `@all`, or a room-wide question as
eligible, and marks messages without a strong signal as silent. Its durable
decision and each candidate's evaluation cursor commit together.

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
  Shadow mode creates no active lease.

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
other participants. A room-wide question can produce one selected speaker or
an `eligible` shadow result. Ordinary agent output does not automatically force
another provider call.

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

PostgreSQL is not required for local shadow evaluation. Both SQLite and
PostgreSQL must pass the same repository contract before ambient mode is enabled
for hosted multi-worker use.
