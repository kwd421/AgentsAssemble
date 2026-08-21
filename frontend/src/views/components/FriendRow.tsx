import { useEffect, useRef, useState } from "react";
import type { MouseEvent as ReactMouseEvent } from "react";
import { MoreVertical, Trash2, UserPlus } from "lucide-react";
import type { RoomFriend } from "../../api";
import { participantTypeMeta } from "../../lib/participantTypes";
import { presenceStatusLabel } from "../../lib/presenceStatus";

export default function FriendRow({
  friend,
  actionLabel,
  onAction,
  onDelete,
  selected,
  onSelect,
}: {
  friend: RoomFriend;
  actionLabel?: string;
  onAction?: (friend: RoomFriend) => void;
  onDelete?: (friend: RoomFriend) => void;
  selected?: boolean;
  onSelect?: (friend: RoomFriend) => void;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [menuPosition, setMenuPosition] = useState<{ left: number; top: number } | null>(null);
  const rowRef = useRef<HTMLDivElement>(null);
  const meta = participantTypeMeta(friend.participant_type);
  const Icon = meta.icon;
  const hasMenuActions = Boolean(onSelect || onDelete);
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
      <span className="dc-friend-status">{presenceStatusLabel(friend.status)}</span>
    </>
  );

  useEffect(() => {
    if (!menuOpen) return;
    function closeOnOutside(event: MouseEvent) {
      if (!rowRef.current?.contains(event.target as Node)) {
        setMenuOpen(false);
      }
    }
    function closeOnViewportChange() {
      setMenuOpen(false);
    }
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape") setMenuOpen(false);
    }
    window.addEventListener("mousedown", closeOnOutside);
    window.addEventListener("keydown", closeOnEscape);
    window.addEventListener("resize", closeOnViewportChange);
    window.addEventListener("scroll", closeOnViewportChange, true);
    return () => {
      window.removeEventListener("mousedown", closeOnOutside);
      window.removeEventListener("keydown", closeOnEscape);
      window.removeEventListener("resize", closeOnViewportChange);
      window.removeEventListener("scroll", closeOnViewportChange, true);
    };
  }, [menuOpen]);

  function toggleMenu(event: ReactMouseEvent<HTMLButtonElement>) {
    if (!menuOpen) {
      const rect = event.currentTarget.getBoundingClientRect();
      const menuWidth = 190;
      const menuHeight = 148;
      setMenuPosition({
        left: Math.max(8, Math.min(rect.right - menuWidth, window.innerWidth - menuWidth - 8)),
        top: Math.max(8, Math.min(rect.bottom + 6, window.innerHeight - menuHeight - 8)),
      });
    }
    setMenuOpen((value) => !value);
  }

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
            onClick={toggleMenu}
          >
            <MoreVertical size={18} />
          </button>
          {menuOpen && (
            <div
              className="dc-friend-row-menu"
              role="menu"
              aria-label={`${friend.display_name} 작업 메뉴`}
              style={menuPosition ? { left: menuPosition.left, top: menuPosition.top } : undefined}
            >
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
