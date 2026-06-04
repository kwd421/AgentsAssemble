import { useState } from "react";
import { Bot, MessageCircle, Play, Square, Trash2 } from "lucide-react";
import {
  resumeLiveAgentSession,
  stopLiveAgentSession,
  type LiveAgentProcessGroup,
  type RoomFriend,
} from "../../api";
import { participantTypeMeta } from "../../lib/participantTypes";
import { presenceStatusLabel } from "../../lib/presenceStatus";

function friendInitial(friend: RoomFriend) {
  return (friend.display_name || friend.handle || "?").slice(0, 1).toUpperCase();
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

  const meta = participantTypeMeta(friend.participant_type);
  const Icon = meta.icon || Bot;
  const sourceAgentId = String(friend.source_agent_id || friend.agent_id || "").trim();
  const sessionGroup = processGroups.find((group) =>
    (group.agents || []).some((agent) => agent.agent_id === sourceAgentId || agent.display_name === friend.display_name)
  );
  const processRunning = sessionGroup?.status === "running";
  const canResumeSession = Boolean(sessionGroup?.group_id && sessionGroup?.meeting_id && sessionGroup?.config_path && !processRunning);
  const canStopSession = Boolean(sessionGroup?.group_id && sessionGroup?.meeting_id && processRunning);
  const facts = [
    ["타입", meta.label],
    ["상태", presenceStatusLabel(friend.status)],
    ["Provider", friend.provider_kind || "미지정"],
    ["연결", friend.connection_kind || "미지정"],
    ["최근 방", friend.last_meeting_id || "기록 없음"],
  ];

  async function handleResumeSession() {
    if (!sessionGroup || !canResumeSession) return;
    setSessionActionBusy(true);
    setSessionActionStatus("RESUME 요청 중...");
    try {
      const response = await resumeLiveAgentSession({
        meetingId: sessionGroup.meeting_id,
        groupId: sessionGroup.group_id,
        liveAgentConfigPath: sessionGroup.config_path,
      });
      setSessionActionStatus(`RESUME 완료${response.status ? ` · ${response.status}` : ""}`);
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
      const response = await stopLiveAgentSession({
        meetingId: sessionGroup.meeting_id,
        groupId: sessionGroup.group_id,
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
          {sessionGroup && (
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
        {sessionActionStatus && <p className="dc-friend-profile-note preserve-words">{sessionActionStatus}</p>}
        <p className="dc-friend-profile-note preserve-words">
          저장된 친구 정보는 AgentsAssemble 안에만 남습니다.
        </p>
      </div>
    </article>
  );
}
