# Room Repository Boundary

Status: current storage authority and migration contract

Updated: 2026-07-15

Read this document when changing canonical room persistence, transactions,
attention state, or the SQLite/PostgreSQL boundary. It records ownership, not a
promise to migrate every legacy file into the canonical repository.

## Authority Inventory

| State | Current authority | Target | Decision |
| --- | --- | --- | --- |
| Rooms, room-global settings, participants, Agent Sessions, events, command dedupe | `RoomRepository` (`RoomStore` SQLite by default) | `RoomRepository` | Canonical; SQLite and PostgreSQL share one contract. |
| Deleted-room tombstones | `RoomStore.deleted_rooms` | `RoomRepository` | Canonical; must prevent stale clients from recreating deleted rooms. |
| Human identity, credentials, room membership compatibility, preferences, usage | `IdentityBackend` (`IdentityStore` locally, `PostgresIdentityRepository` with the hosted room backend) | Separate identity repository | Hosted identity uses the same PostgreSQL DSN as the canonical room authority and never falls back to local SQLite. |
| Browser and Agent Bridge invite claims and room bearer sessions | `InviteSessionRepository` (`JsonInviteSessionRepository` locally, PostgreSQL with the hosted room backend) | Invite/session repository | Raw invite/session tokens are never stored; use limits, nonce replay protection, and revocation are repository operations, not room events. |
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
User-level notification and read preferences live in identity storage, not in
this room-global record. Existing legacy room-global values are not silently
imported. Run `assemble room migrate-room-settings --dry-run` against
the canonical SQLite source, repair every reported issue, then run the same
command with `--apply`. Apply requires the saved dry-run plan, verifies both
the room-global source fingerprint and target fingerprint, backs up
`room_settings.json` and `rooms.sqlite3`, updates every eligible room in one
SQLite transaction, and records a durable applied fingerprint. Invalid modes,
relay counts, aliases, channel records, and orphan room entries block apply;
they are never replaced with defaults. User-preference-only changes do not
invalidate the room-global fingerprint. The legacy file remains temporarily for
preference migration input but can no longer replay the same migrated globals.

Legacy notification and read preferences have a separate target because the
old file did not identify their owner. Choose an existing identity user and run
`assemble room migrate-room-preferences --user-id <user-id> --dry-run`, repair
every invalid or orphan entry, then repeat with `--apply`. The saved plan binds
the target user, preference-only source fingerprint, and current target
fingerprint. Apply backs up `room_settings.json` and `identity.db`, writes all
eligible preferences in one identity transaction, verifies the committed rows,
and records a user-scoped migration marker. The command never guesses which
operator account should receive old local preferences.

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

Invite/session persistence follows the selected room backend through
`room_invite_repository_factory.py`. SQLite room mode preserves the existing
`room-invite-state.json` format through `JsonInviteSessionRepository`.
PostgreSQL room mode selects `PostgresInviteSessionRepository` with the same
DSN and never falls back to JSON. `room_invite.py` remains a compatibility
facade for token policy while repository implementations own locking,
persistence, atomic invite consumption, replay protection, and session
revocation.

Identity persistence follows the same backend choice through
`identity_repository_factory.py`. Local mode keeps `identity.db`; hosted mode
selects `PostgresIdentityRepository` with the room DSN. The GUI registers that
single backend for its output root so lower-level membership and realtime
helpers cannot silently open SQLite. PostgreSQL identity operations are split
by ownership: users/operator pairing, memberships/compatibility room registry,
user preferences, and usage accounting. The repository facade alone owns the
pool and transaction lifetime.

PostgreSQL schema changes use the packaged Alembic lineage under
`agentsassemble/migrations` and explicit SQL. Runtime repository queries use
`psycopg3`, not an ORM. Revision `0001_room_repository` represents the original
room/attention schema. Revision `0002_room_repository_authority` adds the
activation marker written only by a verified authority transfer. Revision
`0003_deleted_room_commands`, which corresponds to SQLite schema version 4,
retains the deleting principal, request identity, payload hash, ACK, and cleanup
status in the deleted-room tombstone. This lets the same delete command finish
post-delete cleanup without re-running provider process effects while rejecting
conflicting retries. Revision `0004_room_global_settings` adds canonical
room-global settings, revision `0005_invite_sessions` adds the hosted invite
and bearer-session authority, and revision `0006_identity_authority` adds
hosted users, credentials, operator pairing, membership compatibility,
preferences, and usage. Revision `0007_unique_room_access_session` enforces one
active room bearer session for each `(room_id, participant_id)`. Runtime
repository construction never runs Alembic and
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

The optional PostgreSQL installation includes `psycopg_pool`. One
server-scoped `PostgresRoomRepository` opens one bounded pool at startup and
waits at most 10 seconds for its minimum connection. The current conservative
limits are 1 minimum connection, 8 maximum connections, 32 queued borrowers,
and a 5-second acquisition timeout. Every operation borrows from that pool;
normal repository methods do not call `psycopg.connect()` directly. GUI
shutdown closes the realtime controller and HTTP server before closing the
repository-owned pool, while a startup failure after repository construction
also closes it. SQLite implements the same repository lifecycle with a no-op
close because its connections remain operation-scoped.

PostgreSQL pool diagnostics are an explicit numeric allowlist. They report
bounded configuration and pool counters such as size, available connections,
and waiting requests. They never expose the DSN, connection info, host,
database name, username, arbitrary driver values, or exception text. Pool
startup, acquisition, and closed-state errors are explicit; none can trigger a
SQLite fallback.

While a PostgreSQL room transaction is active, the repository binds its checked
out connection to that synchronous execution context. Transaction methods and
any repository-level read reached by a command helper therefore observe the
same snapshot and do not borrow another pool connection. The binding is cleared
before post-commit listeners run. Starting a nested repository write
transaction in the same context is an error; callers must use the active
`RoomTransaction` instead. This prevents a command from deadlocking against its
own bounded pool or reading state outside its atomic command snapshot.

GitHub Actions runs the PostgreSQL repository, migration, schema, and pool
contracts against a real PostgreSQL 16 service. The dedicated
`python -m tests.run_postgres_contracts` entrypoint requires the PostgreSQL
extra and `AGENTSASSEMBLE_TEST_POSTGRES_DSN` before loading the contracts, and
returns failure when no tests run or any selected test is skipped. The ordinary
cross-platform unit suite may still skip environment-dependent PostgreSQL
integration cases; only the dedicated service job is backend-parity evidence.

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

When a local installation still has `room_settings.json`, run and verify
`migrate-room-settings` before `migrate-postgres`. The PostgreSQL transfer then
copies the already-migrated canonical `room_settings` rows with the rest of the
SQLite authority. The legacy settings command deliberately does not write
directly to an activated PostgreSQL repository.
