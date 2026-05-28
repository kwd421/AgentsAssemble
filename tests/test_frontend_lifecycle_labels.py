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

        visible_labels = json.dumps(
            {
                "states": [value["label"] for value in payload["states"].values()],
                "attention": payload["attention"],
                "sources": payload["sources"],
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
            "/Users/",
        ):
            self.assertNotIn(raw_contract, visible_labels)


if __name__ == "__main__":
    unittest.main()
