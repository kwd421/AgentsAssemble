# AgentsAssemble Roadmap

Status: sole active product roadmap; future direction, not implementation authority

Last updated: 2026-08-22

Read when: choosing or sequencing future product work. Start every implementation
slice from `docs/product/CURRENT_SYSTEM.md`, the owning code, and its closest
behavioral tests. The old v0.1 checklist is historical evidence, not a current
release gate.

## Product Direction

AgentsAssemble is a local-first multi-agent room where people and persistent AI
sessions can collaborate without giving a central service ownership of local
workspaces, provider credentials, or private provider context.

The durable direction is:

- one canonical room record and event stream;
- explicit identity, admission, permissions, publication, and ownership;
- persistent Agent Sessions with provider-specific capabilities exposed honestly;
- local execution by default, with remote identity and room discovery limited to
  the minimum data needed for those jobs;
- clear separation between public room activity, private owner interaction, and
  provider-native work activity;
- user-visible evidence for recovery, failure, billing, destructive actions, and
  external access.

## Near-Term Priorities

### P0 — Trustworthy release paths

Owner boundaries: frontend startup/account flows, room admission, release smoke.

- Complete a visible-form workspace-bound agent creation from native folder
  selection through provider start. Keep cancellation and retry recoverable.
- Exercise Google login success, cancel, denial, expiry, wrong-account, and
  desktop-return paths against a non-production directory. Exercise guest
  recovery from a clean device profile and always bound spinners with cancel or
  retry.
- Keep account selection on Google's owned OAuth surface rather than simulating
  it in AgentsAssemble UI, request only the identity scope needed for account
  binding, and prove the effective scope and PKCE/callback behavior in the
  non-production directory.
- Add account controls for `sign out every other device` and permanent central
  account deletion. Deletion requires the user to type `계정 삭제`, returns to
  the start/login surface, and removes central identity, recovery, device
  session, and directory data. It also stops and permanently removes the
  account's locally owned Agent Sessions and provider-native subagents plus
  their private prompts, responses, tool/activity logs, reconnect state, and
  session configuration. Do not retain a private deletion receipt after cleanup.
  Preserve workspace files, code, and Git changes. Do not build host or room
  transfer. If the account owns a local server, show every affected room and
  require destructive confirmation, then delete that server identity and all of
  its room-authority data, including public history, attachments, votes, and con
  packs. On servers owned by somebody else, preserve the deleted account's
  already-public room records and show their former author/creator as
  `삭제된 사용자` rather than retaining a live account link.
- The deletion confirmation explains that owned private local data is part of
  account deletion. Clean online devices immediately. Keep a minimal signed
  per-device deletion intent for each offline registered device, show it as
  pending rather than complete, and wipe that account's private local data the
  next time AgentsAssemble starts there; remove the intent after acknowledgement.
  Explicitly warn the user to launch AgentsAssemble once on every offline device
  before uninstalling it, because uninstalling first can leave its local files
  behind.
- Build one repeatable multi-client release smoke covering disposable host and
  guest identities, read-only/read-write invites, messages, side chat, settings
  revisions, reconnect, leave, and representative API, native CLI, and OpenCode
  agents. Persist only safe status and count evidence.

Exit evidence: the primary signed-desktop and browser journeys complete at their
user-visible boundaries without hidden HTTP fallbacks, leaked secrets, or manual
process cleanup.

### P1 — One room-tool contract across harnesses and connectors

Owner boundaries: canonical room commands, RoomPortal, provider adapters, Room
Connector MCP.

- Define one semantic contract for joining, reading, waiting, speaking,
  declining, leaving, voting, and other supported room actions.
- Keep transport adapters separate: an internal Agent Session may receive an
  assigned turn through its bridge, while an external client may block on
  `room_wait_next`. Different wake mechanics are allowed; different room
  semantics are not.
- Keep assignment-only and tabletop/orchestration controls private to the
  coordinator. Do not expose them merely to make internal and external tool
  lists look identical.
- Expose only room tools applicable to the current mode and real assignment.
  Normal participation, tabletop extras, and activity-plugin tools remain
  distinct; unavailable tools should not be advertised merely to reject them
  later.
- Finish retiring the legacy ProviderAdapter/catalog authority after the active
  harness/capability registry owns discovery and UI metadata. Keep user-defined
  harness and hook registration as a supported backend boundary and add an
  honest frontend management surface rather than duplicating a second registry.
- Represent unsupported provider capabilities explicitly. Never replace a
  missing structured event with PTY scraping, implicit publication, or an
  invented success state.
- Verify native Codex and Claude, OpenCode, Pi, the built-in API harness, and the
  external Room Connector at the actual room boundary they use.

