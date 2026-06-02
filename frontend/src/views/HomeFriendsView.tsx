import { useEffect, useMemo, useState } from "react";
import { Bot, Braces, Laptop, MessageCircle, Plus, RefreshCw, Send, UserRound } from "lucide-react";
import {
  createLiveAgentJoinBrief,
  fetchRoomFriends,
  saveRoomFriend,
  type LiveAgentJoinBrief,
  type RoomFriend,
  type RoomFriendType,
  type RoomFriendsResponse,
} from "../api";

const FRIEND_TYPE_LABELS: Record<RoomFriendType, string> = {
  human: "사람",
  subscription_ai: "구독형 AI",
  api: "API",
  local: "Local",
  unknown: "기타",
};

const FRIEND_TYPE_ICONS = {
  human: UserRound,
  subscription_ai: Bot,
  api: Braces,
  local: Laptop,
  unknown: MessageCircle,
};

const FRIEND_TYPES: RoomFriendType[] = ["human", "subscription_ai", "api", "local", "unknown"];

function friendKey(friend: RoomFriend) {
  return friend.friend_id || friend.agent_id || friend.handle || friend.display_name;
}

function friendSubtitle(friend: RoomFriend) {
  return [friend.handle, friend.provider_kind, friend.connection_kind].filter(Boolean).join(" · ");
}

function groupFriends(friends: RoomFriend[]) {
  return FRIEND_TYPES.map((type) => ({
    type,
    friends: friends.filter((friend) => friend.participant_type === type),
  })).filter((group) => group.friends.length > 0);
}

function FriendRow({
  friend,
  actionLabel,
  onSave,
  onInvite,
  busy,
}: {
  friend: RoomFriend;
  actionLabel?: string;
  onSave?: (friend: RoomFriend) => void;
  onInvite?: (friend: RoomFriend) => void;
  busy?: boolean;
}) {
  const Icon = FRIEND_TYPE_ICONS[friend.participant_type] || MessageCircle;
  return (
    <article className="group flex items-center gap-3 rounded-lg px-3 py-2 hover:bg-white/[0.04]">
      <span className="relative grid h-10 w-10 shrink-0 place-items-center rounded-full bg-[#313338] text-text-secondary">
        <Icon size={18} />
        <span className="absolute bottom-0 right-0 h-3 w-3 rounded-full border-2 border-[#1e1f22] bg-online" />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <p className="truncate text-[14px] font-bold text-text-primary preserve-words">
            {friend.display_name}
          </p>
          <span className="rounded bg-[#2b2d31] px-1.5 py-0.5 text-[10px] font-black text-text-muted">
            {FRIEND_TYPE_LABELS[friend.participant_type]}
          </span>
        </div>
        <p className="truncate text-[12px] text-text-muted preserve-words">
          {friendSubtitle(friend) || "저장된 참가자"}
        </p>
      </div>
      {onInvite && (
        <button
          type="button"
          onClick={() => onInvite(friend)}
          disabled={busy}
          className="grid h-9 w-9 place-items-center rounded-full bg-[#2b2d31] text-text-muted opacity-0 transition hover:bg-[#3a3c42] hover:text-text-primary disabled:opacity-40 group-hover:opacity-100"
          aria-label={`${friend.display_name} 다시 초대`}
          title="다시 초대"
        >
          <Send size={15} />
        </button>
      )}
      {onSave && (
        <button
          type="button"
          onClick={() => onSave(friend)}
          disabled={busy}
          className="rounded-md bg-[#5865f2] px-3 py-1.5 text-[12px] font-black text-white transition hover:bg-[#4752c4] disabled:opacity-40"
        >
          {actionLabel || "친구 추가"}
        </button>
      )}
    </article>
  );
}

