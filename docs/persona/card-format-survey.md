# Character Card And Risu Module Format Survey

This document maps external character formats into the AgentsAssemble
`PersonaCard` identity layer. It is a phase-0 mapping document; implementation
must keep the lossless-preservation promises here or explicitly update this
file.

## Supported v1 Inputs

v1 target formats:

- Character Card V3 JSON.
- Character Card V3 PNG/APNG with `ccv3` chunk.
- CHARX (`.charx`) zip container.
- RisuAI native module (`.risum`).

Cheap compatibility target:

- Character Card V2 when it appears through the same PNG/text paths and can be
  normalized without special encrypted/private flows.

Out of v1 scope:

- encrypted `rcc||` cards.
- live Realm fetching by id.
- executing imported scripts or MCP declarations.
- browser-side provider/API secrets.
- executing the full RisuAI macro/script language.
- executing card-provided regex lore matching.

## PersonaCard Target Shape

The current `agentsassemble.persona_cards.PersonaCard` is already enough for
the first `.risum` slice, but v1 character runtime needs these additional
normalized fields:

| Target field | Meaning |
| --- | --- |
| `id` | stable local persona id, safe for paths |
| `display_name` | character/card display name |
| `description` | card description/body |
| `system_prompt` | card system/main prompt |
| `personality` | speech/personality traits |
| `scenario` | world/current situation |
| `first_message` | default opening/greeting |
| `alternate_greetings` | alternate single-chat greetings |
| `group_only_greetings` | group-chat-only greetings |
| `example_messages` | example dialogue |
| `post_history_instructions` | post-history/global-note style instruction |
| `creator_notes` | metadata, not prompt by default |
| `tags` | safe tags |
| `talkativeness` | group speaker selection weight |
| `lorebook` | normalized `PersonaLoreEntry` list |
| `lore_settings` | scan depth, budget, recursion, full-word matching |
| `assets` | stored local asset files plus source metadata |
| `source` | import source, card spec, hash, Realm id/source ids |
| `ignored_features` | counts of preserved-but-not-executed features |
| `ignored_payloads` | raw preserved unsupported payloads |
| `extra` | lossless card/module fields not otherwise normalized |

Raw text bodies are allowed in local `card.json`. Safe API/CLI summaries must
use counts, lengths, ids, hashes, and labels instead of raw lore/description
bodies.

## CCv3 Mapping

CCv3 requires `spec: "chara_card_v3"` and a `data` object. The CCv3 spec says
future/unknown fields should be ignored for operation but may be saved for safe
export, so AgentsAssemble should preserve unknown fields under `extra`.

| CCv3 field | PersonaCard field |
| --- | --- |
| `data.name` | `display_name` |
| `data.description` | `description` |
| `data.system_prompt` | `system_prompt` |
| `data.personality` | `personality` |
| `data.scenario` | `scenario` |
| `data.first_mes` | `first_message` |
| `data.alternate_greetings` | `alternate_greetings` |
| `data.group_only_greetings` | `group_only_greetings` |
| `data.mes_example` | `example_messages` |
| `data.post_history_instructions` | `post_history_instructions` |
| `data.creator_notes` | `creator_notes` |
| `data.tags` | `tags` |
| `data.creator` | `extra.creator` |
| `data.character_version` | `extra.character_version` |
| `data.nickname` | `extra.nickname`, used for `{{char}}` if set |
| `data.source` | `source.card_source` |
| `data.creation_date` | `source.creation_date` |
| `data.modification_date` | `source.modification_date` |
| `data.character_book` | `lorebook` + `lore_settings` |
| `data.assets` | `assets` |
| `data.extensions` | normalized known Risu fields plus `extra.extensions` |

## PNG/APNG Mapping

CCv3 PNG handling should follow the spec and RisuAI behavior:

- Read `ccv3` text chunk as base64-encoded JSON.
- If both `chara` and `ccv3` exist, prefer `ccv3`.
- Preserve old `chara` payload as compatibility/source evidence when useful.
- Import `chara-ext-asset_:{path}` / `chara-ext-asset_<path>` chunks as asset
  payloads accessible to `__asset:<path>` references.

The source image itself should be preserved under the persona directory when
`preserve_source` is enabled.

## CHARX Mapping

CHARX is a zip container. The CCv3 spec requires a root `card.json`; embedded
assets are addressed as `embeded://path/to/asset.png` (spelled `embeded`, not
`embedded`).

AgentsAssemble import should:

- require a readable root `card.json`.
- require `card.spec == "chara_card_v3"` for v1.
- reject encrypted/corrupted zip files clearly.
- copy embedded assets referenced by card assets into the persona asset store.
- preserve unrecognized root application files under ignored/extra metadata
  where practical, without executing them.

RisuAI CHARX can also carry module data. In the inspected RisuAI path, decodable
module lorebook data unconditionally overrides card lorebook data when present.
AgentsAssemble v1 should follow that import result for Risu compatibility while
also preserving source evidence that the lore came from embedded module data.

