# Open this agent's session in a terminal (future)

Host idea, 2026-08-01. Not scheduled — recorded so the shape is not lost.

## What

A button in the agent details panel that opens the agent's own CLI session in
a real terminal window on the host's machine. First press asks which terminal
to use and remembers the choice.

Terminal choices are platform-specific:

- macOS: Terminal, iTerm, Orca, Ghostty, WezTerm, kitty
- Windows: Windows Terminal, PowerShell, PowerShell 7, cmd
- Linux: the desktop's default, gnome-terminal, konsole, alacritty

## Why it fits

The pieces already exist. Sessions carry a provider session id and a workspace,
and every subscription CLI can resume by id:

| provider | resume |
| --- | --- |
| codex | `thread/resume`, `--resume` |
| claude | `--resume` |
| grok | `session/load` |
| antigravity | `--conversation` |
| cursor | `--resume` |
| opencode | session id in its own store |

`docs/…/자연스러운 에이전트 대화` argues the room is a venue, not a jailer. This
is the same idea in the other direction: the human steps into the agent's own
session rather than watching it through the room.

## Notes for whoever builds it

- **Local operator only.** Spawning a terminal is a host-machine action; a
  remote guest must never trigger it. Same gate as provider login and the
  workspace picker.
- **External agents have no session to open.** `process_ownership == external`
  means this server never launched it and has no workspace or session id for
  it — hide the button rather than failing on press.
- **Depends on folder session resume (item E).** Opening "this agent's session"
  needs the provider session id to be known and resumable; that work lands
  first.
- **Terminal choice is a stored preference**, not a per-press prompt. Detect
  installed terminals rather than listing all of them.
- The command is per-provider and per-terminal: pick the resume invocation
  from the provider, then wrap it in the terminal's own launch syntax
  (`open -a`, `wt.exe`, `x-terminal-emulator -e`, …).
