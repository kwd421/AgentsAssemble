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
        send({"jsonrpc": "2.0", "id": request_id, "result": {"stopReason": "end_turn"}})
        continue
    if text == "recall-after-restart":
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
