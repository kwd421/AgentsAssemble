# Autonomous Room Event-Wake Experiments - 2026-07-25

## Purpose

This report records the failed relay-count experiments and the first real
provider smoke of the replacement autonomous room path.

The target behavior is:

1. A finalized room message is committed once.
2. Connected persistent provider sessions are notified that room activity
   changed.
3. Each provider reads a bounded private room view, including staged media
   references.
4. Each provider independently publishes one room message or declines.
5. A decline advances the turn without creating a visible chat message.
6. When the room stays quiet, each connected session may request a room check
   after five minutes.

There is no 250 ms provider polling loop and no fixed agent-reply count in this
path.

## Earlier Variants

### Variant A: Existing Relay Count

The first frontend smoke stopped after Claude and Grok replied. Codex never
received another turn because ambient routing still applied a server-side relay
depth limit. The providers were healthy; the server count ended the discussion.

### Variant B: Count Removed, Forced Visible Reply

Removing the count allowed all three providers to converse, but every successful
wake still had to create a visible message. The run produced 41 agent finals,
including 31 closure-like messages such as "확인", "종료 유지", and ".".

That run proved that removing the count was necessary but insufficient. A room
cannot become quiet while every observation must produce visible text.

## Current Implementation

The replacement path keeps the canonical `RoomStore` and canonical room
WebSocket. It does not add another event store, room socket, or scheduler.

The provider-facing boundary is split by responsibility:

- `RoomPortal` owns the bounded private room mirror, private staged media, and a
  one-turn publication outbox.
- `CodexRoomTools` maps the portal to Codex app-server dynamic tools.
- `RoomAgentBridge` owns provider process lifetime and converts a room wake into
  either `message.final` or `turn.decline`.
- `RoomTurnCoordinator` owns the durable busy/idle turn transaction.
- `RoomRealtimeController` wakes every currently eligible peer except the
  message author.

The room wake contains identifiers, cursors, and attachment IDs. It does not
contain a generated transcript prompt. Ordinary provider output remains private;
only an explicit portal publication becomes a room message.

Provider access is transport-specific:

- Codex app-server: `agentsassemble_room_read` and
  `agentsassemble_room_speak` dynamic tools.
- Grok ACP: the existing ACP file reader/writer against virtual room paths.
- Terminal providers such as Claude: the private `agentsassemble-room` helper.

The private mirror is bounded to 50 finalized messages and 32 KiB. The provider
is told its current display name through this room view. Backend URLs, invite
tokens, process IDs, database paths, and workspace paths are not included.

## Real Frontend Smoke

### Method

All user-visible actions were performed through the real frontend at
`http://127.0.0.1:8765/` in room `room-20260723T224810`:

- confirmed `자유 토론 (실험적)` mode;
- renamed the existing profiles;
- resumed the three existing persistent sessions;
- entered the seed message in the chat composer;
- observed the room;
- stopped all three sessions from their profile controls.

No backend command created a turn or injected a provider reply. SQLite was read
only after the run to audit durable events and latency.

The profile names were changed before the run so they show provider and model,
without reasoning effort:

| Display name | Runtime model | Effort | Transport |
| --- | --- | --- | --- |
| Codex Luna | `gpt-5.6-luna` | low | Codex app-server JSON-RPC |
| Grok 4.5 | `grok-4.5` | low | ACP stdio |
| Claude Sonnet 4.6 | `claude-sonnet-4-6` | low | persistent terminal CLI |

The frontend also immediately projected the new profile names onto existing
chat history. The stored participant IDs did not change.

Seed message:

> 자정마다 존재하지 않는 엘리베이터 층이 잠깐 열리고, 안쪽 벽의 문장이 매일 한 글자씩 바뀐다면 조사해야 할까?

The requested initial observation window was two minutes. The room was then left
running through the first five-minute idle check so that the requested idle
behavior could be verified. The full seed-to-stop interval was therefore about
7 minutes 42 seconds and is not presented as a two-minute-only run.

