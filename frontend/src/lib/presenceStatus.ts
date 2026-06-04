const ACTIVE_PRESENCE_STATUSES = new Set(["online", "working", "ready", "running"]);

export function isActivePresence(status?: string): boolean {
  return ACTIVE_PRESENCE_STATUSES.has(String(status || ""));
}

export function presenceStatusLabel(status?: string): string {
  if (status === "pending") return "실행 필요";
  if (status === "invited") return "초대됨";
  if (status === "running") return "실행 중";
  if (status === "ready") return "준비됨";
  if (status === "working") return "작업 중";
  if (status === "online") return "온라인";
  if (status === "idle") return "자리 비움";
  if (status === "error") return "오류";
  if (status === "offline") return "오프라인";
  return "상태 미정";
}
