"""Generate the interactive AgentsAssemble codebase map (HTML + JSON).

Scans the actual source tree so the map stays accurate:

- backend modules, docstrings, internal imports, domains, classifications,
  and migration status reuse the same graph that guards the architecture
  (`scripts/generate_package_map.py`);
- frontend files and their relative imports are scanned from `frontend/src`;
- the package dependency graph is laid out deterministically (SCC
  condensation + longest-path layering) and shipped pre-computed in the JSON.

Outputs:

- `docs/product/CODEBASE_MAP.json` - machine-readable map for agents;
- `docs/product/CODEBASE_MAP.html` - self-contained interactive view
  (no build step, no network assets; open directly or serve statically).

Regenerate: `python3 scripts/generate_codebase_map.py`
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

try:
    from scripts.generate_package_map import (
        PackageGraph,
        load_package_graph,
        _known_module_prefix,
        _migration_status,
        _proposed_package,
        _resolve_from_base,
    )
except ModuleNotFoundError as error:  # pragma: no cover - direct script run
    if error.name != "scripts":
        raise
    from generate_package_map import (
        PackageGraph,
        load_package_graph,
        _known_module_prefix,
        _migration_status,
        _proposed_package,
        _resolve_from_base,
    )


JSON_RELATIVE_PATH = Path("docs/product/CODEBASE_MAP.json")
HTML_RELATIVE_PATH = Path("docs/product/CODEBASE_MAP.html")

# Display metadata for top-level backend groups. Descriptions come from each
# package's own __init__.py docstring when present; these fallbacks only cover
# groups without one. Root flat modules are grouped by their ownership domain
# (the same domain PACKAGE_MAP.md reports), which is the migration target.
BACKEND_OWNED_ORDER = (
    "web",
    "application",
    "room",
    "admission",
    "identity",
    "providers",
    "diagnostics",
    "persistence",
    "features",
    "adapters",
    "bridges",
    "migrations",
    "legacy",
)
ROOT_GROUP_FALLBACK_DOC = (
    "Flat root modules owned by the {domain} domain that have not been "
    "physically moved into the target package yet."
)

FRONTEND_GROUP_DOCS = {
    "(src root)": "Frontend entry files (App, main, API client, styles) living directly under src/.",
    "app": "Application shell, routing, and global client state.",
    "api": "Room protocol client: HTTP calls, WebSocket wiring, DTO mapping.",
    "lib": "Shared utilities and presentation helpers.",
    "views": "Top-level room views rendered by the shell.",
    "components": "Reusable view components.",
}

REPO_AREAS = (
    ("agentsassemble", "Backend package (Python)", (".py",)),
    ("frontend/src", "React room client (TS/TSX)", (".ts", ".tsx", ".js", ".jsx", ".css")),
    ("tests", "Backend test suite", (".py",)),
    ("scripts", "Repo automation scripts", (".py",)),
    ("docs", "Product and architecture docs", (".md",)),
    ("configs", "Runtime configuration", (".json", ".toml", ".yaml", ".yml")),
    ("seeds", "Seed data", None),
    ("infra", "Deployment / infrastructure", None),
)

IMPORT_FROM_RE = re.compile(
    r"""(?:import|export)\s[^'"]*?from\s+['"]([^'"]+)['"]"""
    r"""|import\s*\(\s*['"]([^'"]+)['"]\s*\)"""
    r"""|import\s+['"]([^'"]+)['"]"""
)
SOURCE_SUFFIXES = (".ts", ".tsx", ".js", ".jsx", ".css")


# ---------------------------------------------------------------------------
# Backend data
# ---------------------------------------------------------------------------


def _docstring_summary(tree: ast.Module) -> str:
    doc = ast.get_docstring(tree) or ""
    return doc.strip().splitlines()[0].strip() if doc.strip() else ""


def collect_backend(root: Path) -> dict:
    graph = load_package_graph(root)
    modules = []
    reverse: dict[str, set[str]] = defaultdict(set)
    for name, imports in graph.imports_by_module.items():
        for imported in imports:
            reverse[imported].add(name)

    for name in sorted(graph.modules):
        module = graph.modules[name]
        domain = graph.domains[name]
        imports = sorted(graph.imports_by_module[name])
        modules.append(
            {
                "name": name,
                "path": module.relative_path,
                "lines": len(module.source.splitlines()),
                "doc": _docstring_summary(module.tree),
                "domain": domain,
                "cls": graph.classifications[name],
                "pkg": _backend_group(module.relative_path, domain),
                "proposed": _proposed_package(module, domain),
                "mig": _migration_status(
                    module, domain, graph.classifications[name]
                ),
                "imp": imports,
                "rev": sorted(reverse[name]),
                "classes": _class_defs(module, frozenset(graph.modules)),
            }
        )

    cycles = _import_cycles({m["name"]: tuple(m["imp"]) for m in modules})
    return {"modules": modules, "cycles": cycles, "graph": graph}


def _backend_group(relative_path: str, domain: str) -> str:
    """Top-level display group: owned package dir, or 'root:<domain>'."""
    parts = Path(relative_path).parts
    if len(parts) > 2:
        return parts[1]
    return f"root:{domain}"


def _class_defs(module, known_modules: frozenset[str]) -> list:
    """Top-level classes with base classes, best-effort resolved to modules.

    Returns [[class_name, [[base_name, defining_module_or_""], ...]], ...].
    Only direct internal imports are resolvable; external or indirect bases
    keep an empty module name.
    """
    symbol_module: dict[str, str] = {}
    for node in ast.walk(module.tree):
        if isinstance(node, ast.ImportFrom):
            base = _resolve_from_base(module, node)
            for alias in node.names:
                if alias.name == "*":
                    continue
                candidate = f"{base}.{alias.name}" if base else alias.name
                resolved = _known_module_prefix(
                    candidate, known_modules
                ) or _known_module_prefix(base, known_modules)
                if resolved:
                    symbol_module[alias.asname or alias.name] = resolved
        elif isinstance(node, ast.Import):
            for alias in node.names:
                resolved = _known_module_prefix(alias.name, known_modules)
                if resolved:
                    symbol_module[alias.asname or alias.name.split(".")[0]] = resolved
    local_classes = {
        node.name for node in module.tree.body if isinstance(node, ast.ClassDef)
    }
    classes = []
    for node in module.tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        bases = []
        for base in node.bases:
            base_name = _base_expr_name(base)
            if not base_name:
                continue
            root_name = base_name.split(".")[0]
            defining = (
                module.name
                if root_name in local_classes
                else symbol_module.get(root_name, "")
            )
            bases.append([base_name, defining])
        classes.append([node.name, bases])
    return classes


def _base_expr_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _base_expr_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    if isinstance(node, ast.Subscript):
        return _base_expr_name(node.value)
    if isinstance(node, ast.Call):
        return _base_expr_name(node.func)
    return ""


def _import_cycles(imports_by_module: dict[str, tuple[str, ...]]) -> list[list[str]]:
    """Iterative Tarjan SCC; returns components with >1 member or self-loops."""
    index_of: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    cycles: list[list[str]] = []
    counter = 0

    for start in sorted(imports_by_module):
        if start in index_of:
            continue
        work = [(start, iter(sorted(imports_by_module.get(start, ()))))]
        index_of[start] = lowlink[start] = counter
        counter += 1
        stack.append(start)
        on_stack.add(start)
        while work:
            node, it = work[-1]
            advanced = False
            for succ in it:
                if succ not in index_of:
                    index_of[succ] = lowlink[succ] = counter
                    counter += 1
                    stack.append(succ)
                    on_stack.add(succ)
                    work.append((succ, iter(sorted(imports_by_module.get(succ, ())))))
                    advanced = True
                    break
                if succ in on_stack:
                    lowlink[node] = min(lowlink[node], index_of[succ])
            if advanced:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                lowlink[parent] = min(lowlink[parent], lowlink[node])
            if lowlink[node] == index_of[node]:
                component = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                if len(component) > 1 or node in imports_by_module.get(node, ()):
                    cycles.append(sorted(component))
    return sorted(cycles, key=lambda c: (len(c), c))


# ---------------------------------------------------------------------------
# Frontend data
# ---------------------------------------------------------------------------


def collect_frontend(root: Path) -> dict:
    src = root / "frontend" / "src"
    files: dict[str, dict] = {}
    if not src.is_dir():
        return {"files": [], "present": False}
    for path in sorted(src.rglob("*")):
        if path.suffix not in SOURCE_SUFFIXES or not path.is_file():
            continue
        relative = path.relative_to(src).as_posix()
        text = path.read_text(encoding="utf-8", errors="replace")
        files[relative] = {
            "path": f"frontend/src/{relative}",
            "group": relative.split("/")[0] if "/" in relative else "(src root)",
            "lines": len(text.splitlines()),
            "doc": _frontend_doc(text),
            "text": text,
        }
    # Resolve relative imports now that all candidate files are known.
    known = set(files)
    for relative, info in files.items():
        info["deps"] = sorted(
            set(_frontend_imports(info.pop("text"), src / relative, src, known))
        )
        info["rev"] = []
    for relative, info in files.items():
        for dep in info["deps"]:
            files[dep]["rev"].append(relative)
    for info in files.values():
        info["rev"] = sorted(info["rev"])
    return {"files": [files[k] for k in sorted(files)], "present": True}


def _frontend_doc(text: str) -> str:
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("//"):
            return line.lstrip("/ ").strip()
        if line.startswith("/*"):
            return line.lstrip("/* ").strip().rstrip("*/").strip()
        if line and not line.startswith(("import ", "'use", '"use')):
            return ""
    return ""


def _frontend_imports(text: str, path: Path, src: Path, known: set[str]) -> list[str]:
    deps = []
    for match in IMPORT_FROM_RE.finditer(text):
        spec = next(g for g in match.groups() if g)
        if not spec.startswith("."):
            continue
        target = (path.parent / spec).resolve()
        try:
            base = target.relative_to(src.resolve()).as_posix()
        except ValueError:
            continue
        resolved = _resolve_frontend_file(base, known)
        if resolved:
            deps.append(resolved)
    return deps


def _resolve_frontend_file(base: str, known: set[str]) -> str:
    candidates = [base + suffix for suffix in SOURCE_SUFFIXES]
    candidates.extend(f"{base}/index{suffix}" for suffix in SOURCE_SUFFIXES)
    candidates.append(base)
    for candidate in candidates:
        if candidate in known:
            return candidate
    return ""


# ---------------------------------------------------------------------------
# Repo overview
# ---------------------------------------------------------------------------


def collect_repo_areas(root: Path) -> list[dict]:
    areas = []
    for name, note, suffixes in REPO_AREAS:
        directory = root / name
        if not directory.is_dir():
            continue
        files = lines = 0
        for path in directory.rglob("*"):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            if "node_modules" in path.parts or "dist" in path.parts:
                continue
            if suffixes and path.suffix not in suffixes:
                continue
            files += 1
            try:
                lines += len(path.read_text(encoding="utf-8", errors="replace").splitlines())
            except OSError:
                pass
        areas.append({"name": name, "note": note, "files": files, "lines": lines})
    return areas


