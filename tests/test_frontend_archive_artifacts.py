from __future__ import annotations

import subprocess
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FrontendArchiveArtifactsTests(unittest.TestCase):
    def test_archive_artifacts_prioritize_canonical_final_outputs(self):
        script = textwrap.dedent(
            """
            import assert from "node:assert/strict";
            import fs from "node:fs/promises";
            import os from "node:os";
            import path from "node:path";
            import { pathToFileURL } from "node:url";
            import ts from "./frontend/node_modules/typescript/lib/typescript.js";

            const sourcePath = path.resolve("frontend/src/views/RecordsView.tsx");
            const source = await fs.readFile(sourcePath, "utf8");
            const start = source.indexOf("export type ArchiveArtifactMap");
            const end = source.indexOf("function statusLabel");
            assert.ok(start >= 0, "archive artifact helpers should stay local to RecordsView");
            assert.ok(end > start, "archive artifact helper block should end before view helpers");
            const helperSource = source.slice(start, end);
            const tempDir = await fs.mkdtemp(path.join(os.tmpdir(), "aa-archive-artifacts-"));
            const modulePath = path.join(tempDir, "archiveArtifacts.mjs");
            await fs.writeFile(modulePath, ts.transpileModule(helperSource, {
              compilerOptions: {
                module: ts.ModuleKind.ES2022,
                target: ts.ScriptTarget.ES2022,
              },
            }).outputText, "utf8");
            const archive = await import(pathToFileURL(modulePath).href);

            assert.deepEqual(
              archive.CANONICAL_FINAL_ARTIFACTS.map((artifact) => artifact.path),
              [
                "transcript.md",
                "decision.md",
                "shared_memory/rolling-summary.md",
                "shared_memory/action-items.md",
                "shared_memory/open-questions.md",
              ]
            );

            const artifacts = {
              "agenda.md": "# Agenda",
              "meeting.json": "{}",
              "decision.md": "# Decision",
              "shared_memory/action-items.md": "# Action Items",
              "shared_memory/open-questions.md": "",
              "transcript.md": null,
            };

            const rows = archive.canonicalArchiveArtifactRows(artifacts);
            assert.deepEqual(rows.map((row) => [row.path, row.available]), [
              ["transcript.md", false],
              ["decision.md", true],
              ["shared_memory/rolling-summary.md", false],
              ["shared_memory/action-items.md", true],
              ["shared_memory/open-questions.md", false],
            ]);

            assert.deepEqual(archive.otherArchiveArtifactNames(artifacts), [
              "agenda.md",
              "meeting.json",
            ]);
            assert.equal(archive.defaultArchiveArtifactSelection(artifacts), "decision.md");
            assert.equal(
              archive.defaultArchiveArtifactSelection(artifacts, "agenda.md"),
              "agenda.md"
            );
            assert.equal(
              archive.defaultArchiveArtifactSelection(artifacts, "transcript.md"),
              "decision.md"
            );
            assert.equal(
              archive.defaultArchiveArtifactSelection({ "meeting.json": "{}" }),
              "meeting.json"
            );
            assert.equal(archive.defaultArchiveArtifactSelection({}), null);
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
