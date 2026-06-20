import { useState } from "react";
import { Bot, Copy, Gamepad2, Plus, Square, Zap } from "lucide-react";
import {
  startFlow,
  startMafiaGame,
  stopFlow,
  type ChannelNotificationSetting,
  type FlowState,
  type LiveAgent,
  type LiveAgentProcessGroup,
  type MafiaGame,
  type RoomMember,
} from "../../api";
import type { AgentQuotaVisibilityViewer } from "../../lib/agentQuotaVisibility";
import type { RoomAppearance } from "../../lib/roomAppearance";
import { isActivePresence } from "../../lib/presenceStatus";
import MemberList, { type RoleId } from "./MemberList";

const ROOM_FLOW_MODES = [
  { id: "turn_based_floor", label: "Turn-Based Floor" },
];

// duration 0 = 무제한: 백엔드가 deadline을 잡지 않아 중지할 때까지 계속됩니다.
const ROOM_FLOW_DURATIONS = [
  { seconds: 0, label: "무제한" },
  { seconds: 60, label: "1분" },
  { seconds: 180, label: "3분" },
  { seconds: 300, label: "5분" },
  { seconds: 600, label: "10분" },
  { seconds: 1800, label: "30분" },
  { seconds: 3600, label: "1시간" },
];

const PLAY_ACTIVITIES = [
  { id: "conversation", label: "일반 대화", icon: Zap },
  { id: "mafia", label: "Mafia Night", icon: Gamepad2 },
] as const;

type PlayActivityId = (typeof PLAY_ACTIVITIES)[number]["id"];

type RoomSummary = {
  id: string;
  label: string;
  meetingId: string;
  topic: string;
  tone: string;
};

type RoomConnectionPanelProps = {
  room: RoomSummary;
  appearance: RoomAppearance;
  agents: LiveAgent[];
  members: RoomMember[];
  roleOverrides?: Record<string, string>;
  onRoleChange?: (memberId: string, role: RoleId) => void;
  flow?: FlowState;
  refreshFlow?: () => void;
  onMafiaStarted?: (game: MafiaGame) => void;
  onFlowStarted?: () => void;
  guestLocked?: boolean;
  guestOperator?: boolean;
  moderatorSessionToken?: string;
  guestAiPacketPreview?: string;
  guestAiPacketStatus?: string;
  onCreateCompanionAiPacket?: () => void;
  onCopyGuestAiPacket?: () => void;
  channelNotifications?: Record<string, { notifications: ChannelNotificationSetting; lastReadAt?: string }>;
  processGroups?: LiveAgentProcessGroup[];
  onSessionActionComplete?: () => void;
  quotaViewer?: AgentQuotaVisibilityViewer;
  onStartAddAgent?: () => void;
  memberSearchQuery?: string;
  onMemberSearchQueryChange?: (query: string) => void;
};

function mutedChannelCount(
  channelNotifications?: RoomConnectionPanelProps["channelNotifications"]
): number {
  return Object.values(channelNotifications || {}).filter((setting) => setting.notifications === "mute").length;
}

function flowPolicyLabel(_policy?: string): string {
  return "Turn-Based Floor";
}

function flowDurationLabel(durationSeconds?: number): string {
  if (!durationSeconds || durationSeconds <= 0) return "무제한";
  const preset = ROOM_FLOW_DURATIONS.find((option) => option.seconds === durationSeconds);
  if (preset) return preset.label;
  if (durationSeconds % 3600 === 0) return `${durationSeconds / 3600}시간`;
  if (durationSeconds % 60 === 0) return `${durationSeconds / 60}분`;
  return `${durationSeconds}초`;
}

function playErrorMessage(errorValue: unknown, fallback: string): string {
  const message = errorValue instanceof Error ? errorValue.message : String(errorValue || "");
  if (/^Meeting .+ was not found\.?$/.test(message)) {
    return "이 방은 아직 실행 가능한 회의 세션이 없습니다. 에이전트를 추가하거나 기존 세션 방에서 시작하세요.";
  }
  if (message.includes("Mafia game was not found")) {
    return "Mafia Night 세션이 없습니다. 게임을 다시 시작하세요.";
  }
  return message || fallback;
}

function mafiaPlayersFromAgents(agents: LiveAgent[]) {
  const candidates = agents.length
    ? agents
    : [
        { agent_id: "codex-spark-a", display_name: "Codex Spark A" } as LiveAgent,
        { agent_id: "codex-spark-b", display_name: "Codex Spark B" } as LiveAgent,
        { agent_id: "codex-spark-c", display_name: "Codex Spark C" } as LiveAgent,
        { agent_id: "codex-spark-d", display_name: "Codex Spark D" } as LiveAgent,
      ];
  return candidates.slice(0, 8).map((agent) => ({
    agent_id: agent.agent_id,
    display_name: agent.display_name || agent.agent_id,
  }));
}

