import type { LifecycleProjection } from "../api";

type BoardLifecycleCopy = {
  stepLabel: string;
  nextAction: string;
};

export type BoardAttentionTone = "info" | "warn" | "danger";

export type BoardAttentionItem = {
  code: string;
  label: string;
  tone: BoardAttentionTone;
};

export type BoardLifecycleRole = {
  roleId: string;
  displayName: string;
  admissionStatus: string;
  admissionLabel: string;
  permissions: {
    meeting_read: boolean;
    lobby_chat: boolean;
    official_turn: boolean;
    web_search: boolean;
    tool_use: boolean;
  };
  unsafePermissionViolations: number;
};

export type BoardLifecycleSummary = BoardLifecycleCopy & {
  rolesTotal: number;
  boundRoles: number;
  missingRoles: number;
  unsafePermissionViolations: number;
  officialTurnRoles: number;
  toolUseRoles: number;
  webSearchRoles: number;
  pendingTurns: number;
  officialMessages: number;
  attentionItems: BoardAttentionItem[];
  roles: BoardLifecycleRole[];
};

const LIFECYCLE_COPY: Record<string, BoardLifecycleCopy> = {
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
};

const ATTENTION_COPY: Record<string, { label: string; tone: BoardAttentionTone }> = {
  pending_official_turns: { label: "공식 턴 대기", tone: "warn" },
  stalled_running_state: { label: "세션 정지 추정", tone: "danger" },
  malformed: { label: "기록 손상", tone: "danger" },
};

const ADMISSION_LABELS: Record<string, string> = {
  bound_to_meeting: "입장 완료",
  waiting_for_agent: "입장 대기",
  requested: "입장 대기",
  present_unapproved: "미승인 입장",
  missing_binding: "바인딩 없음",
};

function attentionItem(code: string): BoardAttentionItem {
  const normalized = code.trim();
  const copy = ATTENTION_COPY[normalized];
  return {
    code: normalized,
    label: copy?.label || normalized,
    tone: copy?.tone || "info",
  };
}

function admissionLabel(status: string): string {
  const normalized = status.trim();
  return ADMISSION_LABELS[normalized] || normalized || "상태 불명";
}

function safeRolePermissions(role: LifecycleProjection["role_hints"][number]["permissions"]) {
  return {
    meeting_read: Boolean(role.meeting_read),
    lobby_chat: Boolean(role.lobby_chat),
    official_turn: Boolean(role.official_turn),
    web_search: Boolean(role.web_search),
    tool_use: Boolean(role.tool_use),
  };
}

export function summarizeBoardLifecycle(
  lifecycle?: LifecycleProjection | null
): BoardLifecycleSummary {
  const roleHints = lifecycle?.role_hints ?? [];
  const copy = LIFECYCLE_COPY[lifecycle?.state || ""] || LIFECYCLE_COPY.unknown;
  const rolesTotal = Math.max(lifecycle?.counts?.roles ?? 0, roleHints.length);
  const boundRoles = roleHints.filter((role) => role.admission_status === "bound_to_meeting").length;
  const missingRoles = Math.max(0, rolesTotal - boundRoles);
  const unsafePermissionViolations = roleHints.reduce(
    (total, role) => total + Math.max(0, role.unsafe_permission_violations || 0),
    0
  );
  return {
    ...copy,
    rolesTotal,
    boundRoles,
    missingRoles,
    unsafePermissionViolations,
    officialTurnRoles: roleHints.filter((role) => role.permissions.official_turn).length,
    toolUseRoles: roleHints.filter((role) => role.permissions.tool_use).length,
    webSearchRoles: roleHints.filter((role) => role.permissions.web_search).length,
    pendingTurns: lifecycle?.counts?.pending_turns ?? 0,
    officialMessages: lifecycle?.counts?.official_messages ?? 0,
    attentionItems: (lifecycle?.attention ?? [])
      .map((code) => attentionItem(String(code)))
      .filter((item) => item.code.length > 0),
    roles: roleHints.map((role) => ({
      roleId: role.role_id,
      displayName: role.display_name || role.role_id,
      admissionStatus: role.admission_status,
      admissionLabel: admissionLabel(role.admission_status),
      permissions: safeRolePermissions(role.permissions),
      unsafePermissionViolations: Math.max(0, role.unsafe_permission_violations || 0),
    })),
  };
}
