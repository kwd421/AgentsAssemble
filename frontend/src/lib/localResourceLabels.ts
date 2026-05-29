export const RESOURCE_ROLE_LABELS: Record<string, string> = {
  supervised_resident: "감독 중",
  agentsassemble: "AA 자식",
  other: "기타",
};

export const RESOURCE_ATTENTION_LABELS: Record<string, string> = {
  load_average_high: "부하 높음 (CPU당 1.5 초과)",
  process_cpu_high: "CPU 점유 높음 (90% 이상)",
  ps_unavailable: "ps 실행 불가",
  ps_failed: "ps 응답 실패",
};

export type LoadAverageTriple = {
  one?: number;
  five?: number;
  fifteen?: number;
};

export function resourceRoleLabel(role: string) {
  return RESOURCE_ROLE_LABELS[role] || role;
}

export function resourceAttentionLabel(code: string) {
  return RESOURCE_ATTENTION_LABELS[code] || code;
}

export function formatResourceMemory(rssKb?: number) {
  const mb = Math.max(0, Number(rssKb || 0) / 1024);
  if (mb >= 1024) {
    return `${(mb / 1024).toFixed(1)} GB`;
  }
  return `${mb >= 100 ? mb.toFixed(0) : mb.toFixed(1)} MB`;
}

export function formatLoadAverageTriple(loadAverage: LoadAverageTriple) {
  return `${formatLoadAverageValue(loadAverage.one)} / ${formatLoadAverageValue(
    loadAverage.five
  )} / ${formatLoadAverageValue(loadAverage.fifteen)}`;
}

function formatLoadAverageValue(value?: number) {
  const numeric = Number(value || 0);
  if (!Number.isFinite(numeric) || numeric < 0) {
    return "0.00";
  }
  return numeric.toFixed(2);
}
