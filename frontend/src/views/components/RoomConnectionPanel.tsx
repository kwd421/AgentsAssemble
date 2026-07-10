import { useState } from "react";
import { Bot, Copy, Gamepad2, Play, Plus, RotateCcw, Square, Zap } from "lucide-react";
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
  type RoomAgentSession,
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
  agentSessions?: RoomAgentSession[];
  capabilities?: Record<string, boolean>;
  onAgentControl?: (
    session: RoomAgentSession,
    action: "start" | "stop" | "resume" | "interrupt"
  ) => void | Promise<void>;
  onParticipantKick?: (participantId: string) => void | Promise<void>;
  onParticipantMute?: (participantId: string, muted: boolean) => void | Promise<void>;
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

function liveCliStatusLabel(status?: string) {
  if (status === "busy") return "응답 중";
  if (status === "starting") return "시작 중";
  if (status === "idle") return "대기";
  if (status === "stopping") return "중지 중";
  if (status === "stopped") return "중지됨";
  if (status === "error") return "오류";
  if (status === "disconnected") return "끊김";
  return status || "상태 미정";
}

function liveCliStatusTone(status?: string) {
  if (status === "busy" || status === "starting") return "running";
  if (status === "idle") return "ready";
  if (status === "error" || status === "disconnected") return "error";
  return "";
}

function liveCliLatency(agent: RoomAgentSession) {
  const latency = agent.latency || {};
  const ttfo = typeof latency.ttfo_ms === "number" ? `${Math.round(latency.ttfo_ms)}ms first` : "";
  const total = typeof latency.total_turn_ms === "number" ? `${Math.round(latency.total_turn_ms)}ms total` : "";
  return [ttfo, total].filter(Boolean).join(" · ");
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
  agentSessions = [],
  capabilities = {},
  onAgentControl = () => undefined,
  onParticipantKick,
  onParticipantMute,
}: RoomConnectionPanelProps) {
  const mutedCount = mutedChannelCount(channelNotifications);
  const [selectedMode, setSelectedMode] = useState("turn_based_floor");
  const [selectedDurationSeconds, setSelectedDurationSeconds] = useState(180);
  const [selectedActivityId, setSelectedActivityId] = useState<PlayActivityId>("conversation");
  const [busy, setBusy] = useState(false);
  const [playError, setPlayError] = useState("");
  const isFlowRunning = flow.status === "running";
  const conversationFlowDisabled = selectedActivityId === "conversation";
  const readyAgents = agents.filter((agent) => isActivePresence(agent.status));
  const selectedActivity =
    PLAY_ACTIVITIES.find((activity) => activity.id === selectedActivityId) || PLAY_ACTIVITIES[0];

  async function handleStartConversation() {
    setPlayError("Free/play flow is disabled. Use ordered Agent Sessions.");
    return;
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
      {!guestLocked && agentSessions.length > 0 && (
        <section className="dc-room-play-panel" aria-label="Agent Session">
          <div className="dc-room-play-header">
            <span className="dc-room-play-title">Agent Session</span>
            <span className="dc-room-play-state running">{agentSessions.length}</span>
          </div>
          <div className="dc-room-live-cli-list">
            {agentSessions.map((session) => {
              const status = session.runtime_status || session.status;
              const running = ["starting", "idle", "busy"].includes(status);
              return (
                <article key={session.session_id} className="dc-room-live-cli-card">
                  <div className="dc-room-live-cli-head">
                    <div className="min-w-0">
                      <p className="truncate text-[13px] font-black text-text-primary preserve-words">
                        {session.display_name || session.participant_id}
                      </p>
                      <p className="truncate text-[11px] text-text-muted preserve-words">
                        {session.runtime_kind || "local CLI"}
                        {session.pid ? ` · pid ${session.pid}` : ""}
                      </p>
                    </div>
                    <span className={`dc-room-play-state ${liveCliStatusTone(status)}`}>
                      {liveCliStatusLabel(status)}
                    </span>
                  </div>
                  <div className="dc-room-live-cli-actions">
                    <button
                      type="button"
                      title="세션 시작"
                      disabled={running}
                      onClick={() => void onAgentControl(session, "start")}
                    >
                      <Play size={13} />
                      Start
                    </button>
                    <button
                      type="button"
                      title="세션 중지"
                      disabled={!running && status !== "error"}
                      onClick={() => void onAgentControl(session, "stop")}
                    >
                      <Square size={13} />
                      Stop
                    </button>
                    <button
                      type="button"
                      title="세션 재개"
                      disabled={running}
                      onClick={() => void onAgentControl(session, "resume")}
                    >
                      <RotateCcw size={13} />
                      Resume
                    </button>
                    <button
                      type="button"
                      title="현재 응답 중단"
                      disabled={status !== "busy"}
                      onClick={() => void onAgentControl(session, "interrupt")}
                    >
                      <Zap size={13} />
                      Interrupt
                    </button>
                  </div>
                  <details className="dc-room-connection-note preserve-words">
                    <summary>진단</summary>
                    <p>{liveCliLatency(session) || `turns ${session.turn_count || 0}`}</p>
                    <p>cursor {session.last_seen_event_id || "none"}</p>
                    {session.last_error && <p className="dc-room-play-error">{session.last_error}</p>}
                  </details>
                </article>
              );
            })}
          </div>
        </section>
      )}
      {!guestLocked && agentSessions.length === 0 && (
        <section className="dc-room-play-panel" aria-label="Agent Session">
          <div className="dc-room-play-header">
            <span className="dc-room-play-title">Agent Session</span>
            <span className={`dc-room-play-state ${isFlowRunning ? "running" : ""}`}>
              {isFlowRunning ? "진행 중" : "대기"}
            </span>
          </div>
          {playError && <p className="dc-room-play-error preserve-words">{playError}</p>}
          <div className="dc-room-play-activity-list" aria-label="Agent Session 자동 응답">
            <p className="dc-room-connection-note preserve-words">
              방에 메시지를 보내면 ordered Agent Session이 자동으로 다음 응답을 시작합니다.
            </p>
          </div>
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
                    Turn mode
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
                    Duration
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
                disabled={busy || conversationFlowDisabled}
                className="dc-room-play-primary"
                aria-label={`${selectedActivity.label} 시작`}
                title={conversationFlowDisabled ? "Free/play flow is disabled. Use ordered Agent Sessions." : undefined}
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
        canEditRoles={Boolean(capabilities["room.manage"])}
        canModerate={Boolean(capabilities["participant.kick"] || capabilities["participant.mute"])}
        onParticipantKick={capabilities["participant.kick"] ? onParticipantKick : undefined}
        onParticipantMute={capabilities["participant.mute"] ? onParticipantMute : undefined}
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
