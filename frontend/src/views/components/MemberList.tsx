import { useEffect, useMemo, useRef, useState } from "react";
import type { MouseEvent as ReactMouseEvent, PointerEvent as ReactPointerEvent } from "react";
import {
  Bot,
  Code2,
  Crown,
  LogOut,
  Play,
  Search,
  ShieldCheck,
  Square,
  Trash2,
  User,
  UserCheck,
  UserMinus,
  Volume2,
  VolumeX,
  X,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import {
  resumeLiveAgentSessionAgent,
  deleteLiveAgentSession,
  expelLiveAgentFromRoom,
  kickRoomMember,
  muteRoomMember,
  stopLiveAgentSessionAgent,
  stopSelfManagedAgent,
  resumeSelfManagedAgent,
  updateLiveAgentSessionAgentTiming,
  updateLiveAgentSessionAgentOptions,
  uploadLobbyAttachment,
  type LiveAgent,
  type LiveAgentProcessGroup,
  type RoomMember,
} from "../../api";
import {
  loadAgentProfileSettings,
  saveAgentProfileSettings,
  type AgentProfileSettings,
} from "../../lib/agentProfileSettings";
import {
  agentMemberSignals,
  agentQuotaWindowSignals,
  agentTruthBadges,
  lastObservedSummary,
  providerExecutionLabel,
  roomContextSummaryBadges,
} from "../../lib/agentLabels";
import {
  canViewAgentQuota,
  type AgentQuotaVisibilityViewer,
} from "../../lib/agentQuotaVisibility";
import {
  findProcessGroupForAgent,
  processGroupCanControlSingleAgent,
  processGroupIndividualControlReason,
  registeredAgentProcessGroupForAgent,
} from "../../lib/liveAgentProcessControls";
import {
  permissionOptionsForKind,
  providerSupportsFast,
} from "../../lib/liveAgentPermissionOptions";
import { participantTypeMeta } from "../../lib/participantTypes";
import { isActivePresence, presenceStatusLabel } from "../../lib/presenceStatus";
import ProviderTruthChips from "./ProviderTruthChips";
import ImageCropper from "./ImageCropper";

export type RoleId = "human" | "director" | "implementer" | "reviewer" | "agent";

type MemberEntry = {
  id: string;
  agent?: LiveAgent;
  member?: RoomMember;
  displayName: string;
  detail: string;
  fullDetail?: string;
  statusLabel?: string;
  role: RoleId;
  owner: boolean;
  ownedByViewer: boolean;
  ownerDisplayName?: string;
  agentDisplayName?: string;
  agentProfile?: AgentProfileSettings;
  avatarImage?: string;
  active: boolean;
  muted: boolean;
  meetingId: string;
  canViewQuota: boolean;
  icon: LucideIcon;
};

const ROLE_OPTIONS: Array<{ id: RoleId; label: string; icon: LucideIcon }> = [
  { id: "human", label: "사람", icon: User },
  { id: "director", label: "디렉터", icon: Crown },
  { id: "implementer", label: "구현", icon: Code2 },
  { id: "reviewer", label: "리뷰어", icon: ShieldCheck },
  { id: "agent", label: "에이전트", icon: Bot },
];

const ROW_POINTER_MOVE_TOLERANCE = 8;
const DEFAULT_AGENT_POLL_INTERVAL_SECONDS = 0.25;

function isPrimaryActivationPointer(event: ReactPointerEvent<HTMLElement>) {
  return event.pointerType !== "mouse" || event.button === 0;
}

function rowTargetIsInteractive(target: EventTarget | null) {
  const element = target instanceof HTMLElement ? target : null;
  return Boolean(element?.closest("button, input, textarea, select, a, [role='dialog']"));
}

function isActive(agent: LiveAgent) {
  return isActivePresence(agent.status);
}

function statusDotClass(status: string) {
  if (status === "working" || status === "running") return "bg-online live-pulse";
  if (status === "online" || status === "ready") return "bg-online";
  if (status === "idle") return "bg-idle";
  if (status === "error") return "bg-danger";
  return "bg-offline";
}

function signalToneClass(tone: "accent" | "online" | "idle" | "danger" | "muted") {
  if (tone === "online") return "online";
  if (tone === "idle") return "idle";
  if (tone === "danger") return "danger";
  if (tone === "muted") return "muted";
  return "accent";
}

function roleStorageKey(roomId: string) {
  return `agentsassemble.roomRoles.${roomId || "default"}`;
}

function inferAgentRole(agent: LiveAgent): RoleId {
  const text = [
    agent.binding_role_id,
    agent.display_name,
    agent.agent_id,
    agent.provider_kind,
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
  if (/(director|moderator|manager|lead|owner|디렉터|총괄|책임자|팀장)/.test(text)) {
    return "director";
  }
  if (/(implement|engineer|developer|builder|coder|cursor|code|구현|개발)/.test(text)) {
    return "implementer";
  }
  if (/(review|critic|qa|xhigh|검토|리뷰)/.test(text)) {
    return "reviewer";
  }
  return "agent";
}

function memberActive(member: RoomMember) {
  return isActivePresence(member.status);
}

function memberRole(member: RoomMember): RoleId {
  return ["human", "director", "implementer", "reviewer", "agent"].includes(member.role)
    ? member.role
    : "agent";
}

function memberStatusLabel(member: RoomMember) {
  return presenceStatusLabel(member.status);
}

function inlineQuotaChips(agent: LiveAgent) {
  const quotaWindows = agentQuotaWindowSignals(agent);
  if (quotaWindows.length > 0) {
    return quotaWindows.slice(0, 2).map((window) => ({
      label: window.label,
      value: window.usageLabel || `${window.percent}%`,
      tone: signalToneClass(window.tone),
      title: window.title,
    }));
  }
  return [
    {
      label: "5h",
      value: String(agent.quota_5h || "").trim() || "—",
      tone: signalToneClass("muted"),
      title: "5-hour usage",
    },
    {
      label: "1w",
      value: String(agent.quota_1w || "").trim() || "—",
      tone: signalToneClass("muted"),
      title: "1-week usage",
    },
  ];
}

function processStatusLabel(status?: string) {
  if (status === "running") return "실행 중";
  if (status === "stopped") return "중지됨";
  if (status === "error") return "오류";
  if (status === "finished") return "종료됨";
  return "상태 미정";
}

function compactPathForDisplay(path?: string, maxSegments = 4) {
  const cleanPath = String(path || "").trim();
  if (!cleanPath) return "";
  const normalized = cleanPath.replace(/\\/g, "/");
  const segments = normalized.split("/").filter(Boolean);
  if (segments.length <= maxSegments) return cleanPath;
  const prefix = normalized.startsWith("/") ? "/" : "";
  return `${prefix}.../${segments.slice(-maxSegments).join("/")}`;
}

function sessionLocationRows(agent: LiveAgent, sessionGroup?: LiveAgentProcessGroup) {
  const rows = [
    {
      label: "프로세스 그룹",
      value: sessionGroup?.group_id || agent.process_group_id || "",
    },
    {
      label: "설정 파일",
      value: sessionGroup?.config_path || agent.live_agent_config_path || "",
      path: true,
    },
    {
      label: "작업 폴더",
      value: agent.workspace_path || "",
      path: true,
    },
    {
      label: "로그 파일",
      value: sessionGroup?.log_path || "",
      path: true,
    },
  ];
  return rows.filter((row) => String(row.value || "").trim());
}

function pollIntervalFromAgent(agent?: LiveAgent) {
  const value = agent?.poll_interval;
  return typeof value === "number" && Number.isFinite(value) && value > 0
    ? value
    : DEFAULT_AGENT_POLL_INTERVAL_SECONDS;
}

function cooldownFromAgent(agent?: LiveAgent) {
  const value = agent?.cooldown;
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : 0;
}

function pollIntervalSecondsText(interval: number) {
  return String(interval > 0 ? interval : DEFAULT_AGENT_POLL_INTERVAL_SECONDS);
}

function secondsText(value: number) {
  return String(Number.isFinite(value) && value >= 0 ? value : 0);
}

function parsePositiveSeconds(secondsText: string): number | null {
  const parsed = Number(secondsText);
  if (!Number.isFinite(parsed) || parsed <= 0) return null;
  return parsed;
}

function parseNonnegativeSeconds(secondsText: string): number | null {
  const parsed = Number(secondsText);
  if (!Number.isFinite(parsed) || parsed < 0) return null;
  return parsed;
}

function pollIntervalLabel(interval: number | null) {
  if (interval === null) return "확인 필요";
  return `${interval}s`;
}

function cooldownLabel(cooldown: number | null) {
  if (cooldown === null) return "확인 필요";
  return cooldown <= 0 ? "없음" : `${cooldown}s`;
}

function agentExecutionMode(
  agent?: LiveAgent
): "baseline" | "runtime" | "tool_loop" | "tool_loop_unverified" | "persistent" | "manual" | "unknown" {
  const mode = String(agent?.execution_mode || "").trim();
  if (mode === "baseline_call_resume" || mode === "call" || mode === "call_resume") return "baseline";
  if (mode === "runtime_managed_room_turn") return "runtime";
  if (mode === "provider_tool_loop") return "tool_loop";
  if (mode === "tool_loop_unverified") return "tool_loop_unverified";
  if (mode === "persistent" || mode === "provider_persistent") return "persistent";
  if (mode === "manual") return mode;
  const join = String(agent?.join_semantics || "").trim();
  if (join === "runtime_managed_room_turn") return "runtime";
  if (
    [
      "mcp_tool_loop",
      "cli_tool_loop",
    ].includes(join)
  ) {
    return "tool_loop";
  }
  if (
    [
      "provider_tool_loop",
      "self_service_room_loop",
      "remote_bridge_room_loop",
      "native_remote_room_loop",
    ].includes(join)
  ) {
    return "tool_loop_unverified";
  }
  if (
    [
      "codex_exec_resume",
      "kiro_chat_resume",
      "antigravity_conversation_resume",
      "cursor_chat_resume",
      "grok_session_resume",
      "hermes_chat_resume",
      "stateless_prompt_call",
    ].includes(join)
  ) {
    return "baseline";
  }
  if (
    [
      "terminal_pty_prompt_bridge",
      "jsonl_live_session",
    ].includes(join)
  ) {
    return "persistent";
  }
  if (join === "manual_room_loop") return "manual";
  return "unknown";
}

function persistentModeAvailable(agent?: LiveAgent) {
  return agentExecutionMode(agent) === "persistent" && agent?.provider_persistent !== false;
}

function callModeAvailable(agent?: LiveAgent) {
  const mode = agentExecutionMode(agent);
  return mode === "baseline" || mode === "runtime" || mode === "tool_loop" || mode === "persistent";
}

function executionModeSummary(agent?: LiveAgent) {
  const mode = agentExecutionMode(agent);
  if (mode === "baseline") {
    return "현재 이 AI는 baseline 호출형입니다. 방 runner는 살아 있지만 provider 답변은 매번 exec/resume으로 호출합니다.";
  }
  if (mode === "runtime") {
    return "현재 이 AI는 runtime-managed입니다. runtime이 방 API에서 턴을 받아 provider를 호출하고 답장을 방에 게시합니다.";
  }
  if (mode === "tool_loop") {
    return "현재 이 AI는 provider tool-loop입니다. provider가 wait-next/read-since/say 도구로 방에 직접 참여하는 구조입니다.";
  }
  if (mode === "tool_loop_unverified") {
    return agent?.tool_loop_unverified_reason || "tool-loop가 요청됐지만 이 provider의 MCP/CLI 참여 경로가 아직 검증되지 않았습니다.";
  }
  if (mode === "persistent") {
    return "현재 이 AI는 상주형입니다. provider 프로세스/PTY/스트림/room loop가 실행 중인 동안 계속 붙어 있습니다.";
  }
  if (mode === "manual") {
    return "수동 참가자입니다. AgentsAssemble이 provider 실행을 제어하지 않습니다.";
  }
  return "실행 방식이 아직 증명되지 않았습니다.";
}

function MemberRow({
  entry,
  onOpenDetails,
  onRoleChange,
  onContextMenu,
  canEditRoles,
}: {
  entry: MemberEntry;
  onOpenDetails: (entry: MemberEntry) => void;
  onRoleChange: (memberId: string, role: RoleId) => void;
  onContextMenu: (entry: MemberEntry, event: ReactMouseEvent<HTMLElement>) => void;
  canEditRoles: boolean;
}) {
  const Icon = entry.icon;
  const pointerStartRef = useRef<{ x: number; y: number } | null>(null);
  const quotaChips = entry.agent && entry.canViewQuota ? inlineQuotaChips(entry.agent) : [];
  const roleLabel = ROLE_OPTIONS.find((option) => option.id === entry.role)?.label || "에이전트";

  function openDetails() {
    if (entry.agent) onOpenDetails(entry);
  }

  function handlePointerDown(event: ReactPointerEvent<HTMLDivElement>) {
    if (!entry.agent || !isPrimaryActivationPointer(event) || rowTargetIsInteractive(event.target)) {
      pointerStartRef.current = null;
      return;
    }
    pointerStartRef.current = { x: event.clientX, y: event.clientY };
  }

  function handlePointerUp(event: ReactPointerEvent<HTMLDivElement>) {
    if (!entry.agent || !isPrimaryActivationPointer(event) || rowTargetIsInteractive(event.target)) return;
    const pointerStart = pointerStartRef.current;
    pointerStartRef.current = null;
    if (!pointerStart) return;
    const movedX = Math.abs(event.clientX - pointerStart.x);
    const movedY = Math.abs(event.clientY - pointerStart.y);
    if (movedX > ROW_POINTER_MOVE_TOLERANCE || movedY > ROW_POINTER_MOVE_TOLERANCE) return;
    openDetails();
  }

  function handlePointerCancel() {
    pointerStartRef.current = null;
  }

  return (
    <div
      className="dc-member group"
      data-role={entry.role}
      data-active={entry.active}
      role={entry.agent ? "button" : undefined}
      tabIndex={entry.agent ? 0 : undefined}
      data-muted={entry.muted}
      onPointerDown={handlePointerDown}
      onPointerUp={handlePointerUp}
      onPointerCancel={handlePointerCancel}
      onContextMenu={(event) => onContextMenu(entry, event)}
      onClick={(event) => {
        if (rowTargetIsInteractive(event.target)) return;
        openDetails();
      }}
      onKeyDown={(event) => {
        if (!entry.agent) return;
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          openDetails();
        }
      }}
    >
      <span className="relative shrink-0">
        <span className="dc-member-avatar">
          {entry.avatarImage ? (
            <img className="dc-member-avatar-image" src={entry.avatarImage} alt="" />
          ) : (
            <Icon size={15} />
          )}
        </span>
        <span
          className={`absolute -bottom-0.5 -right-0.5 h-3 w-3 rounded-full border-2 border-sidebar ${
            statusDotClass(entry.agent?.status || entry.member?.status || "online")
          }`}
          aria-hidden
        />
      </span>
      <div className="min-w-0 flex-1">
        <div className="dc-member-name-row">
          <p className="dc-member-name truncate preserve-words">
            {entry.displayName}
          </p>
          {entry.owner && (
            // canEditRoles is only true for the room host's own view, so the
            // host sees HOST on themselves while guests see YOU on themselves.
            <span
              className="rounded bg-accent/20 px-1 py-0.5 text-[9px] font-black text-accent"
              title={canEditRoles ? "이 방의 호스트(방장)" : "나"}
            >
              {canEditRoles ? "HOST" : "YOU"}
            </span>
          )}
          {entry.muted && (
            <span
              className="dc-member-muted-badge"
              title={`${entry.displayName}은(는) 뮤트되어 발언할 수 없습니다`}
              aria-label="뮤트됨"
            >
              <VolumeX size={11} />
            </span>
          )}
          {quotaChips.length > 0 && (
            <span className="dc-member-inline-quota" aria-label={`${entry.displayName} 사용량`}>
              {quotaChips.map((chip) => (
                <span key={`${chip.label}-${chip.value}`} data-tone={chip.tone} title={chip.title}>
                  <b>{chip.label}</b> {chip.value}
                </span>
              ))}
            </span>
          )}
        </div>
        <div className="dc-member-detail-row">
          <p className="min-w-0 flex-1 truncate preserve-words" title={entry.fullDetail || entry.detail}>
            {entry.detail}
          </p>
          {entry.member && entry.statusLabel && (
            <span
              className="dc-member-status-chip preserve-words"
              data-state={entry.member.status === "pending" ? "attention" : entry.active ? "active" : "idle"}
            >
              {entry.statusLabel}
            </span>
          )}
        </div>
        <div className="dc-member-role-row">
          {canEditRoles ? (
            <select
              className="dc-role-select"
              value={entry.role}
              aria-label={`${entry.displayName} 역할`}
              onClick={(event) => event.stopPropagation()}
              onKeyDown={(event) => event.stopPropagation()}
              onChange={(event) => onRoleChange(entry.id, event.target.value as RoleId)}
            >
              {ROLE_OPTIONS.map((option) => (
                <option key={option.id} value={option.id}>
                  {option.label}
                </option>
              ))}
            </select>
          ) : (
            <span className="dc-role-label">{roleLabel}</span>
          )}
        </div>
      </div>
    </div>
  );
}

function MemberDetailModal({
  entry,
  onClose,
  processGroups = [],
  onSessionActionComplete,
  onAgentProfileSettingsChange,
}: {
  entry: MemberEntry;
  onClose: () => void;
  processGroups?: LiveAgentProcessGroup[];
  onSessionActionComplete?: () => void;
  onAgentProfileSettingsChange?: (settings: Record<string, AgentProfileSettings>) => void;
}) {
  const [sessionActionBusy, setSessionActionBusy] = useState(false);
  const [sessionActionStatus, setSessionActionStatus] = useState("");
  const initialPollInterval = pollIntervalFromAgent(entry.agent);
  const [pollIntervalSeconds, setPollIntervalSeconds] = useState(
    pollIntervalSecondsText(initialPollInterval)
  );
  const initialCooldown = cooldownFromAgent(entry.agent);
  const [cooldownSeconds, setCooldownSeconds] = useState(secondsText(initialCooldown));
  const [pollIntervalBusy, setPollIntervalBusy] = useState(false);
  const [pollIntervalStatus, setPollIntervalStatus] = useState("");
  const [agentNameDraft, setAgentNameDraft] = useState(entry.agentProfile?.displayName || entry.agentDisplayName || "");
  const [agentAvatarImage, setAgentAvatarImage] = useState(entry.avatarImage || "");
  const [agentProfileCropFile, setAgentProfileCropFile] = useState<File | null>(null);
  const [agentProfileStatus, setAgentProfileStatus] = useState("");
  const [permissionDraft, setPermissionDraft] = useState(entry.agent?.permission_option || "");
  const [fastModeDraft, setFastModeDraft] = useState(Boolean(entry.agent?.fast_mode));
  const [optionsBusy, setOptionsBusy] = useState(false);
  const [optionsStatus, setOptionsStatus] = useState("");

  useEffect(() => {
    const nextPollInterval = pollIntervalFromAgent(entry.agent);
    setPollIntervalSeconds(pollIntervalSecondsText(nextPollInterval));
    setCooldownSeconds(secondsText(cooldownFromAgent(entry.agent)));
    setPollIntervalStatus("");
  }, [entry.agent?.agent_id, entry.agent?.poll_interval, entry.agent?.cooldown]);

  useEffect(() => {
    setAgentNameDraft(entry.agentProfile?.displayName || entry.agentDisplayName || "");
    setAgentAvatarImage(entry.avatarImage || "");
    setAgentProfileStatus("");
  }, [entry.agent?.agent_id, entry.agentDisplayName, entry.agentProfile?.displayName, entry.avatarImage]);

  if (!entry.agent) return null;
  const agent = entry.agent;
  const DetailIcon = entry.icon;
  const quotaWindows = entry.canViewQuota ? agentQuotaWindowSignals(entry.agent) : [];
  const quotaFallback = entry.canViewQuota ? inlineQuotaChips(agent) : [];
  const signals = agentMemberSignals(entry.agent).filter((signal) => !/^5h |^1w /.test(signal.label));
  const lastObserved = lastObservedSummary(entry.agent);
  const processIdentity = {
    agent_id: agent.agent_id,
    display_name: agent.display_name,
  };
  const processGroup = findProcessGroupForAgent(processGroups, processIdentity);
  const registeredSessionGroup = processGroup ? undefined : registeredAgentProcessGroupForAgent(agent);
  const sessionGroup = processGroup || registeredSessionGroup;
  const sessionIsRegisteredOnly = Boolean(registeredSessionGroup);
  const canControlSingleAgent = processGroupCanControlSingleAgent(sessionGroup, processIdentity);
  const processOwnsAgent = Boolean(sessionGroup);
  const individualControlReason = processGroupIndividualControlReason(
    sessionGroup,
    processIdentity,
    entry.displayName || "이 AI"
  );
  const processRunning = sessionGroup?.status === "running";
  const showIndividualControlReason = Boolean(individualControlReason && processRunning);
  const resumeActionLabel = sessionIsRegisteredOnly ? "START" : "RESUME";
  const hasResumeControl = Boolean(
    sessionGroup &&
      processOwnsAgent &&
      sessionGroup.group_id &&
      sessionGroup.meeting_id &&
      sessionGroup.config_path &&
      !processRunning
  );
  const hasStopControl = Boolean(
    sessionGroup &&
      canControlSingleAgent &&
      sessionGroup.group_id &&
      sessionGroup.meeting_id &&
      processRunning
  );
  const locationRows = sessionLocationRows(agent, sessionGroup);
  const hasSessionLocation = locationRows.length > 0;
  const hasSessionSection = Boolean(
    hasSessionLocation || hasResumeControl || hasStopControl || showIndividualControlReason
  );
  const hasTimingControl = Boolean(
    sessionGroup &&
      processOwnsAgent &&
      sessionGroup.group_id &&
      sessionGroup.meeting_id &&
      sessionGroup.config_path
  );
  const hasRoomAdminControl = Boolean(agent.agent_id && agent.meeting_id);
  const pollIntervalValue = parsePositiveSeconds(pollIntervalSeconds);
  const cooldownValue = parseNonnegativeSeconds(cooldownSeconds);
  const canResumeSession = Boolean(
    hasResumeControl
  );
  const canStopSession = Boolean(
    hasStopControl
  );
  // Self-managed agents (e.g. launched from a terminal) advertise their own pid +
  // relaunch recipe, so the room can really STOP (kill) and RESUME (relaunch) them
  // even though the server didn't spawn them. Only when not server-owned + mine.
  const selfRelaunchPid = Number(agent.relaunch_pid || 0);
  const selfRelaunchArgv = Array.isArray(agent.relaunch_argv)
    ? agent.relaunch_argv.filter(Boolean)
    : [];
  const isAgentOnline = isActivePresence(agent.status);
  const selfManaged = Boolean(
    entry.ownedByViewer && !sessionGroup && (selfRelaunchPid > 0 || selfRelaunchArgv.length > 0)
  );
  const canSelfStop = Boolean(selfManaged && isAgentOnline && selfRelaunchPid > 0);
  const canSelfResume = Boolean(selfManaged && !isAgentOnline && selfRelaunchArgv.length > 0);
  const canEditAgentProfile = entry.ownedByViewer;
  // Edit the agent's provider-native permission + fast toggle after creation.
  const permissionOptions = permissionOptionsForKind(agent.provider_kind);
  const supportsFast = providerSupportsFast(agent.provider_kind);
  const optionsConfigPath = agent.live_agent_config_path || sessionGroup?.config_path || "";
  const canEditAgentOptions = Boolean(
    canEditAgentProfile && (permissionOptions.length > 0 || supportsFast)
  );
  const optionsDirty =
    permissionDraft !== (agent.permission_option || "") ||
    fastModeDraft !== Boolean(agent.fast_mode);
  const executionMode = agentExecutionMode(agent);
  const canUseCallMode = callModeAvailable(agent);
  const canUsePersistentMode = persistentModeAvailable(agent);
  const executionSummary = executionModeSummary(agent);

  async function handleAgentAvatarCropped(file: File) {
    setAgentProfileStatus("프로필 사진 저장 중...");
    try {
      const attachment = await uploadLobbyAttachment(file);
      setAgentAvatarImage(attachment.url);
      setAgentProfileCropFile(null);
      setAgentProfileStatus("프로필 사진 준비됨");
    } catch (error) {
      setAgentProfileStatus(error instanceof Error ? error.message : "프로필 사진 저장 실패");
    }
  }

  function handleSaveAgentProfile() {
    const nextProfiles = saveAgentProfileSettings(agent.agent_id, {
      displayName: agentNameDraft,
      avatarImage: agentAvatarImage,
    });
    onAgentProfileSettingsChange?.(nextProfiles);
    setAgentProfileStatus("에이전트 프로필 저장됨");
  }

  async function handleSaveAgentOptions() {
    if (!canEditAgentOptions || optionsBusy) return;
    setOptionsBusy(true);
    setOptionsStatus("권한/속도 저장 중...");
    try {
      const response = await updateLiveAgentSessionAgentOptions({
        agentId: agent.agent_id,
        liveAgentConfigPath: optionsConfigPath || undefined,
        ...(permissionOptions.length > 0 ? { permissionOption: permissionDraft } : {}),
        ...(supportsFast ? { fastMode: fastModeDraft } : {}),
      });
      setOptionsStatus(
        response.config_path
          ? "저장됨 · 다음 시작/재시작부터 적용"
          : "저장됨 (방 기록만) · 다음 시작부터 적용"
      );
      onSessionActionComplete?.();
    } catch (error) {
      setOptionsStatus(error instanceof Error ? error.message : "권한/속도 저장 실패");
    } finally {
      setOptionsBusy(false);
    }
  }

  async function handleSelfStop() {
    if (!canSelfStop) return;
    setSessionActionBusy(true);
    setSessionActionStatus("STOP(종료) 요청 중...");
    try {
      await stopSelfManagedAgent({ agentId: agent.agent_id });
      setSessionActionStatus("STOP 완료 · 프로세스 종료됨");
      onSessionActionComplete?.();
    } catch (error) {
      setSessionActionStatus(error instanceof Error ? error.message : "STOP 실패");
    } finally {
      setSessionActionBusy(false);
    }
  }

  async function handleSelfResume() {
    if (!canSelfResume) return;
    setSessionActionBusy(true);
    setSessionActionStatus("RESUME(재실행) 요청 중...");
    try {
      await resumeSelfManagedAgent({ agentId: agent.agent_id });
      setSessionActionStatus("RESUME 완료 · 서버 백그라운드로 재실행됨");
      onSessionActionComplete?.();
    } catch (error) {
      setSessionActionStatus(error instanceof Error ? error.message : "RESUME 실패");
    } finally {
      setSessionActionBusy(false);
    }
  }

  async function handleResumeSession() {
    if (!sessionGroup || !canResumeSession) return;
    setSessionActionBusy(true);
    setSessionActionStatus(`${resumeActionLabel} 요청 중...`);
    try {
      const response = await resumeLiveAgentSessionAgent({
        meetingId: sessionGroup.meeting_id,
        groupId: sessionGroup.group_id,
        agentId: agent.agent_id,
        liveAgentConfigPath: sessionGroup.config_path,
      });
      setSessionActionStatus(
        `${resumeActionLabel} 완료${response.status ? ` · ${processStatusLabel(response.status)}` : ""}`
      );
      onSessionActionComplete?.();
    } catch (error) {
      setSessionActionStatus(error instanceof Error ? error.message : "RESUME 실패");
    } finally {
      setSessionActionBusy(false);
    }
  }

  async function handleStopSession() {
    if (!sessionGroup || !canStopSession) return;
    setSessionActionBusy(true);
    setSessionActionStatus("STOP(KILL) 요청 중...");
    try {
      const response = await stopLiveAgentSessionAgent({
        meetingId: sessionGroup.meeting_id,
        groupId: sessionGroup.group_id,
        agentId: agent.agent_id,
      });
      setSessionActionStatus(
        `STOP(KILL) 완료${response.status ? ` · ${processStatusLabel(response.status)}` : ""}`
      );
      onSessionActionComplete?.();
    } catch (error) {
      setSessionActionStatus(error instanceof Error ? error.message : "STOP(KILL) 실패");
    } finally {
      setSessionActionBusy(false);
    }
  }

  async function handleExpelAgent() {
    const meetingId = sessionGroup?.meeting_id || agent.meeting_id;
    if (!meetingId || !agent.agent_id) return;
    if (!window.confirm(`${entry.displayName}을 이 방에서 추방할까요? 세션 설정은 유지됩니다.`)) return;
    setSessionActionBusy(true);
    setSessionActionStatus("추방 요청 중...");
    try {
      await expelLiveAgentFromRoom({
        meetingId,
        groupId: sessionGroup?.group_id || agent.process_group_id,
        agentId: agent.agent_id,
      });
      setSessionActionStatus("추방 완료");
      onSessionActionComplete?.();
      onClose();
    } catch (error) {
      setSessionActionStatus(error instanceof Error ? error.message : "추방 실패");
    } finally {
      setSessionActionBusy(false);
    }
  }

  async function handleDeleteAgentSession() {
    const meetingId = sessionGroup?.meeting_id || agent.meeting_id;
    if (!meetingId || !agent.agent_id) return;
    const confirmed = window.confirm(
      `${entry.displayName} 세션을 삭제합니다. 방에서 제거되고 실행 중이면 중지되며 저장된 세션 설정도 삭제됩니다. 계속할까요?`
    );
    if (!confirmed) return;
    setSessionActionBusy(true);
    setSessionActionStatus("세션 삭제 요청 중...");
    try {
      await deleteLiveAgentSession({
        meetingId,
        groupId: sessionGroup?.group_id || agent.process_group_id,
        agentId: agent.agent_id,
      });
      setSessionActionStatus("세션 삭제 완료");
      onSessionActionComplete?.();
      onClose();
    } catch (error) {
      setSessionActionStatus(error instanceof Error ? error.message : "세션 삭제 실패");
    } finally {
      setSessionActionBusy(false);
    }
  }

  async function handleUpdateTiming() {
    if (!sessionGroup || !hasTimingControl || pollIntervalValue === null || cooldownValue === null) {
      setPollIntervalStatus("호출 간격은 0보다 크고, 쿨다운은 0 이상의 숫자로 입력하세요");
      return;
    }
    setPollIntervalBusy(true);
    setPollIntervalStatus("저장 중...");
    try {
      const response = await updateLiveAgentSessionAgentTiming({
        meetingId: sessionGroup.meeting_id,
        groupId: sessionGroup.group_id,
        agentId: agent.agent_id,
        liveAgentConfigPath: sessionGroup.config_path,
        pollInterval: pollIntervalValue,
        cooldown: cooldownValue,
      });
      const applied =
        typeof response.poll_interval === "number" && Number.isFinite(response.poll_interval)
          ? response.poll_interval
          : pollIntervalValue;
      const appliedCooldown =
        typeof response.cooldown === "number" && Number.isFinite(response.cooldown)
          ? response.cooldown
          : cooldownValue;
      setPollIntervalStatus(
        `저장됨 · 호출 ${pollIntervalLabel(applied)} · 쿨다운 ${cooldownLabel(appliedCooldown)}`
      );
      onSessionActionComplete?.();
    } catch (error) {
      setPollIntervalStatus(error instanceof Error ? error.message : "호출 간격 저장 실패");
    } finally {
      setPollIntervalBusy(false);
    }
  }

  return (
    <div className="dc-modal-backdrop" role="presentation" onClick={onClose}>
      <section
        className="dc-member-detail-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="member-detail-title"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="dc-member-detail-modal-head">
          <span className="dc-member-detail-modal-avatar" data-role={entry.role}>
            {entry.avatarImage ? (
              <img className="dc-member-avatar-image" src={entry.avatarImage} alt="" />
            ) : (
              <DetailIcon size={22} />
            )}
          </span>
          <div className="min-w-0 flex-1">
            <h2 id="member-detail-title" className="truncate preserve-words">
              {entry.displayName}
            </h2>
            <p className="truncate preserve-words">{entry.fullDetail || entry.detail}</p>
          </div>
          <button type="button" className="dc-modal-close" onClick={onClose} aria-label="멤버 정보 닫기">
            <X size={18} />
          </button>
        </header>
        <section className="dc-member-detail-section" aria-label={`${entry.displayName} 사용량`}>
          <h3>사용량</h3>
          {!entry.canViewQuota ? (
            <p className="dc-member-detail-note preserve-words">
              사용량은 이 AI를 소유한 참가자에게만 표시됩니다.
            </p>
          ) : quotaWindows.length > 0 ? (
            <div className="dc-member-quota-row">
              {quotaWindows.map((window) => (
                <span
                  key={`${window.label}-${window.percent}`}
                  className="dc-member-quota-window"
                  data-tone={signalToneClass(window.tone)}
                  title={window.title}
                  aria-label={window.title}
                >
                  <span className="dc-member-quota-label preserve-words">{window.label}</span>
                  <span className="dc-member-quota-bar" aria-hidden>
                    <span style={{ width: `${window.percent}%` }} />
                  </span>
                  <span className="dc-member-quota-percent">{window.percent}%</span>
                </span>
              ))}
            </div>
          ) : (
            <div className="dc-member-quota-fallback">
              {quotaFallback.map((chip) => (
                <span key={`${chip.label}-${chip.value}`} data-tone={chip.tone} title={chip.title}>
                  <b>{chip.label}</b>
                  {chip.value}
                </span>
              ))}
            </div>
          )}
        </section>
        <section className="dc-member-detail-section" aria-label={`${entry.displayName} 세션 상태`}>
          <h3>연결 상태</h3>
          <div className="dc-member-signal-row">
            {signals.map((signal) => (
              <span
                key={signal.label}
                className="dc-member-signal preserve-words"
                data-tone={signalToneClass(signal.tone)}
                title={signal.title || signal.label}
              >
                {signal.label}
              </span>
            ))}
          </div>
          <ProviderTruthChips badges={agentTruthBadges(entry.agent)} compact limit={4} />
          {lastObserved && <p className="dc-member-detail-note preserve-words">{lastObserved}</p>}
        </section>
        {canEditAgentProfile && (
          <section className="dc-member-detail-section" aria-label={`${entry.displayName} 에이전트 프로필`}>
            <h3>에이전트 프로필</h3>
            <label className="dc-agent-profile-field">
              이름
              <input
                type="text"
                maxLength={80}
                value={agentNameDraft}
                onChange={(event) => setAgentNameDraft(event.currentTarget.value)}
                placeholder={agent.display_name || agent.agent_id}
              />
            </label>
            <div className="dc-agent-profile-avatar-row">
              <span className="dc-member-avatar dc-agent-profile-preview">
                {agentAvatarImage ? (
                  <img className="dc-member-avatar-image" src={agentAvatarImage} alt="" />
                ) : (
                  <DetailIcon size={18} />
                )}
              </span>
              <label className="dc-member-session-button">
                프로필 사진 편집
                <input
                  className="sr-only"
                  type="file"
                  accept="image/*"
                  onChange={(event) => {
                    const file = event.currentTarget.files?.[0] || null;
                    if (file) setAgentProfileCropFile(file);
                    event.currentTarget.value = "";
                  }}
                />
              </label>
              <button type="button" className="dc-member-session-button" onClick={handleSaveAgentProfile}>
                저장
              </button>
            </div>
            {agentProfileCropFile && (
              <ImageCropper
                file={agentProfileCropFile}
                onCancel={() => setAgentProfileCropFile(null)}
                onCropped={(file) => void handleAgentAvatarCropped(file)}
              />
            )}
            {agentProfileStatus && (
              <p className="dc-member-session-status preserve-words">{agentProfileStatus}</p>
            )}
          </section>
        )}
        {canEditAgentOptions && (
          <section className="dc-member-detail-section" aria-label={`${entry.displayName} 권한·속도`}>
            <h3>권한 / 속도</h3>
            {permissionOptions.length > 0 && (
              <label className="dc-agent-profile-field">
                권한
                <select
                  value={permissionDraft}
                  onChange={(event) => setPermissionDraft(event.currentTarget.value)}
                >
                  {permissionOptions.map((option) => (
                    <option key={option.id} value={option.id}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {supportsFast && (
              <label className="dc-agent-start-toggle">
                <input
                  type="checkbox"
                  checked={fastModeDraft}
                  onChange={(event) => setFastModeDraft(event.currentTarget.checked)}
                />
                <span>빠른 모드 (fast)</span>
              </label>
            )}
            <button
              type="button"
              className="dc-member-session-button"
              onClick={() => void handleSaveAgentOptions()}
              disabled={optionsBusy || !optionsDirty}
            >
              저장
            </button>
            <p className="dc-member-detail-note preserve-words">
              변경은 다음 시작/재시작부터 적용됩니다. (실행 중인 세션은 재시작 전까지 기존 설정 유지)
            </p>
            {optionsStatus && (
              <p className="dc-member-session-status preserve-words">{optionsStatus}</p>
            )}
          </section>
        )}
        {hasSessionSection && (
          <section className="dc-member-detail-section" aria-label={`${entry.displayName} 세션 제어`}>
            <h3>세션 제어</h3>
            {hasSessionLocation ? (
              <div className="dc-member-session-location" aria-label={`${entry.displayName} 세션 위치`}>
                <div className="dc-member-session-location-head">
                  <span>세션 위치</span>
                  <span>{processStatusLabel(sessionGroup?.status)}</span>
                </div>
                <dl>
                  {locationRows.map((row) => {
                    const value = String(row.value || "");
                    return (
                      <div key={row.label}>
                        <dt>{row.label}</dt>
                        <dd title={value}>{row.path ? compactPathForDisplay(value) : value}</dd>
                      </div>
                    );
                  })}
                </dl>
              </div>
            ) : (
              <p className="dc-member-session-summary preserve-words">
                세션 위치 기록 없음 · {processStatusLabel(sessionGroup?.status)}
              </p>
            )}
            <div className="dc-member-execution-mode" aria-label={`${entry.displayName} 실행 방식`}>
              <div className="dc-member-execution-mode-head">
                <span>실행 방식</span>
                <span>{providerExecutionLabel(agent)}</span>
              </div>
              <div className="dc-member-execution-options" role="radiogroup" aria-label="에이전트 실행 방식">
                <button
                  type="button"
                  className="dc-member-execution-option"
                  role="radio"
                  aria-checked={executionMode === "baseline"}
                  data-active={executionMode === "baseline"}
                  disabled={!canUseCallMode}
                  title="방 runner와 턴 스케줄러는 살아 있지만 provider는 매 답변마다 exec/resume으로 호출합니다."
                >
                  baseline 호출형
                </button>
                <button
                  type="button"
                  className="dc-member-execution-option"
                  role="radio"
                  aria-checked={executionMode === "runtime"}
                  data-active={executionMode === "runtime"}
                  disabled
                  title="1번 비교 구조입니다. runtime이 방 API에서 턴을 받아 provider를 호출합니다."
                >
                  runtime-managed
                </button>
                <button
                  type="button"
                  className="dc-member-execution-option"
                  role="radio"
                  aria-checked={executionMode === "tool_loop"}
                  data-active={executionMode === "tool_loop"}
                  disabled
                  title="3번 비교 구조입니다. provider가 wait-next/read-since/say 도구로 방에 참여합니다."
                >
                  provider tool-loop
                </button>
                <button
                  type="button"
                  className="dc-member-execution-option"
                  role="radio"
                  aria-checked={executionMode === "tool_loop_unverified"}
                  data-active={executionMode === "tool_loop_unverified"}
                  disabled
                  title="tool-loop가 요청됐지만 이 provider의 MCP/CLI 참여 경로가 아직 검증되지 않았습니다."
                >
                  미검증
                </button>
                <button
                  type="button"
                  className="dc-member-execution-option"
                  role="radio"
                  aria-checked={executionMode === "persistent"}
                  data-active={executionMode === "persistent"}
                  disabled={!canUsePersistentMode}
                  title={
                    canUsePersistentMode
                      ? "provider 프로세스/PTY/스트림이 계속 붙어 있는 진짜 상주형입니다."
                      : "이 provider는 아직 persistent bridge PoC가 통과하지 않아 선택할 수 없습니다."
                  }
                >
                  빠른 provider 상주형
                </button>
              </div>
              <p className="dc-member-detail-note preserve-words">
                {executionSummary}
              </p>
              <p className="dc-member-detail-note preserve-words">
                baseline 호출형은 유휴 자원이 적지만 느릴 수 있습니다. Codex 5.3 Spark 실측은 4-6초였고,
                원인은 polling/cooldown이 아니라 Codex exec/resume 호출 비용입니다.
              </p>
              <p className="dc-member-detail-note preserve-words">
                runtime-managed는 1번 비교 구조이고, provider tool-loop는 3번 비교 구조입니다. 둘 다 실검증 결과를 문서에 남겨야 선택할 수 있습니다.
                미검증은 MCP/CLI tool-loop 확인이 아직 없는 상태입니다. 빠른 provider 상주형은 persistent bridge PoC가 통과한 provider에서만 활성화합니다.
              </p>
              <p className="dc-member-session-status preserve-words">
                runner: {agent.runner_residency || "unknown"} · provider: {agent.provider_residency || "unknown"}
              </p>
            </div>
            {hasResumeControl || hasStopControl ? (
              <div className="dc-member-session-actions">
                {hasResumeControl && (
                  <button
                    type="button"
                    className="dc-member-session-button"
                    disabled={!canResumeSession || sessionActionBusy}
                    onClick={handleResumeSession}
                  >
                    <Play size={15} />
                    {resumeActionLabel}
                  </button>
                )}
                {hasStopControl && (
                  <button
                    type="button"
                    className="dc-member-session-button"
                    data-variant="danger"
                    disabled={!canStopSession || sessionActionBusy}
                    onClick={handleStopSession}
                  >
                    <Square size={14} />
                    STOP(KILL)
                  </button>
                )}
              </div>
            ) : (
              <p className="dc-member-detail-note preserve-words">{individualControlReason}</p>
            )}
            {sessionActionStatus && (
              <p className="dc-member-session-status preserve-words">{sessionActionStatus}</p>
            )}
          </section>
        )}
        {hasTimingControl && (
          <section className="dc-member-detail-section" aria-label={`${entry.displayName} 응답 타이밍`}>
            <h3>응답 타이밍</h3>
            <div className="dc-member-session-actions">
              <label className="min-w-0 flex-1 text-[11px] font-bold text-text-muted">
                확인 주기
                <input
                  className="mt-1 w-full rounded border border-line bg-black/20 px-2 py-2 text-[12px] text-text-primary outline-none transition focus:border-accent focus:ring-2 focus:ring-accent/30 disabled:opacity-45"
                  type="number"
                  min="0.05"
                  step="0.05"
                  inputMode="decimal"
                  disabled={pollIntervalBusy}
                  value={pollIntervalSeconds}
                  onChange={(event) => setPollIntervalSeconds(event.target.value)}
                />
              </label>
              <label className="min-w-0 flex-1 text-[11px] font-bold text-text-muted">
                쿨다운
                <input
                  className="mt-1 w-full rounded border border-line bg-black/20 px-2 py-2 text-[12px] text-text-primary outline-none transition focus:border-accent focus:ring-2 focus:ring-accent/30 disabled:opacity-45"
                  type="number"
                  min="0"
                  step="0.1"
                  inputMode="decimal"
                  disabled={pollIntervalBusy}
                  value={cooldownSeconds}
                  onChange={(event) => setCooldownSeconds(event.target.value)}
                />
              </label>
              <button
                type="button"
                className="dc-member-session-button"
                disabled={pollIntervalBusy || pollIntervalValue === null || cooldownValue === null}
                onClick={handleUpdateTiming}
              >
                적용
              </button>
            </div>
            <p className="dc-member-session-status preserve-words">
              현재 호출 {pollIntervalLabel(pollIntervalValue)} · 쿨다운 {cooldownLabel(cooldownValue)} (초 단위)
            </p>
            {pollIntervalStatus && (
              <p className="dc-member-session-status preserve-words">{pollIntervalStatus}</p>
            )}
          </section>
        )}
        {hasRoomAdminControl && (
          <section className="dc-member-detail-section" aria-label={`${entry.displayName} 방 관리`}>
            <h3>방 관리</h3>
            <p className="dc-member-detail-note preserve-words">
              추방은 이 방에서만 제거하고, 삭제는 저장된 세션 설정까지 제거합니다.
            </p>
            {!hasResumeControl && !hasStopControl && !canSelfStop && !canSelfResume && (
              <p className="dc-member-detail-note preserve-words">
                이 세션은 서버가 직접 실행하는 프로세스도 아니고, 자기 실행 정보(pid/재실행 명령)도 등록하지 않아 여기서 멈추거나 재개할 수 없습니다.
              </p>
            )}
            {(canSelfStop || canSelfResume) && (
              <p className="dc-member-detail-note preserve-words">
                터미널에서 직접 띄운 내 에이전트입니다. STOP은 그 프로세스를 실제로 종료하고, RESUME은 서버 백그라운드로 다시 띄웁니다.
              </p>
            )}
            <div className="dc-member-session-actions">
              {canSelfStop && (
                <button
                  type="button"
                  className="dc-member-session-button"
                  disabled={sessionActionBusy}
                  onClick={() => void handleSelfStop()}
                >
                  <Square size={15} />
                  STOP
                </button>
              )}
              {canSelfResume && (
                <button
                  type="button"
                  className="dc-member-session-button"
                  disabled={sessionActionBusy}
                  onClick={() => void handleSelfResume()}
                >
                  <Play size={15} />
                  RESUME
                </button>
              )}
              <button
                type="button"
                className="dc-member-session-button"
                data-variant="danger"
                disabled={sessionActionBusy}
                onClick={handleExpelAgent}
              >
                <LogOut size={15} />
                추방
              </button>
              <button
                type="button"
                className="dc-member-session-button"
                data-variant="danger"
                disabled={sessionActionBusy}
                onClick={handleDeleteAgentSession}
              >
                <Trash2 size={15} />
                세션 삭제
              </button>
            </div>
            {!hasSessionSection && sessionActionStatus && (
              <p className="dc-member-session-status preserve-words">{sessionActionStatus}</p>
            )}
          </section>
        )}
      </section>
    </div>
  );
}

export default function MemberList({
  agents,
  members = [],
  roomId,
  roomName,
  roleOverrides,
  onRoleChange,
  canEditRoles = true,
  processGroups = [],
  onSessionActionComplete,
  quotaViewer,
  searchQuery,
  onSearchQueryChange,
  hideSearch = false,
  moderatorSessionToken = "",
}: {
  agents: LiveAgent[];
  members?: RoomMember[];
  roomId: string;
  roomName: string;
  roleOverrides?: Record<string, string>;
  onRoleChange?: (memberId: string, role: RoleId) => void;
  canEditRoles?: boolean;
  processGroups?: LiveAgentProcessGroup[];
  onSessionActionComplete?: () => void;
  quotaViewer?: AgentQuotaVisibilityViewer;
  searchQuery?: string;
  onSearchQueryChange?: (query: string) => void;
  hideSearch?: boolean;
  moderatorSessionToken?: string;
}) {
  const [localRoleOverrides, setLocalRoleOverrides] = useState<Record<string, RoleId>>({});
  const [localQuery, setLocalQuery] = useState("");
  const [detailEntry, setDetailEntry] = useState<MemberEntry | null>(null);
  const [collapsedGroups, setCollapsedGroups] = useState<Record<string, boolean>>({});
  const [memberMenu, setMemberMenu] = useState<{ x: number; y: number; entry: MemberEntry } | null>(null);
  const [muteBusy, setMuteBusy] = useState(false);
  const [agentProfileSettings, setAgentProfileSettings] = useState<Record<string, AgentProfileSettings>>(
    () => loadAgentProfileSettings()
  );
  const query = searchQuery ?? localQuery;
  const contextBadges = roomContextSummaryBadges(agents);
  const effectiveRoleOverrides = (roleOverrides || localRoleOverrides) as Record<string, RoleId>;
  const entries = useMemo<MemberEntry[]>(() => {
    const mutedById = new Map(members.map((member) => [member.participant_id, Boolean(member.muted)]));
    const human: MemberEntry = {
      id: "human:self",
      displayName: "나",
      detail: "사람",
      role: effectiveRoleOverrides["human:self"] || "human",
      owner: true,
      active: true,
      muted: false,
      meetingId: "",
      canViewQuota: false,
      ownedByViewer: true,
      icon: UserCheck,
    };
    const agentEntries = agents.map((agent) => {
      const inferredRole = inferAgentRole(agent);
      const role = effectiveRoleOverrides[agent.agent_id] || inferredRole;
      const profile = agentProfileSettings[agent.agent_id] || {};
      const canViewQuotaForAgent = canViewAgentQuota(agent, quotaViewer);
      const ownerDisplayName = String(agent.owner_display_name || (canViewQuotaForAgent ? "SeiNel" : "다른 사람")).trim();
      const agentDisplayName = String(profile.displayName || agent.display_name || agent.agent_id).trim();
      const agentPanelDisplayName = `${ownerDisplayName}'s ${agentDisplayName}`;
      return {
        id: agent.agent_id,
        agent,
        displayName: agentPanelDisplayName,
        detail: providerExecutionLabel(agent),
        role,
        owner: false,
        active: isActive(agent),
        muted: mutedById.get(agent.agent_id) ?? false,
        meetingId: String(agent.meeting_id || ""),
        canViewQuota: canViewQuotaForAgent,
        ownedByViewer: canViewQuotaForAgent,
        ownerDisplayName,
        agentDisplayName,
        agentProfile: profile,
        avatarImage: profile.avatarImage || agent.avatar_image_url,
        icon: ROLE_OPTIONS.find((option) => option.id === role)?.icon || Bot,
      } satisfies MemberEntry;
    });
    const agentIds = new Set(agentEntries.map((entry) => entry.id));
    const invitedEntries = members
      .filter((member) => member.participant_id && !agentIds.has(member.participant_id))
      .map((member) => {
        const fallbackRole = memberRole(member);
        const role = effectiveRoleOverrides[member.participant_id] || fallbackRole;
        const typeMeta = participantTypeMeta(member.participant_type);
        const fullDetail = [
          typeMeta.label,
          member.provider_kind,
          member.connection_kind,
          member.source === "friend_invite" ? "친구 초대" : "",
        ]
          .filter(Boolean)
          .join(" · ");
        const detail = [
          typeMeta.label,
          member.source === "friend_invite" ? "친구 초대" : "",
        ]
          .filter(Boolean)
          .join(" · ");
        return {
          id: member.participant_id,
          member,
          displayName: member.display_name || member.participant_id,
          detail,
          fullDetail,
          statusLabel: memberStatusLabel(member),
          role,
          owner: false,
          active: memberActive(member),
          muted: Boolean(member.muted),
          meetingId: String(member.meeting_id || ""),
          canViewQuota: false,
          ownedByViewer: false,
          avatarImage: member.avatar_image_url,
          icon: ROLE_OPTIONS.find((option) => option.id === role)?.icon || typeMeta.icon,
        } satisfies MemberEntry;
      });
    return [human, ...agentEntries, ...invitedEntries];
  }, [agentProfileSettings, agents, effectiveRoleOverrides, members, quotaViewer]);
  const visibleEntries = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return entries;
    return entries.filter((entry) =>
      [entry.displayName, entry.detail, entry.role].some((value) =>
        value.toLowerCase().includes(needle)
      )
    );
  }, [entries, query]);

  useEffect(() => {
    try {
      const stored = localStorage.getItem(roleStorageKey(roomId));
      setLocalRoleOverrides(stored ? JSON.parse(stored) : {});
    } catch {
      setLocalRoleOverrides({});
    }
  }, [roomId]);

  useEffect(() => {
    setAgentProfileSettings(loadAgentProfileSettings());
  }, [roomId]);

  const canModerate = canEditRoles;

  function handleMemberContextMenu(entry: MemberEntry, event: ReactMouseEvent<HTMLElement>) {
    // Host-only moderation: right-clicking a participant opens the mute menu.
    // Self and any participant without a room scope can't be muted.
    if (!canModerate || entry.id === "human:self" || !entry.meetingId) return;
    event.preventDefault();
    setMemberMenu({ x: event.clientX, y: event.clientY, entry });
  }

  async function handleToggleMute(entry: MemberEntry) {
    if (!entry.meetingId) return;
    setMuteBusy(true);
    try {
      await muteRoomMember({
        meetingId: entry.meetingId,
        participantId: entry.id,
        muted: !entry.muted,
        sessionToken: moderatorSessionToken,
      });
      onSessionActionComplete?.();
    } catch (error) {
      window.alert(
        `뮤트 변경 실패: ${error instanceof Error ? error.message : String(error)}\n` +
          "(호스트 토큰이 없으면 거부됩니다 — 호스트 브라우저에서 다시 시도하세요)"
      );
    } finally {
      setMuteBusy(false);
      setMemberMenu(null);
    }
  }

  async function handleKick(entry: MemberEntry) {
    if (!entry.meetingId) return;
    if (!window.confirm(`${entry.displayName}을(를) 이 방에서 내보낼까요? (열린 초대 링크로는 다시 들어올 수 있어요)`)) {
      setMemberMenu(null);
      return;
    }
    setMuteBusy(true);
    try {
      await kickRoomMember({
        meetingId: entry.meetingId,
        participantId: entry.id,
        sessionToken: moderatorSessionToken,
      });
      onSessionActionComplete?.();
    } catch (error) {
      window.alert(
        `내보내기 실패: ${error instanceof Error ? error.message : String(error)}\n` +
          "(호스트 토큰이 없으면 거부됩니다 — 호스트 브라우저에서 다시 시도하세요)"
      );
    } finally {
      setMuteBusy(false);
      setMemberMenu(null);
    }
  }

  function handleRoleChange(memberId: string, role: RoleId) {
    if (onRoleChange) {
      onRoleChange(memberId, role);
      return;
    }
    setLocalRoleOverrides((previous) => {
      const next = { ...previous, [memberId]: role };
      try {
        localStorage.setItem(roleStorageKey(roomId), JSON.stringify(next));
      } catch {
        // Local role grouping is a UI preference; keep the in-memory state if storage is unavailable.
      }
      return next;
    });
  }

  function toggleGroup(groupId: string) {
    setCollapsedGroups((previous) => ({ ...previous, [groupId]: !previous[groupId] }));
  }

  const visibleGroups = useMemo(
    () => [
      // Invited members carry no LiveAgent record, so split them by role
      // (seeded from participant_type at join, host-adjustable via dropdown)
      // instead of dumping every guest into the people section.
      {
        id: "people",
        label: "사람",
        icon: User,
        entries: visibleEntries.filter((entry) => !entry.agent && entry.role === "human"),
      },
      {
        id: "owned-agents",
        label: "내 에이전트",
        icon: Bot,
        entries: visibleEntries.filter((entry) => entry.agent && entry.ownedByViewer),
      },
      {
        id: "other-agents",
        label: "다른 사람의 에이전트",
        icon: Bot,
        entries: visibleEntries.filter(
          (entry) =>
            (entry.agent && !entry.ownedByViewer) || (!entry.agent && entry.role !== "human")
        ),
      },
    ],
    [visibleEntries]
  );

  return (
    <div className="flex h-full min-h-0 flex-col">
      {!hideSearch && (
      <div className="dc-member-search shrink-0">
        <label className="dc-member-search-box">
          <span className="sr-only">{roomName} 멤버 검색</span>
          <input
            type="search"
            value={query}
            onChange={(event) => {
              const nextQuery = event.target.value;
              if (onSearchQueryChange) {
                onSearchQueryChange(nextQuery);
              } else {
                setLocalQuery(nextQuery);
              }
            }}
            placeholder={`${roomName} 검색`}
          />
          <Search size={15} aria-hidden />
        </label>
      </div>
      )}
      <div className="min-h-0 flex-1 overflow-y-auto px-2 py-3 chat-scroll">
        {agents.length === 0 && members.length === 0 && (
          <p className="mb-2 px-2 text-[13px] text-text-muted preserve-words">
            {roomName}에는 아직 멤버가 없습니다.
          </p>
        )}
        {visibleGroups.map(({ id, label, icon: Icon, entries: groupEntries }) => {
          if (!groupEntries.length) return null;
          return (
            <details
              key={id}
              className="dc-role-group"
              open={!collapsedGroups[id]}
              onToggle={(event) => {
                const open = event.currentTarget.open;
                setCollapsedGroups((previous) => ({ ...previous, [id]: !open }));
              }}
            >
              <summary
                className="dc-role-heading"
                onClick={(event) => {
                  event.preventDefault();
                  toggleGroup(id);
                }}
              >
                <Icon size={13} />
                {label} — {groupEntries.length}
              </summary>
              {groupEntries.map((entry) => (
                <MemberRow
                  key={entry.id}
                  entry={entry}
                  onOpenDetails={setDetailEntry}
                  onRoleChange={handleRoleChange}
                  onContextMenu={handleMemberContextMenu}
                  canEditRoles={canEditRoles}
                />
              ))}
            </details>
          );
        })}
        {contextBadges.length > 0 && (
          <details className="dc-member-context mt-3 px-2" aria-label="참가자 맥락 요약">
            <summary className="cursor-pointer list-none text-[11px] font-bold text-text-muted hover:text-text-secondary">
              고급 연결 요약
            </summary>
            <ProviderTruthChips badges={contextBadges} compact />
          </details>
        )}
      </div>
      {detailEntry && (
        <MemberDetailModal
          entry={detailEntry}
          onClose={() => setDetailEntry(null)}
          processGroups={processGroups}
          onSessionActionComplete={onSessionActionComplete}
          onAgentProfileSettingsChange={setAgentProfileSettings}
        />
      )}
      {memberMenu && (
        <>
          <div
            className="dc-member-menu-backdrop"
            role="presentation"
            onClick={() => setMemberMenu(null)}
            onContextMenu={(event) => {
              event.preventDefault();
              setMemberMenu(null);
            }}
          />
          <div
            className="dc-member-context-menu"
            role="menu"
            style={{ top: memberMenu.y, left: memberMenu.x }}
          >
            <p className="dc-member-context-menu-title preserve-words">{memberMenu.entry.displayName}</p>
            <button
              type="button"
              role="menuitem"
              className="dc-member-context-menu-item"
              disabled={muteBusy}
              onClick={() => void handleToggleMute(memberMenu.entry)}
            >
              {memberMenu.entry.muted ? <Volume2 size={14} /> : <VolumeX size={14} />}
              {memberMenu.entry.muted ? "뮤트 해제" : "뮤트"}
            </button>
            <button
              type="button"
              role="menuitem"
              className="dc-member-context-menu-item"
              data-variant="danger"
              disabled={muteBusy}
              onClick={() => void handleKick(memberMenu.entry)}
            >
              <UserMinus size={14} />
              내보내기
            </button>
          </div>
        </>
      )}
    </div>
  );
}
