"""Isolated system DNS resolver used by credentialed provider transport."""

from __future__ import annotations

import json
import socket
import sys


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        return 2
    hostname = argv[1]
    try:
        port = int(argv[2])
        answers = socket.getaddrinfo(
            hostname,
            port,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
        )
    except (OSError, ValueError):
        return 1
    addresses: list[str] = []
    for answer in answers:
        socket_address = answer[4] if len(answer) > 4 else ()
        candidate = str(socket_address[0] if socket_address else "").strip()
        if candidate and candidate not in addresses:
            addresses.append(candidate)
    sys.stdout.write(json.dumps(addresses, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
