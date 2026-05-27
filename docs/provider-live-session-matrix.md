# Provider Live Session Matrix

This matrix records the current resident-room evidence for provider families.
It is intentionally conservative: discovery, config generation, and preflight
checks are not execution, and a passing static check is not real provider
readiness.

Codex, Kiro, and Grok are the provider-specific resume residents in this matrix.
They are still not native provider channels: each one has a provider-specific
resume adapter plus bounded proofs, while full start/observe/official-turn/reply
heartbeat/stop/restart semantics still need explicit real-provider smoke before
being treated as production-ready for a given local install. Grok is the newest
and narrowest of these paths: its proof and runner must parse only JSON stdout
`text` and keep raw stderr/logs out of public artifacts.

`local_cli` is a stateless delegate, not a resident session. In plain terms:
local_cli is a stateless delegate. It can be useful
for fake agents, one-shot opinion checks, and wrapper development, but it must
not be presented as provider-owned live-room participation or context
continuity evidence.

`configs/live-agents.provider-staging.example.json` is the conservative
non-Codex config reference for the rows below. It is a contract example, not a
native-ready bundle; run discovery, explicit approval, preflight, and a targeted
smoke before starting any real provider process from it.

| Provider kind | Current status | Supported connection_kind | Context durability | Sandbox enforcement | Required wrapper | Next smoke test |
| --- | --- | --- | --- | --- | --- | --- |
| `codex_live_session` | Experimental Codex resident MVP; first-class compared with generic CLIs, but still `codex exec resume` rather than a provider channel. A no-model regression uses a fake `codex` executable to prove lifecycle plumbing, and `live-agent continuity-proof --provider-kind codex_live_session --approve-real-providers` proves real two-turn resume recall for a local install. | `live_session` compatibility gate, implemented by the Codex resident runner. | `codex_exec_resume`; provider-managed session history after Codex returns a session id. | `codex_readonly` via `codex exec --sandbox read-only --ignore-rules`; Codex CLI owns actual enforcement. | None for checked-in examples; optional GUI/CLI invite writes local session ids. | Next real Codex smoke: run three approved `moderator_called` agents through `start-session`, official rounds, restart/resume, finalization, and stop with safe artifacts. |
| `kiro_live_session` | Experimental Kiro resident MVP. The runner uses `kiro chat`, captures a new session id from a serialized before/after session-list diff, then resumes through `kiro chat --resume-id`. `live-agent continuity-proof --provider-kind kiro_live_session --approve-real-providers` proves real two-turn resume recall for a local install without storing the continuity code or provider output. | `live_session` compatibility gate, implemented by the Kiro resident runner; it does not speak the JSONL live-session protocol. | `kiro_chat_resume`; provider-managed session history after Kiro exposes a session id. | `advisory`; Kiro CLI owns its tools and permissions, and AgentsAssemble has no Kiro read-only launch flag. | None for checked-in examples; operator may provide a `session_id` or let one `run-group` process serialize fresh capture. | Add a real Kiro room smoke that combines continuity-proof with start-session/probe/stop evidence for host-approved roles. |
| `claude_code` | Planned provider-native integration; no real provider-owned continuity evidence recorded in AgentsAssemble yet. | `self_service` wrapper or `terminal_session` experiment; `local_cli` is stateless delegate fallback only. | Provider-managed only when Claude owns its own process; process-lifetime through PTY/self-service wrappers; stateless-prompt for one-shot `local_cli`. | `advisory` until launched through a real sandbox or native read-only channel. | Claude Code Channels or a self-service room-loop wrapper. | Prove register, wait-next, official-reply, leave, and stop without prompt injection or write permissions. |
| `cursor` | Planned provider-native integration; local `cursor-agent` contract discovery found version `2026.05.24-dda726e` plus `--print`, JSON/stream-JSON output, `--resume`, `--continue`, `create-chat`, `ask`/`plan` mode flags, and a sandbox flag. Approved explicit-chat-id and workspace-continue probes both failed to recall the suffix. The app `cursor` CLI remains editor/open-file oriented, not the agent surface. | `self_service` or `terminal_session` experiment only; do not add a `cursor_agent` resume runner from current evidence. `local_cli` is stateless delegate fallback only if a command accepts stdin. | Candidate provider-managed chat resume remains unproven after failed probes; process-lifetime for PTY/self-service; stateless-prompt for one-shot calls. | `advisory` until a constrained launcher exists. | Cursor-specific wrapper or native CLI/session interface only after better provider-documented proof. | Find a documented Cursor Agent resume contract or self-service loop before another real room smoke. |
| `antigravity_cli` | Generic resident candidate. Local `agy` contract discovery found version `1.0.1` plus `--print`, `--prompt-interactive`, `--continue`, `--conversation`, and `--sandbox`. The approved `agy --print --continue` probe returned successfully but did not recall the suffix. | `self_service` preferred; `terminal_session` experimental; provider-specific resume only after a documented conversation id flow is proven. | Candidate session-store resume remains unproven after this probe; process-lifetime unless the provider exposes its own durable session id. | `advisory` until a sandboxed launcher exists. | Self-service room loop or Antigravity-specific command runner only after approved evidence. | Revisit with a documented conversation id flow, then prove observe/reply/leave with fake wrapper first. |
| `grok_live_session` | Experimental Grok resident room path. Local `grok` contract discovery found version `grok 0.2.3 (14d81fd875e)`, `--prompt-file`, `--resume`, JSON output, and related session surfaces. Approved isolated-git-cwd JSON stdout probes proved two-turn suffix recall through explicit `--resume <sessionId>`, and an approved Grok-only real room smoke proved start/probe/stop with safe counts: connected 1/1, reply probe 1/1, stop stopped, post-stop stopped. | `live_session` compatibility gate, implemented by the Grok resident runner; it does not speak the JSONL live-session protocol. | `grok_session_resume`; provider-managed session history is proven for the narrow JSON stdout `text` field. Full process output can echo prompt material in stderr, so raw stdout/stderr must not be persisted. This now proves one local install can enter the room and answer one lobby probe, but does not prove official-turn quality, restart/recover behavior, tool safety, future billing state, or sandboxing. | `advisory` until a sandboxed launcher exists. | None for the narrow runner; command must be the `grok` executable only, and the runner supplies `--prompt-file`, `--output-format json`, `--disable-web-search`, `--no-subagents`, `--verbatim`, and later `--resume <sessionId>`. | Next Grok smoke: official-turn quality, restart/resume, and final stop evidence without persisting prompts, replies, stderr/stdout, or full session ids. |
| `grok_build_cli` | Planned local CLI/provider candidate for other Grok Build surfaces. `grok agent` exposes modes including `stdio`, `headless`, `serve`, and `leader`, but those modes are not wired into AgentsAssemble resident participation. | `self_service` and `terminal_session` remain possible later paths; `local_cli` remains stateless delegate only. `grok_build_cli` with `terminal_session` is still unsupported by continuity-proof. | Unknown beyond generic process-lifetime experiments; use `grok_live_session` when the explicit JSON stdout resume runner is intended. | `advisory` until a sandboxed launcher exists. | Self-service or terminal wrapper only after a separate proof. | Prove a non-JSON-runner room loop separately before changing this row. |
| `hermes_cli` | Memory/profile inspiration and possible CLI candidate. Local `hermes` contract discovery found `chat --query`, `--resume`, `--continue`, `--quiet`, `--source`, `--ignore-user-config`, `--ignore-rules`, and session management help. The approved `hermes chat --query --continue` probe returned successfully but did not recall the suffix. A bounded `hermes version` command did not return before timeout, and account/status output is not safe public evidence. | `self_service` or `terminal_session` only after CLI behavior is known; provider-specific resume only after approved `chat` continuity proof. | Candidate chat/session resume remains unproven after this probe; imported artifacts should use memory-pack durability until a live process is proven. | `advisory` for any generic CLI run. | Memory capsule gate first; CLI wrapper later. | Revisit only with a documented session id resume flow that avoids hidden session dumps and account/status leakage. |
| `openclaw_cli` | Memory/profile inspiration and possible CLI candidate, not verified as live resident. | `self_service` or `terminal_session` only after CLI behavior is known. | Unknown; imported artifacts should use memory-pack durability until a live process is proven. | `advisory` for any generic CLI run. | Memory capsule gate first; CLI wrapper later. | Gate a sample memory capsule, then prove a CLI wrapper can join without raw hidden session dumps. |
| `local_cli` | Implemented generic local resident/delegate family. Useful for fake sessions and custom wrappers, not a native provider. | `local_cli`, `live_session`, `terminal_session`, `self_service`. | `stateless-prompt` for `local_cli`; process-lifetime for JSONL, PTY, and self-service residents. | `advisory` unless a future `SandboxLauncher` constrains the process. | Command accepting stdin, JSONL child, PTY command, or self-service room loop. | Keep `session-smoke --json` green across local CLI, JSONL live-session, terminal-session when PTY exists, and self-service. |
| `remote_http_bridge` | Implemented bridge contract for another owner’s session; useful fallback, not bridge-free native participation. | `remote_bridge`. | Remote-owner-managed; context durability belongs to the remote process. | `advisory` from the host perspective; bridge owner controls their machine. | Authenticated `/agentsassemble/health` and `/agentsassemble/run` bridge. | Health probe plus local loopback run smoke; real friend readiness must be recorded separately. |
| `native_remote_room_client` | Planned no-Tailscale/LAN client path, not a resident `run-group` transport yet. The Phase 5 PoC only signs and verifies a LAN invite token for host admission. | Future `remote_room_client`; not accepted by resident configs yet. | Remote-owner-managed; context belongs to the remote client/provider session. | `advisory` until authenticated room APIs, token revocation, and remote-client sandbox evidence exist. | Native room client that registers, waits, replies, heartbeats, and leaves through room APIs after invite admission. | Verify LAN invite signature/expiry, then add authenticated remote register/wait/reply smoke before any relay/WebRTC claim. |
| `memory_pack` | Implemented as an import/gate surface, not executable live participation. | None as a resident process. | File-backed shared artifact; not provider-private live context. | Not applicable; no provider command is launched by the gate. | `memory-capsule gate` for reviewed persona/memory artifacts. | Validate required capsule files and meeting-safe permissions, then design explicit host admission. |

