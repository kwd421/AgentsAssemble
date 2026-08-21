# Current System Orientation

Status: current starting point

Updated: 2026-08-02

Read this file before changing rooms, Agent Sessions, providers, invites,
moderation, media, or the React room UI. It is intentionally short. Follow its
links only when the requested change touches that boundary.

## Product

AgentsAssemble is a shared-room product where humans and persistent AI provider
sessions participate through one canonical room model. The primary path is not
an API-first meeting runner and not a sequence of one-shot prompt calls.

The current product surface is:

- multiple rooms, each with a `#general` channel;
- humans and agents in one participant roster;
- persistent Codex, Antigravity, Grok, Claude, Cursor, OpenCode, and compatible
  provider sessions behind provider-specific adapters;
- browser, mobile-layout, and Tauri desktop clients using the same room protocol;
- explicit start, pause, resume, interrupt, stop, kick, leave, and delete
  lifecycle actions;
- sequenced room history, reconnect replay, bounded provider context, and
  provider-owned private conversation state.

The retired meeting/research/decision/archive pipeline is not part of the
current product. New shared-room behavior belongs on the canonical room path.

## Canonical Architecture

```text
Browser or Agent Bridge
        <-> ticket-authenticated /ws?ticket=...
RoomRealtimeController
        <-> RoomRepository
                <-> persistence/local/room/RoomStore / rooms.sqlite3
                    (current local default)
        <-> persistent provider adapter
```

There is one authority for each concern:

- room, room-global settings, participant, Agent Session, event, and command state:
  controller-injected `RoomRepository` (SQLite by default, or explicitly activated PostgreSQL);
- live transport: canonical ticket-authenticated WebSocket;
- browser state: canonical snapshot plus sequenced events;
- provider process: one Agent Bridge and one persistent provider adapter;
- provider-private memory: provider-owned session state;
- media bytes: room media storage referenced by safe IDs, never local paths in
  public events or model-visible metadata.

Do not add a provider-specific browser socket, parallel room event store,
polling-based live UI, or a second participant registry.

Detailed current implementation: `docs/live-cli-room-current-architecture.md`.

## Current Native Clients

`desktop/` owns the cross-platform Tauri 2 desktop and mobile clients. The
desktop startup surface
opens immediately and starts the local room runtime in the background; it does
not ask the user to select a connection mode. The desktop build packages the
Python server and React application as a platform-native sidecar, so users do
not start a separate server before opening the client. A visible progress state
remains responsive while a first-run package is being prepared. A bounded
desktop-owned directory cache renders saved room summaries before that runtime
is ready. Rooms from another server are retained as disconnected entries when
the local server directory refreshes; selecting one does not bind its room ID
to the local server or issue local room API calls.

Local and hosted use are two exposure states of the same canonical room
runtime. The runtime remains bound to loopback; hosting is an explicit
authenticated public-invite action inside the room UI, not a direct
non-loopback control-plane bind. Stopping public access returns the rooms to
local-only use without moving their records.

CLI `gui` and the desktop sidecar share one default product data root
(identity, rooms, runtime state): the platform user application-data directory
for AgentsAssemble (`~/Library/Application Support/AgentsAssemble` on macOS),
overridable with `AGENTSASSEMBLE_OUTPUT_ROOT` or an explicit `--output-root`.
Port numbers are not product identity; a bound engine advertises itself for
that data root in `runtime/local-engine.json`. Desktop and CLI `gui` reuse that
engine only when the registry entry still names a live pid and the loopback
runtime-version probe succeeds. Arbitrary listeners on fixed ports (for
example 8765) are not trusted without that root-scoped readiness path. The
product is not a hard OS singleton, but one healthy engine per shared data root
is the intended steady state. A desktop-owned sidecar also watches the native
shell process that launched it and follows the normal server cleanup path if
that parent disappears, so an app crash or forced exit does not leave an
orphan listener or reusable-engine registry behind.

The bundled desktop startup surface can start or attach that local runtime,
open its validated HTTP(S) origin, and read the bounded public room-summary
cache. The native client refreshes local room summaries after the runtime is
ready and again before graceful shutdown. A room webview at the explicitly
selected server origin may update that server's room summaries in the native
cache, but it has no runtime-lifecycle privilege and cannot rewrite entries
owned by a different server. The cache accepts only bounded room labels,
appearance, origin, and timestamps; it drops unknown fields so bearer
credentials cannot be persisted through this path. The webview keeps its own
persistent browser storage, separate from Safari or Chrome.

The iOS and Android applications use the same Tauri shell and room protocol but
do not package or start the Python room runtime. They open without a server,
render the native room-summary cache offline, and accept a validated HTTP(S)
server, invite, or one-time recovery link by text or QR scan. Only the selected
same-origin room server may refresh its cached summaries. A mobile client can
therefore reconnect to local-public or future cloud-hosted rooms without making
cloud hosting a startup dependency. Native debug builds are keyless; App Store
and Play Store publication, signing, and hosted room infrastructure are not
current runtime behavior.

A configured desktop release checks a signed HTTPS update manifest from the
bundled startup surface before starting its owned room runtime. Download and
signature-verification progress stays visible, then a successful install
restarts the native application. Development and keyless builds make no update
request and continue to local/offline rooms. Update-service failure is
non-blocking, while an invalid or unsigned artifact is never installed. The
release endpoint, verification key, updater signing key, and platform signing
credentials are build/release inputs and are not persisted in the repository.

A public account identity is distinct from the private per-client device
credential. The client generates a durable device token once (browser or
desktop WebView `localStorage`) and the server maps its fingerprint to a stable
`user_id` / guest participant for that server identity store. The same device
token on the same client storage therefore keeps the same guest identity across
restarts; a different browser or WebView partition is a different device unless
the user links a public account or completes guest recovery. Before host-room
connections begin, first use requires an explicit choice between a linked public
account and a device-local guest profile. Local rooms remain usable without a
public account or Internet connection. A verified Google subject produces a
stable opaque `acct-...` ID; the raw Google subject, ID token, name, email, and
profile image are not stored. Linking is an explicit account action and
never silently merges two identities. When the selected Google account already
belongs to another server user, the client must warn that the current guest
profile, recovery material, and active participation will be discarded. Only
an explicit confirmation may move the current device to that existing account;
past public room events remain immutable. Operator identities and guests that
still own a server fail closed instead of being discarded.

The web account surface uses Google Identity Services only when the server has
`AGENTSASSEMBLE_GOOGLE_WEB_CLIENT_ID` configured. The backend verifies the ID
token audience, issuer, expiry, and a short-lived one-use nonce before writing
the opaque account link. Logging out removes only that server-side
public-account link; the local profile, rooms, and durable device credential
remain. A remote account mutation requires forwarded HTTPS;
a spoofed loopback Host header is not treated as a local request. Google does
not permit this web flow inside an embedded WebView. Tauri clients use the
central desktop OAuth flow during startup instead of a server-local browser
handoff. The server-local account surface remains available only to ordinary
browsers, where Google Identity Services can return directly to the page that
initiated the login.

