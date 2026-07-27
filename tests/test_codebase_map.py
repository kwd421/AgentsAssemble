from __future__ import annotations

import json
import re
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.generate_codebase_map import (
    HTML_RELATIVE_PATH,
    JSON_RELATIVE_PATH,
    build_map,
    generated_outputs,
    load_existing_maps,
    prepare_generated_map,
    render_html,
    stale_generated_outputs,
    write_generated_outputs,
)


ROOT = Path(__file__).resolve().parents[1]


class CodebaseMapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.data = build_map(ROOT)
        cls.data = prepare_generated_map(cls.data, load_existing_maps(ROOT))

    def test_committed_codebase_map_matches_source_tree(self) -> None:
        expected_json = json.dumps(self.data, ensure_ascii=False, indent=1) + "\n"
        expected_html = render_html(self.data)

        actual_json = (ROOT / "docs" / "product" / "CODEBASE_MAP.json").read_text(
            encoding="utf-8"
        )
        actual_html = (ROOT / "docs" / "product" / "CODEBASE_MAP.html").read_text(
            encoding="utf-8"
        )

        self.assertEqual(actual_json, expected_json)
        self.assertEqual(actual_html, expected_html)

    def test_map_covers_every_backend_module_once(self) -> None:
        from scripts.generate_package_map import load_package_graph

        graph = load_package_graph(ROOT)
        mapped = [row[0] for row in self.data["modules"]]

        self.assertEqual(sorted(mapped), sorted(graph.modules))
        self.assertEqual(
            self.data["stats"]["backend_lines"],
            sum(len(m.source.splitlines()) for m in graph.modules.values()),
        )

    def test_package_graph_edges_and_layout_are_well_formed(self) -> None:
        package_ids = {package["id"] for package in self.data["packages"]}

        self.assertTrue(package_ids)
        for package in self.data["packages"]:
            with self.subTest(package=package["id"]):
                self.assertIn("x", package)
                self.assertIn("y", package)
                self.assertTrue(package["doc"])
        for edge in self.data["package_edges"]:
            self.assertIn(edge["from"], package_ids)
            self.assertIn(edge["to"], package_ids)
            self.assertNotEqual(edge["from"], edge["to"])
            self.assertGreater(edge["count"], 0)

    def test_class_inheritance_bases_resolve_to_known_modules(self) -> None:
        names = {row[0] for row in self.data["modules"]}
        resolved = 0
        for row in self.data["modules"]:
            for _class_name, bases in row[10]:
                for _base, defining_module in bases:
                    if defining_module:
                        self.assertIn(defining_module, names)
                        resolved += 1
        # Guard against the resolver silently going blind (e.g. import change).
        self.assertGreater(resolved, 10)
        repository_row = next(
            row for row in self.data["modules"]
            if row[0] == "agentsassemble.admission.repository"
        )
        invite_session = next(
            entry for entry in repository_row[10]
            if entry[0] == "InviteSessionRepository"
        )
        resolved = dict((base, defining) for base, defining in invite_session[1])
        self.assertEqual(
            resolved["InviteRepository"], "agentsassemble.admission.repository"
        )
        self.assertEqual(
            resolved["SessionRepository"], "agentsassemble.admission.repository"
        )
        # External bases (typing.Protocol) stay deliberately unresolved.
        self.assertEqual(resolved["Protocol"], "")

    def test_package_layout_is_inside_the_canvas_and_never_overlaps(self) -> None:
        graph = self.data["graph"]
        boxes = [
            (p["id"], p["x"], p["y"], p["w"], p["h"]) for p in self.data["packages"]
        ]

        for name, x, y, w, h in boxes:
            with self.subTest(package=name):
                self.assertGreaterEqual(x, 0)
                self.assertGreaterEqual(y, 0)
                self.assertLessEqual(x + w, graph["width"])
                self.assertLessEqual(y + h, graph["height"])
        for index, (left_name, lx, ly, lw, lh) in enumerate(boxes):
            for right_name, rx, ry, rw, rh in boxes[index + 1 :]:
                overlaps = lx < rx + rw and rx < lx + lw and ly < ry + rh and ry < ly + lh
                self.assertFalse(
                    overlaps, f"{left_name} overlaps {right_name}"
                )

    def test_routed_edges_stay_on_canvas(self) -> None:
        graph = self.data["graph"]

        for edge in self.data["package_edges"]:
            with self.subTest(edge=(edge["from"], edge["to"])):
                self.assertGreaterEqual(len(edge["points"]), 2)
                for x, y in edge["points"]:
                    self.assertGreaterEqual(x, 0)
                    self.assertGreaterEqual(y, 0)
                    self.assertLessEqual(x, graph["width"])
                    self.assertLessEqual(y, graph["height"])

    def test_cycle_groups_contain_their_members_and_flag_internal_imports(self) -> None:
        boxes = {p["id"]: p for p in self.data["packages"]}
        group_of: dict[str, int] = {}

        for index, cluster in enumerate(self.data["graph"]["clusters"]):
            self.assertGreater(len(cluster["members"]), 1)
            self.assertGreater(cluster["internal_edges"], 0)
            # The label strip lives inside the container, above the members.
            self.assertGreater(cluster["label_y"], cluster["y"])
            for member in cluster["members"]:
                group_of[member] = index
                box = boxes[member]
                self.assertGreaterEqual(box["x"], cluster["x"])
                self.assertGreaterEqual(box["y"], cluster["label_y"])
                self.assertLessEqual(box["x"] + box["w"], cluster["x"] + cluster["w"])
                self.assertLessEqual(box["y"] + box["h"], cluster["y"] + cluster["h"])

        counted = 0
        for edge in self.data["package_edges"]:
            same_group = (
                edge["from"] in group_of
                and group_of[edge["from"]] == group_of.get(edge["to"])
            )
            self.assertEqual(edge["intra_cycle"], same_group)
            counted += same_group
        self.assertEqual(
            counted,
            sum(c["internal_edges"] for c in self.data["graph"]["clusters"]),
        )

    def test_class_hierarchy_draws_subclasses_below_their_base(self) -> None:
        class_graph = self.data["class_graph"]
        nodes = {node["id"]: node for node in class_graph["nodes"]}

        self.assertTrue(class_graph["edges"])
        for edge in class_graph["edges"]:
            with self.subTest(edge=(edge["from"], edge["to"])):
                base, subclass = nodes[edge["from"]], nodes[edge["to"]]
                # Edges are emitted base -> subclass so the renderer can put the
                # UML hollow triangle on the base end via marker-start.
                self.assertLess(base["layer"], subclass["layer"])
                self.assertLess(base["y"], subclass["y"])
                self.assertGreaterEqual(len(edge["points"]), 2)
        for node in class_graph["nodes"]:
            self.assertIn("::", node["id"])
            self.assertGreaterEqual(node["x"], 0)
            self.assertLessEqual(node["x"] + node["w"], class_graph["width"])

    def test_html_renders_every_view_the_nav_offers(self) -> None:
        html = render_html(self.data)

        for view in ("overview", "health", "graph", "classes", "connections", "modules"):
            with self.subTest(view=view):
                self.assertIn(f'data-view="{view}"', html)
                self.assertIn(f'id="{view}"', html)
        # The directory tree duplicated the file explorer and the Module Explorer
        # already lists frontend files, so it was removed rather than demoted.
        self.assertNotIn('data-view="tree"', html)
        self.assertNotIn("treewrap", html)
        # Fit mode is what keeps the graphs from scrolling sideways.
        self.assertIn('data-zoom="fit"', html)
        self.assertIn("marker-start", html)

    def test_graph_canvases_are_zoomable_and_pannable(self) -> None:
        html = render_html(self.data)

        # Zoom is Cmd/Ctrl + wheel; a plain wheel must stay page scrolling.
        self.assertIn("attachStage(", html)
        self.assertIn("event.metaKey", html)
        self.assertIn("event.ctrlKey", html)
        self.assertIn("pointermove", html)
        for control in ("zin", "zout", "zfit", "zone", "czin", "czout", "czfit", "czone"):
            with self.subTest(control=control):
                self.assertIn(f'id="{control}"', html)
        # Both stages must be wired, not just the package graph.
        self.assertIn("attachStage(wrap, svg, W, H, {", html)
        self.assertIn("attachStage(wrap, svg, CG.width, CG.height, {", html)

    def test_health_findings_are_derived_and_scoped(self) -> None:
        health = self.data["health"]
        cols = [
            "name", "path", "pkg", "domain", "cls", "mig",
            "lines", "doc", "imp", "rev", "classes",
        ]
        modules = {row[0]: dict(zip(cols, row)) for row in self.data["modules"]}
        retiring = {"legacy", "compatibility"}

        self.assertIn("tests/", health["scope"])
        self.assertTrue(health["cycles"])
        for cycle in health["cycles"]:
            self.assertGreater(cycle["lines"], 0)
            self.assertIn(cycle["heaviest"], cycle["members"])

        # Only current -> retiring imports may be reported as violations.
        for item in health["leaning_on_retiring"]:
            with self.subTest(module=item["module"]):
                self.assertEqual(modules[item["module"]]["cls"], "current")
                for target in item["targets"]:
                    self.assertIn(modules[target]["cls"], retiring)

        for item in health["highest_leverage_migrations"]:
            with self.subTest(module=item["module"]):
                self.assertIn(modules[item["module"]]["cls"], retiring)
                self.assertGreater(item["count"], 0)

        # A shim kept alive by the test suite must never be listed as callerless:
        # that is the difference between a useful hint and a wrong one. Match on
        # an identifier boundary, since one module name can prefix another
        # (agentsassemble.room_settings vs agentsassemble.room_settings_service).
        # This file is skipped: it discusses module names in prose, and the
        # generator reads tests with the AST, so comments are not references.
        blob = "\n".join(
            path.read_text(encoding="utf-8", errors="replace")
            for path in sorted((ROOT / "tests").rglob("*.py"))
            if path.name != Path(__file__).name
        )
        for item in health["unreferenced_shims"]:
            with self.subTest(module=item["module"]):
                self.assertEqual(modules[item["module"]]["cls"], "compatibility")
                self.assertFalse(modules[item["module"]]["rev"])
                self.assertIsNone(
                    re.search(re.escape(item["module"]) + r"(?![A-Za-z0-9_])", blob),
                    f"{item['module']} is referenced by tests/",
                )
        self.assertGreater(health["totals"]["test_only_shims"], 0)

    def test_html_is_self_contained(self) -> None:
        html = render_html(self.data)

        self.assertNotIn("<script src", html)
        self.assertNotIn("<link", html)
        self.assertNotIn('src="http', html)
        self.assertNotIn('href="http', html)
        self.assertNotIn("url(http", html)
        self.assertIn('<script id="mapdata" type="application/json">', html)


