# Room Repository Boundary

Status: current storage authority and migration contract

Updated: 2026-07-14

Read this document when changing canonical room persistence, transactions,
attention state, or the SQLite/PostgreSQL boundary. It records ownership, not a
promise to migrate every legacy file into the canonical repository.

## Authority Inventory

| State | Current authority | Target | Decision |
| --- | --- | --- | --- |
| Rooms, room-global settings, participants, Agent Sessions, events, command dedupe | `RoomRepository` (`RoomStore` SQLite by default) | `RoomRepository` | Canonical; SQLite and PostgreSQL share one contract. |
| Deleted-room tombstones | `RoomStore.deleted_rooms` | `RoomRepository` | Canonical; must prevent stale clients from recreating deleted rooms. |
| Human identity, credentials, room membership compatibility, usage | `identity_store.py` SQLite | Separate identity repository | Keep the security boundary separate initially; migrate deliberately after room parity. |
| Browser and Agent Bridge invite claims | `room_invite.py` plus `room-invite-state.json` | Invite repository | Compatibility state; replace with durable single-use claims, not room events. |
| Room-member moderation compatibility | `room_members.py` and identity DB | Canonical participant/membership commands | Reconcile with canonical participants; do not create a third roster. Ephemeral typing/thinking stays out of durable room state. |
| Side chat | `side_chat.py` JSONL | Legacy or a future explicit channel model | Do not migrate into `RoomRepository` merely because the file exists. |
| Media metadata | Safe IDs in canonical room events | `RoomRepository` | Canonical metadata only; no local path in a public event. |
| Media bytes | Per-room filesystem directories | Media object adapter | Keep outside the room database transaction; PostgreSQL stores references, not bytes. |
| Legacy meeting artifacts | `meetings/<id>/...` files | Legacy path | Do not migrate into the new room repository unless legacy removal is separately approved. |
| Provider-private session memory and credentials | Provider runtime / OS secret store | Provider-owned | Never copy into room events or the room repository. |
| Server-owned process handles and live sockets | In-process runtime managers | Runtime-owned | Ephemeral; durable session rows store recovery state, never reusable OS handles. |
| Legacy session-run monitor records | `live-agent-runs/session-runs.json` | Legacy compatibility | Keep outside the canonical room migration until that product path is removed or redesigned. |

Canonical room-global settings have a strict domain record in
`room_global_settings.py`. It contains only label, topic, room appearance,
conversation mode, bounded relay count, and custom channels. Notification and
read state are user preferences, while participant role belongs to the
participant row. Both repository backends are authoritative for this record;
`rooms.label` is an indexed projection updated in the same transaction.
`room_settings.json` remains temporary compatibility storage only for user-level
notification/read preferences. Existing legacy room-global values are not
silently imported: Phase 3.3 provides an explicit, fingerprinted migration.

## Transaction Contract

`RoomRepository.transaction(room_id)` is the write boundary. A transaction is
room-local and must provide these guarantees on every backend:

1. Event `seq` is strictly increasing within a room and allocated inside the
   committing transaction.
2. Participant, Agent Session, event, and command-result changes made by one
   command either all commit or all roll back.
3. A rollback publishes no event and leaves no consumed sequence gap caused by
   that failed transaction.
4. Event listeners and WebSocket fanout run only after the database commit.
5. Command idempotency is scoped by `(room_id, principal_id, request_id)` and a
   conflicting action or canonical payload hash is rejected by command policy.
6. Cursor replay uses durable room sequence, never process-local counters.
7. Room deletion removes canonical room state atomically and retains its
   tombstone in the same commit.

The repository does not own routing, attention policy, provider execution,
WebSocket serialization, media bytes, or identity authentication. Those layers
may coordinate a repository transaction but must not receive a raw database
connection.

The canonical controller, lifecycle service, turn coordinator, and provider
context projector use one injected repository instance. A provider turn must
not construct a second SQLite store behind that boundary.

## Migration Order

1. Put current SQLite behavior behind this contract and run the backend-neutral
   contract suite against it.
2. Add durable attention records without enabling autonomous replies.
3. Run attention decisions in SQLite shadow mode.
4. Implement the same repository and attention contracts in PostgreSQL.
5. Prove backend parity and provide an explicit SQLite-to-PostgreSQL migration.
6. Enable ambient participation only after shadow evaluation; PostgreSQL is not
   required for a local preview, but is required before hosted multi-worker use.

No compatibility fallback may silently switch databases. Backend selection,
migration failure, and unavailable PostgreSQL must be explicit operator-visible
errors.

Repository configuration lives in `room_repository_factory.py`. PostgreSQL is
an optional installation extra and reads its DSN from the configured environment
variable (default `AGENTSASSEMBLE_ROOM_DATABASE_URL`); the DSN value is excluded
from public diagnostics and object representations. Selecting PostgreSQL without
that value or without the optional driver is a startup error, never a request to
open a local SQLite database instead.

PostgreSQL schema changes use the packaged Alembic lineage under
`agentsassemble/migrations` and explicit SQL. Runtime repository queries use
`psycopg3`, not an ORM. Revision `0001_room_repository` represents the original
room/attention schema. Revision `0002_room_repository_authority` adds the
activation marker written only by a verified authority transfer. Revision
`0003_deleted_room_commands`, which corresponds to SQLite schema version 4,
retains the deleting principal, request identity, payload hash, ACK, and cleanup
status in the deleted-room tombstone. This lets the same delete command finish
post-delete cleanup without re-running provider process effects while rejecting
conflicting retries. Runtime repository construction never runs Alembic and
refuses PostgreSQL until both the head revision and the authority marker are
present. The existing SQLite migrator remains responsible for upgrading
pre-repository local files and old SQLite versions.

Revision `0004_room_global_settings`, which corresponds to SQLite schema version
5, adds one strict settings row per room. Schema upgrade backfills canonical
defaults derived only from the room label; it deliberately does not infer or
copy values from legacy JSON files. A missing or invalid settings row for an
existing room is an error, never a request to recreate defaults silently.

`PostgresRoomRepository` now implements the same transaction, event replay,
participant/session lifecycle, command dedupe, media metadata, and durable
attention contract as `RoomStore`. PostgreSQL-specific reads, mutations, and
attention persistence are separate modules; the repository facade owns
connections, transaction locks, listeners, and filesystem side effects.

The GUI handler, canonical WebSocket controller, Agent Session HTTP actions,
room lifecycle, roster projection, invite admission, attachment metadata, and
canonical SSE replay now receive one server-scoped repository instance. Handler
construction rejects a controller and route repository that are not the same
object.

`assemble room migrate-postgres` is the explicit authority transfer tool. It
reads the DSN only from the named environment variable, defaults to a no-write
dry run, and requires `--apply` to create the PostgreSQL schema and copy rows.
Apply mode holds the SQLite write lock, refuses a non-empty or partial target,
copies every canonical and attention table in one PostgreSQL transaction, and
compares normalized table checksums plus per-room event sequence summaries
before commit. The same transaction writes the PostgreSQL authority activation
marker only after those checks pass. It never deletes or edits the SQLite
source. The GUI selects storage explicitly with
`--room-repository-backend sqlite|postgresql`; PostgreSQL reads its DSN only
from `--room-postgres-dsn-env`. Startup validates the head schema and authority
marker and never auto-migrates or falls back to SQLite. SQLite remains the
default.
