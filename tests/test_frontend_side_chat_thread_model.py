from __future__ import annotations

import subprocess
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FrontendSideChatThreadModelTests(unittest.TestCase):
    def test_side_chat_thread_projection_keeps_thread_replies_out_of_base_feed(self):
        script = textwrap.dedent(
            """
            import assert from "node:assert/strict";
            import fs from "node:fs/promises";
            import os from "node:os";
            import path from "node:path";
            import { pathToFileURL } from "node:url";
            import ts from "./frontend/node_modules/typescript/lib/typescript.js";

            const source = await fs.readFile(path.resolve("frontend/src/lib/sideChatThreadModel.ts"), "utf8");
            const compiled = ts.transpileModule(source, {
              compilerOptions: {
                module: ts.ModuleKind.ES2022,
                target: ts.ScriptTarget.ES2022,
              },
            }).outputText;
            const tempDir = await fs.mkdtemp(path.join(os.tmpdir(), "aa-side-chat-thread-"));
            const modulePath = path.join(tempDir, "sideChatThreadModel.mjs");
            await fs.writeFile(modulePath, compiled, "utf8");
            const model = await import(pathToFileURL(modulePath).href);

            const events = [
              { id: "global-a", name: "나", message: "기본 사이드챗", created_at: "2026-01-01T00:00:00Z" },
              { id: "thread-a", name: "Claude", message: "첫 스레드 답장", created_at: "2026-01-01T00:00:01Z", thread_source_event_id: "lobby-source-1" },
              { id: "thread-b", name: "Codex", message: "다른 스레드", created_at: "2026-01-01T00:00:02Z", thread_source_event_id: "lobby-source-2" },
              { id: "blank-source", name: "Local", message: "빈 소스는 기본 피드", created_at: "2026-01-01T00:00:03Z", thread_source_event_id: "  " },
              { id: "thread-c", name: "Kiro", message: "최신 답장", created_at: "2026-01-01T00:00:04Z", thread_source_event_id: "lobby-source-1" },
            ];
            const threadContext = {
              sourceEventId: "lobby-source-1",
              sourceName: "Room",
              sourceMessage: "원본",
              channelLabel: "general",
            };

            assert.deepEqual(
              model.sideChatEventsForThreadContext(events, null).map((event) => event.id),
              ["global-a", "blank-source"]
            );
            assert.deepEqual(
              model.sideChatEventsForThreadContext(events, threadContext).map((event) => event.id),
              ["thread-a", "thread-c"]
            );

            const summaries = model.threadSummariesForSideChat(events);
            assert.equal(summaries["lobby-source-1"].replyCount, 2);
            assert.equal(summaries["lobby-source-1"].lastReplyName, "Kiro");
            assert.equal(summaries["lobby-source-2"].replyCount, 1);
            assert.equal(summaries["lobby-source-2"].lastReplyName, "Codex");
            assert.equal(Object.hasOwn(summaries, "  "), false);
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
