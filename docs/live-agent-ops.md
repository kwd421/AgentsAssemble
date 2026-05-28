# Live Agent Ops

This is the local operator checklist for the current AgentsAssemble resident live-agent slice. It uses the local GUI room as the control plane and local CLI, JSONL session, and explicitly configured remote HTTP bridge participants as resident agents.

## Start The GUI Room

From the repository root:

```bash
python3 -m agentsassemble.cli gui --host 127.0.0.1 --port 8765 --output-root .agentsassemble
```

Open:

```text
http://127.0.0.1:8765
```

The lobby is the public room surface and should read first as a staging room,
similar to a pick room before the live client opens. The default "상주 실행"
surface keeps the current meeting id, Play Mode free-conversation controls,
participant readiness, and recent preparation evidence in view. Session
startup, ensure, resume, restart, recover, stop, diagnostics, smoke checks,
discovery, and auto-join controls remain available under `고급 운영` for the
operator instead of dominating the default lobby. The default group config path
is:

```text
configs/live-agents.start-session.example.json
```

The GUI's `세션시작` button pairs that resident config with `configs/demo-council.json` and `configs/agents.start-session.example.json` so the visible meeting bindings and resident runner manifest match. The `시작` button still starts only the supervised process group from the config input, while `세션시작` creates the visible meeting and starts the matching resident group through `/api/live-agent-sessions/start`. `세션보장` calls `/api/live-agent-sessions/ensure` to no-op an already ready session or choose start, resume, restart, or recover for the current target. `세션재개` reconnects an existing meeting to its supervised group, `세션재시작` restarts that meeting-aware group and waits for fresh presence, `세션복구` recovers an `unknown` or `error` historical group for the same meeting without requiring the config path again, `세션점검` records a meeting-scoped readiness snapshot, and `세션중지` stops that meeting-aware group and updates bound roster evidence through `/api/live-agent-sessions/stop`.

The live-agent roster and supervised process panel auto-refresh in the GUI every 5 seconds. This keeps stale presence, process crashes, pending auto-restart state, and recovered groups visible during long sessions without relying only on the manual refresh buttons. Background refreshes ignore volatile heartbeat age and monitor tick timestamps when deciding whether to re-render the lobby, so the room stays live without visually resetting every poll. Play Mode flow status polling patches the compact status line in place while the visible control shape is unchanged, so a running timer does not reset lobby scroll or input drafts on every poll. The GUI server also starts a backend supervisor monitor, so owned process crash detection and due auto-restarts continue without an open browser or `/api/live-agent-processes` polling client. The manual refresh buttons remain useful when you want an immediate read after changing files or process state from another terminal.

The current frontend is still the dependency-light vanilla HTML/CSS/JS operator
console. Natural-language room text should keep human-readable tokens intact
(`Kiro Opus 4.7`, `0.5`, `80kg`, ellipses, and similar prose tokens), while
technical surfaces such as URLs, ids, logs, paths, and command output may use
stronger wrapping to avoid horizontal overflow. Live-event stream updates should
append or update event rows without replacing the whole live panel when possible,
so input drafts, scroll position, and the latest-message control remain stable
during SSE refreshes. The Live tab can show Play Mode as an unofficial room
flow with running/finished state, remaining time, participant status, and
flow-scoped events; it should not present Play Mode chatter as transcript or
decision evidence.

Lobby chat can attach local files through the `첨부` control. The browser reads
the selected file and sends a JSON/base64 upload to `/api/attachments`; the GUI
server stores the bytes under `<output-root>/attachments/<attachment-id>/` and
returns safe metadata. Posting a lobby message with attachments stores only that
metadata in `lobby.jsonl`: attachment id, filename, content type, size, image
flag, preview URL, and download URL. Raw bytes, base64 bodies, absolute paths,
and user-supplied forged filenames are not copied into the lobby event. Images
render as small thumbnails in the chat and can be clicked for a local preview;
all attachments keep a download URL. These attachments are still lobby/play
history, not official transcript or decision evidence unless a future explicit
promote path is added.

The React frontend track can start a Play Mode Mafia Night room through
`/api/play/mafia/start`. Mafia state is stored separately under `play/mafia/`
and exposes viewer-filtered reads through `/api/play/mafia?game_id=...`.
The operator console reads with `viewer_agent_id=host`; participant-owned clients
must pass their own agent id so roles, votes, and mafia-team events stay filtered.
Use `/api/play/mafia/chat` for both `all` and `mafia_team` channels; do not
reuse `/api/side-chat` for mafia team chat because side chat is globally visible
room chatter. `/api/play/mafia/vote` records day or night votes, and
`/api/play/mafia/resolve` advances the phase or records a winner. These events
remain Play Mode game records and are not official transcript, decision, or
shared-memory evidence.

## Play Mode Persona Cards

RisuAI-style persona cards are a Play Mode character layer, not a Work Mode role
or provider permission layer. Use them when an approved resident should speak as
a character with imported lore, world context, and speech style.

Inspect a `.risum` module without printing lore bodies:

```bash
python3 -m agentsassemble.cli persona inspect-risum \
  --file /path/to/persona.risum \
  --rpack-map /path/to/RisuAI/src/ts/rpack/rpack_map.bin \
  --json
```

Import the module into the local persona store:

```bash
python3 -m agentsassemble.cli persona import-risum \
  --file /path/to/persona.risum \
  --rpack-map /path/to/RisuAI/src/ts/rpack/rpack_map.bin \
  --output-root .agentsassemble \
  --json
```

The importer writes `.agentsassemble/personas/<persona-id>/card.json`, copied
source evidence, and decoded local asset payloads. It preserves raw lore text in
the stored card, including NSFW or otherwise sensitive text; the safe CLI report
prints counts and metadata instead of lore bodies or descriptions. Risu runtime
features that could execute or rewrite text, such as regex scripts, triggers,
CJS, MCP declarations, and low-level access, are preserved as ignored metadata
and are not executed.

Attach a persona to a resident with either the explicit card path:

```json
{
  "agent_id": "yanagi",
  "display_name": "Tsukishiro Yanagi",
  "engagement_mode": "flow",
  "persona_path": ".agentsassemble/personas/yanagi/card.json",
  "command": ["python3", "scripts/fake_agent.py"]
}
```

or the default local persona id lookup:

```json
{
  "agent_id": "yanagi",
  "display_name": "Tsukishiro Yanagi",
  "engagement_mode": "flow",
  "persona_id": "yanagi",
  "command": ["python3", "scripts/fake_agent.py"]
}
```

Persona context is inserted only into Play Mode `flow` decision prompts. It is
not inserted into official turn prompts, transcript generation, decisions, or
shared memory. For stateful prompt transports such as `live_session`,
`terminal_session`, and `remote_bridge`, a flow resident with a persona attached
observes but does not answer official turn requests from the same flow loop, so
character framing cannot silently bleed into Work Mode evidence. If you want a
character-mode on/off switch in the richer frontend later, wire it to these
resident persona fields and keep the stored raw card separate from the safe
roster summary.

When a character-mode meeting writes Work Mode artifacts, the final meeting
record includes a `persona_artifact_contract` summary. It reports only safe
artifact paths, surfaces, violation codes, and counts for issues such as
unreplaced card variables, roleplay narration markers, ignored execution-feature
names, or raw card text appearing in official artifacts. It does not print raw
lore, adult card bodies, matched snippets, config paths, or provider output.

Before involving a real provider, run the local fake-provider smoke:

```bash
python3 -m agentsassemble.cli live-agent persona-smoke \
  --card .agentsassemble/personas/yanagi/card.json \
  --output-root .agentsassemble \
  --meeting-id persona-smoke-yanagi \
  --json
```

The smoke copies the card into an isolated `persona-smoke/<meeting-id>/`
diagnostic area, creates a fake resident meeting only when the meeting id is
unused, finalizes one official fake reply, and verifies the persona artifact
contract without starting Claude, Codex, Grok, Kiro, or any other real CLI.

## Room-first / Agent-owned Context

The room is a room, not a hidden moderator. A resident agent should treat
AgentsAssemble as shared room tooling: it provides public events, official
artifacts, shared memory, and cursor/diff reads, while the provider or CLI keeps
its own private context and compression behavior.

Agents decide what extra context to read before replying. The normal loop is:
read the room, compare the new public room events against the agent's cursor,
optionally inspect transcript, decision, return-packet, or shared-memory
artifacts, then post one lobby or official reply through the room. The room
records `last_observed_event_id` for lobby events and
`last_observed_live_event_id` for official live events so a returning agent can
ask "what changed since I last looked?" without receiving a giant prompt dump.
Those cursor/diff reads are room evidence, not provider execution.

The moderator does not sit between every room event and every agent reply. It
may start a meeting, call an official turn, close pending turns, or finalize a
record, but agent-owned room loops should not require a host to rewrite each
event into a bespoke hidden prompt. If a provider has a durable native session,
it remains responsible for its own private context; if it is stateless, the
stateless wrapper may read the room artifacts it needs and include them in that
provider call.

Session-owned supervised groups persist the safe `meeting_id` in `live-agent-runs/processes.json`. That meeting ownership is preserved through manual restart, recovery, delayed auto-restart, and stale-watchdog restart, and is visible through `/api/live-agent-processes`, `/api/live-agent-health` as `processes.meeting_ids`, `/api/live-agent-health` meeting-owned session readiness, and the GUI process row as `meeting <id>`. This is operator evidence only; it does not store command arguments, endpoint URLs, auth refs, prompts, replies, log tails, or provider output.

`/api/live-agent-health` also includes an `observations` overlay for ready resident sessions. It compares the latest bounded lobby event and each bound agent's latest official turn request against that agent's preserved lobby/live cursors and reply timestamp, then reports compact counts such as ready agents, lobby-behind, live-behind, active error count, and observation attention labels. This is read-only: health refreshes do not call providers, run probes, append operation rows, start or stop processes, post lobby messages, or expose lobby text, official request content, presence error text, provider output, config paths, endpoints, or auth refs.

The same health snapshot includes shared meeting memory health evidence in `shared_memory` for ready resident sessions. It reports only official event, open-question, and action-item counts without official reply text, including safe meeting/group ids, memory-bearing ready-session counts, and the last official event id. This lets operators see whether long-running resident sessions have current shared memory context without turning the health endpoint into a transcript, prompt, or provider-output surface.

The same health snapshot includes host-admission health evidence in `admission`.
It reuses the safe roster admission projection derived from the current
`meeting.json` and reports total rows, host-approved rows, unapproved rows,
status counts, and compact admission attention labels such as
`resident-m1:agent-a:binding_conflict`. This evidence is read-only and is not
part of overall health degradation because lobby-only, manual, or conflicting
rows can be legitimate staging presence; use `live-agent list
--require-host-approved` when automation needs admission to be a hard gate. The
health payload and compact GUI/CLI summary do not expose session ids, endpoint
URLs, auth refs, config paths, command arguments, prompts, provider output,
presence error text, or spoofed stored admission fields.

The same health snapshot includes a `sandbox_enforcement` summary derived from
the safe roster projection. It counts `codex_readonly` for Codex
`codex exec --sandbox read-only --ignore-rules`, `advisory` for generic local
CLI, JSONL live-session, PTY terminal, self-service, manual, and remote bridge
room loops, `unknown` for unsupported contracts, and `os_sandboxed` only for a
future provider actually launched through a verified hard sandbox.

Durable session-run reconciliation can act on that same observation evidence only when the existing process group already opted into automatic recovery with `auto_restart`, remaining `max_restarts` budget, and a positive `stale_restart_after_seconds`. In that case a ready session whose latest lobby or official turn request has stayed unobserved beyond the stale window is reconciled through the normal `ensure-session` policy, which chooses `restart` for the existing meeting-owned group. Fresh lag, self-authored lobby events, answered official requests, disabled auto-restart, exhausted restart budget, and health-only reads remain non-mutating.

When `ensure-session` changes an otherwise ready session into `restart`, the session payload can include a safe `ensure_reason`: `resident_session_id_drift`, `stale_lobby_observation`, or `stale_live_observation`. Durable session-runs persist that reason under `result.ensure_reason`, and session start/ensure/restart/recover operation details expose the same allowlisted value when present. The reason is operator evidence only; it does not include old or new session ids, lobby text, official request or reply text, provider output, config paths, command arguments, endpoint URLs, or auth refs.

Session controls also enforce that ownership. `start-session` with an explicit meeting id, `resume-session`, `restart-session`, `recover-session`, and `stop-session` refuse to reuse or mutate an existing process group that already belongs to a different meeting. `resume-session`, `restart-session`, and `recover-session` also refuse to mutate resident processes when the current meeting binding has `binding_provider_missing`; `check-session` and readiness stay read-only and report `degraded` instead of treating that group as ready. A non-empty but unsafe stored owner id is treated as a different meeting without echoing the unsafe value.

The real-provider `configs/live-agents.example.json` contains a real `claude`
command. Do not start it until the real-provider checklist below is satisfied.
It intentionally does not include bare `agy`, legacy `antigravity`, or `hermes`
commands because their current continuity evidence is inventory-only.
`configs/live-agents.provider-staging.example.json` is a broader
contract example, not a native-ready bundle: it shows the current conservative
Claude Code, Cursor, Grok Build, and OpenClaw resident shapes with
`moderator_called`, but each row still needs local install,
explicit approval, preflight, and a provider-specific smoke before use.
The current Antigravity CLI evidence is specifically for the `agy` executable:
`agy --print --continue` can recall one prior turn on this local install, but a
later isolated-HOME disambiguation did not prove a deterministic
`--conversation <id>` handle and still created `.antigravitycli` symlinks that
resolved outside the temporary proof root. That does not make the checked-in
Antigravity self-service examples native-ready and does not justify a
provider-specific resume runner. Use a real wrapper that owns room polling
before treating Antigravity as a resident participant, and do not persist raw
Antigravity output, conversation ids, global-store paths, symlink targets, or
config symlinks as proof artifacts.
The current Cursor Agent evidence is also narrow: `cursor-agent
create-chat` plus `cursor-agent --resume <chat_id> --print` can preserve one
chat's context on this local install only when the same workspace is reused.
The checked-in `cursor_live_session` runner preserves that chat id plus
workspace pair, and a later approved real room smoke proved one host-approved
resident can start, connect, answer one redacted lobby probe, stop, and report
post-stop `stopped` with safe counts. Cursor still has not proven official-turn
quality, restart, recover, tool safety, future billing stability, production
readiness, or sandboxing.
The current Hermes evidence is weaker than the provider-specific resident
paths. A/B probes show `--resume <session_a>` and `--resume <session_b>` can
recall their own suffixes, so Hermes has useful provider-owned context. But a
fresh no-resume control also recalled a prior session suffix, so Hermes remains
a global-recall-contaminated provider-owned context surface rather than a
session-id-specific resident path. Keep Hermes in the same explicit-approval
staging lane until a clean session-id proof or self-service room loop is
verified.

## GUI Startup Autostart

GUI startup autostart is explicit. Starting the GUI with only `gui --host ... --port ... --output-root ...` does not autostart `configs/live-agents.example.json` by default.

Use `--live-agent-config` only when you want the GUI server itself to start one supervised resident group after the HTTP server has bound:

```bash
python3 -m agentsassemble.cli gui \
  --host 127.0.0.1 \
  --port 8765 \
  --output-root .agentsassemble \
  --live-agent-config /path/to/fake-live-agents.json \
  --live-agent-group-id boot \
  --live-agent-auto-restart \
  --live-agent-max-restarts 3 \
  --live-agent-restart-backoff-seconds 5
```

The autostart path uses the same supervisor start and preflight gate as the GUI `시작` button and `live-agent processes start`. It passes the actual bound GUI URL to the resident group, including an OS-selected port when `--port 0` is used.

Startup autostart records a safe `process.autostart` entry in `live-agent-runs/operations.jsonl`. If the config is missing or preflight refuses it, the GUI still serves the room and the failed autostart is visible through recent operations.

## Runtime Engagement Policy

Each roster card has a compact engagement selector for the live agent. It writes through:

```text
POST /api/live-agents/<agent_id>/engagement
```

with a JSON body like:

```json
{"engagement_mode": "watch"}
```

The same runtime policy update is available from the CLI:

```bash
assemble live-agent engagement \
  --server http://127.0.0.1:8765 \
  --agent-id claude-code-live \
  --mode watch
```

The module form is equivalent when running from a checkout:

```bash
python3 -m agentsassemble.cli live-agent engagement \
  --server http://127.0.0.1:8765 \
  --agent-id claude-code-live \
  --mode watch
```

Use `--json` to print the raw API response after the update.

Valid modes are `manual`, `mentioned`, `moderator_called`, `human_only`, `always`, and `watch`. Treat `always` as loop-prone: every non-self lobby event can trigger an automatic reply unless the chain-depth guard blocks it.

Changing engagement mode updates `live_agents.json`, `/api/live-agents`, and `/api/live-agents/<agent_id>/room`, records `engagement_mode_updated_at`, but does not refresh `last_seen_at` or reset `heartbeat_age_seconds`. A policy change is operator control, not proof that the agent process is still alive.

Resident runners read the current room presence on every poll and use that roster `engagement_mode` before falling back to their startup config. Re-registration and heartbeat updates preserve an operator-selected mode instead of silently clobbering it. `watch` and `manual` observe new lobby events and advance `last_observed_event_id` without posting replies, so switching an agent back to an active mode does not replay the backlog. If the cursor-only observation heartbeat fails transiently while advancing lobby or live cursors, the runner keeps the local cursor and continues polling instead of taking down the resident group.

External or manually driven agents can register through the same room control plane before they begin polling:

```bash
python3 -m agentsassemble.cli live-agent register \
  --server http://127.0.0.1:8765 \
  --agent-id claude-code-live \
  --display-name "Claude Code Live" \
  --provider-kind claude_code \
  --connection-kind local_cli \
  --meeting-id resident-1 \
  --session-id session-1 \
  --engagement-mode watch \
  --json
```

Use `--json` when a wrapper needs the registration acknowledgement, including the server-preserved meeting, session, and engagement fields, instead of parsing the compact `Registered <agent-id>` line.

The HTTP registration path records a safe `live_agent.register` operation with
agent id, meeting id, provider kind, connection kind, engagement mode, previous
status, registered status, and admission evidence. That evidence includes
`admission_status`, `host_approved_binding`, and, when a target meeting binding
exists, safe binding role/provider/permission/join-mode ids plus compact
`binding_conflicts` such as `provider_kind_mismatch` or
`binding_provider_missing`. `admission_status` can distinguish no meeting id
(`lobby_only`), a missing target meeting (`meeting_missing`), a meeting without a
matching binding (`meeting_lobby_only`), a matching host-approved binding
(`bound_to_meeting`), and a binding conflict (`binding_conflict`). A bound
registration is evidence that the roster row matches a host-approved meeting
binding; lobby-only or conflicting registrations are still allowed as explicit
manual/external presence. The operation records the derived `join_semantics` and
`context_durability` contract for the registered provider/connection pair, such
as stateless prompt calls, process-lifetime terminal or JSONL sessions, Codex
exec resume, self-service room loops, or remote-owner-managed bridges. It does
not trust caller-supplied contract labels, record session ids, endpoint URLs,
auth refs, config paths, provider command arguments, prompts, provider output,
or log tails, and it does not start a provider.

When handing the room to another AI, generate an entry packet, also called a
startup packet in older docs, instead of writing ad hoc instructions:

```bash
python3 -m agentsassemble.cli live-agent join-brief \
  --server http://127.0.0.1:8765 \
  --agent-id external-reviewer \
  --display-name "External Reviewer" \
  --provider-kind manual \
  --connection-kind manual \
  --meeting-id resident-1 \
  --engagement-mode watch \
  --json
```

`join-brief` is local formatting only: it does not contact the room, write files, start providers, or execute commands. Treat its JSON as an `agent_owned_entry_packet`: safe startup evidence that tells the receiving agent how to enter, read, speak, and leave through room tools. The JSON includes safe command arrays for `register`, `wait-next`, `read-since`, `room`, a roster gate, and `leave`, plus `say`, `official-reply`, and `heartbeat` templates that another agent can fill after `wait-next` returns an action. It also includes an `mcp` block with an exact `python3 -m agentsassemble.cli mcp serve --profile participant ...` command for hosts that can connect stdio MCP tools to the invited agent. That MCP command pins the participant identity in startup args (`agent_id`, display name, provider kind, connection kind, meeting id, and engagement mode) and exposes agent-owned room tools over the existing HTTP room contract; it does not start the provider CLI or grant durable provider approval. The packet also includes an `entry_contract` and an `execution_contract` with `join_semantics`, `context_durability`, `evidence_basis`, and `provider_execution: "not_started_by_join_brief"` so the receiving agent and operator can see whether the packet describes a manual room loop, stateless prompt calls, a terminal PTY prompt bridge, JSONL live-session bridge, Codex exec resume, self-service room loop, or remote bridge room loop. It intentionally omits session ids, endpoint URLs, auth refs, config paths, provider command arguments, provider output, log paths, prompts, and reply text. The packet instructions include `Read room.shared_memory as official-only background context when present.` and `Use execution_contract.context_durability as the declared agent-private context boundary.` Give that packet to the external agent, have it run `commands.register` once, then loop `commands.wait_next`, use `commands.read_since` for a raw cursor diff, or connect `mcp.command`: lobby and official-turn actions fill exactly one reply template or call one MCP reply tool, non-reply `observe_lobby` actions run the returned `ack_command` or heartbeat cursor, and return-packet actions run the returned `read_command` or `read_return_packet` before the `ack_command`, without posting a reply. When the agent intentionally exits the room, it should run `commands.leave` or the MCP `leave` tool; this marks its roster row `offline`, clears stale error text, and keeps any supplied lobby/official cursors.

The same safe packet is available from the running GUI server for frontends or other local tools:

```text
POST /api/live-agent-join-brief
```

The HTTP endpoint returns the same command arrays and templates, defaults the packet server URL to the current GUI request host, and still does not register the agent, write files, append operation records, start providers, or execute commands.

The leave command is also available directly:

```bash
python3 -m agentsassemble.cli live-agent leave \
  --server http://127.0.0.1:8765 \
  --agent-id external-reviewer
```

It calls `POST /api/live-agents/<agent_id>/leave`, which posts one offline
heartbeat through the normal room control plane and records a safe
`live_agent.leave` operation with the agent id, meeting id, previous status, and
cursor ids. It does not unregister the agent, delete history, stop supervised
process groups, or start providers; a later `register` or heartbeat can bring
the same approved agent identity back online.

Heartbeat `last_attention` is a safe-code field, not a free-form note. The
only v1 code is `persona_context_blocked_official_turn`; unknown attention text
is recorded as `presence_attention_redacted` before it can appear in roster or
GUI output.

The GUI `살아있는 에이전트` form exposes the same safe packet through the `초대 패킷` button. It reads the agent id, display name, provider kind, and connection kind from the registration form, attaches the current meeting id, requests `engagement_mode: "mentioned"`, and renders the returned join brief without registering the agent or starting any provider. This lets an operator hand a browser-generated register/wait-next packet to Claude, Cursor, a terminal agent, or another external participant while keeping admission explicit.

Inspect the current roster from a terminal with:

```bash
assemble live-agent list --server http://127.0.0.1:8765
```

The checkout module form is equivalent:

```bash
python3 -m agentsassemble.cli live-agent list \
  --server http://127.0.0.1:8765
```

The compact roster output shows each agent's id, display name, provider/connection kind, status, meeting, join semantics, context durability, host-admission status, host-approved binding flag, admission evidence source, safe binding labels when present, engagement mode, heartbeat age, stale threshold, lobby cursor, and official cursor. It intentionally does not print endpoint URLs, auth references, command arguments, config paths, provider output, or presence error text. `--json` uses the same safe roster projection for local wrappers; it does not expose endpoint URLs, auth refs, command arguments, config paths, session ids, or raw presence errors.

For wrappers that read the roster directly over HTTP, prefer:

```text
GET /api/live-agents?safe=1
```

That safe roster projection uses the same allowlisted presence fields as `assemble live-agent list`, including derived `join_semantics`, `context_durability`, and read-only admission evidence derived from the current meeting record. The safe admission fields include `admission_status`, `host_approved_binding`, `admission_evidence_source: "meeting_record"`, and safe binding role/provider/permission/join labels only when a matching binding exists; caller-supplied spoofed admission fields are not enough to make an unbound row appear approved. The projection omits endpoint URLs, auth refs, config paths, session ids, command arguments, provider output, and raw suspicious presence errors. The raw `/api/live-agents` response remains available for the local GUI and legacy in-room tooling that already expects full local presence records.

