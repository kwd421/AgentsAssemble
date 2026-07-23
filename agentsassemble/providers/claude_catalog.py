"""Read exact model identifiers from the installed Claude Code registry."""

from __future__ import annotations

import json
import mmap
import re
from functools import lru_cache
from pathlib import Path


_REGISTRY_PREFIX = b'["claude-3-5-haiku"'
_EXACT_MODEL = re.compile(r"^claude-(?:fable|haiku|opus|sonnet)-\d+(?:-\d+)?$")
_FAMILY_ORDER = {"haiku": 0, "sonnet": 1, "opus": 2, "fable": 3}


def discover_claude_model_ids(executable: str) -> list[str]:
    path = Path(executable).expanduser().resolve()
    stat = path.stat()
    return list(_discover_claude_model_ids(str(path), stat.st_mtime_ns, stat.st_size))


@lru_cache(maxsize=4)
def _discover_claude_model_ids(
    executable: str,
    _mtime_ns: int,
    _size: int,
) -> tuple[str, ...]:
    with Path(executable).open("rb") as stream:
        with mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as binary:
            start = binary.find(_REGISTRY_PREFIX)
            if start < 0:
                return ()
            end = binary.find(b"]", start, min(len(binary), start + 16_384))
            if end < 0:
                return ()
            values = json.loads(binary[start : end + 1].decode("ascii"))
    exact = {
        value
        for value in values
        if isinstance(value, str) and _EXACT_MODEL.fullmatch(value)
    }
    return tuple(sorted(exact, key=_model_sort_key))


def _model_sort_key(model_id: str) -> tuple[int, tuple[int, ...], str]:
    _, family, *version = model_id.split("-")
    return (
        _FAMILY_ORDER.get(family, len(_FAMILY_ORDER)),
        tuple(-int(part) for part in version),
        model_id,
    )


__all__ = ["discover_claude_model_ids"]
