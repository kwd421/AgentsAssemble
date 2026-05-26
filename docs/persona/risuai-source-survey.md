# RisuAI Source Survey For Character Runtime

This survey records the RisuAI behavior AgentsAssemble is allowed to adapt for
character identity. It is source-grounded so future work does not rely on chat
memory or vague "Risu-like" wording.

Survey date: 2026-05-26

RisuAI source snapshot:

- Repository: <https://github.com/kwaroran/RisuAI>
- Local path used for inspection: `/tmp/risuai-src`
- Commit: `fc7811d548784deb6db2f6946a19a5b2d7fe50be`

Character Card V3 reference:

- Spec: <https://github.com/kwaroran/character-card-spec-v3/blob/main/SPEC_V3.md>

## Files Inspected

- `src/ts/characterCards.ts`
- `src/ts/process/lorebook.svelte.ts`
- `src/ts/process/index.svelte.ts`
- `src/ts/process/group.ts`
- `src/ts/process/modules.ts`
- `src/ts/storage/database.svelte.ts`
- `src/ts/process/exampleMessages.ts`
- `src/ts/process/prompt.ts`

## Import Pipeline

`characterCards.ts` is the main import boundary.

JSON input is parsed first as a character-card spec object. If that fails, RisuAI
falls back to older off-spec Tavern-like shapes when enough character fields are
present.

`.charx`, `.jpg`, and `.jpeg` share the `CharXImporter` path. The importer must
produce `cardData`; RisuAI then requires `card.spec === "chara_card_v3"`. If the
CHARX includes module data, RisuAI reads it as a Risu module and copies module
trigger scripts and regex/custom scripts into `data.extensions.risuai`. Module
lorebook data can override the card lorebook for that import.

PNG/APNG import scans text chunks:

- `chara` for older card payloads.
- `ccv3` for Character Card V3.
- `chara-ext-asset_*` for embedded assets.

When both `chara` and `ccv3` are present, RisuAI prefers `ccv3`, matching the
CCv3 spec. It parses the selected chunk as base64 JSON and accepts
`chara_card_v2` or `chara_card_v3`. It also has an older encrypted `rcc||` path;
AgentsAssemble v1 should reject unsupported encrypted/private forms rather than
guessing.

Realm imports use `https://realm.risuai.net/api/v1/download/dynamic/<id>?cors=true`
with `x-risu-api-version: 4`. The response can be PNG, ZIP/CHARX, or JSON with
a `card` object and `img` reference. RisuAI records the Realm id in
`data.extensions.risuRealmImportId` before importing.

AgentsAssemble implication:

- Support CCv3 JSON, CCv3 PNG, CHARX, and Risu native `.risum`.
- Preserve unsupported or execution-shaped fields instead of deleting them.
- Do not fetch Realm cards implicitly in v1; import from local files or explicit
  downloaded artifacts first.

## Character Field Mapping

RisuAI maps card fields into its internal `character` model roughly as follows:

| Source field | RisuAI internal field | AgentsAssemble target |
| --- | --- | --- |
| `data.name` | `name` | `PersonaCard.display_name` |
| `data.description` | `desc` | `description` |
| `data.system_prompt` | `systemPrompt` | `system_prompt` |
| `data.personality` | `personality` | `personality` |
| `data.scenario` | `scenario` | `scenario` |
| `data.first_mes` | `firstMessage` | `first_message` |
| `data.alternate_greetings` | `alternateGreetings` | `alternate_greetings` |
| `data.mes_example` | `exampleMessage` | `example_messages` |
| `data.creator_notes` | `creatorNotes` | metadata/extra, not prompt by default |
| `data.tags` | `tags` | `tags` |
| `data.creator` | `creator` | metadata/extra |
| `data.character_version` | `characterVersion` | metadata/extra |
| `data.post_history_instructions` | `replaceGlobalNote` | `post_history_instructions` |
| `data.character_book` | `globalLore` + `loreSettings` | normalized lorebook + settings |
| `data.assets` | icon/emotion/additional/CC assets | stored assets + metadata |
| `data.nickname` | `nickname` | variable replacement override |
| `data.group_only_greetings` | `group_only_greetings` | preserved for group harness |
| `data.source` | `source` | source metadata |
| `data.creation_date` / `modification_date` | same names | source metadata |

