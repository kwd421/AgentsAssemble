// Per-provider permission/sandbox options + fast support, mirrored from the
// backend (agentsassemble/live_agent_frontend_create.py). Used by the member
// detail card to edit an agent's permission/fast after creation. Keep the ids
// and labels in sync with the backend constants.

export type PermissionOption = { id: string; label: string };

const CODEX_PERMISSION_OPTIONS: PermissionOption[] = [
  { id: "read-only", label: "읽기 전용 (읽기·탐색만)" },
  { id: "workspace-write", label: "작업 (작업폴더 쓰기)" },
  { id: "danger-full-access", label: "전체 해제 (위험)" },
];

const CLAUDE_PERMISSION_OPTIONS: PermissionOption[] = [
  { id: "default", label: "기본 (행동마다 확인)" },
  { id: "plan", label: "계획만 (실행 안 함)" },
  { id: "acceptEdits", label: "편집 자동수락" },
  { id: "bypassPermissions", label: "전체 해제 (위험)" },
];

const ANTIGRAVITY_PERMISSION_OPTIONS: PermissionOption[] = [
  { id: "default", label: "기본" },
  { id: "sandbox", label: "샌드박스 (터미널 제한)" },
  { id: "skip-permissions", label: "전체 해제 (위험)" },
];

const PERMISSION_OPTIONS_BY_KIND: Record<string, PermissionOption[]> = {
  codex_live_session: CODEX_PERMISSION_OPTIONS,
  claude_code: CLAUDE_PERMISSION_OPTIONS,
  grok_live_session: CLAUDE_PERMISSION_OPTIONS,
  antigravity_live_session: ANTIGRAVITY_PERMISSION_OPTIONS,
};

// Providers whose CLI exposes a fast toggle (codex --enable fast_mode, claude /fast).
const FAST_SUPPORTED_KINDS = new Set(["codex_live_session", "claude_code"]);

export function permissionOptionsForKind(providerKind?: string): PermissionOption[] {
  return PERMISSION_OPTIONS_BY_KIND[String(providerKind || "")] || [];
}

export function providerSupportsFast(providerKind?: string): boolean {
  return FAST_SUPPORTED_KINDS.has(String(providerKind || ""));
}

// Exec-per-turn providers re-read permission/fast from the room each turn, so an
// edit applies on the next turn with no restart. claude holds one persistent PTY,
// so its permission change only lands on restart.
export function providerAppliesOptionsLive(providerKind?: string): boolean {
  return String(providerKind || "") !== "claude_code";
}
