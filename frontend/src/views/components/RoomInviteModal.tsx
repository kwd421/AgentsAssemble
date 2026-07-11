import { useEffect, useMemo, useState } from "react";
import { Copy, Search, X } from "lucide-react";
import type { RoomFriend, RoomMember } from "../../api";
import { roomFriendMatchesSearch } from "../../lib/friendSearch";
import { participantTypeMeta } from "../../lib/participantTypes";
import { isActivePresence, presenceStatusLabel } from "../../lib/presenceStatus";
import { inviteFriendButtonLabel, isExternalInviteUrl } from "../../lib/roomInviteCopy";
import type { RoomAppearance } from "../../lib/roomAppearance";
import type { NativeCliProviderAvailability } from "../../roomSocketClient";

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
  secureInviteUrl,
  agentInviteUrl,
  agentInviteProviderId,
  availableProviders,
  localPreviewUrl,
  publicUrl,
  publicUrlDraft,
  hostTokenDraft,
  hostTokenRequired = false,
  tunnelStatus,
  inviteScope = "room",
  friends,
  members = [],
  friendStatuses,
  copyStatus,
  remoteClientPacketPreview,
  remoteClientPacketFriendName,
  onClose,
  onGenerateSecureInvite,
  onCopy,
  onAgentInviteProviderChange,
  onGenerateAgentInvite,
  onCopyAgentInvite,
  onCopyLocalPreview,
  onPublicUrlDraftChange,
  onConfigurePublicUrl,
  onHostTokenDraftChange,
  onSaveHostToken,
  onStartTunnel,
  onStopTunnel,
  onCopyRemoteClientPacket,
  onInviteFriend,
}: {
  roomLabel: string;
  secureInviteUrl: string;
  agentInviteUrl: string;
  agentInviteProviderId: string;
  availableProviders: NativeCliProviderAvailability[];
  localPreviewUrl: string;
  publicUrl?: string;
  publicUrlDraft?: string;
  hostTokenDraft?: string;
  hostTokenRequired?: boolean;
  tunnelStatus?: {
    phase?: string;
    running?: boolean;
    public_url?: string;
    local_url?: string;
    last_error?: string;
  };
  inviteScope?: RoomAppearance["inviteScope"];
  friends: RoomFriend[];
  members?: RoomMember[];
  friendStatuses?: Record<string, string>;
  copyStatus?: string;
  remoteClientPacketPreview?: string;
  remoteClientPacketFriendName?: string;
  onClose: () => void;
  onGenerateSecureInvite: () => void;
  onCopy: () => void;
  onAgentInviteProviderChange: (providerId: string) => void;
  onGenerateAgentInvite: () => void;
  onCopyAgentInvite: () => void;
  onCopyLocalPreview: () => void;
  onPublicUrlDraftChange: (value: string) => void;
  onConfigurePublicUrl: () => void;
  onHostTokenDraftChange: (value: string) => void;
  onSaveHostToken: () => void;
  onStartTunnel: () => void;
  onStopTunnel: () => void;
  onCopyRemoteClientPacket?: () => void;
  onInviteFriend: (friend: RoomFriend) => void;
}) {
  const [query, setQuery] = useState("");
  const searchQuery = query.trim();
  const searchNeedle = searchQuery.toLowerCase();
  const readOnlyInvite = inviteScope === "read_only";
  const secureInviteReady = isExternalInviteUrl(secureInviteUrl);
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
                ? "보안 초대 링크는 공개 URL이 설정된 뒤 생성됩니다. 로컬/dev 미리보기 링크로 들어온 사람은 인증된 외부 게스트 세션을 받지 않습니다."
                : "보안 초대 링크는 공개 URL이 설정된 뒤 생성됩니다. 친구에게 보낼 때는 /join?token=... 링크만 외부 초대로 사용합니다."}
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
        <label className="dc-invite-link-label">
          Agent Session 초대
          <span className="text-[12px] font-bold text-text-muted preserve-words">
            상대 컴퓨터에서 provider CLI를 실행한 뒤 링크를 숨김 입력으로 전달합니다. 링크는 한 번만 사용할 수 있습니다.
          </span>
          <div className="mt-2 grid gap-2 sm:grid-cols-[minmax(132px,0.45fr)_minmax(0,1fr)_112px_112px]">
            <select
              className="dc-invite-link-input"
              value={agentInviteProviderId}
              onChange={(event) => onAgentInviteProviderChange(event.currentTarget.value)}
              aria-label="초대할 Agent Session provider"
            >
              {availableProviders.map((provider) => (
                <option key={provider.id} value={provider.id} disabled={!provider.available}>
                  {provider.display_name}{provider.available ? "" : " (사용 불가)"}
                </option>
              ))}
            </select>
            <input
              className="dc-invite-link-input"
              value={agentInviteUrl}
              placeholder="1회용 Agent Session 초대 링크"
              readOnly
              onFocus={(event) => event.currentTarget.select()}
            />
            <button type="button" className="dc-invite-copy-button" onClick={onGenerateAgentInvite}>
              링크 생성
            </button>
            <button type="button" className="dc-invite-copy-button" disabled={!agentInviteUrl} onClick={onCopyAgentInvite}>
              <Copy size={15} />
              복사
            </button>
          </div>
          <code className="mt-2 block text-[12px] text-text-muted">assemble room attend --provider {agentInviteProviderId}</code>
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
          친구에게 보낼 보안 초대 링크
          <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_112px_112px]">
            <input
              className="dc-invite-link-input"
              value={secureInviteReady ? secureInviteUrl : ""}
              placeholder="공개 URL을 먼저 설정하면 /join?token=... 링크가 여기에 표시됩니다"
              readOnly
              onFocus={(event) => event.currentTarget.select()}
            />
            <button type="button" className="dc-invite-copy-button" onClick={onGenerateSecureInvite}>
              링크 생성
            </button>
            <button type="button" className="dc-invite-copy-button" disabled={!secureInviteReady} onClick={onCopy}>
              <Copy size={15} />
              복사
            </button>
          </div>
        </label>
        <div className="dc-invite-link-label">
          <span>공개 URL</span>
          <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_112px]">
            <input
              className="dc-invite-link-input"
              value={publicUrlDraft || publicUrl || ""}
              placeholder="https://random-words.trycloudflare.com"
              onChange={(event) => onPublicUrlDraftChange(event.currentTarget.value)}
            />
            <button type="button" className="dc-invite-copy-button" onClick={onConfigurePublicUrl}>
              설정
            </button>
          </div>
          <div className="mt-2 flex flex-wrap gap-2 text-[12px] font-bold text-text-muted">
            <button type="button" className="dc-invite-copy-button" onClick={onStartTunnel}>
              터널 시작
            </button>
            <button type="button" className="dc-invite-copy-button" onClick={onStopTunnel}>
              터널 중지
            </button>
            <span className="self-center preserve-words">
              {publicUrl || tunnelStatus?.public_url || "Paste public URL / Start tunnel first"}
              {tunnelStatus?.phase ? ` · ${tunnelStatus.phase}` : ""}
              {tunnelStatus?.last_error ? ` · ${tunnelStatus.last_error}` : ""}
            </span>
          </div>
        </div>
        {hostTokenRequired && (
          <label className="dc-invite-link-label">
            Host token required
            <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_112px]">
              <input
                className="dc-invite-link-input"
                value={hostTokenDraft || ""}
                placeholder="Host token"
                onChange={(event) => onHostTokenDraftChange(event.currentTarget.value)}
              />
              <button type="button" className="dc-invite-copy-button" onClick={onSaveHostToken}>
                저장
              </button>
            </div>
          </label>
        )}
        <details className="dc-invite-link-label">
          <summary>로컬/dev 미리보기</summary>
          <span className="text-[12px] font-bold text-text-muted preserve-words">
            이 링크는 이 컴퓨터에서만 확인하는 개발용입니다. 친구에게 보내지 마세요.
          </span>
          <div className="mt-2 grid gap-2 sm:grid-cols-[minmax(0,1fr)_132px]">
            <input
              className="dc-invite-link-input"
              value={localPreviewUrl}
              readOnly
              onFocus={(event) => event.currentTarget.select()}
            />
            <button type="button" className="dc-invite-copy-button" onClick={onCopyLocalPreview}>
              <Copy size={15} />
              로컬 미리보기 복사
            </button>
          </div>
        </details>
        {remoteClientPacketPreview && (
          <label className="dc-invite-link-label">
            기존 Agent Session 안내
            <span className="text-[12px] font-bold text-text-muted preserve-words">
              {remoteClientPacketFriendName || "상대 AI"}에게 전달할 간단한 입장 안내입니다. 새 초대는 위의
              지속 연결 명령을 사용하세요.
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
              ? "이 미리보기 링크는 로컬/dev 확인용입니다. 외부 공유에는 공개 URL 기반 보안 초대 링크가 필요합니다."
              : "사람은 보안 /join?token=... 링크로 입장합니다. 오프라인 AI는 provider/CLI 세션을 먼저 시작하거나 resume해야 합니다.")}
        </p>
      </section>
    </div>
  );
}