export default function HomeFriendsView({ meetingId }: { meetingId: string }) {
  const [payload, setPayload] = useState<RoomFriendsResponse>({ friends: [], suggestions: [], types: FRIEND_TYPES });
  const [displayName, setDisplayName] = useState("");
  const [handle, setHandle] = useState("");
  const [participantType, setParticipantType] = useState<RoomFriendType>("subscription_ai");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [invitePacket, setInvitePacket] = useState<LiveAgentJoinBrief | null>(null);
  const groupedFriends = useMemo(() => groupFriends(payload.friends), [payload.friends]);

  useEffect(() => {
    let cancelled = false;
    fetchRoomFriends()
      .then((nextPayload) => {
        if (!cancelled) setPayload(nextPayload);
      })
      .catch((errorValue) => {
        if (!cancelled) {
          setError(errorValue instanceof Error ? errorValue.message : "친구 목록을 불러오지 못했습니다.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function refreshFriends() {
    setBusy(true);
    setError("");
    try {
      setPayload(await fetchRoomFriends());
    } catch (errorValue) {
      setError(errorValue instanceof Error ? errorValue.message : "친구 목록 새로고침 실패");
    } finally {
      setBusy(false);
    }
  }

  async function addFriend(friend: Partial<RoomFriend>) {
    setBusy(true);
    setError("");
    try {
      const nextPayload = await saveRoomFriend(friend);
      setPayload(nextPayload);
      setDisplayName("");
      setHandle("");
    } catch (errorValue) {
      setError(errorValue instanceof Error ? errorValue.message : "친구 추가 실패");
    } finally {
      setBusy(false);
    }
  }

  async function inviteFriend(friend: RoomFriend) {
    setBusy(true);
    setError("");
    setInvitePacket(null);
    try {
      const packet = await createLiveAgentJoinBrief({
        agent_id: friend.agent_id || friend.handle || friend.friend_id,
        display_name: friend.display_name,
        provider_kind: friend.provider_kind || friend.participant_type,
        connection_kind: friend.connection_kind || "",
        meeting_id: meetingId,
      });
      setInvitePacket(packet);
    } catch (errorValue) {
      setError(errorValue instanceof Error ? errorValue.message : "초대 패킷 생성 실패");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="mx-auto grid h-full min-h-0 w-full max-w-[1180px] gap-4 overflow-hidden lg:grid-cols-[minmax(0,1fr)_340px]">
      <main className="ops-panel flex min-h-0 flex-col overflow-hidden rounded-xl border border-[#1f2024] bg-[#313338]">
        <div className="flex shrink-0 items-center justify-between gap-3 border-b border-[#1f2024] px-5 py-3">
          <div className="flex min-w-0 items-center gap-3">
            <UserRound size={20} className="text-text-muted" />
            <h1 className="truncate text-[16px] font-black text-text-primary">친구</h1>
            <span className="h-5 w-px bg-[#1f2024]" aria-hidden />
            <button type="button" className="rounded bg-[#404249] px-3 py-1.5 text-[13px] font-bold text-text-primary">
              온라인
            </button>
            <button type="button" className="px-2 py-1.5 text-[13px] font-bold text-text-muted">
              모두
            </button>
          </div>
          <button
            type="button"
            onClick={refreshFriends}
            disabled={busy}
            className="grid h-9 w-9 place-items-center rounded-full text-text-muted transition hover:bg-white/[0.05] hover:text-text-primary disabled:opacity-40"
            aria-label="친구 목록 새로고침"
          >
            <RefreshCw size={16} />
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto p-4 chat-scroll">
          {error && (
            <p className="mb-3 rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-[12px] text-danger preserve-words">
              {error}
            </p>
          )}

          <section className="mb-5 rounded-lg bg-[#2b2d31] p-4">
            <h2 className="mb-3 flex items-center gap-2 text-[14px] font-black">
              <Plus size={16} className="text-online" />
              친구 추가
            </h2>
            <div className="grid gap-2 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_150px_auto]">
              <input
                value={displayName}
                onChange={(event) => setDisplayName(event.target.value)}
                className="ops-input rounded-lg px-3 py-2.5 text-[13px]"
                placeholder="표시 이름"
              />
              <input
                value={handle}
                onChange={(event) => setHandle(event.target.value)}
                className="ops-input rounded-lg px-3 py-2.5 text-[13px]"
                placeholder="세션 ID / 핸들"
              />
              <select
                value={participantType}
                onChange={(event) => setParticipantType(event.target.value as RoomFriendType)}
                className="ops-input rounded-lg px-3 py-2.5 text-[13px]"
              >
                {FRIEND_TYPES.map((type) => (
                  <option key={type} value={type}>
                    {FRIEND_TYPE_LABELS[type]}
                  </option>
                ))}
              </select>
              <button
                type="button"
                disabled={busy || (!displayName.trim() && !handle.trim())}
                onClick={() => addFriend({ display_name: displayName, handle, participant_type: participantType })}
                className="rounded-lg bg-[#5865f2] px-4 py-2.5 text-[13px] font-black text-white transition hover:bg-[#4752c4] disabled:opacity-40"
              >
                추가
              </button>
            </div>
          </section>

          {groupedFriends.length === 0 ? (
            <div className="flex h-[220px] items-center justify-center text-center text-[13px] font-bold text-text-muted">
              아직 저장된 친구가 없습니다.
            </div>
          ) : (
            <div className="space-y-5">
              {groupedFriends.map((group) => (
                <section key={group.type}>
                  <h2 className="mb-2 px-3 text-[12px] font-black uppercase tracking-wide text-text-muted">
                    {FRIEND_TYPE_LABELS[group.type]} - {group.friends.length}
                  </h2>
                  <div className="divide-y divide-[#1f2024]">
                    {group.friends.map((friend) => (
                      <FriendRow key={friendKey(friend)} friend={friend} onInvite={inviteFriend} busy={busy} />
                    ))}
                  </div>
                </section>
              ))}
            </div>
          )}
        </div>
      </main>

      <aside className="flex min-h-0 flex-col gap-4 overflow-y-auto chat-scroll">
        <section className="ops-panel rounded-xl border border-[#1f2024] bg-[#2b2d31] p-4">
          <h2 className="mb-3 text-[15px] font-black">활동 중인 세션</h2>
          {payload.suggestions.length === 0 ? (
            <p className="rounded-lg bg-[#313338] px-3 py-4 text-center text-[12px] font-bold text-text-muted">
              저장 가능한 활성 세션이 없습니다.
            </p>
          ) : (
            <div className="space-y-1">
              {payload.suggestions.map((friend) => (
                <FriendRow
                  key={friendKey(friend)}
                  friend={friend}
                  onSave={addFriend}
                  actionLabel="추가"
                  busy={busy}
                />
              ))}
            </div>
          )}
        </section>

        <section className="ops-panel rounded-xl border border-[#1f2024] bg-[#2b2d31] p-4">
          <h2 className="mb-3 text-[15px] font-black">초대 패킷</h2>
          {invitePacket ? (
            <pre className="max-h-[320px] overflow-auto rounded-lg bg-[#1e1f22] p-3 text-[11px] leading-relaxed text-text-secondary chat-scroll">
              {JSON.stringify(invitePacket, null, 2)}
            </pre>
          ) : (
            <p className="rounded-lg bg-[#313338] px-3 py-4 text-center text-[12px] font-bold text-text-muted">
              저장된 친구 옆의 전송 아이콘을 누르면 현재 방 기준 Join Brief를 만듭니다.
            </p>
          )}
        </section>
      </aside>
    </section>
  );
}
