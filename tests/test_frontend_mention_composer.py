from __future__ import annotations

import subprocess
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FrontendMentionComposerTests(unittest.TestCase):
    def test_mention_model_handles_spaced_display_names(self):
        script = textwrap.dedent(
            """
            import assert from "node:assert/strict";
            import fs from "node:fs/promises";
            import os from "node:os";
            import path from "node:path";
            import { pathToFileURL } from "node:url";
            import ts from "./frontend/node_modules/typescript/lib/typescript.js";

            const sourcePath = path.resolve("frontend/src/lib/mentionComposerModel.ts");
            const source = await fs.readFile(sourcePath, "utf8");
            const compiled = ts.transpileModule(source, {
              compilerOptions: {
                module: ts.ModuleKind.ES2022,
                target: ts.ScriptTarget.ES2022,
              },
            }).outputText;
            const tempDir = await fs.mkdtemp(path.join(os.tmpdir(), "aa-mentions-"));
            const modulePath = path.join(tempDir, "mentionComposerModel.mjs");
            await fs.writeFile(modulePath, compiled, "utf8");
            const mentions = await import(pathToFileURL(modulePath).href);

            const query = mentions.mentionQueryAtCursor("hello @Cod", "hello @Cod".length);
            assert.deepEqual(query, { start: 6, query: "cod" });
            assert.equal(mentions.mentionQueryAtCursor("email@codex", "email@codex".length), null);

            const options = mentions.mentionOptions(
              ["Codex Spark A", "Kiro Opus 4.8", "codex spark a", "", "나"],
              query
            );
            assert.deepEqual(options, ["Codex Spark A"]);

            assert.equal(mentions.formatMentionToken("Kiro"), "@Kiro");
            assert.equal(mentions.formatMentionToken("Codex Spark A"), "<@Codex Spark A>");

            const inserted = mentions.insertMentionText(
              "hello @Cod",
              "hello @Cod".length,
              query,
              "Codex Spark A"
            );
            assert.deepEqual(inserted, {
              message: "hello <@Codex Spark A> ",
              cursor: "hello <@Codex Spark A> ".length,
            });
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
