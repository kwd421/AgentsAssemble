"""Session-local Pi tools for bounded workspace reads and RoomPortal access."""

from __future__ import annotations

import json
from pathlib import Path


PI_READ_ONLY_TOOLS = (
    "read",
    "grep",
    "find",
    "ls",
    "read_discussion",
    "list_participants",
    "publish_message",
    "decline_to_speak",
    "create_vote",
    "cast_vote",
    "vote_summary",
)


def write_pi_room_extension(
    path: str | Path,
    *,
    workspace: str | Path,
    room_helper: str | Path,
) -> Path:
    """Write the isolated Pi extension used by one Agent Session."""

    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    source = _extension_source(
        workspace=Path(workspace).expanduser().resolve(),
        room_helper=Path(room_helper).expanduser().resolve(),
    )
    target.write_text(source, encoding="utf-8")
    target.chmod(0o600)
    return target


def _extension_source(*, workspace: Path, room_helper: Path) -> str:
    root = json.dumps(str(workspace))
    helper = json.dumps(str(room_helper))
    return f'''import {{ execFile }} from "node:child_process";
import {{ realpath }} from "node:fs/promises";
import {{ isAbsolute, relative, resolve }} from "node:path";
import {{ promisify }} from "node:util";
import {{ Type }} from "@earendil-works/pi-ai";
import type {{ ExtensionAPI }} from "@earendil-works/pi-coding-agent";

const workspace = {root};
const roomHelper = {helper};
const runFile = promisify(execFile);

async function room(args: string[]): Promise<string> {{
  const result = await runFile(roomHelper, args, {{
    encoding: "utf8",
    timeout: 10000,
    maxBuffer: 131072,
  }});
  return String(result.stdout || "").trim();
}}

function result(text: string) {{
  return {{ content: [{{ type: "text", text }}], details: {{}} }};
}}

export default function (pi: ExtensionAPI) {{
  pi.on("tool_call", async (event) => {{
    if (!["read", "grep", "find", "ls"].includes(event.toolName)) return undefined;
    const input = event.input as Record<string, unknown>;
    const requested = String(input.path || ".");
    const candidate = isAbsolute(requested) ? requested : resolve(workspace, requested);
    let canonical: string;
    try {{
      canonical = await realpath(candidate);
    }} catch {{
      return undefined;
    }}
    const remainder = relative(workspace, canonical);
    if (remainder === "" || (!remainder.startsWith("..") && !isAbsolute(remainder))) {{
      return undefined;
    }}
    return {{ block: true, reason: "Path is outside the assigned workspace." }};
  }});

  pi.registerTool({{
    name: "read_discussion",
    label: "Read room discussion",
    description: "Read the bounded private room mirror for the active observation.",
    parameters: Type.Object({{}}),
    execute: async () => result(await room(["read"])),
  }});
  pi.registerTool({{
    name: "list_participants",
    label: "List room participants",
    description: "List participants in the current room.",
    parameters: Type.Object({{}}),
    execute: async () => result(await room(["participants"])),
  }});
  pi.registerTool({{
    name: "publish_message",
    label: "Publish room message",
    description: "Publish one message to the shared room.",
    parameters: Type.Object({{
      content: Type.String(),
      next_agent_id: Type.Optional(Type.String()),
    }}),
    execute: async (_id, params) => {{
      const args = params.next_agent_id
        ? ["speak-to", params.next_agent_id, params.content]
        : ["speak", params.content];
      await room(args);
      return result("Published to the shared room.");
    }},
  }});
  pi.registerTool({{
    name: "decline_to_speak",
    label: "Decline room turn",
    description: "End this room wake without publishing a message.",
    parameters: Type.Object({{ reason_code: Type.String() }}),
    execute: async (_id, params) => result(await room(["decline", params.reason_code])),
  }});
  pi.registerTool({{
    name: "create_vote",
    label: "Create room vote",
    description: "Create a structured room vote.",
    parameters: Type.Object({{
      question: Type.String(),
      options: Type.Array(Type.String()),
      duration_seconds: Type.Optional(Type.Integer()),
    }}),
    execute: async (_id, params) => result(await room([
      "vote-create", params.question, JSON.stringify(params.options),
      String(params.duration_seconds || 0),
    ])),
  }});
  pi.registerTool({{
    name: "cast_vote",
    label: "Cast room vote",
    description: "Cast a vote in a structured room vote.",
    parameters: Type.Object({{ vote_id: Type.String(), choice: Type.String() }}),
    execute: async (_id, params) => result(await room(["vote-cast", params.vote_id, params.choice])),
  }});
  pi.registerTool({{
    name: "vote_summary",
    label: "Read vote summary",
    description: "Read the bounded tally for a structured room vote.",
    parameters: Type.Object({{ vote_id: Type.String() }}),
    execute: async (_id, params) => result(await room(["vote-summary", params.vote_id])),
  }});
}}
'''


__all__ = ["PI_READ_ONLY_TOOLS", "write_pi_room_extension"]
