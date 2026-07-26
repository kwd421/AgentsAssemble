"""Executable composition for a server-owned provider Agent Bridge."""

from __future__ import annotations

import json
import os
import signal
import sys
from pathlib import Path

from agentsassemble.providers.agent_bridge import RoomAgentBridge
from agentsassemble.providers.room_portal import ROOM_SESSION_ORIENTATION, RoomPortal
from agentsassemble.providers.runtime_config import CanonicalBridgeLaunchConfig
from agentsassemble.providers.runtime_factory import runtime_from_config
from agentsassemble.web.room_client import connect_room_ws_with_ticket


def main() -> int:
    server_url = str(os.environ.get("AGENTSASSEMBLE_BRIDGE_SERVER_URL") or "")
    ticket = str(os.environ.get("AGENTSASSEMBLE_BRIDGE_TICKET") or "")
    config_path = Path(str(os.environ.get("AGENTSASSEMBLE_BRIDGE_CONFIG") or ""))
    if not server_url or not ticket or not config_path.is_file():
        raise SystemExit("Agent Bridge requires server URL, ticket, and config environment variables.")
    raw_config = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw_config, dict):
        raise SystemExit("Agent Bridge config must be a JSON object.")
    config = CanonicalBridgeLaunchConfig.parse_strict(raw_config)
    credential = ""
    if config.credential_stdin:
        credential = sys.stdin.buffer.readline(16_384).decode("utf-8", errors="replace").strip()
        if not credential:
            raise SystemExit("Agent Bridge credential handoff was empty.")
    portal = RoomPortal(
        Path(config.runtime.runtime_state_dir) / "room-portal",
        participant_id=config.runtime.participant_id,
    )
    portal.prepare()
    provider_environment = portal.provider_environment(os.environ.get("PATH", ""))
    client = connect_room_ws_with_ticket(server_url, ticket, ["room_events"], timeout=10.0)
    bridge = RoomAgentBridge(
        client,
        runtime_from_config(
            config.runtime,
            credential=credential,
            environment=provider_environment,
            room_portal=portal,
        ),
        room_id=config.room_id,
        participant_id=config.runtime.participant_id,
        session_id=config.session_id,
        runtime_profile=config.runtime.profile,
        initial_orientation=ROOM_SESSION_ORIENTATION,
        room_portal=portal,
    )

    def stop_bridge(_signum, _frame) -> None:
        bridge.stop()

    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, stop_bridge)
    return bridge.run()


if __name__ == "__main__":
    raise SystemExit(main())
