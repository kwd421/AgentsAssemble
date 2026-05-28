import { humanizeToken } from "./agentLabels";

export type LifecycleTone = "accent" | "online" | "idle" | "danger" | "muted";

export type LifecycleLabel = {
  label: string;
  tone: LifecycleTone;
};

const STATE_LABELS: Record<string, LifecycleLabel> = {
  preparing: { label: "준비 중", tone: "accent" },
  waiting_for_agents: { label: "입장 대기", tone: "idle" },
  running_official_turns: { label: "공식 진행", tone: "online" },
  blocked_by_pending_turns: { label: "응답 대기", tone: "idle" },
  stopped: { label: "정지됨", tone: "danger" },
  finalized: { label: "완료됨", tone: "online" },
  archived: { label: "기록만 있음", tone: "muted" },
  unknown: { label: "상태 불명", tone: "muted" },
};

const ATTENTION_LABELS: Record<string, string> = {
  pending_official_turns: "공식 턴 대기",
  stalled_running_state: "장시간 갱신 없음",
  malformed: "기록 파싱 오류",
};

const STATUS_SOURCE_LABELS: Record<string, string> = {
  live_state: "실시간 상태",
  final_record: "최종 기록",
  stale_running_inference: "정지 추정",
  missing_state: "기록 없음",
  malformed_record: "손상된 기록",
};

export function lifecycleStateLabel(state?: string): LifecycleLabel {
  const key = String(state || "").trim();
  return STATE_LABELS[key] || { label: humanizeToken(key || "unknown"), tone: "muted" };
}

export function lifecycleAttentionLabel(code?: string): string {
  const key = String(code || "").trim();
  return ATTENTION_LABELS[key] || humanizeToken(key);
}

export function lifecycleStatusSourceLabel(source?: string): string {
  const key = String(source || "").trim();
  return STATUS_SOURCE_LABELS[key] || humanizeToken(key);
}
