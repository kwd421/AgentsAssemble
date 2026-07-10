# Spec: Rooms as DB-backed server objects (owner-scoped)

Status: ready to implement. Author hand-off for Codex. Self-contained — read the
referenced files before editing; do not assume anything not stated here.

## Why (problem this solves)

Rooms today live in two disjoint places, neither of which is an owned, queryable
object:

- **Frontend**: the room dock caches server rooms in browser `localStorage` and
  keeps local hidden-room tombstones. Hard-coded demo rooms are not restored on
  startup.
- **Server**: a room only materializes as a filesystem dir `.agentsassemble/meetings/<id>/`
  when something needs it. There is **no registry of which rooms exist, who owns
  them, their label, or when they were last active.**

Concrete failures this caused (already partially patched, see "Already done"):
1. Adding an agent to a localStorage room failed with `Meeting <id> was not found`
   because no meeting dir existed.
2. A real multi-agent chat room (`room-20260605T021739`, 270 messages) became
   invisible because it fell out of the localStorage dock — the data was safe on
   disk but there was no server-side way to list/recover it.

This is also the foundation login (Google/guest) rides on later: a room owned by
an account/guest id.

## Design decision (do NOT deviate without asking)

- **Put the room REGISTRY + OWNERSHIP in the DB.** Add a `rooms` table.
- **Do NOT move message bodies into the DB.** Lobby/chat events stay as append-only
  files (`.agentsassemble/lobby.jsonl`, per-room/channel jsonl). The DB holds only
  metadata: which rooms exist, owner, label, timestamps, archived flag.
- **localStorage is demoted to a cache.** The dock merges server room list (source of
  truth) with any local entries.
- **Local-first.** The DB is the existing local SQLite (`identity.db`). No new DB,
  no network service.

## Current state (verified facts)

DB: `.agentsassemble/identity.db`, owned by `agentsassemble/identity_store.py`
(class `IdentityStore`, schema in the module-level `_SCHEMA` string, applied by
`_ensure_schema()`, additive migrations via the `_ensure_column(...)` helper).
Existing tables:
- `users(user_id, participant_id, display_name, avatar_image_url, participant_type, auth_provider, is_operator, created_at, last_seen_at)` — accounts.
- `credentials(auth_key, user_id, provider, created_at, last_used_at)` — device/guest tokens.
- `memberships(meeting_id, participant_id, display_name, role, participant_type, provider_kind, connection_kind, status, muted, is_host, source, created_at, updated_at, last_seen_at)` — who is in which meeting.
- `usage_events(...)`.

So accounts, guest credentials, and membership are ALREADY in the DB. The only
missing piece is the `rooms` registry.

The store is wired in `agentsassemble/gui.py` near line 8137 via
`configure_room_users_store(default_identity_db_path(output_root))`. The
membership/user accessors are exposed as **module-level functions** (e.g.
`participant_is_operator`, `upsert_membership`, `list_memberships`) — find the
module that re-exports them (grep `def configure_room_users_store` and the
`room_users`-style wrapper) and add the rooms accessors the SAME way, so callers
import module-level functions, not a store instance.

## Already done (do not redo; build on these)

- `ensure_frontend_meeting(output_root, meeting_id, *, label="")` in
  `agentsassemble/live_agent_frontend_create.py` — creates a minimal valid
  `meetings/<id>/live_state.json` if absent (idempotent, path-safe). The
  agent-create flow calls it; `POST /api/room/ensure` exposes it; the frontend
  calls it on room activation (`ensureRoomMeeting` in `frontend/src/api.ts`,
  effect in `frontend/src/App.tsx` near the `activeRoom` definition).
- `read_lobby(output_root, limit, *, meeting_id=...)` in `gui.py` now scans
  newest-first with the room filter applied, so a room's own history loads.

**This spec extends `ensure_frontend_meeting` and `/api/room/ensure` to ALSO
upsert a `rooms` row, and adds a list endpoint + frontend dock merge.**

## Tasks

### 1. Schema — `agentsassemble/identity_store.py`
Add to `_SCHEMA`:
```sql
CREATE TABLE IF NOT EXISTS rooms (
  room_id TEXT PRIMARY KEY,
  owner_id TEXT NOT NULL DEFAULT '',
  label TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  last_active_at TEXT NOT NULL,
  archived INTEGER NOT NULL DEFAULT 0,
  origin TEXT NOT NULL DEFAULT ''
);
```
Because existing DBs already have a schema, also guard with `_ensure_column` for
any column you might add later. `CREATE TABLE IF NOT EXISTS` covers the new table
on existing DBs (the table simply gets created on next open).

Add `IdentityStore` methods + `IdentityBackend` Protocol entries, mirroring the
membership methods' style:
- `upsert_room(self, *, room_id, owner_id="", label="", origin="") -> dict` —
  insert if absent (set created_at=last_active_at=now), else update label/owner
  only if non-empty and **always bump `last_active_at`**; never un-archive here.
- `list_rooms(self, *, owner_id="", include_archived=False) -> list[dict]` —
  ordered by `last_active_at DESC`. If `owner_id` empty, return all (operator view).
- `get_room(self, room_id) -> dict | None`
- `set_room_archived(self, room_id, archived: bool) -> bool`
- `touch_room(self, room_id) -> None` — bump `last_active_at` only (cheap, called on activity).

Add a `_room_dict(row)` helper (coerce `archived` to bool) like `_membership_dict`.

