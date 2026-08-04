from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

from agentsassemble.providers.native_harness import (
    HARNESS_GATEWAY_ENV_KEY,
    NativeHarnessRuntime,
    OpenCodexHarnessGateway,
)
from agentsassemble.providers.capabilities import ProviderCapabilityCatalog


_FAKE_GATEWAY = """#!/usr/bin/env python3
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import signal
import sys

port = int(sys.argv[sys.argv.index("--port") + 1])
state_dir = Path(os.environ["OPENCODEX_HOME"])
config = json.loads((state_dir / "config.json").read_text(encoding="utf-8"))
provider = config["providers"]["agentsassemble"]
(state_dir / "child-observation.json").write_text(
    json.dumps(
        {
            "credential_available": bool(
                os.environ.get("AGENTSASSEMBLE_HARNESS_UPSTREAM_KEY")
            ),
            "credential_is_reference": provider.get("apiKey")
            == "${AGENTSASSEMBLE_HARNESS_UPSTREAM_KEY}",
        }
    ),
    encoding="utf-8",
)

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200 if self.path == "/healthz" else 404)
        self.end_headers()

    def log_message(self, _format, *args):
        pass

server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
signal.signal(signal.SIGTERM, lambda _signum, _frame: sys.exit(0))
server.serve_forever()
"""


class _FailingDelegate:
    def start(self):
        raise RuntimeError("delegate startup failed")

    def health(self):
        return {"running": False}

    def stop(self, *, timeout_seconds: float = 2.0) -> None:
        del timeout_seconds


class NativeHarnessCatalogTests(unittest.TestCase):
    def test_api_catalog_exposes_installed_native_coding_harnesses(self) -> None:
        available = {"codex", "claude", "ocx"}
        catalog = ProviderCapabilityCatalog(
            runner=lambda _command, _timeout: (1, "", "not installed"),
            resolver=lambda executable: (
                f"/bin/{executable}" if executable in available else None
            ),
            remote_model_discovery=lambda _profile, _api_key: [],
            secret_resolver=lambda _provider_id: "",
        )

        deepseek = next(
            provider
            for provider in catalog.payload(refresh=True)
            if provider["id"] == "deepseek"
        )
        harness = next(
            control
            for control in deepseek["controls"]
            if control["key"] == "execution_harness"
        )

        self.assertEqual(
            [option["value"] for option in harness["options"]],
            ["builtin", "codex", "claude"],
        )
        self.assertTrue(deepseek["native_harness_gateway_available"])
        self.assertTrue(deepseek["native_harness_gateway_required"])


@unittest.skipIf(os.name == "nt", "fake executable uses a POSIX shebang")
class NativeHarnessGatewayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.executable = self.root / "fake-ocx"
        self.executable.write_text(
            _FAKE_GATEWAY.replace("#!/usr/bin/env python3", f"#!{sys.executable}"),
            encoding="utf-8",
        )
        self.executable.chmod(0o700)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _gateway(self) -> OpenCodexHarnessGateway:
        return OpenCodexHarnessGateway(
            state_dir=self.root / "gateway",
            upstream_base_url="https://api.example.test/v1",
            upstream_api_key="credential-must-not-be-persisted",
            model="vendor/model",
            executable=str(self.executable),
        )

    def test_gateway_keeps_the_credential_out_of_its_config_and_stops_cleanly(self) -> None:
        gateway = self._gateway()
        try:
            gateway.start()
            pid = gateway.pid
            observation = json.loads(
                (gateway.state_dir / "child-observation.json").read_text(encoding="utf-8")
            )
            config_text = (gateway.state_dir / "config.json").read_text(encoding="utf-8")

            self.assertTrue(observation["credential_available"])
            self.assertTrue(observation["credential_is_reference"])
            self.assertNotIn("credential-must-not-be-persisted", config_text)
            self.assertIn(f"${{{HARNESS_GATEWAY_ENV_KEY}}}", config_text)
        finally:
            gateway.stop()

        self.assertIsNotNone(pid)
        with self.assertRaises(ProcessLookupError):
            os.kill(int(pid), 0)

    def test_delegate_start_failure_also_stops_the_gateway_process(self) -> None:
        gateway = self._gateway()
        runtime = NativeHarnessRuntime(
            _FailingDelegate(),
            harness="codex",
            gateway=gateway,
        )

        with self.assertRaisesRegex(RuntimeError, "delegate startup failed"):
            runtime.start()

        self.assertIsNone(gateway.pid)

if __name__ == "__main__":
    unittest.main()
