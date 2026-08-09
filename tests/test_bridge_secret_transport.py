import io
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from agentsassemble.providers.bridge_launch_secrets import read_secure_launch_payload
from agentsassemble.providers.bridge_process import NativeCliBridgeProcessManager
from agentsassemble.room.realtime import NativeCliProviderSpec


def _api_spec() -> NativeCliProviderSpec:
    return NativeCliProviderSpec(
        agent_id="deepseek",
        display_name="DeepSeek",
        command=("server-owned-api",),
        provider_kind="deepseek_api",
        model="deepseek-v4-flash",
        reasoning_effort="low",
        service_tier="default",
        permission_mode="meeting_read_only",
    )


class _CapturingBytesIO(io.BytesIO):
    def close(self) -> None:
        pass


class _Process:
    def __init__(self, *, pid: int = 3210, stderr_bytes: bytes = b"") -> None:
        self.pid = pid
        self.returncode = None
        self.stderr = io.BytesIO(stderr_bytes)
        self.stdin = _CapturingBytesIO()
        self._done = threading.Event()

    def poll(self):
        return self.returncode

    def terminate(self) -> None:
        self.returncode = 0
        self._done.set()

    def kill(self) -> None:
        self.returncode = -9
        self._done.set()

    def wait(self, timeout=None):
        if not self._done.wait(timeout):
            raise TimeoutError()
        return self.returncode


class _Popen:
    def __init__(self, process: _Process | None = None) -> None:
        self.process = process or _Process()

    def __call__(self, _command, **_kwargs):
        return self.process


class BridgeSecretTransportTests(unittest.TestCase):
    def _start(self, manager, *, ticket="long-enough-ticket"):
        return manager.start(
            "general",
            {"session_id": "deepseek"},
            _api_spec(),
            server_url="http://127.0.0.1:9999",
            ticket_issuer=lambda _identity: ticket,
        )

    def test_secure_launch_round_trips_maximum_unicode_credential_through_child_frame(self):
        credential = "秘" * 8_192
        with tempfile.TemporaryDirectory() as temp_dir:
            popen = _Popen()
            manager = NativeCliBridgeProcessManager(
                Path(temp_dir),
                popen_factory=popen,
                secret_resolver=lambda _provider_id: credential,
            )
            launch = manager.start(
                "general",
                {"session_id": "deepseek"},
                _api_spec(),
                server_url="http://127.0.0.1:9999",
                ticket_issuer=lambda _identity: {
                    "ticket": "long-enough-ticket",
                    "session_token": "renewable-session-token",
                },
            )
            popen.process.stdin.seek(0)
            handoff = read_secure_launch_payload(popen.process.stdin)
            launch_config = json.loads(
                Path(launch["config_path"]).read_text(encoding="utf-8")
            )

        self.assertEqual(handoff["credential"], credential)
        self.assertEqual(handoff["session_token"], "renewable-session-token")
        self.assertEqual(
            launch_config["bridge_launch_id"],
            launch["bridge_handle_id"],
        )

    def test_stderr_secret_is_redacted_before_multibyte_byte_tail_is_bounded(self):
        secret = "unknown-prefix-runtime-credential-918273645"
        secret_offset = 11
        filler_bytes = 16_000 - len(secret[secret_offset:].encode("utf-8"))
        filler = "한" * (filler_bytes // len("한".encode("utf-8")))
        process = _Process(stderr_bytes=("x" * 100 + secret + filler).encode("utf-8"))
        with tempfile.TemporaryDirectory() as temp_dir:
            popen = _Popen(process)
            manager = NativeCliBridgeProcessManager(
                Path(temp_dir),
                popen_factory=popen,
                secret_resolver=lambda _provider_id: secret,
            )
            launch = self._start(manager)
            deadline = time.monotonic() + 2
            health = manager.health("general", "deepseek")
            while health.get("stderr_line_count") != 1 and time.monotonic() < deadline:
                time.sleep(0.01)
                health = manager.health("general", "deepseek")
            manager.stop("general", "deepseek", handle_id=launch["bridge_handle_id"])
            persisted = Path(launch["stderr_path"]).read_bytes()

        self.assertLessEqual(len(persisted), 16_000)
        self.assertNotIn(secret[secret_offset:].encode("utf-8"), persisted)
        self.assertNotIn(secret[secret_offset:], str(health.get("stderr_tail") or ""))
        self.assertIn(b"[redacted]", persisted)

    def test_finished_bridge_secret_stops_over_redacting_later_diagnostics(self):
        old_secret = "ordinary-looking-old-value-918273645"
        current_secret = "ordinary-looking-current-value-564738291"
        active_secret = [old_secret]
        with tempfile.TemporaryDirectory() as temp_dir:
            popen = _Popen()
            manager = NativeCliBridgeProcessManager(
                Path(temp_dir),
                popen_factory=popen,
                secret_resolver=lambda _provider_id: active_secret[0],
            )
            launch = self._start(manager)
            manager.stop("general", "deepseek", handle_id=launch["bridge_handle_id"])
            active_secret[0] = current_secret
            popen.process = _Process(pid=3211)
            self._start(manager, ticket="another-long-ticket")
            public_diagnostic = manager.redact_diagnostic(
                "general",
                "deepseek",
                f"old={old_secret} current={current_secret}",
            )

        self.assertIn(old_secret, public_diagnostic)
        self.assertNotIn(current_secret, public_diagnostic)

if __name__ == "__main__":
    unittest.main()
