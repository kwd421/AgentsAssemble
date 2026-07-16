"""Generate the deterministic AgentsAssemble package ownership inventory."""
from __future__ import annotations

import argparse
import ast
import hashlib
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping


ROOT_ENTRYPOINTS = frozenset({"agentsassemble", "agentsassemble.cli", "agentsassemble.gui"})
EXISTING_PACKAGES = frozenset(
    {
        "adapters",
        "admission",
        "application",
        "bridges",
        "features",
        "identity",
        "legacy",
        "migrations",
        "persistence",
        "providers",
        "room",
        "web",
    }
)
PATH_OWNED_DOMAINS = frozenset(
    {
        "admission",
        "application",
        "features",
        "identity",
        "providers",
        "room",
        "web",
    }
)
FROZEN_POLICY_TERMS = (
    "attention",
    "autonomous",
    "speaker",
    "semantic_silence",
    "scheduled_wakeup",
)


@dataclass(frozen=True)
class ModuleSource:
    name: str
    path: Path
    relative_path: str
    source: str
    tree: ast.Module
    is_package: bool


@dataclass(frozen=True)
class ModuleInventory:
    name: str
    relative_path: str
    line_count: int
    domain: str
    classification: str
    imports: tuple[str, ...]
    reverse_import_count: int
    side_effects: tuple[str, ...]
    reference_evidence: str
    primary_tests: tuple[str, ...]
    proposed_package: str
    migration_status: str


@dataclass(frozen=True)
class PackageGraph:
    modules: Mapping[str, ModuleSource]
    imports_by_module: Mapping[str, tuple[str, ...]]
    domains: Mapping[str, str]
    classifications: Mapping[str, str]


def load_package_graph(repository_root: Path) -> PackageGraph:
    root = Path(repository_root).resolve()
    modules = _load_modules(root)
    known_modules = frozenset(modules)
    imports_by_module = {
        name: _internal_imports(module, known_modules)
        for name, module in modules.items()
    }
    classifications = {
        name: _classification(module)
        for name, module in modules.items()
    }
    domains = {
        name: _domain(module, classifications[name])
        for name, module in modules.items()
    }
    return PackageGraph(
        modules=modules,
        imports_by_module=imports_by_module,
        domains=domains,
        classifications=classifications,
    )


def build_package_map(repository_root: Path) -> str:
    root = Path(repository_root).resolve()
    graph = load_package_graph(root)
    modules = graph.modules
    imports_by_module = graph.imports_by_module
    known_modules = frozenset(modules)
    reverse_counts = Counter(
        imported
        for imports in imports_by_module.values()
        for imported in imports
    )
    test_imports, test_patches = _test_references(root, known_modules)
    inventories = []
    for name in sorted(modules):
        module = modules[name]
        classification = graph.classifications[name]
        domain = graph.domains[name]
        inventories.append(
            ModuleInventory(
                name=name,
                relative_path=module.relative_path,
                line_count=len(module.source.splitlines()),
                domain=domain,
                classification=classification,
                imports=imports_by_module[name],
                reverse_import_count=reverse_counts[name],
                side_effects=_import_time_effects(module.tree),
                reference_evidence=_reference_evidence(
                    test_imports.get(name, ()),
                    test_patches.get(name, ()),
                ),
                primary_tests=_primary_tests(
                    test_imports.get(name, ()),
                    test_patches.get(name, ()),
                ),
                proposed_package=_proposed_package(module, domain),
                migration_status=_migration_status(
                    module,
                    domain,
                    classification,
                ),
            )
        )
    return _render(inventories)


def write_package_map(repository_root: Path, output_path: Path) -> bool:
    rendered = build_package_map(repository_root)
    path = Path(output_path)
    previous = path.read_text(encoding="utf-8") if path.exists() else ""
    if previous == rendered:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(rendered, encoding="utf-8")
    return True


