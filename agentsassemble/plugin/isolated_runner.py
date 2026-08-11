"""Minimal isolated launcher for bundled plugin server entrypoints."""

from __future__ import annotations

import argparse
import os
import runpy
import sys
from pathlib import Path


def _inside(path: Path, roots: tuple[Path, ...]) -> bool:
    resolved = path.resolve(strict=False)
    resolved_roots = tuple(root.resolve(strict=False) for root in roots)
    return any(resolved == root or root in resolved.parents for root in resolved_roots)


def _install_audit_policy(*, plugin_root: Path, storage_root: Path) -> None:
    readable = (
        plugin_root.resolve(),
        storage_root.resolve(),
        Path(sys.base_prefix).resolve(),
    )
    writable = (storage_root.resolve(),)

    def audit(event: str, args: tuple[object, ...]) -> None:
        if event == "open" and args:
            raw_path = args[0]
            if isinstance(raw_path, int):
                return
            path = Path(os.fsdecode(raw_path))
            mode = str(args[1] or "") if len(args) > 1 else ""
            flags = int(args[2] or 0) if len(args) > 2 and isinstance(args[2], int) else 0
            writes = any(marker in mode for marker in ("w", "a", "x", "+")) or bool(
                flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)
            )
            allowed = writable if writes else readable
            if not _inside(path, allowed):
                raise PermissionError(f"Plugin filesystem access denied: {path}")
            return
        if event.startswith("socket."):
            raise PermissionError("Plugin network access is denied.")
        if event in {
            "subprocess.Popen",
            "os.system",
            "os.posix_spawn",
            "os.posix_spawnp",
            "ctypes.dlopen",
        }:
            raise PermissionError(f"Plugin native execution is denied: {event}")

    sys.addaudithook(audit)


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--plugin-root", required=True)
    parser.add_argument("--entry", required=True)
    parser.add_argument("--storage", required=True)
    args = parser.parse_args()

    plugin_root = Path(args.plugin_root).resolve()
    entry = Path(args.entry).resolve()
    storage = Path(args.storage).resolve()
    if not _inside(entry, (plugin_root,)):
        raise SystemExit("Plugin entrypoint escaped its package root.")
    storage.mkdir(parents=True, exist_ok=True)
    os.environ.clear()
    os.environ.update(
        {
            "HOME": str(storage),
            "TMPDIR": str(storage),
            "XDG_CACHE_HOME": str(storage / "cache"),
            "XDG_CONFIG_HOME": str(storage / "config"),
            "XDG_DATA_HOME": str(storage / "data"),
        }
    )
    sys.path[:] = [str(entry.parent)] + [
        value for value in sys.path if value and _inside(Path(value), (Path(sys.base_prefix),))
    ]
    _install_audit_policy(plugin_root=plugin_root, storage_root=storage)
    runpy.run_path(str(entry), run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
