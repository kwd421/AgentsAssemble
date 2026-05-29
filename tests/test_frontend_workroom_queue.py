from __future__ import annotations

import json
import subprocess
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def frontend_file(path: str) -> str:
    return (ROOT / "frontend" / "src" / path).read_text(encoding="utf-8")


class FrontendWorkroomQueueTests(unittest.TestCase):
    def test_workroom_queue_summarizes_gates_without_raw_room_text(self):
        script = textwrap.dedent(
            """
            import assert from "node:assert/strict";
            import fs from "node:fs/promises";
            import os from "node:os";
            import path from "node:path";
            import { pathToFileURL } from "node:url";
            import ts from "./frontend/node_modules/typescript/lib/typescript.js";

            const sourcePath = path.resolve("frontend/src/lib/workroomQueue.ts");
            const source = await fs.readFile(sourcePath, "utf8");
            const compiled = ts.transpileModule(source, {
              compilerOptions: {
                module: ts.ModuleKind.ES2022,
                target: ts.ScriptTarget.ES2022,
              },
            }).outputText;
            const tempDir = await fs.mkdtemp(path.join(os.tmpdir(), "aa-workroom-queue-"));
            const modulePath = path.join(tempDir, "workroomQueue.mjs");
            await fs.writeFile(modulePath, compiled, "utf8");
            const queue = await import(pathToFileURL(modulePath).href);

            const lifecycle = {
              state: "blocked_by_pending_turns",
              status_source: "live_state",
              counts: {
                roles: 3,
                bindings: 2,
                live_agents: 1,
                pending_turns: 2,
                official_messages: 4,
              },
              role_hints: [
                {
                  role_id: "director",
                  display_name: "Director",
                  admission_status: "bound_to_meeting",
                  unsafe_permission_violations: 0,
                  permissions: {
                    meeting_read: true,
                    lobby_chat: true,
                    official_turn: true,
                    web_search: false,
                    tool_use: false,
                  },
                },
                {
                  role_id: "designer",
                  display_name: "Design Lead",
                  admission_status: "requested",
                  unsafe_permission_violations: 2,
                  permissions: {
                    meeting_read: true,
                    lobby_chat: true,
                    official_turn: false,
                    web_search: true,
                    tool_use: true,
                  },
                },
              ],
              attention: ["pending_official_turns", "stalled_running_state"],
            };

            const summary = queue.summarizeWorkroomQueue({
              lifecycle,
              artifacts: {
                "transcript.md": { available: true },
                "decision.md": { available: true },
                "shared_memory/rolling-summary.md": { available: true },
                "shared_memory/action-items.md": { available: true },
                "shared_memory/open-questions.md": { available: false },
                "agenda.md": { available: true },
              },
              reviewCheckpointCount: 1,
              returnPacketCount: 1,
              lobbyEvents: [
                {
                  id: "lobby-1",
                  kind: "message",
                  message: "SECRET_UNPROMOTED_PLAY_CHATTER",
                  official_record: false,
                },
              ],
            });

            assert.deepEqual(summary.lanes.map((lane) => lane.id), [
              "blocked",
              "review",
              "official_record",
              "shared_memory",
            ]);

            const blocked = summary.lanes.find((lane) => lane.id === "blocked");
            assert.deepEqual(blocked.items.map((item) => item.id), [
              "pending_official_turns",
              "missing_roles",
              "unsafe_permissions",
              "attention:stalled_running_state",
            ]);
            assert.equal(blocked.count, 4);

            const review = summary.lanes.find((lane) => lane.id === "review");
            assert.deepEqual(review.items.map((item) => item.id), [
              "review_checkpoints",
              "return_packets",
            ]);
            assert.equal(review.count, 2);

            const official = summary.lanes.find((lane) => lane.id === "official_record");
            assert.equal(official.count, 4);
            assert.equal(official.total, 5);
            assert.ok(official.items.some((item) => item.id === "artifact:decision.md"));
            assert.ok(official.items.some((item) => item.id === "artifact:shared_memory/open-questions.md"));

            const memory = summary.lanes.find((lane) => lane.id === "shared_memory");
            assert.deepEqual(memory.items.map((item) => item.id), [
              "shared_memory/rolling-summary.md",
              "shared_memory/action-items.md",
              "shared_memory/open-questions.md",
            ]);
            assert.equal(memory.count, 2);
            assert.equal(memory.total, 3);

            const serialized = JSON.stringify(summary);
            for (const forbidden of [
              "SECRET_UNPROMOTED_PLAY_CHATTER",
            ]) {
              assert.equal(serialized.includes(forbidden), false, forbidden);
            }
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

    def test_board_uses_read_only_workroom_queue_panel(self):
        board_source = frontend_file("views/BoardView.tsx")
        app_source = frontend_file("App.tsx")
        panel_source = frontend_file("views/components/WorkroomQueuePanel.tsx")

        self.assertIn("import WorkroomQueuePanel", board_source)
        self.assertIn("workroomQueueEvidence: WorkroomQueueEvidence | null;", board_source)
        self.assertIn("<WorkroomQueuePanel", board_source)
        self.assertIn("fetchWorkroomQueueEvidence", app_source)
        self.assertIn('channel !== "board"', app_source)
        self.assertIn('channel !== "live"', app_source)
        self.assertIn("scopedWorkroomQueueEvidence", app_source)
        self.assertIn("workroomQueueEvidence={scopedWorkroomQueueEvidence}", app_source)
        self.assertNotIn("fetchMeetingDetail", app_source)
        self.assertNotIn("meetingDetail={", app_source)
        self.assertIn("summarizeWorkroomQueue", panel_source)

        for forbidden in ("onClick=", "fetch(", "postJson", "EventSource", "dangerouslySetInnerHTML"):
            self.assertNotIn(forbidden, panel_source)


if __name__ == "__main__":
    unittest.main()