RisuAI also preserves application-specific `extensions`, especially
`extensions.risuai`. AgentsAssemble should preserve those payloads in
`ignored_payloads` or `extra` unless a field is intentionally normalized.

## `.risum` Module Format

`process/modules.ts` defines Risu module import/export.

The binary shape is:

1. Magic byte `111`.
2. Version byte `0`.
3. Little-endian 4-byte length.
4. RPack-encoded JSON payload with `{ "type": "risuModule", "module": ... }`.
5. Zero or more asset records:
   - marker byte `1`.
   - little-endian 4-byte length.
   - RPack-encoded asset bytes.
6. EOF marker byte `0`.

Known module fields include:

- `name`
- `description`
- `lorebook`
- `regex`
- `cjs`
- `trigger`
- `id`
- `lowLevelAccess`
- `hideIcon`
- `backgroundEmbedding`
- `assets`
- `namespace`
- `customModuleToggle`
- `mcp`

AgentsAssemble v1 must parse and preserve module lore/assets, but it must not
execute `regex`, `cjs`, `trigger`, `mcp`, or `lowLevelAccess` behavior.

## Lorebook Activation

`process/lorebook.svelte.ts` builds active lore from:

- character global lore.
- current chat local lore.
- module lorebooks.

Important settings:

- `scanDepth`
- `tokenBudget`
- `recursiveScanning`
- full-word matching from `extensions.risu_fullWordMatching`

Matching searches recent messages. It can also search recursively activated lore
unless the entry opts out. Literal matching lowercases text, strips Risu comment
macros, and either checks whole words or removes spaces for partial matching.
Regex matching is accepted only for keys that look like slash-delimited regex.
Invalid regex means no match.

RisuAI compiles card-provided regex keys directly while scanning lore. That is
source behavior, not the AgentsAssemble v1 safety policy. AgentsAssemble v1
should preserve regex lore entries and count them as ignored runtime features
unless a later bounded regex engine is added with explicit pattern length, scan
text length, timeout, and pathological-pattern tests.

Activation records carry:

- depth/position.
- role: `system`, `user`, or `assistant`.
- insertion order.
- approximate token cost.
- priority.
- source/comment.
- optional injection instruction.

RisuAI supports many content decorators. The v1 AgentsAssemble scanner should
prioritize the stable non-execution subset:

- `@@activate_only_after`
- `@@activate_only_every`
- `@@keep_activate_after_match`
- `@@dont_activate_after_match`
- `@@depth`
- `@@reverse_depth`
- `@@role`
- `@@scan_depth`
- `@@is_greeting`
- `@@position`
- `@@additional_keys`
- `@@exclude_keys`
- `@@exclude_keys_all`
- `@@match_full_word`
- `@@match_partial_word`
- `@@activate`
- `@@dont_activate`
- `@@probability`
- `@@priority`
- `@@unrecursive`
- `@@recursive`
- `@@no_recursive_search`

Execution-like or output-rewrite-like decorators such as `inject_lore`,
`inject_at`, `inject_replace`, and `inject_prepend` should be preserved in
metadata first. They can become a later explicit prompt-template feature, but
they are not needed for v1 character identity.

Token budget behavior in RisuAI is two-stage: active lore is first sorted by
priority for budget trimming, then sorted back by insertion order for prompt
placement. AgentsAssemble can approximate token cost with character length in v1
as long as tests prove deterministic ordering and trimming.

## Prompt Assembly

`process/index.svelte.ts` assembles prompt buckets named:

- `main`
- `description`
- `personaPrompt`
- `chats`
- `lastChat`
- `jailbreak`
- `lorebook`
- `globalNote`
- `authorNote`
- `postEverything`

The observed default formatting order in `storage/database.svelte.ts` is:

