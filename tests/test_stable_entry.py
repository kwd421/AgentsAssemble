from __future__ import annotations

import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import mock

from agentsassemble.application import stable_entry
from agentsassemble.application.public_invite_runtime import PublicInviteRuntime
from agentsassemble.application.public_tunnel import PublicTunnelManager
from agentsassemble.application.stable_entry import (
    StableEntryPublisher,
    announce_stable_entry,
)


class StableEntryPublicationTests(unittest.TestCase):
    def test_rolling_replacement_cannot_be_overwritten_by_previous_owner(self) -> None:
        old_url = "https://old-tunnel.trycloudflare.com"
        new_url = "https://new-tunnel.trycloudflare.com"
        old_started = threading.Event()
        release_old = threading.Event()
        commands: list[list[str]] = []

        def run_wrangler(command, **_kwargs):
            commands.append(command)
            if old_url in command:
                old_started.set()
                self.assertTrue(release_old.wait(timeout=2))
            return SimpleNamespace(returncode=0)

        config = {
            "url": "https://stable-entry.example",
            "namespace_id": "namespace-1",
            "kv_key": "target",
        }
        with TemporaryDirectory() as temporary_root:
            root = Path(temporary_root)
            previous = StableEntryPublisher(
                state_root=root,
                owner_id="runtime-old",
                config_provider=lambda: config,
                command_runner=run_wrangler,
            )
            replacement = StableEntryPublisher(
                state_root=root,
                owner_id="runtime-new",
                predecessor_owner_id="runtime-old",
                active=False,
                config_provider=lambda: config,
                command_runner=run_wrangler,
            )

            old_publication = previous.announce(old_url)
            self.assertIsNotNone(old_publication)
            self.assertTrue(old_started.wait(timeout=2))
            self.assertIsNone(replacement.announce(new_url))

            activated: list[threading.Thread | None] = []
            activation_started = threading.Event()

            def activate_replacement() -> None:
                activation_started.set()
                activated.append(replacement.activate())

            activation = threading.Thread(
                target=activate_replacement
            )
            activation.start()
            self.assertTrue(activation_started.wait(timeout=1))
            self.assertTrue(activation.is_alive())
            release_old.set()
            old_publication.join(timeout=2)  # type: ignore[union-attr]
            activation.join(timeout=2)
            self.assertFalse(activation.is_alive())
            self.assertEqual(len(activated), 1)
            self.assertIsNotNone(activated[0])
            activated[0].join(timeout=2)  # type: ignore[union-attr]

            stale_clear = previous.clear()
            self.assertIsNotNone(stale_clear)
            stale_clear.join(timeout=2)  # type: ignore[union-attr]

        published_urls = [
            next(part for part in command if str(part).startswith("https://"))
            for command in commands
            if "put" in command
        ]
        self.assertEqual(published_urls, [old_url, new_url])
        self.assertFalse(any("delete" in command for command in commands))

    def test_switching_manual_https_to_http_clears_the_external_stable_target(
        self,
    ) -> None:
        first_publish_finished = threading.Event()
        clear_finished = threading.Event()
        commands: list[list[str]] = []

        def run_wrangler(command, **_kwargs):
            commands.append(command)
            if "delete" in command:
                clear_finished.set()
            else:
                first_publish_finished.set()
            return SimpleNamespace(returncode=0)

        config = {
            "url": "https://stable-entry.example",
            "namespace_id": "namespace-1",
            "kv_key": "target",
        }
        manager = PublicTunnelManager(
            public_invite_runtime=PublicInviteRuntime(environ={}),
        )
        with (
            mock.patch(
                "agentsassemble.application.stable_entry.stable_entry_config",
                return_value=config,
            ),
            mock.patch(
                "agentsassemble.application.stable_entry.subprocess.run",
                side_effect=run_wrangler,
            ),
        ):
            manager.set_manual_public_url("https://manual.example.com")
            self.assertTrue(first_publish_finished.wait(timeout=2))
            manager.set_manual_public_url("")
            self.assertTrue(clear_finished.wait(timeout=1))

        self.assertIn("delete", commands[-1])

    def test_older_blocked_publication_cannot_overwrite_a_newer_url(self) -> None:
        old_url = "https://old-tunnel.trycloudflare.com"
        new_url = "https://new-tunnel.trycloudflare.com"
        old_started = threading.Event()
        release_old = threading.Event()
        both_published = threading.Event()
        published: list[str] = []
        published_lock = threading.Lock()

        def run_wrangler(command, **_kwargs):
            public_url = next(part for part in command if str(part).startswith("https://"))
            if public_url == old_url:
                old_started.set()
                self.assertTrue(release_old.wait(timeout=2))
            with published_lock:
                published.append(public_url)
                if len(published) == 2:
                    both_published.set()
            return SimpleNamespace(returncode=0)

        config = {
            "url": "https://stable-entry.example",
            "namespace_id": "namespace-1",
            "kv_key": "target",
        }
        with (
            mock.patch(
                "agentsassemble.application.stable_entry.stable_entry_config",
                return_value=config,
            ),
            mock.patch(
                "agentsassemble.application.stable_entry.subprocess.run",
                side_effect=run_wrangler,
            ),
        ):
            announce_stable_entry(old_url)
            self.assertTrue(old_started.wait(timeout=2))
            announce_stable_entry(new_url)
            release_timer = threading.Timer(0.2, release_old.set)
            release_timer.start()
            try:
                self.assertTrue(both_published.wait(timeout=3))
            finally:
                release_timer.cancel()
                release_old.set()

        self.assertEqual(published[-1], new_url)

    def test_newer_clear_finishes_after_an_older_blocked_publication(self) -> None:
        old_started = threading.Event()
        release_old = threading.Event()
        clear_finished = threading.Event()
        commands: list[list[str]] = []

        def run_wrangler(command, **_kwargs):
            commands.append(command)
            if "put" in command:
                old_started.set()
                self.assertTrue(release_old.wait(timeout=2))
            else:
                clear_finished.set()
            return SimpleNamespace(returncode=0)

        config = {
            "url": "https://stable-entry.example",
            "namespace_id": "namespace-1",
            "kv_key": "target",
        }
        with (
            mock.patch(
                "agentsassemble.application.stable_entry.stable_entry_config",
                return_value=config,
            ),
            mock.patch(
                "agentsassemble.application.stable_entry.subprocess.run",
                side_effect=run_wrangler,
            ),
        ):
            announce_stable_entry("https://old-tunnel.trycloudflare.com")
            self.assertTrue(old_started.wait(timeout=2))
            stable_entry.clear_stable_entry()
            release_old.set()
            self.assertTrue(clear_finished.wait(timeout=3))

        self.assertIn("delete", commands[-1])


if __name__ == "__main__":
    unittest.main()
