# Package Import Cycles

Status: generated architecture report

Generator: `python3 scripts/check_package_architecture.py --write-cycle-report`

Source fingerprint: `68fd9e9f026c0dee`

- Current import cycles: 2
- Grandfathered exact cycles: 2
- New cycles: 0

An exact historical cycle may disappear without updating the baseline. Any
changed or newly introduced cycle fails the architecture gate. A cycle that
moves into a target package is therefore not silently grandfathered.

## Current Cycles

- **grandfathered**: `agentsassemble.gui` -> `agentsassemble.gui_observability_http` -> `agentsassemble.release_health` -> `agentsassemble.room_event_benchmark`
- **grandfathered**: `agentsassemble.antigravity_resident` -> `agentsassemble.codex_resident` -> `agentsassemble.cursor_resident` -> `agentsassemble.grok_resident` -> `agentsassemble.hermes_resident` -> `agentsassemble.kiro_resident` -> `agentsassemble.live_agent_runner`