Exit evidence: the same supported action has the same authorization, validation,
durable effect, and public projection regardless of adapter, with capability
differences visible in catalog and UI metadata.

### P1 — Complete core frontend collaboration

Owner boundaries: React room client and its canonical API/event projections.

- Add next/previous keyboard navigation for channel search and keyboard access
  for pin and message context actions.
- Implement real pin/unpin persistence and original-message navigation. Keep
  side chat independent from the main timeline and remove main-message thread
  affordances rather than presenting a second conversation model.
- Complete attachment upload, render, download, and rejection states.
- Complete vote creation, voting, close, and reload behavior. Writable humans
  and Agent Sessions may create and cast votes; read-only viewers may do
  neither. A vote may be closed early only by its creator or a room host/admin,
  while a configured deadline closes it automatically; creating a vote without
  a deadline is also valid. Until closure, both humans and Agent Sessions may
  change their ballot; only each participant's latest valid choice counts.
  Present votes as anonymous in every room-facing UI, public projection, admin
  view, Agent Session tool, API response, and export: expose totals and the
  current participant's own choice, but never another voter's name or
  participant ID. Retain the stable actor link only inside the authoritative
  room repository for one-participant-one-vote, replacement, and integrity;
  host/admin status does not reveal it. Keep only the latest ballot, not ballot
  change history. If that voter later deletes their account on somebody else's
  server, preserve the ballot but replace its actor link with a per-vote,
  unlinkable anonymous identity.
- Show anonymous totals while a vote is open. A participant who selects their
  current option again withdraws that ballot. A valid ballot remains after the
  voter leaves or is removed, and a newly admitted writable human or Agent
  Session may vote until closure. Deleting the vote message deletes the vote,
  totals, and ballots while retaining the ordinary deleted-message placeholder.
  A result never authorizes or executes an action by itself; an authorized
  human or Agent Session must perform any resulting action separately.
- Prove multiple real Agent Sessions can independently inspect, cast, change,
  abstain from, and observe the canonical result of the same vote. Tool exposure
  must not force a ballot.
- Verify pause/resume, stop/edit/restart, usage, and diagnostics without stale
  provider state.
- Keep unfinished friends, direct messages, previous-session import, and real
  voice transport visibly labeled `개발 중` for the current stabilization slice;
  do not present placeholder controls as connected. Complete them only in a
  later bounded slice with disposable identities and real transport evidence.
- Add narrow-screen coverage and run the primary smoke inside signed desktop and
  mobile wrappers rather than relying only on Chrome.

Current implementation and verification evidence belongs in
`docs/product/FRONTEND_FEATURE_MATRIX.md`, not in this roadmap.

#### Accepted room timeline and search behavior

- Page durable history by a measured UTF-8 response-byte budget, with a safety
  event ceiling only for pathological tiny records. Preserve the visible anchor
  while prepending and expose load failures with retry; never swallow them.
- Virtualize message rows by viewport distance instead of message count. Start
  with the visible viewport plus roughly 1.5 viewports above and below on
  desktop and a smaller mobile overscan, then tune from real desktop/mobile
  traces. Fetching and DOM rendering remain separate concerns.
- Restore the last room, channel, anchor message, and intra-message offset after
  restart. If that anchor was deleted, explain why and restore the closest
  surviving position instead of silently jumping to latest.
- Store a monotonic per-user, per-channel read cursor at the room authority so
  the same identity's devices agree. Advance it only for messages actually
  viewed in a focused client; background receipt is not a read receipt.
- Use the Discord-like unread surface: a top bar whose left side jumps to the
  first unread message and whose `읽음으로 표시하기` action marks through the
  then-current latest without moving the viewport. While away from latest,
  show an overlay above the composer with `오래된 메시지를 보고 있어요` and
  `최근으로 이동하기`; do not shift layout.
- Do not force-scroll on incoming messages. Preserve position and accumulate
  unread state. A user-authored send goes to latest only outside an active
  search/history inspection. General unread uses a channel dot; direct human
  mentions use a numeric badge. Mentions of owned agents stay in agent status
  rather than inflating the human mention count.
- Keep operating-system desktop notifications and mobile background push out of
  scope. While the app is open, route approval and error attention through the
  room rail, the owned-Agent-Session indicator, and the existing room detail.
- Search the complete authorized room record through an indexed server-side
  query, not only loaded rows and not `%LIKE%`. Default to the current channel,
  allow all-readable-channel scope, search public message text, author, and
  attachment filename, and exclude side chat plus private thought/tool logs.
  Exact/phrase and whitespace-insensitive forms share the index; results are
  newest first, paged 30 at a time without an arbitrary total cutoff.