For scriptable roster gates, use `live-agent list --fail-on-attention` for liveness and `live-agent list --require-host-approved` for meeting admission. The command prints the normal roster summary first. `--fail-on-attention` exits `1` if any returned agent is not `online` or `working`, including `stale`, `offline`, `error`, or unknown statuses. `--require-host-approved` exits `1` if any returned agent is not currently `host_approved_binding: true` in the safe roster projection. An empty roster exits `0` for both gates because there is no agent row claiming unhealthy or unapproved presence; combine either gate with `--require-match` when the target must exist.

When a room has multiple resident sessions or historical roster rows, target the roster read before using it as a gate:

```bash
python3 -m agentsassemble.cli live-agent list \
  --server http://127.0.0.1:8765 \
  --meeting-id resident-1 \
  --agent-id claude-code-live \
  --require-match \
  --require-host-approved \
  --fail-on-attention
```

The CLI sends `safe=1` plus meeting_id, agent_id, and status query filters to `/api/live-agents`, with repeatable `--agent-id` and `--status`. Use `live-agent list --require-match` when the filtered roster must contain at least one row; it prints the normal empty summary first and exits `1` if no agent matches. Use `live-agent list --require-all-agents` when every requested `--agent-id` must be present in the filtered roster. This lets automation check one session's expected agents without unrelated stale rows or unapproved lobby rows from another session failing the gate, while still failing when the requested target is missing.

`--fail-on-attention` evaluates only the returned rows. If you want to detect stale, offline, or error rows for a target, filter by `--meeting-id` or `--agent-id` and leave `--status` unset; adding `--status online --status working` intentionally hides non-ready rows before the gate runs.

The GUI "Codex 세션 초대" panel can bind a current Codex CLI session to a meeting role through:

```text
POST /api/codex-sessions/invite
```

The endpoint writes `.agentsassemble/codex-live-session.local.json` with a `codex_live_session` provider binding for the selected role. It also records sanitized successful and failed invite attempts as `codex_session.invite` operations, so another operator or automation can verify what happened from `/api/live-agent-operations`. Successful records include the safe role id, generated agent id, join mode, and provider id. Failed records include only the safe role id and a generic invite failure. The operation record does not include the Codex session id, local config path, command arguments, auth refs, prompts, provider output, or log tails.

The GUI panel also exposes `입장`, which uses the same current-session selection but performs the resident join in one server-side operation:

```text
POST /api/codex-sessions/join
```

`join` is intentionally narrower than a generic meeting rebinder. It only accepts an existing live pre-round meeting, refuses meetings that already have debate rounds or official-turn events, writes both `codex-live-session.local.json` and `live-agents.codex-session.local.json`, updates the live meeting's role bindings to the generated Codex live bindings, and then calls the existing `ensure-session` policy for the generated resident group. The selected role keeps the chosen Codex session id; the other meeting roles receive fresh Codex live bindings so the resident manifest still covers the whole meeting. The operation record is `codex_session.join` and includes only safe meeting, role, agent, group, result, and ensure-action evidence. It does not include the Codex session id or local config paths.

## LAN Invite Token PoC

No-Tailscale multi-host support is a separate product axis from local resident
sessions. The first checked-in proof is `live-agent lan-invite`, which creates
and verifies a signed LAN admission packet for a future
`native_remote_room_client`. It does not bind the GUI to `0.0.0.0`, contact a
remote machine, start provider CLIs, authorize real provider execution, or make
relay/WebRTC ready.

Create an invite with a secret stored outside the command line:

```bash
export AGENTSASSEMBLE_LAN_INVITE_SECRET="replace-with-local-secret"

python3 -m agentsassemble.cli live-agent lan-invite create \
  --server http://192.168.1.50:8765 \
  --meeting-id resident-m1 \
  --agent-id friend-claude \
  --display-name "Friend Claude" \
  --provider-kind claude_code \
  --secret-ref env:AGENTSASSEMBLE_LAN_INVITE_SECRET \
  --ttl-seconds 600 \
  --json
```

Verify a token locally:

```bash
python3 -m agentsassemble.cli live-agent lan-invite verify \
  --token "$AGENTSASSEMBLE_LAN_INVITE_TOKEN" \
  --secret-ref env:AGENTSASSEMBLE_LAN_INVITE_SECRET \
  --expected-meeting-id resident-m1 \
  --expected-agent-id friend-claude \
  --json
```

The packet names `client_kind: "native_remote_room_client"` and
`admission.provider_execution: "not_started_by_invite"` so it stays distinct
from `remote_http_bridge`, where the host calls a remote `/agentsassemble/run`
bridge. Treat this as remote agent admission and identity proof only; actual
remote registration, token revocation, authenticated room endpoints, relay, and
WebRTC are later work documented in `docs/no-tailscale-multi-host.md`.

## Codex Live Session Quickstart

The checked-in Codex live examples are for an experimental local Codex-only
resident session. They intentionally cover the same three demo roles in both
`configs/codex-live-session.example.json` and
`configs/live-agents.codex-session.example.json`, use `moderator_called`, and do
not contain real session ids by default. This path can start fresh Codex CLI
sessions and later continue them through `codex exec resume` when Codex emits a
session id. It is not proof that Claude Code, Cursor, Antigravity, Grok, Hermes,
or OpenClaw have provider-native live connectors.

The regression suite includes a no-model Codex lifecycle proof with a temporary
fake `codex` executable on `PATH`. That proof uses these checked-in configs
through the real GUI session API, runs one official round, restarts the
supervised resident group, proves session id continuity through restart by
resuming the captured session ids for the remaining round, finalizes the
meeting, and stops the group offline. It verifies Codex resident plumbing and
`codex exec --sandbox read-only --ignore-rules` command shape without proving
real Codex login, model availability, subscription state, or generation quality.

Check the resident config without starting Codex:

```bash
python3 -m agentsassemble.cli live-agent preflight \
  --config configs/live-agents.codex-session.example.json
```

Start the matching visible meeting and resident group only after you intend to
run local Codex CLI model calls:

```bash
python3 -m agentsassemble.cli live-agent start-session \
  --server http://127.0.0.1:8765 \
  --council-config configs/demo-council.json \
  --agent-config configs/codex-live-session.example.json \
  --live-agent-config configs/live-agents.codex-session.example.json \
  --meeting-id codex-live-demo \
  --group-id codex-live-demo \
  --wait-ready
```

To attach an existing Codex session instead of using the fresh checked-in
example, use `sessions invite`, the GUI `Codex 세션 초대` panel, or the GUI
`입장` flow so real session ids live in `.agentsassemble/*.local.json` rather
than in repository examples.

The same operation-recorded invite path is available from the CLI when the GUI room is running:

```bash
python3 -m agentsassemble.cli sessions invite <codex-session-id> \
  --server http://127.0.0.1:8765 \
  --meeting-id resident-1 \
  --role lore_lawyer \
  --json
```

Replace `<codex-session-id>` with a current local Codex CLI session id. Without
`--server`, `sessions invite` keeps its local file mode and writes only the
selected `--output` config.

Convert the invite config into a resident `run-group` config before starting the invited Codex live sessions:

```bash
python3 -m agentsassemble.cli sessions live-agent-config \
  --input .agentsassemble/codex-live-session.local.json \
  --output .agentsassemble/live-agents.codex-session.local.json \
  --server http://127.0.0.1:8765 \
  --meeting-id resident-1 \
  --engagement-mode moderator_called \
  --json
```

The generated resident config uses `provider_kind: "codex_live_session"` and `connection_kind: "live_session"` for each Codex binding, preserves configured session ids, and omits command arguments so the resident runner applies its default safe Codex command shape. With `--json`, the response also includes `next_commands.preflight` and `next_commands.ensure_session` arrays so another local agent or wrapper can run the next control-plane checks without parsing prose. Compact output shell-quotes those next commands for copy/paste. `ensure-session` is emitted with the generated resident config path and the same normalized process `group_id` that a supervised start would derive from that config filename, so it can target the same meeting/process pair and no-op, start, resume, restart, or recover depending on the current state. This conversion writes a local config only; it does not start resident sessions, execute Codex, or append operation records.

## Memory Capsule Gate

When a prepared or trained session cannot join as a live process, ask for an
explicit memory capsule instead of raw hidden session history. The required
capsule files are:

- `persona.md`
- `memory_summary.md`
- `decision_history.md`
- `lessons_learned.md`
- `evidence_index.json`
- `handoff.md`
- `permissions.json`
- `provenance.json`

Inspect the capsule before letting it influence meeting context:

```bash
python3 -m agentsassemble.cli memory-capsule gate \
  --path /path/to/capsule \
  --json
```

The command reports a safe `agentsassemble.memory_capsule.v0` gate result with
redacted local path evidence, required capsule files status, JSON-object checks for
`evidence_index.json`, `permissions.json`, and `provenance.json`, denied
permission flags, raw session dump detection, and compact source/entry counts.
It does not register an agent, start a provider, import the capsule into a
meeting, read provider-private session state, or print capsule body text.

The gate exits `0` only for `status: "ok"`. It exits `1` when the report is
`failed` or otherwise not meeting-safe, for example when required files are
missing, JSON metadata is malformed, raw dump files such as `session.jsonl` are
present, or `permissions.json` requests implementation, filesystem writes, git
writes, pushes, secrets, tool use, shell, deploy, release, or credential access.
Denied examples include implementation, filesystem writes, git writes, pushes, secrets.
This is a shape and permission gate only; it does not prove the capsule's claims
are true or current.

External or manually driven agents can also post a linked lobby reply through the live-agent endpoint:

```bash
python3 -m agentsassemble.cli live-agent say \
  --server http://127.0.0.1:8765 \
  --agent-id claude-code-live \
  --source-event-id evt1 \
  --auto-chain-depth 1 \
  --json \
  "I saw evt1 and can continue."
```

The live-agent lobby endpoint fills in the agent identity and server-issued `live_agent_endpoint` evidence. It also advances the agent roster with `last_reply_at` from the posted event timestamp, clears stale `last_error` from earlier failures, and, when `--source-event-id` is present, `last_observed_event_id` from that source. Use `--json` to verify the posted event id, `source_event_id`, `auto_chain_depth`, updated agent cursor, and endpoint evidence instead of parsing the compact `Posted <event-id>` line.

## Terminal Self-Service Room Tools

A terminal agent can observe the room through the same live-agent control plane instead of waiting for the resident runner to inject every prompt into its terminal. Register the terminal participant first, then let the agent call the room tools from inside its own Claude, Antigravity, Cursor, or other CLI session.

Read the current room snapshot:

```bash
python3 -m agentsassemble.cli live-agent room \
  --server http://127.0.0.1:8765 \
  --agent-id claude-code-live
```

Read the cursor-based room diff without asking AgentsAssemble to choose the
next action:

```bash
python3 -m agentsassemble.cli live-agent read-since \
  --server http://127.0.0.1:8765 \
  --agent-id claude-code-live \
  --json
```

The read-since diff uses the agent row's `last_observed_event_id` and
`last_observed_live_event_id` by default, or explicit `--after-event-id` and
`--after-live-event-id` overrides when a wrapper is carrying its own cursor. It
returns all lobby and official live events after those cursors, the next cursor
values, compact `room.shared_memory`, and a heartbeat `ack_command`. It does not
post replies, acknowledge delivery, call providers, or mutate presence by
itself. MCP `read_since` exposes the same read-only room diff for participant
tools; the agent should call `heartbeat` only after it has decided what it
observed or replied to.

For a terminal loop that should handle either official work or lobby chat, wait for the next actionable item:

```bash
python3 -m agentsassemble.cli live-agent wait-next \
  --server http://127.0.0.1:8765 \
  --agent-id claude-code-live \
  --timeout 30 \
  --json
```

`wait-next` reads one room snapshot per poll, prefers a targeted unanswered official turn request, then a targeted return-packet event, and then falls back to engagement-aware lobby observation. The JSON payload includes `action: "official_turn"` with an `official-reply` `reply_command`, `action: "return_packet"` with `artifact_path`, `artifact_json_path`, an agent-scoped `read_command`, and a heartbeat `ack_command`, `action: "lobby"` with a `say` `reply_command`, `action: "observe_lobby"` with a heartbeat `ack_command` and no reply command, or `action: "persona_blocks_official_turn"` when a stateful Character Mode agent is intentionally blocked from answering official Work Mode turns with full persona context. That persona-block payload is non-official, includes `reason: "persona_context_blocked_official_turn"`, has no reply command, and its `ack_command` advances the official cursor while recording the safe `last_attention` code. `observe_lobby` is returned for visible lobby events that should advance the cursor but should not be answered under the agent's current engagement mode or chain-depth guard. The `reply_command` arrays include the `--` option boundary before `<reply>`, so terminal/self-service loops can safely replace `<reply>` even when the reply text starts with `-` or `--`. Timeout payloads include both `last_observed_event_id` and `last_observed_live_event_id`, advanced to the latest visible event ids in that room snapshot, so a terminal loop can keep lobby and official cursors separate and optionally heartbeat cursor-only observation without replying.

For Play Mode free conversation, start a bounded flow instead of scheduling
fixed official turns:

```bash
python3 -m agentsassemble.cli live-agent flow \
  --server http://127.0.0.1:8765 \
  --meeting-id resident-m1 \
  --topic "고죠 사토루 vs 스쿠나" \
  --duration-seconds 180 \
  --json
```

`live-agent flow` calls `/api/live-agent-flow/start`, waits for the room server
to report `finished`, `stopped`, or `failed`, and never starts provider CLIs by
itself. The server temporarily moves only host-approved resident agents for that
meeting into `engagement_mode: "flow"`, restores their previous modes on stop or
finish, and appends scoped lobby control events with `flow_id` and
`flow_meeting_id`. Public `/api/lobby` posts cannot create flow control events.
Resident flow runners still prefer targeted official-turn requests first; when
only Play Mode lobby events are pending, they ask the provider for one JSON
decision and publish only the visible `message`. The action, reason, target,
source event, chain depth, and flow id stay as lobby metadata. `wait` advances
the lobby cursor without posting. Silence checks are internal idle ticks rather
than visible moderator messages; when the room is caught up, a runner may use
the latest flow event as the source for one invisible tick candidate, but the
room does not post "continue talking" nudges on behalf of a moderator. Cooldown
and optional per-agent or total turn budgets bound automated loops when the
operator chooses to use those limits.

The flow status endpoint is read-only:

```text
GET /api/live-agent-flow?meeting_id=resident-m1
```

It returns `flow` for the selected running, stopped, finished, or restored
flow, `flow_events` scoped to that flow id for new frontends, and
`events` as a compatibility room tail for older callers. After a GUI server
restart, the status path restores the latest flow state from durable lobby log
metadata so a recently running or finished Play Mode room does not appear as a
blank idle room just because in-memory supervisor state was lost. The GUI should
prefer `flow_events` for Play Mode display and treat those events as unofficial
room flow, not transcript evidence.

When resident finalization writes per-agent return packets, it appends one non-official targeted return-packet event for each bound agent whose `return_packets/<role>.md` and `.json` artifacts exist. The event points only at relative artifact paths, carries `artifact_kind: "return_packet"`, and is delivered through `/api/live-agents/<agent_id>/room` only to the matching agent. If that live event later falls out of the bounded room tail, `/room` reprojects the relative return-packet path from the public artifact files and the meeting's agent binding, still without reading packet bodies into the event, and only while the packet is still pending for that agent. A terminal, self-service, or external/manual agent can read only its own packet body through `GET /api/live-agents/<agent_id>/return-packet?meeting_id=<meeting_id>&source_event_id=<event_id>` or the matching `python3 -m agentsassemble.cli live-agent return-packet` command returned as `read_command`. The read path validates the current live-agent row's meeting, agent binding, and source event, reads only the expected `return_packets/<role>.md` and `.json` files, does not accept arbitrary artifact paths, and does not heartbeat, acknowledge, post replies, append operations, or expose sibling packets. Once the agent has acknowledged the original return-packet event, the projected fallback id, or any later live event in the same meeting log, `/room` stops reprojecting that packet. After reading it, a terminal or self-service loop should run the returned `ack_command` so the roster's `last_observed_live_event_id` moves past that packet without posting a lobby reply. If the returned `read_command` is missing, exits non-zero, times out, or cannot be launched, the checked-in self-service wrapper and credential-free smoke child report `status: "error"` with the packet cursor and do not run the `ack_command`.

For meeting-bound agents, the room snapshot also includes compact shared_memory in `/api/live-agents/<agent_id>/room`. When current official live events exist, the room read treats the official log as the authoritative source and projects compact memory from it without rewriting the artifact files, even if `shared_memory/index.json` is stale, malformed, missing, or has matching metadata with untrusted body text. The projection is cached by the live event file state so hot room polling does not repeatedly full-read the same log. Durable index or embedded meeting memory is used only when no usable current official live events exist. The compact memory includes bounded counts, rolling summary, decisions, open questions, and action items. `wait-room-event`, `wait-official-turn`, and wait-next event payloads include `room.shared_memory` from that same compact room snapshot so terminal, self-service, and external/manual agents see the same official-only background context as parent-managed resident prompts. It does not include lobby chat, side chat, review checkpoints, private turn requests for other agents, provider prompts, config paths, endpoint URLs, auth refs, log tails, or provider output.

Wait for the next non-self lobby event:

```bash
python3 -m agentsassemble.cli live-agent wait-room-event \
  --server http://127.0.0.1:8765 \
  --agent-id claude-code-live \
  --timeout 30 \
  --json
```

The wait command polls `/api/live-agents/<agent_id>/room`, starts after `--after-event-id` or the agent roster's `last_observed_event_id`, skips the agent's own lobby messages, skips empty events, and applies the same chain guard with `--max-chain-depth`. When the cursor event is no longer in the bounded room snapshot, the command falls back to scanning the visible snapshot instead of treating the missing cursor as fatal. A JSON event response includes `source_event_id`, the next `auto_chain_depth`, the raw lobby event, compact room counts, and a `reply_command` array that shows the matching `live-agent say` call. A JSON timeout response advances `last_observed_event_id` to the latest visible lobby event id from that snapshot, so wrappers can heartbeat observation for skipped self, empty, or over-depth events.

Post the reply through the linked live-agent endpoint:

```bash
python3 -m agentsassemble.cli live-agent say \
  --server http://127.0.0.1:8765 \
  --agent-id claude-code-live \
  --source-event-id evt1 \
  --auto-chain-depth 1 \
  "I saw evt1 and can continue."
```

If the terminal agent chooses to observe without replying, it can advance its cursor with a heartbeat instead of posting:

```bash
python3 -m agentsassemble.cli live-agent heartbeat \
  --server http://127.0.0.1:8765 \
  --agent-id claude-code-live \
  --status online \
  --last-observed-event-id evt1
```

Official meeting turns use the same self-service pattern. A moderator-called terminal agent can wait for its next targeted official request:

```bash
python3 -m agentsassemble.cli live-agent wait-official-turn \
  --server http://127.0.0.1:8765 \
  --agent-id claude-code-live \
  --timeout 30 \
  --json
```

`wait-turn-request` is accepted as an alias for older scripts. The wait command reads the agent-visible `live_events` from `/api/live-agents/<agent_id>/room`, starts after `--after-event-id` or the roster's `last_observed_live_event_id`, skips requests for other agents, and skips visible requests that already have an official reply or matching cancellation for the same agent. When the live cursor event is no longer in the bounded room snapshot, the command falls back to scanning the visible snapshot instead of treating the missing cursor as fatal. A JSON event response includes `meeting_id`, `source_event_id`, the raw `live_agent_turn_request`, compact room counts, and a `reply_command` array using `live-agent official-reply`.

Post the official reply through the same endpoint used by resident runners:

```bash
python3 -m agentsassemble.cli live-agent official-reply \
  --server http://127.0.0.1:8765 \
  --agent-id claude-code-live \
  --meeting-id meeting-1 \
  --source-event-id live-evt1 \
  "Official answer text."
```

`answer-turn` is accepted as an alias for older scripts.

Successful official replies advance `last_observed_live_event_id` separately from the lobby cursor, so a terminal agent can move between official meeting turns and lobby chat without mixing the two streams.
If the source request has been closed with `live_agent_turn_cancelled`,
`official-reply` refuses the reply instead of turning a closed control event into
new transcript evidence.

This is closer to direct room participation than the PTY prompt-injection path: the model running inside the terminal can pull the room snapshot, wait for a fresh lobby event or official turn request, and publish its own linked reply. It is still a bounded CLI polling surface, not Claude Code Channels, Antigravity native sessions, a tmux subscription protocol, or OS-level sandbox enforcement.

## Self-Service Resident Processes

Use `--connection-kind self_service` when the provider process should stay resident and call `wait-next`, `say`, `official-reply`, and heartbeat acknowledgements by itself. In this mode AgentsAssemble registers the agent, starts the configured command with `stdin` closed, exports the live-agent environment variables, sends parent liveness heartbeats, and stops the process on resident shutdown. A direct `live-agent run` resident treats `SIGTERM` through the same clean shutdown path as `KeyboardInterrupt`: it closes the self-service child or active command runner, restores the temporary signal handler, prints the normal stopped summary, and exits `0` instead of leaking a traceback. If the self-service child exits with a non-zero status outside shutdown, the parent records `status: "error"` with a safe `last_error` and does not overwrite that evidence with a final offline heartbeat. When the GUI supervisor later reconciles a failed process group, it preserves matching agent rows that are already `error` and reports them as `preserved_error` in the lifecycle offline attention instead of converting the provider failure into an ordinary offline shutdown. It does not read room events, build `delegate_prompt` or `official_turn_prompt`, or write event prompts into the child process.

```bash
python3 -m agentsassemble.cli live-agent run \
  --server http://127.0.0.1:8765 \
  --agent-id custom-cli-live \
  --display-name "Custom CLI Wrapper" \
  --provider-kind local_cli \
  --connection-kind self_service \
  --meeting-id resident-1 \
  --engagement-mode always \
  --command python3 scripts/my_self_service_agent.py
```

The child process receives `AGENTSASSEMBLE_SERVER`, `AGENTSASSEMBLE_AGENT_ID`, `AGENTSASSEMBLE_DISPLAY_NAME`, `AGENTSASSEMBLE_PROVIDER_KIND`, `AGENTSASSEMBLE_CONNECTION_KIND`, `AGENTSASSEMBLE_MEETING_ID`, `AGENTSASSEMBLE_ENGAGEMENT_MODE`, `AGENTSASSEMBLE_MAX_CHAIN_DEPTH`, `AGENTSASSEMBLE_POLL_INTERVAL`, and `AGENTSASSEMBLE_HEARTBEAT_INTERVAL`. It also receives shell-escaped room command templates: `AGENTSASSEMBLE_ROOM_COMMAND`, `AGENTSASSEMBLE_WAIT_NEXT_COMMAND`, `AGENTSASSEMBLE_WAIT_ROOM_EVENT_COMMAND`, `AGENTSASSEMBLE_WAIT_OFFICIAL_TURN_COMMAND`, `AGENTSASSEMBLE_SAY_COMMAND_TEMPLATE`, `AGENTSASSEMBLE_OFFICIAL_REPLY_COMMAND_TEMPLATE`, `AGENTSASSEMBLE_HEARTBEAT_COMMAND_TEMPLATE`, and `AGENTSASSEMBLE_LEAVE_COMMAND`. The wait and leave commands are directly runnable; children should `shlex.split()` the templates and replace placeholders inside argv tokens rather than using `eval` or `shell=True`. Reply templates contain `{meeting_id}`, `{source_event_id}`, `{auto_chain_depth}`, and `{message}` placeholders where applicable. The heartbeat template contains `{status}`, `{last_error}`, `{last_attention}`, `{last_reply_at}`, `{last_observed_event_id}`, and `{last_observed_live_event_id}` placeholders so a child can report `working`, `online`, `error`, safe attention codes, lobby cursor, official cursor, and reply timestamp evidence through the same presence path as parent-managed resident runners. Optional heartbeat value placeholders are embedded in `--last-error={last_error}` style argv tokens so values beginning with `-` or `--` are parsed as values, not options. Unreplaced optional heartbeat placeholders are ignored by the CLI payload builder; blank replacement values intentionally clear those fields. Unknown `last_attention` values are redacted instead of preserved. `AGENTSASSEMBLE_LEAVE_COMMAND` runs `live-agent leave --json` for the same server and agent id so a self-service child can intentionally mark itself `offline` before exiting without stopping its supervised process group or starting a provider. Parent liveness heartbeats refresh `last_seen_at` without downgrading a child-reported `working` or `error` status back to `online`. When a wait command times out after observing only non-actionable events, such as self-authored lobby messages, over-depth lobby chains, empty lobby messages, or non-turn live updates, its JSON timeout payload returns advanced `last_observed_event_id` and `last_observed_live_event_id` cursor values. Self-service children should send an `online` cursor-only heartbeat with those values so health and stale-restart checks know the resident is still watching the room even though it did not reply. A self-service child is responsible for its own room loop and replies; parent summary counts therefore report parent-managed replies, usually `0`, even when the child posts messages.

