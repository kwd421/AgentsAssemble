import { Bot, Trash2 } from "lucide-react";
import { type RoomFriend } from "../../api";
import { participantTypeMeta } from "../../lib/participantTypes";
import { presenceStatusLabel } from "../../lib/presenceStatus";

function friendInitial(friend: RoomFriend) {
  return (friend.display_name || friend.handle || "?").slice(0, 1).toUpperCase();
}

export default function FriendProfileCard({
  friend,
  onDelete,
}: {
  friend: RoomFriend | null;
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
    ["상태", presenceStatusLabel(friend.status)],
    ["Agent Session", friend.source_agent_id || friend.agent_id || "미지정"],
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
          저장된 친구 정보는 AgentsAssemble 안에만 남습니다.
        </p>
      </div>
    </article>
  );
}
