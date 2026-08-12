import threading
import unittest
from time import monotonic
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
        announce_patcher = mock.patch(
            "agentsassemble.application.public_tunnel.announce_stable_entry",
            lambda _url: None,
        )
        clear_patcher = mock.patch(
            "agentsassemble.application.public_tunnel.clear_stable_entry",
            lambda: None,
        )
        announce_patcher.start()
        clear_patcher.start()
        self.addCleanup(announce_patcher.stop)
        self.addCleanup(clear_patcher.stop)

    def test_extract_trycloudflare_url_from_cloudflared_log_line(self):
        self.assertEqual(
            extract_trycloudflare_url("Visit https://soft-river-demo.trycloudflare.com to inspect your tunnel"),
            "https://soft-river-demo.trycloudflare.com",
        )

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
        manager._origin_host = self.runtime.prepare_managed_ingress(
            ingress_kind="cloudflare"
        )
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

    def test_manual_url_transition_stops_owned_tunnel_before_committing_url(self):
        manager = PublicTunnelManager(
            public_invite_runtime=self.runtime,
            local_url="http://127.0.0.1:8765",
            which=lambda _name: "/bin/cloudflared",
        )
        process = FakeProcess()
        origin_host = self.runtime.prepare_managed_ingress(ingress_kind="cloudflare")
        manager._process = process
        manager._origin_host = origin_host
        manager._generation = 4

        public_url = manager.set_manual_public_url("https://manual.example.com")
        manager._record_output_line(
            process,
            4,
            "Visit https://late-output.trycloudflare.com to inspect\n",
        )

        self.assertEqual(process.poll(), 0)
        self.assertEqual(public_url, "https://manual.example.com")
        self.assertEqual(self.runtime.public_url(), "https://manual.example.com")
        self.assertFalse(self.runtime.verify_managed_ingress_origin(origin_host))

    def test_failed_stop_revokes_ingress_and_keeps_process_for_retry(self):
        class FailOnceTerminateProcess(FakeProcess):
            def __init__(self):
                super().__init__()
                self.terminate_calls = 0

            def terminate(self):
                self.terminate_calls += 1
                if self.terminate_calls == 1:
                    raise OSError("simulated terminate failure")
                self._exit_code = 0

        manager = PublicTunnelManager(
            public_invite_runtime=self.runtime,
            local_url="http://127.0.0.1:8765",
            which=lambda _name: "/bin/cloudflared",
        )
        process = FailOnceTerminateProcess()
        origin_host = self.runtime.prepare_managed_ingress(ingress_kind="cloudflare")
        self.runtime.set_managed_public_url(
            "https://active-tunnel.trycloudflare.com",
            ingress_kind="cloudflare",
            expected_origin_host=origin_host,
        )
        manager._process = process
        manager._public_url = "https://active-tunnel.trycloudflare.com"
        manager._origin_host = origin_host

        with self.assertRaises(OSError):
            manager.stop()

        failed_status = manager.status()
        self.assertEqual(self.runtime.public_url(), "")
        self.assertFalse(self.runtime.verify_managed_ingress_origin(origin_host))
        self.assertTrue(failed_status["running"])
        self.assertTrue(failed_status["last_error"])
        self.assertIs(manager._process, process)

        stopped_status = manager.stop()

        self.assertEqual(process.terminate_calls, 2)
        self.assertFalse(stopped_status["running"])
        self.assertIsNone(manager._process)

    def test_invalid_manual_url_does_not_stop_the_active_tunnel(self):
        manager = PublicTunnelManager(
            public_invite_runtime=self.runtime,
            local_url="http://127.0.0.1:8765",
            which=lambda _name: "/bin/cloudflared",
        )
        process = FakeProcess()
        origin_host = self.runtime.prepare_managed_ingress(ingress_kind="cloudflare")
        manager._process = process
        manager._origin_host = origin_host

        with self.assertRaises(ValueError):
            manager.set_manual_public_url("not a public URL")

        self.assertIsNone(process.poll())
        self.assertIs(manager._process, process)
        self.assertTrue(self.runtime.verify_managed_ingress_origin(origin_host))

    def test_manual_transition_serializes_a_concurrent_tunnel_start(self):
        stop_started = threading.Event()
        release_stop = threading.Event()
        start_returned = threading.Event()
        launched: list[FakeProcess] = []

        class BlockingStopProcess(FakeProcess):
            def terminate(self):
                stop_started.set()

            def wait(self, timeout=None):
                del timeout
                self.assert_released()
                self._exit_code = 0
                return 0

            def assert_released(self):
                if not release_stop.wait(timeout=2):
                    raise AssertionError("stop was not released")

        old_process = BlockingStopProcess()

        def popen(_command, **_kwargs):
            process = FakeProcess()
            launched.append(process)
            return process

        manager = PublicTunnelManager(
            public_invite_runtime=self.runtime,
            local_url="http://127.0.0.1:8765",
            which=lambda _name: "/bin/cloudflared",
            popen=popen,
        )
        manager._process = old_process
        manager._origin_host = self.runtime.prepare_managed_ingress(
            ingress_kind="cloudflare"
        )

        manual = threading.Thread(
            target=lambda: manager.set_manual_public_url("https://manual.example.com")
        )
        starter = threading.Thread(
            target=lambda: (manager.start(), start_returned.set())
        )
        manual.start()
        self.assertTrue(stop_started.wait(timeout=2))
        starter.start()
        deadline = monotonic() + 0.2
        while monotonic() < deadline and not start_returned.is_set():
            threading.Event().wait(0.01)
        self.assertFalse(start_returned.is_set())
        self.assertEqual(launched, [])

        release_stop.set()
        manual.join(timeout=2)
        starter.join(timeout=2)

        self.assertFalse(manual.is_alive())
        self.assertFalse(starter.is_alive())
        self.assertTrue(start_returned.is_set())
        self.assertEqual(len(launched), 1)

    def test_manual_url_replaces_the_stable_entry_target(self):
        manager = PublicTunnelManager(
            public_invite_runtime=self.runtime,
            local_url="http://127.0.0.1:8765",
            which=lambda _name: "/bin/cloudflared",
        )
        announced: list[str] = []
        with mock.patch(
            "agentsassemble.application.public_tunnel.announce_stable_entry",
            lambda url: announced.append(url),
        ):
            manager.set_manual_public_url("https://manual.example.com")

        self.assertEqual(announced[-1], "https://manual.example.com")

    def test_exited_tunnel_clears_owned_runtime_public_url(self):
        manager = PublicTunnelManager(
            public_invite_runtime=self.runtime,
            local_url="http://127.0.0.1:8765",
            which=lambda _name: "/bin/cloudflared",
        )
        process = FakeProcess(exit_code=1)
        manager._process = process
        manager._public_url = "https://dead-tunnel.trycloudflare.com"
        origin_host = self.runtime.prepare_managed_ingress(ingress_kind="cloudflare")
        self.runtime.set_managed_public_url(
            "https://dead-tunnel.trycloudflare.com",
            ingress_kind="cloudflare",
            expected_origin_host=origin_host,
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