`scripts/my_self_service_agent.py` is the checked-in custom wrapper example used by `configs/live-agents.self-service.example.json`. It reads the exported command templates, polls `wait-next`, posts lobby replies through `say`, posts official replies through `official-reply`, reads `return_packet` actions through the returned `read_command` before running the `ack_command` or heartbeat cursor fallback, treats missing or failed return-packet reads as `error` heartbeats without acknowledging delivery, treats failed lobby and official reply commands as cursor-bearing `error` heartbeats, treats failed return-packet and observe-lobby ack commands as `error` heartbeats with the matching cursor, sends cursor-only `online` heartbeats for timeout observations, and sends `working`, `online`, or `error` heartbeats without reading stdin or receiving per-event prompt injection from the parent supervisor.

## Director-Led Agent-Owned Template

The checked-in director-led bundle demonstrates the intended organization shape
without launching real provider CLIs:

```text
configs/director-led-team.example.json
configs/agents.director-led-team.example.json
configs/live-agents.director-led-team.self-service.example.json
```

It defines a director, product lead, engineering lead, design lead, and
implementer as room roles. Display names can say things like `Opus 4.6
Director`, `5.5 xHigh Engineering Lead`, or `5.4 mini Implementer`, but those
labels are provider slots only. The safe v1 proof uses fake/self-service agents
and `scripts/my_self_service_agent.py`; real Opus, Codex, Kiro, Cursor,
Antigravity, DeepSeek, or other provider execution still needs the normal
explicit operator approval, preflight, and provider-specific smoke. This bundle
proves that the room can host an organization-style conversation where agents
read with `wait-next`, reply with `say` or `official-reply`, and heartbeat their
own cursors through the room contract instead of receiving hidden host prompt injection.

## Start A Resident Meeting

Use `start-session` when you want the operator path that creates the visible resident meeting and starts its supervised resident group in one bounded operation:

```bash
python3 -m agentsassemble.cli live-agent start-session \
  --server http://127.0.0.1:8765 \
  --meeting-id resident-1 \
  --group-id resident-main \
  --council-config configs/demo-council.json \
  --agent-config configs/agents.start-session.example.json \
  --live-agent-config configs/live-agents.start-session.example.json \
  --connect-timeout 5
```

The HTTP control-plane path is:

```text
POST /api/live-agent-sessions/start
```

with `meeting_id`, `group_id`, `council_config_path`, `agent_config_path`, `live_agent_config_path`, `connect_timeout_seconds`, and the same `auto_restart`, `max_restarts`, `restart_backoff_seconds`, and `stale_restart_after_seconds` options used by supervised process start. The coordinator preflights the resident group config before creating the meeting, refuses if the resident group manifest does not exactly match the meeting's bound agent ids, and checks direct provider kinds plus compatible resident connection kinds. A meeting provider of `remote_http_bridge` must use a resident `remote_bridge` connection, but the resident `provider_kind` may still name the agent behind that bridge, such as `claude_code`. After those checks, it creates the normal visible resident meeting, starts the supervised group, then waits briefly for bound agents to appear as `online` or `working` and attached to the meeting.

The paired examples `configs/agents.start-session.example.json` and `configs/live-agents.start-session.example.json` are intentionally local fake CLI configs for the demo council. They use the same bound agent ids as `configs/demo-council.json`, keep resident `meeting_id` blank, and are safe for first-pass validation without real Claude, Gemini, Cursor, account login, billing, or bridge setup.

For the safe three-agent completion proof, run all demo template rounds and
finalize before stopping the resident group:

```bash
python3 -m agentsassemble.cli live-agent start-session \
  --server http://127.0.0.1:8765 \
  --meeting-id resident-fake-demo \
  --group-id resident-fake-demo \
  --council-config configs/demo-council.json \
  --agent-config configs/agents.start-session.example.json \
  --live-agent-config configs/live-agents.start-session.example.json \
  --connect-timeout 5 \
  --run-remaining-rounds \
  --round-timeout 8 \
  --max-rounds 2 \
  --finalize-after-rounds \
  --wait-ready

python3 -m agentsassemble.cli live-agent stop-session \
  --server http://127.0.0.1:8765 \
  --meeting-id resident-fake-demo \
  --group-id resident-fake-demo
```

The checked-in demo template has two rounds. Do not cap this proof at
`--max-rounds 1`; that intentionally leaves a template round remaining and
causes `finalization.reason: "rounds_still_remaining"` instead of writing final
artifacts.

Resident group configs used with `start-session` should either leave each agent's `meeting_id` blank, letting the created roster binding supply it, or match the explicit `--meeting-id`. A resident config that points an agent at a different meeting is refused before state is written, because that runner would otherwise reconnect itself away from the new official meeting.

The response reports `status: "ready"` only when the supervised group is still `running`, its process manifest covers every expected agent, and every expected agent has `online` or `working` presence for the same meeting that is not older than the group's `started_at` when both timestamps are available. It reports `status: "starting"` when the group launched but the process or presence evidence is not ready before the bounded wait ends. A pre-start `online` or `working` roster row is reported as `agent_id:not_reconnected` instead of connected. CLI exit code is `0` for `ready`, `1` for `starting`, and `2` for refused, HTTP, or argument errors. Add `--wait-ready` when a script should verify the final read-only `GET /api/live-agent-sessions/readiness` snapshot after the initial bounded command, even if that initial response already says `ready`. `--wait-timeout` defaults to 30 seconds and `--wait-poll-interval` defaults to 2 seconds. This follow-up wait does not append repeated `session.check` operations and does not call providers, probes, official turns, smoke checks, decisions, or new transcript finalization work; it exits `0` once the target becomes ready and `1` with the last observed summary on timeout. If the initial command included bound-agent probes, remaining-round execution, or finalization after remaining rounds, their sanitized `reply_probe`, `auto_rounds`, and `finalization` evidence is preserved in the printed final summary; the readiness status comes from the targeted snapshot, and the CLI still exits `1` when requested probes, remaining rounds, or finalization did not complete successfully. The initial command path does not run official turns unless `--run-remaining-rounds` is supplied, and it never runs smoke probes, model calls, remote bridge `/agentsassemble/run`, decisions, or transcript finalization unless `--finalize-after-rounds` is supplied with successful remaining rounds. Partial meeting and process state remains visible for recovery instead of being deleted; if process launch fails after a generated meeting is created, the failed response and `session.start` operation include the safe meeting id for recovery. The compact CLI session summary includes connection, process, ownership attention, optional auto-round counts, and optional finalization status so duplicate active group ownership, manifest/process issues, or artifact-write refusal are not hidden behind a simple connected count.
When the target process has bounded lifecycle reason evidence, targeted `session-readiness` and `check-session` responses include the same safe `process_reason` used by health summaries, and the compact CLI line prints it after the attention list. This keeps a focused session check from hiding reasons such as `recovered_unknown orphan running record marked unknown`.

The opt-in automatic path uses `--run-remaining-rounds` in the CLI or `run_remaining_rounds: true` in `POST /api/live-agent-sessions/start`. It reuses the bounded remaining-rounds fields as `round_timeout_seconds`, `round_max_rounds`, and `round_stop_on_timeout`. The server only calls remaining official rounds after `status: "ready"`. If the session is still `starting`, the response includes `auto_rounds.status: "skipped"` and `auto_rounds.reason: "session_not_ready"` and does not append official turn requests. When it does run, `auto_rounds` reports the same sanitized aggregate counts and statuses as `call-remaining-rounds`; degraded round results remain visible instead of being hidden as session recovery.

Add `--finalize-after-rounds` or `finalize_after_rounds: true` only when the operator wants successful remaining rounds to write durable resident meeting artifacts in the same bounded control action. Finalization is a sibling payload named `finalization`, not part of `auto_rounds`, because it writes meeting artifacts. The server attempts it only when remaining rounds report `answered` or `complete` and a full fresh meeting read shows no template rounds still remaining. If `round_max_rounds` leaves later template rounds unrun, finalization is skipped with `finalization.reason: "rounds_still_remaining"`. If rounds are skipped, timed out, stopped, or blocked by a failed probe, finalization is skipped with `rounds_not_ready`. If artifact finalization refuses the meeting, for example because a pending official turn request still has no strict official reply, the response keeps the successful `auto_rounds` evidence and reports `finalization.status: "failed"` with a sanitized reason. CLI session commands exit `1` for skipped or failed finalization, while `finalized` and `already_finalized` are success states.

Resident official replies also refresh deterministic shared meeting memory artifacts: `shared_memory/rolling-summary.md`, `shared_memory/open-questions.md`, `shared_memory/action-items.md`, and `shared_memory/index.json`. These artifacts are derived only from strict official transcript events; lobby messages, side chat, review checkpoints, status events, provider prompts, and private turn requests are excluded. The first slice does not infer tasks from arbitrary prose: action items and open questions are extracted only from explicit official markers such as `Action item:` and `Open question:` or from clear question-form official lines. Finalization rewrites the same artifacts, Archive can project them before finalization without writing files, and room reads use a cached projection from the current official log whenever official events exist, regardless of stale or untrusted durable index contents.

Resident `delegate_prompt` and `official_turn_prompt` use that compact room payload as a `Shared meeting memory` block before the current event or official request. The block is background context only: the runner still replies to exactly one selected lobby event or targeted official turn, and chain/cursor/self-loop guards still decide whether anything should answer.

Add `--probe-bound-agents` or `probe_bound_agents: true` when a session entrypoint must prove every bound resident can actually answer a fresh lobby event before optional auto-rounds begin. The server probes all bound ready-session agents one by one. The probe is bounded by `--probe-timeout` / `probe_timeout_seconds`, capped at 240 seconds per agent, and applies to `start-session`, `resume-session`, `restart-session`, `recover-session`, and `ensure-session`. Moderator-called, manual, and watch agents are temporarily opened to `human_only` for the probe event and restored afterward, so the check works with the default session engagement policy without leaving the room in always-on mode or leaving an operator override timestamp behind. Probe replies are summarized under `reply_probe`; operation history stores only sanitized counts, agent ids, and statuses. It omits reply text, prompts, config paths, command arguments, endpoint URLs, auth refs, tokens, provider output, and log tails. If any bound probe is skipped, timed out, or failed, optional remaining rounds are skipped with `auto_rounds.reason: "probe_not_ready"` and the session operation is recorded as degraded.

The operation ledger records one sanitized `session.start` entry with result status, meeting id, group id, expected/connected counts, process status, safe agent ids, connection/process attention, bounded `auto_rounds` counts when the opt-in path is requested, and `finalization_status`, `finalization_reason`, and official event count when artifact finalization is requested. It does not record config paths, command arguments, endpoints, auth refs, prompts, log tails, provider output, replies, or official turn content.

Use `resume-session` when the visible resident meeting already exists and you want one bounded operator action to reconnect its supervised group and re-check readiness:

```bash
python3 -m agentsassemble.cli live-agent resume-session \
  --server http://127.0.0.1:8765 \
  --meeting-id resident-1 \
  --group-id resident-main \
  --live-agent-config configs/live-agents.start-session.example.json \
  --connect-timeout 5
```

The HTTP control-plane path is:

```text
POST /api/live-agent-sessions/resume
```

with `meeting_id`, `group_id`, `live_agent_config_path`, `connect_timeout_seconds`, and the same process restart and optional `run_remaining_rounds` fields as `start-session`. `meeting_id` must name an existing meeting. Resume reads that meeting's `agent_bindings` and `provider_configs`, validates the resident group manifest against those bound agents, and refuses mismatches before starting processes. It never calls the meeting creation path and never overwrites the existing meeting.

If the matching process group is already `running`, resume reuses it only when it is unowned legacy state or already owned by the requested meeting, then waits for real presence evidence. Presence rows whose `last_seen_at` predates the reused or freshly started process group's `started_at` are reported as `agent_id:not_reconnected` and do not make the session ready. If the group is missing, stopped, `restarting`, `unknown`, or `error`, resume starts a fresh supervised process from the supplied config and group id so the process manifest matches the config that was just validated against the meeting. A `restarting` group is a pending supervisor restart/backoff record rather than proof of a live process, so `resume-session` and `ensure-session` may bring it back immediately through the validated config. Fresh starts from `start-session` and `resume-session` stamp the supervised group with the safe meeting id so later process inspection can show which room the group belongs to. Missing roster entries are repaired as `offline` rows attached to the meeting so the operator can see stale evidence, but they are not counted as connected until a real registration or heartbeat reports `online` or `working`.

`resume-session --run-remaining-rounds` uses the same ready and optional bound-probe gates as `start-session`: remaining official rounds run only when the resumed session returns `status: "ready"` and any requested `--probe-bound-agents` check passes. Otherwise `auto_rounds.status` is `skipped` with `reason: "session_not_ready"` or `reason: "probe_not_ready"` and no official turn request events are appended. `resume-session`, `restart-session`, and `recover-session` also accept the same `--wait-ready`, `--wait-timeout`, `--wait-poll-interval`, and `--finalize-after-rounds` CLI options as `start-session`, using the read-only targeted readiness endpoint after the initial control-plane POST while preserving any finalization evidence from the mutating response. The operation ledger records one sanitized `session.resume` entry with the same safe count/status/finalization fields as `session.start`; it does not record config paths, command arguments, endpoints, auth refs, prompts, log tails, provider output, replies, or official turn content.

Use `restart-session` when the visible resident meeting already exists and you want one bounded operator action to restart its matching supervised group and prove that agents reconnected after the restart:

```bash
python3 -m agentsassemble.cli live-agent restart-session \
  --server http://127.0.0.1:8765 \
  --meeting-id resident-1 \
  --group-id resident-main \
  --connect-timeout 5
```

The HTTP control-plane path is:

```text
POST /api/live-agent-sessions/restart
```

with `meeting_id`, `group_id`, and `connect_timeout_seconds`; `--run-remaining-rounds` also sends the optional round-control fields documented below. `meeting_id` must name an existing meeting and `group_id` must name an existing supervised process group. Restart reads the meeting's bound agent ids, validates the current process group manifest against those ids, preflights the persisted restart config and server when the process record names a config, refuses blank persisted config/server fields or manifest mismatches before touching processes, stops the group first when it is currently `running` or `restarting`, clears only that meeting's bound roster rows to `offline`, then starts a fresh supervised group from the persisted process record through the process supervisor's restart path.

The response reports `status: "ready"` only when the restarted process is `running`, its returned manifest matches every expected agent without extras or duplicates, and every expected agent reports fresh `online` or `working` presence for that same meeting after the stale-presence reset. It reports `status: "starting"` when the process restarted but the returned process manifest or fresh presence evidence is still incomplete before the bounded wait ends. This reset is intentional: stale online rows cannot prove restart readiness. A roster row for the same `agent_id` that is currently attached to another meeting is left untouched and reported as `agent_id:wrong_meeting`.

`restart-session --run-remaining-rounds` uses the same ready and optional bound-probe gates as `start-session` and `resume-session`: remaining official rounds run only when the restarted session returns `status: "ready"` and any requested `--probe-bound-agents` check passes. Otherwise `auto_rounds.status` is `skipped` with `reason: "session_not_ready"` or `reason: "probe_not_ready"` and no official turn request events are appended. The round options are the same `--round-timeout`, `--max-rounds`, and `--stop-on-timeout` fields used by start/resume, and `--finalize-after-rounds` uses the same no-rounds-remaining guard before writing artifacts.

CLI exit code is `0` for `ready` with no auto-round or requested-finalization failure, `1` for `starting`, degraded auto-rounds, or skipped/failed finalization, and `2` for refused, HTTP, or argument errors. This path does not run official turns unless `--run-remaining-rounds` is supplied, and it never runs smoke probes, model calls outside the called resident official turns, remote bridge `/agentsassemble/run` outside those resident turns, decisions, or transcript finalization unless `--finalize-after-rounds` is explicitly supplied.

The operation ledger records one sanitized `session.restart` entry with result status, meeting id, group id, expected/connected counts, process status, safe agent ids, attention fields, and, when requested, remaining-round counts/statuses. It does not record config paths, command arguments, endpoint URLs, auth refs, prompts, log tails, provider output, replies, or official turn content.

Use `recover-session` when the visible resident meeting already exists and the matching supervised group is historical, `unknown`, or `error`, and you want to relaunch it from its persisted process record instead of supplying a config path again:

```bash
python3 -m agentsassemble.cli live-agent recover-session \
  --server http://127.0.0.1:8765 \
  --meeting-id resident-1 \
  --group-id resident-main \
  --connect-timeout 5
```

The HTTP control-plane path is:

```text
POST /api/live-agent-sessions/recover
```

with `meeting_id`, `group_id`, and `connect_timeout_seconds`; `--run-remaining-rounds` also sends the optional round-control fields documented below. `meeting_id` must name an existing meeting and `group_id` must name an existing supervised process group with a persisted manifest. Recovery validates the stored meeting owner and the current process manifest against the meeting's bound agent ids before touching roster state. When the process record names a persisted config, `recover-session` preflights the persisted recover config and server before roster or process side effects, so manifest drift, duplicate config agents, missing launch evidence, or supervisor-specific preflight refusals stop the operation before stale rows are cleared. It refuses `running` groups with an already-running error, refuses other non-recoverable states such as `stopped` with a `use restart` message, and only then clears that meeting's bound roster rows to `offline` before calling the process supervisor's recover path.

The response reports `status: "ready"` only when the recovered process is `running`, its returned manifest matches every expected agent without extras or duplicates, and every expected agent reports fresh `online` or `working` presence for that same meeting after the stale-presence reset. It reports `status: "starting"` when the process recovered but the returned process manifest or fresh presence evidence is still incomplete before the bounded wait ends. Like restart, a roster row for the same `agent_id` that is currently attached to another meeting is left untouched and reported as `agent_id:wrong_meeting`.

`recover-session --run-remaining-rounds` uses the same ready and optional bound-probe gates as the other session entrypoints: remaining official rounds run only when the recovered session returns `status: "ready"` and any requested `--probe-bound-agents` check passes. Otherwise `auto_rounds.status` is `skipped` with `reason: "session_not_ready"` or `reason: "probe_not_ready"` and no official turn request events are appended. The round options are the same `--round-timeout`, `--max-rounds`, and `--stop-on-timeout` fields used by start/resume/restart, and `--finalize-after-rounds` follows the same artifact-finalization rules after successful remaining rounds.

CLI exit code is `0` for `ready` with no auto-round or requested-finalization failure, `1` for `starting`, degraded auto-rounds, or skipped/failed finalization, and `2` for refused, HTTP, or argument errors. This path does not run official turns unless `--run-remaining-rounds` is supplied, and it never runs smoke probes, model calls outside the called resident official turns, remote bridge `/agentsassemble/run` outside those resident turns, decisions, or transcript finalization unless `--finalize-after-rounds` is explicitly supplied.

The operation ledger records one sanitized `session.recover` entry with result status, meeting id, group id, expected/connected counts, offline reset counts, process status, safe agent ids, attention fields, and, when requested, remaining-round counts/statuses. It does not record config paths, command arguments, endpoint URLs, auth refs, prompts, log tails, provider output, replies, or official turn content. Use process-level `live-agent processes recover <group_id>` only when you intentionally want a process control action without the meeting-aware roster reset and readiness proof.

Use `check-session` when a resident meeting already exists and you want a meeting-scoped proof of current readiness without starting, stopping, probing, running official rounds, or calling providers:

```bash
python3 -m agentsassemble.cli live-agent check-session \
  --server http://127.0.0.1:8765 \
  --meeting-id resident-1 \
  --group-id resident-main \
  --fail-on-degraded
```

The HTTP control-plane path is:

```text
POST /api/live-agent-sessions/check
```

with `meeting_id` and `group_id` only. The check reads the existing meeting's `agent_bindings`, reads the current supervised group manifest/status through the process supervisor, and reads live-agent roster presence for the same meeting id. It returns `status: "ready"` only when the group is `running`, its manifest matches every bound agent without extras, every bound agent has a host-approved binding whose provider config is still present, every bound agent is `online` or `working` for that meeting, and no other active non-diagnostic group owns the same meeting. When comparable timestamps are present, each heartbeat must be at or after the process group's `started_at`; older rows are reported as `agent_id:not_reconnected`. Otherwise it returns `status: "degraded"` with process, connection, and ownership attention such as `group:stopped`, `agent-a:offline`, `agent-a:wrong_meeting`, `agent-a:not_reconnected`, `agent-a:binding_provider_missing`, `agent-x:extra_in_group`, or `meeting:duplicate_active_group`.

The default CLI exit code is `0` for a successful check request, even when the session is degraded, matching the global `health` command's default read-only behavior. Add `--fail-on-degraded` when a script should exit `1` unless the session is `ready`. Transport, argument, and validation failures still exit `2`.

The operation ledger records one sanitized `session.check` entry for this explicit operator check with result status, meeting id, group id, expected/connected counts, process status, safe agent ids, and attention fields. It does not record config paths, command arguments, endpoint URLs, auth refs, prompts, log tails, provider output, replies, or official turn content. Ordinary auto-refresh of `/api/live-agents`, `/api/live-agent-processes`, and `/api/live-agent-health` does not create `session.check` records.

Use `session-readiness` when automation needs the same targeted meeting/group readiness snapshot without appending a `session.check` operation record:

```bash
python3 -m agentsassemble.cli live-agent session-readiness \
  --server http://127.0.0.1:8765 \
  --meeting-id resident-1 \
  --group-id resident-main \
  --fail-on-degraded
```

The HTTP path is read-only:

```text
GET /api/live-agent-sessions/readiness?meeting_id=resident-1&group_id=resident-main
```

It reuses the session readiness rules from `check-session`: the target meeting must exist, the target process group manifest must match the meeting's bound agents, each bound agent must still have a resolvable provider config, current presence must belong to the same meeting, comparable `last_seen_at` values must be at or after the process `started_at`, and no other active non-diagnostic group may own the same meeting. It returns `ready` or `degraded` with the same process, connection, and ownership attention fields, but it does not start, stop, restart, recover, probe, call providers, run official turns, mutate roster rows, or append operation history. The CLI exits `0` when the request succeeds unless `--fail-on-degraded` is set and the targeted session is not `ready`; transport, argument, and validation failures still exit `2`.

Use `ensure-session` or the GUI `세션보장` control when a script or operator should make one resident session ready without manually choosing between start, resume, restart, or recover:

```bash
python3 -m agentsassemble.cli live-agent ensure-session \
  --server http://127.0.0.1:8765 \
  --meeting-id resident-1 \
  --group-id resident-main \
  --council-config configs/demo-council.json \
  --agent-config configs/agents.start-session.example.json \
  --live-agent-config configs/live-agents.start-session.example.json \
  --connect-timeout 5 \
  --wait-timeout 30
```

The command first reads `GET /api/live-agent-sessions/readiness` when both `meeting_id` and `group_id` are known. If that snapshot is already `ready`, the CLI still posts once to `/api/live-agent-sessions/ensure` so the server can compare the requested resident config against live roster state and run any requested `--probe-bound-agents`, `--run-remaining-rounds`, or `--finalize-after-rounds` gates. When `meeting_id` is blank but `group_id` names a supervised process group with a safe stored owner, server-side `ensure` adopts that process-owned meeting id before choosing an action; this lets repeated `auto-join` or `ensure-session` calls no-op, resume, restart, or recover the existing resident session instead of blindly starting another meeting. If that stored meeting no longer exists, `ensure` fails with that missing meeting id and does not create a replacement meeting. Server-side `ensure` also refuses to trust a ready snapshot when the requested resident config names a different non-empty `session_id` for a currently connected meeting agent; in that case it chooses `restart` so a refreshed Codex or live-session resident does not keep running against stale session state. If the meeting is missing or no target ids are supplied, it calls `start-session`. If the target meeting exists but the process group is absent, or if the target is degraded with a `running` or `restarting` process, it calls `resume-session` so the existing meeting can reconnect without recreating the meeting or forcing an unnecessary restart. If the process is `stopped`, it calls `restart-session`; if it is `unknown` or `error` for an existing process group, it calls `recover-session`. The CLI and API share the same base `session_ensure_action` helper for start/resume/restart/recover decisions, while the server `ensure` endpoint owns the extra ready-session drift guard before committing to `none`. After any mutating command, `ensure-session` uses the same read-only readiness wait as `--wait-ready`, so the final success decision comes from the targeted readiness snapshot rather than from the mutating command's shorter response, while preserving any `reply_probe` and `auto_rounds` evidence plus any `finalization` evidence returned by the control-plane call. It returns `0` once ready with requested checks complete and `1` with the last observed summary on timeout, degraded rounds, or skipped/failed finalization.

