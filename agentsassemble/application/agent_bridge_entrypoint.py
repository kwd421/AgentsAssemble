"""Executable composition for a server-owned provider Agent Bridge."""

from __future__ import annotations

import json
import os
import signal
import sys
import threading
import time
from pathlib import Path

from agentsassemble.providers.agent_bridge import RoomAgentBridge
from agentsassemble.providers.bridge_launch_secrets import (
    SecureLaunchPayloadError,
    read_secure_launch_payload,
)
from agentsassemble.providers.redacting_room_client import CredentialRedactingRoomClient
from agentsassemble.providers.room_portal import RoomPortal, room_session_orientation
from agentsassemble.providers.runtime_config import CanonicalBridgeLaunchConfig
from agentsassemble.providers.runtime_factory import runtime_from_config
from agentsassemble.web.room_client import connect_room_ws, connect_room_ws_with_ticket
from agentsassemble.web.websocket_codec import WebSocketProtocolError


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
    session_token = ""
    try:
        secure_launch = read_secure_launch_payload(sys.stdin.buffer)
    except SecureLaunchPayloadError as error:
        raise SystemExit(str(error)) from error
    credential = str(secure_launch.get("credential") or "")
    session_token = str(secure_launch.get("session_token") or "")
    if config.credential_stdin and not credential:
        raise SystemExit("Agent Bridge credential handoff was empty.")
    portal = RoomPortal(
        Path(config.runtime.runtime_state_dir) / "room-portal",
        participant_id=config.runtime.participant_id,
    )
    portal.prepare()
    provider_environment = portal.provider_environment(os.environ.get("PATH", ""))
    runtime = runtime_from_config(
        config.runtime,
        credential=credential,
        environment=provider_environment,
        room_portal=portal,
    )
    stop_requested = threading.Event()
    current_bridge: list[RoomAgentBridge | None] = [None]

    def stop_bridge(_signum, _frame) -> None:
        stop_requested.set()
        bridge = current_bridge[0]
        if bridge is not None:
            bridge.stop()

    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, stop_bridge)
    first_connection = True
    exit_code = 0
    runtime_stopped = False
    try:
        while not stop_requested.is_set():
            try:
                if first_connection:
                    client = connect_room_ws_with_ticket(
                        server_url,
                        ticket,
                        ["room_events"],
                        timeout=10.0,
                    )
                    first_connection = False
                elif session_token:
                    client = connect_room_ws(
                        server_url,
                        session_token,
                        ["room_events"],
                        timeout=10.0,
                    )
                else:
                    break
            except (OSError, TimeoutError, WebSocketProtocolError):
                if not session_token or stop_requested.wait(1.0):
                    break
                first_connection = False
                continue
            bridge = RoomAgentBridge(
                CredentialRedactingRoomClient(
                    client,
                    sensitive_values=(credential, ticket, session_token),
                ),
                runtime,
                room_id=config.room_id,
                participant_id=config.runtime.participant_id,
                session_id=config.session_id,
                runtime_profile=config.runtime.profile,
                initial_orientation=room_session_orientation(
                    config.runtime.provider_kind
                ),
                room_portal=portal,
                stop_runtime_on_exit=False,
            )
            current_bridge[0] = bridge
            exit_code = bridge.run()
            current_bridge[0] = None
            if stop_requested.is_set() or bridge.remote_stop_requested:
                runtime_stopped = bridge.remote_stop_requested
                break
            if not session_token:
                break
            time.sleep(0.25)
    finally:
        current_bridge[0] = None
        if not runtime_stopped:
            try:
                runtime.stop(timeout_seconds=2.0)
            except Exception:
                exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
