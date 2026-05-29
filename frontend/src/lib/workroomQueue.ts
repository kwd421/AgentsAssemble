import type { LifecycleProjection, LobbyEvent } from "../api";

export type WorkroomQueueLaneId =
  | "blocked"
  | "review"
  | "official_record"
  | "shared_memory";

export type WorkroomQueueTone = "danger" | "warn" | "accent" | "online" | "muted";

export type WorkroomQueueItem = {
  id: string;
  label: string;
  detail: string;
  tone: WorkroomQueueTone;
  available?: boolean;
};

export type WorkroomQueueLane = {
  id: WorkroomQueueLaneId;
  label: string;
  description: string;
  tone: WorkroomQueueTone;
  count: number;
  total?: number;
  items: WorkroomQueueItem[];
};

export type WorkroomQueueSummary = {
  lanes: WorkroomQueueLane[];
  blockedCount: number;
  reviewCount: number;
  finalArtifactCount: number;
  finalArtifactTotal: number;
  sharedMemoryCount: number;
  sharedMemoryTotal: number;
};

export type WorkroomQueueInput = {
  lifecycle?: LifecycleProjection | null;
  artifacts?: Record<string, boolean | { available?: boolean } | null | undefined>;
  reviewCheckpointCount?: number;
  returnPacketCount?: number;
  taskScope?: {
    available?: boolean;
    summary?: string;
    overlap_count?: number;
    candidate_count_total?: number;
    overlaps?: Array<{
      kind?: string;
      token?: string;
    }>;
    overlaps_truncated?: boolean;
  } | null;
  lobbyEvents?: Pick<LobbyEvent, "official_record" | "kind" | "message">[];
};

const FINAL_ARTIFACTS = [
  {
    path: "transcript.md",
    label: "Transcript",
    detail: "공식 발언 기반 회의록",
  },
  {
    path: "decision.md",
    label: "Decision",
    detail: "결정 게이트 문서",
  },
  {
    path: "shared_memory/rolling-summary.md",
    label: "Rolling summary",
    detail: "장기 회의 맥락 요약",
  },
  {
    path: "shared_memory/action-items.md",
    label: "Action items",
    detail: "공식 기록에서 나온 실행 항목",
  },
  {
    path: "shared_memory/open-questions.md",
    label: "Open questions",
    detail: "다음 회의가 이어받을 질문",
  },
];

const SHARED_MEMORY_ARTIFACTS = FINAL_ARTIFACTS.filter((artifact) =>
  artifact.path.startsWith("shared_memory/")
);

const ATTENTION_LABELS: Record<string, string> = {
  stalled_running_state: "세션 정지 추정",
  malformed: "기록 손상",
};

function isAvailable(value: boolean | { available?: boolean } | null | undefined): boolean {
  if (typeof value === "boolean") return value;
  return Boolean(value?.available);
}

function lifecycleCounts(lifecycle?: LifecycleProjection | null) {
  const roleHints = lifecycle?.role_hints ?? [];
  const rolesTotal = Math.max(lifecycle?.counts?.roles ?? 0, roleHints.length);
  const boundRoles = roleHints.filter(
    (role) => role.admission_status === "bound_to_meeting"
  ).length;
  const unsafePermissions = roleHints.reduce(
    (total, role) => total + Math.max(0, role.unsafe_permission_violations || 0),
    0
  );
  return {
    pendingTurns: Math.max(0, lifecycle?.counts?.pending_turns ?? 0),
    missingRoles: Math.max(0, rolesTotal - boundRoles),
    unsafePermissions,
  };
}

function attentionItems(lifecycle?: LifecycleProjection | null): WorkroomQueueItem[] {
  return (lifecycle?.attention ?? [])
    .map((code) => String(code || "").trim())
    .filter((code) => code && code !== "pending_official_turns")
    .map((code) => ({
      id: `attention:${code}`,
      label: ATTENTION_LABELS[code] || code,
      detail: "라이프사이클 주의 신호가 있습니다.",
      tone: code === "stalled_running_state" || code === "malformed" ? "danger" : "warn",
    }));
}

function artifactItem(
  artifact: (typeof FINAL_ARTIFACTS)[number],
  artifacts: WorkroomQueueInput["artifacts"]
): WorkroomQueueItem {
  const available = isAvailable(artifacts?.[artifact.path]);
  return {
    id: `artifact:${artifact.path}`,
    label: artifact.label,
    detail: available ? `${artifact.detail} 생성됨` : `${artifact.detail} 미생성`,
    tone: available ? "online" : "muted",
    available,
  };
}

function sharedMemoryItem(
  artifact: (typeof SHARED_MEMORY_ARTIFACTS)[number],
  artifacts: WorkroomQueueInput["artifacts"]
): WorkroomQueueItem {
  const available = isAvailable(artifacts?.[artifact.path]);
  return {
    id: artifact.path,
    label: artifact.label,
    detail: available ? "공유 메모리 사용 가능" : "아직 공유 메모리가 비어 있음",
    tone: available ? "online" : "muted",
    available,
  };
}

