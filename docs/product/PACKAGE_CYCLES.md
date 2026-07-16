# Package Import Cycles

Status: generated architecture report

Generator: `python3 scripts/check_package_architecture.py --write-cycle-report`

Source fingerprint: `d90bf3a4111dc800`

- Current import cycles: 1
- Grandfathered exact cycles: 1
- New cycles: 0

An exact historical cycle may disappear without updating the baseline. Any
changed or newly introduced cycle fails the architecture gate. A cycle that
moves into a target package is therefore not silently grandfathered.

## Current Cycles

- **grandfathered**: `agentsassemble.antigravity_resident` -> `agentsassemble.codex_resident` -> `agentsassemble.cursor_resident` -> `agentsassemble.grok_resident` -> `agentsassemble.hermes_resident` -> `agentsassemble.kiro_resident` -> `agentsassemble.live_agent_runner`