## Rules For Updating

- Do not mark a provider native-ready from discovery or config generation alone.
- Do not mark `os_sandboxed` until the process is actually constrained by a
  verified OS-level sandbox, restricted worktree, env scrubber, or equivalent
  launcher.
- The shared `SandboxLauncher` mapping is the source of truth for
  `sandbox_enforcement`: `NoSandboxLauncher` means `advisory`, Codex read-only
  execution means `codex_readonly`, and no current non-Codex provider is
  hard-sandboxed.
- A passing `continuity-proof` proves only two-turn provider-owned resume recall
  without hidden prompt replay. It does not prove room admission, official-turn
  quality, tool safety, stop/restart behavior, billing state for future calls,
  or OS sandboxing.
- A passing `real-session-smoke` proves only a bounded approved start, connection,
  lobby reply probe, stop, and post-stop process status for the provided local
  configs. It does not prove official-turn quality, restart/recover behavior,
  Play Mode flow, future billing state, or sandboxing.
- Use `continuity-proof-group` when auditing a resident config with multiple
  local CLI candidates. The group proof runs real approved checks only for
  provider-specific resume residents such as Codex, Kiro, and Grok, while
  unsupported terminal, self-service, bridge, or stateless entries are reported
  without execution.
- Keep `terminal_session` experimental until each provider has a prompt boundary
  and completion detector.
- Keep Play Mode and Work Mode separate; lobby chatter becomes official input
  only through an explicit promote action.
