from __future__ import annotations

from pathlib import Path


def prepare_codex_output_file(path: Path) -> None:
    """Ensure Codex output accepted after this point belongs to the next call."""

    path.unlink(missing_ok=True)