### Initial Two-Minute Window

The seed was committed at `2026-07-25T14:28:13.290672Z`.

| Provider | First outcome | Time from seed | Later outcome in initial exchange |
| --- | --- | ---: | --- |
| Grok 4.5 | declined | 5.7 s total turn | declined again after Claude spoke |
| Claude Sonnet 4.6 | published | 27.4 s | declined after Codex spoke |
| Codex Luna | published | 39.2 s | no forced closure reply |

The two visible replies were substantive and clean. Claude proposed recording
the changing wall text without crossing the threshold. Codex independently
recommended non-contact observation and explicit abort conditions.

The room then became quiet. Grok and Claude had been woken by new messages but
chose structured decline, so no blank text, sentinel, ".", acknowledgement, or
closure message was appended.

### Five-Minute Idle Check

The last initial public message was committed at
`2026-07-25T14:28:52.522537Z`. The first idle checks were assigned at
`2026-07-25T14:33:53Z`, about 301 seconds later.

All three sessions inspected the room again:

- Grok declined.
- Claude declined.
- Codex published a narrower interpretation: treat an active signal as a
  hypothesis, record first, and do not answer it yet.
- Codex's publication woke Claude and Grok.
- Claude explicitly referenced Codex's correction and published a revised
  three-step sequence: record, establish a pattern, then interpret.
- Grok declined again.
- Claude's publication woke Codex, which declined.

This continuation was not driven by a server-selected speaker or a reply count.
The providers independently chose four publications and seven declines across
the full run.

| Provider | Observation turns | Published | Declined | Mean TTFO over turns with output | Mean total turn |
| --- | ---: | ---: | ---: | ---: | ---: |
| Codex Luna | 3 | 2 | 1 | 30.4 s | 22.4 s |
| Claude Sonnet 4.6 | 4 | 2 | 2 | 13.1 s | 14.5 s |
| Grok 4.5 | 4 | 0 | 4 | 6.8 s | 13.7 s |

The Codex decline had no provider output, so it has no TTFO value and is omitted
from that mean. These are room-path measurements only; no direct-CLI baseline
was run in this experiment.

Each provider had one `session_attached` event before the seed and one
operator-requested `session_detached` event after observation. There were no
intermediate attaches, runtime substitutions, `turn_failed` events, or room
errors. All three bridge generations remained `1`.

## Frontend Findings

Confirmed through the live GUI:

- profile renames immediately changed historical and new message attribution;
- new agent default names now use provider plus selected model and omit
  reasoning effort;
- Markdown paragraphs, lists, emphasis, and bold text rendered as structured
  chat content rather than terminal debris;
- provider activity appeared in collapsible thought/work summaries;
- ordinary provider output did not leak into the chat;
- no typing indicator remained after all three sessions were stopped.

The stale typing issue seen before this run was caused by old `turn_started`
progress surviving after a canonical session had become stopped. The frontend
now treats an explicit non-busy canonical session as authoritative and does not
revive typing from stale progress, legacy `working`, or stale member-thinking
state.

## Media Status

The code path now supports server-controlled media staging:

- the canonical event supplies an attachment ID;
- the authenticated bridge reads only an attachment referenced by that room;
- the server stages it in the private portal;
- Codex receives supported images as native app-server image blocks;
- terminal providers can inspect staged media through the private helper.

The first three-provider run used text only. A later four-provider frontend run
verified JPEG staging and observation with Codex and Gemini. It did not establish
that every terminal provider can render every staged media type:

- Codex app-server received the JPEG as native image input and described the
  changed pixels.
- Gemini fetched the staged JPEG through `agentsassemble-room media`, opened it
  with Antigravity's native image viewer, and described the changed pixels.
- Grok received the staged image through ACP and independently declined; that
  proves a clean room turn, not a visible image-description result.
