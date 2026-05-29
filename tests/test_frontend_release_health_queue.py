from __future__ import annotations

import json
import subprocess
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FrontendReleaseHealthQueueTests(unittest.TestCase):
    def test_release_health_queue_helpers_are_deterministic_and_cli_only(self):
        script = textwrap.dedent(
            """
            import assert from "node:assert/strict";
            import fs from "node:fs/promises";
            import os from "node:os";
            import path from "node:path";
            import { pathToFileURL } from "node:url";
            import ts from "./frontend/node_modules/typescript/lib/typescript.js";

            const source = await fs.readFile(
              path.resolve("frontend/src/lib/releaseHealthLabels.ts"),
              "utf8"
            );
            const compiled = ts.transpileModule(source, {
              compilerOptions: {
                module: ts.ModuleKind.ES2022,
                target: ts.ScriptTarget.ES2022,
              },
            }).outputText;
            const tempDir = await fs.mkdtemp(path.join(os.tmpdir(), "aa-release-health-"));
            const modulePath = path.join(tempDir, "releaseHealthLabels.mjs");
            await fs.writeFile(modulePath, compiled, "utf8");
            const labels = await import(pathToFileURL(modulePath).href);

            const expectedSafetyClasses = [
              "frontend_static_syntax",
              "python_unit",
              "python_integration",
              "python_compile",
              "git_format",
              "local_room_benchmark",
            ];
            assert.deepEqual(
              Object.keys(labels.RELEASE_HEALTH_SAFETY_LABELS).sort(),
              expectedSafetyClasses.sort()
            );
            assert.equal(labels.releaseHealthSafetyLabel("python_unit"), "Python 단위검증");
            assert.equal(labels.releaseHealthSafetyLabel("local_room_benchmark"), "로컬 룸 벤치");
            assert.equal(labels.releaseHealthSafetyLabel("future_class"), "검증");

            const catalog = {
              checks: [
                {
                  id: "late_default",
                  label: "Late default",
                  kind: "unit",
                  category: "tests",
                  requires: ["python3"],
                  optional: false,
                  default_run: true,
                  order: 2,
                  safety_class: "python_unit",
                },
                {
                  id: "benchmark",
                  label: "Benchmark",
                  kind: "benchmark",
                  category: "live_room",
                  requires: ["python3"],
                  optional: true,
                  default_run: false,
                  order: null,
                  safety_class: "local_room_benchmark",
                },
                {
                  id: "early_default",
                  label: "Early default",
                  kind: "syntax",
                  category: "frontend",
                  requires: ["node"],
                  optional: false,
                  default_run: true,
                  order: 1,
                  safety_class: "frontend_static_syntax",
                },
              ],
            };
            const partitioned = labels.partitionReleaseHealthChecks(catalog);
            assert.deepEqual(partitioned.defaultChecks.map((check) => check.id), [
              "early_default",
              "late_default",
            ]);
            assert.deepEqual(partitioned.optInChecks.map((check) => check.id), ["benchmark"]);
            assert.equal(labels.releaseHealthQueueBadge(partitioned.defaultChecks[0]), "default");
            assert.equal(labels.releaseHealthQueueBadge(partitioned.optInChecks[0]), "opt-in");
            assert.equal(labels.releaseHealthStatusLabel("passed"), "통과");
            assert.equal(labels.releaseHealthStatusLabel("ok"), "통과");
            assert.equal(labels.releaseHealthStatusLabel("failed"), "실패");
            assert.equal(labels.releaseHealthStatusLabel("not_run"), "미실행");
            assert.equal(labels.releaseHealthStatusTone("failed"), "danger");
            assert.equal(labels.releaseHealthStatusTone("ok"), "online");
            assert.equal(labels.releaseHealthStatusTone("not_run"), "muted");
            assert.deepEqual(
              labels.releaseHealthLatestById({
                checks: [
                  { id: "early_default", latest_status: "passed", latest_duration_seconds: 0.2 },
                  {
                    id: "benchmark",
                    latest_status: "passed",
                    benchmark_summary: {
                      status: "ok",
                      metrics_summary: {
                        flow_anchor_share_off: 0.65,
                        flow_anchor_share_on: 0.25,
                        flow_anchor_share_improvement: 0.4,
                        flow_scheduler_predicate_p99_ms: 12.5,
                      },
                      regression_signals: [
                        {
                          name: "flow_anchor_share_improvement",
                          value: 0.4,
                          floor: 0.25,
                          ok: true,
                        },
                        {
                          name: "flow_scheduler_predicate_p99_ms",
                          value_ms: 12.5,
                          ceiling_ms: 75,
                          ok: true,
                        },
                      ],
                    },
                  },
                ],
              }).get("early_default"),
              { id: "early_default", latest_status: "passed", latest_duration_seconds: 0.2 }
            );
            assert.deepEqual(
              labels.releaseHealthBenchmarkRows({
                status: "ok",
                metrics_summary: {
                  flow_anchor_share_off: 0.65,
                  flow_anchor_share_on: 0.25,
                  flow_anchor_share_improvement: 0.4,
                  flow_scheduler_predicate_p99_ms: 12.5,
                },
                regression_signals: [
                  {
                    name: "flow_anchor_share_improvement",
                    value: 0.4,
                    floor: 0.25,
                    ok: true,
                  },
                  {
                    name: "flow_scheduler_predicate_p99_ms",
                    value_ms: 12.5,
                    ceiling_ms: 75,
                    ok: true,
                  },
                ],
              }).map((row) => ({
                id: row.id,
                value: row.value,
                detail: row.detail,
                ok: row.ok,
              })),
              [
                {
                  id: "flow_anchor_share_improvement",
                  value: "+40pp",
                  detail: "65% → 25%",
                  ok: true,
                },
                {
                  id: "flow_scheduler_predicate_p99_ms",
                  value: "12.5ms",
                  detail: "ceiling 75ms",
                  ok: true,
                },
              ]
            );
            assert.deepEqual(labels.releaseHealthBenchmarkRows({ status: "unparsed" }), []);
            assert.equal(
              labels.releaseHealthBenchmarkRows({
                status: "ok",
                metrics_summary: {
                  flow_anchor_share_improvement: -0.1,
                },
                regression_signals: [
                  {
                    name: "flow_anchor_share_improvement",
                    value: -0.1,
                    floor: 0.25,
                    ok: false,
                  },
                ],
              })[0].value,
              "-10pp"
            );
            assert.equal(
              labels.releaseHealthSelector(partitioned.optInChecks[0]),
              "assemble release-health run --check benchmark"
            );

            const unsafeSourceFragments = ["fetch(", "EventSource", "method: \\"POST\\"", "child_process"];
            for (const fragment of unsafeSourceFragments) {
              assert.equal(source.includes(fragment), false, fragment);
            }
            console.log(JSON.stringify({ ok: true }));
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
        self.assertEqual(json.loads(completed.stdout), {"ok": True})


if __name__ == "__main__":
    unittest.main()
