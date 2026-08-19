# Legacy runtime quarantine

The retained `agentsassemble.legacy` package is a migration and rollback
surface, but it is not yet one uniform runtime boundary. During migration,
some current canonical coordinators still live under the legacy package even
though their APIs are actively used by the current React application.

## Default HTTP behavior

`register_legacy_gui_routes()` places only the explicitly retained
meeting/live-agent compatibility registrars behind a read-only registration
proxy. Their `GET` routes remain available for historical views, status reads,
and migration. Their `POST`, `DELETE`, and other mutating or process-control
routes are dropped before they reach the HTTP router.

`register_room_routes()` is intentionally registered on the real router even
though its module path is currently under `agentsassemble.legacy`. It is the
current canonical coordinator for room history, room lifecycle, members,
channels, voice, invites, moderation/media, and agent-session APIs. Those
current routes remain enabled until their coordinator moves to a non-legacy
owner package.

Current identity, provider, account, room-settings, Cloudflare ingress, and
other current GUI routes are also outside this quarantine.

## Default CLI behavior

The following retained top-level commands are disabled immediately after
argument parsing, before their handlers can execute:

- `demo`
- `lobby`
- `live-agent`
- `memory-capsule`
- `mcp`
- `sessions`

Current commands such as `gui`, `room`, `providers`, `api-call`, `persona`,
`frontend-info`, `release-health`, and `rolling-restart` remain available.
Keeping the retained parsers registered provides a clear quarantine error and
preserves a controlled rollback path without making old handlers reachable in
normal operation.

## Emergency rollback

A temporary rollback can restore retained meeting/live-agent HTTP mutations
and retained top-level CLI execution by setting:

```text
AGENTSASSEMBLE_UNSAFE_ENABLE_LEGACY_MUTATIONS=1
```

Only the exact value `1` enables the escape hatch. Do not use it on a shared,
public, or long-lived deployment. Remove the variable immediately after the
migration or rollback operation.

## Artifact boundary

Legacy meeting artifact identifiers are validated as single portable path
components. Every dynamic artifact path is resolved and checked against its
assigned root before writing. This prevents retained local migration tools
from using role or task identifiers to escape a meeting directory.

## Follow-up removal

This quarantine intentionally keeps imports, parsers, and read compatibility
in place. Full removal requires:

1. moving the canonical room coordinator out of `agentsassemble.legacy`;
2. replacing the remaining legacy CLI commands and read projections;
3. deleting or retiring the compatibility registrars and parsers; and
4. excluding `agentsassemble.legacy*` from the distribution.

Until those migrations are complete, the package name alone must not be used
to decide whether a route is current or retired. The registration boundary in
`register_legacy_gui_routes()` and the command allowlist in
`agentsassemble.legacy.runtime_policy` are authoritative.
