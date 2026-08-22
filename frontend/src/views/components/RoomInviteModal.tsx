import { useEffect, useMemo, useState } from "react";
import { Copy, Globe2, LoaderCircle, LockKeyhole, Search, X } from "lucide-react";
import type { RoomFriend, RoomMember } from "../../api";
import type {
  HumanInviteOptions,
  PublicAccessTransition,
} from "../../app/useRoomInviteController";
import { roomFriendMatchesSearch } from "../../lib/friendSearch";
import { participantTypeMeta } from "../../lib/participantTypes";
import { isActivePresence, presenceStatusLabel } from "../../lib/presenceStatus";
import { inviteFriendButtonLabel, isExternalInviteUrl } from "../../lib/roomInviteCopy";
import type { RoomAppearance } from "../../lib/roomAppearance";
import "./RoomInviteModal.css";

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

type PendingPublicAction =
  | { kind: "human"; options: HumanInviteOptions }
  | { kind: "agent" }
  | { kind: "friend"; friend: RoomFriend };

export default function RoomInviteModal({
  roomLabel,
  secureInviteUrl,
  agentInviteUrl,
  operatorPairingUrl,
  localPreviewUrl,
  publicUrl,
  publicUrlDraft,
  hostTokenDraft,
  hostTokenRequired = false,
  publicAccessTransition = "idle",
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
  onGenerateAgentInvite,
  onCopyAgentInvite,
  onGenerateOperatorPairing,
  onCopyOperatorPairing,
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
  operatorPairingUrl: string;
  localPreviewUrl: string;
  publicUrl?: string;
  publicUrlDraft?: string;
  hostTokenDraft?: string;
  hostTokenRequired?: boolean;
  publicAccessTransition?: PublicAccessTransition;
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
  onGenerateSecureInvite: (options: HumanInviteOptions, startTunnelIfNeeded: boolean) => void;
  onCopy: () => void;
  onGenerateAgentInvite: (startTunnelIfNeeded: boolean) => void;
  onCopyAgentInvite: () => void;
  onGenerateOperatorPairing: () => void;
  onCopyOperatorPairing: () => void;
  onCopyLocalPreview: () => void;
  onPublicUrlDraftChange: (value: string) => void;
  onConfigurePublicUrl: () => void;
  onHostTokenDraftChange: (value: string) => void;
  onSaveHostToken: () => void;
  onStartTunnel: () => void;
  onStopTunnel: () => void;
  onCopyRemoteClientPacket?: () => void;
  onInviteFriend: (friend: RoomFriend, startTunnelIfNeeded: boolean) => void;
}) {
  const [query, setQuery] = useState("");
  const [humanMaxUses, setHumanMaxUses] = useState(1);
  const [humanTtlSeconds, setHumanTtlSeconds] = useState(86400);
  const [generatedHumanOptions, setGeneratedHumanOptions] =
    useState<HumanInviteOptions | null>(null);
  const [pendingPublicAction, setPendingPublicAction] =
    useState<PendingPublicAction | null>(null);
  const searchQuery = query.trim();
  const searchNeedle = searchQuery.toLowerCase();
  const readOnlyInvite = inviteScope === "read_only";
  const currentHumanOptions = { maxUses: humanMaxUses, ttlSeconds: humanTtlSeconds };
  const secureInviteReady = Boolean(
    isExternalInviteUrl(secureInviteUrl) &&
      generatedHumanOptions?.maxUses === humanMaxUses &&
      generatedHumanOptions?.ttlSeconds === humanTtlSeconds
  );
  const publicAccessStarting =
    publicAccessTransition === "starting" || tunnelStatus?.phase === "starting";
  const publicAccessStopping = publicAccessTransition === "stopping";
  const publicAccessRunning = Boolean(
    publicUrl || tunnelStatus?.public_url || tunnelStatus?.phase === "running"
  );
  const publicAccessBusy = publicAccessStarting || publicAccessStopping;
  function requestPublicAction(action: PendingPublicAction) {
    if (publicAccessRunning) {
      if (action.kind === "human") {
        setGeneratedHumanOptions(action.options);
        onGenerateSecureInvite(action.options, false);
      } else if (action.kind === "agent") {
        onGenerateAgentInvite(false);
      } else {
        onInviteFriend(action.friend, false);
      }
      return;
    }
    setPendingPublicAction(action);
  }

  function confirmPublicAction() {
    const action = pendingPublicAction;
    setPendingPublicAction(null);
    if (!action) return;
    if (action.kind === "human") {
      setGeneratedHumanOptions(action.options);
      onGenerateSecureInvite(action.options, true);
    } else if (action.kind === "agent") {
      onGenerateAgentInvite(true);
    } else {
      onInviteFriend(action.friend, true);
    }
  }
  const visibleFriends = useMemo(() => {
    if (!searchNeedle) return friends;
    return friends.filter((friend) => roomFriendMatchesSearch(friend, searchNeedle));
  }, [friends, searchNeedle]);
  useEffect(() => {
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      if (pendingPublicAction) {
        setPendingPublicAction(null);
        return;
      }
      onClose();
    }
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [onClose, pendingPublicAction]);

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
              {roomLabel} 초대 및 연결
            </h2>
            <p className="mt-1 text-[13px] text-text-muted preserve-words">
              사람이나 외부 AI 세션을 초대합니다. 서버가 계속 관리할 에이전트는 이 창이 아니라 에이전트 추가에서 만드세요.
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

        <section
          className="dc-invite-hosting"
          data-state={publicAccessBusy ? "busy" : publicAccessRunning ? "public" : "local"}
          aria-labelledby="room-hosting-heading"
        >
          <span className="dc-invite-hosting-icon" aria-hidden="true">
            {publicAccessBusy ? (
              <LoaderCircle className="dc-invite-hosting-spinner" size={22} />
            ) : publicAccessRunning ? (
              <Globe2 size={22} />
            ) : (
              <LockKeyhole size={22} />
            )}
          </span>
          <div className="dc-invite-hosting-copy">
            <div className="dc-invite-hosting-title-row">
              <h3 id="room-hosting-heading">이 컴퓨터의 서버 공개</h3>
              <span className="dc-invite-hosting-state">
                {publicAccessStarting
                  ? "공개 준비 중"
                  : publicAccessStopping
                    ? "외부 접속 닫는 중"
                    : publicAccessRunning
                      ? "외부 접속 열림"
                      : "외부 접속 꺼짐"}
              </span>
            </div>
            <p>
              {publicAccessRunning
                ? publicUrl || tunnelStatus?.public_url || "외부 주소가 연결되어 있습니다."
                : "서버를 공개하지 않아도 이 컴퓨터의 룸과 에이전트는 그대로 작동합니다."}
            </p>
          </div>
          <div className="dc-invite-hosting-actions">
            <button
              type="button"
              className="dc-invite-copy-button"
              disabled={publicAccessBusy || publicAccessRunning}
              onClick={onStartTunnel}
            >
              외부 접속 열기
            </button>
            <button
              type="button"
              className="dc-invite-copy-button"
              disabled={publicAccessBusy || !publicAccessRunning}
              onClick={onStopTunnel}
            >
              외부 접속 끄기
            </button>
          </div>
        </section>

        <div className="dc-invite-primary-grid">
          <section className="dc-invite-card" aria-labelledby="human-invite-heading">
            <div>
              <h3 id="human-invite-heading">사람 초대</h3>
              <p>
                브라우저에서 여는 보안 링크입니다. 초대 인원과 만료 시간을 선택할 수 있습니다.
                {readOnlyInvite ? " 이 방에서는 읽기 전용으로 참가합니다." : ""}
              </p>
            </div>
            <div className="dc-invite-options">
              <label>
                <span>초대 가능 인원</span>
                <select
                  value={humanMaxUses}
                  onChange={(event) => {
                    setHumanMaxUses(Number(event.currentTarget.value));
                    setGeneratedHumanOptions(null);
                  }}
                >
                  <option value={1}>1명 (권장)</option>
                  <option value={5}>5명</option>
                  <option value={0}>제한 없음</option>
                </select>
              </label>
              <label>
                <span>링크 유효시간</span>
                <select
                  value={humanTtlSeconds}
                  onChange={(event) => {
                    setHumanTtlSeconds(Number(event.currentTarget.value));
                    setGeneratedHumanOptions(null);
                  }}
                >
                  <option value={3600}>1시간</option>
                  <option value={86400}>24시간 (권장)</option>
                  <option value={604800}>7일</option>
                </select>
              </label>
            </div>
            <div className="dc-invite-link-row">
              <input
                className="dc-invite-link-input"
                value={secureInviteReady ? secureInviteUrl : ""}
                placeholder="공개 주소를 준비하면 링크가 표시됩니다"
                readOnly
                aria-label="사람 초대 링크"
                onFocus={(event) => event.currentTarget.select()}
              />
              <button
                type="button"
                className="dc-invite-copy-button"
                aria-label="사람 초대 링크 생성"
                onClick={() => requestPublicAction({ kind: "human", options: currentHumanOptions })}
              >
                생성
              </button>
              <button
                type="button"
                className="dc-invite-copy-button"
                disabled={!secureInviteReady}
                onClick={onCopy}
              >
                <Copy size={15} />
                복사
              </button>
            </div>
          </section>

          <section className="dc-invite-card" aria-labelledby="ai-invite-heading">
            <div>
              <h3 id="ai-invite-heading">외부 AI 세션 초대</h3>
              <p>
                Room Connector가 등록된 Codex·Claude 앱이나 대화형 CLI에 링크를 붙여 넣으면 그 세션이 직접 참가합니다. 새 provider나 관리형 세션은 만들지 않습니다.
              </p>
              <span className="dc-invite-expiry-note">1회 사용 · 1시간 후 만료</span>
            </div>
            <div className="dc-invite-link-row">
              <input
                className="dc-invite-link-input"
                value={agentInviteUrl}
                placeholder="외부 AI 세션 초대 링크"
                readOnly
                aria-label="외부 AI 세션 초대 링크"
                onFocus={(event) => event.currentTarget.select()}
              />
              <button
                type="button"
                className="dc-invite-copy-button"
                aria-label="외부 AI 세션 초대 링크 생성"
                onClick={() => requestPublicAction({ kind: "agent" })}
              >
                생성
              </button>
              <button
                type="button"
                className="dc-invite-copy-button"
                disabled={!agentInviteUrl}
                onClick={onCopyAgentInvite}
              >
                <Copy size={15} />
                복사
              </button>
            </div>
            <details className="dc-invite-setup">
              <summary>처음 한 번: Room Connector 설치 및 등록</summary>
              <ol>
                <li>
                  AgentsAssemble 프로젝트 폴더에서 설치
                  <code>python3 -m pip install -e .</code>
                </li>
                <li>
                  사용하는 앱에 MCP 등록
                  <span className="dc-invite-command-label">Codex</span>
                  <code>codex mcp add agentsassemble-room -- assemble room connector-mcp</code>
                  <span className="dc-invite-command-label">Claude Code</span>
                  <code>claude mcp add --scope user agentsassemble-room -- assemble room connector-mcp</code>
                  <span className="dc-invite-command-label">기타 MCP 클라이언트</span>
                  <code>{'{"command":"assemble","args":["room","connector-mcp"]}'}</code>
                </li>
                <li>
                  앱에서 <code>room_join</code> 도구가 보이는지 확인한 뒤 위 초대 링크만 대화에 붙여 넣기
                </li>
              </ol>
            </details>
          </section>
        </div>

        <section className="dc-invite-friends-section" aria-labelledby="saved-friends-heading">
          <div className="dc-invite-section-heading">
            <div>
              <h3 id="saved-friends-heading">저장된 친구</h3>
              <p>이미 등록한 사람이나 AI 세션에 초대를 보냅니다.</p>
            </div>
          </div>
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
                      onClick={() => requestPublicAction({ kind: "friend", friend })}
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
        </section>

        <details className="dc-invite-advanced">
          <summary>고급 연결 설정</summary>
          <div className="dc-invite-advanced-body">
            <label className="dc-invite-link-label">
              공개 주소에서 나로 열기
              <span className="text-[12px] font-bold text-text-muted preserve-words">
                현재 운영자 본인만 사용하세요. 2분 뒤 만료되며 한 번 사용하면 폐기됩니다.
              </span>
              <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_112px_112px]">
                <input
                  className="dc-invite-link-input"
                  value={operatorPairingUrl}
                  placeholder="일회용 운영자 기기 연결 링크"
                  readOnly
                  onFocus={(event) => event.currentTarget.select()}
                />
                <button
                  type="button"
                  className="dc-invite-copy-button"
                  aria-label="운영자 기기 연결 링크 생성"
                  onClick={onGenerateOperatorPairing}
                >
                  링크 생성
                </button>
                <button
                  type="button"
                  className="dc-invite-copy-button"
                  disabled={!operatorPairingUrl}
                  onClick={onCopyOperatorPairing}
                >
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
          {tunnelStatus?.last_error && (
            <span className="mt-2 text-[12px] font-bold text-offline preserve-words">
              {tunnelStatus.last_error}
            </span>
          )}
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
            선택한 AI 친구 연결 정보
            <span className="text-[12px] font-bold text-text-muted preserve-words">
              {remoteClientPacketFriendName || "상대 AI"}에게 보낸 초대의 연결 정보입니다.
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
          </div>
        </details>
        <p className="mt-3 text-[12px] text-text-muted preserve-words">
          {copyStatus ||
            (readOnlyInvite
              ? "이 미리보기 링크는 로컬/dev 확인용입니다. 외부 공유에는 공개 URL 기반 보안 초대 링크가 필요합니다."
              : "사람은 보안 /join?token=... 링크로 입장합니다. 오프라인 AI는 provider/CLI 세션을 먼저 시작하거나 resume해야 합니다.")}
        </p>
        {pendingPublicAction && (
          <div
            className="dc-invite-confirm-backdrop"
            role="presentation"
            onClick={() => setPendingPublicAction(null)}
          >
            <section
              className="dc-invite-confirm"
              role="alertdialog"
              aria-modal="true"
              aria-labelledby="public-access-confirm-title"
              onClick={(event) => event.stopPropagation()}
            >
              <h3 id="public-access-confirm-title">외부 접속을 열까요?</h3>
              <p>
                이 컴퓨터의 서버에 임시 공개 주소를 연결한 뒤 초대 링크를 만듭니다. 링크를 가진 사람이나 AI 세션만 참가할 수 있습니다.
              </p>
              <div className="dc-invite-confirm-actions">
                <button
                  type="button"
                  className="dc-invite-copy-button"
                  onClick={() => setPendingPublicAction(null)}
                >
                  취소
                </button>
                <button
                  type="button"
                  className="dc-invite-confirm-primary"
                  onClick={confirmPublicAction}
                >
                  외부 접속 열고 링크 만들기
                </button>
              </div>
            </section>
          </div>
        )}
      </section>
    </div>
  );
}
