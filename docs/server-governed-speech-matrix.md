# Server-Governed Speech Matrix

Reviewed: 2026-06-18

This is the working map for server-governed speech in the room. It is
intentionally a current-state audit plus the next safe refactor order, not a
finished architecture. The goal is to show every visible speech/presence/admission
entry that can affect the room and which governance checks are actually enforced
there today.

The product direction is:

> Adapters may differ, transports may differ, but room-visible speech should pass
> through one server-governed policy path before it is appended.

This document keeps `MISSING`, `UNCLEAR`, and `N/A` explicit so the next change
can fix real gaps instead of inventing a clean-looking abstraction.

## Legend

- `ENFORCED` means this entry performs the check directly before the side effect.
- `DELEGATED` means this entry calls a helper that performs the check or cleanup.
- `TRUSTED` means the route is currently treated as host/operator-local control
  plane, not as participant speech.
- `MISSING` means the check appears absent for this entry.
- `UNCLEAR` means the current behavior may be intentional, but the product rule is
  not explicit enough to classify it.
- `N/A` means the check does not belong to that operation.

## Entry Inventory

These entries were derived by grepping for room appenders and visible-state
mutators (`append_lobby_event`, `append_lobby_event_to_file`,
`append_side_chat_event`, `append_live_event`, direct DM appenders, room invite
join/leave, and live-agent presence updates).

Excluded from the main matrix:

- `agentsassemble/live_agent_persona_smoke.py`, `agentsassemble/room_event_benchmark.py`,
  and smoke/benchmark helpers: diagnostic fixtures, not user-facing room entry
  points.
- `agentsassemble/lobby_promotion.py`, `agentsassemble/live_agent_meetings.py`,
  `agentsassemble/live_agent_finalization.py`, and `agentsassemble/meeting.py`:
  official meeting/finalization internals. They should get their own official
  record matrix, but they are not participant lobby speech entries.
- `/api/play/mafia/*`: Play Mode game channel. Product docs keep Play Mode
  separate from Work Mode/lobby governance unless a promote action explicitly
  bridges them.

## Speech And Message Entries