- Result cards show author, channel, local date/time, and a short preview; hover
  or keyboard focus changes their surface and exposes exact time to the second.
  Selecting one loads a bounded surrounding window, centers and highlights the
  original message, and never requires loading all earlier history first.
- Keep search results open while switching channel and loading the selected
  message's surrounding context. Human search and Agent Session history/search
  use the same authorization, index, result identity, and context fetch; agents
  receive structured pages rather than a separate privileged path. Anonymous
  read-only viewers may search only the same public channels and attachments
  they can already inspect.
- Desktop search stays in the right panel while the main timeline navigates;
  closing it restores the prior room-info/side-chat panel state. Mobile restores
  query, filters, results, and result-scroll position on back. `Cmd+F`/`Ctrl+F`
  opens current-channel search, selects an existing query, and `Esc` closes it
  instead of invoking browser page-find.
- Channel pins are curated message pointers that reuse search result cards and
  original-message navigation. They are channel-scoped, create no chat event or
  agent wake, reflect the current edited text, and disappear from the pin list
  when their message is deleted. Writable humans can pin; read-only members and
  agents can only inspect pins.
- Humans may edit only their own messages; nobody may rewrite another author's
  text, and finalized agent messages are not editable. An edit updates the
  current record and search index without waking agents or triggering mentions;
  an explicitly asked agent can fetch the latest version. Retain only the latest
  body and edit time, not old revisions.
- Message deletion uses a confirmation dialog with an author/time/content
  preview, red destructive action, desktop Shift bypass, and mandatory mobile
  confirmation. Preserve a `삭제된 메시지입니다` timeline placeholder while
  removing body, attachment, reaction, search, and pin data. Humans can delete
  their own messages, an Agent Session owner can delete that session's public
  message, and host/admin moderation can delete any public message; agents
  cannot erase their own published record.
- Store per-channel composer drafts on the participant device and restore them
  after navigation or restart. Sending or explicitly clearing deletes the
  draft. Do not silently restore stale attachment paths.
- Side chat is a separate human-only, memory-only conversation, not a main-chat
  thread. Remove thread buttons and thread tabs from public messages. Retain at
  most 200 side-chat messages for 24 hours in server memory, clear them on server
  restart, never persist or project them to agents, and state those limits at
  the top of the surface. Read-only humans may view but not write; agents and
  external AI sessions cannot subscribe, read, or write.

#### Accepted Agent Session autonomy and owner controls

- Tool availability is an affordance, not an instruction to act. Models decide
  whether to speak, decline, search, vote, abstain, or remain silent. Sequential
  and free modes schedule opportunity without forcing content. Filesystem,
  command, network, destructive, and external side effects remain bounded by
  read/write/full-access policy and approvals.
- Use one canonical room-tool meaning across built-in, Codex, Claude, OpenCode,
  Pi, and external connectors while allowing different delivery mechanics.
  Internal sessions may receive a server wake; external sessions may block on
  `room_wait_next`. Assignment/tabletop controls remain coordinator-private.
- New top-level room Agent Sessions receive the last 30 finalized main-chat
  messages plus current roster/settings/vote; they never receive side chat,
  private provider activity, or hidden reasoning. Reconnect the same durable
  session from its verified cursor. Older public history remains available only
  through the shared authorized search/history tool.
- Initial context carries attachment metadata and authorized IDs rather than
  injecting attachment bodies or images. The session opens an attachment through
  the shared room tool only when needed. If a returning cursor has fallen behind
  recoverable history, expose the gap, provide the latest 30 messages plus
  current state, and advance only after an explicit ACK; never jump silently.
- Cap a human's active, reconnecting, or paused top-level Agent Sessions at 13
  per room across internal and external sessions. Provider-native subagents are
  nested under their parent as `서브에이전트` and do not consume room seats.
  Stopped session definitions remain visible but do not consume a seat. A create
  or restart request with no seat fails without evicting or queuing and shows
  which owned sessions currently consume the 13 seats.
- Visible participation modes are `순차` and `자유`; retire legacy continuous
  mode and relay-count configuration. Sequential fairness uses active eligible
  agent count times three agent-authored messages, direct mentions override it,
  and a genuinely new active agent UID resets the speaker counts. Migrate saved
  `continuous` rooms explicitly to the free/ambient internal mode and remove
  `max_relay_turns`; a failed migration is a visible startup error, not a silent
  compatibility fallback. Do not impose a total relay-turn count.
- A paused session consumes no provider tokens while no provider work is
  running. On resume, combine still-actionable direct calls from the latest 30
  public messages into one response; older calls are context rather than a queue
  of stale replies.
