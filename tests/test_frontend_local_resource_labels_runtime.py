from __future__ import annotations

import json
import subprocess
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FrontendLocalResourceLabelsRuntimeTests(unittest.TestCase):
    def test_local_resource_labels_and_formatters_are_deterministic(self):
        script = textwrap.dedent(
            """
            import assert from "node:assert/strict";
            import fs from "node:fs/promises";
            import os from "node:os";
            import path from "node:path";
            import { pathToFileURL } from "node:url";
            import ts from "./frontend/node_modules/typescript/lib/typescript.js";

            const source = await fs.readFile(path.resolve("frontend/src/lib/localResourceLabels.ts"), "utf8");
            const compiled = ts.transpileModule(source, {
              compilerOptions: {
                module: ts.ModuleKind.ES2022,
                target: ts.ScriptTarget.ES2022,
              },
            }).outputText;
            const tempDir = await fs.mkdtemp(path.join(os.tmpdir(), "aa-local-resources-"));
            const modulePath = path.join(tempDir, "localResourceLabels.mjs");
            await fs.writeFile(modulePath, compiled, "utf8");
            const labels = await import(pathToFileURL(modulePath).href);

            assert.equal(labels.resourceAttentionLabel("load_average_high"), "부하 높음 (CPU당 1.5 초과)");
            assert.equal(labels.resourceAttentionLabel("process_cpu_high"), "CPU 점유 높음 (90% 이상)");
            assert.equal(labels.resourceAttentionLabel("ps_unavailable"), "ps 실행 불가");
            assert.equal(labels.resourceAttentionLabel("ps_failed"), "ps 응답 실패");
            assert.equal(labels.resourceAttentionLabel("future_code"), "future_code");

            assert.equal(
              labels.formatLoadAverageTriple({ one: 1.234, five: 2, fifteen: 3.456 }),
              "1.23 / 2.00 / 3.46"
            );
            assert.equal(
              labels.formatLoadAverageTriple({ one: Number.NaN, five: -2, fifteen: undefined }),
              "0.00 / 0.00 / 0.00"
            );
            assert.equal(labels.formatResourceMemory(512), "0.5 MB");
            assert.equal(labels.formatResourceMemory(120 * 1024), "120 MB");
            assert.equal(labels.formatResourceMemory(2 * 1024 * 1024), "2.0 GB");
            console.log(JSON.stringify({ ok: true }));
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
        self.assertEqual(json.loads(completed.stdout), {"ok": True})


if __name__ == "__main__":
    unittest.main()
