"""Reject unowned growth in already-large source files.

Line count is only a pressure signal. The policy does not require a coherent
file to be split merely because it is long; it records a ceiling so that adding
another responsibility requires an explicit architecture decision.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import tomllib
from typing import Mapping


POLICY_RELATIVE_PATH = Path("docs/product/SOURCE_GROWTH_LIMITS.toml")
SOURCE_ROOTS = (
    Path("agentsassemble"),
    Path("frontend/src"),
    Path("scripts"),
    Path("tests"),
)
SOURCE_SUFFIXES = frozenset({".css", ".js", ".jsx", ".py", ".ts", ".tsx"})


@dataclass(frozen=True)
class SourceGrowthPolicy:
    new_file_line_limit: int
    file_limits: Mapping[str, int]


def load_source_growth_policy(repository_root: Path) -> SourceGrowthPolicy:
    root = Path(repository_root)
    payload = tomllib.loads(
        (root / POLICY_RELATIVE_PATH).read_text(encoding="utf-8")
    )
    policy = payload.get("policy")
    file_limits = payload.get("file_limits")
    if not isinstance(policy, dict) or not isinstance(file_limits, dict):
        raise ValueError("Source growth policy needs [policy] and [file_limits].")

    limit = policy.get("new_file_line_limit")
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError("new_file_line_limit must be a positive integer.")

    paths = list(file_limits)
    if paths != sorted(paths):
        raise ValueError("Source growth file limits must be sorted by path.")
    normalized_limits: dict[str, int] = {}
    for relative_path, ceiling in file_limits.items():
        path = Path(relative_path)
        if (
            path.is_absolute()
            or ".." in path.parts
            or path.as_posix() != relative_path
        ):
            raise ValueError(f"Invalid source growth path: {relative_path!r}")
        if (
            not isinstance(ceiling, int)
            or isinstance(ceiling, bool)
            or ceiling < 1
        ):
            raise ValueError(
                f"Source growth ceiling for {relative_path!r} must be positive."
            )
        normalized_limits[relative_path] = ceiling
    return SourceGrowthPolicy(
        new_file_line_limit=limit,
        file_limits=normalized_limits,
    )


def collect_source_line_counts(repository_root: Path) -> dict[str, int]:
    root = Path(repository_root)
    counts: dict[str, int] = {}
    for source_root in SOURCE_ROOTS:
        absolute_root = root / source_root
        if not absolute_root.exists():
            continue
        for path in absolute_root.rglob("*"):
            if not _is_tracked_source(path):
                continue
            relative_path = path.relative_to(root).as_posix()
            counts[relative_path] = len(
                path.read_text(encoding="utf-8").splitlines()
            )
    return dict(sorted(counts.items()))


def source_growth_violations(
    line_counts: Mapping[str, int],
    policy: SourceGrowthPolicy,
) -> tuple[str, ...]:
    violations = []
    for relative_path, line_count in sorted(line_counts.items()):
        ceiling = policy.file_limits.get(relative_path)
        if ceiling is not None and line_count > ceiling:
            violations.append(
                f"{relative_path}: {line_count} lines exceeds its recorded "
                f"ceiling of {ceiling}"
            )
        elif ceiling is None and line_count > policy.new_file_line_limit:
            violations.append(
                f"{relative_path}: {line_count} lines exceeds the unowned-file "
                f"limit of {policy.new_file_line_limit}"
            )

    missing = sorted(set(policy.file_limits) - set(line_counts))
    violations.extend(
        f"{relative_path}: recorded source file is missing" for relative_path in missing
    )
    return tuple(violations)


def _is_tracked_source(path: Path) -> bool:
    return (
        path.is_file()
        and path.suffix.lower() in SOURCE_SUFFIXES
        and not path.name.lower().startswith("generated")
        and "__pycache__" not in path.parts
        and "node_modules" not in path.parts
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    policy = load_source_growth_policy(root)
    violations = source_growth_violations(
        collect_source_line_counts(root),
        policy,
    )
    if not violations:
        return 0
    print("Source growth violations:")
    for violation in violations:
        print(f"- {violation}")
    print(
        "Split the new responsibility at its owning boundary. Do not raise or "
        "remove a recorded ceiling without an explicit user decision."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