export default function RoomConnectionPanel({
  room,
  agents,
  members,
  roleOverrides,
  onRoleChange,
  flow = { status: "idle" } as FlowState,
  refreshFlow,
  onMafiaStarted,
  onFlowStarted,
  guestLocked = false,
  guestOperator = false,
  moderatorSessionToken = "",
  guestAiPacketPreview = "",
  guestAiPacketStatus = "",
  onCreateCompanionAiPacket,
  onCopyGuestAiPacket,
  channelNotifications,
  processGroups = [],
  onSessionActionComplete,
  quotaViewer,
  onStartAddAgent,
  memberSearchQuery,
  onMemberSearchQueryChange,
}: RoomConnectionPanelProps) {
  const mutedCount = mutedChannelCount(channelNotifications);
  const [selectedMode, setSelectedMode] = useState("turn_based_floor");
  const [selectedDurationSeconds, setSelectedDurationSeconds] = useState(180);
  const [selectedActivityId, setSelectedActivityId] = useState<PlayActivityId>("conversation");
  const [busy, setBusy] = useState(false);
  const [playError, setPlayError] = useState("");
  const isFlowRunning = flow.status === "running";
  const readyAgents = agents.filter((agent) => isActivePresence(agent.status));
  const selectedActivity =
    PLAY_ACTIVITIES.find((activity) => activity.id === selectedActivityId) || PLAY_ACTIVITIES[0];

  async function handleStartConversation() {
    if (!room.meetingId.trim()) {
      setPlayError("회의 ID가 없습니다.");
      return;
    }
    setBusy(true);
    setPlayError("");
    try {
      await startFlow({
        meeting_id: room.meetingId.trim(),
        topic: room.topic.trim() || undefined,
        flow_policy: selectedMode,
        duration_seconds: selectedDurationSeconds,
        max_agent_turns: 0,
        max_total_turns: 0,
      });
      onFlowStarted?.();
      refreshFlow?.();
    } catch (errorValue) {
      setPlayError(playErrorMessage(errorValue, "대화 시작 실패"));
    } finally {
      setBusy(false);
    }
  }

  async function handleStartMafia() {
    if (!room.meetingId.trim()) {
      setPlayError("회의 ID가 없습니다.");
      return;
    }
    setBusy(true);
    setPlayError("");
    try {
      const payload = await startMafiaGame({
        game_id: room.meetingId.trim(),
        players: mafiaPlayersFromAgents(readyAgents.length >= 3 ? readyAgents : agents),
        mafia_count: 1,
      });
      if (payload.game) onMafiaStarted?.(payload.game);
    } catch (errorValue) {
      setPlayError(playErrorMessage(errorValue, "Mafia Night 시작 실패"));
    } finally {
      setBusy(false);
    }
  }

  async function handleStartSelectedActivity() {
    if (selectedActivityId === "mafia") {
      await handleStartMafia();
      return;
    }
    await handleStartConversation();
  }

  async function handleStopFlow() {
    const meetingId = flow.meeting_id || room.meetingId;
    if (!meetingId.trim()) {
      setPlayError("중지할 회의 ID가 없습니다.");
      return;
    }
    setBusy(true);
    setPlayError("");
    try {
      await stopFlow(meetingId.trim());
      refreshFlow?.();
    } catch (errorValue) {
      setPlayError(playErrorMessage(errorValue, "중지 실패"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="dc-room-connection-panel">
      {!guestLocked && onStartAddAgent && (
        <div className="dc-room-agent-add-row">
          <button type="button" className="dc-agent-add-entry" onClick={onStartAddAgent}>
            <Plus size={16} />
            에이전트 추가
          </button>
          {mutedCount > 0 && <span className="dc-room-muted-count">{mutedCount} muted</span>}
        </div>
      )}
      {!guestLocked && (
        <section className="dc-room-play-panel" aria-label="플레이 모드">
          <div className="dc-room-play-header">
            <span className="dc-room-play-title">플레이 모드</span>
            <span className={`dc-room-play-state ${isFlowRunning ? "running" : ""}`}>
              {isFlowRunning ? "진행 중" : "대기"}
            </span>
          </div>
          {playError && <p className="dc-room-play-error preserve-words">{playError}</p>}
          {isFlowRunning ? (
            <div className="dc-room-play-running">
              <span className="min-w-0 truncate text-text-secondary preserve-words">
                {flowPolicyLabel(flow.policy)} · {flowDurationLabel(flow.duration_seconds)} ·{" "}
                {flow.topic || flow.meeting_id || room.label}
              </span>
              <button type="button" onClick={handleStopFlow} disabled={busy} className="dc-room-play-stop">
                <Square size={14} />
                중지
              </button>
            </div>
          ) : (
            <>
              <div className="dc-room-play-activity-list" role="listbox" aria-label="활동 선택">
                {PLAY_ACTIVITIES.map((activity) => {
                  const Icon = activity.icon;
                  const selected = activity.id === selectedActivityId;
                  return (
                    <button
                      key={activity.id}
                      type="button"
                      role="option"
                      aria-selected={selected}
                      data-active={selected}
                      className="dc-room-play-activity"
                      onClick={() => setSelectedActivityId(activity.id)}
                    >
                      <Icon size={15} />
                      <span className="truncate">{activity.label}</span>
                    </button>
                  );
                })}
              </div>
              {selectedActivityId === "conversation" && (
                <>
                  <label className="dc-room-play-label" htmlFor="room-flow-mode">
                    대화 방식
                  </label>
                  <select
                    id="room-flow-mode"
                    value={selectedMode}
                    onChange={(event) => setSelectedMode(event.target.value)}
                    className="dc-room-play-select"
                    aria-label="대화 방식"
                  >
                    {ROOM_FLOW_MODES.map((mode) => (
                      <option key={mode.id} value={mode.id}>
                        {mode.label}
                      </option>
                    ))}
                  </select>
                  <label className="dc-room-play-label" htmlFor="room-flow-duration">
                    시간 제한
                  </label>
                  <select
                    id="room-flow-duration"
                    value={selectedDurationSeconds}
                    onChange={(event) => setSelectedDurationSeconds(Number(event.target.value))}
                    className="dc-room-play-select"
                    aria-label="시간 제한"
                  >
                    {ROOM_FLOW_DURATIONS.map((option) => (
                      <option key={option.seconds} value={option.seconds}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </>
              )}
              <button
                type="button"
                onClick={handleStartSelectedActivity}
                disabled={busy}
                className="dc-room-play-primary"
                aria-label={`${selectedActivity.label} 시작`}
              >
                {selectedActivityId === "mafia" ? <Gamepad2 size={15} /> : <Zap size={15} />}
                시작
              </button>
            </>
          )}
        </section>
      )}
      {guestLocked && onCreateCompanionAiPacket && (
        <section className="dc-room-connection-card" aria-label="게스트 AI 세션 연결">
          <div className="dc-room-connection-title">
            <span className="dc-room-connection-icon" aria-hidden>
              <Bot size={18} />
            </span>
            <div className="min-w-0">
              <p className="truncate text-[13px] font-black text-text-primary preserve-words">
                AI 세션 패킷 만들기
              </p>
              <p className="truncate text-[11px] text-text-muted preserve-words">
                이미 실행 중인 내 AI에게 이 방 입장 패킷을 전달합니다.
              </p>
            </div>
          </div>
          {guestAiPacketPreview && (
            <textarea
              className="dc-invite-packet-textarea"
              value={guestAiPacketPreview}
              readOnly
              onFocus={(event) => event.currentTarget.select()}
              aria-label="게스트 AI 세션 입장 패킷"
            />
          )}
          <div className="mt-3 flex flex-wrap gap-2">
            <button type="button" className="dc-invite-copy-button" onClick={onCreateCompanionAiPacket}>
              <Bot size={15} />
              패킷 생성
            </button>
            {guestAiPacketPreview && (
              <button type="button" className="dc-invite-copy-button" onClick={onCopyGuestAiPacket}>
                <Copy size={15} />
                패킷 복사
              </button>
            )}
          </div>
          <p className="dc-room-connection-note preserve-words">
            {guestAiPacketStatus || "패킷은 이 방 범위의 join/read/say/leave 요청만 담습니다."}
          </p>
        </section>
      )}
      <MemberList
        agents={agents}
        members={members}
        roomId={room.id}
        roomName={room.label}
        roleOverrides={roleOverrides}
        onRoleChange={onRoleChange}
        canEditRoles={!guestLocked || guestOperator}
        moderatorSessionToken={moderatorSessionToken}
        processGroups={processGroups}
        onSessionActionComplete={onSessionActionComplete}
        quotaViewer={quotaViewer}
        searchQuery={memberSearchQuery}
        onSearchQueryChange={onMemberSearchQueryChange}
        hideSearch={memberSearchQuery !== undefined}
      />
    </div>
  );
}
