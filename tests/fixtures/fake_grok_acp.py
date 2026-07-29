from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from uuid import uuid4


def send(message: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(message, separators=(",", ":")) + "\n")
    sys.stdout.flush()


for index in range(3000):
    sys.stderr.write(f"WARN fake ACP diagnostic line {index}\n")
sys.stderr.flush()

state_path = Path(os.environ.get("GROK_HOME") or ".") / "fake-provider-sessions.json"
try:
    provider_sessions = json.loads(state_path.read_text(encoding="utf-8"))
except (FileNotFoundError, OSError, json.JSONDecodeError):
    provider_sessions = {}
if not isinstance(provider_sessions, dict):
    provider_sessions = {}
session_id = ""
room_mcp_configured = False
filesystem_disabled = False


def save_provider_sessions() -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(provider_sessions, sort_keys=True), encoding="utf-8")
for line in sys.stdin:
    try:
        request = json.loads(line)
    except json.JSONDecodeError:
        continue
    request_id = request.get("id")
    method = request.get("method")
    params = request.get("params") if isinstance(request.get("params"), dict) else {}
    if method == "initialize":
        client_capabilities = (
            params.get("clientCapabilities")
            if isinstance(params.get("clientCapabilities"), dict)
            else {}
        )
        filesystem = (
            client_capabilities.get("fs")
            if isinstance(client_capabilities.get("fs"), dict)
            else {}
        )
        filesystem_disabled = (
            filesystem.get("readTextFile") is False
            and filesystem.get("writeTextFile") is False
        )
        send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "protocolVersion": 1,
                    "agentCapabilities": {"loadSession": True},
                    "_meta": {"modelState": {"currentModelId": "fake-grok"}},
                },
            }
        )
        continue
    if method == "session/new":
        mcp_servers = params.get("mcpServers")
        room_mcp_configured = any(
            isinstance(server, dict)
            and server.get("name") == "agentsassemble_room"
            and isinstance(server.get("command"), str)
            and bool(server.get("args"))
            for server in (mcp_servers if isinstance(mcp_servers, list) else [])
        )
        session_id = f"fake-{uuid4().hex}"
        provider_sessions[session_id] = {"last_text": ""}
        save_provider_sessions()
        send({"jsonrpc": "2.0", "id": request_id, "result": {"sessionId": session_id}})
        if os.environ.get("FAKE_GROK_ACP_YOLO_ON_START") == "1":
            send(
                {
                    "jsonrpc": "2.0",
                    "method": "_x.ai/sessions/changed",
                    "params": {"upserted": [{"sessionId": session_id, "yolo": True}]},
                }
            )
        continue
    if method == "session/load":
        requested_session_id = str(params.get("sessionId") or "")
        if os.environ.get("FAKE_GROK_ACP_LOAD_FAIL") == "1" or requested_session_id not in provider_sessions:
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32000, "message": "stored session is unavailable"},
                }
            )
        else:
            session_id = requested_session_id
            send({"jsonrpc": "2.0", "id": request_id, "result": {}})
        continue
    if method == "session/cancel":
        send({"jsonrpc": "2.0", "id": request_id, "result": {}})
        continue
    if method != "session/prompt":
        continue
    prompt = params.get("prompt") if isinstance(params.get("prompt"), list) else []
    text = "".join(str(block.get("text") or "") for block in prompt if isinstance(block, dict))
    send(
        {
            "jsonrpc": "2.0",
            "method": "_x.ai/sessions/changed",
            "params": {
                "upserted": [
                    {
                        "sessionId": session_id,
                        "yolo": text == "unsafe-yolo",
                        "activity": "working",
                    }
                ]
            },
        }
    )
    if text == "unsafe-yolo":
        send({"jsonrpc": "2.0", "id": request_id, "result": {"stopReason": "end_turn"}})
        continue
    if text == "quota":
        sys.stderr.write("ERROR 402 Payment Required: Grok Build usage balance exhausted\n")
        sys.stderr.flush()
        send({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32603, "message": "Internal error"}})
        continue
    if text == "exit-mid-turn":
        raise SystemExit(7)
    if text == "overflow":
        for index in range(20):
            send(
                {
                    "jsonrpc": "2.0",
                    "method": "session/update",
                    "params": {
                        "sessionId": session_id,
                        "update": {
                            "sessionUpdate": "agent_message_chunk",
                            "content": {"type": "text", "text": str(index)},
                        },
                    },
                }
            )
        send({"jsonrpc": "2.0", "id": request_id, "result": {"stopReason": "end_turn"}})
        continue
    if text == "empty-turn":
        session = provider_sessions.setdefault(session_id, {})
        empty_turn_count = int(session.get("empty_turn_count") or 0) + 1
        session["empty_turn_count"] = empty_turn_count
        save_provider_sessions()
        if empty_turn_count == 1:
            send({"jsonrpc": "2.0", "id": request_id, "result": {"stopReason": "end_turn"}})
            continue
        response = "unexpected second provider prompt"
    elif text == "recall-after-restart":
        remembered = str((provider_sessions.get(session_id) or {}).get("last_text") or "")
        response = f"recalled {remembered}"
    elif text == "permission":
        send(
            {
                "jsonrpc": "2.0",
                "id": 0,
                "method": "session/request_permission",
                "params": {
                    "sessionId": session_id,
                    "toolCall": {"toolCallId": "tool-1", "title": "write file"},
                    "options": [
                        {"optionId": "allow-once", "kind": "allow_once"},
                        {"optionId": "reject-once", "kind": "reject_once"},
                    ],
                },
            }
        )
        permission_response = json.loads(sys.stdin.readline())
        outcome = (permission_response.get("result") or {}).get("outcome")
        response = (
            "permission denied safely"
            if isinstance(outcome, dict)
            and outcome.get("outcome") == "selected"
            and outcome.get("optionId") == "reject-once"
            else "permission was not denied"
        )
    elif text == "room-mcp-permission":
        allowed = []
        for index, tool_name in enumerate(
            (
                "agentsassemble_room__read_discussion",
                "agentsassemble_room__publish_message",
            ),
            start=1,
        ):
            tool_call_id = f"room-tool-{index}"
            send(
                {
                    "jsonrpc": "2.0",
                    "method": "session/update",
                    "params": {
                        "sessionId": session_id,
                        "update": {
                            "sessionUpdate": "tool_call",
                            "toolCallId": tool_call_id,
                            "_meta": {"x.ai/tool": {"name": "use_tool"}},
                        },
                    },
                }
            )
            send(
                {
                    "jsonrpc": "2.0",
                    "method": "session/update",
                    "params": {
                        "sessionId": session_id,
                        "update": {
                            "sessionUpdate": "tool_call_update",
                            "toolCallId": tool_call_id,
                            "_meta": {"x.ai/tool": {"name": tool_name}},
                        },
                    },
                }
            )
            send(
                {
                    "jsonrpc": "2.0",
                    "id": index,
                    "method": "session/request_permission",
                    "params": {
                        "sessionId": session_id,
                        "toolCall": {"toolCallId": tool_call_id},
                        "options": [
                            {"optionId": f"allow-{index}", "kind": "allow_once"},
                            {"optionId": f"reject-{index}", "kind": "reject_once"},
                        ],
                    },
                }
            )
            permission_response = json.loads(sys.stdin.readline())
            outcome = (permission_response.get("result") or {}).get("outcome")
            allowed.append(
                isinstance(outcome, dict)
                and outcome.get("outcome") == "selected"
                and outcome.get("optionId") == f"allow-{index}"
            )
        send(
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": session_id,
                    "update": {
                        "sessionUpdate": "tool_call",
                        "toolCallId": "room-tool-spoofed",
                        "_meta": {
                            "x.ai/tool": {
                                "name": "evil_agentsassemble_room__publish_message"
                            }
                        },
                    },
                },
            }
        )
        send(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "session/request_permission",
                "params": {
                    "sessionId": session_id,
                    "toolCall": {"toolCallId": "room-tool-spoofed"},
                    "options": [
                        {"optionId": "allow-3", "kind": "allow_once"},
                        {"optionId": "reject-3", "kind": "reject_once"},
                    ],
                },
            }
        )
        permission_response = json.loads(sys.stdin.readline())
        outcome = (permission_response.get("result") or {}).get("outcome")
        spoof_rejected = (
            isinstance(outcome, dict)
            and outcome.get("outcome") == "selected"
            and outcome.get("optionId") == "reject-3"
        )
        response = (
            "room MCP permissions allowed"
            if (
                room_mcp_configured
                and filesystem_disabled
                and all(allowed)
                and spoof_rejected
            )
            else "room MCP permission contract failed"
        )
    else:
        response = f"remembered {text}"
        provider_sessions.setdefault(session_id, {})["last_text"] = text
        save_provider_sessions()
    midpoint = max(1, len(response) // 2)
    for piece in (response[:midpoint], response[midpoint:]):
        send(
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": session_id,
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": piece},
                    },
                },
            }
        )
    send(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "stopReason": "end_turn",
                "_meta": {"modelId": "fake-grok"},
            },
        }
    )