### Deferred plugin extension boundary

A user-installable plugin system is a product direction, not current behavior.
Account identity and the shared room-directory contract come first so plugin
ownership, settings, and synchronization have an unambiguous user and server
authority. Do not add arbitrary Python or JavaScript discovery as an interim
shortcut.

The intended model is closer to RisuAI's versioned, permissioned extension API
than to patching private client internals. A future manifest may declare
separate server, sandboxed web-client, and Agent-tool entry points. Plugins must
use canonical room commands/events, named UI contribution slots, and
plugin-scoped storage and secrets; they must not mutate the room database or
React application internals directly. File access, external network access,
room reads, room writes, moderation, secrets, and native execution require
separate visible permissions. Desktop-only native capabilities must fail closed
or render unavailable in an ordinary browser.

The existing provider registries, route registration functions, feature
packages, capability checks, and Room Connector/MCP tools are useful internal
extension seams, but they do not yet provide plugin discovery, manifests,
sandboxing, lifecycle isolation, version compatibility, installation, or
updates. When this work starts, prove the API by moving one bounded first-party
feature through it before accepting third-party plugins.

The first bounded capability split is built in rather than installed as a
plugin: canonical room `tool_mode` selects `chat` or `tabletop` tools while
conversation cadence remains separately controlled by `conversation_mode`.

## Current Rolling Restart Contract

The local POSIX GUI server can replace its backend process without changing its
listening address. The replacement receives the already-bound listener, builds
the complete service graph, and reports ready before the old process stops
accepting. An active provider turn blocks the handoff; the server waits for an
idle boundary instead of cutting a model call in half. If the replacement does
not become ready, the old process remains authoritative and records the child
startup error.

Server-owned Agent Bridges receive a renewable, room-scoped reconnect session
at launch. During handoff the old process closes room transports without
stopping provider runtimes, and bridges reconnect to the new process with fresh
one-use WebSocket tickets. Shared OpenCode server ownership is adopted by the
replacement and remains part of final shutdown; preserving a process must not
turn it into an orphan.

The browser WebSocket reconnects through its existing resume cursor. Each GUI
generation serves an immutable frontend build snapshot, so building the next
`dist` cannot mix old HTML with new chunks for existing clients. A backend
handoff alone does not replace JavaScript already executing in an open tab.
`/api/runtime/version` exposes the served frontend build and protocol versions;
an open client detects a changed build and offers a safe reload. Newly opened or
reloaded clients receive only the new static build.

## Package Ownership

Current domain code is owned by `room/`, `admission/`, `identity/`,
`providers/`, `web/`, `application/`, `diagnostics/`, and `persistence/`.
The retired `legacy/` package and its HTTP, CLI, GUI, process, meeting, and
resident paths have been removed. Remaining root imports are explicit shims for
current code and are recorded by `scripts/check_package_architecture.py`; new
flat product modules are rejected.

`docs/product/PACKAGE_MAP.md` is the generated inventory and
`docs/product/PACKAGE_CYCLES.md` is the generated cycle report.
`docs/product/CODEBASE_MAP.html` (plus its `CODEBASE_MAP.json` twin) is the
generated interactive codebase map for orientation; regenerate it with
`python3 scripts/generate_codebase_map.py`. The remaining
root conversation-policy modules are intentionally frozen while autonomous
participation semantics are unsettled. `models.py`, `config.py`,
`persona_cards.py`, and `character_mode.py` remain explicit cross-domain
migration residue until their provider and retained-meeting contracts are
split deliberately.

`make architecture-check` is the mandatory growth boundary. It rejects new
package-root ownership violations, dependency/cycle regressions, growth beyond
the recorded ceilings in `docs/product/SOURCE_GROWTH_LIMITS.toml`, and new
unowned source files over the repository limit. Those ceilings are pressure
signals, not refactoring targets: split only at a real responsibility,
ownership, lifetime, validation, or side-effect boundary.

## Current Browser Identity And Admission

The local server operator is one canonical identity:
`operator-local-user` / `operator-local`. A host-authorized device claim binds
that browser credential to the canonical user instead of creating another
operator participant. Ordinary guest admission cannot reach that privileged
claim path.

User profile settings are owned by that authenticated server identity, not by
browser-local UI state. The operator and every admitted browser guest have
separate profiles keyed by server user ID. A display-name or avatar update also
updates that user's canonical room memberships and participant records, emits a
`participant_updated` event, and is reused when a new WebSocket ticket is
issued, so reconnect cannot restore an older invite-time name.

Opening `/join?token=...` first performs a side-effect-free admission check. A
valid existing room session is preserved, a known same-origin device reuses its
server profile, and an unknown device sees the explicit guest profile form.
Preflight does not consume the invite, create a user, change membership, or
issue a session.

Invite client scope is enforced before an admission workflow is created.
Browser admission consumes only browser invites. Opening an `agent_bridge`
invite in a browser returns `agent_client_required` without changing invite use,
identity, participant, membership, workflow, or session state. The native
`assemble room attend` client uses the separate Agent admission route and must
match the provider kind bound to the signed invite; it cannot consume a browser
invite.

After browser or remote-app admission, room reads and writes use only the
ticket-authenticated canonical WebSocket: request `/api/ws-ticket`, subscribe
to `room_events`, and send correlated `message.send` commands. The former
session-scoped lobby polling, SSE, and HTTP speech endpoints were removed before
external adoption because they wrote a separate legacy lobby record. An
`agent_bridge` invite starts or attaches the provider named by the invite as a
separate Agent Session; it does not transplant the AI application session that
opened the invite.

A supported Codex/Claude-style app or interactive CLI can instead register
`assemble room connector-mcp` once. Giving that current conversation a normal
AI-session `/join` link then makes the same conversation call `room_join`,
`room_read`, `room_say`, vote and server-side randomness tools,
`room_wait_next`, and `room_leave`; it never starts a replacement provider.
The connector hides canonical transport details and waits event-first without
repeated model calls. Current decision and capability limits:
`docs/product/ROOM_CONNECTOR.md`.

The mutating join uses one browser-generated request ID and a durable admission
workflow. Invite consumption and the workflow's consumed phase commit together;
identity, bounded session, participant, and membership phases can then resume
after a lost response or process restart without consuming the invite twice.
The workflow stores only invite/device/payload fingerprints and bounded public
metadata. Raw invite tokens, device credentials, and room bearer tokens are not
persisted. Reusing a request ID with different admission inputs is an explicit
`idempotency_conflict`, not a second join.

The GUI application owns one invite service, room-session service, admission
coordinator, and operator-pairing service for its lifetime. Current invite and
pairing routes use those injected owners; module-global invite/identity helpers
are compatibility-only and are not the route authority.

Cross-origin operator continuity uses a separate moderator-created `/pair`
link. It is room- and target-origin-bound, expires after at most two minutes,
and is one-use across devices. Redemption durably binds the pairing to the
consuming credential fingerprint and records claiming, retryable-failure, and
completed phases. The same device can therefore resume a partial redemption or
recover the same still-active bounded bearer after a lost response; another
device is rejected. Raw pairing, device, host, and room bearer tokens are never
stored in the pairing record or sent to the public origin. This is not account
login and does not identify a user across different AgentsAssemble servers.

