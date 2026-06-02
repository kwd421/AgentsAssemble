import { useEffect, useMemo, useState } from "react";
import { MoreVertical, Search, UserPlus, Users } from "lucide-react";
import {
  addRoomFriend,
  fetchRoomFriends,
  type ParticipantType,
  type RoomFriend,
  type RoomFriendsResponse,
} from "../api";
import { PARTICIPANT_TYPE_OPTIONS, participantTypeMeta } from "../lib/participantTypes";
import FriendDmPanel from "./components/FriendDmPanel";
import FriendProfileCard from "./components/FriendProfileCard";

function statusLabel(status: string) {
  if (status === "online") return "온라인";
  if (status === "working") return "작업 중";
  if (status === "idle") return "자리 비움";
  if (status === "error") return "오류";
  if (status === "offline") return "오프라인";
  return "상태 미정";
}

function FriendRow({
  friend,
  actionLabel,
  onAction,
  inviteLabel,
  onInvite,
  selected,
  onSelect,
}: {
  friend: RoomFriend;
  actionLabel?: string;
  onAction?: (friend: RoomFriend) => void;
  inviteLabel?: string;
  onInvite?: (friend: RoomFriend) => void;
  selected?: boolean;
  onSelect?: (friend: RoomFriend) => void;
}) {
  const meta = participantTypeMeta(friend.participant_type);
  const Icon = meta.icon;
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
  return (
    <div className="dc-friend-row" data-type={meta.tone} data-selected={selected ? "true" : "false"}>
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
      {onAction || onInvite ? (
        <div className="dc-friend-actions">
          {onInvite && (
            <button type="button" className="dc-friend-action" onClick={() => onInvite(friend)}>
              <UserPlus size={15} />
              {inviteLabel || "방에 초대"}
            </button>
          )}
          {onAction && (
            <button type="button" className="dc-friend-action" onClick={() => onAction(friend)}>
              <UserPlus size={15} />
              {actionLabel || "추가"}
            </button>
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

export default function FriendsView({
  typeFilter,
  activeRoomName = "",
  onInviteFriendToRoom,
  onFriendsChanged,
  selectedFriendId,
  onSelectFriend,
}: {
  typeFilter: ParticipantType | null;
  activeRoomName?: string;
  onInviteFriendToRoom?: (friend: RoomFriend) => Promise<void>;
  onFriendsChanged?: (payload: RoomFriendsResponse) => void;
  selectedFriendId?: string;
  onSelectFriend?: (friend: RoomFriend) => void;
}) {
  const [payload, setPayload] = useState<RoomFriendsResponse>({ friends: [], candidates: [] });
  const [filter, setFilter] = useState<"online" | "all" | "add">("online");
  const [query, setQuery] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [participantType, setParticipantType] = useState<ParticipantType>("subscription_ai");
  const [providerKind, setProviderKind] = useState("");
  const [status, setStatus] = useState("");
  const [busyId, setBusyId] = useState("");
  const [loading, setLoading] = useState(true);

  function refresh() {
    setLoading(true);
    fetchRoomFriends()
      .then((next) => {
        setPayload(next);
        onFriendsChanged?.(next);
        setStatus("");
      })
      .catch((error) => {
        setStatus(error instanceof Error ? error.message : "친구 목록을 불러오지 못했습니다");
      })
      .finally(() => setLoading(false));
  }

  useEffect(refresh, []);

  const visibleFriends = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return payload.friends.filter((friend) => {
      if (typeFilter && friend.participant_type !== typeFilter) return false;
      if (filter === "online" && !["online", "working"].includes(friend.status)) return false;
      if (!needle) return true;
      return [friend.display_name, friend.provider_kind, friend.participant_type, friend.last_meeting_id].some((value) =>
        String(value || "").toLowerCase().includes(needle)
      );
    });
  }, [filter, payload.friends, query, typeFilter]);

  const visibleCandidates = useMemo(() => {
    if (!typeFilter) return payload.candidates;
    return payload.candidates.filter((friend) => friend.participant_type === typeFilter);
  }, [payload.candidates, typeFilter]);
  const selectedFriend = useMemo(
    () =>
      payload.friends.find((friend) => friend.friend_id === selectedFriendId) ||
      visibleFriends[0] ||
      payload.friends[0] ||
      null,
    [payload.friends, selectedFriendId, visibleFriends]
  );

  async function handleAddCandidate(friend: RoomFriend) {
    setBusyId(friend.friend_id);
    setStatus("");
    try {
      const result = await addRoomFriend(friend);
      const nextPayload = {
        friends: result.friends,
        candidates: payload.candidates.filter((candidate) => candidate.friend_id !== friend.friend_id),
      };
      setPayload(nextPayload);
      onFriendsChanged?.(nextPayload);
      setStatus(`${friend.display_name} 추가됨`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "친구 추가 실패");
    } finally {
      setBusyId("");
    }
  }

  async function handleAddManual() {
    const name = displayName.trim();
    if (!name) {
      setStatus("이름을 입력하세요");
      return;
    }
    setBusyId("manual");
    setStatus("");
    try {
      const result = await addRoomFriend({
        display_name: name,
        participant_type: participantType,
        provider_kind: providerKind.trim(),
        status: "offline",
        source: "manual",
      });
      const nextPayload = { ...payload, friends: result.friends };
      setPayload(nextPayload);
      onFriendsChanged?.(nextPayload);
      setDisplayName("");
      setProviderKind("");
      setStatus(`${name} 추가됨`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "친구 추가 실패");
    } finally {
      setBusyId("");
    }
  }

  async function handleInvite(friend: RoomFriend) {
    if (!onInviteFriendToRoom) return;
    const busyKey = `invite:${friend.friend_id}`;
    setBusyId(busyKey);
    setStatus("");
    try {
      await onInviteFriendToRoom(friend);
      setStatus(`${friend.display_name} ${activeRoomName ? `${activeRoomName}에 ` : ""}초대됨`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "방 초대 실패");
    } finally {
      setBusyId("");
    }
  }

  return (
    <div className="dc-friends-page">
      <header className="dc-friends-head">
        <div className="dc-friends-title">
          <Users size={20} />
          <span>친구</span>
        </div>
        <nav className="dc-friends-tabs" aria-label="친구 필터">
          <button type="button" data-active={filter === "online"} onClick={() => setFilter("online")}>
            온라인
          </button>
          <button type="button" data-active={filter === "all"} onClick={() => setFilter("all")}>
            모두
          </button>
          <button
            type="button"
            className="add-tab"
            data-active={filter === "add"}
            onClick={() => setFilter("add")}
          >
            친구 추가하기
          </button>
        </nav>
      </header>

      <div className="dc-friends-body">
        <main className="dc-friends-main">
          <label className="dc-friends-search">
            <Search size={16} />
            <input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="검색하기"
            />
          </label>

          {filter === "add" && (
          <section className="dc-friend-add-panel">
            <div className="min-w-0">
              <h2>친구 추가하기</h2>
              <p>사람, 구독형 AI, API, Local 세션을 디렉터리에 저장해 다시 초대할 수 있게 준비합니다.</p>
            </div>
            <div className="dc-friend-add-grid">
              <input
                value={displayName}
                onChange={(event) => setDisplayName(event.target.value)}
                placeholder="이름 또는 세션 별명"
              />
              <select
                value={participantType}
                onChange={(event) => setParticipantType(event.target.value as ParticipantType)}
              >
                {PARTICIPANT_TYPE_OPTIONS.map((option) => (
                  <option key={option.id} value={option.id}>
                    {option.label}
                  </option>
                ))}
              </select>
              <input
                value={providerKind}
                onChange={(event) => setProviderKind(event.target.value)}
                placeholder="provider 예: codex, claude, lmstudio"
              />
              <button type="button" onClick={handleAddManual} disabled={busyId === "manual"}>
                <UserPlus size={15} />
                추가
              </button>
            </div>
          </section>
          )}

          {status && <p className="dc-friend-status-line preserve-words">{status}</p>}

          <section className="dc-friend-section">
            <h2>{filter === "online" ? "온라인" : "모든 친구"} — {visibleFriends.length}</h2>
            {loading ? (
              <p className="dc-friend-empty">불러오는 중...</p>
            ) : visibleFriends.length ? (
              visibleFriends.map((friend) => (
                <FriendRow
                  key={friend.friend_id}
                  friend={friend}
                  inviteLabel={busyId === `invite:${friend.friend_id}` ? "초대 중" : "방에 초대"}
                  onInvite={onInviteFriendToRoom ? handleInvite : undefined}
                  selected={selectedFriend?.friend_id === friend.friend_id}
                  onSelect={onSelectFriend}
                />
              ))
            ) : (
              <p className="dc-friend-empty">아직 친구가 없습니다. 이전 세션 후보를 추가해 보세요.</p>
            )}
          </section>

          <section className="dc-friend-section">
            <h2>이전 세션에서 추가 — {visibleCandidates.length}</h2>
            {visibleCandidates.length ? (
              visibleCandidates.slice(0, 10).map((friend) => (
                <FriendRow
                  key={friend.friend_id}
                  friend={friend}
                  actionLabel={busyId === friend.friend_id ? "추가 중" : "친구 추가"}
                  onAction={handleAddCandidate}
                />
              ))
            ) : (
              <p className="dc-friend-empty">추가할 수 있는 새 세션 후보가 없습니다.</p>
            )}
          </section>
        </main>

        <aside className="dc-friends-activity">
          <h2>{selectedFriend ? "프로필" : "현재 활동 중"}</h2>
          <FriendProfileCard
            friend={selectedFriend}
            activeRoomName={activeRoomName}
            inviteLabel={
              selectedFriend && busyId === `invite:${selectedFriend.friend_id}` ? "초대 중" : "방에 초대"
            }
            onInvite={onInviteFriendToRoom ? handleInvite : undefined}
          />
          <FriendDmPanel friend={selectedFriend} />
        </aside>
      </div>
    </div>
  );
}
