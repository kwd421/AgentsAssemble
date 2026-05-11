# Live Room Foundation Review Checklist

Use this checklist for the human review gate before marking the live-room/council-workflow foundation complete.

## Review Target

- Branch: `codex/live-room-council-foundation`
- Local GUI: `http://127.0.0.1:8765/`
- Latest implementation commit: `28bd2e3 Harden live meeting stream recovery`
- Review checklist commit: `86fb49f Add live room review checklist`

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

3. Council Semantics
   - Official turns have deterministic turn metadata: `turn_id`, `turn_index`, and `engagement_mode`.
   - Free chat remains unofficial; it should not feed `transcript.md` or `decision.md`.
   - Final meeting payload still appears after completion even if the final record is temporarily unavailable while being written.

4. Artifacts
   - `agenda.md`, `transcript.md`, `decision.md`, per-agent tasks, and return packets still render in Archive.
   - Decision Gate status is visible in Live/Board/Archive where relevant.

## Verification Evidence

The last implementation verification used:

```text
python3 -m unittest discover -s tests
Ran 144 tests
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

The latest xhigh review found no blocking issues after the final SSE recovery fix.

## Known Limits

- The current live room is file-backed SSE, not a provider-native live session attachment.
- Real Claude Code Channels integration remains future work.
- Friend-hosted bridge behavior still needs external network validation.
- Meeting read-only permissions are policy/audit metadata unless paired with an OS-level sandbox.
