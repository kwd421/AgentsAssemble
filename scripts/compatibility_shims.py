"""Load compatibility policy and measure the callers that block shim removal."""
from __future__ import annotations

import ast
import hashlib
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, TYPE_CHECKING

if TYPE_CHECKING:
    from scripts.generate_package_map import PackageGraph


COMPATIBILITY_SHIMS_RELATIVE_PATH = Path("docs/product/compatibility_shims.toml")
SHIM_RETIREMENT_RELATIVE_PATH = Path("docs/product/SHIM_RETIREMENT.md")


@dataclass(frozen=True)
class CompatibilityShim:
    replacement_import: str
    removal_gate: str
    allowed_callers: tuple[str, ...]
    introduced_in: str
    export_policy: str


@dataclass(frozen=True)
class CompatibilityShimUsage:
    production_imports: tuple[str, ...] = ()
    test_imports: tuple[str, ...] = ()
    tool_imports: tuple[str, ...] = ()
    monkeypatches: tuple[str, ...] = ()
    documentation: tuple[str, ...] = ()

    @property
    def code_callers(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                set(self.production_imports)
                | set(self.test_imports)
                | set(self.tool_imports)
                | set(self.monkeypatches)
            )
        )


def load_compatibility_shims(
    repository_root: Path,
) -> dict[str, CompatibilityShim]:
    path = Path(repository_root) / COMPATIBILITY_SHIMS_RELATIVE_PATH
    with path.open("rb") as source:
        document = tomllib.load(source)
    raw_entries = document.get("shim")
    if not isinstance(raw_entries, list):
        raise ValueError("Compatibility metadata must contain [[shim]] entries.")
    shims: dict[str, CompatibilityShim] = {}
    filenames: list[str] = []
    for raw in raw_entries:
        if not isinstance(raw, dict):
            raise ValueError("Each compatibility shim entry must be a table.")
        filename = _required_text(raw, "filename")
        if filename in shims:
            raise ValueError(f"Duplicate compatibility shim: {filename}")
        callers = raw.get("allowed_callers")
        if not isinstance(callers, list) or any(
            not isinstance(caller, str) or not caller.strip() for caller in callers
        ):
            raise ValueError(
                f"Compatibility shim {filename!r} needs allowed_callers strings."
            )
        clean_callers = tuple(sorted(set(callers)))
        if list(callers) != list(clean_callers):
            raise ValueError(
                f"Compatibility shim {filename!r} allowed_callers must be sorted and unique."
            )
        shims[filename] = CompatibilityShim(
            replacement_import=_required_text(raw, "replacement_import"),
            removal_gate=_required_text(raw, "removal_gate"),
            allowed_callers=clean_callers,
            introduced_in=_required_text(raw, "introduced_in"),
            export_policy=_required_text(raw, "export_policy"),
        )
        filenames.append(filename)
    if filenames != sorted(filenames):
        raise ValueError("Compatibility shim entries must be sorted by filename.")
    return shims


def analyze_compatibility_shim_usage(
    repository_root: Path,
    graph: "PackageGraph",
    shims: Mapping[str, CompatibilityShim],
) -> dict[str, CompatibilityShimUsage]:
    root = Path(repository_root).resolve()
    module_to_filename = {
        f"agentsassemble.{filename.removesuffix('.py')}": filename
        for filename in shims
    }
    production: dict[str, set[str]] = _empty_usage_sets(shims)
    for source_name, imported_names in graph.imports_by_module.items():
        source = graph.modules[source_name]
        for imported_name in imported_names:
            filename = module_to_filename.get(imported_name)
            if filename:
                production[filename].add(source.relative_path)

    test_imports: dict[str, set[str]] = _empty_usage_sets(shims)
    tool_imports: dict[str, set[str]] = _empty_usage_sets(shims)
    monkeypatches: dict[str, set[str]] = _empty_usage_sets(shims)
    for directory, destination in (
        (root / "tests", test_imports),
        (root / "scripts", tool_imports),
    ):
        for path in _python_sources(directory):
            relative = path.relative_to(root).as_posix()
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
            except (OSError, SyntaxError, UnicodeError):
                continue
            for candidate in _import_candidates(tree):
                filename = _matching_shim(candidate, module_to_filename)
                if filename:
                    destination[filename].add(relative)
            for candidate in _string_candidates(tree):
                filename = _matching_shim(candidate, module_to_filename)
                module_name = f"agentsassemble.{filename.removesuffix('.py')}"
                if filename and candidate.startswith(f"{module_name}."):
                    monkeypatches[filename].add(relative)

    documentation: dict[str, set[str]] = _empty_usage_sets(shims)
    ignored_reports = {
        (root / "docs/product/PACKAGE_MAP.md").resolve(),
        (root / SHIM_RETIREMENT_RELATIVE_PATH).resolve(),
    }
    for path in _documentation_sources(root / "docs"):
        if path.resolve() in ignored_reports:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        relative = path.relative_to(root).as_posix()
        for module_name, filename in module_to_filename.items():
            module_path = module_name.replace(".", "/") + ".py"
            if module_name in text or module_path in text:
                documentation[filename].add(relative)

    return {
        filename: CompatibilityShimUsage(
            production_imports=tuple(sorted(production[filename])),
            test_imports=tuple(sorted(test_imports[filename])),
            tool_imports=tuple(sorted(tool_imports[filename])),
            monkeypatches=tuple(sorted(monkeypatches[filename])),
            documentation=tuple(sorted(documentation[filename])),
        )
        for filename in sorted(shims)
    }