The GUI and HTTP API expose the same one-shot policy through:

```text
POST /api/live-agent-sessions/ensure
```

The server records exactly one sanitized `session.ensure` operation for the API call. Its details include the safe `ensure_action` value (`none`, `start`, `resume`, `restart`, or `recover`) plus the same bounded readiness counts and attention fields as other session controls. When the chosen action is `none`, the server still runs requested `probe_bound_agents` and `run_remaining_rounds` post-ready checks without starting, resuming, restarting, or recovering the process group. Internal readiness reads stay read-only and do not append `session.check` operation records.

Use `stop-session` when the visible resident meeting already exists and you want one operator action to stop the supervised group and make the roster evidence immediately show that the bound agents are no longer live:

```bash
python3 -m agentsassemble.cli live-agent stop-session \
  --server http://127.0.0.1:8765 \
  --meeting-id resident-1 \
  --group-id resident-main
```

The HTTP control-plane path is:

```text
POST /api/live-agent-sessions/stop
```

with `meeting_id` and `group_id` only. `meeting_id` must name an existing meeting. When the process supervisor can list groups, stop validates that the requested group's agents manifest exactly matches the meeting's bound agent ids before calling `stop_group`, including rejecting duplicate manifest agent ids; a mistyped group id or a group whose manifest belongs to another meeting is refused before any process or roster state is changed. If the group stop call itself fails, bound agents are not marked offline.

After the group is stopped, stop marks only roster rows already bound to that meeting as `offline`. Missing bound rows may be recreated as offline evidence, but a row for the same `agent_id` that is currently attached to another meeting is left untouched and reported in `offline.attention` as `agent_id:wrong_meeting`. The response reports `status: "stopped"` only when the process is no longer running and every expected bound agent was recorded offline for that meeting; otherwise it reports `status: "stopping"` with attention fields for the remaining evidence gap. CLI exit code is `0` for `stopped` and `1` for `stopping`.

The operation ledger records one sanitized `session.stop` entry with result status, meeting id, group id, offline counts, safe agent ids, process status, stopped session-run count, stopped session-run ids, and attention fields. It does not record config paths, command arguments, endpoint URLs, auth refs, prompts, log tails, provider output, replies, or official turn content.

Use `start-meeting` when you want a normal, visible meeting record that is ready for resident live-agent official turns, instead of a diagnostic smoke meeting:

```bash
python3 -m agentsassemble.cli live-agent start-meeting \
  --server http://127.0.0.1:8765 \
  --meeting-id resident-1 \
  --council-config configs/demo-council.json \
  --agent-config configs/agents.example.json
```

The HTTP control-plane path is:

```text
POST /api/live-agent-meetings/start
```

with `meeting_id`, `council_config_path`, and `agent_config_path`. The server creates `meetings/<meeting_id>/live_state.json`, writes `agenda.md`, appends a meeting status live event, registers the approved `agent_bindings` as live-agent roster entries attached to that meeting, and pins their engagement mode to `moderator_called` so a later resident runner registration cannot accidentally turn official participants into lobby auto-chat agents. Explicit engagement modes from the runtime config are overridden on this start path because resident meeting participants are official-turn agents.

The created meeting is not diagnostic and appears in `/api/meetings`, `/api/meetings/latest`, and the GUI meeting selector. It does not start provider commands, run research, call paid APIs, or synthesize a decision. Start or restart the resident process group separately, or use `start-session` when you want the meeting and group start composed for you, then use `call-round` when the roster is attached and the agents are running.

The operation ledger records a sanitized `meeting.start` entry with meeting id, role count, and bound-agent count only. It does not record config paths, command arguments, endpoints, auth refs, prompts, or provider output.

## Moderator-Called Official Turns

`moderator_called` is for official meeting turns, not lobby auto-chat. A resident runner in this mode ignores lobby reply policy and waits for a moderator request in the agent's meeting live event stream.

Request a turn through the API:

```text
POST /api/meetings/<meeting_id>/live-agent-turns/request
```

with a JSON body like:

```json
{
  "agent_id": "claude-code-live",
  "role_id": "architect",
  "display_name": "Claude Code Live",
  "content": "Give the official architecture recommendation.",
  "turn_id": "round_1:0:architect",
  "turn_index": 0
}
```

The CLI wrapper is:

```bash
python3 -m agentsassemble.cli live-agent call \
  --server http://127.0.0.1:8765 \
  --meeting-id meeting-1 \
  --agent-id claude-code-live \
  --role-id architect \
  "Give the official architecture recommendation."
```

The request appends a `live_agent_turn_request` event to `meetings/<meeting_id>/live_events.jsonl`. That request is `channel: "system"` and `official_record: false`; it is a control event, not transcript evidence.

Automation that needs a bounded completion result can call and wait in one step:

```text
POST /api/meetings/<meeting_id>/live-agent-turns/call
```

or:

```bash
python3 -m agentsassemble.cli live-agent call \
  --server http://127.0.0.1:8765 \
  --meeting-id meeting-1 \
  --agent-id claude-code-live \
  --wait \
  --timeout 30 \
  "Give the official architecture recommendation."
```

The wait path creates the same turn request, then polls the meeting's full `live_events.jsonl` until it finds a verified official reply, finds a matching turn cancellation, or reaches the timeout. A reply is accepted only when it is a `kind: "message"` event with `channel: "official"`, `official_record: true`, the requested `actor_id`, and the request event id in `source_event_id`. A cancellation is accepted only when it is a non-official `live_agent_turn_cancelled` system event for the same target agent and source request. Lobby messages, wrong-agent replies, wrong-source replies, generic official messages, and unrelated cancellations do not complete the wait. The API returns `status: "answered"` with `request_event`, `reply_event`, timing fields, and visible live events, `status: "cancelled"` with the cancellation event in `reply_event`, or `status: "timeout"` with the request event and no fabricated reply. The CLI exits `0` for answered, `1` for cancellation or timeout, and `2` for transport or validation errors.

Automation can call multiple official turns in order through:

```text
POST /api/meetings/<meeting_id>/live-agent-turns/sequence
```

with:

```json
{
  "timeout_seconds": 30,
  "stop_on_timeout": false,
  "turns": [
    {
      "agent_id": "claude-code-live",
      "role_id": "architect",
      "display_name": "Claude Code Live",
      "content": "Give the first official turn.",
      "turn_id": "round_1:0:architect",
      "turn_index": 0
    }
  ]
}
```

The sequence path validates the meeting and every listed agent/content before appending the first request, then runs request -> bounded wait one turn at a time. Repeated agents are allowed because replies are matched by `source_event_id`. Top-level `status` is `answered` when every turn answered, `cancelled` when at least one turn was explicitly closed and no timeout/skip occurred, `timeout` when at least one turn timed out and the sequence continued, or `stopped` when `stop_on_timeout` skipped remaining turns. Per-turn status is `answered`, `cancelled`, `timeout`, or `skipped`.

The CLI wrapper is:

```bash
python3 -m agentsassemble.cli live-agent call-sequence \
  --server http://127.0.0.1:8765 \
  --meeting-id meeting-1 \
  --turns-file turns.json \
  --timeout 30
```

Use `--turns-json` for inline JSON or `--turns-file` for a JSON array. The CLI exits `0` only when the sequence status is `answered`, exits `1` for `cancelled`, `timeout`, or `stopped`, and exits `2` for transport or validation errors. The sequence endpoint records one sanitized aggregate `official_turn.sequence` operation with ids, counts, statuses, and timing only. The existing `/api/live-agents/<agent_id>/official-turn` reply endpoint still records its normal sanitized `official_turn.reply` entries when residents answer. Neither operation type records turn prompts, reply content, endpoints, config paths, auth refs, command arguments, or logs.

To run a full official round without hand-writing every turn object, call:

```text
POST /api/meetings/<meeting_id>/live-agent-turns/round
```

with:

```json
{
  "round_id": "round_1",
  "role_ids": ["architect", "critic"],
  "content": "Use the round instructions and answer in your assigned role.",
  "timeout_seconds": 30,
  "stop_on_timeout": false
}
```

The round path reads the meeting record, uses `agent_bindings` as the role-to-agent source of truth, checks current live-agent presence for the same meeting, builds normal sequence turns with `turn_id` values like `round_1:0:architect`, and then delegates to the same request → wait sequence primitive. If `role_ids` is omitted, it uses the matching meeting template round: `selected_roles` uses `speaker_role_ids`, otherwise all meeting roles speak in meeting order. If `content` is omitted, the matching template round instruction is used.

The CLI wrapper is:

```bash
python3 -m agentsassemble.cli live-agent call-round \
  --server http://127.0.0.1:8765 \
  --meeting-id meeting-1 \
  --round-id round_1 \
  --timeout 30
```

Repeat `--role` to override speaker order, for example `--role critic --role architect`. The endpoint records one sanitized `official_turn.round` operation with round id, role ids, request/reply ids, statuses, counts, and timing only; it does not record round instructions, reply content, endpoints, config paths, auth refs, command arguments, or logs.

In the GUI, the Lobby `상주 실행` panel exposes the same moderator-called path as `라운드호출`. It uses the panel's `meeting id`, `official round id`, timeout, and `timeout stop` fields, posts to `/api/meetings/<meeting_id>/live-agent-turns/round`, reports only answered/timed-out/skipped counts in the status line, and asks the meeting view to refresh after a successful call.

For Play Mode, a small preset surface can enqueue repeatable debate prompts
without starting providers or granting new admission:

```text
POST /api/meetings/<meeting_id>/live-agent-turns/preset
```

with:

```json
{
  "preset": "meme_debate_fast",
  "role_ids": ["architect", "critic"],
  "timeout_seconds": 30,
  "stop_on_timeout": false
}
```

The CLI wrapper is:

```bash
python3 -m agentsassemble.cli live-agent call-preset \
  --server http://127.0.0.1:8765 \
  --meeting-id meeting-1 \
  --preset meme_debate_fast \
  --timeout 30
```

Available checked-in preset ids are `meme_debate_fast`,
`meme_debate_argument`, and `concession_round`. Presets expand into ordinary
moderator-called official turn requests for the meeting's already bound agents,
then delegate to the same sequence path. They do not start local CLIs, discover
providers, approve real providers, create a meeting, or promote informal lobby
chat into Work Mode. The operation ledger records a sanitized
`official_turn.preset` aggregate with preset id, ids, statuses, counts, and
timing only; it does not record preset prompt text, replies, endpoints, config
paths, auth refs, command arguments, or logs.

To advance a resident meeting without pressing each round manually, call the bounded remaining-round path:

```text
POST /api/meetings/<meeting_id>/live-agent-turns/rounds
```

with:

```json
{
  "timeout_seconds": 30,
  "stop_on_timeout": true,
  "max_rounds": 8
}
```

This path reads the meeting template, skips round ids already recorded in `debate_rounds` with `status: "answered"` using either the `id` or legacy `round` field, runs the remaining template rounds in order through the same single-round primitive, and records answered rounds back into `live_state.json` so future calls and the GUI can advance. If `meeting.json` also exists, live round progress is merged into the read payload without replacing the meeting's roles, template, or provider data. Draft or placeholder round records remain runnable. It is bounded by `max_rounds` and the existing per-turn timeout. The CLI wrapper is `python3 -m agentsassemble.cli live-agent call-remaining-rounds --meeting-id meeting-1 --timeout 30 --max-rounds 8`; add `--finalize-after-rounds` when the same command should write resident final artifacts after the final remaining template round has answered. A capped batch that still leaves template rounds unrun reports `finalization.status: "skipped"` and `finalization.reason: "rounds_still_remaining"` instead of prematurely completing the meeting. The operation ledger records a sanitized `official_turn.rounds` aggregate with round ids, statuses, counts, completed-round counts, timing, and requested finalization status/reason/counts only.

The GUI Lobby `상주 실행` panel exposes the same bounded path as `남은라운드`. It uses the panel's `meeting id`, timeout, `max remaining official rounds`, and `timeout stop` fields, then refreshes the selected meeting after the batch returns.

Finalize a resident meeting only after official turn requests are either
answered or explicitly closed:

```bash
python3 -m agentsassemble.cli live-agent finalize-meeting \
  --server http://127.0.0.1:8765 \
  --meeting-id meeting-1
```

The HTTP control-plane path is:

```text
POST /api/meetings/<meeting_id>/finalize
```

By default, finalization refuses unanswered `live_agent_turn_request` events and
returns the pending request ids. It does not fabricate placeholder agent
messages, does not treat lobby replies as official answers, and does not let a
non-official review checkpoint close a normal official turn. When the operator
intentionally wants to finish with pending turns closed, use:

```bash
python3 -m agentsassemble.cli live-agent finalize-meeting \
  --server http://127.0.0.1:8765 \
  --meeting-id meeting-1 \
  --close-pending
```

`--close-pending` appends one non-official `live_agent_turn_cancelled` system
event per still-pending official request, then finalizes from the real official
messages that exist. Cancellation events carry the target agent, role, turn id,
and source request id, but not the private request prompt. They are terminal
control events for waiters and resident runners, not transcript evidence, shared
memory, decision evidence, or return-packet content. The finalization response
and sanitized `meeting.finalize` operation include only counts and event/request
ids such as `cancelled_pending_count`, `cancelled_event_ids`, and
`cancelled_turn_request_ids`.

Use a review checkpoint when the operator wants ready resident agents to review a slice of work without turning that review into official meeting transcript evidence:

```bash
python3 -m agentsassemble.cli live-agent review-checkpoint \
  --server http://127.0.0.1:8765 \
  --meeting-id meeting-1 \
  --group-id resident-main \
  --timeout 30 \
  "Review the current implementation against the goal."
```

The HTTP path is:

```text
POST /api/meetings/<meeting_id>/review-checkpoints
```

with `group_id`, optional repeated `agent_ids`, optional `checkpoint_id`, `content`, and `timeout_seconds`. The server first runs the same targeted resident session readiness check as `session-readiness`; if the group is not `ready`, it returns `status: "degraded"` and `reason: "session_not_ready"` without appending turn requests or calling providers. When ready, it creates one targeted `live_agent_turn_request` per selected or bound agent, waits for each resident reply, and returns `status: "answered"`, `status: "timeout"`, or `status: "stopped"` with answered/timed-out/skipped counts.

The GUI Lobby `상주 실행` panel exposes this same path as `리뷰요청`. It uses the panel's `meeting id`, `group id`, review message, optional checkpoint id, and timeout fields, refreshes recent operation history, and requests a meeting refresh after the checkpoint returns.

Review checkpoint request and reply live events use `channel: "review"`, `official_record: false`, and the same `review_checkpoint_id`. Resident runners still post replies through `/api/live-agents/<agent_id>/official-turn`, but the request metadata makes those replies review records rather than official transcript turns. Successful review replies are recorded as sanitized `review.reply` operations instead of `official_turn.reply`. The aggregate operation ledger records one sanitized `review.checkpoint` entry with meeting id, group id, checkpoint id, agent ids, request/reply ids, counts, statuses, and timeout only; it does not include prompt or reply content, endpoints, config paths, auth refs, command arguments, provider output, or logs.

Answered, timed-out, or partially answered resident review checkpoints write deterministic review artifacts under `review_checkpoints/<checkpoint_id>.md` and `review_checkpoints/<checkpoint_id>.json`, with unsafe checkpoint id characters normalized before the filename is chosen. Reusing the same checkpoint id rewrites the same artifact paths, while distinct checkpoint ids that normalize to the same filename receive a numeric suffix such as `-2` so earlier operator evidence is not overwritten. These review checkpoint artifacts are Archive/operator records: they intentionally include the review prompt and resident replies so the operator can inspect the checkpoint later, but they stay excluded from official transcripts, shared memory, decisions, return packets, and operation details. A non-official `artifact_kind: "review_checkpoint"` live event points at the artifact paths without copying prompt or reply text into the event. Because review events are not official records, `build_meeting_payload()` does not project them into the Archive transcript.

The resident runner answers by posting to:

```text
POST /api/live-agents/<agent_id>/official-turn
```

The server validates that the source event exists in the same meeting, is a `live_agent_turn_request`, and targets the path agent id. The reply is appended as a `kind: "message"` live event with `channel: "official"` and `official_record: true`; it does not write to `lobby.jsonl`. The server uses the request/path metadata for `actor_id`, `role_id`, `display_name`, `turn_id`, and `turn_index`, so an agent reply cannot choose a different official identity by changing its payload. A successful official reply also clears stale `last_error`, updates `last_reply_at`, and advances `last_observed_live_event_id` to the request event id. Resident runners also skip visible official turn requests that already have a same-agent reply in the live event stream before calling the provider, while the server remains idempotent for repeated reply posts to the same request.

Meeting ids for this path must be single meeting directory names, not paths. Encoded slashes, `..`, nested paths, and backslash-style paths are rejected before resolving under `.agentsassemble/meetings`.

Targeted turn requests are visible only to their target agent through `/api/live-agents/<agent_id>/room` and through the official-turn prompt context. Official reply events remain visible to all meeting participants because they are transcript records.

When a running or partial live meeting has official live events but no physical `transcript.md` yet, `build_meeting_payload()` projects the Archive transcript artifact from the full `live_events.jsonl` log at read time. The projection includes only official transcript events with `official_record: true`, `channel: "official"`, `kind: "message"` or `kind: "synthesis"`, and non-empty content. It preserves safe audit metadata such as event id, created-at, actor id, role id, turn id, turn index, and source event id, while excluding turn request content, status/progress events, lobby, side chat, operation details, prompts, and private targeted control text. Existing `transcript.md` files always win, so completed meeting transcripts written by the normal artifact writer are not overwritten by live projection.

Runner cursors are separated: `last_observed_event_id` tracks lobby events, while `last_observed_live_event_id` tracks meeting live events. This keeps an official turn reply from poisoning later lobby auto-reply state if the operator changes the agent back to `always`, `mentioned`, or `human_only`.

Operation history for `official_turn.call` records safe ids, result status, and timing only. It does not include request text, reply text, prompts, endpoints, config paths, auth refs, command arguments, or log tails.

Successful `official_turn.reply`, `official_turn.rounds`, and session finalization rows may include sanitized shared-memory evidence such as `shared_memory_official_event_count`, `shared_memory_last_event_id`, `shared_memory_open_question_count`, and `shared_memory_action_item_count`. They do not include official reply text, prompt text, extracted action text, or extracted question text.

## Config Preflight

If you do not want to hand-author a resident config, ask AgentsAssemble to discover installed local CLIs first:

```bash
python3 -m agentsassemble.cli live-agent discover \
  --server http://127.0.0.1:8765 \
  --meeting-id resident-1 \
  --engagement-mode mentioned \
  --output .agentsassemble/live-agents.discovered.json \
  --json
```

Discovery writes `.agentsassemble/live-agents.discovered.local.json` by default. It only checks whether known executables are on `PATH`; it does not run Claude, Codex, Kiro, Antigravity, Gemini, Cursor, Grok, Hermes CLI, openclaw, model prompts, login checks, network calls, or billing-affecting operations. Detected `claude`, `openclaw`, and legacy `gemini` are `terminal_session` candidates when PTY terminal sessions are available; their `join_semantics` is `terminal_pty_prompt_bridge`, their `context_durability` is `process_lifetime`, and their `evidence_basis` is `path_and_pty_preflight`. Hand-authored `self_service` residents remain supported through `join_semantics: "self_service_room_loop"` when a wrapper truly owns the room loop; discovery does not invent that wrapper for a bare provider executable. Detected `codex` becomes a `codex_live_session` resident that omits `command` so the existing Codex default and safety preflight remain centralized; its discovery contract is `join_semantics: "codex_exec_resume"`, `context_durability: "provider_managed_resume"`, and `evidence_basis: "path_and_codex_safety_preflight"`. Detected `kiro` becomes a `kiro_live_session` resident that omits `command` so the resident runner applies the Kiro default command shape; its discovery contract is `join_semantics: "kiro_chat_resume"`, `context_durability: "provider_managed_resume"`, and `evidence_basis: "path_and_kiro_resume_preflight"`. Detected `cursor-agent` becomes a `cursor_live_session` resident that omits `command` so the resident runner applies `cursor-agent create-chat` plus `--resume` with a runner-owned workspace; the older generic `cursor` / `terminal_session` row is still reported as `entry_status: "superseded"` for evidence/back-compat visibility, but it is not written into generated resident configs or approval targets. Detected `grok` becomes a `grok_live_session` resident that omits `command` so the resident runner applies the Grok default JSON stdout resume shape; its discovery contract is `join_semantics: "grok_session_resume"`, `context_durability: "provider_managed_resume"`, and `evidence_basis: "path_and_grok_resume_preflight"`. Detected `agy` or legacy `antigravity`, plus detected `hermes`, are reported as `unsupported_evidence` inventory rows only; their `join_semantics` is `unsupported_evidence`, their `context_durability` is `not_proven`, and their `evidence_basis` is `path_and_negative_continuity_evidence`, so they are not written into the generated resident config or session bundle. Detected `gemini` is reported as legacy and skipped unless `--include-legacy-gemini` is set. If PTY terminal sessions are unavailable on the host, terminal-session candidates such as Claude, openclaw, and legacy Gemini are not written into the generated resident config; their discovery rows use `entry_status: "unsupported"`, `operator_action: "unsupported_terminal"`, and `reason: "terminal_unsupported"` with a `safety_note` explaining the PTY limit. Each discovery row also includes `agent_id`, `entry_status`, `entry_mode`, `operator_action`, `requires_approval`, and `safety_note` so the CLI, GUI, and scripts can explain whether that candidate is ready for `auto_join`, needs `include_legacy_gemini`, needs `install_cli`, is blocked because PTY terminal sessions are unavailable, is superseded by a provider-specific resident, or is evidence-only. Discovery defaults generated agents to `engagement_mode: "mentioned"` so a discovered real-provider group does not answer every lobby message unless the operator explicitly chooses `--engagement-mode always`. A successful JSON response includes the generated resident config plus `next_commands.preflight` and `next_commands.run_group`.

Add `--session-bundle` when discovery should also prepare the official resident meeting entry surface:

```bash
python3 -m agentsassemble.cli live-agent discover \
  --server http://127.0.0.1:8765 \
  --meeting-id resident-1 \
  --output .agentsassemble/live-agents.discovered.json \
  --session-bundle \
  --json
```

This writes companion `council.discovered...json` and `agents.discovered...json` files beside the resident config, and the JSON response adds `session_bundle` paths, `session_bundle.group_id`, plus `next_commands.ensure_session`. The generated council and agent bindings use the discovered agent ids exactly, so `ensure-session` can start, resume, restart, or recover the matching meeting/process pair without the operator hand-authoring role bindings. Like base discovery, this is config generation only: it does not start agents, run preflight, contact the room as an agent, execute provider CLIs, or call model services.

The GUI `상주 실행` panel exposes this same local discovery path. `CLI발견` sends `session_bundle: true` when the `세션번들` checkbox is enabled, then fills the live-agent, council, agent, and group fields from the discovery response. Discovery rows for approval-required candidates with a safe `agent_id` expose per-candidate exact approval checkboxes; selecting one and then pressing `자동입장` sends `approved_agents` before preflight, so the backend writes a narrowed resident config and session bundle. Evidence-only rows such as Antigravity and Hermes do not expose a startable approval target because current continuity controls do not justify a resident runner. The underlying `POST /api/live-agent-discovery` endpoint also accepts `approved_agents` and `approved_commands` arrays for frontend or script clients that want the same exact allowlist filtering before files are written. `discovery.run` operation details record safe exact approval evidence such as `approved_count`, `approved_agent_ids`, `excluded_agent_count`, and `unmatched_approval_count`; they do not record CLI executable paths or command-name lists. `자동입장` always requests the session bundle before preflight and durable session-run ensure, so discovered Claude/Codex/Kiro/Cursor/Grok residents can enter a matching visible resident meeting without hand-editing those config paths while still leaving `session-runs.json` recovery intent. When any included discovery row has `requires_approval: true`, `자동입장` stops before preflight or ensure unless the operator selected an exact candidate approval or checked the broad `실사용 CLI 승인` checkbox; this keeps PATH discovery and config writing available without starting real provider CLIs by accident. Approved real-provider auto-join also sends `probe_bound_agents: true`, so readiness is gated by the same bounded reply-probe evidence as manual session start/ensure rather than by process launch alone.

The CLI has the same explicit one-command entrypoint when automation should discover and then immediately ensure the generated resident session:

```bash
python3 -m agentsassemble.cli live-agent auto-join \
  --server http://127.0.0.1:8765 \
  --meeting-id resident-1 \
  --output .agentsassemble/live-agents.discovered.json \
  --connect-timeout 7 \
  --wait-timeout 30 \
  --approve-real-providers
```

