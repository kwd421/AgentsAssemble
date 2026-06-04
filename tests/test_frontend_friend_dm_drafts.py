from __future__ import annotations

import subprocess
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FrontendFriendDmDraftTests(unittest.TestCase):
    def test_friend_dm_drafts_are_keyed_by_friend_and_clear_after_send(self):
        script = textwrap.dedent(
            """
            import assert from "node:assert/strict";
            import fs from "node:fs/promises";
            import os from "node:os";
            import path from "node:path";
            import { pathToFileURL } from "node:url";
            import ts from "./frontend/node_modules/typescript/lib/typescript.js";

            const source = await fs.readFile(path.resolve("frontend/src/lib/friendDmDraftModel.ts"), "utf8");
            const compiled = ts.transpileModule(source, {
              compilerOptions: {
                module: ts.ModuleKind.ES2022,
                target: ts.ScriptTarget.ES2022,
              },
            }).outputText;
            const tempDir = await fs.mkdtemp(path.join(os.tmpdir(), "aa-friend-dm-drafts-"));
            const modulePath = path.join(tempDir, "friendDmDraftModel.mjs");
            await fs.writeFile(modulePath, compiled, "utf8");
            const model = await import(pathToFileURL(modulePath).href);

            let drafts = {};
            drafts = model.updateFriendDmDraft(drafts, "friend:codex", "codex draft");
            drafts = model.updateFriendDmDraft(drafts, "friend:claude", "claude draft");

            assert.equal(model.friendDmDraftValue(drafts, "friend:codex"), "codex draft");
            assert.equal(model.friendDmDraftValue(drafts, "friend:claude"), "claude draft");
            assert.equal(model.friendDmDraftValue(drafts, "friend:missing"), "");

            const unchanged = model.updateFriendDmDraft(drafts, "friend:codex", "codex draft");
            assert.equal(unchanged, drafts);

            const blankFriendNoop = model.updateFriendDmDraft(drafts, "  ", "ignored");
            assert.equal(blankFriendNoop, drafts);

            const afterClear = model.clearFriendDmDraft(drafts, "friend:codex");
            assert.equal(model.friendDmDraftValue(afterClear, "friend:codex"), "");
            assert.equal(model.friendDmDraftValue(afterClear, "friend:claude"), "claude draft");
            assert.deepEqual(Object.keys(afterClear), ["friend:claude"]);
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
