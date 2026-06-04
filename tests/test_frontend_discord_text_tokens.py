from __future__ import annotations

import subprocess
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FrontendDiscordTextTokenTests(unittest.TestCase):
    def test_discord_text_tokens_keep_invite_link_punctuation_out_of_href(self):
        script = textwrap.dedent(
            """
            import assert from "node:assert/strict";
            import fs from "node:fs/promises";
            import os from "node:os";
            import path from "node:path";
            import { pathToFileURL } from "node:url";
            import ts from "./frontend/node_modules/typescript/lib/typescript.js";

            const sourcePath = path.resolve("frontend/src/lib/discordTextTokens.ts");
            const source = await fs.readFile(sourcePath, "utf8");
            const compiled = ts.transpileModule(source, {
              compilerOptions: {
                module: ts.ModuleKind.ES2022,
                target: ts.ScriptTarget.ES2022,
              },
            }).outputText;
            const tempDir = await fs.mkdtemp(path.join(os.tmpdir(), "aa-discord-text-"));
            const modulePath = path.join(tempDir, "discordTextTokens.mjs");
            await fs.writeFile(modulePath, compiled, "utf8");
            const textTokens = await import(pathToFileURL(modulePath).href);

            const invite = "http://127.0.0.1:8765/app/?guest=1&room=room-a";
            assert.deepEqual(textTokens.tokenizeDiscordText(`초대 ${invite}.`), [
              { kind: "text", value: "초대 " },
              { kind: "link", value: invite },
              { kind: "text", value: "." },
            ]);
            assert.deepEqual(textTokens.tokenizeDiscordText(`(${invite})`), [
              { kind: "text", value: "(" },
              { kind: "link", value: invite },
              { kind: "text", value: ")" },
            ]);
            assert.deepEqual(textTokens.tokenizeDiscordText("문서 https://example.test/a_(b)."), [
              { kind: "text", value: "문서 " },
              { kind: "link", value: "https://example.test/a_(b)" },
              { kind: "text", value: "." },
            ]);
            assert.deepEqual(textTokens.tokenizeDiscordText("@Codex #general `code`"), [
              { kind: "mention", value: "@Codex" },
              { kind: "text", value: " " },
              { kind: "channel", value: "#general" },
              { kind: "text", value: " " },
              { kind: "code", value: "code" },
            ]);
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