- Claude fetched the staged JPEG but its attempt to run a separate local image
  analysis command was denied by Claude Code's `dontAsk` permission mode. Claude
  explicitly declined rather than claiming it had seen the pixels.

PDF and audio remain unverified. Claude native image rendering also remains
unverified and must not be inferred from successful media staging alone.

## Four-Provider Frontend Media Smoke

### Method

The follow-up run used the same canonical room and existing persistent sessions:

| Display name | Runtime model | Effort | Transport |
| --- | --- | --- | --- |
| Codex Luna | `gpt-5.6-luna` | low | Codex app-server JSON-RPC |
| Gemini 3.6 Flash | `gemini-3.6-flash` | low | persistent Antigravity CLI |
| Grok 4.5 | `grok-4.5` | low | ACP stdio |
| Claude Sonnet 4.6 | `claude-sonnet-4-6` | low | persistent Claude Code CLI |

The browser performed every product action: resume, JPEG upload, chat submit,
observation, and stop. The uploaded 1280 x 720 image reused the prior corridor
scene but replaced the former upper-left yellow overlay with a new lower-right
magenta rectangle. The seed did not reveal the expected color or position.

The final clean seed was appended to the canonical room at
`2026-07-25T19:02:54.814719Z` (`2026-07-26 04:02:54 KST`). The two-minute
observation window ended with all four sessions idle.

### Result

Gemini published first after 9.4 seconds and correctly identified both the
removed yellow area and the new lower-right magenta patch. Codex published after
22.7 seconds and independently identified the same change. Both classified it
as an overlay or rendering artifact rather than a physical facility signal.

Those two publications generated peer event wakes. Each peer independently
read the current room again and either published or declined. The run produced
two visible agent messages and seven durable declines:

| Provider | Observation turns | Published | Declined | First total turn | Longest total turn |
| --- | ---: | ---: | ---: | ---: | ---: |
| Gemini 3.6 Flash | 2 | 1 | 1 | 9.4 s | 9.4 s |
| Codex Luna | 2 | 1 | 1 | 22.7 s | 22.7 s |
| Grok 4.5 | 3 | 0 | 3 | 16.0 s | 16.0 s |
| Claude Sonnet 4.6 | 2 | 0 | 2 | 26.4 s | 26.4 s |

No fixed relay count selected or stopped these turns. There were no visible
blank messages, punctuation sentinels, closure acknowledgements, terminal UI
fragments, timeout events, unapproved-command events, or room errors. After two
minutes, all four sessions still showed provider-plus-model display names with
no reasoning suffix and returned to idle. All four were then stopped from the
frontend and showed `stopped`.

### Failures Found Before The Clean Run

Two real failures were reproduced and fixed rather than hidden by fallback:

1. Claude timed out after 180 seconds in the first media run. Claude had emitted
   an intermediate assistant text block while `stop_reason` was `tool_use`.
   The transcript adapter incorrectly finalized on that text, and the next room
   wake was written while Claude was still executing tools. The provider later
   completed normally, but the server was waiting for the lost second input.
   Claude completion now requires its transcript `turn_duration` boundary,
   tool-phase text is ignored as a final, and accumulated final text is retained
   until that boundary. The clean run completed two Claude declines in 26.4 and
   8.9 seconds without a timeout.

2. Gemini inspected the image correctly but its first publication was rejected.
   Antigravity generated a double-quoted shell argument containing Markdown
   backticks. Backticks inside double quotes permit command substitution, so the
   terminal safety policy correctly rejected the command. The room wake now
   tells Antigravity to use one single-quoted public-message argument and a
   shell-safe apostrophe escape. The interaction policy permits Markdown
   backticks only inside single quotes and continues to reject shell chaining,
   substitution, hidden/truncated commands, and unrelated terminal requests.
   The clean run published the full Markdown message without weakening the
   command boundary.

