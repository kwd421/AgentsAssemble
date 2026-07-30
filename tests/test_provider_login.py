from __future__ import annotations

import unittest

from agentsassemble.providers.login import ProviderLoginService


class _CompletedLoginProcess:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def wait(self, timeout: float | None = None) -> int:
        self.events.append(f"wait:{timeout:g}")
        return 0


class ProviderLoginTests(unittest.TestCase):
    def test_browser_oauth_waits_for_completion_then_refreshes_catalog(self):
        events: list[str] = []
        process = _CompletedLoginProcess(events)
        service = ProviderLoginService(
            command_resolver=lambda executable: f"/resolved/{executable}",
            command_launcher=lambda command: (
                events.append(f"launch:{' '.join(command)}"),
                process,
            )[1],
            catalog_refresher=lambda: events.append("refresh"),
        )

        result = service.start({"provider_id": "cursor"})

        self.assertEqual(result["status"], "authenticated")
        self.assertEqual(
            events,
            [
                "launch:/resolved/cursor-agent login",
                "wait:600",
                "refresh",
            ],
        )


if __name__ == "__main__":
    unittest.main()
