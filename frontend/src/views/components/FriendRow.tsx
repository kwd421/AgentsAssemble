import { useEffect, useRef, useState } from "react";
import { MessageCircle, MoreVertical, Trash2, UserPlus } from "lucide-react";
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

export default function FriendRow({
  friend,
  actionLabel,
  onAction,
  inviteLabel,
  onInvite,
  onStartDm,
  onDelete,
  selected,
  onSelect,
}: {
  friend: RoomFriend;
  actionLabel?: string;
  onAction?: (friend: RoomFriend) => void;
  inviteLabel?: string;
  onInvite?: (friend: RoomFriend) => void;
  onStartDm?: (friend: RoomFriend) => void;
  onDelete?: (friend: RoomFriend) => void;
  selected?: boolean;
  onSelect?: (friend: RoomFriend) => void;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const rowRef = useRef<HTMLDivElement>(null);
  const meta = participantTypeMeta(friend.participant_type);
  const Icon = meta.icon;
  const hasMenuActions = Boolean(onStartDm || onInvite || onSelect || onDelete);
  const detail = [
    meta.label,
    friend.last_meeting_id ? `최근 방 ${friend.last_meeting_id}` : "",
  ]
    .filter(Boolean)
    .join(" · ");
  const fullDetail = [
    meta.label,
    friend.provider_kind,
    friend.last_meeting_id,
  ]
    .filter(Boolean)
    .join(" · ");
  const rowContent = (
    <>
      <span className="dc-friend-avatar">
        <Icon size={18} />
      </span>
      <span className="min-w-0 flex-1 text-left">
        <span className="dc-friend-name preserve-words">{friend.display_name}</span>
        <span className="dc-friend-detail preserve-words" title={fullDetail || detail}>
          {detail}
        </span>
      </span>
      <span className="dc-friend-status">{statusLabel(friend.status)}</span>
    </>
  );

  useEffect(() => {
    if (!menuOpen) return;
    function closeOnOutside(event: MouseEvent) {
      if (!rowRef.current?.contains(event.target as Node)) {
        setMenuOpen(false);
      }
    }
    window.addEventListener("mousedown", closeOnOutside);
    return () => window.removeEventListener("mousedown", closeOnOutside);
  }, [menuOpen]);

  return (
    <div ref={rowRef} className="dc-friend-row" data-type={meta.tone} data-selected={selected ? "true" : "false"}>
      {onSelect ? (
        <button
          type="button"
          className="dc-friend-main-button"
          onClick={() => onSelect(friend)}
          aria-pressed={selected}
        >
          {rowContent}
        </button>
      ) : (
        <span className="dc-friend-main-button" aria-current={selected ? "true" : undefined}>
          {rowContent}
        </span>
      )}
      {onAction ? (
        <div className="dc-friend-actions">
          <button type="button" className="dc-friend-action" onClick={() => onAction(friend)}>
            <UserPlus size={15} />
            {actionLabel || "추가"}
          </button>
        </div>
      ) : hasMenuActions ? (
        <div className="dc-friend-menu-wrap">
          <button
            type="button"
            className="dc-friend-icon-action"
            aria-label={`${friend.display_name} 작업`}
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen((value) => !value)}
          >
            <MoreVertical size={18} />
          </button>
          {menuOpen && (
            <div className="dc-friend-row-menu" role="menu" aria-label={`${friend.display_name} 작업 메뉴`}>
              {onStartDm && (
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    setMenuOpen(false);
                    onStartDm(friend);
                  }}
                >
                  <MessageCircle size={14} />
                  로컬 DM 열기
                </button>
              )}
              {onInvite && (
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    setMenuOpen(false);
                    onInvite(friend);
                  }}
                >
                  <UserPlus size={14} />
                  {inviteLabel === "초대 중" ? inviteLabel : "방에 초대하기"}
                </button>
              )}
              {onSelect && (
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    setMenuOpen(false);
                    onSelect(friend);
                  }}
                >
                  친구 정보 보기
                </button>
              )}
              {onDelete && (
                <button
                  type="button"
                  role="menuitem"
                  className="danger"
                  onClick={() => {
                    setMenuOpen(false);
                    onDelete(friend);
                  }}
                >
                  <Trash2 size={14} />
                  친구 삭제
                </button>
              )}
            </div>
          )}
        </div>
      ) : (
        <button type="button" className="dc-friend-icon-action" aria-label={`${friend.display_name} 더 보기`}>
          <MoreVertical size={18} />
        </button>
      )}
    </div>
  );
}