```text
main -> description -> personaPrompt -> chats -> lastChat -> jailbreak -> lorebook -> globalNote -> authorNote
```

RisuAI pushes `postEverything` after that ordered list. Example messages and
the first message or selected alternate greeting are built into the chat bucket
before final formatting. Depth prompts can be spliced into the chat bucket, and
trigger output can add to `lastChat` or `postEverything`.

The exact RisuAI observed order is therefore:

1. Main/system prompt, including card `system_prompt` with an original-prompt
   fallback.
2. Description block from `description`, additional info, `personality`, and
   `scenario`.
3. User persona prompt if enabled.
4. Chat bucket, which includes examples, start marker, first/alternate greeting,
   chat history, and depth-inserted prompts.
5. Last-chat bucket.
6. Jailbreak bucket when enabled.
7. Lorebook bucket.
8. Global-note/post-history bucket.
9. Author-note bucket.
10. Post-everything bucket.

AgentsAssemble does not need to copy that bucket engine exactly. Its v1 prompt
assembler may choose a simpler order, but docs and tests must label it as an
AgentsAssemble order rather than a RisuAI source fact.

AgentsAssemble should not copy RisuAI's entire UI prompt-template engine. The
needed v1 behavior is a deterministic assembler that creates a stable provider
envelope:

- card system/main instruction.
- character description/personality/scenario.
- activated lore.
- examples and greeting style.
- room history/diff.
- final room instruction for one visible message.

## Group Chat Harness

RisuAI group chat is not only a prompt template. `process/group.ts` chooses
speakers by:

- first prioritizing characters named in the latest user input.
- then shuffling active characters.
- then including each remaining character by talkness probability.
- ensuring at least one speaker.
- excluding the character that just spoke.

`process/index.svelte.ts` then calls `sendChat` for each chosen character and
adds a group-only instruction to write only as the active character.

AgentsAssemble implication:

- Do not make the room dictate every turn forever.
- For future Play Mode group chat, choose eligible speakers from room state,
  mentions, cooldown, and talkativeness.
- Each selected agent should still use its own provider/session context and
  room diff.

## Variables And Macros

RisuAI's parser is broad. It includes simple identity macros such as `{{char}}`,
`{{bot}}`, and `{{user}}`, history macros such as previous/last messages,
persistent and temporary chat variables, arithmetic helpers, conditionals,
loops, random/pick/roll-style helpers, asset/source macros, buttons/triggers,
and block functions.

AgentsAssemble v1 should not try to execute that full macro language. The
supported macro subset is:

- `{{char}}`, `<char>`, and `<bot>` -> character nickname if present, otherwise
  display name.
- `{{user}}` -> configured persona user label, defaulting to `User`.
- `{{persona}}` -> optional operator-provided user persona text.
- `{{slot::<name>}}` -> explicit `persona_variables` value for that key.

All other macros are preserved as raw card text but not executed. That includes
state-mutating variables, random helpers, history lookups, buttons, triggers,
asset/source expansion, HTML/display helpers, and custom block functions. The
artifact contract should flag unreplaced macro tokens when they appear in
ArtifactSurface outputs.

## Features Not Executed In v1

These RisuAI features must be preserved as data but not run:

- regex replacement scripts.
- trigger scripts.
- CJS/custom JavaScript.
- module MCP declarations.
- low-level access.
- background HTML.
- virtual/script fields.
- arbitrary asset prompt execution.
- output rewriting hooks.
- macros outside the v1 identity-variable subset.

This is not censorship and not card-content stripping. Raw card text, including
adult or sensitive lore, may be preserved in local card storage. The boundary is
execution and public summary exposure: imported code-like behavior does not run,
and safe summaries do not print raw lore bodies.

## Implementation Rule

AgentsAssemble character runtime should be "RisuAI-informed", not a wholesale
RisuAI clone. The shared contract is:

- import cards losslessly enough for later export/review.
- activate lore deterministically.
- assemble a character prompt envelope predictably.
- keep the provider-owned session responsible for private context.
- keep Play Mode and Work Mode official artifacts separated.
- preserve execution-shaped data without executing it.