def _load_modules(root: Path) -> dict[str, ModuleSource]:
    package_root = root / "agentsassemble"
    result: dict[str, ModuleSource] = {}
    for path in sorted(package_root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        relative = path.relative_to(root)
        source = path.read_text(encoding="utf-8")
        name = _module_name(relative)
        result[name] = ModuleSource(
            name=name,
            path=path,
            relative_path=relative.as_posix(),
            source=source,
            tree=ast.parse(source, filename=relative.as_posix()),
            is_package=path.name == "__init__.py",
        )
    return result


def _module_name(relative_path: Path) -> str:
    parts = list(relative_path.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _internal_imports(
    module: ModuleSource,
    known_modules: frozenset[str],
) -> tuple[str, ...]:
    imported: set[str] = set()
    for node in ast.walk(module.tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                resolved = _known_module_prefix(alias.name, known_modules)
                if resolved:
                    imported.add(resolved)
        elif isinstance(node, ast.ImportFrom):
            base = _resolve_from_base(module, node)
            candidates = [base]
            candidates.extend(
                f"{base}.{alias.name}" if base else alias.name
                for alias in node.names
                if alias.name != "*"
            )
            for candidate in candidates:
                resolved = _known_module_prefix(candidate, known_modules)
                if resolved:
                    imported.add(resolved)
    imported.discard(module.name)
    return tuple(sorted(imported))


def _resolve_from_base(module: ModuleSource, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return str(node.module or "")
    package_parts = module.name.split(".") if module.is_package else module.name.split(".")[:-1]
    remove_count = node.level - 1
    if remove_count:
        package_parts = package_parts[:-remove_count]
    if node.module:
        package_parts.extend(node.module.split("."))
    return ".".join(package_parts)


def _known_module_prefix(candidate: str, known_modules: frozenset[str]) -> str:
    if not candidate.startswith("agentsassemble"):
        return ""
    parts = candidate.split(".")
    for size in range(len(parts), 0, -1):
        prefix = ".".join(parts[:size])
        if prefix in known_modules:
            return prefix
    return ""


def _test_references(
    root: Path,
    known_modules: frozenset[str],
) -> tuple[dict[str, tuple[str, ...]], dict[str, tuple[str, ...]]]:
    imports: dict[str, set[str]] = defaultdict(set)
    patches: dict[str, set[str]] = defaultdict(set)
    for path in _reference_source_paths(root):
        relative = path.relative_to(root).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
        except (OSError, SyntaxError, UnicodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                candidates = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                base = str(node.module or "")
                candidates = [base]
                candidates.extend(
                    f"{base}.{alias.name}" if base else alias.name
                    for alias in node.names
                    if alias.name != "*"
                )
            else:
                candidates = []
            for candidate in candidates:
                resolved = _known_module_prefix(candidate, known_modules)
                if resolved:
                    imports[resolved].add(relative)
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                resolved = _known_module_prefix(node.value, known_modules)
                if resolved and node.value.startswith(f"{resolved}."):
                    patches[resolved].add(relative)
    return (
        {name: tuple(sorted(paths)) for name, paths in imports.items()},
        {name: tuple(sorted(paths)) for name, paths in patches.items()},
    )


def _reference_source_paths(root: Path) -> Iterable[Path]:
    for directory in (root / "tests", root / "scripts"):
        if not directory.exists():
            continue
        for path in sorted(directory.rglob("*.py")):
            if "__pycache__" not in path.parts:
                yield path


def _classification(module: ModuleSource) -> str:
    relative_parts = Path(module.relative_path).parts
    stem = module.path.stem
    docstring = ast.get_docstring(module.tree) or ""
    if docstring.startswith("Compatibility export"):
        return "compatibility"
    if "compat" in stem or "compatibility" in stem:
        return "compatibility"
    if "legacy" in relative_parts[1:-1] or stem.startswith("legacy_"):
        return "legacy"
    if stem == "admission" or stem == "meeting" or stem.startswith("meeting_"):
        return "legacy"
    if stem.startswith("live_agent_"):
        return "legacy"
    if len(relative_parts) > 2 and relative_parts[1] == "features":
        return "optional"
    if any(term in stem for term in ("mafia", "friend", "side_chat", "social")):
        return "optional"
    return "current"


def _domain(module: ModuleSource, classification: str) -> str:
    stem = module.path.stem
    path_text = module.relative_path
    relative_parts = Path(module.relative_path).parts
    if (
        classification == "legacy"
        or "/legacy/" in path_text
        or stem.startswith("legacy_")
    ):
        return "legacy"
    if (
        "/persistence/" in path_text
        or "/migrations/" in path_text
        or stem.startswith(("postgres_", "sqlite_"))
    ):
        return "persistence"
    if len(relative_parts) > 2 and relative_parts[1] in PATH_OWNED_DOMAINS:
        return relative_parts[1]
    if any(term in stem for term in ("mafia", "friend", "side_chat", "social")):
        return "features"
    if any(
        term in stem
        for term in (
            "diagnostic",
            "health",
            "benchmark",
            "smoke",
            "cleanup_report",
            "release_",
        )
    ):
        return "diagnostics"
    if stem.startswith("identity") or "identity_" in stem or stem == "operator_pairing":
        return "identity"
    if any(
        term in stem
        for term in (
            "room_admission",
            "room_invite",
            "room_session",
            "public_invite",
            "invite_",
            "operator_pairing",
        )
    ):
        return "admission"
    if stem == "frontend_runtime":
        return "web"
    if (
        "/adapters/" in path_text
        or "/bridges/" in path_text
        or any(
            term in stem
            for term in (
                "provider",
                "runtime",
                "agent_bridge",
                "live_cli",
                "native_cli",
                "conpty",
                "codex_",
                "claude_",
                "grok_",
                "opencode_",
                "antigravity_",
            )
        )
    ):
        return "providers"
    if any(
        term in stem
        for term in (
            "gui_",
            "_http",
            "_router",
            "websocket",
            "ws_",
            "static_",
            "request_security",
            "http_response",
        )
    ):
        return "web"
    if stem.startswith("room_") or stem in {"room", "room_store", "room_repository"}:
        return "room"
    return "application"


def _proposed_package(module: ModuleSource, domain: str) -> str:
    if module.name in ROOT_ENTRYPOINTS:
        return "root entrypoint"
    if domain == "persistence":
        if (
            "/persistence/postgres/" in module.relative_path
            or module.path.stem.startswith("postgres_")
            or "/migrations/" in module.relative_path
        ):
            return "persistence/postgres/"
        return "persistence/local/"
    if domain == "features":
        relative_parts = Path(module.relative_path).parts
        if len(relative_parts) > 2 and relative_parts[1] == "features":
            if len(relative_parts) > 3:
                return f"features/{relative_parts[2]}/"
            return "features/"
        stem = module.path.stem
        feature = next(
            (name for name in ("mafia", "friends", "side_chat", "social") if name in stem),
            "optional",
        )
        return f"features/{feature}/"
    return f"{domain}/"


def _migration_status(
    module: ModuleSource,
    domain: str,
    classification: str,
) -> str:
    if module.name in ROOT_ENTRYPOINTS:
        return "retained-entrypoint"
    if classification == "compatibility":
        return "compatibility-shim"
    stem = module.path.stem
    if domain != "persistence" and any(term in stem for term in FROZEN_POLICY_TERMS):
        return "deferred-policy"
    parts = Path(module.relative_path).parts
    if len(parts) > 2 and parts[1] in EXISTING_PACKAGES:
        if parts[1] == domain:
            return "in-target-package"
        if parts[1] == "migrations":
            return "retained-migration"
        return "pending-consolidation"
    return "planned-move"


def _import_time_effects(tree: ast.Module) -> tuple[str, ...]:
    markers = []
    for node in tree.body:
        marker = ""
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            marker = f"call:{_call_name(node.value)}"
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            if isinstance(value, ast.Call):
                marker = f"call:{_call_name(value)}"
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            marker = "context-manager"
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.While, ast.Try)):
            marker = "control-flow"
        elif isinstance(node, ast.If) and not _is_main_or_type_checking_guard(node):
            marker = "conditional"
        if marker:
            markers.append(f"{marker}@{node.lineno}")
    unique = tuple(dict.fromkeys(markers))
    if len(unique) <= 5:
        return unique
    return (*unique[:5], f"+{len(unique) - 5}")


def _call_name(call: ast.Call) -> str:
    target = call.func
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return "<expression>"


def _is_main_or_type_checking_guard(node: ast.If) -> bool:
    if isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING":
        return True
    return (
        isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Name)
        and node.test.left.id == "__name__"
    )


def _reference_evidence(imports: tuple[str, ...], patches: tuple[str, ...]) -> str:
    evidence = []
    if imports:
        evidence.append(f"test-import:{len(imports)}")
    if patches:
        evidence.append(f"monkeypatch:{len(patches)}")
    return ", ".join(evidence) or "-"


def _primary_tests(imports: tuple[str, ...], patches: tuple[str, ...]) -> tuple[str, ...]:
    paths = sorted(set(imports) | set(patches))
    if len(paths) <= 3:
        return tuple(paths)
    return (*paths[:3], f"+{len(paths) - 3}")


def _render(inventories: list[ModuleInventory]) -> str:
    domains = Counter(item.domain for item in inventories)
    classifications = Counter(item.classification for item in inventories)
    top_level_count = sum(
        1
        for item in inventories
        if len(Path(item.relative_path).relative_to("agentsassemble").parts) == 1
    )
    fingerprint_material = "\n".join(
        f"{item.name}|{item.line_count}|{','.join(item.imports)}|{item.domain}|{item.classification}"
        for item in inventories
    )
    fingerprint = hashlib.sha256(fingerprint_material.encode("utf-8")).hexdigest()[:16]
    lines = [
        "# Package Map",
        "",
        "Status: generated architecture inventory",
        "",
        "Generator: `python3 scripts/generate_package_map.py`",
        "",
        f"Source fingerprint: `{fingerprint}`",
        "",
        "This file describes current evidence and proposed ownership. It does not by",
        "itself authorize a module move or a product behavior change.",
        "",
        "## Summary",
        "",
        f"- Python modules: {len(inventories)}",
        f"- Top-level package modules: {top_level_count}",
        "- Domains: " + _counter_summary(domains),
        "- Classifications: " + _counter_summary(classifications),
        "",
        "## Classification Rules",
        "",
        "- `current`: active product or infrastructure code.",
        "- `optional`: current feature code that is not required for the core room path.",
        "- `compatibility`: an explicit compatibility boundary or shim.",
        "- `legacy`: meeting/live-agent legacy behavior retained for compatibility.",
        "- `deferred-policy`: autonomous attention or speaker policy frozen by the active plan.",
        "- Import-time markers are review signals, not proof that an import is unsafe.",
        "- Test references include direct imports and string monkeypatch targets.",
        "",
        "## Modules",
        "",
        "| Module | Path | Lines | Domain | Class | Internal imports | Reverse | Import-time markers | Reference evidence | Primary tests | Proposed owner | Migration |",
        "| --- | --- | ---: | --- | --- | --- | ---: | --- | --- | --- | --- | --- |",
    ]
    for item in inventories:
        lines.append(
            "| "
            + " | ".join(
                (
                    _cell(f"`{item.name}`"),
                    _cell(f"`{item.relative_path}`"),
                    str(item.line_count),
                    item.domain,
                    item.classification,
                    _cell(_compact(item.imports, limit=8)),
                    str(item.reverse_import_count),
                    _cell(_compact(item.side_effects, limit=6)),
                    item.reference_evidence,
                    _cell(_compact(item.primary_tests, limit=4)),
                    _cell(f"`{item.proposed_package}`"),
                    item.migration_status,
                )
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


def _counter_summary(counter: Counter[str]) -> str:
    return ", ".join(f"{name}={counter[name]}" for name in sorted(counter))


def _compact(values: tuple[str, ...], *, limit: int) -> str:
    if not values:
        return "-"
    shown = values[:limit]
    rendered = ", ".join(f"`{value}`" for value in shown)
    if len(values) > limit:
        rendered += f", +{len(values) - limit}"
    return rendered


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--output", default="docs/product/PACKAGE_MAP.md")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    output = root / args.output
    rendered = build_package_map(root)
    if args.check:
        if not output.exists() or output.read_text(encoding="utf-8") != rendered:
            print(f"Package map is stale: {output.relative_to(root)}")
            return 1
        return 0
    changed = write_package_map(root, output)
    print(f"{'Updated' if changed else 'Unchanged'} {output.relative_to(root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
