from __future__ import annotations

import os
import re
import sys
import tty


START = b"\x1b[200~"
END = b"\x1b[201~"
RELAY = re.compile(r"RELAY_MARKER=([A-Za-z0-9_.-]+) SOURCE=([\w-]+) TARGET=([\w-]+)")


def main() -> int:
    agent_id = sys.argv[1]
    tty.setraw(sys.stdin.fileno())
    os.write(sys.stdout.fileno(), f"FAKE_RELAY_READY {agent_id}\n".encode())
    buffer = b""
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
            matches = RELAY.findall(text)
            if not matches:
                response = f"{agent_id} saw no relay marker\n"
            else:
                marker, source, target = matches[-1]
                if agent_id == source:
                    response = f"{marker}에 관해 한 문장으로 답해줘 @{target}\n"
                elif agent_id == target:
                    response = f"{marker} 릴레이를 정상적으로 받았어.\n"
                else:
                    response = f"{agent_id} ignored {marker}\n"
            os.write(sys.stdout.fileno(), response.encode("utf-8"))


if __name__ == "__main__":
    raise SystemExit(main())
