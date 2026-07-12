from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FRONTEND_SOURCE = ROOT / "frontend" / "src"
API_BARREL = FRONTEND_SOURCE / "api.ts"
API_MODULES = FRONTEND_SOURCE / "api"


def frontend_file(relative_path: str) -> str:
    return (FRONTEND_SOURCE / relative_path).read_text(encoding="utf-8")


def api_module_source(module_name: str) -> str:
    """Read one concrete API owner module, without hiding module ownership."""
    return (API_MODULES / f"{module_name}.ts").read_text(encoding="utf-8")


def api_barrel_source() -> str:
    return API_BARREL.read_text(encoding="utf-8")
