from __future__ import annotations

import subprocess
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FrontendSideChatRuntimeTests(unittest.TestCase):
    def test_side_chat_parser_merge_and_subscription_contract(self):
        script = textwrap.dedent(
            """
            import assert from "node:assert/strict";
            import fs from "node:fs/promises";
            import os from "node:os";
            import path from "node:path";
            import { pathToFileURL } from "node:url";
            import ts from "./frontend/node_modules/typescript/lib/typescript.js";

            const source = await fs.readFile(path.resolve("frontend/src/api.ts"), "utf8");
            const compiled = ts.transpileModule(source, {
              compilerOptions: {
                module: ts.ModuleKind.ES2022,
                target: ts.ScriptTarget.ES2022,
              },
            }).outputText;
            const tempDir = await fs.mkdtemp(path.join(os.tmpdir(), "aa-side-chat-"));
            const modulePath = path.join(tempDir, "api.mjs");
            await fs.writeFile(modulePath, compiled, "utf8");
            const api = await import(pathToFileURL(modulePath).href);

            const snapshot = api.parseSideChatStreamData(JSON.stringify({
              stream: "side_chat",
              events: [
                { id: "side-a", kind: "message", name: "나", side: "mine", message: "비공식", created_at: "2026-01-01T00:00:00Z", official_record: false },
              ],
            }));
            assert.equal(snapshot.length, 1);
            assert.equal(snapshot[0].id, "side-a");
            assert.equal(snapshot[0].message, "비공식");

            const single = api.parseSideChatStreamData(JSON.stringify({
              id: "side-b",
              kind: "message",
              name: "Kiro",
              side: "other-agent",
              message: "옆 이야기",
              created_at: "2026-01-01T00:00:01Z",
            }));
            assert.equal(single.length, 1);
            assert.equal(single[0].id, "side-b");

            const invalidStream = api.parseSideChatStreamData(JSON.stringify({
              stream: "lobby",
              events: [{ id: "lobby-a", name: "Lobby", message: "섞이면 안 됨" }],
            }));
            assert.equal(invalidStream.length, 0);

            const invalidChannelArray = api.parseSideChatStreamData(JSON.stringify({
              stream: "side_chat",
              events: [{ id: "lobby-b", name: "Lobby", message: "채널도 확인", channel: "lobby" }],
            }));
            assert.equal(invalidChannelArray.length, 0);

            const invalidSingleChannel = api.parseSideChatStreamData(JSON.stringify({
              id: "lobby-c",
              name: "Lobby",
              message: "단일 이벤트도 확인",
              channel: "lobby",
            }));
            assert.equal(invalidSingleChannel.length, 0);

            const merged = api.mergeSideChatEvents(snapshot, [
              { id: "side-a", kind: "message", name: "나", side: "mine", message: "수정", created_at: "2026-01-01T00:00:00Z" },
              { id: "side-c", kind: "message", name: "Grok", side: "other-agent", message: "새 비공식", created_at: "2026-01-01T00:00:02Z" },
            ]);
            assert.deepEqual(merged.map((event) => event.id), ["side-a", "side-c"]);
            assert.equal(merged[0].message, "수정");

            const seen = [];
            let lastSource = null;
            class FakeEventSource {
              constructor(url) {
                this.url = url;
                this.listeners = new Map();
                lastSource = this;
                seen.push({ type: "open", url });
              }
              addEventListener(type, listener) {
                this.listeners.set(type, listener);
              }
              close() {
                seen.push({ type: "close", url: this.url });
              }
            }
            globalThis.EventSource = FakeEventSource;
            const unsubscribe = api.subscribeSideChat((events) => {
              seen.push({ type: "update", count: events.length, message: events[0].message });
            });
            assert.equal(seen[0].url, "/api/events/side-chat");
            lastSource.listeners.get("side_chat")({
              data: JSON.stringify({ stream: "side_chat", events: [{ id: "side-z", name: "Z", message: "z" }] }),
            });
            unsubscribe();
            assert.deepEqual(seen.map((item) => item.type), ["open", "update", "close"]);
            assert.equal(seen[1].message, "z");
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
