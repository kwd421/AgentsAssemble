from __future__ import annotations

import subprocess
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FrontendSidebarResizeTests(unittest.TestCase):
    def test_sidebar_width_model_clamps_drag_delta_and_persists(self):
        script = textwrap.dedent(
            """
            import assert from "node:assert/strict";
            import fs from "node:fs/promises";
            import os from "node:os";
            import path from "node:path";
            import { pathToFileURL } from "node:url";
            import ts from "./frontend/node_modules/typescript/lib/typescript.js";

            const source = await fs.readFile(path.resolve("frontend/src/lib/sidebarResizeModel.ts"), "utf8");
            const compiled = ts.transpileModule(source, {
              compilerOptions: {
                module: ts.ModuleKind.ES2022,
                target: ts.ScriptTarget.ES2022,
              },
            }).outputText;
            const tempDir = await fs.mkdtemp(path.join(os.tmpdir(), "aa-sidebar-resize-"));
            const modulePath = path.join(tempDir, "sidebarResizeModel.mjs");
            await fs.writeFile(modulePath, compiled, "utf8");
            const sidebar = await import(pathToFileURL(modulePath).href);

            assert.equal(sidebar.SIDEBAR_WIDTH_DEFAULT, 312);
            assert.equal(sidebar.SIDEBAR_WIDTH_MIN, 220);
            assert.equal(sidebar.SIDEBAR_WIDTH_MAX, 420);

            assert.equal(sidebar.normalizeSidebarWidth("360.4"), 360);
            assert.equal(sidebar.normalizeSidebarWidth("120"), 220);
            assert.equal(sidebar.normalizeSidebarWidth("999"), 420);
            assert.equal(sidebar.normalizeSidebarWidth("bad"), 312);

            assert.equal(sidebar.resizedSidebarWidth({ startWidth: 312, startX: 500, currentX: 560 }), 372);
            assert.equal(sidebar.resizedSidebarWidth({ startWidth: 312, startX: 500, currentX: 100 }), 220);
            assert.equal(sidebar.resizedSidebarWidth({ startWidth: 312, startX: 500, currentX: 900 }), 420);

            const store = new Map();
            const storage = {
              getItem: (key) => store.has(key) ? store.get(key) : null,
              setItem: (key, value) => store.set(key, String(value)),
              removeItem: (key) => store.delete(key),
            };
            assert.equal(sidebar.loadSidebarWidth(storage), 312);
            sidebar.persistSidebarWidth(380, storage);
            assert.equal(store.get(sidebar.SIDEBAR_WIDTH_STORAGE_KEY), "380");
            assert.equal(sidebar.loadSidebarWidth(storage), 380);
            sidebar.persistSidebarWidth(999, storage);
            assert.equal(sidebar.loadSidebarWidth(storage), 420);
            sidebar.persistSidebarWidth(null, storage);
            assert.equal(sidebar.loadSidebarWidth(storage), 312);
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


if __name__ == "__main__":
    unittest.main()