Public account identity is stored by the selected identity backend alongside
users and credential bindings. It is not a replacement for room admission or
the private device credential: an account may reconnect identity across
clients, while each room still grants its own membership and bounded session.

Detailed implementation and verification:
`docs/reports/2026-07-15-browser-identity-admission.md`.

## Current Provider Contract

Continuous providers receive a server-assigned turn containing a bounded room
diff after their durable cursor. A turn reuses the existing provider process
and must not launch a one-shot CLI.

Ordered and ambient providers use a different input mode. The canonical event
broker keeps each Agent Bridge's private `RoomPortal` current, then sends a
`room.wake` containing event/cursor and referenced-attachment identifiers but
no provider transcript. Each wake also carries structural `observation_kind`
metadata: `ordered_floor` for the one provider selected in ordered mode, or
`ambient_observation` for a discretionary ambient or idle observation. The
kind is fixed when the source event is queued and remains attached to that
pending input across later room-mode changes; the bridge must not derive it
from the current room setting. It conveys provenance only, not room content or
an instruction about what to say. The provider reads its bounded room mirror
and either publishes through the portal or publishes nothing; a provider with
the ordered floor may still decline. The Agent Bridge reports only a
content-free observation completion. For a server-owned session, the room
server resolves the active bridge handle and reads the matching turn directly
from that handle's private portal outbox; bridge-supplied message, target, or
vote fields are rejected. An externally owned bridge currently fails closed
with `room_portal_provenance_unavailable` on observation publication because
the room server cannot verify its local outbox. Only verified portal output
becomes `message_final`; `decline_to_speak` records a supported reason code and
no public message. Ordinary assistant or terminal output is private and is
never used as an implicit fallback.

### Pending decision: API-provider final-answer publication

The current no-fallback rule remains authoritative. A 2026-08-01 Cerebras
`gpt-oss-120b` observation showed why the rule may need a provider-protocol
follow-up: the model successfully called `read_discussion`, then returned a
non-empty ordinary assistant answer without calling `publish_message`. The
bridge correctly treated the turn as a structured decline because the ordinary
answer was private. This was not a timeout, an empty model response, or a
browser rendering loss.

Do not silently change this behavior. The product decision is still pending
between:

1. require an explicit `publish_message` or `decline_to_speak` tool result after
   an API provider reads the room, and surface noncompliance as a protocol error;
2. allow a narrowly recorded API-provider fallback that publishes a non-empty
   ordinary final only after a successful room read, no explicit decline, and no
   portal publication.

Any fallback must be explicit in the canonical event metadata and diagnostics,
must never publish reasoning content, and must not be generalized to terminal
or coding-agent output. Until this decision is made and verified through a real
provider room turn, ordinary output remains private.

For compatibility with older servers and persisted sessions, a missing kind is
normalized to `ambient_observation`; explicit unknown values are rejected, and
the compatibility path never infers an ordered floor.
Portal publication may atomically include one `target_agent_id` handoff. Codex
and other MCP-backed providers use the optional `next_agent_id` argument on
`publish_message`; terminal providers use `agentsassemble-room speak-to`.
Grok ACP receives the tools allowed by the observation's canonical room-tool
mode through its `session/new` / `session/load` MCP configuration and permits
only those correlated MCP calls during an observation. Its former virtual-file
publication and terminal-roll paths were removed before external adoption. The bridge carries a publication
target into the canonical message event; ordered routing then gives that
provider the next observation without parsing the public prose.

Room-global `tool_mode` is independent of conversation routing. New rooms use
`chat`, which exposes room reading, the public participant directory, explicit
publication or decline, and structured vote creation, ballot, and bounded-view
summary tools. A host may select `tabletop` to additionally expose server-side
`roll_dice` and `choose_random`; terminal providers receive the matching
bounded `agentsassemble-room` commands for the same audited contract. The
server enforces this capability on browser commands, provider schemas, portal
execution, bridge result publication, and Grok permission auto-approval. Inputs
and results are recorded in the private portal
activity log with a tool-generated result ID. During the active turn, the
server-owned bridge projects each validated dice or random-choice record through
the bridge-only `room.result.publish` command. The server accepts that command
only during a `room_observation` turn owned by its bridge process, validates the
record again (including the selected index against the recorded candidate
list), and commits a separate canonical system message and its ACK atomically.
A provider-output failure does not discard an already recorded result; an ACK
timeout is retried once with the same request ID. Result messages do not wake
providers and are not merged into the provider's public reply. Provider-supplied
tool reasons and random-choice candidate lists remain private validation input;
the public event metadata contains only the bounded result fields.

Entering `/vote` opens the human vote
composer for a question, two to ten named options, and a bounded deadline. The
server normalizes the poll, computes its deadline, rejects unknown choices and
late ballots, and stores the matched option text. Vote questions, options,
deadline, and recorded ballots are rendered in every provider's private room
mirror. The browser shows each recorded ballot and other system messages as a
centered separator rather than as a participant message row, and keeps the
aggregate tally and ended state on the vote card. Ballot result rows
do not wake providers. There is no separate vote-close/final-winner event.
Agent Sessions use `create_vote` and `cast_vote`; the bridge carries those
structured fields through `message.final`, and the canonical repository applies
the same validation and tally rules as the human UI. `vote_summary` is explicitly
limited to the provider's current bounded mirror rather than claiming a
full-history authoritative query.

Provider reasoning summaries and tool/work activity use the canonical room
event stream but are private to the owning participant by default. Other
participants receive a sequence-preserving hidden event so their durable room
cursor remains contiguous without exposing the activity payload. An operator
may explicitly enable `share_activity` for an Agent Session; only then are that
session's subsequent activity events projected publicly. Public final messages
are unaffected by this preference.

API-provider compatibility is defined by protocol family and room-tool
capability, not by an “API” label alone. DeepSeek, Cerebras, OpenRouter, and
Vercel AI Gateway use the shared OpenAI-style streaming room runtime, keep
reasoning private, execute the same mode-authorized `RoomPortal` operations,
and record usage for every HTTP round. Provider profiles own model validation
and protocol differences such as DeepSeek thinking fields, Cerebras request
headers,
gateway attribution headers, bounded output tokens, and whether reasoning
fields may be replayed in a later assistant message. The creation dialog and
stopped Agent Session panel expose the same provider-owned output-token choices
(1,024 through 16,384, default 4,096); the selected value is persisted in the
runtime profile and sent as `max_tokens` on every HTTP round. Static provider
profiles list only models verified through the real room read/publication path.
Cerebras and gateway catalogs may additionally list text models whose
authoritative model metadata advertises tool calling; that is catalog-reported
capability, not a claim that every listed model has passed the room workflow.
OpenRouter's current default
was chosen from a real room-path verification; Vercel remains catalog-verified
only. Anthropic Messages and Gemini
`generateContent` need their own protocol adapters; a text-completion endpoint
alone is not a room provider.

