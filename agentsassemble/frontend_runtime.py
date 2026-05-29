from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


REACT_APP_BUILD_COMMAND = "npm --prefix frontend run build"
REACT_APP_MISSING_BUILD_MESSAGE = f"React frontend build is not available. Run {REACT_APP_BUILD_COMMAND}."


@dataclass(frozen=True)
class FrontendDistStatus:
    root: Path
    index_present: bool
    assets_dir_present: bool

    @property
    def static_available(self) -> bool:
        return self.index_present and self.assets_dir_present


def default_frontend_dist_root() -> Path:
    return Path(__file__).resolve().parent.parent / "frontend" / "dist"


def frontend_dist_status(frontend_dist_root: Path | None = None) -> FrontendDistStatus:
    root = frontend_dist_root or default_frontend_dist_root()
    return FrontendDistStatus(
        root=root,
        index_present=(root / "index.html").is_file(),
        assets_dir_present=(root / "assets").is_dir(),
    )
