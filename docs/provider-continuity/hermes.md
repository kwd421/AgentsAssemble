# Hermes CLI Continuity Evidence

Date: 2026-05-27; updated 2026-05-28

This note records the bounded Hermes CLI continuity probes used for the
provider live-session matrix. They are evidence only: no resident runner,
config promotion, or sandbox claim was added from these probes.

## Safe Contract Surface

- `hermes --version` returned `Hermes Agent v0.11.0 (2026.4.23)`. The command
  also prints a local project path, which is intentionally not copied here.
- `hermes chat --help` exposes `--query`, `--resume`, `--continue`, `--quiet`,
  `--pass-session-id`, `--source`, `--ignore-user-config`, `--ignore-rules`,
  and `--max-turns`.
- `hermes sessions --help` exposes `list`, `export`, `delete`, `prune`,
  `stats`, `rename`, and `browse`.
- `hermes sessions list --help` exposes `--source` and `--limit`.
- `hermes sessions export --help` exposes `--session-id`, `--source`, and an
  output path.

## Initial Probe Summary

The seed call used `hermes chat --query ... --quiet --ignore-user-config
--ignore-rules --source tool --max-turns 1 --pass-session-id`. It returned a
session id and the requested ready marker.

The resume call used the returned session id with `hermes chat --query ...
--resume <session_id> --quiet --ignore-user-config --ignore-rules --source tool
--max-turns 1`. It recalled the prior codeword exactly.

The negative-control call used a fresh `hermes chat --query ... --quiet
--ignore-user-config --ignore-rules --source tool --max-turns 1` with no
`--resume`. It also returned the same codeword. That means the successful resume
recall is contaminated by a broader recent/global context path and cannot prove
session-id-specific continuity by itself.

`hermes sessions list --source tool --limit 5` returned no rows, while
`hermes sessions export --session-id <session_id>` did export one JSONL session
record. The export was inspected only for safe counts and was not committed.

## Disambiguation Summary

A later approved disambiguation run used two independent temporary git cwds and
the same safe public-output rules. It created session A with code A and session
B with code B, then compared targeted `--resume` calls with fresh no-resume
controls.

Safe fields showed:

- Session A and session B both returned session ids, and the ids were distinct.
- Session A's seed returned the expected ready marker and did not reveal code A.
- Session B's seed returned successfully and did not reveal code B, but did not
  exactly match the ready marker.
- `--resume <session_a>` recalled suffix A.
- `--resume <session_b>` recalled suffix B.
- A cross-query against session A still surfaced suffix A rather than proving
  access to B.
- A fresh no-resume control also surfaced suffix A.
- Reusing session A from a different cwd surfaced suffix A.

This proves Hermes can preserve useful provider-owned session context, but it
also proves that a fresh no-resume call can reach at least one session secret.
That breaks the negative-control requirement for an AgentsAssemble
provider-specific resident runner.

## Verdict

Hermes is `global-recall-contaminated-no-runner` for this slice.

The current local install can recall prior probes after `--resume`, and the A/B
disambiguation shows the session ids are not simply ignored. However, a fresh
no-resume control can also recall a prior session secret. That is useful
evidence that Hermes has provider-owned context, but it is not enough to build
or advertise a deterministic live-room resident runner.

Do not add a Hermes runner until a later slice proves one of these:

- a stable session-id-only recall path with a clean no-resume negative control;
- or an agent-owned self-service wrapper that registers, waits, replies,
  heartbeats, and leaves through room tools without relying on hidden global
  recall.

Public docs must keep raw prompts, raw replies, full session ids, codewords,
absolute local paths, account data, provider logs, and exported transcript bodies
out of committed evidence.
