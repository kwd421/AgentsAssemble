import type {
  ReleaseHealthBenchmarkSummary,
  ReleaseHealthCatalog,
  ReleaseHealthCheck,
  ReleaseHealthQueue,
  ReleaseHealthQueueCheck,
} from "../api";

export const RELEASE_HEALTH_SAFETY_LABELS: Record<string, string> = {
  frontend_react_build: "React 빌드",
  python_unit: "Python 단위검증",
  python_integration: "통합 검증",
  python_compile: "패키지 컴파일",
  git_format: "Git 형식",
  local_room_benchmark: "로컬 룸 벤치",
};

export function releaseHealthSafetyLabel(safetyClass?: string) {
  return RELEASE_HEALTH_SAFETY_LABELS[safetyClass || ""] || "검증";
}

export function releaseHealthSelector(check: Pick<ReleaseHealthCheck, "id">) {
  return `assemble release-health run --check ${check.id}`;
}

export function releaseHealthQueueBadge(check: Pick<ReleaseHealthCheck, "default_run">) {
  return check.default_run === true ? "default" : "opt-in";
}

export function releaseHealthStatusLabel(status?: string) {
  if (status === "ok") return "통과";
  if (status === "passed") return "통과";
  if (status === "failed") return "실패";
  if (status === "skipped") return "건너뜀";
  if (status === "not_run") return "미실행";
  return "미확인";
}

export function releaseHealthStatusTone(status?: string) {
  if (status === "ok") return "online";
  if (status === "passed") return "online";
  if (status === "failed") return "danger";
  if (status === "skipped") return "warn";
  return "muted";
}

export function releaseHealthLatestById(
  queue?: Pick<ReleaseHealthQueue, "checks"> | null
) {
  const checks = Array.isArray(queue?.checks) ? queue.checks : [];
  return new Map<string, ReleaseHealthQueueCheck>(
    checks.map((check) => [check.id, check])
  );
}

function releaseHealthOrder(check: ReleaseHealthCheck) {
  return typeof check.order === "number" ? check.order : 999;
}

export function partitionReleaseHealthChecks(catalog?: Pick<ReleaseHealthCatalog, "checks"> | null) {
  const checks = Array.isArray(catalog?.checks) ? catalog.checks : [];
  return {
    defaultChecks: checks
      .filter((check) => check.default_run === true)
      .slice()
      .sort((left, right) => releaseHealthOrder(left) - releaseHealthOrder(right)),
    optInChecks: checks.filter((check) => check.default_run !== true),
  };
}

export type ReleaseHealthBenchmarkRow = {
  id: string;
  label: string;
  value: string;
  detail: string;
  ok: boolean | null;
};

export function releaseHealthBenchmarkRows(
  summary?: ReleaseHealthBenchmarkSummary | null
): ReleaseHealthBenchmarkRow[] {
  if (!summary || summary.status !== "ok") return [];
  const metrics = summary.metrics_summary ?? {};
  const rows: ReleaseHealthBenchmarkRow[] = [];
  const anchorImprovement = finiteMetric(metrics.flow_anchor_share_improvement);
  if (anchorImprovement !== null) {
    const off = finiteMetric(metrics.flow_anchor_share_off);
    const on = finiteMetric(metrics.flow_anchor_share_on);
    rows.push({
      id: "flow_anchor_share_improvement",
      label: "첫 발언 앵커 완화",
      value: formatSignedPercentPoint(anchorImprovement),
      detail:
        off !== null && on !== null
          ? `${formatShare(off)} → ${formatShare(on)}`
          : "scheduler on/off comparison",
      ok: benchmarkSignalOk(summary, "flow_anchor_share_improvement"),
    });
  }
  const predicateP99 = finiteMetric(metrics.flow_scheduler_predicate_p99_ms);
  if (predicateP99 !== null) {
    const signal = benchmarkSignal(summary, "flow_scheduler_predicate_p99_ms");
    const ceiling = finiteMetric(signal?.ceiling_ms);
    rows.push({
      id: "flow_scheduler_predicate_p99_ms",
      label: "스케줄러 판정 p99",
      value: `${predicateP99.toFixed(1)}ms`,
      detail: ceiling !== null ? `ceiling ${ceiling.toFixed(0)}ms` : "local predicate latency",
      ok: signal?.ok ?? null,
    });
  }
  return rows;
}

function benchmarkSignal(summary: ReleaseHealthBenchmarkSummary, name: string) {
  return (summary.regression_signals ?? []).find((signal) => signal.name === name);
}

function benchmarkSignalOk(summary: ReleaseHealthBenchmarkSummary, name: string) {
  return benchmarkSignal(summary, name)?.ok ?? null;
}

function finiteMetric(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function formatShare(value: number) {
  return `${Math.round(value * 100)}%`;
}

function formatSignedPercentPoint(value: number) {
  const percentPoint = Math.round(value * 100);
  return `${percentPoint > 0 ? "+" : ""}${percentPoint}pp`;
}
