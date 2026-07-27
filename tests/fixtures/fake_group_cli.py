from __future__ import annotations

import os
import re
import subprocess
import sys
import tty


START = b"\x1b[200~"
END = b"\x1b[201~"
CONTROL_MARKER = re.compile(r"(PAUSE-RESUME-[A-Z0-9_.-]+)", re.IGNORECASE)
ROOM_SPEAKER = re.compile(r"^## (?!Agent handles$|Conversation position$|Finalized messages$)(.+)$", re.MULTILINE)


def room_command(*args: str) -> str:
    completed = subprocess.run(
        ["agentsassemble-room", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout


def main() -> int:
    agent_id = sys.argv[1]
    tty.setraw(sys.stdin.fileno())
    os.write(sys.stdout.fileno(), f"FAKE_GROUP_READY {agent_id}\n".encode())
    buffer = b""
    turn = 0
    normal_replies = 0
    while True:
        chunk = os.read(sys.stdin.fileno(), 4096)
        if not chunk:
            return 0
        buffer += chunk
        while START in buffer:
            _prefix, _, after_start = buffer.partition(START)
            if END not in after_start:
                break
            payload, _, buffer = after_start.partition(END)
            buffer = buffer.lstrip(b"\r\n")
            text = payload.decode("utf-8", errors="replace")
            room_observation = "room.wake " in text
            visible_text = room_command("read") if room_observation else text
            turn += 1
            latest_message = visible_text.rsplit("\n## ", 1)[-1]
            control_markers = CONTROL_MARKER.findall(latest_message)
            marker_targets_this_agent = bool(
                control_markers
                and agent_id.casefold() in control_markers[-1].casefold()
            )
            publish = False
            if marker_targets_this_agent:
                response = f"{control_markers[-1].upper()} {agent_id} 세션이 같은 대화를 이어받았어.\n"
                publish = True
            elif control_markers or normal_replies >= 2:
                response = f"{agent_id}는 이번 관찰에서 공개 발언을 추가하지 않아.\n"
            else:
                speakers = ROOM_SPEAKER.findall(visible_text)
                heard = ", ".join(speakers[-3:]) or "공개 방"
                response = f"{agent_id}의 {turn}번째 의견이야. 앞선 {heard}의 대화를 읽고 자연스럽게 이어갈게.\n"
                normal_replies += 1
                publish = True
            if room_observation and publish:
                room_command("speak", response.strip())
            os.write(sys.stdout.fileno(), response.encode("utf-8"))


if __name__ == "__main__":
    raise SystemExit(main())
