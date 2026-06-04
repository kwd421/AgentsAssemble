import { useEffect, useMemo, useState } from "react";
import { Copy, Search, X } from "lucide-react";
import type { RoomFriend, RoomMember } from "../../api";
import { roomFriendMatchesSearch } from "../../lib/friendSearch";
import { participantTypeMeta } from "../../lib/participantTypes";
import { isActivePresence, presenceStatusLabel } from "../../lib/presenceStatus";
import { inviteFriendButtonLabel } from "../../lib/roomInviteCopy";
import type { RoomAppearance } from "../../lib/roomAppearance";

function participantIdForFriend(friend: RoomFriend): string {
  return friend.source_agent_id || friend.friend_id;
}

function memberForFriend(friend: RoomFriend, members: RoomMember[]): RoomMember | undefined {
  const participantIds = new Set([participantIdForFriend(friend), friend.friend_id].filter(Boolean));
  return members.find((member) => participantIds.has(member.participant_id));
}

function inviteStatusForMember(member?: RoomMember): string {
  if (!member) return "";
  if (member.status === "pending") return "실행 필요";
  if (member.status === "invited") return "초대됨";
  if (isActivePresence(member.status)) return "참가 중";
  return presenceStatusLabel(member.status);
}

function inviteFriendSubtitle(friend: RoomFriend, typeLabel: string): string {
  const detail =
    friend.handle ||
    friend.provider_kind ||
    friend.connection_kind ||
    friend.source_agent_id ||
    "";
  return detail ? `${typeLabel} · ${detail}` : typeLabel;
}

export default function RoomInviteModal({
  roomLabel,
  inviteUrl,
  inviteScope = "room",
  friends,
  members = [],
  friendStatuses,
  copyStatus,
  remoteClientPacketPreview,
  remoteClientPacketFriendName,
  onClose,
  onCopy,
  onCopyRemoteClientPacket,
  onInviteFriend,
}: {
  roomLabel: string;
  inviteUrl: string;
  inviteScope?: RoomAppearance["inviteScope"];
  friends: RoomFriend[];
  members?: RoomMember[];
  friendStatuses?: Record<string, string>;
  copyStatus?: string;
  remoteClientPacketPreview?: string;
  remoteClientPacketFriendName?: string;
  onClose: () => void;
  onCopy: () => void;
  onCopyRemoteClientPacket?: () => void;
  onInviteFriend: (friend: RoomFriend) => void;
}) {
  const [query, setQuery] = useState("");
  const searchQuery = query.trim();
  const searchNeedle = searchQuery.toLowerCase();
  const readOnlyInvite = inviteScope === "read_only";
  const visibleFriends = useMemo(() => {
    if (!searchNeedle) return friends;
    return friends.filter((friend) => roomFriendMatchesSearch(friend, searchNeedle));
  }, [friends, searchNeedle]);
  useEffect(() => {
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [onClose]);

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
              {readOnlyInvite
                ? "수신자는 읽기 전용 초대 링크를 로컬 DM으로 받습니다. 이 링크로 들어온 사람은 이 방만 보고 메시지는 보낼 수 없습니다."
                : "수신자는 이 방 초대 링크를 로컬 DM으로 받습니다. 이 링크로 들어온 사람은 이 방만 보고 채팅합니다."}
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
            aria-label="친구 검색"
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
              const existingMember = memberForFriend(friend, members);
              const status = friendStatuses?.[friend.friend_id] || inviteStatusForMember(existingMember);
              const done = status === "초대됨" || status === "호출됨" || status === "참가 중";
              const needsRun = status === "실행 필요";
              const disabled = status === "초대 중" || done || needsRun;
              const isAiFriend = friend.participant_type !== "human";
              return (
                <div
                  key={friend.friend_id}
                  className="dc-invite-friend-row"
                  data-type={meta.tone}
                  data-member-state={existingMember?.status || undefined}
                  role="listitem"
                >
                  <span className="dc-invite-friend-avatar">
                    <Icon size={20} />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="dc-invite-friend-name preserve-words">{friend.display_name}</span>
                    <span className="dc-invite-friend-handle preserve-words">
                      {inviteFriendSubtitle(friend, meta.label)}
                    </span>
                  </span>
                  <button
                    type="button"
                    className="dc-invite-friend-button"
                    data-state={needsRun ? "attention" : done ? "done" : "idle"}
                    disabled={disabled}
                    title={needsRun ? "provider/CLI 세션을 먼저 시작하거나 resume해야 합니다." : undefined}
                    onClick={() => onInviteFriend(friend)}
                  >
                    {inviteFriendButtonLabel({ status, isAiFriend, readOnlyInvite })}
                  </button>
                </div>
              );
            })
          ) : (
            <p className="dc-invite-empty">
              {searchQuery
                ? "일치하는 친구가 없습니다."
                : "초대할 친구가 없습니다. 친구 탭에서 먼저 추가하세요."}
            </p>
          )}
        </div>

        <label className="dc-invite-link-label">
          {readOnlyInvite
            ? "또는 친구에게 읽기 전용 초대 링크 전송하기"
            : "또는 친구에게 방 초대 링크 전송하기"}
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
        {remoteClientPacketPreview && (
          <label className="dc-invite-link-label">
            AI 세션용 입장 패킷
            <span className="text-[12px] font-bold text-text-muted preserve-words">
              {remoteClientPacketFriendName || "상대 AI"}에게 그대로 전달하면 이미 실행 중인 세션이
              join/read/say/leave 요청으로 방에 들어올 수 있습니다.
            </span>
            <textarea
              className="dc-invite-packet-textarea"
              value={remoteClientPacketPreview}
              readOnly
              onFocus={(event) => event.currentTarget.select()}
              aria-label="AI 세션용 입장 패킷"
            />
            <button
              type="button"
              className="dc-invite-copy-button"
              onClick={onCopyRemoteClientPacket}
            >
              <Copy size={15} />
              패킷 복사
            </button>
          </label>
        )}
        <p className="mt-3 text-[12px] text-text-muted preserve-words">
          {copyStatus ||
            (readOnlyInvite
              ? "읽기 전용 링크로 들어온 사람은 방을 보기만 합니다. 오프라인 AI는 provider/CLI 세션을 먼저 시작하거나 resume해야 합니다."
              : "사람은 링크로 입장하고, AI는 살아 있는 세션이면 호출됩니다. 오프라인 AI는 provider/CLI 세션을 먼저 시작하거나 resume해야 합니다.")}
        </p>
      </section>
    </div>
  );
}
