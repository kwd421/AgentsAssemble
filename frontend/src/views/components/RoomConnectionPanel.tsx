import { Bot, CirclePause, Copy, Play, Plus, RotateCcw, Square, Zap } from "lucide-react";
import {
  type ChannelNotificationSetting,
  type LiveAgent,
  type LiveAgentProcessGroup,
  type RoomMember,
  type RoomAgentSession,
} from "../../api";
import type { AgentQuotaVisibilityViewer } from "../../lib/agentQuotaVisibility";
import MemberList, { type RoleId } from "./MemberList";

type RoomSummary = {
  id: string;
  label: string;
  meetingId: string;
  topic: string;
  tone: string;
};

type RoomConnectionPanelProps = {
  room: RoomSummary;
  agents: LiveAgent[];
  members: RoomMember[];
  roleOverrides?: Record<string, string>;
  onRoleChange?: (memberId: string, role: RoleId) => void;
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
    action: "start" | "pause" | "stop" | "resume" | "interrupt"
  ) => void | Promise<void>;
  onParticipantKick?: (participantId: string) => void | Promise<void>;
  onParticipantMute?: (participantId: string, muted: boolean) => void | Promise<void>;
};

function mutedChannelCount(
  channelNotifications?: RoomConnectionPanelProps["channelNotifications"]
): number {
  return Object.values(channelNotifications || {}).filter((setting) => setting.notifications === "mute").length;
}

function liveCliStatusLabel(status?: string) {
  if (status === "busy") return "응답 중";
  if (status === "starting") return "시작 중";
  if (status === "idle") return "대기";
  if (status === "paused") return "일시정지";
  if (status === "stopping") return "중지 중";
  if (status === "stopped") return "중지됨";
  if (status === "error") return "오류";
  if (status === "disconnected") return "끊김";
  return status || "상태 미정";
}

function liveCliStatusTone(status?: string) {
  if (status === "busy" || status === "starting") return "running";
  if (status === "idle" || status === "paused") return "ready";
  if (status === "error" || status === "disconnected") return "error";
  return "";
}

function liveCliLatency(agent: RoomAgentSession) {
  const latency = agent.latency || {};
  const ttfo = typeof latency.ttfo_ms === "number" ? `${Math.round(latency.ttfo_ms)}ms first` : "";
  const total = typeof latency.total_turn_ms === "number" ? `${Math.round(latency.total_turn_ms)}ms total` : "";
  return [ttfo, total].filter(Boolean).join(" · ");
}

function providerSessionContinuity(session: RoomAgentSession) {
  const structuredSession =
    session.transport === "acp_stdio" ||
    session.provider_session_load_supported ||
    session.provider_session_reused ||
    session.provider_session_resume_failed;
  if (!structuredSession) return "";
  if (!session.provider_session_active && session.provider_session_load_supported) return "provider session 재개 대기";
  if (!session.provider_session_active) return "provider session 비활성";
  if (session.provider_session_resume_failed) return "provider session 새로 시작됨";
  if (session.provider_session_reused) return "provider session 이어짐";
  return "provider session 활성";
}

export default function RoomConnectionPanel({
  room,
  agents,
  members,
  roleOverrides,
  onRoleChange,
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
              const running = ["starting", "idle", "busy", "paused"].includes(status);
              const sessionContinuity = providerSessionContinuity(session);
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
                      title="세션 일시정지"
                      disabled={status !== "idle"}
                      onClick={() => void onAgentControl(session, "pause")}
                    >
                      <CirclePause size={13} />
                      Pause
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
                      disabled={status !== "paused" && running}
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
                  <details className="dc-room-connection-note dc-room-runtime-diagnostics preserve-words">
                    <summary>진단</summary>
                    <p>
                      runtime {session.runtime_kind || "live_cli"} · {session.transport || "pty"}
                    </p>
                    {session.runtime_profile_key && <p>profile {session.runtime_profile_key}</p>}
                    {session.message_source && (
                      <p>
                        message {session.message_source}
                        {session.message_source_strict ? " · strict" : ""}
                      </p>
                    )}
                    <p>{liveCliLatency(session) || `turns ${session.turn_count || 0}`}</p>
                    <p>cursor {session.last_seen_event_id || "none"}</p>
                    <p>
                      input {session.provider_visible_chars || 0} chars · {session.provider_visible_event_count || 0} events
                    </p>
                    <p>
                      stderr {session.stderr_byte_count || 0} bytes · warnings {session.stderr_warning_count || 0}
                    </p>
                    {Boolean(session.notification_drop_count) && (
                      <p className="dc-room-play-error">protocol drops {session.notification_drop_count}</p>
                    )}
                    {sessionContinuity && <p>{sessionContinuity}</p>}
                    {typeof session.yolo_mode === "boolean" && (
                      <p>approval {session.yolo_mode ? "unsafe always-approve" : session.approval_policy || "restricted"}</p>
                    )}
                    {Boolean(session.permission_request_count) && (
                      <p>
                        permissions denied {session.permission_denied_count || 0}/{session.permission_request_count}
                      </p>
                    )}
                    {session.context_error_detected && <p className="dc-room-play-error">context error detected</p>}
                    {session.provider_session_resume_error && (
                      <p className="dc-room-play-error">{session.provider_session_resume_error}</p>
                    )}
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
            <span className="dc-room-play-state">대기</span>
          </div>
          <p className="dc-room-connection-note preserve-words">연결된 세션 없음</p>
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
