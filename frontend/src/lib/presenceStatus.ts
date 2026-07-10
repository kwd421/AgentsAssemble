const ACTIVE_PRESENCE_STATUSES = new Set([
  "online",
  "working",
  "ready",
  "running",
  "joined",
  "attached",
]);

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
  if (status === "joined") return "참여 중";
  if (status === "attached") return "연결됨";
  if (status === "idle") return "자리 비움";
  if (status === "available") return "시작 대기";
  if (status === "paused") return "일시정지";
  if (status === "stopped") return "중지됨";
  if (status === "disconnected") return "연결 끊김";
  if (status === "left") return "퇴장";
  if (status === "kicked") return "추방됨";
  if (status === "error") return "오류";
  if (status === "offline") return "오프라인";
  return "상태 미정";
}