`auto-join` always writes the session bundle, derives the normalized `group_id` from the discovered resident config filename, and then posts to `/api/live-agent-session-runs/ensure`, which wraps the same server-side ensure policy used by the GUI `상주보장` path. Unlike `discover`, this is an explicit start/resume/restart/recover operation: after the local PATH-only discovery step succeeds, the ensure step may start the supervised resident group and therefore may execute the configured provider CLIs through the normal preflight-gated session path. If any included discovery row has `requires_approval: true`, the CLI exits `1` with status `approval_required` before contacting the room unless `--approve-real-providers` is present. That flag is the scriptable equivalent of the GUI approval checkbox and should only be used after the operator accepts any cost, network, account, or external side effect. Once that approval is present for real-provider discoveries, `auto-join` forces `probe_bound_agents: true` on the durable ensure request; a failed or timed-out reply probe leaves the returned session/run degraded and makes the CLI exit `1`, even if the resident processes launched. When `--meeting-id` is omitted, the durable ensure request still includes the discovered `group_id` so the server can adopt an existing process-owned meeting id before deciding whether to no-op, resume, restart, recover, or start. The resulting `session_run` stays visible in `session-runs.json` and the GUI `상주 세션런` list. It returns `1` without writing configs or contacting the room when no supported CLI is found.

For narrower scriptable approval, use repeated `--approve-agent <agent_id>` or `--approve-command <cli>` instead of broad approval. Exact approval filters the discovery before writing files, and exact allowlists still narrow the run if `--approve-real-providers` is also present: approved candidates get `approval_status: "approved"`, unapproved real-provider candidates get `approval_status: "not_approved"` and are excluded from the generated resident config and session bundle, so only the explicitly approved agents can be started by that `auto-join` call. Command approvals are accepted only when that command name maps to exactly one approval-required included discovery row. For Cursor, `--approve-command cursor-agent` now maps to the provider-specific `cursor-agent-live-session` row because the older generic terminal row is `superseded` and not an approval target. The durable ensure request still carries one-shot real-provider approval and `probe_bound_agents: true` for the narrowed config, but the approval list is not persisted as durable permission.

Add `--run-remaining-rounds --finalize-after-rounds` to `auto-join` for the full automatic completion path: discovery writes the resident bundle, durable session-run ensure starts/resumes/restarts/recovers the group, successful readiness can call the remaining official template rounds, and finalization writes artifacts only after the same no-rounds-remaining guard used by `ensure-session`. Skipped or failed finalization remains visible in the returned `session.finalization` payload and makes the CLI exit `1`.

```text
POST /api/live-agent-discovery
```

The API writes `live-agents.discovered.local.json` under the GUI output root by default and returns `written: true`, or returns only the report with `written: false` when `write_config` is `false` or no supported CLI is found. Passing `session_bundle: true` also writes `council.discovered.local.json` and `agents.discovered.local.json` and returns the same `next_commands.ensure_session` shape as the CLI. Like the CLI, it only checks `PATH` and does not start agents, contact the room as an agent, execute provider commands, or call model services.

Before starting a real provider group, run a local preflight:

```bash
python3 -m agentsassemble.cli live-agent preflight \
  --server http://127.0.0.1:8765 \
  --config configs/live-agents.example.json
```

The GUI "상주 실행" panel exposes the same check as the `예비점검` button through `POST /api/live-agent-preflight`. Preflight is credential-free and does not send prompts, start resident sessions, call remote bridges, or execute model turns. It checks that the config can be read, agent ids are unique, resident connection kinds are supported, local command executables are present for `local_cli`, `live_session`, `terminal_session`, and `self_service`, that PTY support is available for `terminal_session`, `codex_live_session` residents use the `live_session` connection kind with a resolvable command executable named `codex` and no extra pre-`exec` arguments, that the resolved Codex command parses the required `codex exec --sandbox read-only --ignore-rules resume --skip-git-repo-check --help` safety-flag probe, that `kiro_live_session` residents use the `live_session` connection kind with a resolvable executable named `kiro`, `kiro-cli`, or `kiro-cli-chat`, that `cursor_live_session` residents use the `live_session` connection kind with a resolvable executable named `cursor-agent` and no extra command arguments, that the older `cursor` / `terminal_session` shape for `cursor-agent` fails closed with a message pointing to `cursor-agent-live-session`, that `grok_live_session` residents use the `live_session` connection kind with a resolvable executable named `grok` and no extra command arguments, and remote bridge agents have an endpoint plus an available `auth_ref`. Each agent row includes the derived `sandbox_enforcement` value from the shared `SandboxLauncher` mapping. The CLI exits `0` for `status: ok`, exits `1` for failed checks, and `--json` prints the same machine-readable report shape returned by the GUI endpoint except that browser-visible GUI/API config-load failures redact the local `config_path` and raw loader detail. It cannot prove Claude, Antigravity, legacy Gemini CLI, account login, billing, subscription, model availability, network access, bridge command execution, Codex login/model execution, Kiro login/model execution, Cursor login/model execution, Grok login/model execution, or provider-specific terminal readiness.

Resident runners support only `local_cli`, `live_session`, `terminal_session`, `remote_bridge`, and `self_service`. Registration-only kinds such as `manual` and `codex_resume` can appear in the roster, but `live-agent run`, `live-agent run-group`, supervised start, and preflight reject them as resident process configs instead of silently treating them like a local CLI command.

Direct `live-agent run` and `live-agent run-group` also refuse missing `local_cli`, `live_session`, `terminal_session`, or `self_service` command executables before registering any resident agent. For `provider_kind: "codex_live_session"` plus `connection_kind: "live_session"`, omitting `command` defaults to `["codex"]`; preflight and direct resident starts both verify that the resolved Codex CLI accepts the resident safety flags before any supervised process, resident registration, or worker thread is launched. For `provider_kind: "kiro_live_session"` plus `connection_kind: "live_session"`, omitting `command` defaults to `["kiro"]`; the resident runner starts with `kiro chat`, captures the new Kiro session id, and resumes later turns with `kiro chat --resume-id <session-id>`. For `provider_kind: "grok_live_session"` plus `connection_kind: "live_session"`, omitting `command` defaults to `["grok"]`; the resident runner writes each prompt to a temporary prompt file, parses only JSON stdout `text`, captures a safe `sessionId`, and resumes later turns with `grok --resume <sessionId>`. Direct `run-group` rejects duplicate agent ids before worker threads start, so one bad local config cannot partially register sibling agents under an ambiguous id. Supervised process start, restart, and recovery run this same preflight gate automatically inside the local GUI supervisor before opening a log file, launching `run-group`, or clearing meeting roster evidence. A failed gate returns a GUI/API/CLI error immediately and leaves no new process record behind. The GUI status line shows the refusal reason returned by the API. The check runs in the launching process environment, so PATH and `env:` auth references are evaluated from the process that would launch the resident group.

## Provider Runtime Health

Before running a meeting with API, local model, bridge, Codex, or CLI-backed providers, check the host-approved provider config:

```bash
python3 -m agentsassemble.cli providers health \
  --config configs/http-providers.example.json
```

The GUI "본회의 승인" panel exposes the same static check as `Provider 점검` through `POST /api/provider-health` when the current meeting was created from an agent runtime config file. Provider health uses `probe_mode: none`: it parses provider configs, permission profiles, and approved bindings; checks registry availability, auth_ref presence, endpoint requirements, local command executable availability, duplicate ids, and meeting-only permission compatibility; and redacts auth values, command arguments, local `config_path`, and raw loader detail in both CLI and GUI/API reports. It does not execute provider commands, does not start a meeting, does not call paid model APIs, and does not contact remote bridges. A passing static report means the config is locally coherent, not that account login, billing, model availability, network reachability, or real provider behavior has been proven.

For LM Studio, Ollama, or another loopback OpenAI-compatible server, operators can opt into a local reachability probe:

```bash
python3 -m agentsassemble.cli providers health \
  --config configs/http-providers.example.json \
  --probe local \
  --probe-timeout 2
```

`--probe local` only sends `GET /models` to `local_openai_compatible` providers whose endpoint is a loopback `http://localhost`, `http://127.0.0.0/8`, or `http://[::1]` URL. It skips API providers, local CLI providers, Codex providers, and remote bridges without executing them. It never calls `/chat/completions`, never sends prompts, never reads auth values, rejects endpoint query strings or non-loopback hosts before making a request, and does not follow redirects or environment proxy settings. A passing local probe proves the local OpenAI-compatible models endpoint is reachable and returns at least one model; it still does not prove generation quality, prompt compliance, paid API access, or remote bridge readiness.

For a friend-owned remote HTTP bridge, operators can opt into a bridge health probe:

```bash
python3 -m agentsassemble.cli providers health \
  --config configs/remote-bridge.example.json \
  --probe bridge \
  --probe-timeout 2
```

`--probe bridge` only sends an authenticated `GET /agentsassemble/health` to `remote_http_bridge` providers. It skips non-bridge providers, does not call `/agentsassemble/run`, sends no prompt or meeting payload, does not execute Claude or any provider command, and does not follow redirects or environment proxy settings. This probe reads the configured `auth_ref` value only in the explicit bridge probe mode so it can set `Authorization: Bearer ...`; the report still redacts the token and endpoint details. A passing bridge probe proves the bridge HTTP server is reachable and accepts the configured token. It does not prove the friend's Claude login, billing, model availability, command execution, prompt compliance, or read-only behavior.

For real API-backed Anthropic, Gemini, or Grok providers, operators can opt into a model-list credential probe:

```bash
python3 -m agentsassemble.cli providers health \
  --config configs/http-providers.example.json \
  --probe api \
  --probe-timeout 2
```

`--probe api` only applies to `anthropic`, `gemini`, and `grok` providers. It reads the configured `auth_ref` value only in this explicit API probe mode, sends an HTTPS `GET` to the official provider model-list endpoint, and returns only safe evidence such as status, a generic message, model count, and whether a configured `default_model` appeared in that list. It skips local CLI, Codex, local OpenAI-compatible, remote bridge, mock, memory, and registration-only providers without contacting them. It does not call Messages, generateContent, chat/completions, or any prompt-bearing endpoint; it sends no prompt, meeting payload, lobby text, file content, command arguments, bridge request, or resident session start. It follows no redirects or environment proxy settings and rejects endpoint userinfo, query strings, fragments, custom hosts, and non-official models endpoints before network access. A passing API probe proves only that the configured API credential can reach the provider's model-list endpoint and that at least one model was returned. It does not prove generation quality, prompt compliance, live resident readiness, CLI login state, account billing sufficiency for generation, web access, tool access, or future prompt calls.

## Credential-Free Operator Smoke

After the GUI room is running, use the one-command smoke before touching real providers:

```bash
python3 -m agentsassemble.cli live-agent smoke \
  --server http://127.0.0.1:8765
```

This command posts a human lobby event, starts a temporary supervised process group through `/api/live-agent-processes/start`, and waits for three fake diagnostic participants to answer:

- `smoke local_cli ok`
- `smoke live_session ok`
- `smoke remote_bridge ok`

The smoke is credential-free: it uses local Python fake agents plus a loopback fake `remote_bridge`, not Claude, Gemini, Cursor, account login, network model calls, or paid provider APIs. It verifies the GUI control plane, lobby event ingestion, supervised `run-group`, one-shot `local_cli`, long-lived JSONL `live_session`, remote bridge resident dispatch, and process cleanup path from the same CLI surface an operator uses.

For a clean doctor run, start the GUI with a temporary `--output-root` and point smoke at that local server. The smoke server must be able to read a temporary config path from the same machine, so treat this as a local GUI diagnostic with a loopback fake bridge rather than proof that a friend's real bridge is reachable.

The GUI "상주 실행" panel exposes the same local diagnostic as the `진단` button. It calls `POST /api/live-agent-smoke`, starts the same temporary fake `local_cli`, `live_session`, and fake `remote_bridge` group, verifies the smoke replies by `source_event_id` and live-agent endpoint evidence, then refreshes the lobby, presence roster, and process records. Use it when you want operator-visible evidence without leaving the room UI.

The GUI also exposes the credential-free official-turn path as `공식진단`. That button runs the same `POST /api/live-agent-official-round-smoke` path as the CLI official round smoke, reports the official reply counts in the status line, and treats any `status` other than `ok` as a failed 공식 라운드 smoke. The adjacent `세션진단` button runs `POST /api/live-agent-session-smoke` without reusing the currently selected meeting or group fields, so each click creates a fresh diagnostic resident session and avoids colliding with a real meeting. Its status line reports the generated diagnostic meeting id, official-round result, pre-restart, post-restart, post-recover, and optional same-session soak lobby reply counts, plus start/check/resume/restart/recover/stop statuses. The two numeric session smoke soak controls in the same `상주 실행` panel are bounded like the CLI options: `0-5` cycles and `0-60` seconds interval. When cycles are left at `0`, the GUI keeps the fast default. When cycles are positive, both `세션진단` and `점검` with `세션 포함` send the same soak controls to the backend.

For the moderator-called official-turn path, run the official round smoke:

```bash
python3 -m agentsassemble.cli live-agent official-round-smoke \
  --server http://127.0.0.1:8765
```

That command calls `POST /api/live-agent-official-round-smoke`. The server creates a diagnostic meeting, binds three fake resident agents to its roles, starts the group in `moderator_called` mode, calls the same `/api/meetings/<meeting_id>/live-agent-turns/round` endpoint used by real operator rounds, waits for official replies, and stops the group. It is still credential-free and omits prompt text, reply text, config paths, endpoint URLs, auth refs, command arguments, tokens, and log tails from the smoke response and operation history.

For the strongest credential-free resident session proof, run the session smoke:

```bash
python3 -m agentsassemble.cli live-agent session-smoke \
  --server http://127.0.0.1:8765 \
  --lobby-probes 2 \
  --soak-cycles 2 \
  --soak-interval 5
```

That command calls `POST /api/live-agent-session-smoke`. The server creates temporary fake council, agent binding, and resident group configs for one stateless diagnostic leg plus three credential-free resident transports on every host: one-shot `local_cli`, JSONL `live_session`, loopback `remote_bridge`, and supervised `self_service`. It covers a fifth transport when PTY terminal sessions are available by adding a local Python `terminal_session` fake as the fifth participant. When PTY support is unavailable, the smoke skips the `terminal_session` fake and reports `terminal_session_reason: pty_unavailable` instead of writing an unsupported resident config. It calls the same `start-session` path used by the GUI `세션시작` button; runs one bounded remaining official round through `/api/meetings/<meeting_id>/live-agent-turns/rounds` with finalization requested; requires `finalization_status` to be `finalized` or `already_finalized`; switches the fake bound agents from moderator-called mode to `always`; posts one or more human lobby probes; verifies every included fake agent replied through the live-agent lobby endpoint with each probe `source_event_id`; calls `check-session`; calls `resume-session`; calls `restart-session`; posts the same number of post-restart human lobby probes; verifies every included restarted fake agent replied with each post-restart probe `source_event_id`; forces only the diagnostic process group into a recoverable `error` or `unknown` state; calls `recover-session`; posts the same number of post-recover human lobby probes; verifies every included recovered fake agent replied with each post-recover probe `source_event_id`; then calls `stop-session` for cleanup and checks the post-stop process status so a still-running or restarting diagnostic group fails the smoke instead of being reported as clean. The `local_cli` fake still makes a fresh stateless prompt call for each selected event; the terminal-session fake stays alive behind a PTY and answers repeated submissions with a fixed local Python reply. The self-service fake process observes the room through `wait-next` and posts through `official-reply` or `say` with stdin closed, so this smoke also covers a resident that owns its own room loop rather than receiving prompt injection. It proves the visible meeting binding, diagnostic process launch, official-turn dispatch, final artifact write, repeated auto lobby reply before restart, after restart, and after recover, meeting-aware readiness check, meeting-aware resume, meeting-aware restart, meeting-aware recover, and meeting-aware stop surfaces in one bounded local operation.

If `--group-id` and `--meeting-id` are omitted, the command generates a fresh diagnostic group and meeting id so the no-argument smoke is safe to rerun. `--lobby-probes` is bounded to 1-5 probes per phase; use `1` for the fast default and a higher value when you want a short soak that proves repeated event observation before restart, after restart, and after recover. `--soak-cycles` is bounded to 0-5 and keeps the same recovered diagnostic session alive for bounded soak cycles before cleanup. Each soak cycle waits `--soak-interval` seconds, runs `check-session`, requires `ready`, posts one fresh human lobby probe, and verifies every included fake agent replied with that probe's `source_event_id`. The session smoke is still credential-free and does not call Claude, Gemini, Cursor, model APIs, a real remote bridge, account login, billing, or external networks. The direct response includes safe diagnostic ids plus counts/statuses, including whether terminal-session coverage was included or skipped. It also reports `finalization_status`, `finalization_official_event_count`, `return_packet_event_count`, `artifact_status`, `artifact_paths`, and self-service proof counters such as `self_service_official_reply_count`, `self_service_lobby_reply_count`, and post-restart/post-recover self-service reply counts so operators can see that the supervised child directly used `wait-next`, `official-reply`, and `say`. The `session.smoke` operation records these counts and soak statuses only: no soak source event ids, reply ids, temporary config paths, command arguments, endpoint URLs, auth refs, prompts, reply text, provider output, tokens, or log tails.

`session-smoke --json schema` is intentionally compact and safe. Stable top-level
fields include `status`, `meeting_id`, `group_id`, `agent_ids`,
`terminal_session_supported`, `terminal_session_included`,
`terminal_session_status`, `terminal_session_reason`, `rounds_status`,
`round_count`, `answered_round_count`, `finalization_status`,
`finalization_official_event_count`, `return_packet_event_count`,
`artifact_status`, `artifact_paths`, `expected_reply_count`, per-phase reply
counts, self-service proof counts, `start_status`, `check_status`,
`resume_status`, `restart_status`, `recover_status`, `stop_status`, and
`post_stop_process_status`. Reply arrays are present for direct smoke debugging
but contain only safe event ids, actor ids, and source ids; operation history and
readiness summaries keep only counts/statuses.

Use `--json` when another script needs machine-readable evidence:

```bash
python3 -m agentsassemble.cli live-agent smoke \
  --server http://127.0.0.1:8765 \
  --group-id operator-smoke \
  --timeout 12 \
  --json
```

## Operator Readiness Doctor

Use the doctor command when you want one operator-verifiable readiness answer instead of mentally combining health and smoke:

```bash
python3 -m agentsassemble.cli live-agent doctor \
  --server http://127.0.0.1:8765
```

The doctor calls `POST /api/live-agent-readiness`. The readiness endpoint first records the current `/api/live-agent-health` snapshot, then runs the same credential-free smoke used by `live-agent smoke`. This order is intentional: the smoke leaves offline fake agents and a stopped smoke process record behind, so readiness uses pre-smoke health as the room health proof and treats the smoke result as a separate control-plane proof.

The default doctor text mirrors the important health attention surfaces: agent attention, process attention, connection attention, and sanitized process watchdog reasons. This keeps missing, stale, wrong-meeting, offline, error, or not-reconnected manifest agents visible in the single readiness answer without opening raw health JSON.

When you also need the moderator-called official-turn path in the same operator answer, opt into the credential-free official round smoke:

```bash
python3 -m agentsassemble.cli live-agent doctor \
  --server http://127.0.0.1:8765 \
  --official-round-smoke
```

`--official-round-smoke` adds an official-turn smoke check inside the same readiness payload after the regular local CLI, live session, and remote bridge smoke passes. It uses the same fake diagnostic official round as `live-agent official-round-smoke`, does not call real providers, and makes readiness `failed` if the official round smoke is skipped, timed out, fails cleanup, or returns a non-`ok` status. The readiness response and operation history include bounded counts, ids, statuses, and timing only; they omit official prompts, reply text, config paths, endpoint URLs, auth refs, command arguments, tokens, and log tails.

The official round smoke creates diagnostic official-round smoke meetings so the request/reply event evidence remains inspectable by direct meeting id and operation history. Those diagnostic meetings do not appear in `/api/meetings` or `/api/meetings/latest`, so a smoke run cannot replace the operator's latest real meeting in the normal GUI/archive surface.

When you need the strongest credential-free resident session proof in the same operator answer, opt into the full session smoke:

```bash
python3 -m agentsassemble.cli live-agent doctor \
  --server http://127.0.0.1:8765 \
  --session-smoke
```

`--session-smoke` runs the full `live-agent session-smoke` path inside readiness after the regular local CLI, live session, and remote bridge smoke passes. It creates a fresh diagnostic resident session instead of reusing the doctor's smoke group id, verifies start-session, one official round, check-session, resume-session, restart-session, recover-session, stop-session, and pre-restart/post-restart/post-recover lobby replies. The readiness response and `readiness.check` operation history expose only safe session smoke counts and statuses such as reply counts, post-restart and post-recover reply counts, soak check statuses, recover status, and post-stop process status. They omit source event ids, reply arrays, reply text, temporary config paths, endpoint URLs, auth refs, command arguments, provider output, tokens, and log tails. If the base smoke fails, session smoke is reported as `skipped` and is not run.

For a stronger but slower readiness proof, add `--session-smoke-soak-cycles N` and optionally `--session-smoke-soak-interval SECONDS` with `--session-smoke`. These namespaced flags forward to the diagnostic session smoke and keep the same recovered diagnostic session alive for bounded soak cycles. Readiness and operation history expose only the resulting soak cycle count, reply count, and check statuses.

By default, doctor stays credential-free. Add `--probe-agent <agent_id>` only when you explicitly want opt-in targeted resident probes against already-running agents after smoke passes:

```bash
python3 -m agentsassemble.cli live-agent doctor \
  --server http://127.0.0.1:8765 \
  --probe-agent claude-code-live \
  --probe-agent gemini-cli
```

To probe every agent from a supervised process group, use the group's launch-time manifest:

```bash
python3 -m agentsassemble.cli live-agent doctor \
  --server http://127.0.0.1:8765 \
  --probe-group resident-main
```

`--probe-group` expands the requested group from `/api/live-agent-processes` safe manifest entries, then de-dupes those agent ids with any explicit `--probe-agent` values. The manifest is launch-time evidence from the config the supervisor started with; it does not reread an edited config file. Missing groups, stopped groups, manifestless groups, and requests that expand above the probe cap are refused as `failed` instead of silently probing a subset.

These probes use the same `POST /api/live-agents/<agent_id>/probe` path described below. They can make a real resident runner call its configured local CLI, JSONL session, or remote bridge, so treat them as real-provider checks rather than credential-free smoke. A single readiness request accepts up to 10 targeted probe agents after explicit and group-expanded ids are merged; requests above that limit are refused as `failed` instead of silently probing a subset. The readiness payload includes bounded smoke, probe group, probe status, count, and event id evidence, but omits reply message text, config paths, endpoint URLs, log paths, auth refs, prompts, and log tails from both the readiness response and readiness operation history.

Smoke-created fake agents and process groups are marked `diagnostic`. They remain visible in `.agentsassemble/live_agents.json` and `.agentsassemble/live-agent-runs/processes.json` for operator inspection, but `/api/live-agent-health` ignores diagnostic records so a successful doctor run does not contaminate later health checks or repeated readiness checks. Legacy smoke artifacts from before the `diagnostic` flag are also ignored when their preserved agent identity matches the built-in `Smoke Local CLI` or `Smoke Live Session` diagnostic agents.

Status meanings:

- `ready`: pre-smoke health is `ok`, the fake `local_cli`, `live_session`, plus `remote_bridge` smoke passed, and every requested targeted probe replied.
- `degraded`: smoke passed, every requested official/session smoke and targeted probe replied, but pre-smoke health already had agent, process, or connection attention.
- `failed`: the room was reached, but the base smoke check did not pass, requested official/session smoke was skipped, timed out, or failed, or a requested targeted probe was skipped, timed out, or failed.

For scripts:

```bash
python3 -m agentsassemble.cli live-agent doctor \
  --server http://127.0.0.1:8765 \
  --group-id doctor-smoke \
  --timeout 12 \
  --json
```

Exit code contract:

- `0`: readiness status is `ready`.
- `1`: the server returned a readiness payload, but status was `degraded` or `failed`.
- `2`: the CLI could not fetch or parse the readiness response, or the command arguments were invalid.

The GUI "상주 실행" panel exposes the same readiness path as the `점검` button. Use `진단` for a raw smoke run and `점검` when you want the combined health-plus-smoke answer.

GUI `점검` can include the same official-turn smoke check as `--official-round-smoke`: enable `공식 포함` before pressing `점검`. When unchecked, the GUI keeps the default health-plus-smoke readiness path and does not run the official round smoke. Use `라운드호출` only after a real meeting and resident roster are ready; it calls the real meeting round endpoint rather than the credential-free smoke endpoint.