| Entry | Identity / Auth | Scope / Mute / Read-Only | Sanitization | Chain / Flood / Rate | Turn / Source CAS | Side Effect | Gap Candidate |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `POST /api/room/say` | `ENFORCED` by session token via `RequestContext.require_posting_session` in `agentsassemble/gui_router.py:151`; identity is normalized through `ActorIdentity.from_mapping` in `agentsassemble/room_speech.py:23` and stamped server-side in `agentsassemble/room_speech.py:87`. | `ENFORCED` read-only reject in `agentsassemble/gui_router.py:156`; shared policy check in `agentsassemble/room_speech.py:53`; HTTP preserves the pre-body mute check order in `agentsassemble/gui_room_http.py:216`. | `DELEGATED` to governed lobby payload stamping in `agentsassemble/room_speech.py:65` and event cleanup in `agentsassemble/meeting_events.py:97`. | `MISSING` for flood/rate and server-side chain-depth policy. | `N/A` for ordinary lobby speech; flow turn CAS only exists on live-agent lobby replies. | Appends lobby event through `governed_lobby_say` in `agentsassemble/gui_room_http.py:230`. | First slice complete: HTTP room say uses the shared governed lobby-say service, while keeping transport parsing/error behavior in the route. |
| WebSocket `op: "say"` | `ENFORCED` by the verified WS identity; client metadata is safelisted in `agentsassemble/ws_room_session.py:33`, then real server deps normalize/stamp through `governed_lobby_say` in `agentsassemble/gui.py:8266` and `agentsassemble/room_speech.py:87`. | `ENFORCED` read-only and mute checks still fast-fail in `agentsassemble/ws_room_session.py:188`; real server deps re-check shared policy in `agentsassemble/room_speech.py:53`. | `DELEGATED` through WS metadata safelist in `agentsassemble/ws_room_session.py:196` and governed lobby append in `agentsassemble/gui.py:8266`. | `MISSING` for burst/flood/rate; the file comment says burst/dedup limiting is not implemented in `agentsassemble/ws_room_session.py:6`. Server accepts safelisted `auto_chain_depth`, but does not enforce a chain policy here. | `N/A` for ordinary lobby speech. | Appends lobby event through `governed_lobby_say` in `agentsassemble/gui.py:8266`. | First slice complete: WS now shares the same server-side identity stamping/append service as HTTP, though protocol-level fast-fails remain in the WS core. |
| `POST /api/room/channel-say` | `ENFORCED` for session callers by `ctx.session()`/host fallback in `agentsassemble/gui_room_http.py:494`; session identity stamped in `agentsassemble/gui_room_http.py:568`; local operator identity stamped in `agentsassemble/gui_room_http.py:570`. | `ENFORCED` read-only for session callers in `agentsassemble/gui_room_http.py:499`; mute for session callers in `agentsassemble/gui_room_http.py:556`; host/local operator mute is `N/A/TRUSTED`. Channel existence/type is checked in `agentsassemble/gui_room_http.py:561`. | `DELEGATED` to `append_lobby_event_to_file` in `agentsassemble/gui_room_http.py:573` and `agentsassemble/meeting_events.py:399`. | `MISSING` for flood/rate and chain policy. | `N/A` for custom channel speech today. | Appends channel event file at `agentsassemble/gui_room_http.py:573`. | Same family as room say, but it bypasses the HTTP/WS lobby append route and therefore duplicates governance. |
| `POST /api/live-agents/{agent_id}/lobby` | `ENFORCED` from persisted live-agent state via heartbeat lookup in `agentsassemble/gui.py:3475`; actor id comes from the stored agent in `agentsassemble/gui.py:3480`. Route dispatch starts in `agentsassemble/gui.py:11108`. | `ENFORCED` mute check in `agentsassemble/gui.py:3481`; read-only is `N/A` because this is a server-owned resident endpoint, not a guest invite session. | `DELEGATED` to lobby append cleanup in `agentsassemble/gui.py:3530` and `agentsassemble/meeting_events.py:97`. | `UNCLEAR/MISSING`: `auto_chain_depth` is copied from payload in `agentsassemble/gui.py:3540`, while flow turn CAS exists, but there is no generic chain-depth cap or flood/rate limit. | `ENFORCED` for flow speaking actions through `_flow_turn_conflict` in `agentsassemble/gui.py:3513`, with duplicate/stale-turn checks in `agentsassemble/gui.py:3448` and `agentsassemble/gui.py:3457`. | Appends lobby event at `agentsassemble/gui.py:3530` and updates heartbeat after reply. | This is closest to a policy choke, but only for live-agent lobby replies. |
| `POST /api/live-agents/{agent_id}/official-turn` | `ENFORCED` by live-agent lookup and meeting attachment in `agentsassemble/gui.py:4077`. | `UNCLEAR`: meeting attachment is enforced in `agentsassemble/gui.py:4079`, but muted-agent behavior is not checked here. Product rule must decide whether host-requested official replies bypass lobby mute or still honor mute. | `ENFORCED` content cleanup in `agentsassemble/gui.py:4086`; official event cleanup delegated to `append_live_event` in `agentsassemble/gui.py:4132` and `agentsassemble/meeting_events.py:430`. | `N/A` for lobby chain; official turns are bounded by turn requests rather than auto-reply chain depth. | `ENFORCED` matching request/cancellation/idempotency under `LIVE_AGENT_TURN_LOCK` in `agentsassemble/gui.py:4092`. | Appends official live event at `agentsassemble/gui.py:4132` and refreshes shared memory in `agentsassemble/gui.py:4133`. | This must remain a separate official-record choke, not be folded into generic lobby `say`. |
| `POST /api/lobby` | `TRUSTED` local/operator route: it is not in the public invite allowlist in `agentsassemble/gui.py:8170`; loopback trust gate is in `agentsassemble/gui.py:8190`. | `MISSING/TRUSTED`: no participant session, read-only, or mute enforcement in the route body at `agentsassemble/gui.py:8791`. | `DELEGATED` through attachment cleanup in `agentsassemble/gui.py:8802` and lobby event cleanup in `agentsassemble/gui.py:8806`. | `MISSING` for chain/flood/rate. | `N/A` for generic operator lobby post. | Appends lobby event at `agentsassemble/gui.py:8806`. | If this route remains operator-only, mark it explicitly as control-plane. If exposed to participants, it must call governed speech. |
| `POST /api/side-chat` | `TRUSTED` local/operator route under the same request trust gate; no participant session in route body at `agentsassemble/gui.py:8821`. | `MISSING/TRUSTED`: no session, read-only, or mute enforcement before append. | `DELEGATED` to side-chat event cleanup in `agentsassemble/gui.py:8828` and `agentsassemble/meeting_events.py:422`. | `MISSING` for flood/rate. Chain depth is `N/A` unless side-chat auto-replies are added. | `N/A`. | Appends side-chat event at `agentsassemble/gui.py:8828`. | Side-chat needs an explicit decision: operator-only scratchpad or governed participant channel. |
| `POST /api/room-friends/dm` | `TRUSTED` operator-to-friend DM route; route delegates at `agentsassemble/gui.py:8854`. | `N/A/TRUSTED`: this is a directed operator DM, not room participant speech. | `ENFORCED` message/friend/target cleanup in `agentsassemble/room_friend_dms.py:84`. | `MISSING` for DM flood/rate. | `N/A` for outgoing operator DM. | Appends queued DM event at `agentsassemble/room_friend_dms.py:102`. | DM is outside public lobby, but still needs bounded rate/cost policy if long-running rooms use it. |
| `POST /api/live-agents/{agent_id}/dm-reply` | `ENFORCED` by target agent id and source event lookup in `agentsassemble/room_friend_dms.py:152` and `agentsassemble/room_friend_dms.py:161`. | `UNCLEAR`: mute/read-only room policy is not checked; this may be acceptable because DM is not public room speech. | `ENFORCED` reply cleanup in `agentsassemble/room_friend_dms.py:153`. | `MISSING` for DM flood/rate. | `ENFORCED` source DM must exist for that agent in `agentsassemble/room_friend_dms.py:161`. | Appends DM reply at `agentsassemble/room_friend_dms.py:165`. | Decide whether public room mute should also suppress private DM replies. |
| `POST /api/lobby/remote` | `TRUSTED` operator-triggered remote bridge call in `agentsassemble/gui.py:8906`; binding/provider selected in `agentsassemble/gui.py:1913`. | `MISSING/TRUSTED`: no mute/read-only check for the selected remote binding before append. Meeting scope is used to resolve the meeting, but the appended event does not carry `flow_meeting_id` in `agentsassemble/gui.py:1927`. | `DELEGATED` remote response normalized by adapter in `agentsassemble/adapters/remote_bridge.py:170`; lobby append cleanup in `agentsassemble/gui.py:1933`. | `MISSING` for chain/flood/rate. | `N/A` for one-shot remote bridge lobby call. | Calls remote bridge in `agentsassemble/adapters/remote_bridge.py:190` and appends returned lobby text in `agentsassemble/gui.py:1933`. | High-value cleanup: stamp actor/source/meeting scope and route through governed speech if this is used as participant speech. |

