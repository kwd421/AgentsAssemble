import { humanizeToken } from "./agentLabels";
import type { LifecycleProjection } from "../api";

export type LifecycleTone = "accent" | "online" | "idle" | "danger" | "muted";

export type LifecycleLabel = {
  label: string;
  tone: LifecycleTone;
};

type LifecycleCopy = {
  stepLabel: string;
  nextAction: string;
};

export type CompactLifecycleAttention = {
  code: string;
  label: string;
};

export type CompactLifecycleSummary = LifecycleCopy & {
  state: string;
  statusSourceLabel: string;
  rolesTotal: number;
  boundRoles: number;
  missingRoles: number;
  liveAgents: number;
  pendingTurns: number;
  officialMessages: number;
  unsafePermissionViolations: number;
  attentionItems: CompactLifecycleAttention[];
  hasLifecycle: boolean;
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

const LIFECYCLE_COPY: Record<string, LifecycleCopy> = {
  preparing: {
    stepLabel: "준비 중",
    nextAction: "회의 목표와 역할 바인딩을 확인하세요.",
  },
  waiting_for_agents: {
    stepLabel: "입장 대기",
    nextAction: "미입실 역할을 초대하거나 승인 상태를 확인하세요.",
  },
  running_official_turns: {
    stepLabel: "공식 진행",
    nextAction: "공식 발언과 공유 메모리 갱신을 확인하세요.",
  },
  blocked_by_pending_turns: {
    stepLabel: "응답 대기",
    nextAction: "대기 중인 공식 턴을 기다리거나 명시적으로 닫으세요.",
  },
  finalized: {
    stepLabel: "완료됨",
    nextAction: "아카이브에서 transcript, decision, shared memory를 확인하세요.",
  },
  stopped: {
    stepLabel: "정지됨",
    nextAction: "필요하면 명시적으로 재개하거나 종료 기록을 확인하세요.",
  },
  archived: {
    stepLabel: "기록만 있음",
    nextAction: "아카이브에서 최종 산출물과 리뷰 기록을 확인하세요.",
  },
  unknown: {
    stepLabel: "상태 불명",
    nextAction: "라이프사이클 기록을 확인하세요.",
  },
  none: {
    stepLabel: "회의 없음",
    nextAction: "채팅 채널에서 새 회의를 시작하거나 기존 회의를 선택하세요.",
  },
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

function lifecycleProjectionStatusSourceLabel(source?: string): string {
  const key = String(source || "").trim();
  return STATUS_SOURCE_LABELS[key] || STATUS_SOURCE_LABELS.missing_state;
}

function nonNegativeNumber(value: unknown): number {
  const numberValue = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(numberValue)) return 0;
  return Math.max(0, Math.trunc(numberValue));
}

export function summarizeCompactLifecycle(
  lifecycle?: LifecycleProjection | null
): CompactLifecycleSummary {
  const hasLifecycle = Boolean(lifecycle);
  const lifecycleState = hasLifecycle ? String(lifecycle?.state || "unknown").trim() : "none";
  const copy = LIFECYCLE_COPY[lifecycleState] || LIFECYCLE_COPY.unknown;
  const roleHints = lifecycle?.role_hints ?? [];
  const rolesTotal = Math.max(nonNegativeNumber(lifecycle?.counts?.roles), roleHints.length);
  const boundRoles = roleHints.filter((role) => role.admission_status === "bound_to_meeting").length;
  const unsafePermissionViolations = roleHints.reduce(
    (total, role) => total + nonNegativeNumber(role.unsafe_permission_violations),
    0
  );
  return {
    state: LIFECYCLE_COPY[lifecycleState] ? lifecycleState : "unknown",
    stepLabel: copy.stepLabel,
    nextAction: copy.nextAction,
    statusSourceLabel: hasLifecycle
      ? lifecycleProjectionStatusSourceLabel(lifecycle?.status_source)
      : lifecycleStatusSourceLabel("missing_state"),
    rolesTotal,
    boundRoles,
    missingRoles: Math.max(0, rolesTotal - boundRoles),
    liveAgents: nonNegativeNumber(lifecycle?.counts?.live_agents),
    pendingTurns: nonNegativeNumber(lifecycle?.counts?.pending_turns),
    officialMessages: nonNegativeNumber(lifecycle?.counts?.official_messages),
    unsafePermissionViolations,
    attentionItems: (lifecycle?.attention ?? [])
      .map((code) => {
        const normalized = String(code || "").trim();
        return {
          code: normalized,
          label: lifecycleAttentionLabel(normalized),
        };
      })
      .filter((item) => item.code.length > 0),
    hasLifecycle,
  };
}
