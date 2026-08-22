"""Standalone terminal helper installed into provider-private room state."""

from __future__ import annotations

import functools
import os
import shutil
import subprocess
import sys
from pathlib import Path


@functools.lru_cache(maxsize=1)
def helper_interpreter() -> str:
    """Resolve an interpreter that remains usable inside provider sandboxes."""
    candidates = ("/usr/bin/python3", sys.executable, shutil.which("python3") or "")
    for candidate in candidates:
        if not candidate or not os.path.isabs(candidate):
            continue
        try:
            completed = subprocess.run(
                [candidate, "-c", "import json, os, pathlib, re, secrets, sys"],
                capture_output=True,
                timeout=10.0,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if completed.returncode == 0:
            return candidate
    return "/usr/bin/env python3"


def helper_script() -> str:
    """Return the helper with its shebang pinned to the resolved interpreter."""
    _, _, body = _HELPER_SCRIPT.partition("\n")
    return f"#!{helper_interpreter()}\n{body}"


_HELPER_SCRIPT = r"""#!/usr/bin/env python3
import json
import os
import re
import secrets
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parent.parent
VIEW = ROOT / "current.md"
TURN = ROOT / "turn.json"
OUTBOX = ROOT / "outbox.json"
MEDIA = ROOT / "media.json"
PARTICIPANTS = ROOT / "participants.json"
MESSAGES = ROOT / "messages.json"
ACTIVITY = ROOT / "activity.jsonl"
PLUGIN_STATE = ROOT / "activity-plugin-state.json"
PLUGIN_ACTION = ROOT / "activity-plugin-action.json"
PLUGIN_SPEECH = ROOT / "activity-plugin-speech.json"
SEARCH_REQUESTS = ROOT / "search-requests"
SEARCH_RESPONSES = ROOT / "search-responses"

def fail(message):
    print(message, file=sys.stderr)
    raise SystemExit(2)

def atomic_json(path, payload):
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
    try:
        temporary.chmod(0o600)
    except OSError:
        pass
    os.replace(temporary, path)

def audit(operation, turn_id="", details=None):
    observed_through_seq = 0
    if not turn_id:
        try:
            turn = json.loads(TURN.read_text(encoding="utf-8"))
            turn_id = str(turn.get("turn_id") or "")
            observed_through_seq = int(turn.get("input_up_to_seq") or 0)
        except (OSError, json.JSONDecodeError):
            turn_id = ""
    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "operation": operation,
        "turn_id": turn_id,
        "observed_through_seq": observed_through_seq if operation == "read" else 0,
    }
    if operation in {"roll_dice", "choose_random"}:
        payload["result_id"] = f"result-{secrets.token_hex(16)}"
    if details:
        payload["details"] = details
    with ACTIVITY.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    try:
        ACTIVITY.chmod(0o600)
    except OSError:
        pass

def require_tool(name):
    try:
        turn = json.loads(TURN.read_text(encoding="utf-8"))
        allowed = turn.get("allowed_tools")
    except (OSError, json.JSONDecodeError):
        allowed = None
    if not isinstance(allowed, list) or name not in allowed:
        fail(f"room tool {name} is unavailable for this observation")

def active_turn():
    try:
        turn = json.loads(TURN.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        fail("no room observation is active")
    turn_id = str(turn.get("turn_id") or "")
    if not turn_id:
        fail("no room observation is active")
    return turn_id

def stage_publication(payload):
    if OUTBOX.exists():
        fail("a public room action is already staged for this turn")
    atomic_json(OUTBOX, {"turn_id": active_turn(), **payload})

def clean_text(value, limit):
    return str(value or "").replace("\x00", "").strip()[:limit]

def search_exchange(operation, arguments):
    request_id = uuid4().hex
    request_path = SEARCH_REQUESTS / f"{request_id}.json"
    response_path = SEARCH_RESPONSES / f"{request_id}.json"
    SEARCH_REQUESTS.mkdir(parents=True, exist_ok=True)
    SEARCH_RESPONSES.mkdir(parents=True, exist_ok=True)
    atomic_json(request_path, {
        "request_id": request_id,
        "operation": operation,
        "arguments": arguments,
    })
    deadline = time.monotonic() + 10.0
    try:
        while time.monotonic() < deadline:
            try:
                raw = response_path.read_bytes()
            except FileNotFoundError:
                time.sleep(0.025)
                continue
            if len(raw) > 2 * 1024 * 1024:
                fail("room search response exceeded its bounded size")
            try:
                response = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                fail("room search returned an invalid response")
            if not isinstance(response, dict) or response.get("ok") is not True:
                message = clean_text(response.get("error") if isinstance(response, dict) else "", 500)
                fail(message or "room search failed")
            result = response.get("result")
            if not isinstance(result, dict):
                fail("room search returned an invalid result")
            return result
        fail("room search timed out; no result was hidden")
    finally:
        request_path.unlink(missing_ok=True)
        response_path.unlink(missing_ok=True)

def load_messages():
    try:
        values = json.loads(MESSAGES.read_text(encoding="utf-8")).get("messages", [])
    except (OSError, json.JSONDecodeError):
        return []
    return values if isinstance(values, list) else []

def find_poll(vote_id):
    for item in load_messages():
        if not isinstance(item, dict):
            continue
        if item.get("message_kind") == "vote" and str(item.get("id") or "") == vote_id:
            return item
    fail(f"vote {vote_id} was not found in the current bounded room view")

def resolve_choice(choice, options):
    cleaned = clean_text(choice, 100)
    for option in options:
        if str(option).casefold() == cleaned.casefold():
            return str(option)
    if cleaned.isdigit() and 1 <= int(cleaned) <= len(options):
        return str(options[int(cleaned) - 1])
    fail("choice must match one of the vote options")

def plugin_context(tool):
    require_tool(f"rimworld.{tool}")
    try:
        turn = json.loads(TURN.read_text(encoding="utf-8"))
        stored = json.loads(PLUGIN_STATE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        fail("activity plugin state is unavailable")
    if turn.get("activity_plugin") != "rimworld" or stored.get("plugin_id") != "rimworld":
        fail("RimWorld is not active for this observation")
    payload = stored.get("payload")
    if not isinstance(payload, dict):
        fail("activity plugin state is invalid")
    colonist_id = clean_text(turn.get("activity_plugin_colonist_id"), 64)
    if not colonist_id:
        fail("this session has no assigned colonist")
    return turn, payload, colonist_id

command = sys.argv[1] if len(sys.argv) > 1 else "help"
if command == "read":
    content = VIEW.read_text(encoding="utf-8")
    audit("read")
    sys.stdout.write(content)
elif command == "search":
    require_tool("search_messages")
    if len(sys.argv) not in {3, 4, 5}:
        fail("usage: agentsassemble-room search '<query>' [channel-id|all] [cursor]")
    query = clean_text(sys.argv[2], 200)
    if not query:
        fail("room message search requires a query")
    result = search_exchange("search_messages", {
        "query": query,
        "channel_id": clean_text(sys.argv[3], 128) if len(sys.argv) >= 4 else "all",
        "cursor": clean_text(sys.argv[4], 2048) if len(sys.argv) >= 5 else "",
    })
    audit("search_messages")
    print(json.dumps(result, ensure_ascii=False))
elif command == "search-context":
    require_tool("read_message_context")
    if len(sys.argv) != 4:
        fail("usage: agentsassemble-room search-context <channel-id> <event-id>")
    result = search_exchange("read_message_context", {
        "channel_id": clean_text(sys.argv[2], 128),
        "event_id": clean_text(sys.argv[3], 128),
    })
    audit("read_message_context")
    print(json.dumps(result, ensure_ascii=False))
elif command == "participants":
    require_tool("list_participants")
    try:
        result = json.loads(PARTICIPANTS.read_text(encoding="utf-8")).get("participants", [])
    except (OSError, json.JSONDecodeError):
        result = []
    audit("list_participants")
    print(json.dumps(result, ensure_ascii=False))
elif command in {"speak", "speak-to"}:
    target_agent_id = ""
    content_start = 2
    if command == "speak-to":
        if len(sys.argv) < 4 or re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", sys.argv[2]) is None:
            fail("usage: agentsassemble-room speak-to <agent-id> '<message>'")
        target_agent_id = sys.argv[2]
        content_start = 3
    content = " ".join(sys.argv[content_start:]).strip() if len(sys.argv) > content_start else sys.stdin.read().strip()
    if not content:
        fail("room message is empty")
    turn_id = active_turn()
    stage_publication(
        {
            "content": content[:12000],
            "target_agent_id": target_agent_id,
            "message_kind": "message",
        },
    )
    audit("speak", turn_id, {"target_agent_id": target_agent_id} if target_agent_id else None)
elif command == "decline":
    require_tool("decline_to_speak")
    if len(sys.argv) != 3 or sys.argv[2] not in {"nothing_useful_to_add", "not_addressed", "duplicate"}:
        fail("usage: agentsassemble-room decline <nothing_useful_to_add|not_addressed|duplicate>")
    turn_id = active_turn()
    stage_publication({"content": "", "target_agent_id": "", "message_kind": "decline"})
    audit("decline_to_speak", turn_id, {"reason_code": sys.argv[2]})
    print(json.dumps({"declined": True, "reason_code": sys.argv[2]}))
elif command == "vote-create":
    require_tool("create_vote")
    if len(sys.argv) not in {4, 5}:
        fail("usage: agentsassemble-room vote-create '<question>' '<json-options>' [duration-seconds]")
    question = clean_text(sys.argv[2], 300)
    try:
        raw_options = json.loads(sys.argv[3])
    except json.JSONDecodeError:
        fail("vote options must be a JSON array")
    if not question or not isinstance(raw_options, list):
        fail("a vote requires a question and a JSON option array")
    options = []
    seen = set()
    for value in raw_options:
        if not isinstance(value, str):
            fail("every vote option must be text")
        option = clean_text(value, 100)
        if option and option.casefold() not in seen:
            seen.add(option.casefold())
            options.append(option)
    if not 2 <= len(options) <= 10:
        fail("a vote requires 2 to 10 distinct options")
    try:
        duration = int(sys.argv[4]) if len(sys.argv) == 5 else 0
    except ValueError:
        fail("vote duration must be an integer")
    if duration != 0 and not 30 <= duration <= 86400:
        fail("vote duration must be 0 or between 30 and 86400 seconds")
    turn_id = active_turn()
    stage_publication({
        "content": "",
        "target_agent_id": "",
        "message_kind": "vote",
        "vote_question": question,
        "vote_options": options,
        "vote_duration_seconds": duration,
    })
    details = {"question": question, "options": options, "duration_seconds": duration}
    audit("create_vote", turn_id, details)
    print(json.dumps({"queued": True, **details}, ensure_ascii=False))
elif command == "vote-cast":
    require_tool("cast_vote")
    if len(sys.argv) != 4:
        fail("usage: agentsassemble-room vote-cast <vote-id> '<choice>'")
    vote_id = clean_text(sys.argv[2], 128)
    poll = find_poll(vote_id)
    deadline = str(poll.get("vote_deadline_at") or "")
    if deadline:
        try:
            deadline_at = datetime.fromisoformat(deadline.replace("Z", "+00:00"))
        except ValueError:
            fail("vote deadline is invalid")
        if deadline_at <= datetime.now(timezone.utc):
            fail("this vote has ended")
    choice = resolve_choice(sys.argv[3], list(poll.get("vote_options") or []))
    turn_id = active_turn()
    stage_publication({
        "content": "",
        "target_agent_id": "",
        "message_kind": "vote_cast",
        "vote_id": vote_id,
        "vote_choice": choice,
    })
    details = {"vote_id": vote_id, "choice": choice}
    audit("cast_vote", turn_id, details)
    print(json.dumps({"queued": True, **details}, ensure_ascii=False))
elif command == "vote-summary":
    require_tool("vote_summary")
    if len(sys.argv) != 3:
        fail("usage: agentsassemble-room vote-summary <vote-id>")
    vote_id = clean_text(sys.argv[2], 128)
    poll = find_poll(vote_id)
    options = [str(value) for value in poll.get("vote_options") or []]
    raw_tallies = poll.get("vote_tallies")
    if not isinstance(raw_tallies, dict):
        raw_tallies = {}
    tallies = {}
    for option in options:
        value = raw_tallies.get(option, 0)
        if isinstance(value, bool):
            value = 0
        try:
            tallies[option] = max(0, int(value))
        except (TypeError, ValueError):
            tallies[option] = 0
    projected_own_choice = poll.get("vote_own_choice")
    own_choice = (
        resolve_choice(projected_own_choice, options)
        if projected_own_choice
        else ""
    )
    result = {
        "vote_id": vote_id,
        "question": str(poll.get("vote_question") or ""),
        "options": options,
        "tallies": tallies,
        "own_choice": own_choice,
        "total_votes": sum(tallies.values()),
        "scope": "bounded_current_view",
    }
    audit("vote_summary", details={"vote_id": vote_id})
    print(json.dumps(result, ensure_ascii=False))
elif command == "media":
    attachment_id = sys.argv[2] if len(sys.argv) > 2 else ""
    index = json.loads(MEDIA.read_text(encoding="utf-8")).get("media", {})
    item = index.get(attachment_id)
    if not isinstance(item, dict) or not item.get("path"):
        fail("media is unavailable")
    audit("media")
    print(item["path"])
elif command == "roll":
    require_tool("roll_dice")
    if len(sys.argv) != 3:
        fail("usage: agentsassemble-room roll '<NdS+M>'")
    match = re.fullmatch(r"\s*(\d{0,3})d(\d{1,4})([+-]\d{1,5})?\s*", sys.argv[2], re.IGNORECASE)
    if match is None:
        fail("dice notation must look like d20, 2d6, or 1d20+3")
    count = int(match.group(1) or 1)
    sides = int(match.group(2))
    modifier = int(match.group(3) or 0)
    if not 1 <= count <= 100 or not 2 <= sides <= 1000 or not -100000 <= modifier <= 100000:
        fail("dice notation is out of range")
    rolls = [secrets.randbelow(sides) + 1 for _ in range(count)]
    notation = f"{count}d{sides}" + (f"{modifier:+d}" if modifier else "")
    result = {
        "notation": notation,
        "rolls": rolls,
        "modifier": modifier,
        "total": sum(rolls) + modifier,
    }
    audit("roll_dice", details=result)
    print(json.dumps(result, ensure_ascii=False))
elif command == "choose":
    require_tool("choose_random")
    if len(sys.argv) != 3:
        fail("usage: agentsassemble-room choose '<json-options>'")
    try:
        raw_options = json.loads(sys.argv[2])
    except json.JSONDecodeError:
        fail("random options must be a JSON array")
    if not isinstance(raw_options, list):
        fail("random options must be a JSON array")
    options = [clean_text(value, 200) for value in raw_options if isinstance(value, str)]
    if len(options) != len(raw_options) or not 2 <= len(options) <= 50 or any(not value for value in options):
        fail("random choice requires 2 to 50 non-empty text options")
    index = secrets.randbelow(len(options))
    result = {
        "choice": options[index],
        "index": index,
        "option_count": len(options),
        "options": options,
    }
    audit("choose_random", details=result)
    print(json.dumps(result, ensure_ascii=False))
elif command == "rim-observe":
    _turn, payload, colonist_id = plugin_context("observe")
    print(json.dumps({"plugin_id": "rimworld", "colonist_id": colonist_id, "snapshot": payload}, ensure_ascii=False))
elif command == "rim-inspect":
    _turn, payload, colonist_id = plugin_context("inspect")
    if len(sys.argv) < 3 or sys.argv[2] not in {"colonist", "structure", "cell"}:
        fail("usage: agentsassemble-room rim-inspect <colonist|structure|cell> [target-id|x y]")
    target_type = sys.argv[2]
    if target_type == "colonist":
        target_id = clean_text(sys.argv[3] if len(sys.argv) > 3 else colonist_id, 64)
        result = next((item for item in payload.get("colonists", []) if isinstance(item, dict) and item.get("id") == target_id), None)
        if result is None:
            fail(f"colonist {target_id!r} was not found")
        print(json.dumps({"colonist": result}, ensure_ascii=False))
    elif target_type == "structure":
        print(json.dumps({"structures": payload.get("structures", [])}, ensure_ascii=False))
    else:
        if len(sys.argv) != 5:
            fail("usage: agentsassemble-room rim-inspect cell <x> <y>")
        print(json.dumps({"cell": {"x": int(sys.argv[3]), "y": int(sys.argv[4])}}, ensure_ascii=False))
elif command == "rim-act":
    _turn, _payload, colonist_id = plugin_context("act")
    if PLUGIN_ACTION.exists() or len(sys.argv) != 4:
        fail("usage: agentsassemble-room rim-act <action> '<json-args>' (one action per turn)")
    try:
        action_args = json.loads(sys.argv[3])
    except json.JSONDecodeError:
        fail("RimWorld action args must be a JSON object")
    if not isinstance(action_args, dict):
        fail("RimWorld action args must be a JSON object")
    action = clean_text(sys.argv[2], 64)
    atomic_json(PLUGIN_ACTION, {"action": action, "action_args": action_args})
    print(json.dumps({"queued": True, "colonist_id": colonist_id, "action": action}, ensure_ascii=False))
elif command == "rim-speak":
    _turn, _payload, colonist_id = plugin_context("speak")
    if PLUGIN_SPEECH.exists():
        fail("only one colony speech line may be staged per turn")
    text = clean_text(" ".join(sys.argv[2:]) if len(sys.argv) > 2 else sys.stdin.read(), 500)
    if not text:
        fail("colony speech cannot be empty")
    atomic_json(PLUGIN_SPEECH, {"text": text})
    print(json.dumps({"queued": True, "colonist_id": colonist_id, "text": text}, ensure_ascii=False))
elif command == "help":
    print("agentsassemble-room read | search <query> [channel|all] [cursor] | search-context <channel> <event-id> | participants | speak [text] | speak-to <agent-id> [text] | decline <nothing_useful_to_add|not_addressed|duplicate> | vote-create <question> <json-options> [duration] | vote-cast <vote-id> <choice> | vote-summary <vote-id> | media <id> | roll <NdS+M> | choose <json-options> | rim-observe | rim-inspect <type> | rim-act <action> <json-args> | rim-speak <text>")
else:
    fail("unknown command")
"""


def windows_helper_wrapper() -> str:
    executable = str(Path(sys.executable).resolve()).replace("%", "%%")
    return (
        "@echo off\r\n"
        f'"{executable}" "%~dp0\\agentsassemble_room_helper.py" %*\r\n'
    )
