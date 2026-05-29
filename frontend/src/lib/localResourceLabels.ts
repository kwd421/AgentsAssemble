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

export type LocalResourceProcessLike = {
  pid?: number;
  comm?: string;
  role?: string;
  cpu_pct?: number;
  rss_kb?: number;
};

export type LocalResourceSpotlightRow = {
  id: "top_cpu" | "top_memory";
  label: string;
  processName: string;
  roleLabel: string;
  value: string;
  detail: string;
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

export function localResourceSpotlightRows(
  processes: LocalResourceProcessLike[]
): LocalResourceSpotlightRow[] {
  if (!Array.isArray(processes) || processes.length === 0) return [];
  const topCpu = processes
    .slice()
    .sort((left, right) => finiteNumber(right.cpu_pct) - finiteNumber(left.cpu_pct))[0];
  const topMemory = processes
    .slice()
    .sort((left, right) => finiteNumber(right.rss_kb) - finiteNumber(left.rss_kb))[0];
  return [
    {
      id: "top_cpu",
      label: "상위 CPU",
      processName: processName(topCpu),
      roleLabel: resourceRoleLabel(String(topCpu.role || "other")),
      value: `${finiteNumber(topCpu.cpu_pct).toFixed(1)}%`,
      detail: processPidDetail(topCpu),
    },
    {
      id: "top_memory",
      label: "상위 메모리",
      processName: processName(topMemory),
      roleLabel: resourceRoleLabel(String(topMemory.role || "other")),
      value: formatResourceMemory(finiteNumber(topMemory.rss_kb)),
      detail: processPidDetail(topMemory),
    },
  ];
}

export function localResourceUnavailableMessage(error?: Error | null) {
  const message = String(error?.message || "");
  if (/\b404\b/i.test(message) || /\bnot found\b/i.test(message)) {
    return "현재 연결된 backend가 /api/local-resources를 제공하지 않습니다. 최신 GUI backend를 재시작하거나 /app/에서 확인하세요.";
  }
  return "로컬 리소스 정보를 읽지 못했습니다.";
}

function formatLoadAverageValue(value?: number) {
  const numeric = Number(value || 0);
  if (!Number.isFinite(numeric) || numeric < 0) {
    return "0.00";
  }
  return numeric.toFixed(2);
}

function finiteNumber(value?: number) {
  const numeric = Number(value || 0);
  if (!Number.isFinite(numeric) || numeric < 0) return 0;
  return numeric;
}

function processName(process: LocalResourceProcessLike) {
  return String(process.comm || "process");
}

function processPidDetail(process: LocalResourceProcessLike) {
  const pid = Number(process.pid || 0);
  return Number.isFinite(pid) && pid > 0 ? `PID ${Math.trunc(pid)}` : "PID --";
}
