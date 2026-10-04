"""React application and bootstrap-document delivery for the GUI server."""
from __future__ import annotations

import mimetypes
from collections.abc import Callable
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from agentsassemble.plugin.manifest import load_first_party_manifests
from agentsassemble.web.router import request_server_url


REACT_APP_EXACT_PATHS = frozenset(
    {
        "/",
        "/api",
        "/api/",
        "/app",
        "/app/",
        "/join",
        "/join/",
        "/pair",
        "/pair/",
    }
)

_REACT_APP_CONTENT_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".ico": "image/x-icon",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".map": "application/json; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
    ".woff2": "font/woff2",
}


@dataclass(frozen=True)
class ReactStaticTransport:
    frontend_root: Path
    pre_join_guide_payload: Callable[[str], dict[str, object]]
    api_catalog_payload: Callable[[str], dict[str, object]]

    def dispatch_get(
        self,
        handler: Any,
        *,
        path: str,
        query: dict[str, list[str]],
    ) -> bool:
        if path == "/":
            handler._send_react_app_index(self.frontend_root)
            return True
        if path in {"/app", "/app/"}:
            handler._send_react_app_index(self.frontend_root)
            return True
        if path in {"/join", "/join/"}:
            accepts_json = "application/json" in str(
                handler.headers.get("Accept") or ""
            )
            wants_json = (
                accepts_json
                or str(query.get("format", [""])[0]).lower() == "json"
            )
            if wants_json:
                handler._send_json(
                    self.pre_join_guide_payload(request_server_url(handler))
                )
            else:
                handler._send_react_app_index(self.frontend_root)
            return True
        if path in {"/pair", "/pair/"}:
            handler._send_react_app_index(self.frontend_root)
            return True
        if path in {"/api", "/api/"}:
            handler._send_json(
                self.api_catalog_payload(request_server_url(handler))
            )
            return True
        if path.startswith("/app/"):
            relative_path = unquote(path.removeprefix("/app/"))
            app_path = safe_static_path(self.frontend_root, relative_path)
            if app_path is None:
                handler._send_error(HTTPStatus.NOT_FOUND, "File not found")
            elif app_path.name == "index.html":
                handler._send_react_app_index(self.frontend_root)
            else:
                handler._send_file(
                    app_path,
                    react_app_content_type(app_path),
                    cache_control=react_app_cache_control(app_path),
                )
            return True
        if path.startswith("/plugins/"):
            relative_path = unquote(path.removeprefix("/plugins/"))
            plugin_path = public_plugin_asset_path(relative_path)
            if plugin_path is None or not plugin_path.is_file():
                handler._send_error(HTTPStatus.NOT_FOUND, "Plugin asset not found")
            else:
                handler._send_file(
                    plugin_path,
                    react_app_content_type(plugin_path),
                    cache_control="no-store",
                    allow_same_origin_frame=True,
                )
            return True
        return False


def safe_static_path(static_root: Path, relative_path: str) -> Path | None:
    root = static_root.resolve()
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def public_plugin_asset_path(relative_path: str) -> Path | None:
    """Resolve only a declared plugin's web asset directory.

    Server entrypoints, manifests, and agent tool source live beside the web
    directory but are not public HTTP assets.
    """

    plugin_id, separator, asset_path = relative_path.partition("/")
    if not separator or not plugin_id or not asset_path:
        return None
    manifest = next(
        (item for item in load_first_party_manifests() if item.id == plugin_id),
        None,
    )
    if manifest is None:
        return None
    web_root = (manifest.root / manifest.web_entry).resolve().parent
    web_prefix = Path(manifest.web_entry).parent.as_posix().strip("/")
    if web_prefix:
        prefix = f"{web_prefix}/"
        if not asset_path.startswith(prefix):
            return None
        asset_path = asset_path.removeprefix(prefix)
    return safe_static_path(web_root, asset_path)


def react_app_content_type(path: Path) -> str:
    return (
        _REACT_APP_CONTENT_TYPES.get(path.suffix.lower())
        or mimetypes.guess_type(path.name)[0]
        or "application/octet-stream"
    )


def react_app_cache_control(_path: Path) -> str:
    return "no-cache"