### 2. Module-level accessors
Wherever `participant_is_operator` / `upsert_membership` are re-exported as
module functions bound to the configured store, add `upsert_room`, `list_rooms`,
`get_room`, `set_room_archived`, `touch_room` the same way.

### 3. Wire ensure → registry — `agentsassemble/live_agent_frontend_create.py`
After `ensure_frontend_meeting` creates/finds the meeting dir, upsert the rooms
row. Owner resolution must be passed IN (the create module shouldn't reach into
auth): add an optional `owner_id: str = ""` param to `ensure_frontend_meeting`
and call `upsert_room(room_id=clean, owner_id=owner_id, label=title, origin="frontend_room")`.
Keep it best-effort: a registry failure must not break meeting creation (the
meeting dir is still the hard requirement). Import the accessor lazily if needed
to avoid a circular import.

### 4. Endpoints — `agentsassemble/gui.py`
- Extend `POST /api/room/ensure` (already exists) to resolve the requester's
  owner id and pass it to `ensure_frontend_meeting(..., owner_id=...)`. Owner =
  the operator/host for local sessions; for a guest session use their user_id.
  Resolve via the existing auth context (`RequestContext.session()` /
  `participant_is_operator`). For the local operator with no session, use a
  stable operator id (reuse whatever id `users.is_operator=1` row has; if none,
  fall back to `""` = unowned/global).
- Add `GET /api/rooms` → `{ "rooms": [ {room_id, label, last_active_at, archived, origin} ... ] }`.
  Operator/host sees all non-archived rooms; a guest session sees only rooms with
  their `owner_id`. Include `?include_archived=true` to also return archived.
- Add `POST /api/rooms/archive` `{room_id, archived: bool}` (moderator-gated via
  `RequestContext.require_moderator()`), calling `set_room_archived`.
- Register these in the route dispatcher next to the other `/api/...` POST/GET
  handlers (follow the existing pattern at the `/api/room/ensure` site).

### 5. Frontend — `frontend/src/api.ts`
- `fetchRooms(includeArchived = false): Promise<{ rooms: ServerRoom[] }>` →
  `GET /api/rooms`.
- `archiveRoom(roomId, archived): Promise<...>` → `POST /api/rooms/archive`.
- `ServerRoom` type: `{ room_id, label, last_active_at, archived, origin }`.

### 6. Frontend dock merge — `frontend/src/lib/roomDockModel.ts` + `frontend/src/App.tsx`
- On load, fetch `fetchRooms(true)` and **merge** active server rooms into the dock:
  server rooms not already present (by `meetingId === room_id`) become dock items
  (label from server, `meetingId = room_id`). Keep unsynchronized local entries,
  but filter archived, closed, and locally hidden server rooms.
  localStorage stays as a cache/fast-path; server list is authoritative for
  existence. De-dupe by `meetingId`.
- This is what makes `room-20260605T021739` (and any localStorage-cleared room)
  reappear — i.e. the "지난 방 보기 / recover past rooms" behavior, automatic.
- Optional UI: an "archived rooms" section using `?include_archived` + the archive
  toggle. If time-boxed, ship the auto-merge first; archive UI can be a follow-up.

## Constraints (hard)

- **Secret-guard every commit.** Never stage `.wrangler/cache/wrangler-account.json`
  or any cloudflare/credential file. `git status` before each commit.
- **`claude -p` / `--print` is permanently banned** for any resident/automation
  code path. (Not relevant to this task, but don't introduce it.)
- Additive, backward-compatible migration only — existing DBs must keep working
  (the `CREATE TABLE IF NOT EXISTS` + `_ensure_column` pattern guarantees this).
- Do NOT move message bodies into the DB.
- Local-first: no new services, no network deps.

## Tests (required)

- `tests/test_identity_store.py` (or the existing store test file): upsert creates
  then updates (label/owner/last_active bump), `list_rooms` ordering + owner filter
  + archived filter, `set_room_archived`, `get_room` missing → None.
- Extend `tests/test_live_agent_frontend_create.py`: `ensure_frontend_meeting`
  with `owner_id` writes a rooms row; idempotent re-call doesn't duplicate.
- `tests/test_gui_server.py`: `GET /api/rooms` returns created room; guest sees
  only own rooms; archive hides from default list.
- Run the full backend suite. NOTE: `tests/test_gui_server.py::...test_live_agent_session_smoke_endpoint_runs_credential_free_session`
  is a KNOWN pre-existing failure (HTTP 502, spawns a real session subprocess this
  sandbox can't run) — it fails on `main` too; ignore it, don't "fix" it.
- Frontend: `npx tsc --noEmit` clean + `npm run build`.

## Acceptance criteria

1. Creating/activating a room writes a `rooms` row with owner + label.
2. `GET /api/rooms` lists it; restarting the server keeps it (persisted in DB).
3. After clearing browser localStorage, the dock still shows server-known rooms
   (verify `room-20260605T021739` reappears with its 270-message history loading).
4. Archiving a room removes it from the default dock/list but keeps the data.
5. All new tests pass; full suite green except the known smoke-test 502.

## Verify end-to-end (manual)

Server runs locally: `python3 -m agentsassemble.cli gui --port 8765 --output-root .agentsassemble`.
```
curl -s -X POST :8765/api/room/ensure -d '{"meeting_id":"demo-rooms-db","label":"DB방"}'
curl -s :8765/api/rooms | python3 -m json.tool   # demo-rooms-db present
```
