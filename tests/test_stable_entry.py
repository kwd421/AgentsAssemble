from __future__ import annotations

import threading
import unittest
from types import SimpleNamespace
from unittest import mock

from agentsassemble.application import stable_entry
from agentsassemble.application.stable_entry import announce_stable_entry


class StableEntryPublicationTests(unittest.TestCase):
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
