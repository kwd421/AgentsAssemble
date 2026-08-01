import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from agentsassemble.providers.sessions import inspect_provider_sessions
from agentsassemble.web.routes.providers import register_provider_routes
from agentsassemble.web.router import GuiDeps, RequestContext, Router


class _Handler:
    def __init__(self, path: str) -> None:
        self.path = path
        self.headers = {}
        self.payload: dict[str, object] | None = None

    def _send_json(self, payload: dict[str, object]) -> None:
        self.payload = payload


class _Login:
    def start(self, payload: dict[str, object]) -> dict[str, object]:
        return payload

    def record_invalid_json(self) -> None:
        return None


class ProviderSessionListingErrorTests(unittest.TestCase):
    def test_corrupt_provider_store_is_not_reported_as_an_empty_store(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            database = home / ".local" / "share" / "opencode" / "opencode.db"
            database.parent.mkdir(parents=True)
            database.write_text("not a sqlite database", encoding="utf-8")

            listing = inspect_provider_sessions(
                "opencode_server",
                home=home,
                workspace="/workspace",
            )

        self.assertEqual(listing.status, "error")
        self.assertEqual(listing.sessions, [])
        self.assertEqual(listing.error_code, "provider_session_discovery_failed")

    def test_route_preserves_the_discovery_error_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            database = home / ".local" / "share" / "opencode" / "opencode.db"
            database.parent.mkdir(parents=True)
            database.write_text("not a sqlite database", encoding="utf-8")
            router = Router()
            register_provider_routes(
                router,
                credentials_allowed=lambda ctx: True,
                is_local_operator=lambda ctx: True,
                login_service=_Login(),
                session_inspector=lambda provider_kind, *, workspace: inspect_provider_sessions(
                    provider_kind,
                    workspace=workspace,
                    home=home,
                ),
            )
            path = "/api/provider-sessions/local?provider_kind=opencode_server&workspace=/work"
            parsed = urlparse(path)
            handler = _Handler(path)
            context = RequestContext(
                handler,
                GuiDeps(output_root=Path(".")),
                parsed,
                parse_qs(parsed.query),
            )

            self.assertTrue(router.dispatch("GET", context))

        self.assertEqual(handler.payload["status"], "error")
        self.assertEqual(handler.payload["sessions"], [])
        self.assertEqual(handler.payload["error_code"], "provider_session_discovery_failed")


if __name__ == "__main__":
    unittest.main()