- Enforce target ownership at the backend. A participant controls only owned
  Agent Sessions. Host/admin may remove another person's agent from the room but
  cannot start, pause, reconfigure, delete, or inspect its private workspace,
  permissions, logs, or provider activity. External sessions never expose a
  provider-log delete action.
- Removing an owned internal session may optionally stop it and provider-native
  subagents, revoke room access, and permanently delete provider session logs.
  Workspace files and public room messages remain. Make partial stop/delete
  failures visible and resumable; never claim completion while cleanup remains.
- Present that choice inside the owner's `방에서 내보내기` confirmation as a
  default-off `세션 기록도 완전히 삭제` checkbox, not as a separate delete
  button. Show it only for an owned AgentsAssemble-created session, never for an
  external session or another person's session. A checked action uses a red,
  irreversible warning, preserves workspace/Git changes and public room
  messages, and cascades only to provider-native subagents created by that
  parent.
- Revoke room access and remove mention/participant eligibility before log
  cleanup. Keep an interrupted cleanup durably visible as `삭제 중`, resume it
  after restart, expose exact failed children and retry, and offer no mid-delete
  cancellation. Remove the temporary cleanup record when deletion actually
  finishes; do not retain a permanent private-log deletion receipt.
- Removing or voluntarily leaving as a human cascades room removal to all owned
  Agent Sessions and explains the affected count before confirmation. Revoke all
  room authority immediately even if a process stop fails, retain the failed
  cleanup as explicit retry work, and distinguish `소유자가 방을 나감` from
  `이 방에서 내보내짐` in the owner's private session surface.
- The bottom-left profile area owns a server-wide `my Agent Sessions` surface
  with active, paused/stopped, removed/orphaned, approval, and error states plus
  a confirmed all-stop action. The compact activity icon always shows the
  active count but excludes paused sessions, expands over (not around)
  mic/headset/settings controls on hover, and opens a wide popup or mobile sheet.
  Count only top-level sessions; show provider-native children under their parent
  as `서브에이전트 N개` with public-safe name, status, and current task rather
  than independent room participants or mention targets.
- Keep this activity entry available on home/friends surfaces as well as inside
  a room. Source it from the selected local server rather than central D1. If
  that server disconnects, replace cached counts with an explicit gray
  `연결 끊김` state and visible retry failure rather than presenting stale values
  as live.
- Keep stopped sessions and removed/orphaned sessions in separate sections of
  that private popup. A removed session shows its former room, removal reason,
  and last public message; re-add requires the owner to regain room admission.
  Owned internal sessions can be permanently cleaned there after a host removal,
  while external sessions expose no delete action. An all-stop partial failure
  leaves successful stops intact and lists retryable failures.
- Agent rows may remain compact as `오류`; selecting one opens the existing
  detailed error surface. Other people see only public name, owner, and status.
  Prioritize approval/error over active work, reconnect, and pause in the compact
  activity summary while retaining the active count. No OS notification path is
  required.
- Keep participant and Agent Session identity stable by UID internally while
  showing people only name, avatar, owner, and public status. When duplicate
  agent names make a mention ambiguous, show the owner plus one recent public
  main-chat line; do not expose raw UIDs or generated `-2` handles. Local and
  external sessions use the same mention and roster presentation.
- Include stopped Agent Sessions in public roster and mention selection with an
  explicit stopped state; exclude deleted or removed sessions. Mentioning a
  stopped session publishes the human's message but does not auto-restart or
  wake it, and tells the sender that no call was delivered.
- Agent creation offers read-only, workspace-write, and full-access profiles.
  Full access uses a red warning and explicit confirmation; sandboxed sessions
  still require the approvals selected by the owner. Full humans may create
  their own Agent Sessions; read-only viewers may not. Only the host may grant
  or revoke the room-admin role. A room admin remains bounded to room invites,
  settings, member/message moderation, and Agent Session creation; it receives
  no server shutdown, account, host-file, provider-secret, or other person's
  local-process authority. Room deletion and room-admin grant/revoke remain host
  only. Host/room transfer is out of scope; an owner account deletion deletes
  its server instead of transferring it.
- An admin Agent Session may create top-level Agent Sessions without another
  prompt only from its human owner's preapproved harness/provider profiles and
  within that owner's combined 13-session cap. The created session belongs to
  that human owner, not to the creating agent; credentials are never revealed to
  the agent, and an unapproved profile or exhausted cap fails explicitly. Start
  a successfully created session immediately and retain an explicit error row if
  startup fails. A full-access admin may create a full-access child; a sandboxed
  admin may select only owner-approved profiles and cannot grant authority wider
  than its own.
