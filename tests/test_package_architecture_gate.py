from __future__ import annotations

import unittest
from pathlib import Path

from scripts.check_package_architecture import (
    CYCLE_REPORT_RELATIVE_PATH,
    ROOT_COMPATIBILITY_SHIMS,
    current_top_level_modules,
    dependency_direction_violations,
    import_cycles,
    load_cycle_baseline,
    load_root_baseline,
    new_import_cycles,
    render_cycle_report,
    unexpected_top_level_modules,
    validate_compatibility_shims,
)
from scripts.generate_package_map import ModuleSource, PackageGraph, load_package_graph


ROOT = Path(__file__).resolve().parents[1]


class PackageArchitectureGateTests(unittest.TestCase):
    def test_current_root_has_no_unowned_product_modules(self) -> None:
        unexpected = unexpected_top_level_modules(
            current_top_level_modules(ROOT),
            load_root_baseline(ROOT),
        )

        self.assertEqual(unexpected, ())

    def test_gate_rejects_a_synthetic_new_flat_module(self) -> None:
        unexpected = unexpected_top_level_modules(
            {"__init__.py", "cli.py", "gui.py", "new_product_module.py"},
            set(),
        )

        self.assertEqual(unexpected, ("new_product_module.py",))

    def test_root_compatibility_shims_have_replacement_and_removal_metadata(self) -> None:
        validate_compatibility_shims()

        self.assertTrue(
            set(ROOT_COMPATIBILITY_SHIMS).issubset(current_top_level_modules(ROOT))
        )
        graph = load_package_graph(ROOT)
        for filename, shim in ROOT_COMPATIBILITY_SHIMS.items():
            with self.subTest(filename=filename):
                self.assertIn(shim.replacement_import, graph.modules)
                for caller in shim.known_callers:
                    self.assertTrue((ROOT / caller).is_file(), caller)

    def test_package_map_tracks_every_current_top_level_module(self) -> None:
        package_map = (ROOT / "docs" / "product" / "PACKAGE_MAP.md").read_text(
            encoding="utf-8"
        )

        for filename in current_top_level_modules(ROOT):
            with self.subTest(filename=filename):
                self.assertIn(f"`agentsassemble/{filename}`", package_map)

    def test_current_dependencies_follow_migrated_package_rules(self) -> None:
        self.assertEqual(
            dependency_direction_violations(load_package_graph(ROOT)),
            (),
        )

    def test_direction_rules_reject_core_to_legacy_web_and_concrete_storage(self) -> None:
        graph = _synthetic_graph(
            {
                "agentsassemble.room.service": (
                    "agentsassemble.legacy.meeting.http.room_composition",
                    "agentsassemble.legacy_live_agent",
                ),
                "agentsassemble.web.routes": (
                    "agentsassemble.postgres_room_repository",
                ),
                "agentsassemble.application.compose": (
                    "agentsassemble.postgres_room_repository",
                ),
            },
            domains={
                "agentsassemble.room.service": "room",
                "agentsassemble.web.routes": "web",
                "agentsassemble.application.compose": "application",
                "agentsassemble.legacy.meeting.http.room_composition": "web",
                "agentsassemble.legacy_live_agent": "legacy",
                "agentsassemble.postgres_room_repository": "persistence",
            },
            classifications={
                "agentsassemble.legacy_live_agent": "legacy",
            },
        )

        violations = dependency_direction_violations(graph)

        self.assertIn(
            "agentsassemble.room.service imports web module agentsassemble.legacy.meeting.http.room_composition",
            violations,
        )
        self.assertIn(
            "agentsassemble.room.service imports legacy module agentsassemble.legacy_live_agent",
            violations,
        )
        self.assertIn(
            "agentsassemble.web.routes imports concrete persistence module "
            "agentsassemble.postgres_room_repository",
            violations,
        )
        self.assertFalse(
            any("agentsassemble.application.compose" in violation for violation in violations)
        )

    def test_current_import_cycles_are_exactly_grandfathered(self) -> None:
        graph = load_package_graph(ROOT)
        cycles = import_cycles(graph.imports_by_module)

        self.assertEqual(new_import_cycles(cycles, load_cycle_baseline(ROOT)), ())

    def test_new_cycle_is_not_grandfathered(self) -> None:
        cycles = import_cycles(
            {
                "agentsassemble.a": ("agentsassemble.b",),
                "agentsassemble.b": ("agentsassemble.a",),
            }
        )

        self.assertEqual(
            new_import_cycles(cycles, ()),
            (("agentsassemble.a", "agentsassemble.b"),),
        )

    def test_committed_cycle_report_matches_current_graph(self) -> None:
        expected = render_cycle_report(
            load_package_graph(ROOT),
            load_cycle_baseline(ROOT),
        )
        actual = (ROOT / CYCLE_REPORT_RELATIVE_PATH).read_text(encoding="utf-8")

        self.assertEqual(actual, expected)


def _synthetic_graph(
    imports: dict[str, tuple[str, ...]],
    *,
    domains: dict[str, str],
    classifications: dict[str, str],
) -> PackageGraph:
    module_names = set(imports)
    module_names.update(name for values in imports.values() for name in values)
    modules = {}
    for name in module_names:
        package_root = name.split(".")[1]
        suffix = name.split(".")[-1]
        relative_path = (
            f"agentsassemble/{package_root}/{suffix}.py"
            if package_root in {"application", "web", "room"}
            else f"agentsassemble/{suffix}.py"
        )
        modules[name] = ModuleSource(
            name=name,
            path=ROOT / relative_path,
            relative_path=relative_path,
            source="",
            tree=__import__("ast").parse(""),
            is_package=False,
        )
    return PackageGraph(
        modules=modules,
        imports_by_module={name: imports.get(name, ()) for name in module_names},
        domains={name: domains.get(name, "application") for name in module_names},
        classifications={
            name: classifications.get(name, "current")
            for name in module_names
        },
    )


if __name__ == "__main__":
    unittest.main()
