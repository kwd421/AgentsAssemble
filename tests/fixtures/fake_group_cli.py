from __future__ import annotations

import os
import re
import sys
import tty


START = b"\x1b[200~"
END = b"\x1b[201~"
CONTROL_MARKER = re.compile(r"(PAUSE-RESUME-[A-Z0-9_.-]+)", re.IGNORECASE)
ROOM_SPEAKER = re.compile(r"^- ([^:\n]+):", re.MULTILINE)


def main() -> int:
    agent_id = sys.argv[1]
    tty.setraw(sys.stdin.fileno())
    os.write(sys.stdout.fileno(), f"FAKE_GROUP_READY {agent_id}\n".encode())
    buffer = b""
    turn = 0
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
            turn += 1
            control_markers = CONTROL_MARKER.findall(text)
            if control_markers:
                response = f"{control_markers[-1].upper()} {agent_id} 세션이 같은 대화를 이어받았어.\n"
            else:
                speakers = ROOM_SPEAKER.findall(text)
                heard = ", ".join(speakers[-3:]) or "공개 방"
                response = f"{agent_id}의 {turn}번째 의견이야. 앞선 {heard}의 대화를 읽고 자연스럽게 이어갈게.\n"
            os.write(sys.stdout.fileno(), response.encode("utf-8"))


if __name__ == "__main__":
    raise SystemExit(main())
