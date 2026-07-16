"""Guard the package root against new unowned product modules."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


BASELINE_RELATIVE_PATH = Path("docs/product/PACKAGE_ROOT_BASELINE.txt")
PERMANENT_ROOT_ENTRYPOINTS = frozenset({"__init__.py", "cli.py", "gui.py"})


@dataclass(frozen=True)
class CompatibilityShim:
    replacement_import: str
    removal_gate: str


# A new root shim must be reviewed here instead of being added to the historical
# baseline. The replacement and removal gate keep the exception temporary.
ROOT_COMPATIBILITY_SHIMS: dict[str, CompatibilityShim] = {}


def current_top_level_modules(repository_root: Path) -> frozenset[str]:
    package_root = Path(repository_root) / "agentsassemble"
    return frozenset(path.name for path in package_root.glob("*.py"))


def load_root_baseline(repository_root: Path) -> frozenset[str]:
    path = Path(repository_root) / BASELINE_RELATIVE_PATH
    entries = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line and not line.startswith("#"):
            entries.append(line)
    if entries != sorted(set(entries)):
        raise ValueError("Package root baseline must be unique and sorted.")
    invalid = [entry for entry in entries if Path(entry).name != entry or not entry.endswith(".py")]
    if invalid:
        raise ValueError("Package root baseline contains invalid module paths.")
    return frozenset(entries)


def unexpected_top_level_modules(
    current: Iterable[str],
    baseline: Iterable[str],
) -> tuple[str, ...]:
    allowed = (
        frozenset(baseline)
        | PERMANENT_ROOT_ENTRYPOINTS
        | frozenset(ROOT_COMPATIBILITY_SHIMS)
    )
    return tuple(sorted(frozenset(current) - allowed))


def validate_compatibility_shims() -> None:
    for filename, shim in ROOT_COMPATIBILITY_SHIMS.items():
        if Path(filename).name != filename or not filename.endswith(".py"):
            raise ValueError(f"Invalid compatibility shim filename: {filename!r}")
        if not shim.replacement_import.startswith("agentsassemble."):
            raise ValueError(f"Compatibility shim {filename!r} needs a replacement import.")
        if not shim.removal_gate.strip():
            raise ValueError(f"Compatibility shim {filename!r} needs a removal gate.")


def initialize_root_baseline(repository_root: Path) -> Path:
    root = Path(repository_root)
    path = root / BASELINE_RELATIVE_PATH
    if path.exists():
        raise FileExistsError(f"Refusing to replace existing package baseline: {path}")
    entries = sorted(current_top_level_modules(root))
    content = "\n".join(
        (
            "# Historical AgentsAssemble package-root baseline captured 2026-07-16.",
            "# Do not add new files here. New root compatibility shims belong in",
            "# ROOT_COMPATIBILITY_SHIMS with a replacement import and removal gate.",
            *entries,
            "",
        )
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--initialize-baseline", action="store_true")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    if args.initialize_baseline:
        path = initialize_root_baseline(root)
        print(f"Wrote {path.relative_to(root)}")
        return 0
    validate_compatibility_shims()
    unexpected = unexpected_top_level_modules(
        current_top_level_modules(root),
        load_root_baseline(root),
    )
    if unexpected:
        print("Unowned top-level product modules: " + ", ".join(unexpected))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
