# Antigravity CLI Continuity Evidence

Date: 2026-05-28

This note records the Antigravity CLI continuity disambiguation probe for the
provider live-session matrix. It explains why AgentsAssemble still must not add
an Antigravity provider-specific resident runner from the current evidence.

## Safe Contract Surface

- `agy --version` returned `1.0.2`.
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

Public evidence keeps only these booleans, lengths, counts, and outcome
descriptions. It does not store raw prompts, raw Antigravity replies, full
conversation or project ids, codewords, suffix values, account data, local
store paths, generated config paths, or provider logs.

## Verdict

Antigravity CLI is now `global-store-contaminated-no-runner`.

The local install has some provider-owned memory surface because a non-isolated
`--continue` probe recalled a prior turn. The follow-up disambiguation does not
prove a deterministic session handle suitable for an AgentsAssemble resident
runner: the isolated proof did not reproduce suffix recall through either
`--continue` or `--conversation <candidate>`, and generated symlinks still
resolved outside the temporary proof root.

Do not route Antigravity resident participation through one-shot `local_cli`,
do not add an `antigravity_live_session` runner from this evidence, and do not
present `agy --continue` as a room-owned session id. The viable next path is a
self-service wrapper where Antigravity owns the room loop and calls
`register`, `wait-next`, `say`, `official-reply`, `heartbeat`, and `leave`
itself, or a future Antigravity CLI contract that exposes a documented
conversation id flow with clean negative controls.
