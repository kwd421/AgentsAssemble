from __future__ import annotations

import os
import re
import subprocess
import sys
import time
import tty


START = b"\x1b[200~"
END = b"\x1b[201~"


def room_command(*args: str) -> str:
    completed = subprocess.run(
        ["agentsassemble-room", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout


def main() -> int:
    tty.setraw(sys.stdin.fileno())
    os.write(sys.stdout.fileno(), b"FAKE_CLI_READY\n")
    buffer = b""
    marker = ""
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
            room_observation = "room.wake " in text
            visible_text = room_command("read") if room_observation else text
            markers = re.findall(
                r"AGENTSASSEMBLE_SESSION_MARKER=([A-Za-z0-9_.-]+)",
                visible_text,
            )
            if markers:
                marker = markers[-1]
            delay = re.search(r"AGENTSASSEMBLE_RESPONSE_DELAY_MS=(\d+)", visible_text)
            if delay:
                time.sleep(min(int(delay.group(1)), 2_000) / 1_000)
            turn += 1
            response = f"fake reply {turn}; marker={marker}; pid={os.getpid()}\n"
            if room_observation:
                room_command("speak", response.strip())
            os.write(sys.stdout.fileno(), response.encode("utf-8"))


if __name__ == "__main__":
    raise SystemExit(main())
