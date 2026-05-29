from __future__ import annotations

import subprocess
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FrontendMeetingStreamRuntimeTests(unittest.TestCase):
    def test_meeting_stream_parser_merge_and_timeline_conversion(self):
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
            const tempDir = await fs.mkdtemp(path.join(os.tmpdir(), "aa-meeting-stream-"));
            const modulePath = path.join(tempDir, "api.mjs");
            await fs.writeFile(modulePath, compiled, "utf8");
            const api = await import(pathToFileURL(modulePath).href);

            const delta = api.parseMeetingStreamData(JSON.stringify({
              stream: "meeting",
              meeting_id: "m1",
              events: [
                { id: "live-a", kind: "message", display_name: "Codex", content: "첫 발언", created_at: "2026-01-01T00:00:00Z", official_record: true },
              ],
            }));
            assert.equal(delta.meetingId, "m1");
            assert.equal(delta.events.length, 1);
            assert.equal(delta.events[0].content, "첫 발언");

            const snapshot = api.parseMeetingStreamData(JSON.stringify({
              stream: "meeting",
              meeting_id: "m1",
              meeting_payload: {
                meeting: { meeting_id: "m1" },
                live_events: [
                  { id: "live-a", kind: "message", display_name: "Codex", content: "첫 발언", created_at: "2026-01-01T00:00:00Z", official_record: true },
                  { id: "live-b", kind: "research", role_id: "research", content: "근거 수집", created_at: "2026-01-01T00:00:02Z" },
                ],
                lifecycle: { state: "running_official_turns", status_source: "live_state", counts: { roles: 1, bindings: 1, live_agents: 1, pending_turns: 0, official_messages: 1 }, role_hints: [], attention: [] },
              },
            }));
            assert.equal(snapshot.meetingId, "m1");
            assert.equal(snapshot.events.length, 2);
            assert.equal(snapshot.lifecycle.state, "running_official_turns");

            const merged = api.mergeMeetingLiveEvents(delta.events, [
              { id: "live-a", kind: "message", display_name: "Codex", content: "수정", created_at: "2026-01-01T00:00:00Z" },
              { id: "live-c", kind: "message", name: "Kiro", message: "새 발언", created_at: "2026-01-01T00:00:03Z" },
            ]);
            assert.deepEqual(merged.map((event) => event.id), ["live-a", "live-c"]);
            assert.equal(merged[0].content, "수정");

            const timeline = api.meetingLiveEventsToTimelineEvents(snapshot.events);
            assert.equal(timeline[0].id, "live-a");
            assert.equal(timeline[0].name, "Codex");
            assert.equal(timeline[0].message, "첫 발언");
            assert.equal(timeline[0].official_record, true);
            assert.equal(timeline[1].name, "research");
            assert.equal(timeline[1].message, "근거 수집");

            const m1State = api.applyMeetingStreamUpdate(
              api.initialMeetingStreamState("m1"),
              "m1",
              snapshot,
            );
            assert.equal(m1State.meetingId, "m1");
            assert.equal(m1State.events.length, 2);
            assert.equal(m1State.lifecycle.state, "running_official_turns");

            const hiddenForM2 = api.meetingStreamStateForActiveMeeting(m1State, "m2");
            assert.equal(hiddenForM2.meetingId, "m2");
            assert.equal(hiddenForM2.events.length, 0);
            assert.equal(hiddenForM2.lifecycle, null);

            const m2State = api.applyMeetingStreamUpdate(
              api.initialMeetingStreamState("m2"),
              "m2",
              { meetingId: "m1", events: [{ id: "wrong-meeting", content: "stale" }] },
            );
            assert.equal(m2State.meetingId, "m2");
            assert.equal(m2State.events.length, 0);

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
            const unsubscribe = api.subscribeMeetingEvents("m1", (update) => {
              seen.push({ type: "update", count: update.events.length, meetingId: update.meetingId });
            });
            const opened = seen[0];
            assert.equal(opened.url, "/api/meetings/m1/events");
            lastSource.listeners.get("meeting")({
              data: JSON.stringify({ stream: "meeting", meeting_id: "m1", events: [{ id: "live-z", content: "z" }] }),
            });
            unsubscribe();
            assert.deepEqual(seen.map((item) => item.type), ["open", "update", "close"]);
            assert.equal(seen[1].count, 1);
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
