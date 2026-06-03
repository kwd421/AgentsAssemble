import { Bot, MessageCircle, Trash2 } from "lucide-react";
import type { RoomFriend } from "../../api";
import { participantTypeMeta } from "../../lib/participantTypes";

function statusLabel(status: string) {
  if (status === "online") return "온라인";
  if (status === "working") return "작업 중";
  if (status === "idle") return "자리 비움";
  if (status === "error") return "오류";
  if (status === "offline") return "오프라인";
  return "상태 미정";
}

function friendInitial(friend: RoomFriend) {
  return (friend.display_name || friend.handle || "?").slice(0, 1).toUpperCase();
}

export default function FriendProfileCard({
  friend,
  onStartDm,
  onDelete,
}: {
  friend: RoomFriend | null;
  onStartDm?: (friend: RoomFriend) => void;
  onDelete?: (friend: RoomFriend) => void;
}) {
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
  const facts = [
    ["타입", meta.label],
    ["상태", statusLabel(friend.status)],
    ["Provider", friend.provider_kind || "미지정"],
    ["연결", friend.connection_kind || "미지정"],
    ["최근 방", friend.last_meeting_id || "기록 없음"],
  ];

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
            로컬 DM
          </button>
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
        <p className="dc-friend-profile-note preserve-words">
          저장된 친구는 로컬 AgentsAssemble 디렉터리에만 남습니다.
        </p>
      </div>
    </article>
  );
}
