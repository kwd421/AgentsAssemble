# AgentsAssemble Operating Model

This document records the product memory that should survive chat context loss.
It is intentionally small: use it to orient agents before changing live-session,
provider, memory, or GUI behavior.

## Why This Exists

Viewooa has a useful documentation pattern: keep product intent, ownership, status,
and decisions in source-controlled files instead of relying on chat history.
AgentsAssemble should use that pattern, not copy Viewooa files or move anything from
that repository. Do not copy Viewooa files; adapt only the useful documentation
practice.

## Product Shape

AgentsAssemble has two related but different product modes.

Work Mode:
- Runs auditable council meetings.
- Keeps official turns, evidence, decisions, action items, and handoff packets.
- Blocks implementation until meeting artifacts and permission gates allow it.

Play Mode:
- Runs social or theatrical live rooms, such as idle debates, trials, or games.
- Can be entertaining without producing implementation decisions.
- Must still respect provider approval, loop limits, cost limits, and clear record
  boundaries.

Play Mode can feed Work Mode only through an explicit promote action. Lobby banter,
games, and informal chatter must not silently become an official record.

## Non-Negotiable Rules

- discovery is not execution.
- Config generation is not execution.
- meeting room startup must not launch real provider CLIs.
- Real provider CLIs require explicit operator approval before they are started.
- Only a host-approved session, group, or agent binding may participate as a
  resident.
- A stopped session should stay stopped unless the operator explicitly starts,
  ensures, resumes, or recovers it.
- Provider execution style must be named honestly: native/session-managed,
  Codex exec/resume, PTY terminal bridge, self-service room loop, remote bridge,
  or stateless prompt call.
- frontend polish is deferred until the backend state and data contracts are
  stable enough for another AI or human designer to refine.

## Context Model

Each provider or CLI owns its agent-private context.

AgentsAssemble should preserve that boundary:
- Do not merge one agent's private context into another agent.
- Do not dump raw project history into every provider by default.
- Do not pretend to own or compress a provider's hidden session state.
- Pass only the current room event, relevant recent public room context, role
  identity, and explicit shared meeting memory.

AgentsAssemble owns shared meeting memory.

Shared meeting memory includes:
- official transcript entries.
- `shared_memory/rolling-summary.md` as the rolling summary for long-running
  resident meetings.
- decisions and unresolved decision points.
- `shared_memory/open-questions.md`.
- `shared_memory/action-items.md`.
- `shared_memory/index.json` as the deterministic machine-readable index for
  those shared-memory artifacts.
- promoted context from Play Mode into Work Mode.
- memory or handoff packets intentionally shared with future sessions.

The system should record what was shared and what stayed private.

## Official Record Boundary

The room may contain lobby chat, side chat, game chatter, system events, and
official turns. Only typed official meeting events should feed transcript,
decision, evidence, task, and handoff artifacts.

Useful rule:

```text
lobby or play chatter -> visible room history
official turn         -> transcript and decision evidence
explicit promote      -> selected informal context becomes official input
```

## What To Build Next

Near-term work should favor backend contracts over visual polish:
- Add discovery rows that say how a provider can join and what evidence supports it.
- Add context durability labels such as provider-managed, process-lifetime, and
  stateless-prompt where they are accurate.
- Keep `shared_memory/` resident meeting artifacts deterministic, official-only,
  and refreshed during long-running sessions.
- Keep GUI changes minimal: show trustworthy state and leave detailed front-end
  styling for a later pass.

## Source-Of-Truth Routing

- `docs/roadmap.md` tracks status and priority.
- `docs/live-session-room-model.md` owns room semantics.
- `docs/provider-architecture.md` owns provider and adapter boundaries.
- `docs/live-agent-ops.md` owns operator commands, readiness, and verification.
- `docs/product/OPERATING_MODEL.md` owns the product memory in this file.

When these files conflict, stop and surface the conflict before editing behavior.