GUI `점검` can also include the full session smoke check as `--session-smoke`: enable `세션 포함` before pressing `점검`. This is intentionally separate from the standalone `세션진단` button; the checkbox folds the same strong diagnostic into the combined readiness answer, while `세션진단` runs the session smoke as its own operation.

## Targeted Resident Reply Probe

Use `live-agent probe` when a resident agent is already registered as live and you want proof that it can observe a new room event and reply through its own runner path:

```bash
python3 -m agentsassemble.cli live-agent probe \
  --server http://127.0.0.1:8765 \
  --agent-id claude-code-live \
  --timeout 12
```

The probe calls `POST /api/live-agents/<agent_id>/probe`. It does not directly invoke providers, does not start a process, and does not change engagement policy. Instead it appends one visible diagnostic lobby message addressed to the agent id and display name, then waits for a reply whose `actor_id` matches the agent, whose `source_event_id` matches the probe event, and whose event was written through `/api/live-agents/<agent_id>/lobby`. Generic `/api/lobby` posts cannot set the internal `live_agent_endpoint` evidence flag. The reply wait scans a bounded 1000-event recent lobby tail, wider than the default room UI tail, so a busy multi-agent room does not miss a valid probe reply merely because more than 80 later events arrived first. Resident room snapshots use the same 1000-event lobby tail, so the resident can still see the diagnostic probe event in a busy lobby before it answers. Session-level `--probe-bound-agents` uses the same probe evidence, but may temporarily switch default `moderator_called` residents to `human_only` and restore them so a real session can answer the diagnostic human lobby event.

Exit code contract:

- `0`: the agent replied with matching `source_event_id`.
- `1`: the agent existed but was skipped or timed out.
- `2`: the CLI could not reach or parse the server response, or the agent was not found.

Agents in `watch`, `manual`, `moderator_called`, cooldown, provider failure, or remote bridge failure can time out even if their process is alive. Treat timeout as a targeted reply failure, not as proof that the process is dead. The GUI roster exposes the same check as the per-agent `probe` button.

Probe wait time is capped at 240 seconds. The CLI keeps its HTTP request timeout longer than the probe wait window, so an ordinary probe timeout returns the JSON `timeout` result instead of a transport failure.

## Fake CLI Smoke

Use a fake CLI first. It proves the resident loop, room polling, heartbeat, lobby reply, log capture, and stop path without depending on Claude, Gemini, auth, network, or paid model calls.

Post a human lobby message first, then run one bounded resident agent:

```bash
python3 -m agentsassemble.cli live-agent run \
  --server http://127.0.0.1:8765 \
  --agent-id fake-live-one \
  --display-name "Fake CLI" \
  --provider-kind local_cli \
  --connection-kind local_cli \
  --engagement-mode always \
  --poll-interval 1 \
  --heartbeat-interval 5 \
  --cooldown 0 \
  --max-chain-depth 1 \
  --max-ticks 5 \
  --command python3 -c "import sys; sys.stdin.read(); print('Fake CLI saw the room event.')"
```

In the lobby, post a human message such as:

```text
fake-live-one, respond once
```

Expected result: `Fake CLI` appears in the live-agent roster, reads the lobby event, and posts one lobby reply. Because `--max-ticks 5` is bounded, the command exits after the smoke window and sends an offline heartbeat.

## Fake Live Session Smoke

Use `--connection-kind live_session` when the resident agent command speaks the AgentsAssemble JSONL session protocol. Unlike `local_cli`, this keeps one local process alive for the resident runner and sends multiple room events through that same stdin/stdout bridge.

This connection kind is for `live-agent run` and `live-agent run-group`. The one-shot `live-agent delegate` command keeps plain local CLI semantics and does not use the JSONL session envelope. When `delegate` posts its one reply, it links the lobby message to the latest unobserved non-self lobby event from the room snapshot with `source_event_id` and increments `auto_chain_depth`, so the reply still has the same traceable event lineage as resident runner replies. If only self-authored or already-observed events remain, the one-shot reply is still posted but no stale source event is attached. If the delegate command fails, times out, or returns an empty reply after the working heartbeat, the CLI makes a best-effort `error` heartbeat with a compact `last_error` instead of leaving the roster stuck at `working`. That heartbeat does not copy command stdout/stderr, and OS launch failures store the error category without the failing path.

`provider_kind: "codex_live_session"` is a provider-specific resident behind the same `live_session` compatibility gate, but it does not speak the JSONL protocol below. The runner calls Codex CLI directly with `codex exec --sandbox read-only --ignore-rules --output-last-message ... -` for a fresh session and `codex exec --sandbox read-only --ignore-rules resume --output-last-message ... <session_id> -` after a session id is known. The meeting adapter uses the same `codex exec` safety flags for official turns. The resident runner preserves the configured or extracted session id so later lobby events continue the same Codex CLI history. The explicit `--sandbox read-only` flag is the safety input, `--ignore-rules` keeps repository `.rules` files from participating in the launch, and Codex CLI still owns the actual enforcement. AgentsAssemble reports this path as `sandbox_enforcement: "codex_readonly"`. This is not native Codex/Claude channel injection, a PTY attachment, OS-level sandboxing, or a general sandbox for arbitrary local CLI and remote bridge residents.

`provider_kind: "kiro_live_session"` is also provider-specific behind the
`live_session` compatibility gate and does not speak the JSONL protocol below.
The runner calls `kiro chat` for the first room turn, captures the Kiro chat
session id by comparing `kiro chat --list-sessions` before and after that call,
then calls `kiro chat --resume-id <session-id>` for later room turns. This means
Kiro owns the durable private conversation context; AgentsAssemble sends only
the current room prompt and does not replay earlier private turns as a hidden
context bundle. AgentsAssemble reports this path as
`join_semantics: "kiro_chat_resume"` and
`context_durability: "provider_managed_resume"`. Safety is currently advisory:
there is no Kiro read-only launch flag equivalent to the Codex resident safety
input in this adapter. Within one `run-group` process, fresh Kiro session
creation is serialized so multiple Kiro workers do not confuse each other's new
session ids. If you start separate Kiro resident processes by hand, avoid
launching multiple fresh Kiro groups at the exact same time; provide
`session_id` values or start them one at a time when isolation matters.

`provider_kind: "grok_live_session"` is provider-specific behind the
`live_session` compatibility gate and does not speak the JSONL protocol below.
The runner writes each room prompt to a temporary prompt file, calls `grok` with
`--prompt-file`, `--output-format json`, `--disable-web-search`,
`--no-subagents`, and `--verbatim`, then records only the JSON stdout `text` as
the visible reply. After the first call exposes a safe `sessionId`, later calls
add `--resume <sessionId>`. This means Grok owns the durable private session
context; AgentsAssemble does not replay earlier private turns as hidden
context. Grok process stderr can echo prompt material, so stderr/log output must
not be used as reply text or public proof evidence. AgentsAssemble reports this
path as `join_semantics: "grok_session_resume"` and
`context_durability: "provider_managed_resume"`. Safety is currently advisory:
there is no hard Grok sandbox enforced by AgentsAssemble in this adapter.
The Grok control plane also has a no-model fake lifecycle regression: a
temporary fake `grok` executable can start a supervised resident, answer one
official round, restart the group, resume from the captured fake session id,
answer the next official round, finalize, and stop without launching the real
Grok CLI. That offline regression proves the AgentsAssemble runner/session
plumbing for Grok; real Grok official-turn quality still requires a separate
explicitly approved real-provider smoke.

## Provider Continuity Proof

Use `live-agent continuity-proof` when you need direct evidence that a
resume-capable provider resident owns its own multi-turn context. The command
does not join a room or start a supervised group; it calls the provider runner
for two turns. Turn 1 stores a generated continuity code in the provider
session, and turn 2 asks only for the last four characters without replaying the
code. The JSON result reports safe booleans and lengths only: it does not print
the continuity code, provider output, full session id, command stderr, or prompt
bodies.

The command is a real-provider call, so it fails closed unless the current
operator passes `--approve-real-providers` for that one invocation:

```bash
python3 -m agentsassemble.cli live-agent continuity-proof \
  --provider-kind kiro_live_session \
  --connection-kind live_session \
  --agent-id kiro-proof \
  --timeout 180 \
  --approve-real-providers \
  --json \
  --command kiro chat --no-interactive --wrap never --model claude-opus-4.6
```

For Codex:

```bash
python3 -m agentsassemble.cli live-agent continuity-proof \
  --provider-kind codex_live_session \
  --connection-kind live_session \
  --agent-id codex-proof \
  --timeout 240 \
  --approve-real-providers \
  --json \
  --command codex
```

For Grok:

```bash
python3 -m agentsassemble.cli live-agent continuity-proof \
  --provider-kind grok_live_session \
  --connection-kind live_session \
  --agent-id grok-proof \
  --timeout 240 \
  --approve-real-providers \
  --json \
  --command grok
```

To audit a resident group config, use `continuity-proof-group`. It walks the
configured agents, runs the same proof only for supported provider-owned resume
residents, and reports unsupported provider kinds before any provider process is
started. This is the safer way to answer "which of these local CLIs are actually
session-style right now?" without pretending terminal or self-service candidates
have provider-owned resume evidence. An audit where every row is unsupported is
a successful inspection with `status: "unsupported"`; a row that claims Codex,
Kiro, or Grok resume support still must pass the normal resident setup checks
before any provider command is called:

```bash
python3 -m agentsassemble.cli live-agent continuity-proof-group \
  --config configs/live-agents.provider-staging.example.json \
  --approve-real-providers \
  --json
```

Treat a passing continuity proof as one piece of evidence: it proves that the
second provider call can use provider-managed resume context without
AgentsAssemble replaying the first private code. It does not prove room
admission, session start/stop cleanup, official turn quality, tool safety, or
restart behavior. Pair it with `real-session-smoke` when you need room-level
start/probe/stop evidence for a host-approved resident config.

For Cursor:

```bash
python3 -m agentsassemble.cli live-agent continuity-proof \
  --provider-kind cursor_live_session \
  --connection-kind live_session \
  --agent-id cursor-proof \
  --timeout 180 \
  --approve-real-providers \
  --json \
  --command cursor-agent
```

After the continuity proof, a one-resident Cursor room smoke can use an
operator-written config whose only resident has `provider_kind:
cursor_live_session`, `connection_kind: live_session`, and no command arguments
after `cursor-agent`. The approved smoke should stay narrow:

```bash
python3 -m agentsassemble.cli live-agent real-session-smoke \
  --server http://127.0.0.1:8765 \
  --live-agent-config /path/to/live-agents.cursor.json \
  --council-config /path/to/council.cursor.json \
  --agent-config /path/to/agents.cursor.json \
  --group-id cursor-room-smoke \
  --meeting-id cursor-room-smoke \
  --timeout 180 \
  --approve-real-providers \
  --json
```

The first approved local Cursor room smoke returned only safe status/count
evidence: `status: "ok"`, `start_status: "ready"`, connected 1/1, reply probe
1/1, `stop_status: "stopped"`, and `post_stop_process_status: "stopped"`.
Do not store raw Cursor prompts, replies, chat ids, workspace paths, account
data, or logs as public evidence.

In short, the Codex resume path is still `codex exec resume`; the resident runner inserts the read-only sandbox flags at the `codex exec` level before the `resume` subcommand.

When a Codex resident extracts a new session id, later heartbeats store it on the live-agent roster. If that resident process is restarted or recovered from a config that does not include a `session_id`, the fresh runner reads the existing roster entry during registration and seeds the Codex command runner before the next provider call, so it can resume the same Codex CLI session instead of starting over.

Protocol:

- request JSONL: `{"request_id": "...", "prompt": "..."}`
- response JSONL: `{"request_id": "...", "message": "one lobby reply"}`

The subprocess must write only response JSONL to stdout. Diagnostic logs belong on stderr; the runner drains stderr separately and keeps only a bounded tail for error reporting.

On POSIX hosts, the JSONL live-session subprocess starts in its own process group. Closing a session, timing out while waiting for a reply, or timing out while writing a blocked request terminates that process group so ordinary child processes created by a wrapper are cleaned up with the session.

Post a human lobby message first, then run a bounded stateful fake session:

```bash
python3 -m agentsassemble.cli live-agent run \
  --server http://127.0.0.1:8765 \
  --agent-id fake-jsonl-session \
  --display-name "Fake JSONL Session" \
  --provider-kind local_cli \
  --connection-kind live_session \
  --engagement-mode always \
  --poll-interval 1 \
  --heartbeat-interval 5 \
  --cooldown 0 \
  --max-chain-depth 1 \
  --max-ticks 5 \
  --command python3 -u -c "import json, sys; count=0
for line in sys.stdin:
    payload=json.loads(line); count += 1
    print(json.dumps({'request_id': payload['request_id'], 'message': f'Fake JSONL state {count}'}), flush=True)"
```

Expected result: the first eligible lobby event gets `Fake JSONL state 1`. A later eligible lobby event in the same bounded run gets `Fake JSONL state 2`, proving the same process stayed alive. This is a JSONL bridge for local subprocesses, not a native Claude, Gemini, or Cursor PTY protocol.

If the JSONL subprocess exits, times out, stops reading stdin, or returns invalid protocol output, the resident runner records an `error` heartbeat with `last_error`, closes that subprocess, and starts a fresh subprocess for the next eligible event after the normal cooldown gate. Safe short stderr tails can appear in that operator error, but stderr containing auth markers, tokens, endpoints, config paths, option strings, or path-like values is replaced with `stderr tail redacted.` before it can reach presence or GUI surfaces.

## Fake Terminal Session Smoke

Use `--connection-kind terminal_session` when the resident command is an interactive terminal program that should stay alive across multiple room events. The runner starts one PTY-backed process, compacts each generated room prompt into one terminal submission, writes it to the PTY, then reads terminal output until it has been idle for `--terminal-idle-timeout` seconds. This is the first Stoops-style local terminal slice for Claude-like or legacy Gemini-like CLIs; it is still not Claude Code Channels, Antigravity native sessions, tmux ownership, or OS-level sandboxing.

Post a human lobby message first, then run a bounded fake terminal session:

```bash
python3 -m agentsassemble.cli live-agent run \
  --server http://127.0.0.1:8765 \
  --agent-id fake-terminal-session \
  --display-name "Fake Terminal Session" \
  --provider-kind local_cli \
  --connection-kind terminal_session \
  --engagement-mode always \
  --terminal-idle-timeout 0.1 \
  --poll-interval 1 \
  --heartbeat-interval 5 \
  --cooldown 0 \
  --max-chain-depth 1 \
  --max-ticks 5 \
  --command python3 -u -c "import sys; count=0
for line in sys.stdin:
    count += 1
    print(f'Fake terminal state {count}', flush=True)"
```

Expected result: the first eligible lobby event gets `Fake terminal state 1`. A later eligible lobby event in the same bounded run gets `Fake terminal state 2`, proving the same PTY process stayed alive.

## Remote Bridge Resident Smoke

Use `--connection-kind remote_bridge` when a friend-owned bridge is already running and authenticated. Unlike `--probe bridge`, this is not health-only: the resident runner polls the local GUI room, sends one eligible lobby event plus recent lobby context to the bridge's `/agentsassemble/run` endpoint, then posts the returned message back through the same live-agent lobby endpoint used by local runners.

The remote bridge runner still owns the local safety guards:

- self-authored events are skipped by `actor_id`;
- already observed events are skipped by `last_observed_event_id`;
- chain depth is capped by `--max-chain-depth`;
- bridge request failures, command timeouts, and non-zero bridge command return codes become sanitized `error` heartbeats instead of lobby messages, and the runner retries after cooldown.

Post a human lobby message first, then run one bounded remote bridge resident:

```bash
python3 -m agentsassemble.cli live-agent run \
  --server http://127.0.0.1:8765 \
  --agent-id friend-claude-live \
  --display-name "Friend Claude" \
  --provider-kind claude_code \
  --connection-kind remote_bridge \
  --endpoint http://100.64.0.10:8777 \
  --auth-ref env:AGENTSASSEMBLE_BRIDGE_TOKEN \
  --engagement-mode always \
  --poll-interval 2 \
  --heartbeat-interval 30 \
  --cooldown 5 \
  --max-chain-depth 1 \
  --max-ticks 5
```

For a supervised group, use:

```bash
python3 -m agentsassemble.cli live-agent run-group \
  --config configs/live-agents.remote-bridge.example.json \
  --server http://127.0.0.1:8765 \
  --max-ticks 5
```

Do not use a redacted public meeting artifact as the resident auth source. The runner needs a real `env:` or `literal:` `auth_ref`; an env var whose value is `<redacted>` is treated as unavailable. Keep bridge credentials out of endpoint URLs: resident bridge endpoints must be plain HTTP(S) URLs without userinfo, query strings, or fragments. The runner does not register, heartbeat, or log the token value.

## Fake Group Smoke

Use a temporary two-agent config when you want to verify `run-group` without real providers:

```bash
fake_config="$(mktemp)"
cat > "$fake_config" <<'JSON'
{
  "server": "http://127.0.0.1:8765",
  "poll_interval": 1,
  "heartbeat_interval": 5,
  "cooldown": 0,
  "max_chain_depth": 1,
  "agents": [
    {
      "agent_id": "fake-alpha",
      "display_name": "Fake Alpha",
      "provider_kind": "local_cli",
      "connection_kind": "local_cli",
      "engagement_mode": "always",
      "command": ["python3", "-c", "import sys; sys.stdin.read(); print('Fake Alpha saw the room event.')"],
      "timeout_seconds": 10
    },
    {
      "agent_id": "fake-beta",
      "display_name": "Fake Beta",
      "provider_kind": "local_cli",
      "connection_kind": "local_cli",
      "engagement_mode": "always",
      "command": ["python3", "-c", "import sys; sys.stdin.read(); print('Fake Beta saw the room event.')"],
      "timeout_seconds": 10
    }
  ]
}
JSON

python3 -m agentsassemble.cli live-agent run-group \
  --config "$fake_config" \
  --server http://127.0.0.1:8765 \
  --max-ticks 3
```

Post one human lobby message before or during the run. Expected result: both `fake-alpha` and `fake-beta` register, reply through the lobby, and exit cleanly when the bounded ticks finish.

## Start A Group From The GUI

Use the GUI "상주 실행" panel for supervised process records and stop controls. For fake smoke, point the config path at your temporary fake config. For the real example config, complete the Claude and Gemini checklist first.

- config path: the temporary fake config, or `configs/live-agents.example.json` after real-provider approval
- group id: optional, for example `local-cli-group`
- auto restart: optional; auto restart is off by default
- max restarts: bounded retry count written as `max_restarts`
- restart backoff: seconds between crash detection and relaunch, written as `restart_backoff_seconds`
- stale watchdog: optional heartbeat timeout written as `stale_restart_after_seconds`; it requires auto restart with a positive restart budget
- press `시작`

The GUI start button runs the same resident group through the local process supervisor. It preflights the config first, then launches only when the config passes. Group records and log tails remain visible after a launched process stops or crashes. Auto restart only applies to a group launched with that option enabled, and it starts a fresh local process rather than attaching to an old PID.

When the stale watchdog is enabled, the supervisor waits until the launched group has been alive longer than the configured timeout, compares the launch-time agent manifest against current live-agent presence, and stops/restarts the owned group if any manifest agent is missing, stale, offline, error, or attached to the wrong meeting. The watchdog is opt-in and uses the same bounded auto-restart budget/backoff path as crash recovery, so health and readiness reads stay read-only. To avoid killing a quiet but healthy runner, each agent must have a positive `heartbeat_interval`, and the watchdog threshold must be greater than that agent's `heartbeat_interval + poll_interval`.

If the GUI server restarts while a group record still says `running`, the new supervisor marks that historical record `unknown` because it does not attach to old PIDs. Use the GUI `복구` button or CLI `python3 -m agentsassemble.cli live-agent processes recover <group-id>` to start a fresh supervised process from that record's persisted config/server/options. Recovery is distinct from `restart`: it is intended for `unknown` or `error` records, returns `recovered_from_status`, and records sanitized `process.recover` and lifecycle `recovered` evidence. Those records include safe ids, statuses, restart counts, and previous status only; they do not include config paths, command arguments, endpoints, auth refs, prompts, provider output, replies, or log tails in operation details.

The same supervised start path is available from the CLI:

```bash
python3 -m agentsassemble.cli live-agent processes start \
  --server http://127.0.0.1:8765 \
  --config "$fake_config" \
  --group-id local-cli-group
```

For auto restart, pass both `--auto-restart` and a positive `--max-restarts` value:

```bash
python3 -m agentsassemble.cli live-agent processes start \
  --server http://127.0.0.1:8765 \
  --config "$fake_config" \
  --group-id local-cli-group \
  --auto-restart \
  --max-restarts 2 \
  --restart-backoff-seconds 5 \
  --stale-restart-after-seconds 120
```

Here `--server` is the GUI API target and the room server URL passed to the supervised `run-group`.

The GUI autostart path accepts the same watchdog threshold with:

```bash
python3 -m agentsassemble.cli gui \
  --live-agent-config "$fake_config" \
  --live-agent-auto-restart \
  --live-agent-max-restarts 2 \
  --live-agent-stale-restart-after-seconds 120
```

## Stop Or Restart A Group

Prefer the GUI stop button for a running group. The HTTP stop path is also available:

```bash
curl -X POST \
  -H 'Content-Type: application/json' \
  -d '{}' \
  http://127.0.0.1:8765/api/live-agent-processes/local-cli-group/stop
```

After a successful process stop, the supervisor reconciles existing live-agent presence rows for that group's launch-time manifest and marks matching agents `offline` immediately. This prevents a killed or interrupted resident process from looking `online` until heartbeat staleness expires. The stop response includes an output-only offline reconciliation summary with expected/offline/skipped counts, safe `offline_agent_ids`, and attention entries for skipped manifest agents. The reconciliation is conservative: it does not create new presence rows, does not touch agents currently attached to another meeting, and does not mark an agent offline while another running or restarting group with the same meeting id still expects that agent.

Use the GUI restart button on a stopped, crashed, or recovered group to relaunch it from the persisted `config_path` and `server`. Restart, recovery, and auto-restart relaunches refuse a blank persisted `config_path` or `server` before preflight or process launch, so corrupted historical rows fail with a clear missing-launch-evidence error instead of treating the current directory as a resident config or reconnecting to the wrong room. Restart also reruns preflight before launching, so a config or environment that became invalid while the group was down is refused synchronously. The HTTP restart path is:

```bash
curl -X POST \
  -H 'Content-Type: application/json' \
  -d '{}' \
  http://127.0.0.1:8765/api/live-agent-processes/local-cli-group/restart
```

The CLI equivalents are:

```bash
python3 -m agentsassemble.cli live-agent processes list \
  --server http://127.0.0.1:8765

python3 -m agentsassemble.cli live-agent processes list \
  --server http://127.0.0.1:8765 \
  --fail-on-attention

python3 -m agentsassemble.cli live-agent processes wait local-cli-group \
  --server http://127.0.0.1:8765 \
  --timeout 30 \
  --poll-interval 2

python3 -m agentsassemble.cli live-agent processes stop local-cli-group \
  --server http://127.0.0.1:8765

python3 -m agentsassemble.cli live-agent processes restart local-cli-group \
  --server http://127.0.0.1:8765
```

When the room has more than one resident group, use the GUI `실행중지` button or the bulk CLI/API path to stop every currently running group and cancel pending auto-restarts in one operator action. This does not attempt to signal historical `unknown`, `error`, or already `stopped` records that the current supervisor does not own.

```bash
python3 -m agentsassemble.cli live-agent processes stop-running \
  --server http://127.0.0.1:8765

curl -X POST \
  -H 'Content-Type: application/json' \
  -d '{}' \
  http://127.0.0.1:8765/api/live-agent-processes/stop-running
```

The bulk stop response reports `stopped_count`, `failed_count`, `skipped_count`, the corresponding safe group records, and each stopped record's offline reconciliation summary when a manifest was present. The operation ledger records `process.stop_running` with counts, safe group ids, offline counts, safe `offline_agent_ids`, and compact offline attention only; it does not record config paths, server URLs, commands, auth refs, log tails, prompts, or provider output.

Add `--json` to any process CLI command to print the raw HTTP payload.

For scriptable gates, add `--fail-on-attention` to `processes list`. The command still prints the ordinary process summary first, then exits `1` when any listed group is `error`, `unknown`, `restarting`, or has manifest-aware `agent_connection.attention`. A historical `stopped` group alone does not fail this check.

