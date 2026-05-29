# AgentsAssemble Character Runtime Integration Surface

This document defines where RisuAI-style character identity belongs inside
AgentsAssemble, and where it must not leak.

## Product Boundary

Character runtime is an agent identity layer. It is not:

- a provider approval layer.
- a permission grant.
- a hidden moderator that rewrites every room event.
- an official Work Mode evidence source by itself.
- a script execution sandbox.

The room remains a room. Agents own their provider-private context. The room
owns visible events, official artifacts, shared memory, cursors, snapshots, and
safe metadata.

## Modes

Character Mode has three states:

| Mode | Behavior |
| --- | --- |
| `off` | No persona prompt context is added. Imported cards remain stored. |
| `on` | Character harness applies to Play Mode and character chat surfaces only. It does not authorize official Work Mode replies. |
| `work_speech_only` | Work Mode speech/review style may use an explicit safe style capsule, while artifacts use the professional artifact contract. It does not inject raw card lore, examples, greetings, or scenario into official records. |

The mode is per agent per meeting. A future frontend can expose it as an
operator toggle beside the roster row, but the backend state must be correct
first.

## Storage

Local card store:

```text
.agentsassemble/personas/<persona-id>/
  card.json
  source.<ext>
  assets/
```

Meeting snapshot:

```json
{
  "character_mode": {
    "agents": [
      {
        "agent_id": "yanagi",
        "card_id": "yanagi",
        "card_hash": "sha256:...",
        "mode": "on",
        "first_message_index": -1,
        "persona_variables": {},
        "ignored_features": {"trigger": 2},
        "source_path": ".agentsassemble/personas/yanagi/card.json"
      }
    ]
  }
}
```

The snapshot is the meeting-level truth for what persona context was active.
The card store is the durable local source for reusable cards. Meeting records
should never need to print raw card bodies to prove that a character mode was
active.

## Agent Binding Fields

Add these optional fields to `AgentBinding` and config parsing:

- `persona_card_id`
- `persona_path`
- `character_mode`
- `first_message_index`
- `persona_variables`

`persona_id` in existing live-agent configs should remain accepted as an alias
for local persona lookup, but the meeting/binding surface should use
`persona_card_id` when possible.

## Speech Surfaces

Speech surfaces may use character prompt context:

- lobby messages.
- Play Mode flow messages.
- side chat.

Official Work Mode speech is narrower:

- `on` does not apply to official Work Mode speech.
- `work_speech_only` may apply only a safe style capsule: display name,
  operator-approved style notes, collaboration attitude, and role label.
- The capsule is stored as `PersonaCard.speech_style` with safe fields such as
  `tone`, `cadence`, `collaboration_style`, `role_label`, `do`, and `do_not`;
  it is not generated from raw lore, scenario, examples, or card body text.
- It must not include raw card lore, examples, alternate greetings, scenario,
  NSFW/adult body text, or ignored runtime payloads.
- Stateful provider transports that used full Play Mode persona context should
  not answer official turns from that same provider context unless the operator
  explicitly starts a separate Work Mode session or disables character context.

Play/character-chat speech should preserve character style, relationship, world
assumptions, and role attitude. Work Mode `work_speech_only` preserves only the
safe style capsule described above.

## Artifact Surfaces

Artifact surfaces must not use character prose as the governing style:

- code.
- documentation.
- commit messages.
- `decision.md`.
- `transcript.md` synthesis.
- `action_items.md`.
- `shared_memory/*`.
- return-packet body.
- return-packet summaries.
- provider config files.
- machine-readable JSON artifacts.

Artifact surfaces use a professional artifact contract instead:

- direct technical writing.
- no roleplay narration.
- no character catchphrases.
- no unreplaced card variables.
- no NSFW/adult card markers.
- no hidden card lore text unless explicitly promoted by the operator.

## Harness Call Sites

Initial v1 call sites:

