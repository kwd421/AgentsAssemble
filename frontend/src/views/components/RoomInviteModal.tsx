import { useMemo, useState } from "react";
import { Copy, Search, X } from "lucide-react";
import type { RoomFriend } from "../../api";
import { participantTypeMeta } from "../../lib/participantTypes";

export default function RoomInviteModal({
  roomLabel,
  inviteUrl,
  friends,
  friendStatuses,
  copyStatus,
  onClose,
  onCopy,
  onInviteFriend,
}: {
  roomLabel: string;
  inviteUrl: string;
  friends: RoomFriend[];
  friendStatuses?: Record<string, string>;
  copyStatus?: string;
  onClose: () => void;
  onCopy: () => void;
  onInviteFriend: (friend: RoomFriend) => void;
}) {
  const [query, setQuery] = useState("");
  const visibleFriends = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return friends;
    return friends.filter((friend) =>
      [friend.display_name, friend.handle, friend.provider_kind, friend.participant_type].some((value) =>
        String(value || "").toLowerCase().includes(needle)
      )
    );
  }, [friends, query]);

  return (
    <div className="dc-modal-backdrop" role="presentation" onClick={onClose}>
      <section
        className="dc-invite-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="room-invite-title"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <h2 id="room-invite-title" className="truncate text-[18px] font-black text-text-primary preserve-words">
              친구를 {roomLabel}로 초대하기
            </h2>
            <p className="mt-1 text-[13px] text-text-muted preserve-words">
              수신자는 이 방 초대 링크를 로컬 DM으로 받습니다. 이 링크로 들어온 사람은 이 방만 보고 채팅합니다.
            </p>
          </div>
          <button
            type="button"
            className="dc-modal-close"
            onClick={onClose}
            aria-label="초대 닫기"
          >
            <X size={18} />
          </button>
        </header>

        <label className="dc-invite-search">
          <Search size={20} />
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="친구 찾기"
          />
        </label>

        <div className="dc-invite-friend-list" role="list" aria-label="초대할 친구">
          {visibleFriends.length ? (
            visibleFriends.map((friend) => {
              const meta = participantTypeMeta(friend.participant_type);
              const Icon = meta.icon;
              const status = friendStatuses?.[friend.friend_id] || "";
              const done = status === "초대됨" || status === "호출됨";
              const needsRun = status === "실행 필요";
              const isAiFriend = friend.participant_type !== "human";
              return (
                <div key={friend.friend_id} className="dc-invite-friend-row" data-type={meta.tone} role="listitem">
                  <span className="dc-invite-friend-avatar">
                    <Icon size={20} />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="dc-invite-friend-name preserve-words">{friend.display_name}</span>
                    <span className="dc-invite-friend-handle preserve-words">
                      {friend.handle || friend.provider_kind || meta.label}
                    </span>
                  </span>
                  <button
                    type="button"
                    className="dc-invite-friend-button"
                    data-state={needsRun ? "attention" : done ? "done" : "idle"}
                    disabled={status === "초대 중"}
                    onClick={() => onInviteFriend(friend)}
                  >
                    {status || (isAiFriend ? "호출하기" : "초대하기")}
                  </button>
                </div>
              );
            })
          ) : (
            <p className="dc-invite-empty">초대할 친구가 없습니다. 친구 탭에서 먼저 추가하세요.</p>
          )}
        </div>

        <label className="dc-invite-link-label">
          또는 친구에게 서버 초대 링크 전송하기
          <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_112px]">
            <input
              className="dc-invite-link-input"
              value={inviteUrl}
              readOnly
              onFocus={(event) => event.currentTarget.select()}
            />
            <button type="button" className="dc-invite-copy-button" onClick={onCopy}>
              <Copy size={15} />
              링크 복사
            </button>
          </div>
        </label>
        <p className="mt-3 text-[12px] text-text-muted preserve-words">
          {copyStatus ||
            "사람은 링크로 입장하고, AI는 살아 있는 세션이면 호출됩니다. 오프라인 AI는 provider/CLI 세션을 먼저 시작하거나 resume해야 합니다."}
        </p>
      </section>
    </div>
  );
}
