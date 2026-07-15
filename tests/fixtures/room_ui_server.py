from __future__ import annotations

import os
from pathlib import Path
import signal
import sys
import tempfile
import threading


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from http.server import ThreadingHTTPServer

from agentsassemble.gui import _make_handler
from agentsassemble.room_invite import PUBLIC_URL_ENV
from agentsassemble.room_bridge_process import NativeCliBridgeProcessManager
from agentsassemble.room_realtime import NativeCliProviderSpec, RoomRealtimeController
from tests.room_realtime_test_support import memory_room_access_services


def main() -> int:
    stop = threading.Event()
    port = int(os.environ.get("AGENTSASSEMBLE_E2E_PORT", "8898"))
    os.environ[PUBLIC_URL_ENV] = f"http://public.localhost:{port}"
    fixture = Path(__file__).with_name("fake_interactive_cli.py")
    with tempfile.TemporaryDirectory(prefix="agentsassemble-ui-e2e-") as temp_dir:
        output_root = Path(temp_dir)
        spec = NativeCliProviderSpec(
            agent_id="fake",
            display_name="Fake Interactive CLI",
            command=(sys.executable, "-u", str(fixture)),
            cwd=str(ROOT),
            provider_kind="local_cli",
            model="fake-e2e-model",
            reasoning_effort="low",
            permission_mode="meeting_read_only",
            transport="pty",
            default_responder=False,
            quiet_seconds=0.05,
            input_mode="bracketed_paste",
            startup_quiet_seconds=0.05,
            startup_timeout_seconds=1.0,
            turn_timeout_seconds=5.0,
        )
        manager = NativeCliBridgeProcessManager(output_root)
        access = memory_room_access_services()
        access.public_invite.set_host_token("e2e-host-token")
        access.public_invite.set_public_url(f"http://public.localhost:{port}")
        controller = RoomRealtimeController(
            output_root,
            **access.controller_kwargs(),
            providers=[spec],
            bridge_manager=manager,
        )
        manager.set_exit_listener(controller.bridge_process_exited)
        server = ThreadingHTTPServer(
            ("127.0.0.1", port),
            _make_handler(
                output_root,
                room_realtime_controller_override=controller,
                invite_repository_override=access.repository,
                public_invite_runtime_override=access.public_invite,
            ),
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def request_stop(_signum, _frame) -> None:
            stop.set()

        signal.signal(signal.SIGTERM, request_stop)
        signal.signal(signal.SIGINT, request_stop)
        try:
            stop.wait()
        finally:
            controller.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=3.0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