Use `processes wait <group_id>` immediately after `processes start`, `processes restart`, `processes recover`, or an external GUI action when automation needs a bounded readiness gate before posting probes or official turns. The command polls `/api/live-agent-processes` until the named group is `running`, has no process attention, and either has no expected manifest count or has `connected >= expected`. The `processes wait` command exits `0` only when the named group is ready. It exits `1` on timeout and prints the last observed group summary, or `group not found` if the group never appeared. It exits `2` for non-timeout transport, parsing, validation, or HTTP errors. The wait HTTP client bounds each poll by the remaining deadline, recomputes sleep after each poll, and reports both direct timeout and wrapped URL timeout failures as the normal wait timeout shape. Use `--json` when another agent or monitor needs the machine-readable `status`, `group_id`, `timeout_seconds`, `attempts`, `group`, and optional `error` fields.

Use `processes wait-event` when automation needs to observe a process lifecycle event such as `stale_watchdog`, `restart_scheduled`, `restart_failed`, `stopped`, or `recovered` rather than waiting for the group to become ready:

```bash
python3 -m agentsassemble.cli live-agent processes wait-event \
  --server http://127.0.0.1:8765 \
  --group-id local-cli-group \
  --event-type restart_scheduled \
  --status restarting \
  --after-timestamp 2026-05-17T12:00:00+00:00 \
  --timeout 30 \
  --poll-interval 2
```

The lifecycle event wait path polls `/api/live-agent-process-events?limit=N&scan_limit=N&group_id=...`, then filters the returned sanitized events by `event_type`, optional group id, optional status, and optional `--after-timestamp`. Events at or before `--after-timestamp` are ignored so an older crash or restart record does not satisfy a new wait. The command exits `0` when a matching event appears and exits `1` on timeout with the last observed lifecycle event summary. It exits `2` for non-timeout transport, parsing, validation, or HTTP errors. Use `--json` for the machine-readable `status`, filters, `timeout_seconds`, `attempts`, matched `event`, and timeout event tail.

Use `/api/live-agent-session-runs/ensure` when the operator wants the ensure request recorded as durable session intent instead of only as a one-shot control operation. The endpoint accepts the same payload as `/api/live-agent-sessions/ensure`, creates a `session-runs.json` record before invoking ensure, then updates that record with the final readiness status, selected ensure action, connection counts, probe result, remaining-round result, finalization result, and a sanitized error if ensure fails. The GUI server starts a lifecycle-owned session-run monitor that reconciles active durable runs on startup and at a conservative runtime interval by replaying their saved ensure request, so a restarted or long-running GUI process has a visible recovery hook above the lower-level process supervisor. Successful post-ready checks preserve `probe_bound_agents` and `probe_timeout_seconds` as durable reply-proof intent, so a later stale-observation recovery can still prove bound residents answer after restart or recover. Successful post-ready checks consume one-shot durable request fields for remaining rounds and finalization (`run_remaining_rounds`, `finalize_after_rounds`, and their bounded options), so the monitor does not keep posting official turns or finalization attempts after a run is already fully ready. Failed post-ready checks keep the run `degraded`, preserve those request fields for a later reconcile retry, and schedule the next mutating retry with durable `reconcile_failure_count`, `reconcile_backoff_seconds`, and `next_reconcile_at` fields. A future `next_reconcile_at` makes the monitor skip that run without posting a new operation row, so a broken provider or temporary readiness gap does not trigger a mutating ensure attempt on every monitor tick. The next successful `ready`, terminal `failed`, or operator `stopped` result clears the retry fields. The monitor records safe `session_run.reconcile` or `session_run.monitor` operation evidence and shuts down before the process supervisor so it cannot race against process teardown.

The GUI `상주 실행` panel keeps the older `세션보장` one-shot ensure button and adds `상주보장` for the durable session-run path. Its `상주 세션런` list reads `/api/live-agent-session-runs?limit=20&include_readiness=1` during normal runtime refresh and shows only safe operator evidence: run id, meeting id, group id, phase/status, active flag, reconnect count, retry failure count/backoff/next retry time, historical connected/expected counts, and the current read-only readiness overlay. This lets an operator see when a stored `ready` run is currently degraded or waiting for its next durable retry without starting providers, stopping groups, running probes, appending operation records, or overwriting the durable run status. When an active session-run is waiting in backoff or current readiness is no longer ready, its row action posts to `/api/live-agent-session-runs/<run_id>/retry-now`; the control clears the durable backoff for that one run, immediately asks the session-run monitor logic to reconcile only that target, records safe `session_run.retry_now` evidence, then refreshes the same safe list. If the operator has checked the GUI `실사용 CLI 승인` box, that one retry-now request includes current `approve_real_providers: true` so real-provider residents can be relaunched through the same approval gate as CLI retry-now; the approval flag is not persisted into `session-runs.json` or public run output. Active non-terminal session-runs can also be paused through `/api/live-agent-session-runs/<run_id>/pause`; this records safe `session_run.pause` evidence, changes the durable run to `paused`, keeps its previous active status as `paused_status`, and removes it from automatic reconcile without stopping the process group. `/api/live-agent-session-runs/<run_id>/resume` records `session_run.resume`, restores that paused status, clears any saved retry delay while preserving retry failure evidence, marks the run active again, and makes it eligible for the next durable reconcile. The row-level `중지` control posts to `/api/live-agent-session-runs/<run_id>/stop`, stops only that durable run, refreshes the same safe list, and leaves the resident process group and roster evidence untouched. Already-ready retry targets are reported as skipped without mutating the durable record, and inactive or terminal targets are rejected instead of being revived. `세션중지` marks matching active session-runs as stopped, refreshes the list, and reports the stopped run count in the status line. It does not render saved config paths, command arguments, server URLs, prompts, provider output, or log tails.

```bash
curl -X POST \
  -H 'Content-Type: application/json' \
  -d '{"meeting_id":"resident-m1","group_id":"local-cli-group","live_agent_config_path":"configs/live-agents.example.json"}' \
  http://127.0.0.1:8765/api/live-agent-session-runs/ensure

python3 -m agentsassemble.cli live-agent session-runs list \
  --server http://127.0.0.1:8765
```

Add `--include-readiness` when a terminal operator, wrapper script, or another local agent needs the same current read-only readiness overlay that the GUI `상주 세션런` list shows:

```bash
python3 -m agentsassemble.cli live-agent session-runs list \
  --server http://127.0.0.1:8765 \
  --include-readiness
```

Add `--run-id` when an operator or handoff script already knows the exact durable run id and wants that single run filtered before the bounded result limit:

```bash
python3 -m agentsassemble.cli live-agent session-runs list \
  --server http://127.0.0.1:8765 \
  --run-id <session-run-id> \
  --include-readiness
```

Add `--meeting-id` and `--group-id` when an operator or handoff script wants only the durable run tail for one resident meeting/group instead of the global tail:

```bash
python3 -m agentsassemble.cli live-agent session-runs list \
  --server http://127.0.0.1:8765 \
  --meeting-id resident-m1 \
  --group-id local-cli-group \
  --include-readiness
```

For scriptable durable-run gates, add `--fail-on-attention`. The command still prints the normal session-run summary first, then exits `1` when any returned run is `failed`, reports `error`, is active but not `ready`, or is active with a current readiness overlay that is not `ready`. Paused or stopped inactive runs do not fail the gate by themselves. Without that flag, listing session-runs exits `0` whenever the list was fetched successfully.

Use `assemble live-agent session-runs retry-now` when a degraded durable run is visible but its `next_reconcile_at` is still in the future. With `--run-id`, the command targets that exact run and posts the same operator control as the GUI row action to `/api/live-agent-session-runs/<run_id>/retry-now`. Without the exact run id, use both `--meeting-id` and `--group-id`; the CLI posts to `/api/live-agent-session-runs/retry-now`, the server resolves the latest matching meeting/group session-run before any retryability filtering, and exact `run_id` always wins if both target forms are supplied. The command prints the returned public session-run summary:

```bash
python3 -m agentsassemble.cli live-agent session-runs retry-now \
  --server http://127.0.0.1:8765 \
  --run-id <session-run-id>

python3 -m agentsassemble.cli live-agent session-runs retry-now \
  --server http://127.0.0.1:8765 \
  --meeting-id resident-m1 \
  --group-id local-cli-group
```

Durable session-run records are restart intent, not stored provider approval. The durable ensure API and lifecycle-owned session-run monitor can automatically reconcile credential-free fake/local residents, but if the saved session-run config or the matching persisted process-group config resolves to a real provider resident such as `terminal_session`, `codex_live_session`, `antigravity_cli`, `cursor`, or `remote_bridge`, the server records a failed or degraded approval-required result instead of invoking the provider. If a legacy run has no saved config and the process group cannot be inspected, the monitor fails closed with the same safe approval-required evidence. Use `--approve-real-providers` only on the current `retry-now` command when the operator wants that one retry action to relaunch real provider residents; the flag is treated as approval only when it is JSON/CLI true, and it is not persisted into `session-runs.json` or public session-run output:

```bash
python3 -m agentsassemble.cli live-agent session-runs retry-now \
  --server http://127.0.0.1:8765 \
  --run-id <session-run-id> \
  --approve-real-providers
```

Use `assemble live-agent session-runs pause` and `assemble live-agent session-runs resume` when automation should stop or restart reconciling one durable session intent without killing the live-agent process group. With `--run-id`, the commands target the exact run through `/api/live-agent-session-runs/<run_id>/pause` or `/api/live-agent-session-runs/<run_id>/resume`. Without the exact run id, use both `--meeting-id` and `--group-id`; the CLI posts to `/api/live-agent-session-runs/pause` or `/api/live-agent-session-runs/resume`, the server resolves the latest matching meeting/group session-run before pause/resume eligibility checks, and exact `run_id` always wins if both target forms are supplied:

```bash
python3 -m agentsassemble.cli live-agent session-runs pause \
  --server http://127.0.0.1:8765 \
  --run-id <session-run-id>

python3 -m agentsassemble.cli live-agent session-runs pause \
  --server http://127.0.0.1:8765 \
  --meeting-id resident-m1 \
  --group-id local-cli-group

python3 -m agentsassemble.cli live-agent session-runs resume \
  --server http://127.0.0.1:8765 \
  --run-id <session-run-id>

python3 -m agentsassemble.cli live-agent session-runs resume \
  --server http://127.0.0.1:8765 \
  --meeting-id resident-m1 \
  --group-id local-cli-group
```

Use `assemble live-agent session-runs stop` when one durable session intent should be removed from automatic reconciliation without stopping its resident process group. It follows the same target contract as retry/pause/resume: `--run-id` posts to `/api/live-agent-session-runs/<run_id>/stop`, while `--meeting-id` plus `--group-id` posts to `/api/live-agent-session-runs/stop` and resolves the latest matching meeting/group session-run. Exact `run_id` wins if both target forms are supplied. This stops only the selected durable run; it does not sweep older matching runs and does not mark roster rows offline. The API records sanitized `session_run.stop` evidence with the safe run id, meeting id, group id, run status, and phase:

```bash
python3 -m agentsassemble.cli live-agent session-runs stop \
  --server http://127.0.0.1:8765 \
  --run-id <session-run-id>

python3 -m agentsassemble.cli live-agent session-runs stop \
  --server http://127.0.0.1:8765 \
  --meeting-id resident-m1 \
  --group-id local-cli-group
```

Use `assemble live-agent session-runs wait` when another script or agent needs a durable session-run status gate after starting a long-running ensure operation from the GUI, CLI, or auto-join path:

```bash
python3 -m agentsassemble.cli live-agent session-runs wait \
  --server http://127.0.0.1:8765 \
  --run-id <session-run-id> \
  --status ready \
  --timeout 30 \
  --poll-interval 2
```

When a script knows the resident meeting and group but not the exact run id, it can wait on the latest matching meeting/group session-run:

```bash
python3 -m agentsassemble.cli live-agent session-runs wait \
  --server http://127.0.0.1:8765 \
  --meeting-id resident-m1 \
  --group-id local-cli-group \
  --status ready
```

This lets another agent continue without the exact run id after a GUI action or handoff. The command selects the latest matching meeting/group session-run from the bounded public list, so an older `ready` run cannot satisfy the gate while a newer matching run is still `running`. If `--run-id` is supplied together with meeting/group fields, the exact run id is the target.

The list command normally reads stored durable run state. With `--meeting-id` and/or `--group-id`, it asks the server to filter before applying the bounded limit window, so unrelated newer runs do not hide the target tail. Degraded or recovering runs print compact retry evidence such as `reconcile_failures=2`, `reconcile_backoff=120s`, and `next_reconcile=2026-05-21T10:07:00+00:00` when that backoff is active. With `--include-readiness`, it requests `/api/live-agent-session-runs?limit=N&include_readiness=1` and prints compact current readiness evidence such as `readiness=degraded` and `current_connected=1/3` while leaving the raw JSON payload intact when `--json` is used. The wait path polls `/api/live-agent-session-runs?limit=N&run_id=...` for exact run-id waits; the server filters that exact session-run before applying the limit window, so unrelated newer session-runs cannot push a known handoff target out of the returned tail. Meeting/group waits poll `/api/live-agent-session-runs?limit=N&meeting_id=...&group_id=...`; the server filters matching meeting/group session-runs before applying the limit window, so unrelated newer session-runs cannot push the handoff target out of the returned tail. When the requested status is `ready`, the CLI adds `include_readiness=1` and treats the stored session-run status as historical durable intent; current readiness is a separate read-only overlay. A `ready` run satisfies automation only when its current readiness overlay is also `ready`, so stale or stopped process state cannot satisfy a fresh handoff gate. In short, ready waits require the current readiness overlay to be `ready`. The overlay reads the existing session readiness snapshot and does not start providers, stop groups, run probes, append operation records, or overwrite the durable run status. It exits `0` when the target run/status is observed and exits `1` on timeout with the last observed safe run summary. It exits `2` when neither `--run-id` nor both `--meeting-id` and `--group-id` are supplied, or for non-timeout transport, parsing, validation, or HTTP errors. Use `--json` when automation needs the machine-readable `status`, requested run id, meeting/group target, requested status, attempts, matched run, readiness, retry backoff fields, or timeout run tail.

The public session-run API and CLI intentionally omit server URLs, config paths, commands, auth refs, prompts, log tails, provider output, and transient real-provider approval flags. The durable controller keeps enough local request state to reconcile active runs, while the exposed status is limited to safe identifiers, requested toggles, readiness/result summaries, timestamps, `active`, `phase`, and `reconcile_count`.

The process CLI uses exit code `0` for successful supervisor requests, even when a listed group is `stopped`, `error`, or `unknown`, unless `--fail-on-attention`, `processes wait`, or `processes wait-event` is used as an explicit scriptable gate. It uses exit code `2` for argument validation, connection failures, invalid JSON, HTTP errors, missing config files, unknown group ids, and refused restarts.

The supervisor only stops group ids it launched in the current GUI process. It does not control arbitrary OS PIDs. Historical records from a previous GUI process are shown as `unknown`, `stopped`, or `error`, but are not treated as externally stoppable PIDs. Restarting a historical record starts a fresh local process from the saved config and server instead of attaching to the old PID. A manual restart resets the auto-restart counter for the new run while preserving the configured retry policy.

On POSIX hosts, supervised `run-group` processes start in their own process group. If a supervised group does not exit after SIGINT, the supervisor escalates stop signals to that process group so ordinary child processes created by the resident group are cleaned up with the failed parent.

Resident `local_cli` workers use an interruptible subprocess runner. When the supervised group receives SIGINT during stop, active local provider commands are terminated through that runner instead of waiting for the provider command timeout. The same cleanup path is used when a provider command hits its timeout. On POSIX hosts, resident local CLI commands start in their own process group so ordinary child processes created by provider wrappers are stopped with the same command. Direct resident `SIGTERM` also closes the active command runner and returns the normal stopped summary instead of surfacing `KeyboardInterrupt` to the operator. A non-shutdown worker failure is isolated to that worker: sibling workers are not closed just because one worker failed, and the group reports the failed worker on stderr with exit code `2` after the remaining workers finish or the operator stops the group. The failed worker also sends a best-effort `error` heartbeat with a sanitized `last_error`, so the roster can show the failed agent without exposing paths, tokens, endpoints, config filenames, prompts, provider output, or raw exception messages. Sensitive-looking failures use a fixed redacted marker; otherwise the heartbeat carries only a compact exception category. Shutdown-related secondary worker errors are suppressed after the group stop flag is set, so the process record preserves the original failure or operator stop result.

## Inspect Runtime State

Default files under `--output-root .agentsassemble`:

```text
.agentsassemble/live_agents.json
.agentsassemble/lobby.jsonl
.agentsassemble/live-agent-runs/processes.json
.agentsassemble/live-agent-runs/events.jsonl
.agentsassemble/live-agent-runs/operations.jsonl
.agentsassemble/live-agent-runs/session-runs.json
.agentsassemble/live-agent-runs/<group_id>.log
```

What to check:

- `.agentsassemble/live_agents.json`: presence, status, heartbeat metadata, `last_error`, `last_reply_at`, `last_observed_event_id`, and `last_observed_live_event_id`.
- `/api/live-agents`: roster entries add output-only `heartbeat_age_seconds` and `stale_after_seconds` so operators can see why an agent is fresh or stale. These freshness fields are inferred at read time and are not persisted in `live_agents.json`. The GUI live-agent card shows both the lobby `cursor` from `last_observed_event_id` and the `official cursor` from `last_observed_live_event_id`, so external/manual agents can prove which room event and official meeting event they last observed.
- Slow resident replies keep sending `working` heartbeats while the provider command is still in flight, so a long Claude, Gemini, local CLI, live session, or remote bridge turn does not look stale merely because it is still answering.
- `.agentsassemble/lobby.jsonl`: human lobby messages and live-agent replies. Live-agent auto replies include `actor_id`, `source_event_id`, `auto_chain_depth`, and the server-issued `live_agent_endpoint` evidence flag when they were posted through the resident live-agent lobby endpoint. A repeated live-agent lobby post with the same `actor_id` and `source_event_id` returns the existing endpoint reply instead of appending a duplicate, so overlapping resident processes cannot multiply-answer one source event after restart or recovery races.
- `meetings/<meeting_id>/live_events.jsonl`: official room control and meeting
  events. `live_agent_turn_request` and `live_agent_turn_cancelled` are
  non-official control events; only official `message`/`synthesis` events feed
  transcript, shared memory, decision artifacts, tasks, and return packets.
- `.agentsassemble/live-agent-runs/processes.json`: durable group records with `group_id`, `status`, `pid`, `config_path`, `server`, `log_path`, timestamps, `returncode`, `last_error`, and a safe launch-time `agents` manifest. Process list/API/GUI output redacts suspicious `last_error` text before display, and new auto-restart `restart_failed` records store a compact redacted restart-failure label when the relaunch exception contains tokens, endpoints, config paths, command options, env refs, or path-like values. Safe short failures such as a missing agent command remain visible.
- `.agentsassemble/live-agent-runs/session-runs.json`: durable high-level session-run records created by `/api/live-agent-session-runs/ensure`. These records are the operator intent layer above process mechanics: they track `run_id`, `action`, `status`, `active`, `phase`, safe meeting/group ids, timestamps, reconcile count, retry backoff fields, and safe session result summaries. Use `assemble live-agent session-runs list` or `GET /api/live-agent-session-runs` to inspect them.
- agents manifest entries contain only `agent_id`, `display_name`, `provider_kind`, and `connection_kind`. The manifest does not include command arguments, endpoint URLs, auth references, command paths, prompts, or environment-derived values.
- `/api/live-agent-processes`: process rows add output-only `agent_connection` evidence by comparing each group's launch-time manifest with current live-agent presence. This manifest-aware connection evidence reports `expected`, `connected`, and attention entries such as missing, wrong-meeting, not-reconnected, stale, offline, or error agents. A presence row whose `last_seen_at` is older than the process group's `started_at` is `not_reconnected` rather than connected, so stale pre-restart heartbeats do not prove the fresh process is attached. It is not persisted into `processes.json`. Process-control error responses for start, stop, restart, recover, and bulk stop redact suspicious path, endpoint, token, config filename, option, env, and secret-looking details before sending browser-visible JSON, while keeping safe short errors such as a missing agent command visible.
- The default CLI process list and GUI process rows show the latest lifecycle offline summary beside the last event label, such as `last event restart_scheduled, offline 1/2, wrong_meeting agent-b`, so crash-time roster reconciliation evidence is visible without opening JSON. They also show the latest sanitized watchdog reason from recent lifecycle history, such as `last reason stale_watchdog missing manifest agent agent-a`, when the latest row event did not carry that reason itself.
- The GUI `상주 실행` panel reads `/api/live-agent-health` during runtime refresh and shows the backend health snapshot directly: overall status, live/total agents, running/total process groups, process monitor liveness evidence in `process_monitor`, connected/expected manifest agents, ready/total meeting-owned sessions, active/total durable session-runs, session-run monitor liveness evidence in `session_run_monitor`, the combined attention count, a compact `session attention` reason when meeting-owned session health is degraded, and a compact `session-run attention` reason when durable session-run intent is active but not ready. This is the same read-only health summary used by `assemble live-agent health`, so diagnostic smoke artifacts stay excluded and manifest-aware connection gaps remain visible without mentally merging roster, process rows, and durable retry records. An `unknown` group whose latest relevant lifecycle event is `recovered_unknown` reports the safe process reason `orphan running record marked unknown`, so a GUI restart recovery is visible as persisted-state recovery rather than proof that a process was relaunched.
- auto-restart fields in `processes.json`: `auto_restart`, `restart_count`, `max_restarts`, `restart_backoff_seconds`, `stale_restart_after_seconds`, and `next_restart_at`.
- `.agentsassemble/live-agent-runs/events.jsonl`: safe lifecycle event history for supervised groups. It records bounded operator facts such as `started`, `stopped`, `error`, `restart_scheduled`, `restart_failed`, `stale_watchdog`, `stale_watchdog_stop_failed`, and `recovered_unknown` with `timestamp`, `group_id`, `status`, `pid`, `returncode`, and restart counters. Stale-watchdog events include a sanitized `reason` such as a missing, stale, offline, error, or wrong-meeting manifest agent so bounded lifecycle history still explains why a live group was restarted. Lifecycle stop, error, `restart_scheduled`, and `restart_failed` events can include offline reconciliation summaries with expected/offline/skipped counts, safe `offline_agent_ids`, and compact attention entries. The process API and GUI expose each group's bounded `recent_events` view, and `/api/live-agent-process-events` exposes a bounded sanitized lifecycle history for scripts. Lifecycle events do not include command arguments, endpoint URLs, auth references, command paths, prompts, log tails, provider output, or environment-derived values.
- `.agentsassemble/live-agent-runs/operations.jsonl`: safe control-operation history for API, GUI, and CLI operator actions. It records bounded entries for process start/stop/restart, engagement updates, official turn requests/replies, official turn presets, meeting finalization, preflight checks, smoke runs, and readiness checks, including success, degraded, and refused/failed attempts. Browser-visible preflight and provider-health config-load failures redact local config paths and raw loader detail before returning JSON, and provider-health reports redact that path and loader detail at the report source. Meeting finalization rows may include pending-turn cancellation counts and event/request ids, but not the cancelled prompt text. The operation ledger does not include command arguments, endpoint URLs, auth references, prompts, log tails, config paths, environment-derived values, provider secrets, or official turn content. Ordinary heartbeat polling and health reads are intentionally not operation records.
- `.agentsassemble/live-agent-runs/<group_id>.log`: stdout/stderr for the supervised `run-group` process. Delegate provider subprocess stdout/stderr is captured by the runner, not streamed directly into this file. The raw local log file is not scrubbed. The GUI and process API expose only a bounded `log_tail`; safe short tails remain visible, but tails containing auth markers, tokens, endpoints, config filenames, option strings, env references, or path-like values are replaced with `log tail redacted.` before leaving the process supervisor output.

Recent operation views read from the JSONL tail and stop after the requested result window. Recent lifecycle event queries also read from the JSONL tail and stop after the requested result window, with an optional safe group-id filter and a separate `scan_limit` budget for how many recent lifecycle events may be considered before the query stops. If `truncated` is true, the scan budget was exhausted and older matching events may still exist outside the searched tail window. Process rows split bounded `recent_events` by group from the same recent lifecycle tail instead of iterating the full history file; a quiet group with only old lifecycle events may show an empty compact row history, and operators should use `live-agent processes events --group-id <id> --scan-limit <n>` for deeper inspection. Room snapshots used by resident polling and GUI refresh read bounded lobby, side-chat, and live-event tails for their default limited views instead of loading whole JSONL files. If a resident runner's persisted lobby or official cursor has fallen outside the bounded room tail, the runner treats the current tail as newly visible instead of going permanently idle; successful lobby replies keep the source event id as the local cursor so repeated snapshots still avoid duplicate answers. Return-packet projection is the narrow exception: when packet artifacts exist but the targeted event is outside the bounded live-event tail, `/room` may consult full live-event history only to recover the original event id or prove the packet is no longer pending. Full live-event reads remain available through `read_live_events(..., limit=None)` for archive and transcript reconstruction paths that explicitly need complete meeting history. Long sessions can keep historical JSONL artifacts without forcing operator history queries, ordinary room polling, GUI refresh, or process rows to load the whole file or rescan lifecycle history once per group.

