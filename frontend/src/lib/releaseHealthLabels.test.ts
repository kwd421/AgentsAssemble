import { describe, expect, it } from "vitest";

import {
  partitionReleaseHealthChecks,
  releaseHealthBenchmarkRows,
  releaseHealthLatestById,
} from "./releaseHealthLabels";

describe("release health queue projections", () => {
  it("orders default checks while keeping opt-in checks out of the default queue", () => {
    const catalog = {
      checks: [
        {
          id: "late-default",
          label: "Late default",
          kind: "unit",
          category: "tests",
          requires: ["python3"],
          default_run: true,
          order: 2,
        },
        {
          id: "benchmark",
          label: "Benchmark",
          kind: "benchmark",
          category: "room",
          requires: ["python3"],
          default_run: false,
          order: null,
        },
        {
          id: "early-default",
          label: "Early default",
          kind: "build",
          category: "frontend",
          requires: ["node"],
          default_run: true,
          order: 1,
        },
      ],
    };

    const partitioned = partitionReleaseHealthChecks(catalog);

    expect(partitioned.defaultChecks.map((check) => check.id)).toEqual([
      "early-default",
      "late-default",
    ]);
    expect(partitioned.optInChecks.map((check) => check.id)).toEqual(["benchmark"]);
  });

  it("indexes the latest result for each check without inventing missing results", () => {
    const latest = releaseHealthLatestById({
      checks: [
        {
          id: "python",
          label: "Python",
          kind: "unit",
          category: "tests",
          requires: ["python3"],
          latest_status: "passed",
          latest_duration_seconds: 0.2,
        },
        {
          id: "frontend",
          label: "Frontend",
          kind: "build",
          category: "frontend",
          requires: ["node"],
          latest_status: "failed",
          latest_duration_seconds: 0.4,
        },
      ],
    });

    expect(latest.get("python")).toMatchObject({
      latest_status: "passed",
      latest_duration_seconds: 0.2,
    });
    expect(latest.has("missing")).toBe(false);
  });

  it("projects only finite supported benchmark signals with their pass state", () => {
    const rows = releaseHealthBenchmarkRows({
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
          ok: false,
        },
      ],
    });

    expect(rows.map((row) => ({ id: row.id, ok: row.ok }))).toEqual([
      { id: "flow_anchor_share_improvement", ok: true },
      { id: "flow_scheduler_predicate_p99_ms", ok: false },
    ]);
    expect(releaseHealthBenchmarkRows({ status: "unparsed" })).toEqual([]);
  });
});
