from __future__ import annotations

import json
import subprocess
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FrontendLifecycleLabelTests(unittest.TestCase):
    def test_lifecycle_labels_are_human_safe_and_complete(self):
        script = textwrap.dedent(
            """
            import fs from "node:fs/promises";
            import os from "node:os";
            import path from "node:path";
            import { pathToFileURL } from "node:url";
            import ts from "./frontend/node_modules/typescript/lib/typescript.js";

            async function compileSource(sourcePath, outPath, replacements = []) {
              let source = await fs.readFile(sourcePath, "utf8");
              for (const [from, to] of replacements) source = source.replaceAll(from, to);
              const compiled = ts.transpileModule(source, {
                compilerOptions: {
                  module: ts.ModuleKind.ES2022,
                  target: ts.ScriptTarget.ES2022,
                },
              }).outputText;
              await fs.writeFile(outPath, compiled, "utf8");
            }

            const tempDir = await fs.mkdtemp(path.join(os.tmpdir(), "aa-lifecycle-labels-"));
            await compileSource(
              path.resolve("frontend/src/lib/agentLabels.ts"),
              path.join(tempDir, "agentLabels.mjs")
            );
            await compileSource(
              path.resolve("frontend/src/lib/lifecycleLabels.ts"),
              path.join(tempDir, "lifecycleLabels.mjs"),
              [["./agentLabels", "./agentLabels.mjs"]]
            );
            const labels = await import(pathToFileURL(path.join(tempDir, "lifecycleLabels.mjs")).href);

            const states = [
              "preparing",
              "waiting_for_agents",
              "running_official_turns",
              "blocked_by_pending_turns",
              "stopped",
              "finalized",
              "archived",
              "unknown",
            ];
            const result = {
              states: Object.fromEntries(states.map((state) => [state, labels.lifecycleStateLabel(state)])),
              attention: [
                labels.lifecycleAttentionLabel("pending_official_turns"),
                labels.lifecycleAttentionLabel("stalled_running_state"),
                labels.lifecycleAttentionLabel("malformed"),
              ],
              sources: [
                labels.lifecycleStatusSourceLabel("live_state"),
                labels.lifecycleStatusSourceLabel("final_record"),
                labels.lifecycleStatusSourceLabel("stale_running_inference"),
              ],
              summary: labels.summarizeCompactLifecycle({
                state: "blocked_by_pending_turns",
                status_source: "live_state",
                counts: {
                  roles: 3,
                  bindings: 2,
                  live_agents: 1,
                  pending_turns: 2,
                  official_messages: 5,
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
                    role_id: "reviewer",
                    display_name: "Reviewer",
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
                attention: ["pending_official_turns", "malformed"],
              }),
              emptySummary: labels.summarizeCompactLifecycle(null),
              missingSourceSummary: labels.summarizeCompactLifecycle({
                state: "preparing",
                counts: {},
                role_hints: [],
                attention: [],
              }),
              unknownSourceSummary: labels.summarizeCompactLifecycle({
                state: "preparing",
                status_source: "provider_config_/Users/secret_prompt",
                counts: {},
                role_hints: [],
                attention: [],
              }),
              unknown: labels.lifecycleStateLabel("some_new_state"),
            };
            console.log(JSON.stringify(result));
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
        self.assertEqual(payload["states"]["preparing"], {"label": "준비 중", "tone": "accent"})
        self.assertEqual(payload["states"]["waiting_for_agents"], {"label": "입장 대기", "tone": "idle"})
        self.assertEqual(payload["states"]["running_official_turns"], {"label": "공식 진행", "tone": "online"})
        self.assertEqual(payload["states"]["blocked_by_pending_turns"], {"label": "응답 대기", "tone": "idle"})
        self.assertEqual(payload["states"]["stopped"], {"label": "정지됨", "tone": "danger"})
        self.assertEqual(payload["states"]["finalized"], {"label": "완료됨", "tone": "online"})
        self.assertEqual(payload["states"]["archived"], {"label": "기록만 있음", "tone": "muted"})
        self.assertEqual(payload["states"]["unknown"], {"label": "상태 불명", "tone": "muted"})
        self.assertEqual(payload["attention"], ["공식 턴 대기", "장시간 갱신 없음", "기록 파싱 오류"])
        self.assertEqual(payload["sources"], ["실시간 상태", "최종 기록", "정지 추정"])
        self.assertEqual(payload["unknown"], {"label": "Some new state", "tone": "muted"})
        self.assertEqual(payload["summary"]["state"], "blocked_by_pending_turns")
        self.assertEqual(payload["summary"]["stepLabel"], "응답 대기")
        self.assertIn("대기 중인 공식 턴", payload["summary"]["nextAction"])
        self.assertEqual(payload["summary"]["statusSourceLabel"], "실시간 상태")
        self.assertEqual(payload["summary"]["rolesTotal"], 3)
        self.assertEqual(payload["summary"]["boundRoles"], 1)
        self.assertEqual(payload["summary"]["missingRoles"], 2)
        self.assertEqual(payload["summary"]["liveAgents"], 1)
        self.assertEqual(payload["summary"]["pendingTurns"], 2)
        self.assertEqual(payload["summary"]["officialMessages"], 5)
        self.assertEqual(payload["summary"]["unsafePermissionViolations"], 2)
        self.assertEqual(
            [item["label"] for item in payload["summary"]["attentionItems"]],
            ["공식 턴 대기", "기록 파싱 오류"],
        )
        self.assertEqual(payload["summary"]["hasLifecycle"], True)
        self.assertEqual(payload["emptySummary"]["state"], "none")
        self.assertEqual(payload["emptySummary"]["stepLabel"], "회의 없음")
        self.assertEqual(
            payload["emptySummary"]["nextAction"],
            "#general에서 새 회의를 시작하거나 기존 회의를 선택하세요.",
        )
        self.assertEqual(payload["emptySummary"]["statusSourceLabel"], "기록 없음")
        self.assertEqual(payload["emptySummary"]["hasLifecycle"], False)
        self.assertEqual(payload["missingSourceSummary"]["statusSourceLabel"], "기록 없음")
        self.assertEqual(payload["unknownSourceSummary"]["statusSourceLabel"], "기록 없음")

        visible_labels = json.dumps(
            {
                "states": [value["label"] for value in payload["states"].values()],
                "attention": payload["attention"],
                "sources": payload["sources"],
                "summary": {
                    "stepLabel": payload["summary"]["stepLabel"],
                    "nextAction": payload["summary"]["nextAction"],
                    "statusSourceLabel": payload["summary"]["statusSourceLabel"],
                    "attention": [item["label"] for item in payload["summary"]["attentionItems"]],
                },
                "emptySummary": {
                    "stepLabel": payload["emptySummary"]["stepLabel"],
                    "nextAction": payload["emptySummary"]["nextAction"],
                    "statusSourceLabel": payload["emptySummary"]["statusSourceLabel"],
                },
                "missingSourceSummary": {
                    "stepLabel": payload["missingSourceSummary"]["stepLabel"],
                    "statusSourceLabel": payload["missingSourceSummary"]["statusSourceLabel"],
                },
                "unknownSourceSummary": {
                    "stepLabel": payload["unknownSourceSummary"]["stepLabel"],
                    "statusSourceLabel": payload["unknownSourceSummary"]["statusSourceLabel"],
                },
                "unknown": payload["unknown"]["label"],
            },
            ensure_ascii=False,
        )
        for raw_contract in (
            "waiting_for_agents",
            "running_official_turns",
            "blocked_by_pending_turns",
            "pending_official_turns",
            "stale_running_inference",
            "provider_config_/Users/secret_prompt",
            "/Users/",
        ):
            self.assertNotIn(raw_contract, visible_labels)


if __name__ == "__main__":
    unittest.main()