export function summarizeWorkroomQueue(input: WorkroomQueueInput): WorkroomQueueSummary {
  const { pendingTurns, missingRoles, unsafePermissions } = lifecycleCounts(input.lifecycle);
  const blockedItems: WorkroomQueueItem[] = [];
  if (pendingTurns > 0) {
    blockedItems.push({
      id: "pending_official_turns",
      label: "공식 턴 대기",
      detail: `${pendingTurns}개 공식 턴이 아직 닫히지 않았습니다.`,
      tone: "warn",
    });
  }
  if (missingRoles > 0) {
    blockedItems.push({
      id: "missing_roles",
      label: "미입실 역할",
      detail: `${missingRoles}개 역할의 입장 또는 바인딩이 필요합니다.`,
      tone: "warn",
    });
  }
  if (unsafePermissions > 0) {
    blockedItems.push({
      id: "unsafe_permissions",
      label: "권한 검토",
      detail: `${unsafePermissions}개 권한 신호가 Work Mode 경계와 맞지 않습니다.`,
      tone: "danger",
    });
  }
  blockedItems.push(...attentionItems(input.lifecycle));

  const reviewCheckpointCount = Math.max(0, input.reviewCheckpointCount ?? 0);
  const returnPacketCount = Math.max(0, input.returnPacketCount ?? 0);
  const taskScopeOverlapCount = Math.max(0, input.taskScope?.overlap_count ?? 0);
  const reviewItems: WorkroomQueueItem[] = [];
  if (taskScopeOverlapCount > 0) {
    reviewItems.push({
      id: "task_scope_overlaps",
      label: "작업 범위 충돌",
      detail: `${taskScopeOverlapCount}개 파일/디렉터리 후보가 여러 역할에 겹칩니다. task_scope_report.md를 확인하세요.`,
      tone: "warn",
    });
  }
  if (returnPacketCount > 0 && reviewCheckpointCount === 0) {
    reviewItems.push({
      id: "review_checkpoint_needed",
      label: "리뷰 호출 필요",
      detail: "리턴 패킷이 준비됐지만 리뷰 체크포인트가 아직 없습니다.",
      tone: "warn",
      available: false,
    });
  }
  if (reviewCheckpointCount > 0) {
    reviewItems.push({
      id: "review_checkpoints",
      label: "리뷰 체크포인트",
      detail: `${reviewCheckpointCount}개 체크포인트가 검토 대기 중입니다.`,
      tone: "accent",
    });
  }
  if (returnPacketCount > 0) {
    reviewItems.push({
      id: "return_packets",
      label: "리턴 패킷",
      detail: `${returnPacketCount}개 에이전트 핸드오프가 준비되어 있습니다.`,
      tone: "accent",
    });
  }

  const officialItems = FINAL_ARTIFACTS.map((artifact) =>
    artifactItem(artifact, input.artifacts)
  );
  const finalArtifactCount = officialItems.filter((item) => item.available).length;
  const sharedMemoryItems = SHARED_MEMORY_ARTIFACTS.map((artifact) =>
    sharedMemoryItem(artifact, input.artifacts)
  );
  const sharedMemoryCount = sharedMemoryItems.filter((item) => item.available).length;

  return {
    lanes: [
      {
        id: "blocked",
        label: "막힘",
        description: "회의를 계속하거나 닫기 전에 확인할 상태",
        tone: blockedItems.length ? "warn" : "online",
        count: blockedItems.length,
        items: blockedItems,
      },
      {
        id: "review",
        label: "리뷰",
        description: "승인 전 사람이 읽어야 하는 산출물 묶음",
        tone: reviewItems.length ? "accent" : "muted",
        count: reviewItems.length,
        items: reviewItems,
      },
      {
        id: "official_record",
        label: "공식 기록",
        description: "최종화 후 확인할 transcript, decision, shared memory",
        tone: finalArtifactCount === FINAL_ARTIFACTS.length ? "online" : "warn",
        count: finalArtifactCount,
        total: FINAL_ARTIFACTS.length,
        items: officialItems,
      },
      {
        id: "shared_memory",
        label: "공유 메모리",
        description: "긴 회의를 이어받기 위한 공식 맥락",
        tone: sharedMemoryCount === SHARED_MEMORY_ARTIFACTS.length ? "online" : "warn",
        count: sharedMemoryCount,
        total: SHARED_MEMORY_ARTIFACTS.length,
        items: sharedMemoryItems,
      },
    ],
    blockedCount: blockedItems.length,
    reviewCount: reviewItems.length,
    finalArtifactCount,
    finalArtifactTotal: FINAL_ARTIFACTS.length,
    sharedMemoryCount,
    sharedMemoryTotal: SHARED_MEMORY_ARTIFACTS.length,
  };
}
