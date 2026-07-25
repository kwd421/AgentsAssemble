from __future__ import annotations

import subprocess
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FrontendRoomRailMenuPositionTests(unittest.TestCase):
    def test_room_rail_context_menu_stays_inside_bottom_viewport_edge(self):
        script = textwrap.dedent(
            """
            import assert from "node:assert/strict";
            import fs from "node:fs/promises";
            import os from "node:os";
            import path from "node:path";
            import { pathToFileURL } from "node:url";
            import ts from "./frontend/node_modules/typescript/lib/typescript.js";

            const source = await fs.readFile(path.resolve("frontend/src/lib/roomRailMenuPosition.ts"), "utf8");
            const compiled = ts.transpileModule(source, {
              compilerOptions: {
                module: ts.ModuleKind.ES2022,
                target: ts.ScriptTarget.ES2022,
              },
            }).outputText;
            const tempDir = await fs.mkdtemp(path.join(os.tmpdir(), "aa-room-rail-menu-"));
            const modulePath = path.join(tempDir, "roomRailMenuPosition.mjs");
            await fs.writeFile(modulePath, compiled, "utf8");
            const menu = await import(pathToFileURL(modulePath).href);

            const position = menu.roomRailMenuPosition(
              { x: 48, y: 780 },
              { width: 390, height: 800 },
              { width: 220, height: 220 },
              8
            );
            assert.equal(position.left, 48);
            assert.equal(position.top, 572);

            const rightEdgePosition = menu.roomRailMenuPosition(
              { x: 380, y: 400 },
              { width: 390, height: 800 },
              { width: 220, height: 220 },
              8
            );
            assert.equal(rightEdgePosition.left, 162);
            assert.equal(rightEdgePosition.top, 400);
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
