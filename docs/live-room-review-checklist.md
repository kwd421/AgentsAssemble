# Live Room Foundation Review Checklist

Use this checklist for the human review gate before marking the live-room/council-workflow foundation complete.

## Review Target

- Branch: `codex/live-room-council-foundation`
- Local GUI: `http://127.0.0.1:8765/`
- Latest implementation commit: `1ab2503 Preserve side chat channel for legacy events`
- Review checklist file: `docs/live-room-review-checklist.md`

## What To Inspect

1. Lobby
   - Sending a lobby message with Enter keeps the input focused.
   - My messages and friend/agent messages do not clip at the right edge.
   - Lobby stream does not repeatedly show reconnect notices during idle use.

2. Live
   - Official debate messages appear in the official transcript area.
   - Status, research, and artifact events appear as progress logs outside the official transcript area.
   - Side chat is visibly separate from official record and says it is excluded from meeting minutes.
   - Sending side-chat messages with Enter keeps the input usable for the next message.
   - If the transcript is scrolled away from the bottom, new official events do not force-jump it unless the latest button is used.
   - Side-chat updates do not re-render the official transcript or discard an unsent side-chat draft.

3. Council Semantics
   - Official turns have deterministic turn metadata: `turn_id`, `turn_index`, and `engagement_mode`.
   - Free chat remains unofficial; it should not feed `transcript.md` or `decision.md`.
   - Final meeting payload still appears after completion even if the final record is temporarily unavailable while being written.
   - If `meeting.json` is temporarily partial, normal meeting list/payload APIs still use `live_state.json` until the final record becomes readable.

4. Artifacts
   - `agenda.md`, `transcript.md`, `decision.md`, per-agent tasks, and return packets still render in Archive.
   - Decision Gate status is visible in Live/Board/Archive where relevant.

## Verification Evidence

The last implementation verification used:

```text
python3 -m unittest discover -s tests
Ran 146 tests
OK
```

Additional checks:

```text
python3 -m compileall -q agentsassemble
node --check agentsassemble/static/app.js
node --check agentsassemble/static/lobby.js
node --check agentsassemble/static/meeting-views.js
git diff --check
```

The latest xhigh-style review found a partial-final-record API gap and a side-chat polling scroll gap. Commit `da39a98 Harden live room recovery refresh paths` fixed both and added regression coverage.

Human GUI review then found two additional issues: right-aligned lobby bubbles could still clip near the scroll edge, and Live side-chat Enter submissions could leave the submitted draft visible after an SSE refresh race. Commit `7279698 Fix lobby bubble and side chat input regressions` adds a stable lobby scrollbar gutter, a right-side safe margin for owner bubbles, and clears side-chat input optimistically while restoring it only if the send fails.

Runtime smoke also found legacy `side_chat.jsonl` rows without a `channel` field could be read back as `lobby` channel events. The follow-up fix treats legacy rows from the side-chat file as `side_chat` during readback and adds regression coverage.

## Known Limits

- The current live room is file-backed SSE, not a provider-native live session attachment.
- Real Claude Code Channels integration remains future work.
- Friend-hosted bridge behavior still needs external network validation.
- Meeting read-only permissions are policy/audit metadata unless paired with an OS-level sandbox.
