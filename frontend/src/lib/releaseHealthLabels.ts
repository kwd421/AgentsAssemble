import type {
  ReleaseHealthCatalog,
  ReleaseHealthCheck,
  ReleaseHealthQueue,
  ReleaseHealthQueueCheck,
} from "../api";

export const RELEASE_HEALTH_SAFETY_LABELS: Record<string, string> = {
  frontend_static_syntax: "정적 JS 문법",
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