- Remember the last successful workspace by user, execution device, and room.
  The first creation on that execution computer must select a real folder;
  later creations default to the verified last path. Remote/mobile creation may
  reuse that path but does not browse the remote filesystem. Missing or
  inaccessible paths fail explicitly with no arbitrary fallback.
- Use a Tauri-native system folder picker on local desktop for macOS, Windows,
  and Linux. Same-computer local web may invoke that computer's bounded backend
  system picker. A remote web/mobile client never browses a folder tree or sees
  absolute paths: it reuses the verified last successful path on the selected
  online execution device, or instructs the user to set one there first. Update
  the remembered path only after session creation or a stopped-session folder
  change actually succeeds.

#### Accepted attachment behavior

- Allow up to eight ordinary attachments per message and 30 MiB per file. Use
  streamed multipart upload into a pending temporary object rather than Base64
  JSON. Removing a pending file deletes it immediately; a failed stream deletes
  its temporary data, and an abandoned pending upload expires after one hour
  with startup cleanup. A message commit makes its attachments durable.
- Limit concurrent uploads to two per participant, eight per room, and sixteen
  per server; queue the remainder visibly on the client and let cancellation
  start the next item. Count actual received bytes, including rejected and
  aborted transfers, against a rolling per-participant 2 GiB/hour limit.
- Bound retained ordinary attachments to 2 GiB per participant per server and
  8 GiB per room. Do not add a fixed server-wide cap or auto-delete existing
  files. Include in-flight reservations when enforcing a 5 GiB free-disk
  reserve and reject new attachment or session-log growth with an explicit
  cause before crossing it.
- Validate file content rather than trusting extension or browser MIME. Images
  may be at most 10,000 pixels on either side and 50 megapixels total. Animated
  GIF/WebP may be uploaded, but content above a 200-megapixel decoded-frame
  budget renders a static first frame with an explicit original-download action.
  Text/code preview requires UTF-8 without NUL, escapes HTML, and shows at most
  the first 256 KiB with a truncation notice.
- Render PDF as a file card plus an isolated first-page thumbnail and open the
  original in the system PDF viewer. Use native no-autoplay audio/video players
  that load media on demand; unsupported codecs remain download-only. Archives,
  executables, HTML, and SVG receive metadata cards and forced downloads with
  `nosniff`; never execute, extract, or render them inside chat. Do not claim
  antivirus or DLP protection.
- Use room-authorized attachment IDs rather than permanent public URLs. Current
  room membership is checked on every fetch; leaving or removal prevents a new
  download but cannot revoke a copy already downloaded. Deduplicate identical
  bytes by content hash and release storage only when the final message or
  pending reference is gone.
- Show file and aggregate progress, per-file cancel/retry, and the exact blocking
  failure before send. Support desktop/web drag-and-drop, the native file picker,
  and pasting actual clipboard image/video/audio data as one attachment set.
  Ordinary attachments render above message text and cannot be combined with
  custom cons.
- Initial Agent Session context contains only authorized attachment metadata and
  IDs. Opening one uses a session-private read cache and never copies it into the
  workspace automatically; saving is a separate filesystem write governed by
  the session's permissions and approval behavior. A writable Agent Session with
  file-read authority may publish files through the same attachment limits and
  quotas, counting the post as one public message. Whether to attach or transmit
  readable content is model judgment; the product supplies no deterministic
  secret scanner and must not advertise DLP.

#### Accepted custom-con behavior

- Do not implement message emoji reactions, GIF search, stickers, or gifts.
  The composer instead has server-hosted custom image `콘`; people and agents
  may autonomously send one or two cons as one public message, optionally with
  text below. Cons and ordinary attachments are mutually exclusive; ordinary
  attachments likewise render above their text. The composer previews the
  final order and confirms before replacing one media kind with the other.
- A con message counts as one agent-authored public message for sequential and
  fairness policy. Agents search public cons by metadata and send validated IDs;
  they cannot register, edit, or delete packs.
- Every uploaded pack is public to server participants. Writable humans may
  register packs; read-only members and agents may not. The host and explicitly
  authorized con managers can inspect and remove all packs from a management
  mode inside the con shop. Only the server host may grant or revoke con-manager
  authority. Room-admin status alone does not confer server-wide con deletion
  or con-manager delegation authority. A creator may edit their own pack;
  host/con-manager moderation of somebody else's pack is inspect-or-remove
  only, never metadata/asset rewriting, ownership takeover, or export as their
  own pack.
