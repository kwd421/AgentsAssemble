from __future__ import annotations

import subprocess
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FrontendLobbySseRuntimeTests(unittest.TestCase):
    def test_lobby_parser_merge_and_subscription_contract(self):
        script = textwrap.dedent(
            """
            import assert from "node:assert/strict";
            import fs from "node:fs/promises";
            import os from "node:os";
            import path from "node:path";
            import { pathToFileURL } from "node:url";
            import { compileTypeScriptModule } from "./tests/frontend_api_runtime_helpers.mjs";

            const tempDir = await fs.mkdtemp(path.join(os.tmpdir(), "aa-lobby-sse-"));
            const modulePath = await compileTypeScriptModule(path.resolve("frontend/src/api.ts"), tempDir);
            const api = await import(pathToFileURL(modulePath).href);

            const snapshot = api.parseLobbyStreamData(JSON.stringify({
              stream: "lobby",
              events: [
                { id: "lobby-a", kind: "message", name: "나", side: "mine", message: "로비", created_at: "2026-01-01T00:00:00Z", channel: "lobby" },
              ],
            }));
            assert.equal(snapshot.length, 1);
            assert.equal(snapshot[0].id, "lobby-a");
            assert.equal(snapshot[0].message, "로비");

            const single = api.parseLobbyStreamData(JSON.stringify({
              id: "lobby-b",
              kind: "message",
              name: "Kiro",
              side: "other-agent",
              message: "새 로비",
              created_at: "2026-01-01T00:00:01Z",
            }));
            assert.equal(single.length, 1);
            assert.equal(single[0].id, "lobby-b");

            const invalidStream = api.parseLobbyStreamData(JSON.stringify({
              stream: "side_chat",
              events: [{ id: "side-a", name: "Side", message: "섞이면 안 됨", channel: "side_chat" }],
            }));
            assert.equal(invalidStream.length, 0);

            const invalidChannelArray = api.parseLobbyStreamData(JSON.stringify({
              stream: "lobby",
              events: [{ id: "side-b", name: "Side", message: "채널도 확인", channel: "side_chat" }],
            }));
            assert.equal(invalidChannelArray.length, 0);

            const invalidSingleChannel = api.parseLobbyStreamData(JSON.stringify({
              id: "side-c",
              name: "Side",
              message: "단일 이벤트도 확인",
              channel: "side_chat",
            }));
            assert.equal(invalidSingleChannel.length, 0);

            const merged = api.mergeLobbyEvents(snapshot, [
              { id: "lobby-a", kind: "message", name: "나", side: "mine", message: "수정", created_at: "2026-01-01T00:00:00Z", channel: "lobby" },
              { id: "lobby-c", kind: "message", name: "Grok", side: "other-agent", message: "새 로비", created_at: "2026-01-01T00:00:02Z", channel: "lobby" },
            ]);
            assert.deepEqual(merged.map((event) => event.id), ["lobby-a", "lobby-c"]);
            assert.equal(merged[0].message, "수정");

            const chronological = api.mergeLobbyEventsByCreatedAt(
              [{ id: "late", kind: "message", name: "Late", side: "other", message: "늦게 온 기록", created_at: "2026-01-01T00:00:03Z" }],
              [{ id: "early", kind: "message", name: "Early", side: "other", message: "먼저 보여야 함", created_at: "2026-01-01T00:00:01Z" }]
            );
            assert.deepEqual(chronological.map((event) => event.id), ["early", "late"]);

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
            const unsubscribe = api.subscribeLobby((events) => {
              seen.push({ type: "update", count: events.length, message: events[0].message });
            });
            assert.equal(seen[0].url, "/api/events/lobby");
            lastSource.listeners.get("lobby")({
              data: JSON.stringify({ stream: "lobby", events: [{ id: "lobby-z", name: "Z", message: "z", channel: "lobby" }] }),
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
