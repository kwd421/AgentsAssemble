"""Inspect the built React frontend before serving static assets."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import shutil
import urllib.parse
from uuid import uuid4


REACT_APP_BUILD_COMMAND = "npm --prefix frontend run build"
REACT_APP_MISSING_BUILD_MESSAGE = f"React frontend build is not available. Run {REACT_APP_BUILD_COMMAND}."


@dataclass(frozen=True)
class FrontendDistStatus:
    root: Path
    index_present: bool
    assets_dir_present: bool
    referenced_assets_present: bool

    @property
    def static_available(self) -> bool:
        return self.index_present and self.assets_dir_present and self.referenced_assets_present

    @property
    def build_status(self) -> str:
        if self.static_available:
            return "available"
        if not self.index_present and not self.assets_dir_present:
            return "missing"
        return "incomplete"


def default_frontend_dist_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"


def frontend_dist_status(frontend_dist_root: Path | None = None) -> FrontendDistStatus:
    root = frontend_dist_root or default_frontend_dist_root()
    index_path = root / "index.html"
    assets_dir = root / "assets"
    return FrontendDistStatus(
        root=root,
        index_present=index_path.is_file(),
        assets_dir_present=assets_dir.is_dir(),
        referenced_assets_present=_referenced_assets_present(index_path, assets_dir),
    )


def frontend_build_version(frontend_dist_root: Path | None = None) -> str:
    """Return the immutable browser build identity served by this process."""

    status = frontend_dist_status(frontend_dist_root)
    if not status.static_available:
        return "unavailable"
    try:
        payload = (status.root / "index.html").read_bytes()
    except OSError:
        return "unavailable"
    return hashlib.sha256(payload).hexdigest()[:16]


def materialize_frontend_release(
    frontend_dist_root: Path | None = None,
    *,
    release_root: Path,
) -> Path:
    """Copy one complete build into an immutable, generation-safe directory."""

    source = frontend_dist_status(frontend_dist_root)
    if not source.static_available:
        return source.root
    version = frontend_build_version(source.root)
    if version == "unavailable":
        return source.root
    target = Path(release_root) / version
    if (
        frontend_dist_status(target).static_available
        and frontend_build_version(target) == version
    ):
        return target

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.parent / f".{version}.{os.getpid()}.{uuid4().hex}.tmp"
    try:
        shutil.copytree(source.root, temporary)
        if (
            not frontend_dist_status(temporary).static_available
            or frontend_build_version(temporary) != version
        ):
            raise RuntimeError("Frontend build changed while its release was being prepared.")
        try:
            os.replace(temporary, target)
        except OSError:
            if not (
                frontend_dist_status(target).static_available
                and frontend_build_version(target) == version
            ):
                raise
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return target


def _referenced_assets_present(index_path: Path, assets_dir: Path) -> bool:
    if not index_path.is_file() or not assets_dir.is_dir():
        return False
    try:
        html = index_path.read_text(encoding="utf-8")
    except OSError:
        return False
    refs = re.findall(r"""(?:src|href)=["']/(?:app/)?assets/([^"']+)["']""", html)
    if not refs:
        return False
    for ref in refs:
        clean_ref = urllib.parse.unquote(ref.split("?", 1)[0].split("#", 1)[0])
        if not clean_ref or clean_ref.startswith("/") or ".." in Path(clean_ref).parts:
            return False
        if not (assets_dir / clean_ref).is_file():
            return False
    return True