- Play Mode `flow` prompt assembly in `live_agent_runner.py`.
- live-agent persona smoke prompts.
- `persona render` CLI for inspection.
- `persona scan` CLI for active lore inspection.

Later call sites:

- MCP participant tool guidance.
- self-service join brief.
- Work Mode speech envelopes.
- richer frontend roster toggle and badge.

Out of scope for v1:

- provider-native prompt-template engines.
- executing Risu triggers/scripts.
- browser-side Realm downloading.
- automatic promotion of Play Mode persona context to Work Mode.

## Prompt Assembly Contract

RisuAI's observed prompt order is documented in
`docs/persona/risuai-source-survey.md`. AgentsAssemble v1 intentionally uses a
smaller room-oriented order for Play Mode character speech:

1. room/provider safety envelope and one-message output rule.
2. card system/main prompt.
3. description/personality/scenario.
4. activated lorebook entries.
5. example messages.
6. first message or selected alternate greeting.
7. recent room history/diff.
8. post-history instruction.
9. final surface-specific instruction.

For `work_speech_only`, do not include that full character block. Use only a
safe style capsule and then add the professional boundary:

```text
Keep the agent's speech style and collaboration attitude, but do not let
character lore or roleplay style alter code, docs, decisions, or other
artifacts.
```

For ArtifactSurface, do not include the character blocks. Include only safe
identity metadata if needed for attribution.

## Lore Scanner Contract

The scanner takes:

- a `PersonaCard`.
- recent room/context text.
- optional prior scanner state.
- scan depth and budget.
- first-message index.
- deterministic random seed for probability decorators.

It returns:

- active entries.
- safe match metadata.
- updated scanner state for sticky/cooldown entries.
- ignored feature names.

It must not:

- execute regex replacement scripts.
- execute card-provided regex lore matching in v1.
- execute trigger scripts.
- evaluate CJS.
- call MCP endpoints from card/module data.
- expose raw inactive lore in safe metadata.

Regex lore entries are preserved and ignored in v1. Enabling them later requires
explicit ReDoS bounds: pattern length cap, scanned text cap, runtime timeout or
safe-regex validation, and pathological-pattern tests.

## Variable And Macro Contract

Supported v1 replacements:

- `{{char}}`, `<char>`, and `<bot>`.
- `{{user}}`.
- `{{persona}}`.
- `{{slot::<name>}}`.

Unsupported RisuAI macros are preserved but not executed. This includes
stateful variables, math/random helpers, conditionals, loops, history lookups,
buttons/triggers, asset/source expansion, HTML/display helpers, and custom block
functions.

## Work Mode Leak Guard

Add `persona_artifact_contract.py` with a small scanner for artifact text.

It should flag:

- unreplaced variables such as `{{char}}`, `{{user}}`, `{{slot::*}}`.
- roleplay narration markers in artifact bodies.
- character-mode badges or persona prose in commit/document/decision text.
- NSFW/adult marker labels from test cards.
- ignored execution feature names being emitted as instructions.
- raw card lore appearing in official artifacts.

The guard should produce safe violation events/counts, not dump the offending
raw card text into public metadata.

## GUI/API Surface

Safe visible fields:

- `Character: ON`
- `Character: Work speech`
- `Character: OFF`
- card id or display name.
- lore count.
- ignored feature counts.
- source kind.

Do not expose raw lore, descriptions, scripts, triggers, CJS, MCP declarations,
or low-level access payloads through roster, health, archive summaries, or GUI
status panels.

## Verification Gates

Phase 0 docs must pass xHigh review before implementation.

Implementation must add tests for:

- CCv3 JSON import.
- CCv3 PNG import.
- CHARX import.
- `.risum` import preservation.
- scanner activation order and budget.
- prompt block order.
- `off`, `on`, and `work_speech_only` meeting snapshots.
- artifact leak detection.
- safe summary redaction.

Real Grok/Kiro/Codex provider runs are not required for the first runtime
verification. Use fake providers for deterministic proof, then run real provider
smokes only after explicit operator approval.
