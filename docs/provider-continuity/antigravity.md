# Antigravity CLI Continuity Evidence

Date: 2026-05-28

This note records Antigravity CLI continuity evidence for the provider
live-session matrix. The latest evidence is strong enough for the narrow
`antigravity_live_session` runner, while the older generic `antigravity_cli`
and `--continue` paths remain non-live.

## Safe Contract Surface

- `agy --version` returned `1.0.3`.
- `agy --help` exposes `--print`, `--prompt-interactive`, `--continue`,
  `--conversation`, `--sandbox`, and `--dangerously-skip-permissions`.
- The help surface does not expose a session or conversation listing
  subcommand; attempted `sessions --help` and `conversations --help` returned
  the top-level help shape.
- `--sandbox` is Antigravity-owned behavior and remains
  `sandbox_enforcement: "advisory"` from AgentsAssemble's perspective.

## Probe Summary

An earlier approved non-isolated probe showed that `agy --print --continue`
could recall a prior turn on this local install. That positive result was not
enough for a runner because `--continue` follows the provider's most-recent
conversation store rather than a deterministic room-owned handle, the output
described global local-store inspection, and a direct `--conversation <id>`
follow-up did not recall the stored code.

A later approved disambiguation run used a temporary git cwd plus isolated
`HOME`, `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, and `XDG_CACHE_HOME`. Safe fields
showed:

- The seed call returned `0` but did not produce the expected `READY` marker.
- The seed reply did not reveal the continuity code or suffix.
- `--continue` returned `0` but did not contain the expected suffix.
- `--conversation <candidate>` returned `0` but did not contain the expected
  suffix.
- Reusing the same candidate from another cwd did not contain the expected
  suffix.
- A fresh no-resume control did not contain the expected suffix.
- The run found one UUID-shaped candidate from generated project metadata, not
  a proven conversation id.
- The run created cwd `.antigravitycli` symlinks that resolved outside the
  temporary proof root despite the isolated environment.

A newer approved refresh used the documented log line emitted by `agy --print`
instead of `--continue`. Safe fields showed:

- `agy --log-file ... --print` returned the ready marker and captured a safe
  36-character conversation id from `Created conversation <id>`.
- Two independent seed calls produced distinct conversation ids.
- `agy --conversation <a>` recalled only tag A.
- `agy --conversation <b>` recalled only tag B.
- A fresh no-resume control recalled neither tag and returned the expected
  no-context marker.
- The successful proof-root sidecar was a `.antigravitycli` directory inside
  the proof root, not a symlink escaping to the user's config store.
- A checked-in `live-agent continuity-proof` run returned `status: "ok"` with
  session capture, no code/suffix leak, no prompt replay, and
  `recall_match_mode: "mentioned"` because Antigravity wrapped the suffix in
  extra text instead of outputting exactly one token.

Public evidence keeps only these booleans, lengths, counts, and outcome
descriptions. It does not store raw prompts, raw Antigravity replies, full
conversation or project ids, codewords, suffix values, account data, local
store paths, generated config paths, or provider logs.

## Verdict

Antigravity CLI is now `provider-conversation-resume-runner` only for the
`antigravity_live_session` contract.

The runner must use explicit `agy --conversation <conversation_id>` after
capturing the id from the log. It must not use bare `--continue`, generic
`antigravity_cli`, terminal prompt bridging, or one-shot `local_cli` calls as
evidence of live participation. Formatting compliance remains weaker than
session recall, so continuity proof reports `expected_suffix_matched` separately
from `expected_suffix_recalled`.
