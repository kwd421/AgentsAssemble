# Hermes CLI Continuity Evidence

Date: 2026-05-27; updated 2026-05-28

This note records the bounded Hermes CLI continuity probes used for the
provider live-session matrix. The latest evidence is strong enough for the
narrow `hermes_live_session` runner, while generic `hermes_cli` and one-shot
`--query` paths remain non-live.

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

That run proved Hermes can preserve useful provider-owned session context, but
also showed a fresh no-resume call could reach at least one session secret.
That older run stayed `global-recall-contaminated-no-runner`.

A newer approved refresh used a unique `--source` value plus
`--ignore-user-config --ignore-rules` for each call. Safe fields showed:

- Session A and session B both returned distinct safe session ids.
- Both seed calls returned the ready marker and did not reveal their tags.
- `--resume <session_a>` recalled only tag A.
- `--resume <session_b>` recalled only tag B.
- Same-source and fresh-source no-resume controls recalled neither tag and
  returned the expected no-context marker.
- A checked-in `live-agent continuity-proof` run returned `status: "ok"` with
  session capture, no code/suffix leak, no prompt replay, and
  `recall_match_mode: "mentioned"` because Hermes wrapped the suffix in
  explanatory text instead of outputting exactly one token.

## Verdict

Hermes is now `provider-session-resume-runner` only for the
`hermes_live_session` contract.

The runner must use explicit `hermes chat --resume <session_id>` with the
runner-owned `--source` shape and the rule-suppression flags proven above. It
must not present generic `hermes_cli`, `--continue`, terminal prompt bridging,
or one-shot `--query` calls as live participation. Formatting compliance
remains weaker than session recall, so continuity proof reports
`expected_suffix_matched` separately from `expected_suffix_recalled`.

Public docs must keep raw prompts, raw replies, full session ids, codewords,
absolute local paths, account data, provider logs, and exported transcript bodies
out of committed evidence.