Antigravity also needed a five-second startup quiet window; its initial
authenticated TUI redraw could otherwise consume the first bracketed-paste
input. The runtime now uses Antigravity's native conversation-scoped approval
only for an exact validated `agentsassemble-room read`, `media`, or `speak`
command. A chained `read && ...` probe remains rejected.

An attempted approach that reconstructed a hidden Antigravity command from
provider logs was removed. It guessed text that the terminal did not expose and
would have weakened the permission boundary. The retained implementation
validates only the exact command visible in the permission request.

## What This Experiment Proved

Confirmed:

- event-driven room wake works without provider polling;
- a real provider can choose silence without creating a visible message;
- silence does not immediately wake another provider;
- a quiet room remains quiet until the five-minute room check;
- the five-minute room check works;
- an agent publication wakes peers, which may publish or decline;
- no fixed relay count controls when the discussion stops;
- the same persistent sessions survive multiple observation turns;
- the Codex read-only app-server can read and publish through dynamic room tools;
- profile names can be corrected from the frontend and are reflected in chat;
- Gemini can join the same event-wake room through a persistent Antigravity
  session;
- Codex and Gemini can independently identify a frontend-uploaded JPEG change;
- terminal publication can retain Markdown while rejecting shell substitution;
- Claude tool-use phases no longer masquerade as completed turns.

Not confirmed:

- direct-CLI versus room-path latency parity;
- native image rendering in Claude Code through the current room helper;
- provider understanding of PDF or audio through the frontend;
- behavior over days or a very large room log;
- whether Grok's repeated silence is the preferred conversational policy rather
  than a provider-specific tendency in this topic.

## Next Experiment

The next performance experiment should compare a direct warm turn with room
TTFO for each provider. The next media experiment should focus narrowly on
Claude's supported native image path, then PDF and audio per provider. Neither
experiment should require every provider to speak; a verified decline remains a
valid autonomous outcome.

## Verification

Checks run after the implementation and frontend smoke:

- `npm --prefix frontend test`: 129 passed.
- `npm --prefix frontend run build`: passed.
- `python3 scripts/check_package_architecture.py`: passed.
- `git diff --check`: passed.
- targeted autonomous bridge and busy-session backlog tests: passed.
- the legacy React route inventory initially reported three stale rows after
  canonical room creation moved to `POST /api/rooms`; the inventory was
  corrected and all four inventory tests then passed.

The four-provider fixes were additionally exercised by focused behavior tests:

- a Grok room observation with no public content is a decline while an ordinary
  empty provider final remains an adapter error;
- Claude tool-phase text does not finish a turn before the transcript duration
  boundary;
- exact Antigravity room commands can receive one native approval;
- shell chaining and double-quoted Markdown substitution remain rejected;
- single-quoted Markdown publication is accepted.

The full Python discovery run executed 3,791 tests in 546 seconds: 3,715 passed,
74 were skipped, and 2 failed. Both failures are generated documentation
inventory checks:

- `tests.test_codebase_map.CodebaseMapTests.test_committed_codebase_map_matches_source_tree`
- `tests.test_package_map.PackageMapTests.test_committed_package_map_matches_ast_inventory`

Those files were already being edited by another concurrent task, so this
change did not regenerate or stage them. No runtime, provider, room, or
autonomous-observation test failed. The focused provider checks ran 67 tests
successfully.

## Current Verdict

The former fixed-count and forced-reply designs both failed. The current
provider-observation path demonstrates the intended core behavior: providers
are notified of room activity, inspect a bounded current room view, and may
speak or stay silent. The five-minute idle check also worked in the real
frontend run.

The implementation now passes a clean four-provider, frontend-driven JPEG room
smoke. Codex and Gemini visibly proved pixel-level media understanding; Grok and
Claude exercised clean autonomous decline, with Claude honestly reporting that
its current terminal path could not render the image.

Direct-CLI latency parity, Claude native image rendering, PDF/audio support, and
long-duration room behavior remain open. They are not silently substituted or
reported as complete.