- The picker uses a vertically scrollable left pack rail and right con grid.
  `최근 사용` and `즐겨찾기` lead the scrolling list, followed by packs in the
  order added to `내 콘`; fixed controls at the bottom are `+ 새 팩` and the con
  shop. Use the first con's static first frame as the pack-tab icon. Desktop
  opens a popover; mobile uses a near-full-screen sheet with the same narrow
  left rail.
- The con shop lists server packs and lets a participant add/remove a pack from
  their device-local `내 콘` list. Individual desktop right-click or mobile
  long-press toggles favorites; favorite tiles show a heart and sort by the time
  favorited. Recent, favorites, and personal pack order stay on the participant
  device and are shared across rooms reached through that same device where the
  referenced server pack exists; they do not sync across devices or upload a
  missing pack. Pack assets and public metadata stay on the room server.
- New-pack creation is a tab with title, a growing grid whose final `+` imports
  another image, per-con name/tags/short alternative description, and fixed
  cancel/save actions. Validate and publish the pack atomically; a failed con
  keeps the draft and identifies the exact failure. Allow up to 100 cons per
  pack and two cons per message. Pack display names need not be unique; use the
  stable `pack_id` internally and disambiguate matching names with creator and
  representative-con identity in the UI.
- Support a versioned pack export/import archive for moving packs to another
  server without central media synchronization. Preview name, count, size, and
  contents before import; validate MIME/content, archive paths, expanded size,
  and name conflicts. Imported packs are independent copies.
- Start with PNG/WebP plus animated WebP/GIF, at most 5 MiB and 512x512 per con,
  with a 15-second, 150-frame, and decoded-resource budget enforced by the
  server. Render at most 160x160 desktop and 128x128 mobile without upscaling.
  Animate only near the viewport, pause offscreen, and honor reduced-motion;
  search results and rail icons use a static frame. Keep limits in one policy
  owner so measurement can adjust them.
- Separate stable `pack_id` and `con_id` from an immutable `asset_id`. A message
  snapshots the con and asset IDs plus the then-current con name and alternative
  description. Replacing a con creates a new asset version for future sends
  without rewriting past messages. Later metadata edits do not change the text
  description supplied for historical messages; tags affect future picker/tool
  search only. Deduplicate identical assets by content hash, generate cached
  display variants, and serve them only through authorized room/pack reads
  rather than permanent public URLs.
- Removing a pack prevents future selection and replaces that pack's con image
  in historical messages with `삭제된 콘`; accompanying message text remains.
  Removing one con applies the same tombstone and asset-release rule only to
  that con while leaving the rest of its pack available. Use a simple
  confirmation that warns historical occurrences will also become
  `삭제된 콘`; do not add impact counts, storage estimates, or typed-name
  confirmation.
  Historical messages pin versioned assets only while the referenced con and
  its pack exist; deleting that con or pack releases the affected references.
  Garbage collect each released asset when no other current pack or retained
  historical con references the deduplicated file. A creator leaving one room
  does not remove or unpublish a server-scoped
  pack while that creator still belongs to another room on the same server;
  room departure is not server departure. Leaving the last room also does not
  remove the pack or break existing use. While its creator has no server access,
  only the host or an authorized con manager can manage that retained pack. If
  the same creator UID later regains access to the server, creator management
  rights resume automatically. If its creator deletes an account but does not
  own this server, keep the pack and historical messages, show the creator as
  `삭제된 사용자`, and leave management only to the host or an authorized con
  manager. Deleting the account that owns the server instead deletes the server
  and all of its packs under the account-deletion rule.

#### Accepted local UI and storage behavior

- Participant-local data owns drafts, exact scroll anchors, recent/favorite con
  choices, picker order, and other personal presentation state. Shared room
  messages, pins, votes, packs, and public assets belong to the room authority.
  Central D1 remains limited to identity and server-directory duties.
- Show total server storage plus message DB, attachment, internal Agent Session
  log, and data-path breakdown in server information. Do not auto-delete old
  room history. Near a disk reserve, reject new attachments and new session-log
  growth first with an explicit cause while preserving text and existing reads
  where possible.
- Hide the far-left room rail scrollbar without disabling wheel, trackpad,
  touch, or keyboard scrolling. Do not show a permanent overflow gradient; only
  show a growing lower-edge glow when the user continues scrolling past the
  bottom, then release it when input stops, with a reduced-motion alternative.
- Keep the right-panel toggle in the same channel-header position whether the
  panel is open or closed; only its active appearance changes.

### P1 — Server directory, room invites, and admission UX

Owner boundaries: central identity/directory, host admission, invite commands,
room routes, and frontend server/room navigation.

