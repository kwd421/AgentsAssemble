import { useState } from "react";
import { Bot, MessageCircle, Play, Square, Trash2 } from "lucide-react";
import {
  resumeAgentSession,
  stopLiveAgentSessionAgent,
  type LiveAgentProcessGroup,
  type RoomFriend,
} from "../../api";
import {
  findProcessGroupForAgent,
  processGroupCanControlSingleAgent,
  processGroupIndividualControlReason,
} from "../../lib/liveAgentProcessControls";
import { participantTypeMeta } from "../../lib/participantTypes";
import { presenceStatusLabel } from "../../lib/presenceStatus";

function friendInitial(friend: RoomFriend) {
  return (friend.display_name || friend.handle || "?").slice(0, 1).toUpperCase();
}

function agentSessionResumeStatus(response: { state_status?: string; process_status?: string; status?: string }) {
  if (response.process_status === "resumed" || response.process_status === "launched") {
    return "Agent Session process resumed";
  }
  if (response.process_status === "unsupported") return "Agent Session state attached · process unsupported";
  if (response.process_status === "failed") return "Agent Session state attached · process failed";
  if (response.process_status === "not_started") return "Agent Session state attached only";
  return `Agent Session ${response.state_status || response.status || "attached"}`;
}

export default function FriendProfileCard({
  friend,
  onStartDm,
  onDelete,
  processGroups = [],
  onSessionActionComplete,
}: {
  friend: RoomFriend | null;
  onStartDm?: (friend: RoomFriend) => void;
  onDelete?: (friend: RoomFriend) => void;
  processGroups?: LiveAgentProcessGroup[];
  onSessionActionComplete?: () => void;
}) {
  const [sessionActionBusy, setSessionActionBusy] = useState(false);
  const [sessionActionStatus, setSessionActionStatus] = useState("");
  if (!friend) {
    return (
      <div className="dc-activity-card">
        <p>지금은 조용하네요...</p>
        <span>친구가 방에 참여하거나 에이전트 세션이 켜지면 여기에 표시됩니다.</span>
      </div>
    );
  }

  const activeFriend = friend;
  const meta = participantTypeMeta(friend.participant_type);
  const Icon = meta.icon || Bot;
  const sourceAgentId = String(friend.source_agent_id || friend.agent_id || "").trim();
  const hasSourceAgentId = Boolean(sourceAgentId);
  const processIdentity = { agent_id: sourceAgentId, display_name: friend.display_name };
  const sessionGroup = findProcessGroupForAgent(processGroups, processIdentity);
  const canControlSingleAgent = processGroupCanControlSingleAgent(sessionGroup, processIdentity);
  const processOwnsAgent = Boolean(sessionGroup);
  const individualControlReason = processGroupIndividualControlReason(
    sessionGroup,
    processIdentity,
    friend.display_name || "이 AI"
  );
  const processRunning = sessionGroup?.status === "running";
  const showIndividualControlReason = Boolean(individualControlReason && processRunning);
  const canResumeSession = Boolean(
    hasSourceAgentId &&
      processOwnsAgent &&
      sessionGroup?.group_id &&
      sessionGroup?.meeting_id &&
      sessionGroup?.config_path &&
      !processRunning
  );
  const canStopSession = Boolean(
    hasSourceAgentId && canControlSingleAgent && sessionGroup?.group_id && sessionGroup?.meeting_id && processRunning
  );
  const facts = [
    ["타입", meta.label],
    ["상태", presenceStatusLabel(friend.status)],
    ["Agent Session", friend.source_agent_id || friend.agent_id || "미지정"],
    ["최근 방", friend.last_meeting_id || "기록 없음"],
  ];

  async function handleResumeSession() {
    if (!sessionGroup || !canResumeSession) return;
    setSessionActionBusy(true);
    setSessionActionStatus("RESUME 요청 중...");
    try {
      const response = await resumeAgentSession({
        roomId: sessionGroup.meeting_id,
        agentId: sourceAgentId,
        sessionId: sourceAgentId,
        displayName: activeFriend.display_name,
        providerKind: activeFriend.provider_kind,
      });
      setSessionActionStatus(`RESUME 완료 · ${agentSessionResumeStatus(response)}`);
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
        agentId: sourceAgentId,
      });
      setSessionActionStatus(`STOP(KILL) 완료${response.status ? ` · ${response.status}` : ""}`);
      onSessionActionComplete?.();
    } catch (error) {
      setSessionActionStatus(error instanceof Error ? error.message : "STOP(KILL) 실패");
    } finally {
      setSessionActionBusy(false);
    }
  }

  return (
    <article className="dc-friend-profile-card" data-type={meta.tone}>
      <div className="dc-friend-profile-banner" aria-hidden />
      <div className="dc-friend-profile-body">
        <span className="dc-friend-profile-avatar">
          <Icon size={24} />
          <span>{friendInitial(friend)}</span>
        </span>
        <h2 className="preserve-words">{friend.display_name}</h2>
        <p className="dc-friend-profile-handle preserve-words">
          {friend.handle || friend.source_agent_id || friend.friend_id}
        </p>
        <p className="dc-friend-profile-type preserve-words">{meta.detail}</p>
        <div className="dc-friend-profile-actions">
          <button
            type="button"
            className="dc-friend-profile-secondary"
            onClick={() => onStartDm?.(friend)}
          >
            <MessageCircle size={15} />
            DM
          </button>
          {sessionGroup && (canResumeSession || canStopSession) && (
            <>
              <button
                type="button"
                className="dc-friend-profile-secondary"
                onClick={handleResumeSession}
                disabled={!canResumeSession || sessionActionBusy}
              >
                <Play size={15} />
                RESUME
              </button>
              <button
                type="button"
                className="dc-friend-profile-danger"
                onClick={handleStopSession}
                disabled={!canStopSession || sessionActionBusy}
              >
                <Square size={15} />
                STOP(KILL)
              </button>
            </>
          )}
          {onDelete && (
            <button type="button" className="dc-friend-profile-danger" onClick={() => onDelete(friend)}>
              <Trash2 size={15} />
              친구 삭제
            </button>
          )}
        </div>
        <dl className="dc-friend-profile-facts">
          {facts.map(([label, value]) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd className="preserve-words">{value}</dd>
            </div>
          ))}
        </dl>
        {showIndividualControlReason && (
          <p className="dc-friend-profile-note preserve-words">{individualControlReason}</p>
        )}
        {sessionActionStatus && <p className="dc-friend-profile-note preserve-words">{sessionActionStatus}</p>}
        <p className="dc-friend-profile-note preserve-words">
          저장된 친구 정보는 AgentsAssemble 안에만 남습니다.
        </p>
      </div>
    </article>
  );
}