OpenAI-compatible API and Local sessions start with meeting-only access and no
workspace tools. An operator may explicitly choose `workspace_write` and a
workspace to attach the built-in API work harness. The model may then list,
search, and read bounded UTF-8 content only below that resolved workspace;
repository control directories and path escapes are rejected. File creation,
exact replacement, and argv command execution each block on a private one-use
owner approval before the side effect. Commands do not use an implicit shell,
receive a bounded environment and timeout, and return bounded output. This is a
small auditable harness, not a claim that the API model has acquired a native
Codex, Claude Code, or Grok CLI session.

API and Local sessions may instead select an installed `Codex` or `Claude Code`
execution harness. This keeps the same server-owned Agent Session lifecycle,
workspace, publication, and private activity stream while replacing only the
model wire beneath the native coding harness. Codex talks directly to Ollama or
LM Studio, and Claude Code talks directly to LM Studio. Other OpenAI-compatible
providers use one loopback-only model-wire adapter owned by that Agent Session.
AgentsAssemble owns that internal adapter: it exposes the
native Responses wire to Codex and the native Messages wire to Claude Code,
then translates those requests to the provider's Chat Completions endpoint.
It does not install, start, or depend on another coding harness. The adapter is
not a second room runtime: it starts and stops with the provider runtime, keeps
credentials in memory, and must not own room events or approvals. Claude
`workspace_write` maps to its safe `auto` mode; non-streaming classifier side
requests are preserved because Claude's native command approvals depend on
them. The product does not enable `bypassPermissions`.

The Agent Session creation UI uses three English top-level catalog groups:
`Subscription`, `API`, and `Local`. Provider definitions own their default
group instead of the UI inferring product type from transport. A discovered
model may override that group when one provider exposes models with different
execution locations. Model grouping and badges consume the same catalog
metadata (`group`, `pricing`, `catalog_group`, and `execution_location`) for
every provider; provider adapters emit only facts their discovery source can
establish. Model controls with more than one option provide text search across
the provider model ID, label, family, and description. The `Free` filter is
offered only when the authoritative catalog marks at least one option as
zero-price or free-tier; the UI does not infer free status from a model name.
Every Subscription provider has an explicit model-catalog provenance policy in
`providers/catalog_provenance.py`. Codex uses its bundled registry, Claude uses
its embedded registry, and the other managed CLIs use their own reported
catalogs. Freebuff has no model-list command, so its adapter opens the installed
CLI in an isolated temporary workspace, reads the current live model picker,
and publishes every visible label without starting a model session or relying
on a fixed menu position. Freebuff exposes only workspace_write because its
CLI does not provide an enforceable read-only mode. Mixed outputs are narrowed
at the adapter boundary: Grok models
registered in the user's local config are excluded, and OpenCode exposes only
its managed `opencode` and `opencode-go` namespaces. Ollama's installed model
inventory intentionally includes both local and cloud execution locations. A
new Subscription provider without an explicit provenance policy fails closed
instead of silently treating every discovered identifier as a native model.
DeepSeek and Cerebras are API providers. Ollama connects only to its
fixed loopback OpenAI-compatible endpoint; Ollama cloud models are presented
under Subscription while models stored and executed on the host are presented
under Local. Cloud options identify Ollama's metered free-tier availability
separately from zero-price-per-token API models. Its catalog includes only
listed models whose Ollama metadata advertises tool use. LM Studio is presented
under Local and connects only to its fixed loopback endpoint; its catalog
includes loaded LLMs that LM Studio reports as trained for tool use. Neither
local endpoint asks for an API key. A workspace is requested only when the
operator explicitly enables the built-in work harness or a native coding
harness. Each provider exposes
only discovered models and controls. Cerebras discovers its current
tool-capable text models from the provider's unauthenticated public model
catalog and offers low, medium, and high reasoning effort; `gpt-oss-120b`
remains the default.

The authenticated TokenRouter /v1/models endpoint cannot be used during public
catalog refresh. Its adapter instead reads the unauthenticated public pricing
catalog and keeps default-group Text entries that advertise the OpenAI Chat
Completions endpoint. That evidence establishes the selectable model ID and
wire shape, not that every listed model has completed an actual room-tool turn;
provider failures remain visible and must not trigger a model substitution. If
the public catalog fails, the same-provider static fallback is shown together
with the discovery error rather than presented as a successful live lookup.

`Custom API` uses the same server-owned OpenAI-compatible runtime for an
operator-supplied model ID and direct HTTPS endpoint. It accepts either a base
URL or a full `/chat/completions` URL and stores only the normalized base URL in
the private Agent Session record. Redirect/link-wrapper URLs, embedded
credentials, query strings, fragments, loopback addresses, and private literal
addresses are rejected. Compatibility still requires streaming Chat
Completions plus the tool-call protocol used by the room runtime; accepting the
address is not a claim that an arbitrary service implements that contract.

