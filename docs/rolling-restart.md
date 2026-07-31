# Rolling restart (무중단 패치)

How to move the running GUI server onto a new build without dropping a single
connection. The mechanism has been in the tree since 2026-07-30; this is the
operator manual for it.

## What actually happens

The listening socket is never closed. The current process forks a replacement,
hands it the *same* listener file descriptor, waits for the replacement to say
it is serving, and only then stops accepting itself. Browsers, room
WebSockets, and provider bridges keep their existing connections.

```
parent (gen N)                          child (gen N+1)
  |                                        |
  |-- fork with listener fd + ready/go pipes -->
  |                                        |-- boots, binds nothing (inherits fd)
  |                                        |-- writes "ready" ------> |
  |<-- waits for ready ---------------------                          |
  |-- stops accepting, writes "go" ------------------------------->   |
  |-- drains, exits                        |-- serves on the same socket
```

Source: [`agentsassemble/application/rolling_restart.py`](../agentsassemble/application/rolling_restart.py),
wired in [`gui_runtime.py`](../agentsassemble/application/gui_runtime.py),
exposed by [`web/routes/runtime.py`](../agentsassemble/web/routes/runtime.py).

## The one rule: turns must be idle

A rolling restart is **refused** while any provider session has a live turn.
That is deliberate — a provider bridge mid-turn holds state the replacement
cannot inherit, so rolling through it would lose the answer the agent is
composing. The refusal is a normal, expected answer, not a failure.

## Commands

Check first. This never starts anything:

```bash
assemble rolling-restart --status
```

```
state: running
pid: 81395  generation: 0
frontend_version: 40d3579e18b32b91
blockers: 1 provider turn(s) still active
  - grok-elon-musk in room-20260731T000600 (busy)
```

Roll when `blockers: none`:

```bash
assemble rolling-restart
```

Wait for in-flight turns to land, then roll (retries every 2s):

```bash
assemble rolling-restart --wait 120
```

Machine-readable output for scripts:

```bash
assemble rolling-restart --status --json
```

A non-default server:

```bash
assemble rolling-restart --server http://127.0.0.1:9000
```

### Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Restart started, or `--status` succeeded |
| 1 | Refused — provider turns still active, or a roll is already running |
| 2 | Could not reach the server, or it was not launched with rolling control |

Codes 1 and 2 are different on purpose: 1 means "ask again later", 2 means
"something is wrong with your invocation or the server".

## Patching the frontend

`frontend_version` in `--status` is the hash of the built `dist`. The server
**caches `index.html` at startup**, so a rebuild is not live until the process
is replaced:

```bash
cd frontend && npm run build && cd ..
assemble rolling-restart --wait 120
assemble rolling-restart --status   # frontend_version must have changed
```

If `frontend_version` did not change, the build did not land where the server
looks — check `assemble frontend-info` before rolling again.

## HTTP API

The CLI is a thin wrapper; the endpoints are the contract.

- `GET /api/runtime/version` — `frontend_version`, `protocol_version`, `generation`
- `GET /api/runtime/rolling-restart` — full status plus `blockers`
- `POST /api/runtime/rolling-restart` — start one; `409` with
  `code: rolling_restart_blocked` and a `details.blockers` list when refused

All three require moderator authority. A request from localhost qualifies as
the local operator, which is why the CLI needs no token on the same host; a
remote caller needs `X-Host-Token` or an operator session.

## Limits

- **POSIX only.** Descriptor handoff is not implemented for Windows; the
  request is refused there with a clear message.
- **One at a time.** A second request while a roll is in progress is refused.
- **Not a config reloader.** Anything read only at startup (listen address,
  port, repository backend) still needs a full restart.

## When it goes wrong

The replacement's own log is written to
`<output-root>/runtime/rolling-restart/<operation_id>.log`.

If the replacement fails to report ready, the parent keeps serving and the
state goes to `failed` with the child's log tail in `error`. **The old server
stays up** — a failed roll does not take the site down. Fix the build and run
the command again.

## Tests

[`tests/test_rolling_restart.py`](../tests/test_rolling_restart.py) covers the
replacement serving on the same port after the old listener closes, the parent
surviving a failed replacement, and the CLI's status/blocked/accepted/
unreachable paths.