class CodebaseMapOutputTests(unittest.TestCase):
    def test_noop_generation_preserves_the_recorded_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            existing = {
                "generated_at": "2026-07-01T00:00:00Z",
                "stats": {"backend_modules": 2},
                "repo": [{"name": "agentsassemble", "lines": 20}],
            }
            fresh = {
                **existing,
                "generated_at": "2026-07-27T00:00:00Z",
            }
            write_generated_outputs(root, generated_outputs(existing))

            prepared = prepare_generated_map(fresh, load_existing_maps(root))

            self.assertEqual(prepared, existing)

    def test_real_generated_fact_change_keeps_the_fresh_timestamp(self) -> None:
        existing = {
            "generated_at": "2026-07-01T00:00:00Z",
            "stats": {"backend_modules": 2},
            "repo": [{"name": "agentsassemble", "lines": 20}],
        }
        fresh = {
            **existing,
            "generated_at": "2026-07-27T00:00:00Z",
            "repo": [{"name": "agentsassemble", "lines": 21}],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_generated_outputs(root, generated_outputs(existing))
            prepared = prepare_generated_map(fresh, load_existing_maps(root))

        self.assertEqual(prepared, fresh)

    def test_changed_html_repairs_only_html_and_preserves_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            existing = {
                "generated_at": "2026-07-01T00:00:00Z",
                "stats": {"backend_modules": 2},
            }
            fresh = {
                **existing,
                "generated_at": "2026-07-27T00:00:00Z",
            }
            write_generated_outputs(root, generated_outputs(existing))
            html_path = root / "docs" / "product" / "CODEBASE_MAP.html"
            html_path.write_bytes(b"\xff")

            outputs = generated_outputs(
                prepare_generated_map(fresh, load_existing_maps(root))
            )
            stale = stale_generated_outputs(root, outputs)
            changed = write_generated_outputs(root, outputs)

            self.assertEqual(stale, [HTML_RELATIVE_PATH])
            self.assertFalse(changed[JSON_RELATIVE_PATH])
            self.assertTrue(changed[HTML_RELATIVE_PATH])
            self.assertEqual(stale_generated_outputs(root, outputs), [])

    def test_changed_json_uses_current_html_and_repairs_only_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            existing = {
                "generated_at": "2026-07-01T00:00:00Z",
                "stats": {"backend_modules": 2},
            }
            fresh = {
                **existing,
                "generated_at": "2026-07-27T00:00:00Z",
            }
            write_generated_outputs(root, generated_outputs(existing))
            json_path = root / JSON_RELATIVE_PATH
            json_path.write_text(
                json.dumps(
                    {
                        **existing,
                        "stats": {"backend_modules": 99},
                    },
                    ensure_ascii=False,
                    indent=1,
                )
                + "\n",
                encoding="utf-8",
            )

            outputs = generated_outputs(
                prepare_generated_map(fresh, load_existing_maps(root))
            )
            stale = stale_generated_outputs(root, outputs)
            changed = write_generated_outputs(root, outputs)

            self.assertEqual(stale, [JSON_RELATIVE_PATH])
            self.assertTrue(changed[JSON_RELATIVE_PATH])
            self.assertFalse(changed[HTML_RELATIVE_PATH])
            self.assertEqual(stale_generated_outputs(root, outputs), [])

    def test_invalid_json_timestamp_repairs_only_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            existing = {
                "generated_at": "2026-07-01T00:00:00Z",
                "stats": {"backend_modules": 2},
            }
            fresh = {
                **existing,
                "generated_at": "2026-07-27T00:00:00Z",
            }
            write_generated_outputs(root, generated_outputs(existing))
            json_path = root / JSON_RELATIVE_PATH
            json_path.write_text(
                json.dumps(
                    {
                        **existing,
                        "generated_at": "corrupt-but-nonempty",
                    },
                    ensure_ascii=False,
                    indent=1,
                )
                + "\n",
                encoding="utf-8",
            )

            outputs = generated_outputs(
                prepare_generated_map(fresh, load_existing_maps(root))
            )
            stale = stale_generated_outputs(root, outputs)
            changed = write_generated_outputs(root, outputs)

            self.assertEqual(stale, [JSON_RELATIVE_PATH])
            self.assertTrue(changed[JSON_RELATIVE_PATH])
            self.assertFalse(changed[HTML_RELATIVE_PATH])
            self.assertEqual(stale_generated_outputs(root, outputs), [])

    def test_conflicting_valid_timestamps_use_the_fresh_timestamp(self) -> None:
        existing = {
            "generated_at": "2026-07-01T00:00:00Z",
            "stats": {"backend_modules": 2},
        }
        conflicting = {
            **existing,
            "generated_at": "2026-07-02T00:00:00Z",
        }
        fresh = {
            **existing,
            "generated_at": "2026-07-27T00:00:00Z",
        }

        prepared = prepare_generated_map(fresh, [existing, conflicting])

        self.assertEqual(prepared, fresh)

    def test_identical_outputs_are_not_rewritten(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            outputs = generated_outputs(
                {
                    "generated_at": "2026-07-01T00:00:00Z",
                    "stats": {"backend_modules": 2},
                }
            )
            first_write = write_generated_outputs(root, outputs)

            with patch.object(
                Path,
                "write_text",
                side_effect=AssertionError("unchanged output was rewritten"),
            ):
                second_write = write_generated_outputs(root, outputs)

            self.assertTrue(all(first_write.values()))
            self.assertFalse(any(second_write.values()))
            self.assertEqual(stale_generated_outputs(root, outputs), [])


class CodebaseMapMakeTests(unittest.TestCase):
    def test_commit_check_rejects_staged_and_unstaged_generated_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "Makefile").write_text(
                (ROOT / "Makefile").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            for relative_path in (
                Path("docs/product/PACKAGE_MAP.md"),
                JSON_RELATIVE_PATH,
                HTML_RELATIVE_PATH,
            ):
                path = root / relative_path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("baseline\n", encoding="utf-8")

            subprocess.run(["git", "init", "--quiet"], cwd=root, check=True)
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(
                [
                    "git",
                    "-c",
                    "user.name=Codebase Map Test",
                    "-c",
                    "user.email=codebase-map@example.invalid",
                    "commit",
                    "--quiet",
                    "-m",
                    "baseline",
                ],
                cwd=root,
                check=True,
            )

            clean = subprocess.run(
                ["make", "codebase-map-commit-check"],
                cwd=root,
                capture_output=True,
                text=True,
            )
            (root / JSON_RELATIVE_PATH).write_text("changed\n", encoding="utf-8")
            unstaged = subprocess.run(
                ["make", "codebase-map-commit-check"],
                cwd=root,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                ["git", "add", str(JSON_RELATIVE_PATH)],
                cwd=root,
                check=True,
            )
            staged = subprocess.run(
                ["make", "codebase-map-commit-check"],
                cwd=root,
                capture_output=True,
                text=True,
            )
            (root / JSON_RELATIVE_PATH).write_text("baseline\n", encoding="utf-8")
            head_only_check = subprocess.run(
                ["git", "diff", "--quiet", "HEAD", "--", str(JSON_RELATIVE_PATH)],
                cwd=root,
            )
            repaired_worktree = subprocess.run(
                ["make", "codebase-map-commit-check"],
                cwd=root,
                capture_output=True,
                text=True,
            )

            self.assertEqual(clean.returncode, 0, clean.stdout + clean.stderr)
            self.assertNotEqual(unstaged.returncode, 0)
            self.assertNotEqual(staged.returncode, 0)
            self.assertEqual(head_only_check.returncode, 0)
            self.assertNotEqual(repaired_worktree.returncode, 0)
            self.assertIn(
                "Generated codebase maps differ from HEAD.",
                unstaged.stdout + unstaged.stderr,
            )
            self.assertIn(
                "Generated codebase maps differ from HEAD.",
                staged.stdout + staged.stderr,
            )
            self.assertIn(
                "Generated codebase maps differ from HEAD.",
                repaired_worktree.stdout + repaired_worktree.stderr,
            )


class CodebaseMapRenderSmokeTests(unittest.TestCase):
    """Run the page's own scripts so a render-time error cannot pass unnoticed.

    Asserting on the HTML source only proves markup is present; a single bad
    field name blanks every section below it while every string check still
    passes. This executes the real document in jsdom instead.
    """

    def test_every_section_actually_renders(self) -> None:
        script = textwrap.dedent(
            """
            import fs from "node:fs/promises";
            import { JSDOM, VirtualConsole } from "./frontend/node_modules/jsdom/lib/api.js";

            const html = await fs.readFile("docs/product/CODEBASE_MAP.html", "utf8");
            const errors = [];
            const virtualConsole = new VirtualConsole();
            virtualConsole.on("jsdomError", error => errors.push(String(error.message)));
            virtualConsole.on("error", (...args) => errors.push(args.join(" ")));

            const dom = new JSDOM(html, {
              runScripts: "dangerously",
              virtualConsole,
              beforeParse(window) {
                // jsdom has no layout engine; the page only uses these to react
                // to size changes, which never happen here.
                window.ResizeObserver = class {
                  observe() {} unobserve() {} disconnect() {}
                };
              },
            });

            const { window } = dom;
            const { document } = window;

            // Search: "/" opens it, typing filters, Enter runs the first hit.
            const press = (key, target) => target.dispatchEvent(
              new window.KeyboardEvent("keydown",
                { key, bubbles: true, cancelable: true }));
            const overlay = document.getElementById("cmdk");
            const input = document.getElementById("cmdkInput");
            press("/", document.body);
            const searchOpened = !overlay.hidden;
            input.value = "providers";
            input.dispatchEvent(new window.Event("input", { bubbles: true }));
            const searchRows = document.querySelectorAll("#cmdkList li[data-i]").length;
            press("Enter", input);
            const searchOpenedDrawer =
              document.getElementById("drawer").classList.contains("open");
            const searchClosed = overlay.hidden;

            const filled = id => (document.getElementById(id)?.innerHTML ?? "").length;
            const result = {
              errors,
              searchOpened, searchRows, searchOpenedDrawer, searchClosed,
              heroFacts: document.querySelectorAll("#heroFacts span").length,
              heroLines: document.getElementById("heroLines").textContent,
              statgridRemoved: document.getElementById("stats") === null,
              flow: filled("flow"),
              pkgcards: filled("pkgcards"),
              hubs: filled("hubs"),
              repo: filled("repo"),
              healthstats: filled("healthstats"),
              healthbody: filled("healthbody"),
              rows: filled("rows"),
              graphNodes: document.querySelectorAll("#graphwrap .node").length,
              graphEdges: document.querySelectorAll("#graphwrap .edge").length,
              clusters: document.querySelectorAll("#graphwrap .cluster").length,
              umlBoxes: document.querySelectorAll("#classwrap .uml").length,
              genEdges: document.querySelectorAll("#classwrap .gen").length,
              findings: document.querySelectorAll("#healthbody .finding").length,
              flowLayers: document.querySelectorAll("#flow .flowlayer").length,
              navButtons: document.querySelectorAll("nav button").length,
            };
            console.log(JSON.stringify(result));
            """
        )
        completed = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            0,
            msg=f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}",
        )
        result = json.loads(completed.stdout.strip().splitlines()[-1])

        self.assertEqual(result["errors"], [])
        for key in (
            "flow",
            "pkgcards",
            "hubs",
            "repo",
            "healthstats",
            "healthbody",
            "rows",
        ):
            with self.subTest(container=key):
                self.assertGreater(result[key], 0, f"#{key} rendered empty")
        # The hero states the size of the system; the old stat grid repeated it.
        self.assertTrue(result["statgridRemoved"])
        self.assertGreaterEqual(result["heroFacts"], 5)
        self.assertRegex(result["heroLines"], r"^[\d,]+$")
        self.assertNotEqual(result["heroLines"], "0")
        # Search: opens on "/", filters, and Enter opens the matched module.
        self.assertTrue(result["searchOpened"])
        self.assertGreater(result["searchRows"], 0)
        self.assertTrue(result["searchOpenedDrawer"])
        self.assertTrue(result["searchClosed"])
        self.assertGreater(result["graphNodes"], 20)
        self.assertGreater(result["graphEdges"], 50)
        self.assertEqual(result["clusters"], 2)
        self.assertGreater(result["umlBoxes"], 20)
        self.assertGreater(result["genEdges"], 20)
        self.assertGreaterEqual(result["findings"], 4)
        self.assertGreater(result["flowLayers"], 2)
        self.assertEqual(result["navButtons"], 6)


if __name__ == "__main__":
    unittest.main()