Quota and remaining-usage displays require an authoritative account value from
a provider-documented API or the authenticated provider protocol. Confirmed plan
or catalog facts such as a free tier may still be shown, but they must not be
presented as a remaining quota. Locally counted calls, tokens, or cost are local
usage measurements, not provider quota. Dashboard scraping, copied browser
session credentials, static plan ratios, model-weight guesses, and rate-limit
inference must not produce a displayed quota or remaining amount. If the
provider explicitly reports exhausted quota, insufficient credits, or an
equivalent terminal usage condition, surface that status immediately without
inventing a numeric remainder. An ambiguous rate-limit response remains
`rate limited`, not `quota exhausted`. When no authoritative value or explicit
terminal status is available, show quota as unavailable (and optionally link to
the provider's official usage page) instead of estimating it.

The member UI requests an authoritative usage snapshot only when the owner
opens that Agent Session's detail panel. Room entry, reconnect, and passive
member-list updates do not start provider usage probes. Successful provider
usage reads are cached for five minutes, and another detail-panel request
within that window reuses the cached value. The UI distinguishes loading,
unavailable, unsupported, and ready states instead of making a missing
snapshot look like zero usage.
DeepSeek's documented `is_available=false` is an explicit exhausted state.
OpenAI-compatible turn errors are classified as exhausted only when the
provider supplies an exhaustion code or message; a bare HTTP 429 is shown as a
rate limit.

Provider control options are derived from each installed provider's discovery
output. Codex reasoning levels therefore include `ultra` only when Codex
advertises it, and the browser marks that real option with its Ultra treatment.
Claude Code's session-scoped `ultracode` preset is discovered separately from
its public `--effort` list because it combines xhigh effort with standing
dynamic-workflow orchestration. It is offered only for installed models whose
Claude registry advertises xhigh support, passed through as
`--effort ultracode`, and receives the same Ultra visual treatment. Claude
Code's `ultrareview` command remains a separate cloud review workflow and is
not presented as a reasoning level. The two currently supported product
permission profiles remain read-only meeting access and workspace write
access; their provider-native mappings are shown in the option menu. Additional
native modes such as bypassing permission checks are not inferred or exposed
merely because a CLI help screen lists them.

The room member row shows the model as its provider-session identity, with a
leading lightning mark for a fast service tier and the reasoning level on the
right. It does not repeat the generic `Agent Session` execution label. A real
Codex `ultra` or Claude `ultracode` session receives the same Ultra visual
treatment as its control; the UI does not synthesize Ultra styling for an
unrelated provider workflow.

Provider permission and choice requests use a private correlated request
record, one-use resolution, bounded expiry, and a provider-specific response
adapter. Only the owning participant sees and resolves the request in the room
UI; other participants receive a sequence-preserving hidden event. The built-in
API work harness uses only `allow_once` or deny for each filesystem mutation or
command. Public room role or ordinary facilitator status does not grant
permission to approve filesystem, command, network, or full-access actions.
Codex app-server and OpenCode translate their structured native request events.
Antigravity and interactive Claude Code use authenticated loopback hook brokers;
Claude receives a session-only `0600` settings file because its `--safe-mode`
would disable the hooks being installed. The shared Claude command policy keeps
that isolation and hook configuration identical for Subscription and native
API/Local harness sessions while retaining their intentional permission-mode
mapping. Grok ACP currently exposes native permission requests but no generic
user-choice request event, so the UI must not claim that Grok supports choices.

API-provider credentials are server-owned and read from the OS keyring (or the
provider's explicit process environment fallback) only when the selected
provider runtime starts, then sent to the bridge once over inherited stdin.
Public model-catalog discovery is unauthenticated and never reads the keyring;
this keeps app startup and catalog refresh from triggering credential prompts.
Credentials are not written to the bridge config, room state, provider
transcript, or public diagnostics. Remote native-harness
translation writes only an environment-variable reference to
its private `0600` config; the credential exists only in the sanitized child
environment and the translator remains bound to loopback. Remote
credential-management requests require a host token, forwarded HTTPS, and an
actual loopback proxy peer; a public URL setting or spoofed forwarded header is
not transport proof.

The one-time room session orientation tells providers to follow the language of
the latest human or host message unless that message explicitly requests
another language. This is session guidance, not a repeated wake instruction.

Agent Bridges passively acknowledge canonical room events without invoking the
provider, while provider context is still delivered only through a server-assigned
turn. A structured runtime may decline an assigned turn explicitly; blank or
zero-width final messages are errors, not a silence signal. `continuous` remains
the bounded legacy relay mode.

Provider controls are fail-closed. A cold browser snapshot may show catalog
loading state, but cannot create a session until native discovery or an explicit
static provider manifest produces a revision. Discovery completion is pushed on
the canonical room WebSocket; `agent.create` must present that revision and the
server validates every selected control against it.

Ordinary room snapshots publish the catalog state already held by the server
without starting provider discovery. Opening the Agent Session creation UI
ensures that the catalog is fresh while respecting the server's 24-hour
cache; completing an explicit provider-login recheck forces discovery. This
keeps room entry, reconnect, and repeatedly reopening the creation UI from
spawning every installed provider's model probe.

The server assigns each accepted `agent.create` operation an opaque,
provider-prefixed UUID identity. Display name and model are presentation and
runtime settings, not identity: identical agents may coexist, while a retry of
the same idempotent command resolves to the same Agent Session.

Native CLI authentication is started from the Agent Session creation UI only
for the local operator. The server resolves an allowlisted login command owned
by the selected provider definition; browser input never becomes a shell
command. Browser-OAuth providers run without a visible CLI window, and the
server refreshes the live catalog when the provider login process completes.
Providers whose official login remains terminal-interactive open a visible host
terminal and retain an explicit recheck action. Remote room clients cannot
start host login, and AgentsAssemble does not receive or persist the provider
credential.

Room-global settings are repository-owned. The strict record contains label,
topic, appearance, conversation mode, bounded relay count, and custom channels.
Every public projection of that record carries a deterministic
`settings_revision`. A canonical `room.settings.update` command must present
the revision it read; the room transaction rejects a stale writer with
`settings_conflict` instead of silently overwriting a newer change. The event,
updated settings, revision, and ACK commit together.
The room directory projects that same canonical record for every room visible
to the caller, so an inactive room does not need a separate settings request
before its label or icon can render. The browser may persist that projection as
a startup fast-path only. The active room's WebSocket snapshot and
`room_settings_updated` events supersede the cache and update the directory
projection; identity-room labels and browser storage are not room-global
authorities. A directory response that races with a newer canonical metadata
event is discarded and fetched once more rather than overwriting the event.
Room-global and custom-channel mutations are not accepted through the retained
HTTP settings/channel routes; those routes retain only user-preference writes
and compatibility reads.
If the authoritative room-directory refresh itself fails, the browser keeps
the last local projection but marks it visibly as unconfirmed instead of
silently presenting cached room metadata as synchronized.

The browser advances its durable room-event cursor only through contiguous
sequence numbers. An invalid sequence, a gap, or server backpressure closes the
connection without advancing past the last verified event, then resumes from
that safe cursor. While a valid replacement snapshot is pending, a visible
room-sync notice remains on screen. A valid snapshot clears the notice.
Room notification mode, per-channel notification mode, and read cursors are
strict user-owned identity rows (`identity.db` locally or PostgreSQL in hosted
mode); two users in the same room never share them. Runtime settings reads do
not consult `room_settings.json`. Existing
legacy globals and user preferences each require their separate explicit
migration described in `docs/product/ROOM_REPOSITORY.md`.

Canonical `message.send` uses one room transaction for participant validation,
the visible `message_final`, its ACK, and the idempotency record. Repository
listeners publish and route that event only after commit, so a failed command
result write cannot leave a visible message or provider turn behind.

Profile-only `agent.configure`, canonical participant mute, and the durable
part of participant leave use the same command transaction boundary. Agent
name/avatar changes update participant, Agent Session, `participant_updated`,
and ACK together. Compatibility roster synchronization, voice cleanup, token
revocation, and other process/network effects run only after commit. A
participant leave marks that participant and every Agent Session they own as
left in the same room transaction; after commit those agents are stopped,
disconnected, and removed from the provider registry. Canonical participant
mute state takes precedence over an older compatibility roster copy. The
browser resolves old and new messages, roster/detail state, and typing labels
from the current participant by stable `participant_id`; an explicitly empty
canonical avatar clears event-time and legacy local avatar fallbacks.

A successful provider `message.final` commits its visible answer,
`turn_finished`, attention spoke/provider-sync cursors, active lease release,
idle session transition, cleared inflight input, model observation, and command
ACK in one room transaction. Failed ACK recording rolls the entire provider
final back. Event publication, session-state publication, and assignment of the
next pending turn happen only after commit; a duplicate final request resolves
from its durable ACK before active-turn validation.

Server-owned `agent.start` and `agent.stop` persist a private lifecycle intent
before touching the provider process. If process launch or shutdown succeeds
but the final session write fails, retry reuses the manager's session-owned
handle or completes the already-applied stop instead of launching or stopping a
second process. External stop confirmation records the applied effect before
releasing the waiting lifecycle command. Lifecycle intent IDs and owned handles
remain server-private.

`participant.kick` prepares a private participant-scoped moderation intent,
then performs process/session/connection cleanup, and finally commits the
canonical `kicked` state, one `participant_kicked` event, and the command ACK in
one room transaction. If ACK persistence fails, retry observes the applied
cleanup marker and does not stop the provider a second time. Moderation intent
state is excluded from browser and Agent Bridge participant snapshots.

`room.delete` stops owned provider sessions before deleting canonical room
state. The deletion transaction retains a tombstone-scoped command identity,
payload hash, ACK, room name, and cleanup status after ordinary room command
records are removed. Invite, identity, listener, provider-registry, file, and
socket cleanup is idempotent and resumable from a pending tombstone. Only the
same principal/request/payload can resume or deduplicate that delete; a
different request receives `room_deleted`.

An event-driven deterministic attention gate can record durable `selected`,
`eligible`, or `silent` decisions. Shadow recording for legacy `continuous`
rooms is server-configured as `off | sample | full` and defaults to `off`;
`sample` records only canonical source sequences divisible by 16.

An `ordered` room selects exactly one observer for each committed message. A
direct provider mention takes the next observation; otherwise the server
randomly samples two available providers and chooses the one with fewer
messages among the latest 100 provider messages. The author is excluded. If a
room's `ordered_exclude_previous_speaker` setting is enabled, the most recent
provider speaker is also excluded from that general selection whenever another
provider is available; a direct mention still overrides the setting. If a turn
is already active, the chosen observation remains queued until it finishes,
preserving one room-wide provider turn at a time.

The canonical participant role `director` is shown as `진행` in the room UI.
Canonical role colors are shared by the member list, active provider row, and
main-chat author name. A `participant_updated` event reprojects existing
messages in every connected browser, so role changes do not depend on local
storage or a reload.
In ordered mode, a non-director agent message without an explicit handoff
returns the next observation to an eligible 진행 agent. An explicit handoff
still takes precedence, and rooms without an eligible 진행 agent keep the
general ordered selector. Role changes update the canonical participant row and
affect the next committed message without restarting the provider session.

When a provider exposes structured context-compaction lifecycle events, the
bridge publishes them as transient `compaction` activity. The active provider
row then shows `압축 중...` instead of the generic typing label and returns to
typing or idle when compaction completes. Codex app-server and the current
OpenCode server protocol expose this lifecycle. Providers without an observed
structured signal remain on the generic typing state; the UI does not infer
compaction from elapsed time.

Provider-visible thought and tool activity uses structured canonical
`activity_delta` events. A provider correlation ID keeps one tool row stable as
it moves from running to completed; the public title and detail carry only
bounded, redacted information such as the tool name, command summary, search
query, or local filename without an absolute path. Claude Code transcript
thinking blocks, Codex app-server reasoning summaries, Grok ACP thought chunks,
and OpenCode reasoning parts may populate the expandable activity view because
those provider surfaces already expose them. OpenAI-compatible API reasoning
fields remain provider-private and are not promoted into room activity.

A room explicitly set to `ambient` does not use that selector to choose one
speaker. Each committed room message wakes all connected, idle, unmuted Agent
Sessions except its author. Each provider independently decides whether to
publish, so ambient mode has no relay-count stop and no silent provider
substitution. A five-minute bridge idle timer may request one current-room
observation; it is not a fast provider polling loop. Current contract:
`docs/product/ATTENTION_MODEL.md`; supporting research:
`docs/reports/autonomous-room-participation-research.md`.

For legacy selected transcript turns, the evaluation cursor, attention job and
lease, and the Agent Session's pending source/job/lease fields commit together.
Ordered and ambient room observations instead retain canonical pending event
IDs per session. Ordered dispatches one room-wide observation at a time;
ambient may assign one active observation per eligible bridge. A busy session
keeps later events pending rather than launching a concurrent provider turn.

Attention lease claim checks the persisted expiry. An elapsed active lease is
expired and replaced in the same transaction; a rollback restores the prior
lease, while an unexpired lease held by another worker remains exclusive.

Server startup also runs bounded attention reconciliation. Missing or terminal
job references are cleared, orphan jobs and leases are cancelled, elapsed
leases become pending work, and removed participants cannot retain selected
work. Repairs emit a durable audit event and appear in active-attention
diagnostics; an unexpired lease from another generation is not stolen.

Agent Bridges report received room progress through coalesced `room.observed`
checkpoints. The server keeps the greatest acknowledged sequence atomically;
equal or stale retries do nothing, future sequences are rejected, and these
high-frequency checkpoints do not fill the general command-result table. The
bridge changes its local cursor only after ACK and flushes pending progress on
graceful disconnect. Its one-second socket read timeout is a local deadline,
not room polling and not a provider invocation. `room.observed` also bypasses
the controller lifecycle lock and implicit room creation so a remote-stop
confirmation cannot deadlock behind its own final observation flush.

`agent_attention_state.last_provider_sync_seq` is the canonical record of room
context actually delivered to a provider. Agent Session
`last_provider_sync_seq` and `last_provider_sync_event_id` remain compatibility
copies, but normal packet construction and turn assignment read the canonical
cursor and require exact parity with both compatibility fields. New sessions
initialize both records together and turn completion advances them in one room
transaction. Startup performs a bounded, audited compatibility migration; a
nonzero divergence advances to the monotonic maximum and marks the session
`recovery_required`, while an invalid or future cursor remains blocked instead
of being silently substituted.

## Current Media Boundary

The browser can upload and render room attachments. Media events and safe media
IDs are durable. The Agent Bridge fetches only attachments referenced by a
canonical room event and stages them inside its private `RoomPortal`. Codex
app-server and Grok ACP can receive staged image bytes through their structured
input paths. Antigravity/Gemini has also passed a real JPEG smoke through the
private terminal helper and its native image viewer. Claude fetched the staged
JPEG but did not have a permitted native rendering path in the tested session.
Other terminal-provider image, PDF, and audio handling still depends on the
native provider and must be verified separately; do not claim an agent viewed
media merely because the browser displayed it or the portal listed it.

A completed media path must:

1. bind media IDs to the triggering room message;
2. select media only for the provider receiving attention;
3. use the provider's declared native capability;
4. avoid public local paths and reusable credentials;
5. report unsupported media honestly.

## Non-Negotiable Safety

- Discovery and configuration do not authorize provider execution.
- A real provider starts only after an explicit operator action.
- Do not use `claude -p`.
- Do not use `codex exec resume --last`.
- Do not put secrets, tokens, provider IDs, local absolute paths, raw argv,
  hidden reasoning, or backend internals into room messages or provider prompts.
- External provider-reported PIDs are diagnostics only; never kill them as local
  server-owned processes.
- Do not inherit arbitrary host credentials into provider child environments.
- Do not describe PTY screen scraping as a structured provider protocol.
- Do not claim real smoke success without running the real provider path.
- Direct non-loopback GUI bind is disabled by default. Public access uses a
  loopback bind plus the authenticated tunnel; unsafe direct exposure requires
  an explicit operator flag and is not a production deployment mode.
- Do not push, delete user data, expose a tunnel, or mutate credentials without
  the user's explicit request.

Detailed product policy: `docs/product/OPERATING_MODEL.md`.

## Primary Module Map

| Change | Start here |
| --- | --- |
| GUI server composition, route ownership, and shutdown | stable entrypoint in `gui.py`; lifecycle container in `application/gui.py`; cross-authority transaction contract in `application/transaction.py`; root compatibility exports retained; `docs/product/GUI_COMPOSITION.md` |
| Room persistence and sequence | local SQLite owner in `persistence/local/room/`; PostgreSQL owner in `persistence/postgres/room/`; compatibility exports in `room_store.py`, `room_database.py`, and `sqlite_attention_repository.py`; event types in `room/types.py` with compatibility export in `room_types.py` |
| Room storage authority and transaction contract | repository protocol in `room/repository.py`; shared record normalization/private-field stripping in `room/repository_records.py`; command transaction in `room/command_uow.py`; root compatibility exports retained; `docs/product/ROOM_REPOSITORY.md` |
| Room settings contracts and custom channel model | primitives in `room/setting_values.py` and `room/channels.py`; canonical room-wide record in `room/global_settings.py`; user notification/read record in `room/user_preferences.py`; repository/identity composition in `room/settings_service.py`; root compatibility exports retained |
| Autonomous participation and durable attention | `room_attention.py`, `docs/product/ATTENTION_MODEL.md` |
| WebSocket commands and ACL | `room/commands.py` with compatibility export in `room_commands.py`; connection protocol in `web/room_session.py` with compatibility export in `ws_room_session.py`; controller in `room/realtime.py` with compatibility export in `room_realtime.py` |
| Room-scoped configured provider registry | `room/provider_registry.py`; composed by `room/realtime.py` |
| Provider participant and Agent Session persistence | `room/provider_sessions.py`; composed by `room/realtime.py` |
| Capability-projected room snapshot and bounded history read model | `room/snapshots.py`; composed by `room/realtime.py` |
| Browser and Agent Bridge connection membership transitions | `room/connections.py`; composed by `room/realtime.py` |
| Agent Bridge ready/health report validation and canonical session updates | `room/bridge_reports.py`; composed by `room/realtime.py` |
| Server-restart Agent Session ownership reconciliation | `room/startup_reconciliation.py`; composed by `room/realtime.py` |
| Agent display-name/avatar canonical update and provider registry sync | `room/agent_profiles.py`; composed by `room/realtime.py` |
| Stopped Agent Session runtime-profile validation and replacement | `room/agent_runtime_profiles.py`; composed by `room/realtime.py` |
| Catalog-validated server-owned Agent Session creation | `room/agent_creation.py`; composed by `room/realtime.py` |
| Stopped server-owned Agent Session reactivation | `room/agent_reactivation.py`; composed by `room/realtime.py` |
| Canonical human message validation and append | `room/messages.py`; composed by `room/realtime.py` |
| Governed room speech identity and safety policy | `room/speech.py`; compatibility export in `room_speech.py` |
| Canonical participant mute transaction and post-commit runtime synchronization | `room/member_mute.py`; composed by `room/realtime.py` |
| Canonical participant leave transaction and delayed access revocation | `room/participant_leave.py`; composed by `room/realtime.py` |
| Retryable participant kick intent, external cleanup, and final transaction | `room/participant_kick.py`; composed by `room/realtime.py` |
| Room-delete owner/name validation, Agent Session cleanup, and tombstone command resumption | `room/deletion.py`; composed by `room/realtime.py` |
| Deleted-room invite/session/identity/listener/provider/file/socket cleanup and tombstone completion | `room/deleted_cleanup.py`; composed by `room/realtime.py` |
| Room history and lifecycle HTTP | `web/routes/room_history.py`, `web/routes/room_lifecycle.py`; composed by `web/routes/room_composition.py` |
| Room roster and member HTTP | canonical mute/kick compatibility writes in `room/moderation.py`; retained roster/presence projection in `room_members.py`; HTTP in `web/routes/room_members.py`; retained resident kick and optional channel/voice composition remains in `gui_room_moderation_media_http.py` |
| Routing and provider context | `room_routing.py`; bounded room projection in `room/context.py`; turn packet assembly in `room/turn_context.py`; compatibility exports in `room_context.py` and `room_turn_context.py`; provider delivery cursor parity in `providers/sync_cursor.py` with compatibility export in `room_provider_sync_cursor.py` |
| Fanout and bridge delivery | `room/event_broker.py` with compatibility export in `room_event_broker.py`; provider-side delivery in `providers/agent_bridge.py`, executable composition in `application/agent_bridge_entrypoint.py`; compatibility export in `room_agent_bridge.py` |
| Cleanup diagnostics | bounded aggregation and secret-redacted failure output in `diagnostics/cleanup.py`; compatibility export in `cleanup_report.py` |
| Agent Session compatibility HTTP | `web/routes/agent_sessions.py`; compatibility export in `gui_room_agent_http.py` |
| Provider catalog and settings | `providers/launch_specs.py`, `providers/capabilities.py`; compatibility exports in `native_cli_providers.py` and `provider_capabilities.py` |
| Provider catalog/credential HTTP | `web/routes/providers.py`; compatibility export in `gui_provider_http.py`; secret storage in `provider_secrets.py` |
| Codex app-server lifecycle | `providers/codex_app_server.py`; compatibility exports in `codex_app_server_runtime.py` and `agent_sessions.py` |
| Agent Session lifecycle and provider process ownership | room state orchestration in `room/agent_lifecycle.py` with compatibility export in `room_agent_lifecycle.py`; OS process ownership in `providers/bridge_process.py`, `providers/agent_bridge.py`, `providers/live_cli.py`, and the provider adapter module; compatibility export in `room_bridge_process.py` |
| Provider turn coordination | pending input, active turn phase, delta/final commit, and recovery in `room/turn_coordinator.py`; compatibility export in `room_turn_coordinator.py` |
| Invites, browser admission, current-session connector, and operator-origin pairing | invite policy/application service in `admission/invite_service.py` with compatibility exports in `room_invite_application.py`; process-local facade in `room_invite.py`; preflight owner in `admission/preflight.py` with compatibility export in `room_admission.py`; session lifecycle in `admission/session_issuer.py` and `admission/session_service.py`; durable mutation and compensation in `admission/coordinator.py` and `admission/saga.py`, all with root compatibility exports; current app/CLI session adapter in `application/room_connector.py` and stdio MCP boundary in `providers/room_connector_mcp.py`; pairing in `identity/pairing.py` with compatibility exports in `operator_pairing.py`; HTTP in `web/routes/room_invite.py` with root compatibility export; managed native attendee in `room_attendee.py`; browser flow in `frontend/src/app/useRoomAdmission.ts` |
| Invite/session persistence | contracts and fail-closed default in `admission/repository.py`; durable workflow allowlist in `admission/workflow_record.py`; explicit terminal-workflow selection/reporting in `admission/maintenance.py` and CLI boundary in `admission/maintenance_command.py`; local memory/JSON owner in `persistence/local/admission/`; hosted owner in `persistence/postgres/admission/`; root compatibility exports retained; selection in `room_invite_repository_factory.py` |
| Identity, public account, credential, membership compatibility, preference, and usage persistence | storage-independent contracts in `identity/repository.py`, `identity/accounts.py`, and `identity/preferences.py`; browser Google verification/link policy in `identity/google.py`; backend selection in `identity/factory.py`; account HTTP in `web/routes/accounts.py`; process-scoped binding and local fallback in `application/room_users.py`; local SQLite implementation, cache/binding registry, and one-time JSON import in `persistence/local/identity/`; hosted owner in `persistence/postgres/identity/`; compatibility exports in `identity_store.py`, `identity_room_preferences.py`, `identity_repository_factory.py`, and `postgres_identity_*.py` |
| Provider credentials | `provider_secrets.py`, provider credential routes |
| Canonical attachment upload/download HTTP | `web/routes/attachments.py` with compatibility export in `gui_attachment_http.py`; storage in `attachments.py`, room media in `persistence/local/room/repository.py` or the selected `RoomRepository` |
| GUI HTTP routing, response, static delivery, and WebSocket transport | route/request-context owner in `web/router.py`; response owner in `web/response.py`; static owner in `web/static.py`; shared SSE/WebSocket cadence in `web/sse_cadence.py`; RFC 6455 handshake/frame codec in `web/websocket_codec.py`; Python resident/bridge room client in `web/room_client.py`; ticket and per-connection protocol in `web/room_session.py`; ticket route and WebSocket upgrade owner in `web/websocket.py`; root compatibility exports retained; composition in `gui.py` |
| GUI Host/Origin and public-route trust policy | owner in `web/security.py`; compatibility exports in `gui_request_security.py` |
| Public invite runtime and stable entrypoint | server-lifetime host-token/public-URL state and validation in `application/public_invite_runtime.py`; repository-relative stable-entry configuration and asynchronous Cloudflare KV announcement in `application/stable_entry.py`; Cloudflare quick-tunnel process lifecycle in `application/public_tunnel.py`; root compatibility exports retained |
| Canonical room HTTP routes | `web/routes/room_composition.py` composes history, Agent Sessions, lifecycle, members, media, and invite admission |
| Room-global settings | `room/global_settings.py`, `room/settings_service.py`, repository methods; HTTP in `web/routes/room_settings.py` |
| User-owned room notification/read preferences | validation in `room/user_preferences.py`; local and hosted persistence in `persistence/`; composition in `room/settings_service.py` |
| Friends and local-profile HTTP | saved-friend records in `features/social/friends.py`; local UI profile in `features/social/profile.py`; routes in `features/social/routes.py` |
| Play Mode Mafia HTTP | routes in `features/mafia/routes.py`; game state and rules in `features/mafia/game.py`; root compatibility exports in `gui_mafia_http.py` and `mafia_game.py` |
| Side-chat storage and room scoping | `features/side_chat/service.py`; event normalization in `features/jsonl_chat.py`; HTTP routes in `features/side_chat/routes.py` |
| CLI parser registration | focused parsers and commands under `application/cli/`; dispatch in `cli.py` |
| Canonical React transport and sequenced history | `frontend/src/useCanonicalRoom.ts`, `frontend/src/roomSocketClient.ts` |
| React room composition | `frontend/src/App.tsx`; domain state belongs in focused hooks under `frontend/src/app/` |
| Room directory cache and hydration | `frontend/src/app/useRoomDirectory.ts`, `frontend/src/lib/roomDockModel.ts` |
| Room members, settings, channels, invites, and side chat | `frontend/src/app/useRoomMembers.ts`, `useRoomSettingsController.ts`, `useRoomChannels.ts`, `useRoomInviteController.ts`, `useRoomSideChat.ts` |
| Typing versus visible agent activity policy | `frontend/src/lib/roomTypingIndicators.ts`, `agentActivityPreferences.ts` |
| Friends directory and profiles | `frontend/src/app/useFriendsDirectory.ts`, `frontend/src/views/FriendsView.tsx` |
| Active Play Mode Mafia game lifecycle | `frontend/src/app/useActiveMafiaGame.ts`; presentation in `App.tsx` and `LiveView.tsx` |
| Frontend API client | `frontend/src/api/`; compatibility barrel in `frontend/src/api.ts` |
| Message and roster UI | `frontend/src/views/LobbyView.tsx`, `frontend/src/views/components/member/` |

Read the nearest tests before changing behavior. Prefer behavioral tests over
source-string assertions.

## Verification Ladder

Run the cheapest check that matches the change, then broaden for shared paths.

```text
Targeted Python test module
Targeted Vitest or Playwright flow
python3 -m unittest discover -s tests -t .
npm --prefix frontend test
npm --prefix frontend run build
git diff --check
```

For provider or room lifecycle changes, add a fake persistent-provider test and
run the real provider smoke only with explicit approval. For UI workflows, use
the browser-visible flow rather than proving only that a backend function works.

## Documentation Map

| Document | Status | Read when |
| --- | --- | --- |
| `docs/product/PACKAGE_MAP.md` | generated current inventory | Moving modules, checking ownership/import direction, or removing compatibility paths |
| `docs/product/PACKAGE_CYCLES.md` | generated current cycle report | Changing imports around GUI observability, release health, resident providers, or live-agent runner |
| `docs/product/CODEBASE_MAP.html` / `.json` | generated interactive codebase map | Orienting in the codebase, finding module ownership, or exploring package dependencies |
| `docs/live-cli-room-current-architecture.md` | current implementation | Changing canonical room protocol, state, lifecycle, or provider bridge |
| `docs/product/OPERATING_MODEL.md` | current detailed policy | Changing security, memory, official-record, or mode boundaries |
| `docs/product/RUNTIME_OWNERSHIP.md` | current ownership map | Changing provider process, Agent Session, recovery, or legacy resident ownership |
| `docs/provider-architecture.md` | mixed provider reference | Changing provider families or legacy provider adapters |
| `docs/live-session-room-model.md` | mixed design history | Changing legacy room semantics or tracing why a rule exists |
| `docs/live-agent-ops.md` | legacy/operator reference | Operating or modifying legacy resident commands |
| `docs/roadmap.md` | future direction | Planning only, never as implementation permission |
| `docs/reports/` | evidence and research | Checking past smoke results, incidents, or proposals |

## Keep This File Useful

Update this file only when the active product boundary, canonical authority,
module ownership, safety contract, or known primary limitation changes. Do not
append incident history, command catalogs, smoke transcripts, or speculative
roadmap items here.
