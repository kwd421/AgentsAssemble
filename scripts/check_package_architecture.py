"""Guard the package root against new unowned product modules."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
from typing import Iterable, Mapping

try:
    from scripts.compatibility_shims import (
        SHIM_RETIREMENT_RELATIVE_PATH,
        analyze_compatibility_shim_usage,
        load_compatibility_shims,
        render_shim_retirement_report,
        unexpected_compatibility_callers,
    )
    from scripts.generate_package_map import PackageGraph, load_package_graph
except ModuleNotFoundError as error:
    if error.name != "scripts":
        raise
    from compatibility_shims import (
        SHIM_RETIREMENT_RELATIVE_PATH,
        analyze_compatibility_shim_usage,
        load_compatibility_shims,
        render_shim_retirement_report,
        unexpected_compatibility_callers,
    )
    from generate_package_map import PackageGraph, load_package_graph


BASELINE_RELATIVE_PATH = Path("docs/product/PACKAGE_ROOT_BASELINE.txt")
CYCLE_BASELINE_RELATIVE_PATH = Path("docs/product/PACKAGE_CYCLE_BASELINE.txt")
CYCLE_REPORT_RELATIVE_PATH = Path("docs/product/PACKAGE_CYCLES.md")
PERMANENT_ROOT_ENTRYPOINTS = frozenset({"__init__.py", "cli.py", "gui.py"})
CURRENT_CORE_PACKAGE_ROOTS = frozenset(
    {
        "admission",
        "application",
        "diagnostics",
        "identity",
        "persistence",
        "providers",
        "room",
        "web",
    }
)
DOMAIN_PACKAGE_ROOTS = frozenset(
    {"admission", "diagnostics", "identity", "providers", "room"}
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
ROOT_COMPATIBILITY_SHIMS = load_compatibility_shims(REPOSITORY_ROOT)


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


def validate_compatibility_shims(
    repository_root: Path = REPOSITORY_ROOT,
) -> None:
    root = Path(repository_root)
    for filename, shim in ROOT_COMPATIBILITY_SHIMS.items():
        if Path(filename).name != filename or not filename.endswith(".py"):
            raise ValueError(f"Invalid compatibility shim filename: {filename!r}")
        if not shim.replacement_import.startswith("agentsassemble."):
            raise ValueError(f"Compatibility shim {filename!r} needs a replacement import.")
        if not shim.removal_gate.strip():
            raise ValueError(f"Compatibility shim {filename!r} needs a removal gate.")
        if not shim.export_policy.strip():
            raise ValueError(f"Compatibility shim {filename!r} needs an export policy.")
        if any(not caller.strip() for caller in shim.allowed_callers):
            raise ValueError(f"Compatibility shim {filename!r} has an empty allowed caller.")
        if not shim.introduced_in.strip():
            raise ValueError(
                f"Compatibility shim {filename!r} needs introduction metadata."
            )
        if not (root / "agentsassemble" / filename).is_file():
            raise ValueError(f"Compatibility shim {filename!r} does not exist.")
        missing_callers = [
            caller for caller in shim.allowed_callers if not (root / caller).is_file()
        ]
        if missing_callers:
            raise ValueError(
                f"Compatibility shim {filename!r} has missing allowed callers: "
                + ", ".join(missing_callers)
            )


def dependency_direction_violations(graph: PackageGraph) -> tuple[str, ...]:
    violations = []
    for source_name in sorted(graph.modules):
        source_root = _migrated_package_root(graph.modules[source_name].relative_path)
        if not source_root:
            continue
        for imported_name in graph.imports_by_module[source_name]:
            imported_domain = graph.domains[imported_name]
            imported_classification = graph.classifications[imported_name]
            if (
                source_root in CURRENT_CORE_PACKAGE_ROOTS
                and imported_classification == "legacy"
            ):
                violations.append(
                    f"{source_name} imports legacy module {imported_name}"
                )
            if source_root in DOMAIN_PACKAGE_ROOTS and imported_domain == "web":
                violations.append(
                    f"{source_name} imports web module {imported_name}"
                )
            if source_root == "web" and _is_concrete_persistence_module(
                imported_name,
                graph.modules[imported_name].relative_path,
            ):
                violations.append(
                    f"{source_name} imports concrete persistence module {imported_name}"
                )
    return tuple(sorted(set(violations)))


def import_cycles(
    imports_by_module: Mapping[str, tuple[str, ...]],
) -> tuple[tuple[str, ...], ...]:
    index = 0
    stack: list[str] = []
    on_stack: set[str] = set()
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    cycles: list[tuple[str, ...]] = []

    def visit(module_name: str) -> None:
        nonlocal index
        indices[module_name] = index
        lowlinks[module_name] = index
        index += 1
        stack.append(module_name)
        on_stack.add(module_name)
        for imported_name in imports_by_module.get(module_name, ()):
            if imported_name not in indices:
                visit(imported_name)
                lowlinks[module_name] = min(
                    lowlinks[module_name],
                    lowlinks[imported_name],
                )
            elif imported_name in on_stack:
                lowlinks[module_name] = min(
                    lowlinks[module_name],
                    indices[imported_name],
                )
        if lowlinks[module_name] != indices[module_name]:
            return
        component = []
        while True:
            member = stack.pop()
            on_stack.remove(member)
            component.append(member)
            if member == module_name:
                break
        if len(component) > 1 or module_name in imports_by_module.get(module_name, ()):
            cycles.append(tuple(sorted(component)))

    for module_name in sorted(imports_by_module):
        if module_name not in indices:
            visit(module_name)
    return tuple(sorted(cycles, key=lambda cycle: (len(cycle), cycle)))


def load_cycle_baseline(repository_root: Path) -> frozenset[tuple[str, ...]]:
    path = Path(repository_root) / CYCLE_BASELINE_RELATIVE_PATH
    cycles = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        modules = tuple(part.strip() for part in line.split("|") if part.strip())
        if len(modules) < 2 or modules != tuple(sorted(set(modules))):
            raise ValueError("Package cycle baseline must contain sorted unique cycles.")
        cycles.append(modules)
    if cycles != sorted(set(cycles), key=lambda cycle: (len(cycle), cycle)):
        raise ValueError("Package cycle baseline must be unique and sorted.")
    return frozenset(cycles)


def new_import_cycles(
    current_cycles: Iterable[tuple[str, ...]],
    baseline_cycles: Iterable[tuple[str, ...]],
) -> tuple[tuple[str, ...], ...]:
    baseline = frozenset(baseline_cycles)
    return tuple(
        sorted(
            (cycle for cycle in current_cycles if cycle not in baseline),
            key=lambda cycle: (len(cycle), cycle),
        )
    )


def render_cycle_report(
    graph: PackageGraph,
    baseline_cycles: Iterable[tuple[str, ...]],
) -> str:
    cycles = import_cycles(graph.imports_by_module)
    baseline = frozenset(baseline_cycles)
    fingerprint = hashlib.sha256(
        "\n".join("|".join(cycle) for cycle in cycles).encode("utf-8")
    ).hexdigest()[:16]
    lines = [
        "# Package Import Cycles",
        "",
        "Status: generated architecture report",
        "",
        "Generator: `python3 scripts/check_package_architecture.py --write-cycle-report`",
        "",
        f"Source fingerprint: `{fingerprint}`",
        "",
        f"- Current import cycles: {len(cycles)}",
        f"- Grandfathered exact cycles: {sum(cycle in baseline for cycle in cycles)}",
        f"- New cycles: {sum(cycle not in baseline for cycle in cycles)}",
        "",
        "An exact historical cycle may disappear without updating the baseline. Any",
        "changed or newly introduced cycle fails the architecture gate. A cycle that",
        "moves into a target package is therefore not silently grandfathered.",
        "",
        "## Current Cycles",
        "",
    ]
    if not cycles:
        lines.append("- None")
    for cycle in cycles:
        status = "grandfathered" if cycle in baseline else "new"
        lines.append(f"- **{status}**: " + " -> ".join(f"`{name}`" for name in cycle))
    return "\n".join(lines) + "\n"


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
            "# docs/product/compatibility_shims.toml with retirement metadata.",
            *entries,
            "",
        )
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def initialize_cycle_baseline(repository_root: Path) -> Path:
    root = Path(repository_root)
    path = root / CYCLE_BASELINE_RELATIVE_PATH
    if path.exists():
        raise FileExistsError(f"Refusing to replace existing cycle baseline: {path}")
    cycles = import_cycles(load_package_graph(root).imports_by_module)
    content = "\n".join(
        (
            "# Exact import cycles grandfathered at the 2026-07-16 architecture gate.",
            "# Cycles may disappear, but changed or new cycles must not be added here.",
            *(" | ".join(cycle) for cycle in cycles),
            "",
        )
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def write_cycle_report(repository_root: Path) -> Path:
    root = Path(repository_root)
    path = root / CYCLE_REPORT_RELATIVE_PATH
    path.write_text(
        render_cycle_report(load_package_graph(root), load_cycle_baseline(root)),
        encoding="utf-8",
    )
    return path


def write_shim_retirement_report(repository_root: Path) -> Path:
    root = Path(repository_root)
    graph = load_package_graph(root)
    shims = load_compatibility_shims(root)
    usage = analyze_compatibility_shim_usage(root, graph, shims)
    path = root / SHIM_RETIREMENT_RELATIVE_PATH
    path.write_text(
        render_shim_retirement_report(shims, usage),
        encoding="utf-8",
    )
    return path


def _migrated_package_root(relative_path: str) -> str:
    parts = Path(relative_path).parts
    if len(parts) < 3 or parts[0] != "agentsassemble":
        return ""
    root = parts[1]
    return root if root in CURRENT_CORE_PACKAGE_ROOTS else ""


def _is_concrete_persistence_module(module_name: str, relative_path: str) -> bool:
    stem = Path(relative_path).stem
    return (
        module_name.startswith("agentsassemble.persistence.postgres")
        or module_name.startswith("agentsassemble.persistence.local")
        or stem.startswith("postgres_")
        or stem.startswith("sqlite_")
        or stem in {"identity_store", "room_store"}
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--initialize-baseline", action="store_true")
    parser.add_argument("--initialize-cycle-baseline", action="store_true")
    parser.add_argument("--write-cycle-report", action="store_true")
    parser.add_argument("--write-shim-report", action="store_true")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    if args.initialize_baseline:
        path = initialize_root_baseline(root)
        print(f"Wrote {path.relative_to(root)}")
        return 0
    if args.initialize_cycle_baseline:
        path = initialize_cycle_baseline(root)
        print(f"Wrote {path.relative_to(root)}")
        return 0
    if args.write_cycle_report:
        path = write_cycle_report(root)
        print(f"Wrote {path.relative_to(root)}")
        return 0
    if args.write_shim_report:
        path = write_shim_retirement_report(root)
        print(f"Wrote {path.relative_to(root)}")
        return 0
    validate_compatibility_shims(root)
    unexpected = unexpected_top_level_modules(
        current_top_level_modules(root),
        load_root_baseline(root),
    )
    if unexpected:
        print("Unowned top-level product modules: " + ", ".join(unexpected))
        return 1
    graph = load_package_graph(root)
    missing_replacements = [
        f"{filename} -> {shim.replacement_import}"
        for filename, shim in ROOT_COMPATIBILITY_SHIMS.items()
        if shim.replacement_import not in graph.modules
    ]
    if missing_replacements:
        print("Compatibility shim replacements are missing:")
        for violation in missing_replacements:
            print(f"- {violation}")
        return 1
    shim_usage = analyze_compatibility_shim_usage(
        root,
        graph,
        ROOT_COMPATIBILITY_SHIMS,
    )
    unexpected_callers = unexpected_compatibility_callers(
        ROOT_COMPATIBILITY_SHIMS,
        shim_usage,
    )
    if unexpected_callers:
        print("Unexpected compatibility shim callers:")
        for violation in unexpected_callers:
            print(f"- {violation}")
        return 1
    violations = dependency_direction_violations(graph)
    if violations:
        print("Dependency direction violations:")
        for violation in violations:
            print(f"- {violation}")
        return 1
    cycles = import_cycles(graph.imports_by_module)
    new_cycles = new_import_cycles(cycles, load_cycle_baseline(root))
    if new_cycles:
        print("New import cycles:")
        for cycle in new_cycles:
            print("- " + " -> ".join(cycle))
        return 1
    expected_report = render_cycle_report(graph, load_cycle_baseline(root))
    report_path = root / CYCLE_REPORT_RELATIVE_PATH
    if not report_path.exists() or report_path.read_text(encoding="utf-8") != expected_report:
        print(f"Package cycle report is stale: {report_path.relative_to(root)}")
        return 1
    expected_shim_report = render_shim_retirement_report(
        ROOT_COMPATIBILITY_SHIMS,
        shim_usage,
    )
    shim_report_path = root / SHIM_RETIREMENT_RELATIVE_PATH
    if (
        not shim_report_path.exists()
        or shim_report_path.read_text(encoding="utf-8") != expected_shim_report
    ):
        print(f"Shim retirement report is stale: {shim_report_path.relative_to(root)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
