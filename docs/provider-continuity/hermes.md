# Hermes CLI Continuity Evidence

Date: 2026-05-27

This note records the bounded Hermes CLI continuity probe used for the provider
live-session matrix. It is evidence only: no resident runner, config promotion,
or sandbox claim was added from this probe.

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

## Probe Summary

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

## Verdict

Hermes is `continuity-ambiguous-no-runner` for this slice.

The current local install can recall a prior probe after `--resume`, but a fresh
no-resume control can also recall it. That is useful evidence that Hermes has a
provider-owned context surface somewhere, but it is not enough to build or
advertise a deterministic live-room resident runner.

Do not add a Hermes runner until a later slice proves one of these:

- a stable session-id-only recall path with a clean no-resume negative control;
- or an agent-owned self-service wrapper that registers, waits, replies,
  heartbeats, and leaves through room tools without relying on hidden global
  recall.

Public docs must keep raw prompts, raw replies, full session ids, codewords,
absolute local paths, account data, provider logs, and exported transcript bodies
out of committed evidence.