def unexpected_compatibility_callers(
    shims: Mapping[str, CompatibilityShim],
    usage: Mapping[str, CompatibilityShimUsage],
) -> tuple[str, ...]:
    violations = []
    for filename, shim in sorted(shims.items()):
        allowed = set(shim.allowed_callers)
        for caller in usage[filename].code_callers:
            if caller not in allowed:
                violations.append(f"{caller} uses {filename}")
    return tuple(violations)


def render_shim_retirement_report(
    shims: Mapping[str, CompatibilityShim],
    usage: Mapping[str, CompatibilityShimUsage],
) -> str:
    zero_callers = [
        filename for filename in sorted(shims) if not usage[filename].code_callers
    ]
    blocked = [
        filename for filename in sorted(shims) if usage[filename].code_callers
    ]
    unexpected = unexpected_compatibility_callers(shims, usage)
    fingerprint_source = "\n".join(
        f"{filename}|{shims[filename]}|{usage[filename]}"
        for filename in sorted(shims)
    )
    fingerprint = hashlib.sha256(fingerprint_source.encode("utf-8")).hexdigest()[:16]
    lines = [
        "# Compatibility Shim Retirement",
        "",
        "Status: generated architecture report",
        "",
        "Generator: `python3 scripts/check_package_architecture.py --write-shim-report`",
        "",
        f"Source fingerprint: `{fingerprint}`",
        "",
        f"- Tracked shims: {len(shims)}",
        f"- Zero code callers: {len(zero_callers)}",
        f"- Blocked by code callers: {len(blocked)}",
        f"- Unexpected callers: {len(unexpected)}",
        "",
        "Generated package-map and retirement-report references are excluded from",
        "documentation evidence. A zero-code-caller entry is a review candidate, not",
        "permission to delete it; its compatibility window and export policy still apply.",
        "",
        "## Unexpected Callers",
        "",
    ]
    lines.extend(f"- `{item}`" for item in unexpected)
    if not unexpected:
        lines.append("- None")
    lines.extend(("", "## Zero Code Callers", ""))
    if not zero_callers:
        lines.append("- None")
    for filename in zero_callers:
        shim = shims[filename]
        docs = _compact_paths(usage[filename].documentation)
        lines.append(
            f"- `{filename}` -> `{shim.replacement_import}`; docs: {docs}; "
            f"gate: {shim.removal_gate}"
        )
    lines.extend(("", "## Blocked", ""))
    if not blocked:
        lines.append("- None")
    for filename in blocked:
        shim_usage = usage[filename]
        lines.append(
            f"- `{filename}`; callers: {_compact_paths(shim_usage.code_callers)}; "
            f"docs: {_compact_paths(shim_usage.documentation)}"
        )
    return "\n".join(lines) + "\n"


def _required_text(raw: Mapping[str, object], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Compatibility shim entry needs {key}.")
    return value.strip()


def _empty_usage_sets(
    shims: Mapping[str, CompatibilityShim],
) -> dict[str, set[str]]:
    return {filename: set() for filename in shims}


def _python_sources(directory: Path) -> Iterable[Path]:
    if not directory.exists():
        return ()
    return (
        path
        for path in sorted(directory.rglob("*.py"))
        if "__pycache__" not in path.parts
    )


def _documentation_sources(directory: Path) -> Iterable[Path]:
    if not directory.exists():
        return ()
    return (
        path
        for path in sorted(directory.rglob("*"))
        if path.is_file() and path.suffix.lower() in {".md", ".txt"}
    )


def _import_candidates(tree: ast.Module) -> Iterable[str]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = str(node.module or "")
            if base:
                yield base
            for alias in node.names:
                if alias.name != "*":
                    yield f"{base}.{alias.name}" if base else alias.name


def _string_candidates(tree: ast.Module) -> Iterable[str]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value.startswith("agentsassemble."):
                yield node.value


def _matching_shim(
    candidate: str,
    module_to_filename: Mapping[str, str],
) -> str:
    for module_name in sorted(module_to_filename, key=len, reverse=True):
        if candidate == module_name or candidate.startswith(f"{module_name}."):
            return module_to_filename[module_name]
    return ""


def _compact_paths(paths: Iterable[str], *, limit: int = 4) -> str:
    values = tuple(paths)
    if not values:
        return "none"
    shown = ", ".join(f"`{path}`" for path in values[:limit])
    remaining = len(values) - limit
    return f"{shown}, +{remaining}" if remaining > 0 else shown