## Admission And Presence Entries

These are not speech entries, so content sanitization, chain depth, and turn CAS
are usually `N/A`. They still matter because they create or update the identity
that later speaks.

| Entry | Identity / Auth | Scope / Policy | Sanitization | Chain / Turn | Side Effect | Gap Candidate |
| --- | --- | --- | --- | --- | --- | --- |
| `POST /api/room-invite/join` | `ENFORCED` invite token verification in `agentsassemble/room_invite.py:435`; nonce/use enforcement in `agentsassemble/room_invite.py:451`; session token issuance in `agentsassemble/room_invite.py:503`. | `ENFORCED` invite scope and participant type are read from invite/claims in `agentsassemble/room_invite.py:426` and returned in `agentsassemble/room_invite.py:512`. | `ENFORCED` display/owner cleanup in `agentsassemble/room_invite.py:489` and `agentsassemble/room_invite.py:496`. | `N/A`. | Registers human member or remote agent best-effort in `agentsassemble/gui_room_http.py:730`, then returns session. | Good admission root for remote participants; downstream speech must keep trusting only this session, not client-supplied actor fields. |
| `POST /api/room-invite/leave` | `ENFORCED` session token required in `agentsassemble/gui_room_http.py:795`. | `ENFORCED` revokes the active session in `agentsassemble/gui_room_http.py:806`. | `N/A`. | `N/A`. | Marks agent offline best-effort in `agentsassemble/gui_room_http.py:798` and revokes session. | Leave is presence/session state, not speech. |
| `POST /api/live-agents` | `TRUSTED` operation route registers a live agent; `connect_live_agent` requires/cleans `agent_id` in `agentsassemble/live_agents.py:88`. | `UNCLEAR`: registration records connection/provider/meeting fields in `agentsassemble/live_agents.py:96`, but host-approved binding enforcement is not visible in `connect_live_agent` itself. | `ENFORCED` provider, display, meeting, session, workspace, model, permission, and related fields are cleaned in `agentsassemble/live_agents.py:102`. | `N/A`. | Upserts live-agent presence state in `agentsassemble/live_agents.py:500`. | Keep registration separate from host admission. Do not let a registered agent imply authorized resident execution. |
| `POST /api/live-agents/{agent_id}/heartbeat` | `ENFORCED` only by route/agent id shape; heartbeat requires a non-empty cleaned id in `agentsassemble/live_agents.py:423`. | `UNCLEAR`: if the agent exists, heartbeat updates it; if missing, it creates a manual placeholder in `agentsassemble/live_agents.py:438`. | `ENFORCED` metadata fields are cleaned/redacted in `agentsassemble/live_agents.py:464`. | `N/A`. | Upserts presence state in `agentsassemble/live_agents.py:500`. | Consider whether heartbeat should be allowed to create unknown agents, or whether registration/admission must happen first. |
| `POST /api/live-agents/{agent_id}/leave` | `ENFORCED` live-agent must exist via `_live_agent_for_id` in `agentsassemble/gui.py:3330`. | `ENFORCED` status changes to offline through heartbeat in `agentsassemble/gui.py:3336`. | `ENFORCED` cursor metadata is allowlisted in `agentsassemble/gui.py:3332`. | `N/A`. | Updates presence offline at `agentsassemble/gui.py:3336`. | Presence only. |

