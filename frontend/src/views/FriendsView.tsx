import { useEffect, useMemo, useState } from "react";
import { Search, UserPlus, Users } from "lucide-react";
import {
  addRoomFriend,
  deleteRoomFriend,
  fetchRoomFriends,
  type ParticipantType,
  type RoomFriend,
  type RoomFriendsResponse,
} from "../api";
import { PARTICIPANT_TYPE_OPTIONS } from "../lib/participantTypes";
import FriendDmPanel from "./components/FriendDmPanel";
import FriendProfileCard from "./components/FriendProfileCard";
import FriendRow from "./components/FriendRow";

export type FriendListFilter = "online" | "all" | "add";

export default function FriendsView({
  typeFilter,
  filter,
  onFilterChange,
  activeRoomName = "",
  onInviteFriendToRoom,
  onFriendsChanged,
  selectedFriendId,
  activeDmFriendId,
  onActiveDmFriendChange,
  onSelectFriend,
}: {
  typeFilter: ParticipantType | null;
  filter: FriendListFilter;
  onFilterChange: (filter: FriendListFilter) => void;
  activeRoomName?: string;
  onInviteFriendToRoom?: (friend: RoomFriend) => Promise<void>;
  onFriendsChanged?: (payload: RoomFriendsResponse) => void;
  selectedFriendId?: string;
  activeDmFriendId?: string;
  onActiveDmFriendChange?: (friendId: string) => void;
  onSelectFriend?: (friend: RoomFriend) => void;
}) {
  const [payload, setPayload] = useState<RoomFriendsResponse>({ friends: [], candidates: [] });
  const [query, setQuery] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [participantType, setParticipantType] = useState<ParticipantType>("subscription_ai");
  const [providerKind, setProviderKind] = useState("");
  const [status, setStatus] = useState("");
  const [busyId, setBusyId] = useState("");
  const [loading, setLoading] = useState(true);
  const [dmFocusSignal, setDmFocusSignal] = useState(0);

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
  const activeDmFriend = useMemo(
    () => payload.friends.find((friend) => friend.friend_id === activeDmFriendId) || null,
    [activeDmFriendId, payload.friends]
  );
  const profileFriend = selectedFriend || activeDmFriend;

  function showDirectory(nextFilter: FriendListFilter) {
    onFilterChange(nextFilter);
    onActiveDmFriendChange?.("");
  }

  function openFriendDm(friend: RoomFriend) {
    onSelectFriend?.(friend);
    onActiveDmFriendChange?.(friend.friend_id);
    setDmFocusSignal((value) => value + 1);
  }

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

  async function handleDeleteFriend(friend: RoomFriend) {
    const busyKey = `delete:${friend.friend_id}`;
    setBusyId(busyKey);
    setStatus("");
    try {
      const result = await deleteRoomFriend(friend.friend_id);
      setPayload({ friends: result.friends, candidates: result.candidates });
      onFriendsChanged?.(result);
      if (activeDmFriendId === friend.friend_id) {
        onActiveDmFriendChange?.("");
      }
      setStatus(`${friend.display_name} 삭제됨`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "친구 삭제 실패");
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
          <button type="button" data-active={filter === "online" && !activeDmFriend} onClick={() => showDirectory("online")}>
            온라인
          </button>
          <button type="button" data-active={filter === "all" && !activeDmFriend} onClick={() => showDirectory("all")}>
            모두
          </button>
          <button
            type="button"
            className="add-tab"
            data-active={filter === "add" && !activeDmFriend}
            onClick={() => showDirectory("add")}
          >
            친구 추가하기
          </button>
        </nav>
      </header>

      <div className="dc-friends-body">
        <main className="dc-friends-main" data-mode={activeDmFriend ? "dm" : "directory"}>
          {activeDmFriend ? (
            <FriendDmPanel friend={activeDmFriend} focusSignal={dmFocusSignal} layout="channel" />
          ) : (
          <>
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
                  onStartDm={openFriendDm}
                  onDelete={handleDeleteFriend}
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
              visibleCandidates.map((friend) => (
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
          </>
          )}
        </main>

        <aside className="dc-friends-activity">
          <h2>{profileFriend ? "프로필" : "현재 활동 중"}</h2>
          <FriendProfileCard
            friend={profileFriend}
            activeRoomName={activeRoomName}
            onStartDm={openFriendDm}
            inviteLabel={
              profileFriend && busyId === `invite:${profileFriend.friend_id}` ? "초대 중" : "방에 초대"
            }
            onInvite={onInviteFriendToRoom ? handleInvite : undefined}
            onDelete={profileFriend ? handleDeleteFriend : undefined}
          />
        </aside>
      </div>
    </div>
  );
}