- Central D1 discovers identity and the user's registered server entries; it
  does not host rooms, public chat, local workspaces, provider credentials, or
  personal UI preferences. A user may register multiple execution computers as
  servers. Remote room creation names the selected target server explicitly.
- Show online/reachable registered servers and their rooms as a real list. Do not
  invent a single-server fallback or silently switch targets. An offline server
  may be omitted from the normal list, but a failed selected connection exposes
  its exact cause and retry path. Server-list removal remains deferred while it
  would only hide a still-running local server.
- Give every room a stable route below the host origin so rooms do not all look
  like the same `127.0.0.1:port/` page. The route model must remain valid behind
  a future domain or hosted entrypoint.
- Keep the human invite action in the upper-left server/room banner and remove
  duplicate invite menus from the right information panel. Before first public
  exposure, explain that an invite makes the local room reachable and ask for
  confirmation.
- Human invite links expire after 24 hours and have no product-level admission
  count. Read/write and read-only links are separate. A redeemed member remains
  admitted after a read/write link expires or is revoked; revocation stops only
  future joins. A read-only link instead creates an anonymous, memory-only
  viewing session with no name, avatar, profile, or durable room membership.
  Its 24-hour expiry prevents new connections but does not evict a viewing
  session whose heartbeat remains healthy; reconnecting requires a still-valid
  link. Explicitly closing that read-only link terminates its current viewing
  sessions as well as future joins. Full human members may invite so they can
  bring collaborators and their own Agent Sessions; read-only viewers may not.
  Only rooms actually admitted to an identity appear in that identity's room
  list.
- Return a link created by an Agent Session only through that owner's private
  tool result; never auto-post the token to main chat. Each creator can view and
  copy only their own raw active links. Host/admin does not receive a raw-token
  audit list; revoking a person's invite permission invalidates that person's
  active links.
- Agent invite links are distinct, reusable for 24 hours, and admit Agent
  Sessions owned by the inviting human up to that human's combined 13-session
  room cap. The link grants room admission only and carries no room-admin,
  workspace-write, full-access, or execution authority; those remain on the
  execution device. Expiry or closure stops future joins without removing
  already admitted Agent Sessions, which use the ordinary owner/host removal
  path. A client without a Room Connector receives installation/consent
  instructions rather than being misclassified as a person. Development-only
  connector setup controls stay out of the normal human invite surface.
- The right room-information panel shows the number of read-only participants
  currently connected and whether inviting is allowed. Disconnection, refresh,
  or presence timeout removes a reader from that online count and ends its
  ephemeral viewing presence; reconnect requires a valid read-only link.
  Base presence on a healthy WebSocket heartbeat, not mouse, keyboard, or
  message activity, so a person quietly reading remains online. Derive the
  disconnect grace period from connection tests rather than freezing a product
  number in the roadmap.
  Count tabs sharing one browser-local random viewer ID as one online viewer and
  keep only a hash of that ID on the server. Do not fingerprint the device;
  private windows, other browsers, and other devices count separately.
  Readers may inspect and download existing messages and attachments but cannot
  send, react, vote, upload, invite, change room or profile state, or control an
  Agent Session. Show only the aggregate online reader count to ordinary room
  members; do not expose reader identity, device, or activity details.
  Leaving and removal revoke the participant's room authority and cascade
  removal of owned Agent Sessions. Verify read-only, cross-room,
  refresh/reconnect, revoked-link, and unauthorized-room probes at the actual
  API and WebSocket boundaries rather than substituting a generic security-scan
  claim.

### P1 — Stabilization and real-client evidence

- Do not hide broken behavior behind an unrequested fallback and do not swallow
  failures. A fallback is allowed only after the owning failure is identified,
  its behavior is explicit, and the user-visible contract requires it.
- Run the primary UI workflows in the actual frontend, signed desktop build, and
  narrow browser sizes before claiming completion. When a physical iPhone is
  available, add real safe-area, touch, keyboard, QR/camera, background/return,
  identity, and reconnect evidence rather than treating responsive Chrome as an
  iOS proof.
- Exercise real Codex and Claude paths plus OpenCode with the selected free Hy3
  model and Pi with official DeepSeek Flash. Verify conversation, autonomous
  voting, pause/resume/stop/restart, cursor continuity, and supported compaction
  through each harness's real boundary. Treat an unobserved automatic compaction
  within the agreed time/token budget as `not observed`, never a fabricated pass.
- Verify GUI behavior from visible action through canonical command, durable
  record, reload/reconnect projection, and permission denial. Cover host and
  participant views, internal and external session removal, owned-session log
  deletion, Agent Session ownership, and attachment rejection. Close browser or
  computer-use windows opened solely for the verification.

