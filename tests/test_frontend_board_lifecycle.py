from __future__ import annotations

import json
import subprocess
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FrontendBoardLifecycleTests(unittest.TestCase):
    def test_board_lifecycle_summary_maps_states_to_next_actions_and_safe_counts(self):
        script = textwrap.dedent(
            """
            import fs from "node:fs/promises";
            import os from "node:os";
            import path from "node:path";
            import { pathToFileURL } from "node:url";
            import ts from "./frontend/node_modules/typescript/lib/typescript.js";

            const sourcePath = path.resolve("frontend/src/lib/boardLifecycle.ts");
            const source = await fs.readFile(sourcePath, "utf8");
            const compiled = ts.transpileModule(source, {
              compilerOptions: {
                module: ts.ModuleKind.ES2022,
                target: ts.ScriptTarget.ES2022,
              },
            }).outputText;
            const tempDir = await fs.mkdtemp(path.join(os.tmpdir(), "aa-board-lifecycle-"));
            const modulePath = path.join(tempDir, "boardLifecycle.mjs");
            await fs.writeFile(modulePath, compiled, "utf8");
            const board = await import(pathToFileURL(modulePath).href);

            const lifecycle = {
              state: "waiting_for_agents",
              status_source: "live_state",
              counts: {
                roles: 3,
                bindings: 2,
                live_agents: 1,
                pending_turns: 0,
                official_messages: 0,
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
                  role_id: "design",
                  display_name: "Design Lead",
                  admission_status: "requested",
                  unsafe_permission_violations: 2,
                  permissions: {
                    meeting_read: true,
                    lobby_chat: true,
                    official_turn: false,
                    web_search: true,
                    tool_use: true,
                    git_write: true,
                    secrets: true,
                  },
                },
                {
                  role_id: "review",
                  display_name: "Review Lead",
                  admission_status: "present_unapproved",
                  unsafe_permission_violations: 1,
                  permissions: {
                    meeting_read: true,
                    lobby_chat: false,
                    official_turn: false,
                    web_search: false,
                    tool_use: false,
                  },
                },
              ],
              attention: [
                "pending_official_turns",
                "stalled_running_state",
                "malformed",
                "future_attention_code",
              ],
            };

            const states = [
              "preparing",
              "waiting_for_agents",
              "running_official_turns",
              "blocked_by_pending_turns",
              "finalized",
              "stopped",
              "archived",
              "unknown",
            ];
            const mapped = Object.fromEntries(
              states.map((state) => [state, board.summarizeBoardLifecycle({ ...lifecycle, state })])
            );
            const empty = board.summarizeBoardLifecycle(null);
            console.log(JSON.stringify({ mapped, empty }));
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
        payload = json.loads(completed.stdout)
        mapped = payload["mapped"]

        self.assertEqual(mapped["preparing"]["stepLabel"], "준비 중")
        self.assertIn("회의 목표", mapped["preparing"]["nextAction"])
        self.assertEqual(mapped["waiting_for_agents"]["stepLabel"], "입장 대기")
        self.assertIn("미입실", mapped["waiting_for_agents"]["nextAction"])
        self.assertEqual(mapped["running_official_turns"]["stepLabel"], "공식 진행")
        self.assertIn("공식 발언", mapped["running_official_turns"]["nextAction"])
        self.assertEqual(mapped["blocked_by_pending_turns"]["stepLabel"], "응답 대기")
        self.assertIn("대기 중인 공식 턴", mapped["blocked_by_pending_turns"]["nextAction"])
        self.assertEqual(mapped["finalized"]["stepLabel"], "완료됨")
        self.assertIn("아카이브", mapped["finalized"]["nextAction"])
        self.assertEqual(mapped["stopped"]["stepLabel"], "정지됨")
        self.assertIn("재개", mapped["stopped"]["nextAction"])
        self.assertEqual(mapped["unknown"]["stepLabel"], "상태 불명")

        summary = mapped["waiting_for_agents"]
        self.assertEqual(summary["rolesTotal"], 3)
        self.assertEqual(summary["boundRoles"], 1)
        self.assertEqual(summary["missingRoles"], 2)
        self.assertEqual(summary["unsafePermissionViolations"], 3)
        self.assertEqual(summary["officialTurnRoles"], 1)
        self.assertEqual(summary["toolUseRoles"], 1)
        self.assertEqual(summary["webSearchRoles"], 1)
        self.assertEqual(
            [item["label"] for item in summary["attentionItems"]],
            ["공식 턴 대기", "세션 정지 추정", "기록 손상", "future_attention_code"],
        )
        self.assertEqual(
            [item["tone"] for item in summary["attentionItems"]],
            ["warn", "danger", "danger", "info"],
        )
        self.assertEqual(
            [role["admissionLabel"] for role in summary["roles"]],
            ["입장 완료", "입장 대기", "미승인 입장"],
        )
        self.assertEqual(summary["roles"][0]["roleId"], "director")
        self.assertEqual(summary["roles"][0]["displayName"], "Director")
        self.assertEqual(summary["roles"][1]["unsafePermissionViolations"], 2)
        self.assertEqual(summary["roles"][1]["permissions"]["tool_use"], True)
        self.assertNotIn("git_write", summary["roles"][1]["permissions"])
        self.assertNotIn("secrets", summary["roles"][1]["permissions"])
        self.assertEqual(summary["roles"][2]["permissions"]["lobby_chat"], False)
        self.assertEqual(payload["empty"]["attentionItems"], [])
        self.assertEqual(payload["empty"]["roles"], [])
        self.assertEqual(payload["empty"]["stepLabel"], "상태 불명")
        self.assertEqual(payload["empty"]["rolesTotal"], 0)


if __name__ == "__main__":
    unittest.main()