def collect_test_references(root: Path, known_modules: frozenset[str]) -> set[str]:
    """Modules referenced from `tests/`.

    The package graph only scans `agentsassemble/`, so a module with no importer
    there can still be pinned by the test suite. Health signals that suggest
    deleting something must account for that or they are simply wrong.
    """
    referenced: set[str] = set()
    tests_root = root / "tests"
    if not tests_root.is_dir():
        return referenced

    def mark(dotted: str) -> None:
        parts = dotted.split(".")
        for size in range(len(parts), 0, -1):
            candidate = ".".join(parts[:size])
            if candidate in known_modules:
                referenced.add(candidate)

    for path in sorted(tests_root.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    mark(alias.name)
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                mark(node.module)
                for alias in node.names:
                    mark(f"{node.module}.{alias.name}")
            elif isinstance(node, ast.Attribute):
                # Reach dotted access such as agentsassemble.gui.serve_gui.
                pieces: list[str] = []
                cursor: ast.expr = node
                while isinstance(cursor, ast.Attribute):
                    pieces.append(cursor.attr)
                    cursor = cursor.value
                if isinstance(cursor, ast.Name):
                    pieces.append(cursor.id)
                    mark(".".join(reversed(pieces)))
    return referenced


# ---------------------------------------------------------------------------
# Package aggregation + graph layout
# ---------------------------------------------------------------------------


def aggregate_packages(backend: dict, frontend: dict, root: Path) -> tuple[list[dict], list[dict]]:
    modules = backend["modules"]
    packages: dict[str, dict] = {}

    def ensure(pid: str, kind: str) -> dict:
        if pid not in packages:
            packages[pid] = {
                "id": pid,
                "kind": kind,
                "doc": "",
                "files": 0,
                "lines": 0,
                "domains": Counter(),
                "classes": Counter(),
            }
        return packages[pid]

    for module in modules:
        pkg = ensure(module["pkg"], "backend")
        pkg["files"] += 1
        pkg["lines"] += module["lines"]
        pkg["domains"][module["domain"]] += 1
        pkg["classes"][module["cls"]] += 1

    package_docstrings = _package_docstrings(root)
    for pid, pkg in packages.items():
        if pid in package_docstrings:
            pkg["doc"] = package_docstrings[pid]
        elif pid.startswith("root:"):
            pkg["doc"] = ROOT_GROUP_FALLBACK_DOC.format(domain=pid.split(":", 1)[1])

    for file in frontend["files"]:
        pkg = ensure(f"fe:{file['group']}", "frontend")
        pkg["files"] += 1
        pkg["lines"] += file["lines"]
    for pid, pkg in packages.items():
        if pkg["kind"] == "frontend":
            group = pid.split(":", 1)[1]
            pkg["doc"] = FRONTEND_GROUP_DOCS.get(
                group, f"Frontend source files grouped under src/{group}/."
            )
        pkg["domains"] = dict(sorted(pkg["domains"].items()))
        pkg["classes"] = dict(sorted(pkg["classes"].items()))

    edges_counter: Counter = Counter()
    for module in modules:
        for imported in module["imp"]:
            target_pkg = _backend_group(
                backend["graph"].modules[imported].relative_path,
                backend["graph"].domains[imported],
            )
            if target_pkg != module["pkg"]:
                edges_counter[(module["pkg"], target_pkg)] += 1
    fe_group_of = {f["path"]: f"fe:{f['group']}" for f in frontend["files"]}
    for file in frontend["files"]:
        for dep in file["deps"]:
            target = fe_group_of.get(f"frontend/src/{dep}")
            source = fe_group_of[file["path"]]
            if target and target != source:
                edges_counter[(source, target)] += 1

    edges = [
        {"from": source, "to": target, "count": count}
        for (source, target), count in sorted(edges_counter.items())
    ]
    ordered = sorted(
        packages.values(),
        key=lambda p: (
            p["kind"] != "backend",
            BACKEND_OWNED_ORDER.index(p["id"])
            if p["id"] in BACKEND_OWNED_ORDER
            else (100 + (p["id"] != "root") ),
            p["id"],
        ),
    )
    return ordered, edges


def _package_docstrings(root: Path) -> dict[str, str]:
    docs: dict[str, str] = {}
    package_root = root / "agentsassemble"
    for path in sorted(package_root.glob("*/__init__.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        doc = _docstring_summary(tree)
        if doc:
            docs[path.parent.name] = doc
    return docs


# ---------------------------------------------------------------------------
# Layered graph layout (Sugiyama pipeline)
# ---------------------------------------------------------------------------
#
# Stages: SCC condensation -> Coffman-Graham width-bounded layering ->
# dummy-node insertion -> median/transpose crossing minimization ->
# priority-placement x-assignment -> port-ordered edge routing.
# Fully deterministic and precomputed so the HTML only renders points.
# Shared by the package dependency graph and the UML class hierarchy graph.

PKG_NODE_W, PKG_NODE_H = 148, 52
CLASS_NODE_W, CLASS_NODE_H = 210, 46
LAYER_GAP_Y = 86
SEP_X = 24
GUTTER_X = 90
MAX_LAYER_NODES = 8


def _median_float(values: list[float]) -> float:
    ranked = sorted(values)
    middle = len(ranked) // 2
    if len(ranked) % 2:
        return float(ranked[middle])
    return (ranked[middle - 1] + ranked[middle]) / 2.0


COMPONENT_GAP_X = 72
COMPONENT_GAP_Y = 64


def sugiyama_layout(
    node_ids: list[str],
    edges: list[tuple[str, str]],
    node_w: int,
    node_h: int,
    max_layer_nodes: int = MAX_LAYER_NODES,
    gutter_x: int = GUTTER_X,
    shelf_width: float = 3200.0,
) -> dict:
    """Lay out a small DAG. Returns per-node boxes, routed edges, layer meta.

    Disconnected components are laid out independently and shelf-packed
    (rows bounded by `shelf_width`); multi-node SCCs collapse to one layout
    box and fan out in a row.
    """
    # Connected components (undirected): pack them next to each other.
    adjacent: dict[str, set[str]] = {i: set() for i in node_ids}
    for source, target in edges:
        if source in adjacent and target in adjacent:
            adjacent[source].add(target)
            adjacent[target].add(source)
    unseen = set(node_ids)
    components: list[list[str]] = []
    while unseen:
        start = min(unseen)
        stack = [start]
        unseen.discard(start)
        members = []
        while stack:
            node = stack.pop()
            members.append(node)
            for other in adjacent[node]:
                if other in unseen:
                    unseen.discard(other)
                    stack.append(other)
        components.append(sorted(members))
    components.sort(key=lambda members: (-len(members), members))
    if len(components) > 1:
        merged = {
            "node_boxes": {},
            "routes": {},
            "layers": {},
            "clusters": [],
            "comp_of": {},
            "width": 0.0,
            "height": 0.0,
        }
        cursor_x = float(gutter_x)
        cursor_y = 0.0
        shelf_h = 0.0
        for members in components:
            member_set = set(members)
            sub_edges = [e for e in edges if e[0] in member_set and e[1] in member_set]
            # Sub-layouts are normalised to a zero gutter; this level owns the
            # gutter so every shelf row starts at the same left margin.
            sub = sugiyama_layout(
                members,
                sub_edges,
                node_w,
                node_h,
                max_layer_nodes,
                gutter_x=0,
                shelf_width=shelf_width,
            )
            if cursor_x > gutter_x and cursor_x + sub["width"] > shelf_width:
                cursor_x = float(gutter_x)
                cursor_y += shelf_h + COMPONENT_GAP_Y
                shelf_h = 0.0
            for node_id, box in sub["node_boxes"].items():
                merged["node_boxes"][node_id] = {
                    **box,
                    "x": box["x"] + cursor_x,
                    "y": box["y"] + cursor_y,
                    "cx": box["cx"] + cursor_x,
                    "cy": box["cy"] + cursor_y,
                }
            for edge, points in sub["routes"].items():
                merged["routes"][edge] = [
                    [x + cursor_x, y + cursor_y] for x, y in points
                ]
            for cluster in sub["clusters"]:
                merged["clusters"].append(
                    {
                        **cluster,
                        "x": cluster["x"] + cursor_x,
                        "y": cluster["y"] + cursor_y,
                        "label_y": cluster["label_y"] + cursor_y,
                    }
                )
            for meta in sub["layers"]:
                key = round(meta["y"] + cursor_y)
                entry = merged["layers"].setdefault(
                    key,
                    {"index": len(merged["layers"]), "y": meta["y"] + cursor_y, "members": []},
                )
                entry["members"].extend(meta["members"])
            merged["comp_of"].update(sub["comp_of"])
            cursor_x += sub["width"] + COMPONENT_GAP_X
            shelf_h = max(shelf_h, sub["height"])
            merged["width"] = max(
                merged["width"], cursor_x - COMPONENT_GAP_X + gutter_x
            )
            merged["height"] = cursor_y + shelf_h
        merged["layers"] = [merged["layers"][k] for k in sorted(merged["layers"])]
        return merged

    # --- 1. Condense SCCs (layering requires a DAG) -------------------------
    outgoing: dict[str, set[str]] = {i: set() for i in node_ids}
    for source, target in edges:
        if source in outgoing and target in outgoing:
            outgoing[source].add(target)
    scc = _import_cycles({i: tuple(sorted(outgoing[i])) for i in node_ids})
    comp_of: dict[str, int] = {}
    for index, component in enumerate(scc):
        for member in component:
            comp_of[member] = index
    for node_id in node_ids:
        comp_of.setdefault(node_id, -1 - node_ids.index(node_id))
    comps = sorted(set(comp_of.values()))
    comp_out: dict[int, set[int]] = {c: set() for c in comps}
    for source, target in edges:
        cs, ct = comp_of[source], comp_of[target]
        if cs != ct:
            comp_out[cs].add(ct)

    # A mutually-dependent group (SCC) is drawn as one block of member boxes.
    # Laying those members out in a single row makes the whole drawing as wide
    # as the largest cycle (13 packages here), so they wrap into a near-square
    # grid instead. Layering/separation below sizes each condensed node by the
    # block it will actually occupy.
    comp_members: dict[int, list[str]] = defaultdict(list)
    for node_id in node_ids:
        comp_members[comp_of[node_id]].append(node_id)
    for members in comp_members.values():
        members.sort()
    column_pitch = node_w + 20
    row_pitch = node_h + 18
    # A cycle group is drawn inside a labelled container, so it reserves a top
    # strip for that label plus a thin margin the members must not sit in.
    CLUSTER_PAD_TOP = 24
    CLUSTER_PAD_SIDE = 12
    CLUSTER_PAD_BOTTOM = 12
    comp_grid: dict[int, dict[str, float]] = {}
    for comp, members in comp_members.items():
        count = len(members)
        cols = min(count, max(1, math.ceil(math.sqrt(count * 1.6))))
        rows = math.ceil(count / cols)
        grid_w = cols * column_pitch - (column_pitch - node_w)
        grid_h = rows * row_pitch - (row_pitch - node_h)
        clustered = count > 1
        comp_grid[comp] = {
            "cols": cols,
            "rows": rows,
            "grid_w": grid_w,
            "pad_top": CLUSTER_PAD_TOP if clustered else 0.0,
            "w": grid_w + (2 * CLUSTER_PAD_SIDE if clustered else 0.0),
            "h": grid_h
            + (CLUSTER_PAD_TOP + CLUSTER_PAD_BOTTOM if clustered else 0.0),
        }

    # --- 2. Coffman-Graham layering (width-bounded) --------------------------
    # Label: sinks first; ready = all successors labeled. Choose the ready
    # vertex with the lexicographically smallest descending successor-label
    # tuple (ties: smaller component id) for determinism.
    label: dict[int, int] = {}
    next_label = 1
    while len(label) < len(comps):
        ready = [c for c in comps if c not in label and comp_out[c] <= set(label)]
        chosen = min(
            ready,
            key=lambda c: (
                tuple(sorted((-label[s] for s in comp_out[c]))),
                c,
            ),
        )
        label[chosen] = next_label
        next_label += 1
    raw_layer: dict[int, int] = {}
    layer_fill: defaultdict[int, int] = defaultdict(int)
    for comp in sorted(comps, key=lambda c: label[c]):
        base = 0 if not comp_out[comp] else max(raw_layer[s] for s in comp_out[comp]) + 1
        while layer_fill[base] >= max_layer_nodes:
            base += 1
        raw_layer[comp] = base
        layer_fill[base] += 1
    # Flip: importers on top, dependencies below.
    max_raw = max(raw_layer.values(), default=0)
    comp_layer = {c: max_raw - raw for c, raw in raw_layer.items()}
    layer_count = max_raw + 1

    # --- 3. Dummy nodes on edges spanning more than one layer ----------------
    chain_edges: list[tuple[object, object]] = []  # comps and dummy ids
    edge_chains: dict[tuple[int, int], list[tuple[object, object]]] = {}
    dummy_layer: dict[str, int] = {}
    dummy_seq = 0
    for comp in comps:
        for target in sorted(comp_out[comp]):
            span = comp_layer[target] - comp_layer[comp]
            chain: list[tuple[object, object]] = []
            prev: object = comp
            for layer_no in range(comp_layer[comp] + 1, comp_layer[target]):
                dummy = f"dummy:{dummy_seq}"
                dummy_seq += 1
                dummy_layer[dummy] = layer_no
                chain.append((prev, dummy))
                prev = dummy
            chain.append((prev, target))
            chain_edges.extend(chain)
            edge_chains[(comp, target)] = chain

    all_nodes: list[object] = [*comps, *dummy_layer]
    node_layer: dict[object, int] = {**comp_layer, **dummy_layer}
    is_dummy = {n: isinstance(n, str) for n in all_nodes}
    order: dict[int, list[object]] = defaultdict(list)
    for node in all_nodes:
        order[node_layer[node]].append(node)
    for members in order.values():
        members.sort(key=str)

    up_nb: dict[object, set[object]] = {n: set() for n in all_nodes}
    down_nb: dict[object, set[object]] = {n: set() for n in all_nodes}
    for source, target in chain_edges:
        down_nb[source].add(target)
        up_nb[target].add(source)

    def positions() -> dict[object, int]:
        return {n: i for members in order.values() for i, n in enumerate(members)}

    def pair_crossings(upper: int) -> int:
        """Crossings between layers `upper` and `upper + 1` for current order."""
        pos = positions()
        ends = [
            (pos[s], pos[t])
            for s, t in chain_edges
            if node_layer[s] == upper and node_layer[t] == upper + 1
        ]
        total = 0
        for i in range(len(ends)):
            for j in range(i + 1, len(ends)):
                if (ends[i][0] - ends[j][0]) * (ends[i][1] - ends[j][1]) < 0:
                    total += 1
        return total

    def total_crossings() -> int:
        return sum(pair_crossings(upper) for upper in range(layer_count - 1))

    # --- 4. Crossing minimization: median sweeps + adjacent transpose --------
    def median(values: list[int]) -> float:
        if not values:
            return -1.0
        values = sorted(values)
        middle = len(values) // 2
        if len(values) % 2:
            return float(values[middle])
        return (values[middle - 1] + values[middle]) / 2.0

    for _sweep in range(8):
        for direction in ("down", "up"):
            layer_range = (
                range(layer_count)
                if direction == "down"
                else range(layer_count - 1, -1, -1)
            )
            pos = positions()
            for layer_no in layer_range:
                neighbors = up_nb if direction == "down" else down_nb
                key_of = {
                    n: median([pos[u] for u in neighbors[n]])
                    for n in order[layer_no]
                }
                order[layer_no].sort(
                    key=lambda n: (key_of[n] < 0, key_of[n], str(n))
                )
            # Adjacent transpose: keep a swap only if it reduces crossings.
            for layer_no in layer_range:
                improved = True
                while improved:
                    improved = False
                    members = order[layer_no]
                    for i in range(len(members) - 1):
                        before = total_crossings()
                        members[i], members[i + 1] = members[i + 1], members[i]
                        if total_crossings() >= before:
                            members[i], members[i + 1] = members[i + 1], members[i]
                        else:
                            improved = True

    # --- 5. X-coordinate assignment: priority placement ----------------------
    # Brandes-Koepf block compaction was tried here first; at this size its
    # balance step produced overlaps between same-layer nodes, so we use the
    # simpler documented alternative: sweep down/up placing each node at the
    # median of its already-placed neighbours, clamped to a minimum gap after
    # its left sibling. Separation is guaranteed by construction; the median
    # reference keeps edges straight; dummy chains stay vertical because a
    # dummy has exactly one neighbour on each side.
    def node_width(node: object) -> float:
        return 8.0 if is_dummy[node] else comp_grid[node]["w"]

    center_x: dict[object, float] = {}
    for sweep in ("down", "down", "up", "down"):
        layer_range = (
            range(layer_count) if sweep == "down" else range(layer_count - 1, -1, -1)
        )
        for layer_no in layer_range:
            members = order[layer_no]
            neighbors = up_nb if sweep == "down" else down_nb
            for index, node in enumerate(members):
                refs = [center_x[u] for u in neighbors[node] if u in center_x]
                desired = _median_float(refs) if refs else None
                if index == 0:
                    center_x[node] = desired if desired is not None else 0.0
                    continue
                prev = members[index - 1]
                min_center = (
                    center_x[prev] + (node_width(prev) + node_width(node)) / 2.0 + SEP_X
                )
                center_x[node] = (
                    max(desired, min_center) if desired is not None else min_center
                )
    # Refinement: re-attach right-drifted nodes to their neighbour median,
    # clamped on both sides so separation is preserved (in-place sweeps).
    for sweep in ("down", "up"):
        layer_range = (
            range(layer_count) if sweep == "down" else range(layer_count - 1, -1, -1)
        )
        for layer_no in layer_range:
            members = order[layer_no]
            neighbors = up_nb if sweep == "down" else down_nb
            for index, node in enumerate(members):
                refs = [center_x[u] for u in neighbors[node] if u in center_x]
                if not refs:
                    continue
                desired = _median_float(refs)
                low = (
                    center_x[members[index - 1]]
                    + (node_width(members[index - 1]) + node_width(node)) / 2.0
                    + SEP_X
                    if index > 0
                    else float("-inf")
                )
                high = (
                    center_x[members[index + 1]]
                    - (node_width(members[index + 1]) + node_width(node)) / 2.0
                    - SEP_X
                    if index < len(members) - 1
                    else float("inf")
                )
                center_x[node] = min(max(desired, low), high)
    # Normalise with a single uniform shift so relative positions survive:
    # a per-node shift would move dummies (8px wide) and real nodes (node_w)
    # by different amounts and bend every dummy chain that should stay vertical.
    # The final gutter is applied once at the end, after SCC members fan out.
    leftmost = min(
        (center_x[n] - node_width(n) / 2.0 for n in all_nodes), default=0.0
    )
    for node in all_nodes:
        center_x[node] -= leftmost

    # --- 6. Final coordinates, ports, and routed edges -----------------------
    # Layer bands are as tall as the tallest SCC block they hold, so a wrapped
    # cycle grid never overlaps the layer beneath it.
    layer_height = {
        layer_no: max(
            (
                comp_grid[c]["h"]
                for c in order[layer_no]
                if not is_dummy[c]
            ),
            default=float(node_h),
        )
        for layer_no in range(layer_count)
    }
    layer_y: dict[int, float] = {}
    cursor = 0.0
    for layer_no in range(layer_count):
        layer_y[layer_no] = cursor
        cursor += layer_height[layer_no] + (LAYER_GAP_Y - node_h)

    def node_box(node: object, is_real: bool) -> dict:
        # Real nodes are sized as the block they occupy (a single package is a
        # 1x1 block) so ports and routes meet the block outline, not its centre.
        w = comp_grid[node]["w"] if is_real else 0
        h = comp_grid[node]["h"] if is_real else 0
        return {
            "x": center_x[node] - w / 2,
            "y": layer_y[node_layer[node]],
            "cx": center_x[node],
            "cy": layer_y[node_layer[node]] + h / 2,
            "w": w,
            "h": h,
            "layer": node_layer[node],
        }

    boxes = {comp: node_box(comp, True) for comp in comps}
    dummy_boxes = {d: node_box(d, False) for d in dummy_layer}

    out_edges: dict[object, list[tuple[object, object]]] = defaultdict(list)
    in_edges: dict[object, list[tuple[object, object]]] = defaultdict(list)
    centers = {**{c: center_x[c] for c in comps}, **{d: center_x[d] for d in dummy_layer}}
    for edge in chain_edges:
        out_edges[edge[0]].append(edge)
        in_edges[edge[1]].append(edge)

    def port_x(node: object, edge: tuple[object, object], outgoing_edge: bool) -> float:
        siblings = out_edges[node] if outgoing_edge else in_edges[node]
        siblings = sorted(
            siblings, key=lambda e: centers[e[1] if outgoing_edge else e[0]]
        )
        index = siblings.index(edge)
        count = len(siblings)
        if count <= 1 or is_dummy[node]:
            return centers[node]
        width = (comp_grid[node]["w"] if not is_dummy[node] else 0) - 20
        width = max(width, count * 14)
        step = min(18.0, width / (count - 1))
        return centers[node] + (index - (count - 1) / 2.0) * step

    routes: dict[tuple[object, object], list[list[float]]] = {}
    for edge in chain_edges:
        source, target = edge
        src_box = boxes.get(source) or dummy_boxes[source]
        tgt_box = boxes.get(target) or dummy_boxes[target]
        routes[edge] = [
            [port_x(source, edge, True), src_box["y"] + src_box["h"]],
            [port_x(target, edge, False), tgt_box["y"]],
        ]

    # Reassemble original edges through their dummy chains.
    chained: dict[tuple[int, int], list[list[float]]] = {}
    for (comp, target), chain in edge_chains.items():
        points: list[list[float]] = []
        for index, segment in enumerate(chain):
            segment_points = routes[segment]
            points.extend(segment_points if index == 0 else segment_points[1:])
        chained[(comp, target)] = points

    # Per-node boxes: SCC members tile the block grid reserved for their comp.
    node_boxes: dict[str, dict] = {}
    for comp, members in comp_members.items():
        box = boxes[comp]
        grid = comp_grid[comp]
        cols = int(grid["cols"])
        for index, node_id in enumerate(members):
            row, column = divmod(index, cols)
            # Centre the last (possibly short) row inside the block.
            in_row = min(cols, len(members) - row * cols)
            row_width = in_row * column_pitch - (column_pitch - node_w)
            left = box["x"] + (box["w"] - row_width) / 2.0 + column * column_pitch
            top = box["y"] + grid["pad_top"] + row * row_pitch
            node_boxes[node_id] = {
                "x": left,
                "y": top,
                "cx": left + node_w / 2.0,
                "cy": top + node_h / 2.0,
                "w": node_w,
                "h": node_h,
                "layer": box["layer"],
            }

    # Routes per original edge, through dummy chains. Edges inside one cycle
    # group run straight between box borders: they stay legible when revealed,
    # and the renderer keeps them hidden by default (see `clusters`).
    def border_point(box: dict, toward_x: float, toward_y: float) -> list[float]:
        dx = toward_x - box["cx"]
        dy = toward_y - box["cy"]
        if not dx and not dy:
            return [box["cx"], box["cy"]]
        scales = []
        if dx:
            scales.append((box["w"] / 2.0) / abs(dx))
        if dy:
            scales.append((box["h"] / 2.0) / abs(dy))
        scale = min(scales)
        return [box["cx"] + dx * scale, box["cy"] + dy * scale]

    edge_routes: dict[tuple[str, str], list[list[float]]] = {}
    for source, target in edges:
        comp_source, comp_target = comp_of[source], comp_of[target]
        if comp_source != comp_target:
            edge_routes[(source, target)] = chained[(comp_source, comp_target)]
            continue
        src, tgt = node_boxes[source], node_boxes[target]
        edge_routes[(source, target)] = [
            border_point(src, tgt["cx"], tgt["cy"]),
            border_point(tgt, src["cx"], src["cy"]),
        ]

    # Cycle groups: mutually dependent packages drawn as one labelled block.
    internal_edges: Counter[int] = Counter()
    for source, target in edges:
        if comp_of[source] == comp_of[target]:
            internal_edges[comp_of[source]] += 1
    # The container is the comp box itself: members were inset by pad_top, so the
    # label sits in that strip instead of colliding with incoming edges above.
    clusters = [
        {
            "members": comp_members[comp],
            "internal_edges": internal_edges[comp],
            "x": boxes[comp]["x"],
            "y": boxes[comp]["y"],
            "w": boxes[comp]["w"],
            "h": boxes[comp]["h"],
            "label_y": boxes[comp]["y"] + 16,
        }
        for comp in comps
        if len(comp_members[comp]) > 1
    ]

    layers_meta = []
    for layer_no in range(layer_count):
        real = {c for c in order[layer_no] if not is_dummy[c]}
        members = sorted(nid for nid in node_ids if comp_of[nid] in real)
        layers_meta.append(
            {"index": layer_no, "y": layer_y[layer_no], "members": members}
        )

    # Fanning SCC members out sideways can push geometry left of the gutter (and
    # routes can bow past the rightmost box), so measure the assembled drawing
    # and shift it into place once. Width/height then describe what is actually
    # drawn instead of an estimate the renderer has to trust.
    xs = [b["x"] for b in node_boxes.values()]
    xs += [b["x"] + b["w"] for b in node_boxes.values()]
    xs += [c["x"] for c in clusters] + [c["x"] + c["w"] for c in clusters]
    ys = [b["y"] for b in node_boxes.values()]
    ys += [b["y"] + b["h"] for b in node_boxes.values()]
    ys += [c["y"] for c in clusters] + [c["y"] + c["h"] for c in clusters]
    for points in edge_routes.values():
        xs += [x for x, _y in points]
        ys += [y for _x, y in points]
    shift_x = gutter_x - min(xs, default=0.0)
    shift_y = -min(ys, default=0.0)
    if shift_x or shift_y:
        for box in node_boxes.values():
            box["x"] += shift_x
            box["y"] += shift_y
            box["cx"] += shift_x
            box["cy"] += shift_y
        for cluster in clusters:
            cluster["x"] += shift_x
            cluster["y"] += shift_y
            cluster["label_y"] += shift_y
        for points in edge_routes.values():
            for point in points:
                point[0] += shift_x
                point[1] += shift_y
        for meta in layers_meta:
            meta["y"] += shift_y
    return {
        "node_boxes": node_boxes,
        "routes": edge_routes,
        "layers": layers_meta,
        "clusters": clusters,
        "width": max(xs, default=0.0) + shift_x + gutter_x,
        "height": max(ys, default=0.0) + shift_y + 20,
        "comp_of": comp_of,
    }


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def build_class_graph(modules: list[dict]) -> dict:
    """UML generalization graph over internally resolvable base classes."""
    class_ids: dict[tuple[str, str], str] = {}
    meta: dict[str, dict] = {}
    for module in modules:
        for class_name, _bases in module["classes"]:
            cid = f"{module['name']}::{class_name}"
            class_ids[(module["name"], class_name)] = cid
            meta[cid] = {
                "name": class_name,
                "module": module["name"],
                "cls": module["cls"],
            }
    edges: set[tuple[str, str]] = set()
    used: set[str] = set()
    for module in modules:
        for class_name, bases in module["classes"]:
            child = class_ids[(module["name"], class_name)]
            for base, defining in bases:
                if not defining:
                    continue
                base_id = class_ids.get((defining, base.split(".")[-1]))
                if base_id and base_id != child:
                    # base -> child for layout (bases drawn above, UML style)
                    edges.add((base_id, child))
                    used.update((base_id, child))
    node_ids = sorted(used)
    edge_list = sorted(edges)
    layout = sugiyama_layout(
        node_ids, edge_list, CLASS_NODE_W, CLASS_NODE_H,
        gutter_x=16, shelf_width=1500.0,
    )
    boxes = layout["node_boxes"]
    nodes = [
        {
            "id": node_id,
            **meta[node_id],
            **{k: boxes[node_id][k] for k in ("x", "y", "w", "h", "layer")},
        }
        for node_id in node_ids
    ]
    routed = [
        {
            "from": base,
            "to": child,
            "points": layout["routes"][(base, child)],
        }
        for base, child in edge_list
    ]
    return {
        "nodes": nodes,
        "edges": routed,
        "clusters": layout["clusters"],
        "width": layout["width"],
        "height": layout["height"],
    }


def build_health(
    modules: list[dict],
    packages: list[dict],
    clusters: list[dict],
    test_refs: set[str],
) -> dict:
    """Findings a reader should act on, derived from the same scan as the map.

    Everything here is a measured fact plus the scope it was measured in; the
    view states that scope so nobody reads "no importer" as "safe to delete"
    without checking the callers this repository does not scan.
    """
    by_name = {m["name"]: m for m in modules}
    retiring = {"legacy", "compatibility"}

    # Current code reaching into code that is meant to go away. These imports
    # are what keeps the legacy tree alive.
    leaning: list[dict] = []
    pulled: defaultdict[str, list[str]] = defaultdict(list)
    for module in modules:
        if module["cls"] != "current":
            continue
        stale = [name for name in module["imp"] if by_name[name]["cls"] in retiring]
        for name in stale:
            pulled[name].append(module["name"])
        if stale:
            leaning.append(
                {
                    "module": module["name"],
                    "lines": module["lines"],
                    "count": len(stale),
                    "targets": sorted(stale)[:12],
                }
            )
    leaning.sort(key=lambda item: (-item["count"], item["module"]))

    # Retiring modules with the most current importers: unblocking these frees
    # the most callers per migration.
    leverage = sorted(
        (
            {
                "module": name,
                "cls": by_name[name]["cls"],
                "lines": by_name[name]["lines"],
                "count": len(importers),
                "importers": sorted(importers)[:12],
            }
            for name, importers in pulled.items()
        ),
        key=lambda item: (-item["count"], item["module"]),
    )

    # Compatibility shims nothing imports. Split by whether the test suite still
    # names them, because only the second group is unreferenced everywhere the
    # generator can see.
    shims = [m for m in modules if m["cls"] == "compatibility" and not m["rev"]]
    unreferenced = sorted(
        (
            {"module": m["name"], "lines": m["lines"]}
            for m in shims
            if m["name"] not in test_refs
        ),
        key=lambda item: (-item["lines"], item["module"]),
    )
    test_only = sorted(
        (
            {"module": m["name"], "lines": m["lines"]}
            for m in shims
            if m["name"] in test_refs
        ),
        key=lambda item: (-item["lines"], item["module"]),
    )

    package_lines = {p["id"]: p["lines"] for p in packages}
    cycles = sorted(
        (
            {
                "members": cluster["members"],
                "internal_edges": cluster["internal_edges"],
                "lines": sum(package_lines.get(m, 0) for m in cluster["members"]),
                "heaviest": max(
                    cluster["members"], key=lambda m: package_lines.get(m, 0)
                ),
            }
            for cluster in clusters
        ),
        key=lambda item: -item["lines"],
    )

    hotspots = sorted(
        (
            {
                "module": m["name"],
                "lines": m["lines"],
                "cls": m["cls"],
                "imported_by": len(m["rev"]),
            }
            for m in modules
        ),
        key=lambda item: -item["lines"],
    )[:12]

    return {
        "scope": (
            "Imports are counted inside agentsassemble/ only; test references "
            "are counted separately from tests/. Callers outside this repository "
            "are not visible to the generator."
        ),
        "totals": {
            "retiring_lines": sum(
                m["lines"] for m in modules if m["cls"] in retiring
            ),
            "current_to_retiring_imports": sum(item["count"] for item in leaning),
            "unreferenced_shim_lines": sum(item["lines"] for item in unreferenced),
            "test_only_shims": len(test_only),
        },
        "cycles": cycles,
        "leaning_on_retiring": leaning[:12],
        "highest_leverage_migrations": leverage[:12],
        "unreferenced_shims": unreferenced[:20],
        "unreferenced_shim_count": len(unreferenced),
        "hotspots": hotspots,
    }


def build_map(root: Path) -> dict:
    backend = collect_backend(root)
    frontend = collect_frontend(root)
    packages, package_edges = aggregate_packages(backend, frontend, root)
    package_ids = [p["id"] for p in packages]
    layout = sugiyama_layout(
        package_ids,
        [(e["from"], e["to"]) for e in package_edges],
        PKG_NODE_W,
        PKG_NODE_H,
        # Keep the drawing close to screen shape: disconnected components wrap
        # onto a new shelf instead of extending one very wide row.
        shelf_width=1600.0,
    )
    for package in packages:
        package.update(layout["node_boxes"][package["id"]])
    cycle_group_of = {
        member: index
        for index, cluster in enumerate(layout["clusters"])
        for member in cluster["members"]
    }
    for edge in package_edges:
        edge["points"] = layout["routes"][(edge["from"], edge["to"])]
        # Same cycle group on both ends: an import inside a mutual-dependency
        # block, hidden by default so the cross-layer structure stays readable.
        edge["intra_cycle"] = (
            edge["from"] in cycle_group_of
            and cycle_group_of[edge["from"]] == cycle_group_of.get(edge["to"])
        )
    primary_domain = {
        p["id"]: (
            max(p["domains"], key=p["domains"].get)
            if p["domains"]
            else ("frontend" if p["kind"] == "frontend" else "root")
        )
        for p in packages
    }
    graph_layers = []
    for layer in layout["layers"]:
        dominant = Counter(
            primary_domain[pid] for pid in layer["members"]
        ).most_common(2)
        graph_layers.append(
            {
                "index": layer["index"],
                "y": layer["y"],
                "dominant": ", ".join(name for name, _count in dominant),
                "members": layer["members"],
                "member_count": len(layer["members"]),
            }
        )
    class_graph = build_class_graph(backend["modules"])
    health = build_health(
        backend["modules"],
        packages,
        layout["clusters"],
        collect_test_references(
            root, frozenset(m["name"] for m in backend["modules"])
        ),
    )

    modules = backend["modules"]
    total_lines = sum(m["lines"] for m in modules)
    fingerprint = hashlib.sha256(
        json.dumps(
            [[m["name"], m["lines"], m["imp"], m["domain"], m["cls"]] for m in modules],
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:16]

    name_index = {m["name"]: i for i, m in enumerate(modules)}
    compact_modules = [
        [
            m["name"],
            m["path"],
            m["pkg"],
            m["domain"],
            m["cls"],
            m["mig"],
            m["lines"],
            m["doc"],
            [name_index[i] for i in m["imp"]],
            [name_index[r] for r in m["rev"]],
            m["classes"],
        ]
        for m in modules
    ]
    fe_index = {f["path"]: i for i, f in enumerate(frontend["files"])}
    compact_frontend = [
        [
            f["path"],
            f["group"],
            f["lines"],
            f["doc"],
            [fe_index[f"frontend/src/{d}"] for d in f["deps"]],
            [fe_index[f"frontend/src/{r}"] for r in f["rev"]],
        ]
        for f in frontend["files"]
    ]

    hubs = sorted(modules, key=lambda m: (-len(m["rev"]), m["name"]))[:10]
    return {
        "readme": (
            "AgentsAssemble codebase map. Regenerate with "
            "`python3 scripts/generate_codebase_map.py`. modules[] columns: "
            "name, path, package, domain, classification, migration_status, "
            "lines, docstring, imports[], imported_by[] (indexes into modules), "
            "classes[[name, [[base, defining_module], ...]]] (best-effort, "
            "top-level classes only). frontend[] columns: path, group, lines, "
            "doc, imports[], imported_by[] (indexes into frontend). "
            "package_edges go importer -> dependency with module-import counts; "
            "`points` is the precomputed polyline for that edge and "
            "`intra_cycle` marks imports whose endpoints share one cycle group. "
            "graph{} holds the layered package drawing: layers[] top-to-bottom "
            "(each with members[] package ids, member_count, and the dominant "
            "domain), "
            "(a package sits below everything importing it), clusters[] are "
            "mutually dependent packages (import cycles at package level) drawn "
            "as one labelled block, plus width/height of the drawing. "
            "health{} ranks findings for a reader who has to act: cycles[] "
            "(mutually dependent packages, with the lines they hold), "
            "leaning_on_retiring[] (current modules importing legacy/compatibility "
            "modules - the reason legacy cannot be deleted), "
            "highest_leverage_migrations[] (retiring modules with the most current "
            "importers), unreferenced_shims[] (compatibility modules with no "
            "importer in agentsassemble/ and no reference in tests/; callers "
            "outside this repository are not visible, so verify before deleting), "
            "and hotspots[] (largest modules). health.scope states exactly what "
            "was measured. "
            "class_graph{} is the UML generalization DAG over base classes that "
            "resolve inside the repo: edges go base -> subclass, so a subclass "
            "always sits below the class it extends. Coordinates in graph and "
            "class_graph are laid out with a Sugiyama pipeline (cycle "
            "condensation, Coffman-Graham layering, dummy-node edge routing, "
            "median/transpose crossing reduction) and are deterministic."
        ),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "fingerprint": fingerprint,
        "stats": {
            "backend_modules": len(modules),
            "backend_lines": total_lines,
            "frontend_files": len(frontend["files"]),
            "frontend_lines": sum(f["lines"] for f in frontend["files"]),
            "packages": len(packages),
            "import_cycles": len(backend["cycles"]),
            "classifications": dict(
                sorted(Counter(m["cls"] for m in modules).items())
            ),
        },
        "packages": packages,
        "package_edges": package_edges,
        "graph": {
            "layers": graph_layers,
            "clusters": layout["clusters"],
            "width": layout["width"],
            "height": layout["height"],
        },
        "class_graph": class_graph,
        "health": health,
        "modules": compact_modules,
        "frontend": compact_frontend,
        "cycles": backend["cycles"],
        "hubs": [
            {"name": h["name"], "imported_by": len(h["rev"]), "doc": h["doc"]}
            for h in hubs
        ],
        "repo": collect_repo_areas(root),
    }


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AgentsAssemble Codebase Map</title>
<!-- Generated by scripts/generate_codebase_map.py - do not edit by hand.
     Machine-readable twin: CODEBASE_MAP.json (same directory). -->
<style>
:root {
  --bg: #0b0e14; --panel: #12171f; --panel2: #1a212c; --line: #262f3d;
  --line2: #313c4d;
  --text: #e3e9f2; --dim: #8b96a5; --accent: #6cb6ff; --accent2: #7ee0b8;
  --warn: #e0b46c; --bad: #e07c7c; --legacy: #8a8f98;
  --glow: 0 0 0 1px rgba(108,182,255,.35), 0 8px 30px -8px rgba(108,182,255,.45);
  --shadow: 0 1px 2px rgba(0,0,0,.4), 0 8px 24px -12px rgba(0,0,0,.7);
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text);
  font: 14px/1.5 -apple-system, "SF Pro Text", "Segoe UI", sans-serif;
  -webkit-font-smoothing: antialiased; }
/* Ambient wash so the flat dark panel does not read as a plain sheet. */
body::before { content: ""; position: fixed; inset: 0; pointer-events: none;
  z-index: 0;
  background:
    radial-gradient(70ch 40ch at 12% -10%, rgba(108,182,255,.10), transparent 70%),
    radial-gradient(60ch 40ch at 95% 0%, rgba(126,224,184,.07), transparent 70%); }
header, main, .drawer, footer { position: relative; z-index: 1; }
code, .mono { font-family: ui-monospace, "SF Mono", Menlo, monospace; }
header { padding: 18px 28px 12px; border-bottom: 1px solid var(--line);
  position: sticky; top: 0; z-index: 5;
  background: rgba(11,14,20,.82); backdrop-filter: blur(14px) saturate(140%); }
h1 { font-size: 19px; margin: 0 0 4px; letter-spacing: -.01em; }
.sub { color: var(--dim); font-size: 12.5px; }
.sub code { color: var(--accent2); }
nav { display: flex; gap: 4px; padding: 10px 28px 0; flex-wrap: wrap; }
nav button { background: none; border: 1px solid transparent; color: var(--dim);
  padding: 7px 14px; border-radius: 9px; cursor: pointer; font-size: 13.5px;
  font-weight: 500; transition: color .16s, background .16s, box-shadow .16s; }
nav button:hover { color: var(--text); background: rgba(255,255,255,.04); }
nav button.active { color: #fff; background: linear-gradient(180deg,
  rgba(108,182,255,.22), rgba(108,182,255,.10));
  box-shadow: inset 0 0 0 1px rgba(108,182,255,.35); }
main { padding: 20px 28px 60px; }
.view { display: none; } .view.active { display: block; animation: rise .22s ease both; }
@keyframes rise { from { opacity: 0; transform: translateY(4px); } }
.statgrid { display: grid; grid-template-columns: repeat(auto-fit,minmax(150px,1fr));
  gap: 10px; margin-bottom: 22px; }
.stat { background: linear-gradient(180deg, var(--panel2), var(--panel));
  border: 1px solid var(--line); border-radius: 12px; padding: 13px 15px;
  box-shadow: var(--shadow); }
.stat b { font-size: 22px; display: block; letter-spacing: -.02em;
  font-variant-numeric: tabular-nums; }
.stat span { color: var(--dim); font-size: 12px; }
.cards { display: grid; grid-template-columns: repeat(auto-fill,minmax(330px,1fr)); gap: 12px; }
.card { background: linear-gradient(180deg, var(--panel2), var(--panel));
  border: 1px solid var(--line); border-radius: 12px;
  padding: 14px 16px; cursor: pointer; box-shadow: var(--shadow);
  transition: transform .16s, border-color .16s, box-shadow .16s; }
.card:hover { border-color: rgba(108,182,255,.5); transform: translateY(-2px);
  box-shadow: 0 1px 2px rgba(0,0,0,.4), 0 16px 34px -18px rgba(108,182,255,.5); }
.card h3 { margin: 0 0 3px; font-size: 14.5px; }
.card h3 .badge { margin-left: 7px; }
.card p { margin: 0 0 8px; color: var(--dim); font-size: 12.5px; min-height: 2.6em; }
.meta { display: flex; gap: 12px; font-size: 12px; color: var(--dim); flex-wrap: wrap; }
.badge { font-size: 10.5px; padding: 1.5px 7px; border-radius: 20px;
  border: 1px solid var(--line); color: var(--dim); white-space: nowrap; }
::-webkit-scrollbar { width: 11px; height: 11px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: #2d3746; border-radius: 8px;
  border: 3px solid transparent; background-clip: content-box; }
::-webkit-scrollbar-thumb:hover { background: #3d4a5e; background-clip: content-box; }
.badge.legacy, .badge.root { color: var(--legacy); }
.badge.current { color: var(--accent2); }
.badge.compatibility { color: var(--warn); }
.badge.optional, .badge.frontend { color: #b79cff; }
.bar { display: flex; height: 5px; border-radius: 3px; overflow: hidden;
  background: var(--panel2); margin-top: 9px; }
.bar i { display: block; }
h2.section { font-size: 15px; margin: 26px 0 10px; color: var(--dim);
  text-transform: uppercase; letter-spacing: .06em; }
/* The graph canvas is a fixed viewport; zoom/pan happen by moving the SVG
   viewBox (crisp at any scale) rather than by scrolling a huge bitmap. */
.graphwrap { position: relative; height: 72vh; min-height: 420px; overflow: hidden;
  border: 1px solid var(--line); border-radius: 14px; box-shadow: var(--shadow);
  background:
    radial-gradient(120ch 60ch at 50% -20%, rgba(108,182,255,.06), transparent 70%),
    linear-gradient(180deg, #141a23, #10151d);
  cursor: grab; touch-action: none; }
.graphwrap.panning { cursor: grabbing; }
.graphwrap svg { display: block; width: 100%; height: 100%; }
.zoomhint { position: absolute; right: 10px; bottom: 9px; z-index: 2;
  padding: 4px 9px; border-radius: 7px; font-size: 11px; color: var(--dim);
  background: rgba(11,14,20,.72); border: 1px solid var(--line);
  backdrop-filter: blur(8px); pointer-events: none;
  font-variant-numeric: tabular-nums; }
svg text { fill: var(--text); font-family: inherit; }
.edge { stroke: #8496b4; stroke-width: 1.4; fill: none; opacity: .8;
  stroke-linejoin: round; stroke-linecap: round;
  transition: opacity .18s, stroke .18s; }
.edge.hot { stroke: var(--accent); opacity: 1;
  filter: drop-shadow(0 0 4px rgba(108,182,255,.75));
  stroke-dasharray: 7 5; animation: flow 1s linear infinite; }
@keyframes flow { to { stroke-dashoffset: -24; } }
.edge.cold { opacity: .14; }
.edge.intra { stroke: #61708c; stroke-dasharray: 3 3; }
.graphwrap:not([data-intra="on"]) .edge.intra { display: none; }
.cluster rect { fill: url(#clusterfill); stroke: #4d5b73; stroke-width: 1.3;
  stroke-dasharray: 7 5; rx: 14; }
.cluster text { font-size: 10.5px; fill: #9fb0c8; font-weight: 700;
  letter-spacing: .02em; }
.node rect.body { fill: url(#nodefill); stroke: var(--line2); rx: 9;
  transition: stroke .18s; }
.node { cursor: pointer; }
.node:hover rect.body { stroke: rgba(108,182,255,.65); }
.node.sel rect.body { stroke: var(--accent); stroke-width: 1.8;
  filter: drop-shadow(0 0 8px rgba(108,182,255,.55)); }
.node.dim { opacity: .34; }
.node .pk { font-size: 12.5px; font-weight: 650; }
.node .st { font-size: 10px; fill: var(--dim); }
.node .tag { width: 4px; rx: 2; stroke: none; }
/* UML class boxes: two compartments (name, owning module). */
.uml { cursor: pointer; }
.uml rect.body { fill: url(#umlfill); stroke: var(--line2); rx: 5;
  transition: stroke .18s; }
.uml:hover rect.body { stroke: rgba(108,182,255,.65); }
.uml line.div { stroke: var(--line2); }
.uml .cn { font-size: 12px; font-weight: 700; }
.uml .cm { font-size: 9.5px; fill: var(--dim); }
.uml.sel rect.body { stroke: var(--accent); stroke-width: 1.8;
  filter: drop-shadow(0 0 8px rgba(108,182,255,.55)); }
.uml.dim { opacity: .32; }
.gen { stroke: #97a8c4; stroke-width: 1.4; fill: none;
  transition: opacity .18s, stroke .18s; }
.gen.cold { opacity: .14; }
.gen.hot { stroke: var(--accent);
  filter: drop-shadow(0 0 4px rgba(108,182,255,.7)); }
.gtoolbar { display: flex; flex-wrap: wrap; align-items: center; gap: 10px 14px;
  margin: 0 0 10px; font-size: 12.5px; color: var(--dim); }
.gtoolbar label { display: inline-flex; align-items: center; gap: 6px;
  cursor: pointer; padding: 4px 9px; border-radius: 8px;
  border: 1px solid var(--line); background: var(--panel2);
  transition: border-color .16s, color .16s; }
.gtoolbar label:hover { color: var(--text); border-color: var(--line2); }
.zoomgroup { display: inline-flex; gap: 3px; padding: 3px;
  border: 1px solid var(--line); border-radius: 10px; background: var(--panel2); }
.zoomgroup button { font: inherit; color: var(--dim); background: none;
  border: 0; border-radius: 7px; padding: 4px 11px; cursor: pointer;
  transition: color .16s, background .16s; }
.zoomgroup button:hover { color: var(--text); background: rgba(255,255,255,.05); }
.zoomgroup button[aria-pressed="true"] { color: #fff;
  background: linear-gradient(180deg, rgba(108,182,255,.34), rgba(108,182,255,.16));
  box-shadow: inset 0 0 0 1px rgba(108,182,255,.4); }
.legend { display: flex; flex-wrap: wrap; gap: 7px; margin: 0 0 12px;
  padding: 0; border: 0; background: none; font-size: 12px; color: var(--dim); }
.legend b { color: var(--text); font-weight: 600; }
.legend span { display: inline-flex; align-items: center; gap: 6px;
  padding: 5px 11px; border-radius: 20px; border: 1px solid var(--line);
  background: linear-gradient(180deg, var(--panel2), var(--panel)); }
.legend i { display: inline-block; width: 11px; height: 11px; border-radius: 3px;
  box-shadow: 0 0 7px -1px currentColor; }
.legend .swatch-line { width: 22px; height: 0; border-radius: 0;
  border-top: 2px solid #8496b4; box-shadow: none; }
.legend .swatch-dash { width: 22px; height: 0; border-radius: 0;
  border-top: 2px dashed #61708c; box-shadow: none; }
table { width: 100%; border-collapse: collapse; font-size: 12.8px; }
th, td { text-align: left; padding: 6px 10px; border-bottom: 1px solid var(--line);
  vertical-align: top; }
th { color: var(--dim); font-weight: 600; position: sticky; top: 0;
  background: var(--panel); cursor: pointer; user-select: none; }
tr:hover td { background: var(--panel2); }
tr.sel td { background: #1f2c3f; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
.filters { display: flex; gap: 8px; margin-bottom: 12px; flex-wrap: wrap; }
.filters input, .filters select { background: var(--panel); color: var(--text);
  border: 1px solid var(--line); border-radius: 8px; padding: 7px 11px; font-size: 13px; }
.filters input { width: 300px; }
.drawer { position: fixed; top: 0; right: 0; bottom: 0; width: 460px; max-width: 92vw;
  background: var(--panel); border-left: 1px solid var(--line); padding: 20px 22px;
  overflow-y: auto; transform: translateX(100%); transition: transform .16s ease;
  z-index: 10; box-shadow: -12px 0 30px rgba(0,0,0,.4); }
.drawer.open { transform: none; }
.drawer h2 { margin: 0 26px 2px 0; font-size: 15px; word-break: break-all; }
.drawer .path { color: var(--dim); font-size: 12px; word-break: break-all; }
.drawer .doc { margin: 12px 0; font-size: 13px; color: var(--text);
  background: var(--panel2); padding: 10px 12px; border-radius: 8px; }
.drawer h4 { margin: 16px 0 6px; font-size: 12px; color: var(--dim);
  text-transform: uppercase; letter-spacing: .05em; }
.chips { display: flex; flex-wrap: wrap; gap: 5px; }
.chip { font-size: 11.5px; background: var(--panel2); border: 1px solid var(--line);
  border-radius: 6px; padding: 2.5px 8px; cursor: pointer; }
.chip:hover { border-color: var(--accent); color: var(--accent); }
.closex { position: absolute; top: 14px; right: 16px; background: none; border: none;
  color: var(--dim); font-size: 19px; cursor: pointer; }
.kv { display: grid; grid-template-columns: auto 1fr; gap: 3px 14px;
  font-size: 12.5px; margin-top: 10px; }
.kv dt { color: var(--dim); } .kv dd { margin: 0; }
.note { color: var(--dim); font-size: 12px; margin-top: 6px; }
/* tree view */
/* Architecture: the dependency layers rendered as a top-to-bottom flow. */
.flowstrip { display: grid; gap: 8px; margin-bottom: 22px; }
.flowlayer { display: flex; align-items: center; gap: 12px; padding: 11px 14px;
  border: 1px solid var(--line); border-radius: 12px;
  background: linear-gradient(90deg, rgba(108,182,255,.07), transparent 60%); }
.flowlayer .lvl { flex: none; width: 74px; font-size: 11px; font-weight: 800;
  letter-spacing: .05em; text-transform: uppercase; color: var(--accent); }
.flowlayer .pkgs { display: flex; flex-wrap: wrap; gap: 5px; flex: 1; }
.flowlayer .role { flex: none; font-size: 11.5px; color: var(--dim);
  max-width: 190px; text-align: right; }
.pkgchip { font-size: 11.5px; padding: 3px 9px; border-radius: 7px;
  border: 1px solid var(--line); background: var(--panel2); cursor: pointer;
  transition: border-color .16s, color .16s; }
.pkgchip:hover { border-color: rgba(108,182,255,.6); color: var(--accent); }
.flowarrow { text-align: center; color: var(--dim); font-size: 15px;
  line-height: 1; }
/* Health: findings ranked worst first. */
.finding { border: 1px solid var(--line); border-radius: 12px; margin-bottom: 12px;
  background: linear-gradient(180deg, var(--panel2), var(--panel));
  box-shadow: var(--shadow); overflow: hidden; }
.finding > header { display: flex; align-items: baseline; gap: 10px;
  padding: 12px 15px; border-bottom: 1px solid var(--line); flex-wrap: wrap; }
.finding h3 { margin: 0; font-size: 14px; }
.finding .sev { font-size: 10px; font-weight: 800; letter-spacing: .06em;
  text-transform: uppercase; padding: 2px 8px; border-radius: 20px; }
.finding .sev.high { color: #ffb4b4; background: rgba(224,124,124,.16);
  box-shadow: inset 0 0 0 1px rgba(224,124,124,.4); }
.finding .sev.med { color: #f0cf9a; background: rgba(224,180,108,.14);
  box-shadow: inset 0 0 0 1px rgba(224,180,108,.36); }
.finding .sev.low { color: #a9d6ff; background: rgba(108,182,255,.13);
  box-shadow: inset 0 0 0 1px rgba(108,182,255,.32); }
.finding .weight { margin-left: auto; color: var(--dim); font-size: 12px;
  font-variant-numeric: tabular-nums; }
.finding .why { padding: 10px 15px 0; color: var(--dim); font-size: 12.5px; }
.finding ol, .finding ul { margin: 8px 0 12px; padding: 0 15px 0 34px;
  font-size: 12.5px; }
.finding li { padding: 2.5px 0; }
.finding li code { color: var(--text); cursor: pointer; }
.finding li code:hover { color: var(--accent); }
.finding .sub { color: var(--dim); }
.morelink { display: inline-block; margin: 0 15px 12px; font-size: 12px;
  color: var(--accent); cursor: pointer; }
/* connections view */
.conn-toolbar { display: flex; gap: 10px; align-items: center; margin-bottom: 8px;
  flex-wrap: wrap; }
.conn-focus { font-size: 13px; }
.conn-focus code { color: var(--accent2); }
#connjump { background: var(--panel2); color: var(--text); border: 1px solid var(--line);
  border-radius: 9px; padding: 8px 12px; font-size: 13px; width: 280px;
  transition: border-color .16s, box-shadow .16s; }
#connjump:focus { outline: none; border-color: rgba(108,182,255,.6);
  box-shadow: 0 0 0 3px rgba(108,182,255,.14); }
.connwrap { border: 1px solid var(--line); border-radius: 14px;
  height: 72vh; overflow: hidden; position: relative; box-shadow: var(--shadow);
  background:
    radial-gradient(120ch 60ch at 50% -20%, rgba(108,182,255,.06), transparent 70%),
    linear-gradient(180deg, #141a23, #10151d); }
.connwrap svg { width: 100%; height: 100%; display: block; cursor: grab; }
.connwrap svg:active { cursor: grabbing; }
.cedge { stroke: #7787a3; stroke-width: 1.2; opacity: .62;
  transition: opacity .18s, stroke .18s; }
.cedge.hot { stroke: var(--accent); opacity: 1; stroke-width: 1.8;
  filter: drop-shadow(0 0 4px rgba(108,182,255,.7)); }
.cnode { cursor: pointer; }
.cnode circle { fill: url(#nodefill); stroke: var(--line2); stroke-width: 1.5;
  transition: stroke .18s; }
.cnode:hover circle { stroke: rgba(108,182,255,.7); }
.cnode.ext circle { opacity: .4; }
.cnode.ext text { opacity: .55; }
.cnode.center circle { stroke: var(--accent); stroke-width: 2.6;
  filter: drop-shadow(0 0 9px rgba(108,182,255,.6)); }
.cnode text { font-size: 10.5px; fill: var(--text); text-anchor: middle;
  pointer-events: none; }
.cnode .csub { font-size: 8.5px; fill: var(--dim); }
.actionbtn { background: var(--panel2); color: var(--text); border: 1px solid var(--line);
  border-radius: 8px; padding: 6px 13px; font-size: 12px; cursor: pointer;
  transition: border-color .16s, color .16s, background .16s; }
.actionbtn:hover { border-color: rgba(108,182,255,.6); color: var(--accent);
  background: rgba(108,182,255,.08); }
.drawer .actions { margin-top: 14px; display: flex; gap: 8px; }
.clsrow { font-size: 12.5px; padding: 3.5px 0; border-bottom: 1px dashed var(--line); }
.clsrow .base { color: var(--dim); font-size: 11.5px; }
footer { color: var(--dim); font-size: 12px; padding: 16px 28px 30px;
  border-top: 1px solid var(--line); }
a { color: var(--accent); }
</style>
</head>
<body>
<header>
  <h1>AgentsAssemble Codebase Map</h1>
  <div class="sub" id="gensub"></div>
  <nav>
    <button data-view="overview" class="active">Architecture</button>
    <button data-view="health">Health</button>
    <button data-view="graph">Dependency Graph</button>
    <button data-view="classes">Class Hierarchy</button>
    <button data-view="connections">Connections</button>
    <button data-view="modules">Module Explorer</button>
  </nav>
</header>
<main>
  <section id="overview" class="view active">
    <div class="legend" id="orient"></div>
    <h2 class="section">How dependencies flow</h2>
    <p class="note">Layers come from the package dependency graph: a package only
      imports packages in rows below its own. <b>&#9650;</b> nothing imports it (an
      entry point), <b>&#9660;</b> it imports nothing else here (a leaf). Groups with
      no import path between them &mdash; the frontend, for instance &mdash; are
      independent and simply occupy their own rows; the right-hand label is the
      row's dominant domain. Click a package to open it in the graph.</p>
    <div class="flowstrip" id="flow"></div>
    <div class="statgrid" id="stats"></div>
    <h2 class="section">Packages</h2>
    <div class="cards" id="pkgcards"></div>
    <h2 class="section">Import Hubs (most depended-upon modules)</h2>
    <div class="cards" id="hubs"></div>
    <h2 class="section">Repository Areas</h2>
    <div class="cards" id="repo"></div>
  </section>
  <section id="health" class="view">
    <p class="note">Measured findings, worst first. Everything here is derived
      from the same scan as the rest of the map &mdash; no hand-maintained lists.
      <b id="healthscope"></b></p>
    <div class="statgrid" id="healthstats"></div>
    <div id="healthbody"></div>
  </section>
  <section id="graph" class="view">
    <p class="note">Layered dependency graph (Sugiyama: cycle condensation &rarr;
      Coffman&ndash;Graham layering &rarr; dummy-node routing &rarr; median/transpose
      crossing reduction). A node sits <em>below</em> everything that imports it, so
      depth reads top&nbsp;&rarr;&nbsp;bottom and arrows point importer &rarr;
      dependency. Click a node to isolate its edges.</p>
    <div class="legend" id="graphlegend"></div>
    <div class="gtoolbar">
      <span class="zoomgroup">
        <button id="zout" title="Zoom out">&minus;</button>
        <button id="zin" title="Zoom in">+</button>
        <button id="zfit" aria-pressed="true">Fit</button>
        <button id="zone" aria-pressed="false">1:1</button>
      </span>
      <label><input type="checkbox" id="showintra">
        Show imports inside cycle groups (<span id="intracount"></span>)</label>
      <span id="graphsummary"></span>
    </div>
    <div class="graphwrap" id="graphwrap" data-zoom="fit"></div>
  </section>
  <section id="classes" class="view">
    <p class="note">UML generalization (class inheritance) for base classes that
      resolve inside this repository; external bases such as
      <code>Protocol</code> or <code>Exception</code> are left out. Read it the UML
      way: the <b>hollow triangle points at the base class</b>, and a subclass always
      sits below the class it extends. Click a class to open its module.</p>
    <div class="legend" id="classlegend"></div>
    <div class="gtoolbar">
      <span class="zoomgroup">
        <button id="czout" title="Zoom out">&minus;</button>
        <button id="czin" title="Zoom in">+</button>
        <button id="czfit" aria-pressed="true">Fit</button>
        <button id="czone" aria-pressed="false">1:1</button>
      </span>
      <span id="classsummary"></span>
    </div>
    <div class="graphwrap" id="classwrap" data-zoom="fit"></div>
  </section>
  <section id="connections" class="view">
    <div class="conn-toolbar">
      <span id="connfocus" class="conn-focus">Pick a module or open one from the
        Tree / a drawer.</span>
      <input id="connjump" list="modnames" placeholder="Jump to module&hellip;">
      <datalist id="modnames"></datalist>
      <button id="connfit" class="actionbtn">Fit</button>
    </div>
    <p class="note">Center = selected module. Left/outgoing = modules it imports,
      right/incoming = modules that import it (arrows point importer &rarr;
      dependency). Click a node to re-center; drag to rearrange; wheel to zoom.
      Dimmed nodes sit outside the selected package.</p>
    <div class="connwrap" id="connwrap"></div>
  </section>
  <section id="modules" class="view">
    <div class="filters">
      <input id="q" type="search" placeholder="Filter by name, path, or docstring&hellip;">
      <select id="fpkg"><option value="">All packages</option></select>
      <select id="fcls"><option value="">All classes</option></select>
      <select id="fsrc"><option value="">Backend + Frontend</option>
        <option value="backend">Backend only</option><option value="frontend">Frontend only</option></select>
    </div>
    <div class="note" id="count"></div>
    <table><thead><tr>
      <th data-k="name">Module</th><th data-k="pkg">Package</th>
      <th data-k="cls">Class</th><th data-k="lines" class="num">Lines</th>
      <th data-k="imp" class="num">Imports</th><th data-k="rev" class="num">Imported&nbsp;by</th>
      <th>Docstring</th>
    </tr></thead><tbody id="rows"></tbody></table>
  </section>
</main>
<aside class="drawer" id="drawer"><button class="closex" id="closex">&times;</button>
  <div id="drawerbody"></div></aside>
<footer id="foot"></footer>
<script id="mapdata" type="application/json">__DATA__</script>
<script>
"use strict";
const D = JSON.parse(document.getElementById("mapdata").textContent);
const MCOL = ["name","path","pkg","domain","cls","mig","lines","doc","imp","rev","classes"];
const FCOL = ["path","group","lines","doc","imp","rev"];
const modules = D.modules.map(r => Object.fromEntries(MCOL.map((k,i)=>[k,r[i]])));
const feFiles = D.frontend.map(r => Object.fromEntries(FCOL.map((k,i)=>[k,r[i]])));
const pkgById = Object.fromEntries(D.packages.map(p => [p.id, p]));
const modByName = Object.fromEntries(modules.map(m => [m.name, m]));
const feByPath = Object.fromEntries(feFiles.map(f => [f.path, f]));

document.getElementById("gensub").innerHTML =
  `Generated ${D.generated_at} &middot; fingerprint <code>${D.fingerprint}</code> &middot; ` +
  `regenerate: <code>python3 scripts/generate_codebase_map.py</code> &middot; ` +
  `machine-readable twin: <code>docs/product/CODEBASE_MAP.json</code>`;
document.getElementById("foot").innerHTML =
  "Backend domains/classifications/migration status match " +
  "<code>docs/product/PACKAGE_MAP.md</code> (same generator graph). " +
  "Import cycles: " + D.stats.import_cycles +
  " (detail: <code>docs/product/PACKAGE_CYCLES.md</code>).";

// ---- tabs ----
function switchView(id) {
  document.querySelectorAll("nav button").forEach(x =>
    x.classList.toggle("active", x.dataset.view === id));
  document.querySelectorAll(".view").forEach(v => v.classList.toggle("active", v.id === id));
}
document.querySelectorAll("nav button").forEach(b => b.onclick = () => switchView(b.dataset.view));

// ---- overview ----
// Orientation for a reader (human or agent) opening this file with no context.
document.getElementById("orient").innerHTML = [
  `<span><b>What this is:</b> a generated map of this repository — every number`
    + ` below is scanned from the source tree, not hand-written.</span>`,
  `<span><b>Health</b> = what needs work, worst first</span>`,
  `<span><b>Dependency Graph</b> = which package imports which</span>`,
  `<span><b>Class Hierarchy</b> = UML inheritance</span>`,
  `<span><b>Connections</b> = one module's neighbours</span>`,
  `<span><b>Module Explorer</b> = searchable table of every module and frontend file</span>`,
  `<span><b>Agents:</b> read <code>docs/product/CODEBASE_MAP.json</code> instead —`
    + ` same data, with a <code>readme</code> field describing every column.</span>`,
].join("");

const S = D.stats;
document.getElementById("stats").innerHTML = [
  [S.backend_modules, "backend modules"], [S.backend_lines.toLocaleString(), "backend lines"],
  [S.frontend_files, "frontend files"], [S.frontend_lines.toLocaleString(), "frontend lines"],
  [S.packages, "map packages"], [S.import_cycles, "import cycles"],
  [D.package_edges.length, "package edges"],
].map(([v,l]) => `<div class="stat"><b>${v}</b><span>${l}</span></div>`).join("");

// Dependency flow: one row per graph layer. Roles are read off the edges rather
// than off the row position, because a disconnected group (the frontend) lands
// on its own row and is not a "foundation" of the backend.
const hasImporters = new Set(D.package_edges.map(e => e.to));
const hasDependencies = new Set(D.package_edges.map(e => e.from));
document.getElementById("flow").innerHTML = D.graph.layers.map((layer, i) => {
  const chips = layer.members.map(id => {
    const mark = !hasImporters.has(id) ? " ▲" : !hasDependencies.has(id) ? " ▼" : "";
    const title = !hasImporters.has(id) ? "nothing imports this — an entry point"
      : !hasDependencies.has(id) ? "imports nothing else here — a leaf" : "";
    return `<span class="pkgchip" data-jumppkg="${esc(id)}" title="${title}">`
      + `${esc(id)}${mark}</span>`;
  }).join("");
  return (i ? `<div class="flowarrow">&darr;</div>` : "")
    + `<div class="flowlayer"><span class="lvl">Layer ${layer.index}</span>`
    + `<span class="pkgs">${chips}</span>`
    + `<span class="role">${esc(layer.dominant)}</span></div>`;
}).join("");

const CLS_COLOR = {current:"#7ee0b8", optional:"#b79cff", compatibility:"#e0b46c",
  legacy:"#8a8f98", "deferred-policy":"#e07c7c"};
document.getElementById("pkgcards").innerHTML = D.packages.map(p => {
  const total = Math.max(1, Object.values(p.classes).reduce((a,b)=>a+b,0));
  const bar = Object.entries(p.classes).map(([c,n]) =>
    `<i style="width:${(n/total*100).toFixed(1)}%;background:${CLS_COLOR[c]||"#666"}"></i>`).join("");
  const dominant = Object.entries(p.classes).sort((a,b) => b[1]-a[1])[0];
  const kind = p.kind === "frontend" ? "frontend"
    : p.id.startsWith("root:") ? "root" : (dominant ? dominant[0] : "current");
  return `<div class="card" data-pkg="${p.id}"><h3><code>${p.id}</code>
    <span class="badge ${kind}">${kind}</span></h3><p>${esc(p.doc)}</p>
    <div class="meta"><span>${p.files} files</span><span>${p.lines.toLocaleString()} lines</span>
    ${Object.entries(p.domains).map(([d,n])=>`<span>${d} ${n}</span>`).join("")}</div>
    <div class="bar">${bar}</div></div>`;
}).join("");
document.getElementById("hubs").innerHTML = D.hubs.map(h =>
  `<div class="card" data-mod="${h.name}"><h3><code>${short(h.name)}</code></h3>
   <p>${esc(h.doc)}</p><div class="meta"><span>imported by ${h.imported_by} modules</span></div></div>`).join("");
document.getElementById("repo").innerHTML = D.repo.map(a =>
  `<div class="card"><h3><code>${a.name}/</code></h3><p>${esc(a.note)}</p>
   <div class="meta"><span>${a.files} files</span><span>${a.lines.toLocaleString()} lines</span></div></div>`).join("");

// ---- health ----
(function renderHealth() {
  const HL = D.health;
  const n = v => v.toLocaleString();
  document.getElementById("healthscope").textContent = HL.scope;
  document.getElementById("healthstats").innerHTML = [
    [n(HL.totals.retiring_lines), "lines in legacy / compat code"],
    [n(HL.totals.current_to_retiring_imports), "current → retiring imports"],
    [n(HL.unreferenced_shim_count), "shims with no caller found"],
    [n(HL.totals.test_only_shims), "shims held only by tests"],
    [n(HL.cycles.length), "cycle groups"],
  ].map(([v, l]) => `<div class="stat"><b>${v}</b><span>${l}</span></div>`).join("");

  const modLink = name => `<code data-jumpmod="${esc(name)}">${esc(short(name))}</code>`;
  const cards = [];

  for (const cycle of HL.cycles) {
    cards.push(finding({
      severity: cycle.members.length > 6 ? "high" : "med",
      title: `Import cycle across ${cycle.members.length} packages`,
      weight: `${n(cycle.lines)} lines involved · ${cycle.internal_edges} internal imports`,
      why: `These packages import each other, so none of them can be built, tested,`
        + ` moved or reasoned about on its own. Heaviest member:`
        + ` <b>${esc(cycle.heaviest)}</b>. Breaking one edge out of the loop is what`
        + ` unlocks the rest.`,
      items: cycle.members.map(m =>
        `<span class="pkgchip" data-jumppkg="${esc(m)}">${esc(m)}</span>`),
      inline: true,
    }));
  }

  if (HL.leaning_on_retiring.length) {
    cards.push(finding({
      severity: "high",
      title: "Current code importing code that is meant to go away",
      weight: `${n(HL.totals.current_to_retiring_imports)} imports`,
      why: `Every one of these is a reason the legacy tree cannot be deleted.`
        + ` The top entries are the chokepoints: fix those and most of the`
        + ` dependency disappears at once.`,
      items: HL.leaning_on_retiring.map(item =>
        `${modLink(item.module)} <span class="sub">→ ${item.count} retiring`
        + ` imports · ${n(item.lines)} lines</span>`),
      ordered: true,
    }));
  }

  if (HL.highest_leverage_migrations.length) {
    cards.push(finding({
      severity: "med",
      title: "Highest-leverage migrations",
      weight: `${HL.highest_leverage_migrations.length} modules`,
      why: `Retiring modules ranked by how many current modules still import them.`
        + ` Migrating the top of this list frees the most callers per unit of work.`,
      items: HL.highest_leverage_migrations.map(item =>
        `${modLink(item.module)} <span class="sub">← ${item.count} current`
        + ` importer(s) · ${item.cls} · ${n(item.lines)} lines</span>`),
      ordered: true,
    }));
  }

  if (HL.unreferenced_shims.length) {
    cards.push(finding({
      severity: "low",
      title: "Compatibility shims with no caller found",
      weight: `${n(HL.unreferenced_shim_count)} files · `
        + `${n(HL.totals.unreferenced_shim_lines)} lines`,
      why: `No module under <code>agentsassemble/</code> imports these, and no file`
        + ` under <code>tests/</code> names them either. A further`
        + ` ${n(HL.totals.test_only_shims)} shims are referenced only by tests and are`
        + ` deliberately <em>not</em> listed here. Check external callers before`
        + ` deleting: this repository is all the generator can see.`,
      items: HL.unreferenced_shims.map(item =>
        `${modLink(item.module)} <span class="sub">${n(item.lines)} lines</span>`),
      more: HL.unreferenced_shim_count - HL.unreferenced_shims.length,
    }));
  }

  cards.push(finding({
    severity: "low",
    title: "Largest modules",
    weight: `top ${HL.hotspots.length}`,
    why: `Size alone is not a defect, but these are where change is most likely`
      + ` to be risky and where splitting pays off first.`,
    items: HL.hotspots.map(item =>
      `${modLink(item.module)} <span class="sub">${n(item.lines)} lines ·`
      + ` ${item.cls} · imported by ${item.imported_by}</span>`),
    ordered: true,
  }));

  document.getElementById("healthbody").innerHTML = cards.join("");

  function finding(spec) {
    const list = spec.inline
      ? `<div style="display:flex;flex-wrap:wrap;gap:5px;padding:10px 15px 14px">`
        + spec.items.join("") + `</div>`
      : `<${spec.ordered ? "ol" : "ul"}>`
        + spec.items.map(i => `<li>${i}</li>`).join("")
        + `</${spec.ordered ? "ol" : "ul"}>`;
    return `<section class="finding">
      <header><span class="sev ${spec.severity}">${spec.severity}</span>
        <h3>${spec.title}</h3><span class="weight">${spec.weight}</span></header>
      <p class="why">${spec.why}</p>${list}`
      + (spec.more > 0
        ? `<span class="morelink" data-view-jump="modules">+ ${spec.more} more —`
          + ` filter by class "compatibility" in Module Explorer</span>` : "")
      + `</section>`;
  }
})();

// ---- graph ----
const NS = "http://www.w3.org/2000/svg";
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
// Shared paint: gradients for boxes/cluster fills plus the dot grid backdrop.
const STAGE_DEFS = `
  <linearGradient id="nodefill" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#232c3a"/><stop offset="1" stop-color="#171d27"/>
  </linearGradient>
  <linearGradient id="umlfill" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#242d3c"/><stop offset="1" stop-color="#181f2a"/>
  </linearGradient>
  <linearGradient id="clusterfill" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="rgba(124,139,168,.13)"/>
    <stop offset="1" stop-color="rgba(124,139,168,.04)"/>
  </linearGradient>
  <pattern id="dotgrid" width="26" height="26" patternUnits="userSpaceOnUse">
    <circle cx="1.5" cy="1.5" r="1.1" fill="#242e3d"/>
  </pattern>`;
function stageBackdrop() {
  const rect = document.createElementNS(NS, "rect");
  rect.setAttribute("x", -6000); rect.setAttribute("y", -6000);
  rect.setAttribute("width", 16000); rect.setAttribute("height", 16000);
  rect.setAttribute("fill", "url(#dotgrid)");
  rect.setAttribute("pointer-events", "none");
  return rect;
}
// Zoom/pan stage: the SVG viewBox is the camera, so text and strokes stay crisp
// at every scale. Plain wheel is left alone so the page keeps scrolling; zoom is
// Cmd/Ctrl + wheel (also pinch, which browsers report as ctrl+wheel).
function attachStage(wrap, svg, W, H, buttons) {
  const PAD = 26, MIN_SCALE = 0.12, MAX_SCALE = 6;
  let view = { x: 0, y: 0, w: W, h: H };
  const hint = document.createElement("div");
  hint.className = "zoomhint";
  wrap.appendChild(hint);
  const aspect = () => wrap.clientWidth / Math.max(1, wrap.clientHeight);
  const scaleNow = () => wrap.clientWidth / view.w;
  function apply() {
    svg.setAttribute("viewBox", `${view.x} ${view.y} ${view.w} ${view.h}`);
    hint.textContent = `${Math.round(scaleNow() * 100)}% · ⌘/Ctrl+scroll zoom · drag pan`;
  }
  function fit() {
    // The tab may still be hidden (zero-size); the ResizeObserver re-fits once
    // it is shown, so skip rather than divide by zero.
    wrap.dataset.zoom = "fit"; press("fit");
    if (!wrap.clientWidth || !wrap.clientHeight) return;
    const cw = W + PAD * 2, ch = H + PAD * 2;
    let w = cw, h = ch;
    if (cw / ch > aspect()) h = w / aspect(); else w = h * aspect();
    view = { x: -PAD - (w - cw) / 2, y: -PAD - (h - ch) / 2, w, h };
    apply();
  }
  function actualSize() {
    const cx = view.x + view.w / 2, cy = view.y + view.h / 2;
    const w = wrap.clientWidth, h = wrap.clientHeight;
    view = { x: cx - w / 2, y: cy - h / 2, w, h };
    wrap.dataset.zoom = "one"; press("one"); apply();
  }
  function zoomAt(clientX, clientY, factor) {
    const rect = wrap.getBoundingClientRect();
    const ux = view.x + ((clientX - rect.left) / rect.width) * view.w;
    const uy = view.y + ((clientY - rect.top) / rect.height) * view.h;
    const minW = wrap.clientWidth / MAX_SCALE, maxW = wrap.clientWidth / MIN_SCALE;
    const nextW = clamp(view.w / factor, minW, maxW);
    const k = nextW / view.w;
    if (k === 1) return;
    view = { x: ux - (ux - view.x) * k, y: uy - (uy - view.y) * k,
             w: nextW, h: view.h * k };
    wrap.dataset.zoom = "free"; press(null); apply();
  }
  function press(mode) {
    if (buttons.fit) buttons.fit.setAttribute("aria-pressed", String(mode === "fit"));
    if (buttons.one) buttons.one.setAttribute("aria-pressed", String(mode === "one"));
  }
  wrap.addEventListener("wheel", event => {
    if (!event.metaKey && !event.ctrlKey) return;   // let the page scroll
    event.preventDefault();
    zoomAt(event.clientX, event.clientY, Math.exp(-event.deltaY * 0.0022));
  }, { passive: false });
  let drag = null;
  wrap.addEventListener("pointerdown", event => {
    if (event.button !== 0) return;
    drag = { x: event.clientX, y: event.clientY, moved: false };
    wrap.setPointerCapture(event.pointerId);
  });
  wrap.addEventListener("pointermove", event => {
    if (!drag) return;
    const rect = wrap.getBoundingClientRect();
    const dx = (event.clientX - drag.x) / rect.width * view.w;
    const dy = (event.clientY - drag.y) / rect.height * view.h;
    if (!drag.moved && Math.hypot(event.clientX - drag.x, event.clientY - drag.y) > 3) {
      drag.moved = true; wrap.classList.add("panning");
    }
    if (!drag.moved) return;
    drag.x = event.clientX; drag.y = event.clientY;
    view.x -= dx; view.y -= dy;
    apply();
  });
  // A finished pan is followed by a click; swallow only that one. A timestamp
  // guard is used instead of a one-shot listener so a pan that never produces a
  // click cannot eat the user's next real click.
  let panEndedAt = -1;
  wrap.addEventListener("click", event => {
    if (performance.now() - panEndedAt < 250) event.stopPropagation();
  }, true);
  const endDrag = () => {
    if (drag && drag.moved) panEndedAt = performance.now();
    drag = null; wrap.classList.remove("panning");
  };
  wrap.addEventListener("pointerup", endDrag);
  wrap.addEventListener("pointercancel", endDrag);
  if (buttons.fit) buttons.fit.onclick = fit;
  if (buttons.one) buttons.one.onclick = actualSize;
  if (buttons.inn) buttons.inn.onclick = () => {
    const r = wrap.getBoundingClientRect();
    zoomAt(r.left + r.width / 2, r.top + r.height / 2, 1.3);
  };
  if (buttons.out) buttons.out.onclick = () => {
    const r = wrap.getBoundingClientRect();
    zoomAt(r.left + r.width / 2, r.top + r.height / 2, 1 / 1.3);
  };
  let raf = 0;
  new ResizeObserver(() => {
    cancelAnimationFrame(raf);
    raf = requestAnimationFrame(() => {
      if (wrap.dataset.zoom === "fit") fit(); else apply();
    });
  }).observe(wrap);
  fit();
  return { fit, apply };
}
// Migration status is encoded as a colour stripe so the node body stays small.
const MIG_COLOR = { current: "#3fb950", legacy: "#d29922", compatibility: "#8a8f98",
  optional: "#a371f7", planned: "#58a6ff" };
const pkgKindColor = p => p.kind === "frontend" ? "#b79cff"
  : p.id.startsWith("root:") ? "#8a8f98" : "#6cb6ff";
const dominantClass = p => {
  const entries = Object.entries(p.classes || {});
  if (!entries.length) return "";
  return entries.sort((a, b) => b[1] - a[1])[0][0];
};
function polyPath(points) {
  // Routed polylines come from the layout; round the interior corners so long
  // dummy-node chains read as one smooth edge instead of a zigzag.
  if (points.length < 2) return "";
  if (points.length === 2) {
    return `M${points[0][0]},${points[0][1]} L${points[1][0]},${points[1][1]}`;
  }
  let d = `M${points[0][0]},${points[0][1]}`;
  for (let i = 1; i < points.length - 1; i++) {
    const [px, py] = points[i - 1], [cx, cy] = points[i], [nx, ny] = points[i + 1];
    const inLen = Math.hypot(cx - px, cy - py), outLen = Math.hypot(nx - cx, ny - cy);
    const r = Math.min(14, inLen / 2, outLen / 2);
    if (r < 0.5) { d += ` L${cx},${cy}`; continue; }
    d += ` L${cx + (px - cx) / inLen * r},${cy + (py - cy) / inLen * r}`;
    d += ` Q${cx},${cy} ${cx + (nx - cx) / outLen * r},${cy + (ny - cy) / outLen * r}`;
  }
  const last = points[points.length - 1];
  return d + ` L${last[0]},${last[1]}`;
}
(function drawGraph() {
  const wrap = document.getElementById("graphwrap");
  const W = D.graph.width, H = D.graph.height;
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
  const defs = document.createElementNS(NS, "defs");
  defs.innerHTML = STAGE_DEFS + `
    <marker id="arr" viewBox="0 0 9 9" refX="8" refY="4.5" markerWidth="6"
      markerHeight="6" orient="auto-start-reverse">
      <path d="M0,0 L9,4.5 L0,9 z" fill="#8496b4"/></marker>
    <marker id="arrhot" viewBox="0 0 9 9" refX="8" refY="4.5" markerWidth="6"
      markerHeight="6" orient="auto-start-reverse">
      <path d="M0,0 L9,4.5 L0,9 z" fill="#6cb6ff"/></marker>`;
  svg.appendChild(defs);
  svg.appendChild(stageBackdrop());
  for (const c of D.graph.clusters || []) {
    const g = document.createElementNS(NS, "g");
    g.setAttribute("class", "cluster");
    g.innerHTML = `<rect x="${c.x}" y="${c.y}" width="${c.w}" height="${c.h}"></rect>
      <text x="${c.x + 12}" y="${c.label_y}">↻ cycle group · ${c.members.length} packages`
      + ` · ${c.internal_edges} internal imports</text>`;
    const t = document.createElementNS(NS, "title");
    t.textContent = `Mutually dependent: ${c.members.join(", ")}`;
    g.appendChild(t);
    svg.appendChild(g);
  }
  const maxCount = Math.max(1, ...D.package_edges.map(e => e.count));
  for (const e of D.package_edges) {
    if (!pkgById[e.from] || !pkgById[e.to] || !e.points) continue;
    const path = document.createElementNS(NS, "path");
    path.setAttribute("d", polyPath(e.points));
    path.setAttribute("class", "edge" + (e.intra_cycle ? " intra" : ""));
    path.setAttribute("marker-end", "url(#arr)");
    // Heavier line = more module-level imports crossing that package boundary.
    path.setAttribute("stroke-width",
      (1.2 + 2.3 * Math.sqrt(e.count / maxCount)).toFixed(2));
    path.dataset.from = e.from; path.dataset.to = e.to;
    const t = document.createElementNS(NS, "title");
    t.textContent = `${e.from} imports ${e.to} (${e.count} module imports)`
      + (e.intra_cycle ? " — inside a cycle group" : "");
    path.appendChild(t); svg.appendChild(path);
  }
  for (const p of D.packages) {
    const g = document.createElementNS(NS, "g");
    g.setAttribute("class", "node"); g.dataset.pkg = p.id;
    const cls = dominantClass(p);
    g.innerHTML = `<rect class="body" x="${p.x}" y="${p.y}" width="${p.w}"
        height="${p.h}"></rect>
      <rect class="tag" x="${p.x + 5}" y="${p.y + 8}" width="4" height="${p.h - 16}"
        fill="${MIG_COLOR[cls] || "#8a8f98"}"></rect>
      <text class="pk" x="${p.x + 16}" y="${p.y + 22}" fill="${pkgKindColor(p)}">${esc(p.id)}</text>
      <text class="st" x="${p.x + 16}" y="${p.y + 38}">${p.files} files · ${p.lines.toLocaleString()} lines</text>`;
    const t = document.createElementNS(NS, "title");
    t.textContent = `${p.id} — ${p.doc}\n${Object.entries(p.classes)
      .map(([c, n]) => c + ": " + n).join(", ")}`;
    g.appendChild(t);
    g.onclick = () => selectPackage(p.id, true);
    svg.appendChild(g);
  }
  wrap.innerHTML = ""; wrap.appendChild(svg);

  const intra = D.package_edges.filter(e => e.intra_cycle).length;
  document.getElementById("intracount").textContent = intra + " edges";
  document.getElementById("graphsummary").textContent =
    `${D.packages.length} packages · ${D.package_edges.length - intra} cross-layer edges`
    + ` · ${D.graph.layers.length} layers`;
  attachStage(wrap, svg, W, H, {
    fit: document.getElementById("zfit"), one: document.getElementById("zone"),
    inn: document.getElementById("zin"), out: document.getElementById("zout"),
  });
  document.getElementById("showintra").onchange = event => {
    wrap.dataset.intra = event.currentTarget.checked ? "on" : "off";
  };
  document.getElementById("graphlegend").innerHTML = [
    `<span><b>Read:</b> arrow = imports, target is the dependency</span>`,
    `<span><i style="background:#6cb6ff"></i>backend package</span>`,
    `<span><i style="background:#b79cff"></i>frontend folder</span>`,
    `<span><i style="background:#8a8f98"></i>root: module group</span>`,
    `<span><i class="swatch-line"></i>thicker = more imports</span>`,
    `<span><i class="swatch-dash"></i>inside a cycle group</span>`,
    `<span><b>Stripe:</b> ` + Object.entries(MIG_COLOR)
      .map(([k, v]) => `<i style="background:${v}"></i>${k}`).join(" ") + `</span>`,
  ].join("");
})();
function highlightEdges(pkgId) {
  document.querySelectorAll(".edge").forEach(el => {
    const hot = el.dataset.from === pkgId || el.dataset.to === pkgId;
    el.classList.toggle("hot", hot);
    el.classList.toggle("cold", !hot && !!pkgId);
    el.setAttribute("marker-end", hot ? "url(#arrhot)" : "url(#arr)");
  });
  document.querySelectorAll(".node").forEach(n =>
    n.classList.toggle("sel", n.dataset.pkg === pkgId));
}

// ---- class hierarchy (UML generalization) ----
(function drawClasses() {
  const wrap = document.getElementById("classwrap");
  const CG = D.class_graph;
  if (!CG || !CG.nodes.length) {
    wrap.innerHTML = `<p class="note" style="padding:18px">No inheritance edges`
      + ` resolve inside the repository.</p>`;
    document.getElementById("classsummary").textContent = "";
    return;
  }
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${CG.width} ${CG.height}`);
  svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
  // Generalization arrowhead: hollow triangle at the base-class end. It is used
  // as marker-start (paths run base -> subclass), so auto-start-reverse turns it
  // back around to point at the base.
  const defs = document.createElementNS(NS, "defs");
  defs.innerHTML = STAGE_DEFS + `
    <marker id="gen" viewBox="0 0 12 12" refX="11" refY="6" markerWidth="10"
      markerHeight="10" orient="auto-start-reverse">
      <path d="M1,1 L11,6 L1,11 z" fill="#151b26" stroke="#97a8c4"
        stroke-width="1.2"/></marker>
    <marker id="genhot" viewBox="0 0 12 12" refX="11" refY="6" markerWidth="10"
      markerHeight="10" orient="auto-start-reverse">
      <path d="M1,1 L11,6 L1,11 z" fill="#151b26" stroke="#6cb6ff"
        stroke-width="1.2"/></marker>`;
  svg.appendChild(defs);
  svg.appendChild(stageBackdrop());
  for (const e of CG.edges) {
    const path = document.createElementNS(NS, "path");
    path.setAttribute("d", polyPath(e.points));
    path.setAttribute("class", "gen");
    path.setAttribute("marker-start", "url(#gen)");
    path.dataset.from = e.from; path.dataset.to = e.to;
    const t = document.createElementNS(NS, "title");
    t.textContent = `${e.to.split("::")[1]} extends ${e.from.split("::")[1]}`;
    path.appendChild(t); svg.appendChild(path);
  }
  const shortModule = m => m.replace(/^agentsassemble\\./, "");
  for (const n of CG.nodes) {
    const g = document.createElementNS(NS, "g");
    g.setAttribute("class", "uml"); g.dataset.cid = n.id;
    g.innerHTML = `
      <rect class="body" x="${n.x}" y="${n.y}" width="${n.w}" height="${n.h}"></rect>
      <rect class="tag" x="${n.x + 4}" y="${n.y + 6}" width="4" height="${n.h - 12}"
        rx="2" fill="${MIG_COLOR[n.cls] || "#8a8f98"}"></rect>
      <line class="div" x1="${n.x}" y1="${n.y + 25}" x2="${n.x + n.w}" y2="${n.y + 25}"></line>
      <text class="cn" x="${n.x + 14}" y="${n.y + 17}">${esc(n.name)}</text>
      <text class="cm" x="${n.x + 14}" y="${n.y + 39}">${esc(shortModule(n.module))}</text>`;
    const t = document.createElementNS(NS, "title");
    t.textContent = `${n.name} — ${n.module}`;
    g.appendChild(t);
    g.onclick = () => {
      document.querySelectorAll(".uml").forEach(el =>
        el.classList.toggle("sel", el.dataset.cid === n.id));
      document.querySelectorAll(".gen").forEach(el => {
        const hot = el.dataset.from === n.id || el.dataset.to === n.id;
        el.classList.toggle("hot", hot);
        el.classList.toggle("cold", !hot);
        el.setAttribute("marker-start", hot ? "url(#genhot)" : "url(#gen)");
      });
      if (modByName[n.module]) showModule(n.module);
    };
    svg.appendChild(g);
  }
  wrap.innerHTML = ""; wrap.appendChild(svg);
  const roots = CG.nodes.filter(n => !CG.edges.some(e => e.to === n.id)).length;
  const depth = CG.nodes.reduce((max, n) => Math.max(max, n.layer), 0) + 1;
  document.getElementById("classsummary").textContent =
    `${CG.nodes.length} classes · ${CG.edges.length} inheritance edges · `
    + `${roots} base classes · ${depth} levels deep`;
  attachStage(wrap, svg, CG.width, CG.height, {
    fit: document.getElementById("czfit"), one: document.getElementById("czone"),
    inn: document.getElementById("czin"), out: document.getElementById("czout"),
  });
  document.getElementById("classlegend").innerHTML = [
    `<span><b>Read:</b> hollow triangle &#9651; points at the base class</span>`,
    `<span>upper box = base, lower box = subclass</span>`,
    `<span><b>Box:</b> class name / owning module</span>`,
    `<span><b>Stripe:</b> ` + Object.entries(MIG_COLOR)
      .map(([k, v]) => `<i style="background:${v}"></i>${k}`).join(" ") + `</span>`,
  ].join("");
})();


// ---- connections view ----
const connState = { nodes: [], links: [], center: null, label: "" };
function connId(be, i) { return (be ? "b" : "f") + i; }
function buildEgoGraph(name) {
  const focus = modByName[name]; if (!focus) return;
  const fi = modules.indexOf(focus);
  const neighbors = new Set([...focus.imp, ...focus.rev]);
  const nodes = [{ id: connId(true, fi), label: short(focus.name), sub: focus.pkg,
    cls: focus.cls, center: true, ext: false }];
  for (const ni of neighbors) {
    const m = modules[ni];
    nodes.push({ id: connId(true, ni), label: short(m.name), sub: m.pkg,
      cls: m.cls, center: false, ext: false });
  }
  // edge direction: importer -> dependency
  const links = [];
  for (const ni of focus.imp) links.push({ s: connId(true, fi), t: connId(true, ni) });
  for (const ni of focus.rev) links.push({ s: connId(true, ni), t: connId(true, fi) });
  connState.nodes = nodes; connState.links = links;
  connState.center = connId(true, fi);
  connState.label = `<code>${focus.name}</code> — ${focus.imp.length} imports · ${focus.rev.length} imported-by`;
  runForce(); renderConn();
}
function buildFeEgoGraph(path) {
  const focus = feByPath[path]; if (!focus) return;
  const fi = feFiles.indexOf(focus);
  const nodes = [{ id: connId(false, fi), label: focus.path.replace("frontend/src/", ""),
    sub: "fe:" + focus.group, cls: "frontend", center: true, ext: false }];
  const seen = new Set([fi]);
  for (const ni of [...focus.imp, ...focus.rev]) {
    if (seen.has(ni)) continue; seen.add(ni);
    const f = feFiles[ni];
    nodes.push({ id: connId(false, ni), label: f.path.replace("frontend/src/", ""),
      sub: "fe:" + f.group, cls: "frontend", center: false, ext: false });
  }
  const links = [];
  for (const ni of focus.imp) links.push({ s: connId(false, fi), t: connId(false, ni) });
  for (const ni of focus.rev) links.push({ s: connId(false, ni), t: connId(false, fi) });
  connState.nodes = nodes; connState.links = links;
  connState.center = connId(false, fi);
  connState.label = `<code>${focus.path}</code> — ${focus.imp.length} imports · ${focus.rev.length} imported-by`;
  runForce(); renderConn();
}
function buildPackageModules(pkgId) {
  const isFe = pkgId.startsWith("fe:");
  const group = pkgId.slice(3);
  const memberIdx = new Set(), extIdx = new Set();
  const nodes = [], links = [];
  if (isFe) {
    feFiles.forEach((f, i) => { if ("fe:" + f.group === pkgId) memberIdx.add(i); });
    memberIdx.forEach(i => feFiles[i].imp.concat(feFiles[i].rev).forEach(j => {
      if (!memberIdx.has(j)) extIdx.add(j); }));
    [...memberIdx].forEach(i => nodes.push(feNode(i, false)));
    [...extIdx].forEach(i => nodes.push(feNode(i, true)));
    memberIdx.forEach(i => feFiles[i].imp.forEach(j =>
      links.push({ s: connId(false, i), t: connId(false, j) })));
  } else {
    modules.forEach((m, i) => { if (m.pkg === pkgId) memberIdx.add(i); });
    memberIdx.forEach(i => modules[i].imp.concat(modules[i].rev).forEach(j => {
      if (!memberIdx.has(j)) extIdx.add(j); }));
    [...memberIdx].forEach(i => nodes.push(beNode(i, false)));
    [...extIdx].forEach(i => nodes.push(beNode(i, true)));
    memberIdx.forEach(i => modules[i].imp.forEach(j =>
      links.push({ s: connId(true, i), t: connId(true, j) })));
  }
  connState.nodes = nodes; connState.links = links;
  connState.center = null;
  connState.label = `<code>${pkgId}</code> — ${memberIdx.size} internal modules · ${extIdx.size} direct external neighbors`;
  runForce(); renderConn();
}
function beNode(i, ext) {
  const m = modules[i];
  return { id: connId(true, i), label: short(m.name), sub: m.pkg, cls: m.cls,
    center: false, ext, orbit: ext };
}
function feNode(i, ext) {
  const f = feFiles[i];
  return { id: connId(false, i), label: f.path.replace("frontend/src/", ""), sub: "fe:" + f.group,
    cls: "frontend", center: false, ext, orbit: ext };
}
function runForce() {
  const nodes = connState.nodes, links = connState.links;
  if (!nodes.length) return;
  const idx = Object.fromEntries(nodes.map((nd, i) => [nd.id, i]));
  // Orbit nodes (external neighbors in package mode) stay out of the
  // simulation; they are placed on a circle around the internal core.
  const movers = nodes.filter(nd => !nd.orbit);
  const orbits = nodes.filter(nd => nd.orbit);
  const internalLinks = links.filter(l =>
    l.s in idx && l.t in idx && !nodes[idx[l.s]].orbit && !nodes[idx[l.t]].orbit);
  const n = movers.length;
  movers.forEach((nd, i) => {
    const a = i / Math.max(1, n) * 2 * Math.PI;
    nd.x = Math.cos(a) * (120 + n * 5); nd.y = Math.sin(a) * (120 + n * 5);
    nd.vx = 0; nd.vy = 0;
  });
  const midx = Object.fromEntries(movers.map((nd, i) => [nd.id, i]));
  const iters = n > 120 ? 220 : 340;
  for (let it = 0; it < iters; it++) {
    for (let i = 0; i < n; i++) for (let j = i + 1; j < n; j++) {
      const a = movers[i], b = movers[j];
      let dx = b.x - a.x, dy = b.y - a.y;
      let d2 = dx * dx + dy * dy || 0.01;
      const d = Math.sqrt(d2), f = 16000 / d2;
      dx = dx / d * f; dy = dy / d * f;
      a.vx -= dx; a.vy -= dy; b.vx += dx; b.vy += dy;
    }
    for (const l of internalLinks) {
      const a = movers[midx[l.s]], b = movers[midx[l.t]];
      let dx = b.x - a.x, dy = b.y - a.y;
      const d = Math.hypot(dx, dy) || 0.01;
      const f = (d - 165) * 0.015;
      dx = dx / d * f; dy = dy / d * f;
      a.vx += dx; a.vy += dy; b.vx -= dx; b.vy -= dy;
    }
    for (const nd of movers) { nd.vx -= nd.x * 0.008; nd.vy -= nd.y * 0.008; }
    for (const nd of movers) { nd.x += nd.vx * 0.5; nd.y += nd.vy * 0.5; nd.vx *= 0.55; nd.vy *= 0.55; }
  }
  if (orbits.length) {
    // Place external neighbors on a circle, ordered by the average angle of
    // the internal modules they connect to (reduces edge crossings).
    const cx = movers.reduce((s, nd) => s + nd.x, 0) / Math.max(1, n);
    const cy = movers.reduce((s, nd) => s + nd.y, 0) / Math.max(1, n);
    let radius = 260;
    for (const nd of movers) {
      radius = Math.max(radius, Math.hypot(nd.x - cx, nd.y - cy) + 150);
    }
    const neighborAngle = new Map();
    for (const nd of orbits) {
      const linked = links.filter(l => l.s === nd.id || l.t === nd.id)
        .map(l => nodes[idx[l.s === nd.id ? l.t : l.s]])
        .filter(o => o && !o.orbit);
      const angle = linked.length
        ? Math.atan2(linked.reduce((s, o) => s + (o.y - cy), 0), linked.reduce((s, o) => s + (o.x - cx), 0))
        : 0;
      neighborAngle.set(nd.id, angle);
    }
    orbits.sort((a, b) => neighborAngle.get(a.id) - neighborAngle.get(b.id));
    orbits.forEach((nd, i) => {
      const a = i / orbits.length * 2 * Math.PI;
      nd.x = cx + Math.cos(a) * radius;
      nd.y = cy + Math.sin(a) * radius;
    });
  }
}
let connView = { x: 0, y: 0, w: 1200, h: 800 };
function renderConn() {
  const wrap = document.getElementById("connwrap");
  document.getElementById("connfocus").innerHTML = connState.label;
  wrap.innerHTML = "";
  if (!connState.nodes.length) return;
  const xs = connState.nodes.map(n => n.x), ys = connState.nodes.map(n => n.y);
  const pad = 90;
  const minX = Math.min(...xs) - pad, maxX = Math.max(...xs) + pad;
  const minY = Math.min(...ys) - pad, maxY = Math.max(...ys) + pad;
  connView = { x: minX, y: minY, w: Math.max(300, maxX - minX), h: Math.max(300, maxY - minY) };
  const svg = document.createElementNS(NS, "svg");
  const defs = document.createElementNS(NS, "defs");
  defs.innerHTML = STAGE_DEFS + `<marker id="carr" viewBox="0 0 8 8" refX="14" refY="4"
    markerWidth="6" markerHeight="6" orient="auto-start-reverse">
    <path d="M0,0 L8,4 L0,8 z" fill="#7787a3"/></marker>`;
  svg.appendChild(defs);
  svg.appendChild(stageBackdrop());
  const edgeEls = new Map();
  for (const l of connState.links) {
    const line = document.createElementNS(NS, "line");
    line.setAttribute("class", "cedge");
    line.setAttribute("marker-end", "url(#carr)");
    line.dataset.s = l.s; line.dataset.t = l.t;
    svg.appendChild(line); edgeEls.set(l, line);
  }
  const byId = Object.fromEntries(connState.nodes.map(nd => [nd.id, nd]));
  const placeEdges = () => {
    for (const [l, el] of edgeEls) {
      const a = byId[l.s], b = byId[l.t];
      if (!a || !b) { el.remove(); continue; }
      el.setAttribute("x1", a.x); el.setAttribute("y1", a.y);
      el.setAttribute("x2", b.x); el.setAttribute("y2", b.y);
    }
  };
  for (const nd of connState.nodes) {
    const g = document.createElementNS(NS, "g");
    g.setAttribute("class", "cnode" + (nd.ext ? " ext" : "") + (nd.center ? " center" : ""));
    const color = nd.cls === "frontend" ? "#b79cff" : (CLS_COLOR[nd.cls] || "#6cb6ff");
    const r = nd.center ? 15 : (nd.ext ? 5 : 10);
    const title = document.createElementNS(NS, "title");
    title.textContent = `${nd.label} (${nd.sub})`;
    g.appendChild(title);
    g.innerHTML += `<circle r="${Math.max(r + 14, 24)}" fill="transparent"></circle>` +
      `<circle r="${r}" style="fill:${color}22;stroke:${color}"></circle>` +
      (nd.ext ? "" : `<text y="${nd.center ? 30 : 24}">${esc(nd.label.length > 34 ? "…" + nd.label.slice(-33) : nd.label)}</text>
      <text class="csub" y="${nd.center ? 41 : 35}">${esc(nd.sub)}</text>`);
    g.dataset.id = nd.id;
    g.onclick = () => {
      if (nd._dragged) { nd._dragged = false; return; }
      const id = g.dataset.id;
      if (id.startsWith("b")) { buildEgoGraph(modules[+id.slice(1)].name); }
      else { buildFeEgoGraph(feFiles[+id.slice(1)].path); }
    };
    // node dragging (suppresses the recenter click after a real drag)
    g.addEventListener("pointerdown", e => {
      e.stopPropagation(); e.preventDefault();
      g.setPointerCapture(e.pointerId);
      const origin = { x: e.clientX, y: e.clientY };
      const move = ev => {
        if (Math.hypot(ev.clientX - origin.x, ev.clientY - origin.y) > 4) nd._dragged = true;
        const pt = svgPoint(svg, ev);
        nd.x = pt.x; nd.y = pt.y;
        g.setAttribute("transform", `translate(${nd.x},${nd.y})`);
        placeEdges();
      };
      const up = () => {
        g.removeEventListener("pointermove", move);
        g.removeEventListener("pointerup", up);
      };
      g.addEventListener("pointermove", move);
      g.addEventListener("pointerup", up);
    });
    nd._el = g;
    svg.appendChild(g);
  }
  const placeNodes = () => connState.nodes.forEach(nd =>
    nd._el.setAttribute("transform", `translate(${nd.x},${nd.y})`));
  placeNodes(); placeEdges();
  const applyView = () => svg.setAttribute("viewBox",
    `${connView.x} ${connView.y} ${connView.w} ${connView.h}`);
  applyView();
  svg.addEventListener("wheel", e => {
    e.preventDefault();
    const s = e.deltaY > 0 ? 1.18 : 1 / 1.18;
    const pt = svgPoint(svg, e);
    connView.x = pt.x - (pt.x - connView.x) * s;
    connView.y = pt.y - (pt.y - connView.y) * s;
    connView.w *= s; connView.h *= s;
    applyView();
  }, { passive: false });
  svg.addEventListener("pointerdown", e => {
    svg.setPointerCapture(e.pointerId);
    const start = { x: e.clientX, y: e.clientY, vx: connView.x, vy: connView.y };
    const rect = svg.getBoundingClientRect();
    const move = ev => {
      connView.x = start.vx - (ev.clientX - start.x) / rect.width * connView.w;
      connView.y = start.vy - (ev.clientY - start.y) / rect.height * connView.h;
      applyView();
    };
    const up = () => {
      svg.removeEventListener("pointermove", move);
      svg.removeEventListener("pointerup", up);
    };
    svg.addEventListener("pointermove", move);
    svg.addEventListener("pointerup", up);
  });
  wrap.appendChild(svg);
}
function svgPoint(svg, e) {
  const rect = svg.getBoundingClientRect();
  return {
    x: connView.x + (e.clientX - rect.left) / rect.width * connView.w,
    y: connView.y + (e.clientY - rect.top) / rect.height * connView.h,
  };
}
document.getElementById("connfit").onclick = () => renderConn();
(function fillJump() {
  const dl = document.getElementById("modnames");
  modules.forEach(m => dl.appendChild(new Option(m.name)));
  feFiles.forEach(f => dl.appendChild(new Option(f.path)));
})();
document.getElementById("connjump").onchange = e => {
  const v = e.target.value.trim();
  if (modByName[v]) buildEgoGraph(v);
  else if (feByPath[v]) buildFeEgoGraph(v);
  e.target.value = "";
  e.target.blur();
};

// ---- module explorer ----
const state = { q: "", pkg: "", cls: "", src: "", sort: "lines", dir: -1 };
const pkgSel = document.getElementById("fpkg");
[...new Set(modules.map(m => m.pkg))].sort().forEach(p =>
  pkgSel.add(new Option(p, p)));
[...new Set(modules.map(m => m.cls))].sort().forEach(c =>
  document.getElementById("fcls").add(new Option(c, c)));
document.getElementById("q").oninput = e => { state.q = e.target.value.toLowerCase(); renderRows(); };
pkgSel.onchange = e => { state.pkg = e.target.value; renderRows(); };
document.getElementById("fcls").onchange = e => { state.cls = e.target.value; renderRows(); };
document.getElementById("fsrc").onchange = e => { state.src = e.target.value; renderRows(); };
document.querySelectorAll("th[data-k]").forEach(th => th.onclick = () => {
  const k = th.dataset.k;
  state.dir = state.sort === k ? -state.dir : (k === "name" || k === "pkg" || k === "cls" ? 1 : -1);
  state.sort = k; renderRows();
});
function rowValue(m, k) {
  if (k === "imp") return m.imp.length;
  if (k === "rev") return m.rev.length;
  return m[k];
}
function renderRows() {
  let list = [];
  if (state.src !== "frontend") list = list.concat(modules.map(m => ({...m, be: true})));
  if (state.src !== "backend") list = list.concat(feFiles.map(f => ({
    name: f.path, path: f.path, pkg: "fe:" + f.group, cls: "frontend",
    lines: f.lines, doc: f.doc, imp: f.imp, rev: f.rev, be: false })));
  if (state.q) list = list.filter(m =>
    m.name.toLowerCase().includes(state.q) || (m.doc||"").toLowerCase().includes(state.q));
  if (state.pkg) list = list.filter(m => m.pkg === state.pkg);
  if (state.cls) list = list.filter(m => m.cls === state.cls);
  list.sort((a,b) => {
    const va = rowValue(a, state.sort), vb = rowValue(b, state.sort);
    return (va < vb ? -1 : va > vb ? 1 : 0) * state.dir;
  });
  const cap = 400;
  document.getElementById("count").textContent =
    `${list.length} modules${list.length > cap ? ` (showing first ${cap}; refine filters)` : ""}`;
  document.getElementById("rows").innerHTML = list.slice(0, cap).map(m =>
    `<tr data-mod="${m.be ? m.name : ""}" data-fe="${m.be ? "" : m.path}">
      <td><code>${esc(short(m.name))}</code></td><td>${m.pkg}</td>
      <td><span class="badge ${m.cls}">${m.cls}</span></td>
      <td class="num">${m.lines.toLocaleString()}</td>
      <td class="num">${m.imp.length}</td><td class="num">${m.rev.length}</td>
      <td style="color:var(--dim)">${esc(m.doc || "")}</td></tr>`).join("");
}

// ---- detail drawer ----
const drawer = document.getElementById("drawer");
document.getElementById("closex").onclick = () => drawer.classList.remove("open");
document.addEventListener("keydown", e => { if (e.key === "Escape") drawer.classList.remove("open"); });
document.body.addEventListener("click", e => {
  // Health / flow-strip shortcuts: open the module drawer, or focus a package
  // in the dependency graph.
  const jumpMod = e.target.closest("[data-jumpmod]");
  if (jumpMod) { showModule(jumpMod.dataset.jumpmod); return; }
  const jumpPkg = e.target.closest("[data-jumppkg]");
  if (jumpPkg) { switchView("graph"); selectPackage(jumpPkg.dataset.jumppkg, false); return; }
  const viewJump = e.target.closest("[data-view-jump]");
  if (viewJump) { switchView(viewJump.dataset.viewJump); return; }
  const connMod = e.target.closest("[data-conn-mod]");
  if (connMod) { switchView("connections"); buildEgoGraph(connMod.dataset.connMod); return; }
  const connFe = e.target.closest("[data-conn-fe]");
  if (connFe) { switchView("connections"); buildFeEgoGraph(connFe.dataset.connFe); return; }
  const connPkg = e.target.closest("[data-conn-pkg]");
  if (connPkg) { switchView("connections"); buildPackageModules(connPkg.dataset.connPkg); return; }
  const fePath = e.target.closest("[data-fe-path]");
  if (fePath) { showFrontend(fePath.dataset.fePath); return; }
  const tr = e.target.closest("tr[data-mod], tr[data-fe]");
  if (tr) { tr.dataset.mod ? showModule(tr.dataset.mod) : showFrontend(tr.dataset.fe); return; }
  const card = e.target.closest(".card[data-pkg]");
  if (card) { selectPackage(card.dataset.pkg, false); return; }
  const hub = e.target.closest(".card[data-mod]");
  if (hub) showModule(hub.dataset.mod);
  const chip = e.target.closest(".chip[data-mod]");
  if (chip) showModule(chip.dataset.mod);
});
function selectPackage(pkgId, fromGraph) {
  const p = pkgById[pkgId]; if (!p) return;
  highlightEdges(pkgId);
  const deps = D.package_edges.filter(e => e.from === pkgId);
  const users = D.package_edges.filter(e => e.to === pkgId);
  const members = modules.filter(m => m.pkg === pkgId);
  const feMembers = feFiles.filter(f => "fe:" + f.group === pkgId);
  openDrawer(`<h2><code>${p.id}</code></h2>
    <div class="doc">${esc(p.doc) || "(no description)"}</div>
    <dl class="kv"><dt>files</dt><dd>${p.files}</dd>
      <dt>lines</dt><dd>${p.lines.toLocaleString()}</dd>
      <dt>classes</dt><dd>${Object.entries(p.classes).map(([c,n])=>c+" "+n).join(", ")||"-"}</dd>
      ${Object.keys(p.domains).length ? `<dt>domains</dt><dd>${Object.entries(p.domains).map(([d,n])=>d+" "+n).join(", ")}</dd>` : ""}</dl>
    <div class="actions"><button class="actionbtn" data-conn-pkg="${p.id}">내부 모듈 연계 그래프</button></div>
    <h4>Imports (module-level count)</h4><div class="chips">${deps.map(e =>
      `<span class="chip" data-pkglink="${e.to}">${e.to} (${e.count})</span>`).join("")||"<span class='note'>none</span>"}</div>
    <h4>Imported by</h4><div class="chips">${users.map(e =>
      `<span class="chip" data-pkglink="${e.from}">${e.from} (${e.count})</span>`).join("")||"<span class='note'>none</span>"}</div>
    <h4>Largest modules</h4><div class="chips">${members.sort((a,b)=>b.lines-a.lines).slice(0,14)
      .map(m => `<span class="chip" data-mod="${m.name}">${short(m.name)} · ${m.lines}</span>`).join("")}
      ${feMembers.sort((a,b)=>b.lines-a.lines).slice(0,14)
        .map(f => `<span class="chip">${f.path.split("/").pop()} · ${f.lines}</span>`).join("")}</div>`);
  drawer.querySelectorAll("[data-pkglink]").forEach(c =>
    c.onclick = () => selectPackage(c.dataset.pkglink, true));
}
function classSection(m) {
  if (!m.classes || !m.classes.length) return "";
  const rows = m.classes.slice(0, 14).map(([cn, bases]) => {
    const baseHtml = bases.length ? " &larr; " + bases.map(([b, mod]) =>
      mod ? `<span class="chip" data-mod="${mod}" title="${mod}">${esc(b)}</span>`
          : `<span class="base">${esc(b)}</span>`).join(" ") : "";
    return `<div class="clsrow"><code>${esc(cn)}</code>${baseHtml}</div>`;
  }).join("");
  const more = m.classes.length > 14 ? `<div class="note">+${m.classes.length - 14} more classes</div>` : "";
  return `<h4>Classes &amp; inheritance (${m.classes.length})</h4>${rows}${more}`;
}
function showModule(name) {
  const m = modules.find(x => x.name === name); if (!m) return;
  openDrawer(`<h2><code>${m.name}</code></h2><div class="path">${m.path}</div>
    <div class="doc">${esc(m.doc) || "(no module docstring)"}</div>
    <dl class="kv"><dt>package</dt><dd>${m.pkg}</dd><dt>domain</dt><dd>${m.domain}</dd>
      <dt>class</dt><dd>${m.cls}</dd><dt>migration</dt><dd>${m.mig}</dd>
      <dt>lines</dt><dd>${m.lines.toLocaleString()}</dd></dl>
    <div class="actions"><button class="actionbtn" data-conn-mod="${m.name}">연계 그래프</button></div>
    ${classSection(m)}
    <h4>Imports (${m.imp.length})</h4><div class="chips">${m.imp.map(i =>
      `<span class="chip" data-mod="${modules[i].name}">${short(modules[i].name)}</span>`).join("")||"<span class='note'>none</span>"}</div>
    <h4>Imported by (${m.rev.length})</h4><div class="chips">${m.rev.map(i =>
      `<span class="chip" data-mod="${modules[i].name}">${short(modules[i].name)}</span>`).join("")||"<span class='note'>none</span>"}</div>`);
}
function showFrontend(path) {
  const f = feFiles.find(x => x.path === path); if (!f) return;
  openDrawer(`<h2><code>${f.path}</code></h2>
    <div class="doc">${esc(f.doc) || "(no file comment)"}</div>
    <dl class="kv"><dt>group</dt><dd>fe:${f.group}</dd><dt>lines</dt><dd>${f.lines}</dd></dl>
    <div class="actions"><button class="actionbtn" data-conn-fe="${f.path}">연계 그래프</button></div>
    <h4>Imports (${f.imp.length})</h4><div class="chips">${f.imp.map(i =>
      `<span class="chip" data-fe-path="${feFiles[i].path}">${feFiles[i].path.replace("frontend/src/","")}</span>`).join("")||"<span class='note'>none</span>"}</div>
    <h4>Imported by (${f.rev.length})</h4><div class="chips">${f.rev.map(i =>
      `<span class="chip" data-fe-path="${feFiles[i].path}">${feFiles[i].path.replace("frontend/src/","")}</span>`).join("")||"<span class='note'>none</span>"}</div>`);
}
function openDrawer(html) {
  document.getElementById("drawerbody").innerHTML = html;
  drawer.classList.add("open");
}
function esc(s) { return String(s ?? "").replace(/[&<>"]/g, c =>
  ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])); }
function short(name) { return name.replace(/^agentsassemble\\./, ""); }
renderRows();
</script>
</body>
</html>
"""


def render_html(data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    payload = payload.replace("</", "<\\/")
    return HTML_TEMPLATE.replace("__DATA__", payload)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="fail if the generated outputs are stale")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    data = build_map(root)
    if args.check:
        # generated_at changes every run, and the repo-area trivia (docs/tests
        # line counts) shifts on any documentation edit. Keep the recorded
        # values so the staleness gate reacts to structural code changes only.
        try:
            existing = json.loads((root / JSON_RELATIVE_PATH).read_text(encoding="utf-8"))
            data["generated_at"] = existing["generated_at"]
            data["repo"] = existing["repo"]
        except (OSError, ValueError, KeyError):
            pass
    outputs = {
        root / JSON_RELATIVE_PATH: json.dumps(data, ensure_ascii=False, indent=1) + "\n",
        root / HTML_RELATIVE_PATH: render_html(data),
    }
    if args.check:
        stale = [
            path.relative_to(root)
            for path, content in outputs.items()
            if not path.exists() or path.read_text(encoding="utf-8") != content
        ]
        if stale:
            print("Codebase map is stale: " + ", ".join(str(p) for p in stale))
            return 1
        return 0
    for path, content in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        changed = not path.exists() or path.read_text(encoding="utf-8") != content
        path.write_text(content, encoding="utf-8")
        print(f"{'Updated' if changed else 'Unchanged'} {path.relative_to(root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
