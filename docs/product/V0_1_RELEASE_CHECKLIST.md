# AgentsAssemble v0.1 Release Checklist

This checklist defines "higher completion quality" for the current v0.1 push.
It is not a feature wishlist. It is the release-hardening bar for the core
usable flow that already exists: local GUI room, approved agents, official
turns, shared meeting memory, finalization, archive or CLI/API reads, and local
stdio MCP room tooling.

## Core Usable Flow

v0.1 is ready when a new operator can complete this Work Mode flow without
reading chat history:

1. start the GUI room from a checkout.
2. create or select a meeting that has clear Work Mode intent, or a Play preset
   that has been explicitly routed into an official-turn artifact path.
3. invite or attach an approved agent through the live-agent packet, terminal
   self-service room tools, local MCP participant tools, or a resident
   live-agent process.
4. verify the roster shows honest provider, connection, admission, context
   durability, and readiness evidence.
5. run at least one official turn through an agent-owned room loop such as
   `self_service`, terminal self-service tools, local MCP participant tools, or
   future native room tools.
   Host-side PTY prompt injection does not satisfy this proof by itself.
6. see official replies update the live transcript and compact shared memory.
7. finalize without invented replies; pending official turns must be explicitly
   closed or cancelled before finalization.
8. read transcript, decision, shared memory, and return packet artifacts from
   the archive, CLI/API artifact reads, or archive MCP.
9. stop or leave agents so roster status and process/session state remain
   honest after the room is done.

## Non-Negotiable Boundaries

- Do not treat provider discovery as execution.
- Do not treat config generation, join-brief generation, MCP attachment, or LAN
  invite creation as provider startup approval.
- Do not restart stopped provider sessions automatically.
- Do not expose prompts, provider output, auth refs, config paths, command
  arguments, endpoint URLs, or raw local paths in health, operation, roster, or
  archive convenience surfaces.
- Do not add the Trello/Jira roadmap board to the vanilla GUI. That belongs to
  the later React/Vite responsive frontend track.
- Do not blur meeting progress with product roadmap progress. Meeting progress
  answers where one room is; the roadmap board answers where the product is
  going across versions.

## Release Evidence

The release candidate should have current evidence for these surfaces:

- GUI loads `/` and static assets from a temporary output root.
- `node --check agentsassemble/static/*.js` passes.
- `python3 -m unittest tests.test_static_ui_assets -v` passes.
- `python3 -m unittest tests.test_mcp_server -v` passes for the checked-in MCP
  participant/archive tool boundary.
- `python3 -m unittest tests.test_gui_server tests.test_live_agent_smoke -v`
  passes for the local GUI/live-agent flow.
- `git diff --check` passes for the intended diff.

The local MCP participant profile exposes only register, heartbeat, wait_next,
say, official_reply, read_room, read_return_packet, and leave. Participant
identity is fixed by the MCP server startup args, not by tool-call input. The
archive profile exposes only read_transcript, read_decision, read_shared_memory,
list_meetings, and read_meeting_summary, with path-like meeting ids rejected and
raw local paths/config metadata kept out of archive outputs. Host-control MCP remains out of scope.

## Verification Commands

Use these as the ordinary v0.1 hardening proof set:

```bash
node --check agentsassemble/static/*.js
python3 -m unittest tests.test_static_ui_assets -v
python3 -m unittest tests.test_docs_architecture -v
python3 -m unittest tests.test_mcp_server -v
python3 -m unittest tests.test_gui_server tests.test_live_agent_smoke -v
python3 -m compileall -q agentsassemble
git diff --check
```

`assemble mcp serve` uses the official MCP Python SDK (`mcp>=1,<2`) through a
lazy import. The unit tests cover the room-tool boundary without requiring the
SDK in the system interpreter; an actual stdio MCP client smoke still requires an
environment where that dependency is installed.

## UX Hardening Queue

These are the next small completion slices. Each one should stay narrow,
verified, and separate from broad frontend redesign.

1. Clarify the room surfaces:
   - Lobby means pre-meeting staging and agent admission.
   - Live means official room progress.
   - Board means operator controls, readiness, and diagnostics.
   - Archive means durable outputs and review records.
2. Reduce default button overload:
   - show the basic path first.
   - tuck diagnostics, smoke, recovery, and advanced controls behind clearer
     groups.
3. Add current-step evidence:
   - show whether the room is preparing, waiting for agents, running official
     turns, waiting on pending turns, finalized, or stopped.
4. Tighten empty and post-run states:
   - make the next action clear when no meeting exists.
   - make final artifacts easy to find after finalization.
5. Keep future roadmap UI deferred:
   - use `docs/roadmap.md` and this checklist as source-of-truth documents.
   - build a Trello/Jira-like roadmap page only when the React/Vite frontend
     track starts.

## Next Slice Queue

The next implementation slice should be chosen from this queue:

- Make the GUI label the core flow as Lobby, Live, Board, Archive with one clear
  next action per state.
- Add or improve tests that verify the GUI does not replace live-event rows
  unnecessarily during refresh.
- Add a compact current-step/status projection for meeting lifecycle state.
- Turn this checklist into a small release-health command after the underlying
  checks stabilize.

## Out Of Scope For v0.1 Hardening

- Public hosted MCP.
- Authenticated remote room APIs.
- React/Vite/Tailwind migration.
- Trello/Jira roadmap board UI.
- Provider billing, login, or subscription management.
- Automatic startup of real provider CLIs without current explicit approval.
