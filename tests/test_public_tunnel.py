import threading
import unittest
from unittest import mock

from agentsassemble.application.public_invite_runtime import PublicInviteRuntime
from agentsassemble.application.public_tunnel import (
    PublicTunnelManager,
    extract_trycloudflare_url,
)


class FakeProcess:
    def __init__(self, *, lines: list[str] | None = None, exit_code: int | None = None):
        self.stdout = iter(lines or [])
        self._exit_code = exit_code

    def poll(self):
        return self._exit_code

    def terminate(self):
        self._exit_code = 0

    def wait(self, timeout=None):
        return self._exit_code or 0

    def kill(self):
        self._exit_code = -9


class PublicTunnelTests(unittest.TestCase):
    def setUp(self):
        self.runtime = PublicInviteRuntime(environ={})

    def test_extract_trycloudflare_url_from_cloudflared_log_line(self):
        self.assertEqual(
            extract_trycloudflare_url("Visit https://soft-river-demo.trycloudflare.com to inspect your tunnel"),
            "https://soft-river-demo.trycloudflare.com",
        )

    def test_extract_trycloudflare_url_returns_empty_for_noise(self):
        self.assertEqual(extract_trycloudflare_url("starting quick tunnel"), "")

    def test_start_reports_unavailable_when_cloudflared_is_missing(self):
        manager = PublicTunnelManager(
            public_invite_runtime=self.runtime,
            local_url="http://127.0.0.1:8765",
            which=lambda _name: None,
        )

        status = manager.start()

        self.assertFalse(status["available"])
        self.assertFalse(status["running"])
        self.assertEqual(status["phase"], "stopped")
        self.assertEqual(status["last_error"], "cloudflared is not installed")
        self.assertEqual(self.runtime.public_url(), "")

    def test_reconnect_with_new_url_updates_and_reannounces(self):
        # cloudflared re-issues a hostname on reconnect; the manager must follow
        # it (not stay locked on the first, now-dead URL) and re-point workers.dev.
        manager = PublicTunnelManager(
            public_invite_runtime=self.runtime,
            local_url="http://127.0.0.1:8765",
            which=lambda _name: "/bin/cloudflared",
        )
        process = FakeProcess()
        manager._process = process
        manager._generation = 1
        announced: list[str] = []
        with mock.patch(
            "agentsassemble.application.public_tunnel.announce_stable_entry",
            lambda url: announced.append(url),
        ):
            manager._record_output_line(
                process,
                1,
                "Visit https://old-tunnel.trycloudflare.com to inspect\n",
            )
            origin_host = self.runtime.managed_ingress_origin_host()
            manager._record_output_line(process, 1, "connection lost, reconnecting...\n")
            manager._record_output_line(
                process,
                1,
                "Visit https://new-tunnel.trycloudflare.com to inspect\n",
            )
        self.assertEqual(manager._public_url, "https://new-tunnel.trycloudflare.com")
        self.assertEqual(self.runtime.public_url(), "https://new-tunnel.trycloudflare.com")
        self.assertEqual(self.runtime.managed_ingress_origin_host(), origin_host)
        self.assertEqual(
            self.runtime.trusted_ingress_kind(
                provided_managed_origin=self.runtime.managed_ingress_origin_host(),
            ),
            "cloudflare",
        )
        # both hostnames were announced in order — workers.dev ends on the live one
        self.assertEqual(
            announced,
            ["https://old-tunnel.trycloudflare.com", "https://new-tunnel.trycloudflare.com"],
        )

    def test_stale_reader_output_after_stop_does_not_restore_public_url(self):
        manager = PublicTunnelManager(
            public_invite_runtime=self.runtime,
            local_url="http://127.0.0.1:8765",
            which=lambda _name: "/bin/cloudflared",
        )
        process = FakeProcess(lines=["https://late-output.trycloudflare.com\n"])
        manager._process = process
        manager._generation = 2

        manager._read_output(process, 1)

        self.assertEqual(self.runtime.public_url(), "")

    def test_exited_tunnel_clears_owned_runtime_public_url(self):
        manager = PublicTunnelManager(
            public_invite_runtime=self.runtime,
            local_url="http://127.0.0.1:8765",
            which=lambda _name: "/bin/cloudflared",
        )
        process = FakeProcess(exit_code=1)
        manager._process = process
        manager._public_url = "https://dead-tunnel.trycloudflare.com"
        self.runtime.set_managed_public_url(
            "https://dead-tunnel.trycloudflare.com",
            ingress_kind="cloudflare",
        )
        manager._origin_host = self.runtime.managed_ingress_origin_host()

        status = manager.status()

        self.assertEqual(status["public_url"], "")
        self.assertEqual(status["phase"], "stopped")
        self.assertEqual(self.runtime.public_url(), "")
        self.assertEqual(self.runtime.trusted_ingress_kind(), "")

    def test_tunnel_uses_a_process_lifetime_origin_credential(self):
        commands: list[list[str]] = []
        release_output = threading.Event()

        class HeldOutput:
            def __iter__(self):
                return self

            def __next__(self):
                release_output.wait(timeout=2)
                raise StopIteration

        def popen(command, **_kwargs):
            commands.append(command)
            process = FakeProcess()
            process.stdout = HeldOutput()
            return process

        manager = PublicTunnelManager(
            public_invite_runtime=self.runtime,
            local_url="http://127.0.0.1:8765",
            which=lambda _name: "/bin/cloudflared",
            popen=popen,
        )

        try:
            manager.start()
            origin_host = self.runtime.managed_ingress_origin_host()

            self.assertTrue(origin_host.endswith(".origin.invalid"))
            self.assertIn("--http-host-header", commands[0])
            self.assertEqual(commands[0][-1], origin_host)
        finally:
            release_output.set()
            manager.stop()

    def test_tunnel_output_eof_revokes_the_registered_ingress_immediately(self):
        manager = PublicTunnelManager(
            public_invite_runtime=self.runtime,
            local_url="http://127.0.0.1:8765",
            which=lambda _name: "/bin/cloudflared",
        )
        process = FakeProcess(
            lines=["Visit https://ended.trycloudflare.com to inspect\n"],
        )
        origin_host = self.runtime.prepare_managed_ingress(ingress_kind="cloudflare")
        manager._process = process
        manager._origin_host = origin_host
        manager._generation = 1

        with mock.patch(
            "agentsassemble.application.public_tunnel.announce_stable_entry",
            lambda _url: None,
        ):
            manager._read_output(process, 1, origin_host)

        self.assertEqual(self.runtime.public_url(), "")
        self.assertEqual(manager._public_url, "")

    def test_tunnel_output_eof_revokes_origin_before_a_url_is_announced(self):
        manager = PublicTunnelManager(
            public_invite_runtime=self.runtime,
            local_url="http://127.0.0.1:8765",
            which=lambda _name: "/bin/cloudflared",
        )
        process = FakeProcess()
        origin_host = self.runtime.prepare_managed_ingress(ingress_kind="cloudflare")
        manager._process = process
        manager._origin_host = origin_host
        manager._generation = 1

        manager._read_output(process, 1, origin_host)

        self.assertFalse(self.runtime.verify_managed_ingress_origin(origin_host))


if __name__ == "__main__":
    unittest.main()
