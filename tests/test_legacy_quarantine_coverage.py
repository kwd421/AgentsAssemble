from __future__ import annotations

import ast
from pathlib import Path

from agentsassemble import legacy_runtime


def _legacy_registrar_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        module = node.module or ""
        if not module.startswith("agentsassemble.legacy"):
            continue
        for alias in node.names:
            if alias.name.startswith("register_"):
                imported.add(f"{module}.{alias.name}")
    return imported


def test_all_legacy_registrars_imported_by_default_entrypoints_are_quarantined() -> None:
    root = Path(__file__).resolve().parents[1]
    imported = set()
    for relative_path in ("agentsassemble/cli.py", "agentsassemble/gui.py"):
        imported.update(_legacy_registrar_imports(root / relative_path))

    quarantined = {
        f"{module_name}.{attribute_name}"
        for module_name, attribute_names in legacy_runtime._QUARANTINED_REGISTRARS.items()
        for attribute_name in attribute_names
    }

    assert imported <= quarantined, (
        "New legacy registrar imports must be added to the default-deny "
        f"quarantine: {sorted(imported - quarantined)}"
    )
