# Provider Live Session Matrix

This matrix records the current resident-room evidence for provider families.
It is intentionally conservative: discovery, config generation, and preflight
checks are not execution, and a passing static check is not real provider
readiness.

Codex and Kiro are the only provider-specific resume residents in this matrix.
They are still not native provider channels: each one has a provider-specific
resume adapter plus bounded proofs, while full start/observe/official-turn/reply
heartbeat/stop/restart semantics still need explicit real-provider smoke before
being treated as production-ready for a given local install.

`configs/live-agents.provider-staging.example.json` is the conservative
non-Codex config reference for the rows below. It is a contract example, not a
native-ready bundle; run discovery, explicit approval, preflight, and a targeted
smoke before starting any real provider process from it.

| Provider kind | Current status | Supported connection_kind | Context durability | Sandbox enforcement | Required wrapper | Next smoke test |
| --- | --- | --- | --- | --- | --- | --- |
| `codex_live_session` | Experimental Codex resident MVP; first-class compared with generic CLIs, but still `codex exec resume` rather than a provider channel. A no-model regression uses a fake `codex` executable to prove lifecycle plumbing, and `live-agent continuity-proof --provider-kind codex_live_session --approve-real-providers` proves real two-turn resume recall for a local install. | `live_session` compatibility gate, implemented by the Codex resident runner. | `codex_exec_resume`; provider-managed session history after Codex returns a session id. | `codex_readonly` via `codex exec --sandbox read-only --ignore-rules`; Codex CLI owns actual enforcement. | None for checked-in examples; optional GUI/CLI invite writes local session ids. | Next real Codex smoke: run three approved `moderator_called` agents through `start-session`, official rounds, restart/resume, finalization, and stop with safe artifacts. |
| `kiro_live_session` | Experimental Kiro resident MVP. The runner uses `kiro chat`, captures a new session id from a serialized before/after session-list diff, then resumes through `kiro chat --resume-id`. `live-agent continuity-proof --provider-kind kiro_live_session --approve-real-providers` proves real two-turn resume recall for a local install without storing the continuity code or provider output. | `live_session` compatibility gate, implemented by the Kiro resident runner; it does not speak the JSONL live-session protocol. | `kiro_chat_resume`; provider-managed session history after Kiro exposes a session id. | `advisory`; Kiro CLI owns its tools and permissions, and AgentsAssemble has no Kiro read-only launch flag. | None for checked-in examples; operator may provide a `session_id` or let one `run-group` process serialize fresh capture. | Add a real Kiro room smoke that combines continuity-proof with start-session/probe/stop evidence for host-approved roles. |
| `claude_code` | Planned provider-native integration; can be staged manually or through generic resident transports only. | `self_service` wrapper or `terminal_session` experiment; `local_cli` is stateless delegate fallback. | Provider-managed when Claude owns its own process; process-lifetime through PTY/self-service wrappers; stateless-prompt for one-shot `local_cli`. | `advisory` until launched through a real sandbox or native read-only channel. | Claude Code Channels or a self-service room-loop wrapper. | Prove register, wait-next, official-reply, leave, and stop without prompt injection or write permissions. |
| `cursor` | Planned provider-native integration; not verified as a room resident. | `self_service` wrapper or `terminal_session` experiment; `local_cli` fallback only if a command accepts stdin. | Process-lifetime for PTY/self-service; stateless-prompt for one-shot calls. | `advisory` until a constrained launcher exists. | Cursor-specific wrapper or native CLI/session interface if available. | Create a fake Cursor wrapper smoke, then a real local smoke with explicit approval. |
| `antigravity_cli` | Generic resident candidate, not provider-native. | `self_service` preferred; `terminal_session` experimental. | Process-lifetime unless the provider exposes its own durable session id. | `advisory` until a sandboxed launcher exists. | Self-service room loop that calls `wait-next`, `say`, `official-reply`, `heartbeat`, and `leave`. | Prove a wrapper can observe without replying, answer one official turn, and leave offline cleanly. |
| `grok_build_cli` | Planned local CLI/provider candidate, not verified. | `self_service`, `terminal_session`, or `local_cli` depending on the actual CLI contract. | Unknown until the CLI contract is verified; treat as process-lifetime or stateless-prompt only after evidence. | `advisory` until a sandboxed launcher exists. | Provider-specific completion detector or self-service wrapper. | Document the real CLI contract, then add a fake wrapper smoke before real-provider smoke. |
| `hermes_cli` | Memory/profile inspiration and possible CLI candidate, not verified as live resident. | `self_service` or `terminal_session` only after CLI behavior is known. | Unknown; imported artifacts should use memory-pack durability until a live process is proven. | `advisory` for any generic CLI run. | Memory capsule gate first; CLI wrapper later. | Gate a sample memory capsule, then prove a CLI wrapper can join without raw hidden session dumps. |
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
- Keep `terminal_session` experimental until each provider has a prompt boundary
  and completion detector.
- Keep Play Mode and Work Mode separate; lobby chatter becomes official input
  only through an explicit promote action.