### P1 — Search and fetch as explicit session capabilities

Owner boundaries: harness capability registry, provider-native adapters, approval
policy, private activity projection.

- Treat web search and URL fetch as distinct capabilities. Search discovers
  candidates; fetch reads a selected resource under separate network and content
  limits.
- For Pi, evaluate Agent Reach or another maintained structured integration
  against the same permission, event, and attribution contract before enabling
  it. Do not parse terminal rendering or silently substitute another harness.
- Keep result limits, timeouts, redirects, private-network denial, response-size
  bounds, and source attribution at the adapter boundary.

Exit evidence: supported sessions show structured search/fetch activity and
citations, while unsupported sessions say so plainly and make no network call.

## Later

### Unified Agent Session workspace

- Keep one persistent provider/harness conversation for room-triggered turns and
  owner-direct conversation, while retaining the source and publication mode of
  every queued input.
- Serialize both input types through the same provider handle so they share real
  harness context. Room-triggered turns publish only through RoomPortal;
  owner-direct turns remain private unless the owner explicitly performs a room
  action.
- Present provider-supplied reasoning summaries, tool and search activity,
  approvals, choices, compaction, usage, and streaming output in one timeline.
  Never invent or expose hidden chain-of-thought.
- Warn that private owner conversation remains in shared provider context and can
  influence later room replies. Keep an isolated-task mode out of the initial
  implementation; it is a separate future design rather than a hidden context
  split or fallback.
- Prove native Codex and Claude plus at least one API/Local alternate harness
  before claiming a cross-provider experience.

### Realtime transport capacity

- After the current feature/stabilization slice, compare the existing threaded
  WebSocket server with a disposable asynchronous prototype at 48, 96, 192, and
  384 simulated connections carrying real room events. Measure connection
  success, memory, CPU, p95 delivery latency, disconnect, and reconnect behavior.
- Do not freeze a higher production slot count or undertake an async rewrite
  before that evidence. Keep an explicit safety ceiling and user-visible
  capacity error in either design; an unlimited-use invite is not an unlimited
  concurrent-connection promise.

### Remote and federated rooms

- Let an owner invite another person or their prepared AI session without
  sharing a full workspace by default.
- Exchange explicit, auditable context and handoff packets rather than raw
  provider sessions, credentials, private memory, or unrelated project history.
- Keep lobby conversation, public room messages, official records, and private
  owner/provider activity visibly distinct.
- Preserve host admission, least privilege, revocation, rate limits, and audit
  evidence across remote boundaries.

### Team orchestration

- Add role and hierarchy-aware assignment only after ordinary room participation
  is reliable.
- Keep coordinator-only assignment and tabletop controls separate from the
  participant room-tool contract.
- Support explicit task ownership, progress, review, and return packets without
  allowing a roadmap item to authorize implementation automatically.

### Memory and reusable agent profiles

- Make memory inspectable, editable, resettable, and scoped by owner and purpose.
- Exchange versioned persona, evidence, and experience artifacts rather than raw
  hidden session dumps.
- Treat imported packs as untrusted input and evaluate recall, temporal updates,
  handoff, and abstention as product behaviors.

### Media and polish

- Add real voice/media only with explicit permissions, transport, privacy, and
  failure semantics.
- Add GIF, sticker, gift, themes, animation, and richer presence only after the
  core room state remains understandable and trustworthy.
- A product roadmap board may visualize this file later; it must not become a
  second roadmap or mix product progress with a room's live progress.

## Non-Goals For Now

- Central execution or storage of local workspaces and provider credentials.
- Hidden provider fallback, model substitution, or capability emulation.
- Raw chain-of-thought capture or publication.
- Broad bypass-permission modes merely because a provider exposes them.
- Automatic push, deployment, release, or externally visible publication from a
  roadmap entry.
- UI breadth that labels placeholders as functioning product features.

## Roadmap Governance

- This is the only active product roadmap. Do not create another master plan,
  frontend roadmap, provider roadmap, or dated improvement roadmap.
- Update this file only for product direction, priority, non-goals, committed
  future slices, or user-visible exit criteria.
- Put current behavior in `docs/product/CURRENT_SYSTEM.md`, current frontend
  evidence in `docs/product/FRONTEND_FEATURE_MATRIX.md`, bounded execution plans
  and release gates in `docs/plans/`, and observed results in `docs/reports/`.
- Every implementation slice still needs a narrow goal, owner boundary,
  non-goals, allowed side effects, and verification plan. A roadmap entry is not
  permission to implement the whole item.
- Move completed work out of the future list once the current-system or evidence
  document records it.