## Risu `.risum` Mapping

`.risum` is a Risu module, not a full CCv3 card. It can still become a
PersonaCard overlay because it carries name, description, lorebook, assets, and
execution-shaped behavior.

| Risu module field | PersonaCard field |
| --- | --- |
| `id` | `id` |
| `name` | `display_name` |
| `description` | `description` |
| `lorebook` | `lorebook` |
| `assets` | `assets[].metadata` plus copied asset payloads |
| `regex` | `ignored_payloads.regex` |
| `trigger` | `ignored_payloads.trigger` |
| `cjs` | `ignored_payloads.cjs` |
| `lowLevelAccess` | `ignored_payloads.lowLevelAccess` |
| `customModuleToggle` | `ignored_payloads.customModuleToggle` |
| `mcp` | `ignored_payloads.mcp` |
| `backgroundEmbedding` | `ignored_payloads.backgroundEmbedding` |
| `namespace` | `extra.namespace` |

If a `.risum` module lacks full card fields such as `system_prompt` or
`scenario`, those fields should remain empty rather than fabricated.

## Lore Entry Mapping

Target `PersonaLoreEntry` should preserve:

| Input field | Target field |
| --- | --- |
| `keys` or `key` | `key` / `keys` |
| `secondary_keys` or `secondkey` | `secondkey` / `secondary_keys` |
| `content` | `content` |
| `comment` / `name` | `comment` |
| `constant` / `alwaysActive` | `always_active` |
| `selective` | `selective` |
| `use_regex` / `useRegex` | `use_regex` |
| `insertion_order` / `insertorder` | `insert_order` |
| `case_sensitive` / `risu_case_sensitive` | `case_sensitive` |
| `enabled` | `enabled` |
| `priority` | `priority` |
| `position` / decorators | `position` |
| role decorators | `role` |
| decorator data | parsed runtime fields + raw decorator metadata |
| unknown fields/extensions | `extra` |

CCv3 says disabled entries must not activate. AgentsAssemble should preserve
disabled entries but skip them in scanning.

`use_regex` entries should be preserved but skipped in v1 scanning. A later
regex scanner needs explicit bounds for pattern length, scanned text size,
runtime timeout or safe-regex validation, and tests for catastrophic backtracking
patterns before it can be enabled.

## Risu Extension Migration

RisuAI migrates some extension fields into decorators before storing lore:

- probability extension -> `@@probability`.
- depth/role extension -> `@@depth` and `@@role`.
- selective logic -> additional/exclusion decorators.
- delay -> `@@activate_only_after`.
- whole-word option -> `@@match_full_word` or `@@match_partial_word`.

AgentsAssemble can either keep those fields structured or rewrite them into the
same decorator text. The runtime must be deterministic either way. The
preferred v1 route is:

- preserve original extension payload under `extra`.
- normalize common runtime semantics into explicit fields.
- keep the content text body intact except for safe decorator parsing at render
  time.

## Macro Mapping

RisuAI supports a broad parser/macro surface. AgentsAssemble v1 supports only
identity-variable replacement:

- `{{char}}`, `<char>`, and `<bot>`.
- `{{user}}`.
- `{{persona}}`.
- `{{slot::<name>}}`.

Everything else is preserved as imported text and ignored as runtime behavior.
This includes variables, math, random helpers, history lookup, buttons,
triggers, asset/source expansion, HTML/display helpers, loops, conditionals, and
custom block functions.

## Asset URI Policy

CCv3 allows several asset URI shapes. AgentsAssemble v1 should keep import local
and bounded:

- `ccdefault:` may point to the imported PNG/APNG source image.
- `embeded://...` may resolve only to files inside the CHARX zip after path
  normalization. Absolute paths, `..`, empty path segments, and symlink-like
  escapes are rejected.
- `__asset:<path>` may resolve only to embedded PNG text-chunk assets from the
  same import.
- `data:` assets may be copied only under an explicit size cap.
- `http:` and `https:` asset URIs are preserved as source metadata but not
  fetched in v1.
- `file:`, `ftp:`, and unknown schemes are preserved as ignored metadata and
  not opened.

Safe summaries expose asset counts, type labels, sizes, and hashes rather than
raw original URIs when those URIs could reveal private paths or remote tracking
URLs.

## Safe Summary Policy

Safe summaries may include:

- persona id.
- display name.
- source kind/spec.
- counts of lore/assets/ignored features.
- tags.
- content lengths.
- hashes.
- current character-mode state.

Safe summaries must not include:

- raw lore body.
- raw description/body/system prompt.
- raw NSFW/adult content.
- script bodies.
- trigger/CJS code.
- local secret paths beyond allowlisted source evidence.
- provider prompts or provider output.
- raw remote asset URLs or local absolute asset paths.

This keeps user-provided local cards intact while avoiding accidental leaks in
roster, health, archive metadata, or CLI JSON intended for operators.