The recent process lifecycle history is available through HTTP:

```bash
curl 'http://127.0.0.1:8765/api/live-agent-process-events?limit=20'
curl 'http://127.0.0.1:8765/api/live-agent-process-events?group_id=local-cli-group&limit=20&scan_limit=1000'
```

and through the CLI:

```bash
assemble live-agent processes events \
  --server http://127.0.0.1:8765 \
  --group-id local-cli-group \
  --limit 20 \
  --scan-limit 1000
```

The module form is equivalent when running from a checkout:

```bash
python3 -m agentsassemble.cli live-agent processes events \
  --server http://127.0.0.1:8765 \
  --limit 20 \
  --json
```

Use the lifecycle event view to answer what the supervised process did over time without opening `events.jsonl`. The default CLI output shows timestamp, group id, event type, process status, pid or return code, restart counters, next restart time, sanitized watchdog restart reason, and compact offline reconciliation evidence when present. A stale-watchdog row can therefore include `reason stale manifest agent agent-a` without requiring `--json`. When the scan is truncated, the CLI prints `searched recent N lifecycle events; older matches may exist` so an empty filtered result is not mistaken for complete proof that no older event exists. Use `--json` when an operator script needs the full sanitized event payload, including `limit`, normalized `group_id`, `scan_limit`, `scanned_event_count`, and `truncated`.

The recent operation history is available through the GUI "최근 작업" list, through HTTP:

```bash
curl 'http://127.0.0.1:8765/api/live-agent-operations?limit=20'
```

Add `operation`, `target_id`, and `status` query parameters when a monitor only needs one operation stream, for example:

```bash
curl 'http://127.0.0.1:8765/api/live-agent-operations?limit=20&operation=session.start&target_id=resident-1&status=success'
```

and through the CLI:

```bash
assemble live-agent operations list \
  --server http://127.0.0.1:8765 \
  --limit 20 \
  --operation session.start \
  --target-id resident-1 \
  --status success \
  --scan-limit 1000
```

The module form is equivalent when running from a checkout:

```bash
python3 -m agentsassemble.cli live-agent operations list \
  --server http://127.0.0.1:8765 \
  --limit 20 \
  --json
```

The `operations list` CLI uses the same optional `--operation`, `--target-id`, and `--status` filters as the HTTP query. These filters are applied before the result limit, so unrelated newer operations do not hide the matching rows a monitor is watching for. The filtered read remains bounded by `scan_limit` / `--scan-limit`, and a truncated response includes `scanned_operation_count` plus `truncated: true` so scripts know older matches may exist. The GUI list and the default CLI output include compact safe `details` values, such as readiness result status, reply counts, probe ids, or restart settings. For `session.smoke` and `readiness.check`, those compact details prioritize high-signal liveness evidence such as bounded sanitized health process reasons and attention, observation cursor-lag counts, shared-memory attention/counts, durable session-run attention/retry counts, session-run monitor attention/counts, reply counts, post-restart and post-recover counts, session-smoke soak cycle/reply counts, and soak check statuses before lower-value identifiers; the operation record stores those health summaries, not the raw health payload. For `session.start`, `session.ensure`, `session.resume`, `session.restart`, and `session.recover`, the compact rows prioritize the chosen ensure action and allowlisted ensure reason when present, connected-agent counts, bound-agent reply probe status, optional auto-round status/reason/counts, and requested `finalization_status`, `finalization_reason`, and official event count before lower-value identifiers, so long-session proof remains visible in the recent operation rows. For `discovery.run`, exact approval evidence is prioritized before lower-signal discovery counts so one-shot real-provider approval decisions stay audit-visible without becoming durable provider permission. For `official_turn.rounds`, finalization status and reason are also prioritized before lower-value round ids, so a degraded auto-finalize batch explains whether artifacts were skipped, failed, or finalized. For `meeting.finalize`, cancellation counts are prioritized before lower-value artifact ids, so an operator can see whether pending turns were explicitly closed without exposing the prompt text. Use `--json` when an operator script needs the full sanitized operation payload.

For scriptable operation-history gates, use `live-agent operations list --fail-on-attention`. The command still prints the normal operation summary first, then exits `1` when any returned operation status is not `success`, such as `failed`, `degraded`, or `unknown`. Without that flag, listing operations exits `0` whenever the history was fetched successfully.

Use `live-agent operations wait` when automation needs to observe a specific control operation after launching an action from another surface:

```bash
python3 -m agentsassemble.cli live-agent operations wait \
  --server http://127.0.0.1:8765 \
  --operation session.start \
  --target-id resident-1 \
  --status success \
  --after-id previous-operation-id \
  --scan-limit 1000 \
  --timeout 30 \
  --poll-interval 2
```

The wait path polls `/api/live-agent-operations?limit=N` until a matching operation appears or the timeout is reached. Add `--scan-limit` when a busy room may have more unrelated recent control operations than the result `limit`; the wait still applies `--operation`, `--target-id`, `--status`, and `--after-id` client-side against the returned unfiltered operation stream so the global marker semantics remain unchanged. When `--after-id` is supplied, operations up to and including that id are ignored, so an old matching success cannot satisfy a new wait. The command exits `0` when a match is observed and exits `1` on timeout with the last observed operation summary. If the operation scan is truncated, the timeout payload and compact output include that evidence so a script knows older matches may exist. It exits `2` for non-timeout transport, parsing, validation, or HTTP errors. Use `--json` when another agent or monitor needs the machine-readable `status`, filter fields, `timeout_seconds`, `attempts`, matched `operation`, scan metadata, and timeout `operations` tail.

Use the operation ledger to answer "what control action happened" and the process lifecycle events to answer "what did the supervised process do next." They are deliberately separate surfaces.

For scriptable monitoring, fetch the combined health summary:

```bash
curl http://127.0.0.1:8765/api/live-agent-health
```

The same health payload is available through the CLI for terminal checks and local monitors:

```bash
python3 -m agentsassemble.cli live-agent health \
  --server http://127.0.0.1:8765 \
  --fail-on-degraded
```

Add `--json` when another script needs the raw response instead of the compact operator summary.

Use `live-agent health --wait-ok` when automation needs a bounded room-level readiness gate after starting, restarting, recovering, or otherwise changing resident process state:

```bash
python3 -m agentsassemble.cli live-agent health \
  --server http://127.0.0.1:8765 \
  --wait-ok \
  --timeout 30 \
  --poll-interval 2
```

The wait path polls the read-only `/api/live-agent-health` snapshot until the room reports `status: "ok"` or the timeout is reached. It prints the final health summary, exits `0` when the room becomes ok, and exits `1` on timeout with the last observed health summary. Each poll is bounded by the remaining timeout. It does not mutate process state, run smoke probes, start auto-restarts, stop groups, append operation records, or call providers.

Use `live-agent health --wait-session-ready` when automation should wait for one resident meeting/group even though some other group may still keep the overall room health degraded:

```bash
python3 -m agentsassemble.cli live-agent health \
  --server http://127.0.0.1:8765 \
  --wait-session-ready \
  --meeting-id resident-1 \
  --group-id resident-main \
  --timeout 30 \
  --poll-interval 2
```

The session wait path polls the same read-only health snapshot and succeeds only when `sessions.items` contains the matching `meeting_id` and `group_id` with `status: "ready"`. It prints the final health summary, exits `0` for that target session becoming ready, and exits `1` on timeout with the last observed health summary. It does not call `check-session`, so it does not append repeated `session.check` operation records while waiting.

When `--fail-on-degraded` is combined with `--wait-session-ready`, both conditions must be true before the command exits `0`: the named session must be `ready`, and the overall health status must be `ok`.

Exit code contract:

- `0`: health was fetched and either reported `ok`, or reported `degraded` without `--fail-on-degraded`.
- `1`: health reached the server but reported non-`ok` while `--fail-on-degraded` was set, `--wait-ok` timed out before health became `ok`, or `--wait-session-ready` timed out before the named session became `ready`.
- `2`: the CLI could not fetch or parse the health response, or the command arguments were invalid.

The response reports overall `status` as `ok` or `degraded`, plus `agents.counts`, `agents.attention`, host-admission health evidence in `admission`, `processes.counts`, `processes.attention`, safe `processes.reasons`, process monitor liveness evidence in `process_monitor`, manifest-aware `connections` evidence, meeting-owned session readiness in `sessions.items`, shared meeting memory health evidence in `shared_memory`, durable session-run retry evidence in `session_runs`, and session-run monitor liveness evidence in `session_run_monitor`. Each session item is read-only and includes safe `meeting_id`, `group_id`, `status`, `process_status`, expected/connected counts, ownership attention, process attention, connection attention, and combined attention. Health marks a session `ready` only when the stored meeting exists, the process group is `running`, the process manifest matches that meeting's bound agents, every bound agent still has a resolvable provider config, every bound agent has fresh `online` or `working` presence for the same meeting, and no other non-diagnostic `running` or `restarting` group owns that same meeting. Duplicate active meeting ownership degrades each active session item with `meeting:duplicate_active_group`, so an old shadow process cannot look ready beside the real resident group. Missing or malformed meetings degrade that session instead of failing the health read, and the payload does not echo config paths, command arguments, endpoint URLs, auth refs, prompts, log tails, provider output, or replies. `binding_provider_missing` is safe session connection attention only; it does not expose the missing provider id. `admission` is a compact read-only summary with derived status counts and safe attention labels; it does not expose session ids, endpoints, auth refs, config paths, command arguments, prompts, provider output, presence error text, or caller-supplied spoofed admission fields, and admission attention is not part of overall health degradation. `shared_memory` is a compact read-only summary with safe ids and counts; it does not expose official reply text, prompts, transcript bodies, return packets, or raw memory artifact bodies. A durable session-run that is active but not ready adds `session-run attention` such as `resident-m1:resident-main:<run_id>:degraded:retrying`, carries safe retry counters in `session_runs.items`, and degrades health while it waits for the next durable retry. An active stored `ready` session-run also carries its current readiness overlay in `session_runs.items`; if that current readiness overlay is degraded or missing, health reports attention such as `resident-m1:resident-main:<run_id>:ready:no_current_readiness` so historical durable intent cannot masquerade as current resident liveness. `readiness.check` operation records preserve the same safe long-session health causes for degraded checks, including observation cursor lag, shared-memory attention, session-run retry/drift attention, and session-run monitor attention, but only as compact labels and counts rather than raw health JSON, event text, prompts, replies, config paths, endpoints, auth refs, or provider output. Diagnostic durable session-runs stay excluded from health so smoke or doctor probes do not contaminate operator readiness. The process supervisor's `process_monitor` reports only compact fields such as `running`, `interval_seconds`, `last_tick_at`, `last_status`, `last_group_count`, and safe `last_error_type`; a failed process monitor snapshot degrades health, but a health read never calls `list_groups()`, starts due restarts, ticks the monitor, calls providers, appends operation records, or scrapes raw exception messages. When the GUI owns the lifecycle session-run monitor, `session_run_monitor` reports only compact fields such as `running`, `interval_seconds`, `last_tick_at`, `last_status`, `last_result_count`, and safe `last_error_type`; a failed monitor snapshot degrades health, but a health read never ticks the monitor, calls providers, appends operation records, or scrapes raw exception messages. These monitor snapshots prove the supervisor loops are alive or recently failed; they do not prove provider credentials, CLI login state, or current durable session readiness by themselves. A non-diagnostic process group that is restarting, error, unknown, or stopped can include a compact sanitized watchdog reason such as `stale_watchdog missing manifest agent agent-a`, a restart-failed launch-evidence reason such as `restart_failed missing launch config` or `restart_failed missing launch server`, or the GUI-restart recovery reason `recovered_unknown orphan running record marked unknown` in `processes.reasons` and the matching session item, so `live-agent health` and `live-agent doctor` explain high-level process attention without opening lifecycle JSON. Suspicious, non-watchdog, or unrecognized restart-failure reason strings are dropped from health. A meeting-owned non-diagnostic running process group whose bound agents are missing, attached to a different meeting, not reconnected after the group start, stale, offline, error, or `binding_provider_missing` adds session connection attention and degrades health. Diagnostic smoke groups are ignored so repeated doctor checks do not contaminate readiness.

Unsafe process or session owner ids and reason labels are not public health
evidence. If a persisted process group, session readiness row, watchdog reason,
live-agent roster row, or legacy durable session-run carries an `env:`, `literal:`, token-like,
absolute or relative slash/backslash path-like, URL-like, or JSON-file-shaped meeting/group/run/agent id, health
omits that value or replaces it with a stable placeholder such as
`missing-process-group-id-2` or `unknown`. This applies to
`agents.attention`, `processes.meeting_ids`, `processes.attention`, `processes.reasons`,
`sessions.items`, `sessions.attention`, session-run readiness overlays, and
`include_readiness=1` overlays. Public durable session-run result dictionaries
also drop unsafe nested keys before returning `process`, `connection`,
`reply_probe`, `auto_rounds`, or `finalization` evidence.

Session connection evidence is binding-compatible rather than id-only. A roster row with
the same `agent_id` and `meeting_id` is not counted as connected when its current
provider or connection kind conflicts with the resident process manifest or the
meeting's host-approved binding rules. These cases surface as safe
`provider_kind_mismatch`, `connection_kind_mismatch`, or
`binding_provider_missing` attention instead of repairing the roster row,
rejecting permissive manual registration, trusting a stale binding, or exposing
session ids, provider ids, endpoints, auth refs, config paths, commands,
prompts, logs, or provider output.

This endpoint is a read-only snapshot. It does not refresh process handles, launch due auto-restarts, stop groups, or mutate process state.

The GUI event streams are also bounded failure surfaces. If a meeting disappears after a `/api/meetings/<meeting_id>/events` SSE connection is already open, including during a payload file read, the server sends one `event: error` SSE payload with the `stream`, `meeting_id`, and error message, then closes that connection instead of leaking a handler traceback. A request for a missing meeting before the stream opens still returns the ordinary JSON `404`.

External or manually driven agents can also report an error heartbeat through the CLI:

```bash
python3 -m agentsassemble.cli live-agent heartbeat \
  --server http://127.0.0.1:8765 \
  --agent-id claude-code-live \
  --status error \
  --last-error "delegate command failed" \
  --last-attention persona_context_blocked_official_turn \
  --last-observed-event-id evt1 \
  --last-observed-live-event-id live_evt1 \
  --last-reply-at 2026-05-17T12:00:00+00:00 \
  --json
```

That command writes the same `last_error`, `last_attention`, `last_observed_event_id`, `last_observed_live_event_id`, and `last_reply_at` fields used by resident runners, so non-runner agents can stay visible in the roster without inventing a separate status path. Presence `last_error` is sanitized before persistence/readback; URLs, scheme-less host/path endpoints, auth/token assignments, env/literal refs, config paths, prompt refs, absolute paths, and common secret-looking values are replaced with `Live-agent presence error details redacted.` before the roster or GUI can expose them. `last_attention` is a short operator-safe label such as `persona_context_blocked_official_turn`, and suspicious values are replaced with `presence_attention_redacted`. Safe short error labels such as `command failed`, `oauth failed`, or `configuration failed` remain visible for operators. Use `--json` when an external agent wrapper needs to verify the persisted heartbeat acknowledgement, including both cursor fields, instead of parsing the compact `agent-id: status` line.

## Claude And Evidence-only CLI Smoke

Only run real provider smoke when the CLI is installed, authenticated, and you have explicit approval for any cost, network, account, or external side effect.

Check local availability for startable provider paths and evidence-only inventory:

```bash
command -v claude
command -v agy || command -v antigravity
command -v hermes
```

If approved and installed, `configs/live-agents.example.json` still shows the generic Claude command-shape example:

```json
["claude"]
```

The Claude example uses `connection_kind: "terminal_session"` so it stays alive behind a PTY instead of using one-shot stdin/stdout delegation. Antigravity is not listed as a bare `agy`/legacy `antigravity` runner recommendation: current continuity evidence keeps bare Antigravity discovery as `unsupported_evidence`, and only a separately written wrapper that truly owns the room loop should be hand-authored as `connection_kind: "self_service"`. Hermes is also evidence-only in discovery until a clean session-id proof or self-service wrapper exists. Gemini CLI is now legacy for consumer use after Google's May 19, 2026 transition announcement; do not substitute bare Antigravity discovery as a runnable Google-backed resident without new proof.

For the first Codex resident shape, use `configs/live-agents.codex-session.example.json`. It uses `provider_kind: "codex_live_session"` with `connection_kind: "live_session"` and may omit `command`, in which case the resident runner defaults to `["codex"]`:

```bash
python3 -m agentsassemble.cli live-agent preflight \
  --config configs/live-agents.codex-session.example.json
```

After approval, run the bounded real resident config smoke:

```bash
python3 -m agentsassemble.cli live-agent real-session-smoke \
  --server http://127.0.0.1:8765 \
  --live-agent-config /path/to/approved-live-agents.real.json \
  --council-config /path/to/matching-council.json \
  --agent-config /path/to/matching-agents.json \
  --group-id real-provider-smoke \
  --meeting-id real-provider-smoke \
  --timeout 240 \
  --approve-real-providers
```

When you need deeper but still bounded room evidence for a provider-specific
resident, opt into the extra checks explicitly:

```bash
python3 -m agentsassemble.cli live-agent real-session-smoke \
  --server http://127.0.0.1:8765 \
  --live-agent-config /path/to/approved-live-agents.real.json \
  --council-config /path/to/matching-council.json \
  --agent-config /path/to/matching-agents.json \
  --group-id real-provider-smoke \
  --meeting-id real-provider-smoke \
  --timeout 240 \
  --approve-real-providers \
  --official-round-smoke \
  --restart-smoke
```

That command calls `POST /api/live-agent-real-session-smoke`. It requires
current explicit approval plus explicit live-agent, council, and agent config
paths before contacting the room, starts the supplied resident config in
diagnostic mode, requires bounded `probe_bound_agents` evidence from the bound
agents, then calls `stop-session` for the same meeting/group even when start or
probe fails. The diagnostic probe source events and matching live-agent replies
are redacted from the durable lobby log, including late replies that arrive
after the initial probe wait. The response and
`session.real_smoke` operation expose only safe ids and counts/statuses:
start status, connected/expected counts, reply-probe counts, optional official
round counts, optional post-restart connection/probe counts, stop status, and
post-stop process status. They do not expose config paths, commands, endpoint
URLs, auth refs, prompt text, reply text, provider output, log tails, or a
durable approval grant. A stop or post-stop failure reports `degraded` so an
operator knows cleanup still needs attention.

For slow real CLIs such as Grok, use the longer timeout. A local Grok-only
generated bundle has passed this smoke with `start_status: "ready"`,
connected/expected `1/1`, reply-probe `1/1`, `stop_status: "stopped"`, and
`post_stop_process_status: "stopped"`. That proves one approved local
start/probe/stop path, not official-turn quality, restart/recover behavior,
future billing state, tool safety, or sandboxing.

A deeper Grok-only run first timed out on the official round, which is kept as
historical evidence that slow provider CLIs need a separate official-turn
command budget. After the fake lifecycle regression covered that routing
boundary, a later approved rerun with `--official-round-smoke --restart-smoke`
returned safe evidence that the official round and restart path worked:
official round answered `1/1` with `0` timeouts, `restart_status: "ready"`,
post-restart connected/expected `1/1`, post-restart reply-probe `1/1`,
`stop_status: "stopped"`, and post-stop process stopped. Treat that as limited
local official-turn and restart evidence, not as production-ready recover, tool
safety, future billing, or sandbox evidence.

Resident configs may set `official_turn_timeout_seconds` for a provider command
budget that applies only while answering targeted official turns. The default is
`0`, which preserves the ordinary `timeout_seconds` behavior. Use it only for
controlled follow-up experiments with slow real CLIs such as Grok: lobby probes,
flow decisions, cursor acknowledgements, heartbeats, restart/recover checks,
and stop paths continue to use the ordinary timeout. The checked-in fake Grok
lifecycle regression proves this routing boundary, not real Grok official-turn
quality.

The supplied council and agent configs must bind the same `agent_id` values as
the resident config. `configs/live-agents.example.json` is useful as the command
shape reference for Claude only; Antigravity needs a separate wrapper that owns
the room loop before it belongs in a startable resident config. The example file
is not paired with the demo `configs/agents.start-session.example.json`;
generate or provide a matching host-approved bundle before running the real
smoke.

For lower-level debugging after the same approval, you can still start a
bounded process group directly:

```bash
python3 -m agentsassemble.cli live-agent run-group \
  --config configs/live-agents.example.json \
  --server http://127.0.0.1:8765 \
  --max-ticks 2
```

Provider command failures are recorded on the live-agent presence as `last_error`. During the failed tick the presence can report `error`; after a bounded run exits or a group is stopped, the final heartbeat can report `offline` while preserving `last_error`. Resident subprocess failures are compacted before they reach that field: non-zero exits report only the return code, timeouts report only the timeout, OS launch failures report only the safe error category, and command args/stdout/stderr are not copied into presence. After a healthy room snapshot selects an eligible lobby or official-turn event, the pre-command `working` heartbeat is best-effort evidence only; a transient presence write failure cannot block the provider command or the durable reply post. Because that heartbeat is not a lock, a process that dies after provider execution but before the reply post may retry the same event on restart. The provider-command error heartbeat is best-effort: if that presence write fails, the runner keeps its local `last_error` and cooldown/backoff state and continues polling instead of converting the handled provider failure into a process crash. Periodic heartbeats during the failure backoff retry the same error evidence. If a generated lobby or official-turn reply cannot be posted back to the room, the runner attempts the same `error` heartbeat with the relevant observed cursor before re-raising the original post failure for the supervisor. That error heartbeat is best-effort and cannot mask the original post failure if the heartbeat write also fails; its `last_error` keeps safe short labels but redacts suspicious post-failure details before sending presence. Once a reply post succeeds, the follow-up `online` heartbeat that carries `last_reply_at`, cleared `last_error`, and cursor evidence is also best-effort; a transient presence write failure cannot turn the already-posted reply into a failed runner tick. After a runner has read at least one room snapshot, a transient read-only `/room` failure also attempts an `error` heartbeat and the runner keeps polling instead of taking down the whole resident group; the first room read still fails fast because there is no usable room state yet. That transient-room error heartbeat is best-effort and redacts suspicious room-read details before presence, so a simultaneous heartbeat write failure does not stop the next poll. A recovered room read does not inherit the provider-command failure cooldown, so a new eligible event can still get an immediate reply, and the runner sends an `online` heartbeat with empty `last_error` to clear that transient room error once a healthy snapshot is visible again. The final offline heartbeat is also best-effort: if the room server is already unavailable during shutdown, the runner keeps the completed reply count or handled command-error result instead of converting shutdown into a provider failure. The process row can still be `stopped` with return code `0` when the resident runner handled the delegate error internally. This runner boundary sanitizes presence evidence only; raw local exceptions and raw process log files remain operator diagnostics. Use `live_agents.json`, `processes.json`, and the bounded sanitized `log_tail` together rather than trusting one field alone.

## Recovery Expectations

After restarting the GUI:

- old `running` records in `.agentsassemble/live-agent-runs/processes.json` become `unknown` with their stale `pid` cleared;
- old `stopped` and `error` records remain listed;
- previous logs remain inspectable through their `log_path`;
- the new GUI supervisor does not claim it can stop PIDs it did not launch;
- restarted resident runners reuse `last_observed_event_id` from their live-agent presence so they do not answer the same lobby event again, and they recover when that cursor has aged out of the bounded room tail;
- existing presence rows in `.agentsassemble/live_agents.json` can remain until heartbeat age makes them `stale`; restarting the GUI does not resume old resident agents except pending auto-restart records, which can start a fresh process after `next_restart_at`.

This slice is not native Claude Code Channels, Antigravity native sessions, tmux ownership, Cursor terminal persistence, or OS-level sandboxing. The JSONL `live_session` transport is not a native Claude, legacy Gemini, or Cursor PTY protocol. The `terminal_session` transport is a local PTY bridge, and `self_service` supervises a child process without injecting room prompts, but provider-specific channel injection and stronger sandboxed launch paths remain future backend variants behind the same room and supervisor shape.
