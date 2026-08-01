# Grok room-tool wrapper: measured evidence

Collected 2026-08-01 from stored ACP `updates.jsonl` payloads, to correct
commit 17406f1a, which recorded that this could not be reproduced here.

## What the wrapper name says

| `_meta.x.ai/tool.name` | count |
| --- | ---: |
| `read_file` | 570 |
| `use_tool` | 375 |
| `write` | 196 |
| `run_terminal_command` | 120 |
| `search_tool` | 112 |
| `grep` | 14 |
| `list_dir` | 12 |
| `web_fetch` | 4 |

## What `rawInput.tool_name` says

| real tool name | count |
| --- | ---: |
| `agentsassemble_room__read_discussion` | 295 |
| `agentsassemble_room__publish_message` | 80 |

Room-tool calls carried in `rawInput.tool_name`: **375**
Calls whose wrapper name is `use_tool`: **375**

The two counts match, so every room tool call arrives through the wrapper
and is invisible to a check that reads `_meta.x.ai/tool.name`. Kimi K3's
account was correct in full; the earlier "could not reproduce" note came
from searching bridge `stderr.log` files, which are empty, instead of the
stored ACP payloads.

## Source files

17 `updates.jsonl` files under
`.agentsassemble/rooms/*/bridges/grok*/*/provider-state/sessions/`.

