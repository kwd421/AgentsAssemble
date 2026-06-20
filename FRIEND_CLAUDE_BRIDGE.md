# Claude Print-Mode Bridge Disabled

The old friend-owned Claude Code print-mode bridge is no longer a supported
AgentsAssemble path.

AgentsAssemble now requires turn-based Agent Sessions: a resumable local AI CLI
session attached to a room, with persisted participant/session identity,
settings, and one ordered room event stream.

Do not run Claude Code through print or one-shot mode for AgentsAssemble:

```text
Claude print-mode bridge is disabled. AgentsAssemble requires resumable local Agent Sessions; do not use claude -p.
```

Use the room invite or Agent Session resume flow instead. A joining agent should
receive only the room endpoint, the session identity to resume, the allowed room
actions, and the instruction that it must not read or edit the project unless a
room turn explicitly asks for that work.

The historical bridge code is kept only to fail closed for old commands and
tests. It must not be used as a billing-bearing or prompt-bearing fallback.
