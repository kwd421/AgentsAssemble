from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import urllib.parse


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
    return Path(__file__).resolve().parent.parent / "frontend" / "dist"


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


def _referenced_assets_present(index_path: Path, assets_dir: Path) -> bool:
    if not index_path.is_file() or not assets_dir.is_dir():
        return False
    try:
        html = index_path.read_text(encoding="utf-8")
    except OSError:
        return False
    refs = re.findall(r"""(?:src|href)=["']/assets/([^"']+)["']""", html)
    if not refs:
        return False
    for ref in refs:
        clean_ref = urllib.parse.unquote(ref.split("?", 1)[0].split("#", 1)[0])
        if not clean_ref or clean_ref.startswith("/") or ".." in Path(clean_ref).parts:
            return False
        if not (assets_dir / clean_ref).is_file():
            return False
    return True