## Current Bug-Shaped Findings

1. The first shared lobby-say service now exists for HTTP room say and WS say,
   but there is still no complete single choke for all room-visible speech.
   Channel say, live-agent lobby replies, local `/api/lobby`, side-chat, DM, and
   remote bridge still assemble their own pre-append policy.
2. WS can be treated as the preferred transport for invited room clients only
   when new WS speech features continue to delegate to the shared governed
   lobby-say service instead of adding another transport-local append path.
3. `official-turn` needs a separate governed official-reply service. It has a
   stronger source/turn lock than lobby speech and writes the official live
   transcript plus shared memory.
4. The reusable core should not be a giant `SayRequest`. The shared pieces are:
   `ActorIdentity`, `RoomScope`, and payload `Sanitizer`, with separate
   operation services for lobby say, channel say, official reply, DM reply,
   join, leave, and heartbeat.
5. Flood/rate governance is missing almost everywhere. Long-running AI rooms
   need bounded post frequency and chain policy at the server before multi-day
   autonomous rooms are safe.
6. `/api/lobby/remote` currently appends a remote bridge response without
   actor/source/meeting metadata. If it is still used as participant speech, it
   should be moved behind the same governed lobby service.

## Refactor Order

1. Extend the shared governance core cautiously. `ActorIdentity` and
   `governed_lobby_say()` exist; `RoomScope` and sanitizer helpers should be
   added only when the next caller needs them.
2. Move `POST /api/room/channel-say` to a sibling governed channel path that
   reuses identity/scope/sanitizer but keeps channel validation local.
3. Move live-agent lobby replies to the same governed lobby service, preserving
   existing flow turn-CAS behavior.
4. Add `governed_official_reply()` separately for official-turn replies.
5. Decide side-chat, DM, and `/api/lobby/remote` policy: operator-only trusted
   routes or participant-governed speech routes. Do not leave them ambiguous.
6. Add flood/rate and chain-depth policy at the shared service boundary.
7. Add a focused governance test suite that attacks every speech entry with:
   spoofed `actor_id`, read-only session, muted participant, over-depth chain,
   stale turn source, expired/missing session, and flood/rate attempts where
   applicable.
