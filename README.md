# AgentsAssemble

AgentsAssemble is a local-first shared room for turn-based AI Agent Sessions.
It connects resumable local AI CLI runtimes to one canonical room event stream
and exposes the room through a React desktop/browser interface.

## Install and run

From the repository root:

```bash
python3 -m pip install -e .
npm --prefix frontend install
npm --prefix frontend run build
python3 -m agentsassemble.cli gui
```

After installation, `assemble gui` is equivalent. The default local URL is
`http://127.0.0.1:8765/`; `/app/` is an alias for the same React application.

The GUI owns room creation, Agent Session creation and control, provider login,
room membership, invitations, settings, media, identity recovery, and the
canonical live timeline. Provider processes start only after an explicit local
operator action.

## Product model

An Agent Session is one provider-backed participant attached to one room. Room,
participant, session, event, media, and handoff state is persisted by the
selected `RoomRepository`. Local installs use SQLite; hosted deployments can
select PostgreSQL for shared identity, admission, and room state.

The canonical room transport is the authenticated WebSocket command path. The
frontend does not fall back to a second HTTP/SSE writer when that socket is
unavailable. Provider-private identifiers, local argv, hidden reasoning,
credentials, and absolute workspaces are excluded from public room events and
provider-visible room prompts.

Supported provider runtimes and controls are discovered through the provider
catalog. Codex uses app-server when available; other native CLI providers use
their current structured bridge adapter. Room state remains authoritative even
when a provider process disconnects or is restarted.

## CLI

Current top-level commands are:

```text
assemble gui
assemble frontend-info
assemble release-health
assemble rolling-restart
assemble api-call
assemble persona
assemble room
```

Run `assemble <command> --help` for command-specific options.

## Verification

```bash
pytest -q
npm --prefix frontend run build
npm --prefix frontend test -- --run
make architecture-check
```

The current product and architecture map starts at
`docs/product/CURRENT_SYSTEM.md`. GUI ownership is documented in
`docs/product/GUI_COMPOSITION.md`.
