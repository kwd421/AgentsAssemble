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


def discover_claude_xhigh_model_ids(executable: str) -> list[str]:
    """Return installed Claude models whose registry advertises xhigh effort."""

    path = Path(executable).expanduser().resolve()
    stat = path.stat()
    return list(
        _discover_claude_xhigh_model_ids(
            str(path),
            stat.st_mtime_ns,
            stat.st_size,
        )
    )


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


@lru_cache(maxsize=4)
def _discover_claude_xhigh_model_ids(
    executable: str,
    _mtime_ns: int,
    _size: int,
) -> tuple[str, ...]:
    models = _discover_claude_model_ids(executable, _mtime_ns, _size)
    if not models:
        return ()
    encoded_models = [model.encode("ascii") for model in models]
    supported: set[str] = set()
    with Path(executable).open("rb") as stream:
        with mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as binary:
            for model, encoded_model in zip(models, encoded_models, strict=True):
                if _model_registry_advertises_xhigh(
                    binary,
                    encoded_model,
                    encoded_models,
                ):
                    supported.add(model)
    return tuple(sorted(supported, key=_model_sort_key))


def _model_registry_advertises_xhigh(
    binary: mmap.mmap,
    model: bytes,
    all_models: list[bytes],
) -> bool:
    """Find the model's feature record without relying on a fixed binary offset."""

    cursor = 0
    while True:
        start = binary.find(model, cursor)
        if start < 0:
            return False
        segment_start = start + len(model)
        search_limit = min(len(binary), segment_start + 8192)
        next_model_positions = [
            position
            for candidate in all_models
            if (position := binary.find(candidate, segment_start, search_limit)) >= 0
        ]
        segment_end = min(next_model_positions, default=search_limit)
        segment = binary[start:segment_end]
        if b"context_management" in segment and b"effort" in segment:
            return b"xhigh_effort" in segment
        cursor = segment_start


def _model_sort_key(model_id: str) -> tuple[int, tuple[int, ...], str]:
    _, family, *version = model_id.split("-")
    return (
        _FAMILY_ORDER.get(family, len(_FAMILY_ORDER)),
        tuple(-int(part) for part in version),
        model_id,
    )


__all__ = [
    "discover_claude_model_ids",
    "discover_claude_xhigh_model_ids",
]
