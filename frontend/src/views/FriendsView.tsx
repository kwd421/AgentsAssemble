import { useEffect, useMemo, useState } from "react";
import { Bot, Search, UserPlus, Users } from "lucide-react";
import {
  addRoomFriend,
  deleteRoomFriend,
  fetchRoomFriends,
  type LiveAgentProcessGroup,
  type ParticipantType,
  type RoomFriend,
  type RoomFriendsResponse,
} from "../api";
import { roomFriendMatchesSearch } from "../lib/friendSearch";
import { PARTICIPANT_TYPE_OPTIONS } from "../lib/participantTypes";
import { isActivePresence } from "../lib/presenceStatus";
import FriendDmPanel from "./components/FriendDmPanel";
import FriendProfileCard from "./components/FriendProfileCard";
import FriendRow from "./components/FriendRow";

export type FriendListFilter = "online" | "all" | "add";

function friendMatchesDirectory(
  friend: RoomFriend,
  {
    typeFilter,
    filter,
    needle,
  }: {
    typeFilter: ParticipantType | null;
    filter: FriendListFilter;
    needle: string;
  }
): boolean {
  if (typeFilter && friend.participant_type !== typeFilter) return false;
  if (filter === "online" && !isActivePresence(friend.status)) return false;
  if (!needle) return true;
  return roomFriendMatchesSearch(friend, needle);
}

export default function FriendsView({
  typeFilter,
  filter,
  onFilterChange,
  onFriendsChanged,
  selectedFriendId,
  activeDmFriendId,
  initialDisplayName = "",
  onActiveDmFriendChange,
  onSelectFriend,
  processGroups = [],
  onSessionActionComplete,
  onStartAddAgent,
}: {
  typeFilter: ParticipantType | null;
  filter: FriendListFilter;
  initialDisplayName?: string;
  onFilterChange: (filter: FriendListFilter) => void;
  onFriendsChanged?: (payload: RoomFriendsResponse) => void;
  selectedFriendId?: string;
  activeDmFriendId?: string;
  onActiveDmFriendChange?: (friendId: string) => void;
  onSelectFriend?: (friend: RoomFriend) => void;
  processGroups?: LiveAgentProcessGroup[];
  onSessionActionComplete?: () => void;
  onStartAddAgent?: () => void;
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
  useEffect(() => {
    if (filter !== "add") return;
    setDisplayName(initialDisplayName);
  }, [filter, initialDisplayName]);

  const visibleFriends = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return payload.friends.filter((friend) => friendMatchesDirectory(friend, { typeFilter, filter, needle }));
  }, [filter, payload.friends, query, typeFilter]);

  const visibleCandidates = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const typedCandidates = typeFilter
      ? payload.candidates.filter((friend) => friend.participant_type === typeFilter)
      : payload.candidates;
    if (!needle) return typedCandidates;
    return typedCandidates.filter((friend) => roomFriendMatchesSearch(friend, needle));
  }, [payload.candidates, query, typeFilter]);
  const selectedFriend = useMemo(() => {
    if (activeDmFriendId) return null;
    const explicitSelection = payload.friends.find((friend) => {
      if (friend.friend_id !== selectedFriendId) return false;
      if (typeFilter && friend.participant_type !== typeFilter) return false;
      return true;
    });
    if (explicitSelection) return explicitSelection;
    if (filter === "online" || filter === "all") return visibleFriends[0] || null;
    return null;
  }, [activeDmFriendId, filter, payload.friends, selectedFriendId, typeFilter, visibleFriends]);
  const activeDmFriend = useMemo(
    () => payload.friends.find((friend) => friend.friend_id === activeDmFriendId) || null,
    [activeDmFriendId, payload.friends]
  );
  const profileFriend = activeDmFriend || selectedFriend;

  function showDirectory(nextFilter: FriendListFilter) {
    onFilterChange(nextFilter);
    onActiveDmFriendChange?.("");
  }

  function openFriendDm(friend: RoomFriend) {
    onSelectFriend?.(friend);
    onActiveDmFriendChange?.(friend.friend_id);
    setDmFocusSignal((value) => value + 1);
  }

  function showAddedFriend(friend: RoomFriend) {
    onSelectFriend?.(friend);
    onActiveDmFriendChange?.("");
    onFilterChange("all");
  }

  function showFriendProfile(friend: RoomFriend) {
    onSelectFriend?.(friend);
    onActiveDmFriendChange?.("");
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
      showAddedFriend(result.friend);
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
      showAddedFriend(result.friend);
      setDisplayName("");
      setProviderKind("");
      setStatus(`${name} 추가됨`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "친구 추가 실패");
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
      const nextSelection =
        result.friends.find((candidate) =>
          friendMatchesDirectory(candidate, { typeFilter, filter, needle: query.trim().toLowerCase() })
        ) || null;
      const shouldMoveSelection =
        selectedFriendId === friend.friend_id || activeDmFriendId === friend.friend_id;
      setPayload({ friends: result.friends, candidates: result.candidates });
      onFriendsChanged?.(result);
      if (activeDmFriendId === friend.friend_id) {
        onActiveDmFriendChange?.("");
      }
      if (shouldMoveSelection && nextSelection) {
        onSelectFriend?.(nextSelection);
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
            <FriendDmPanel
              friend={activeDmFriend}
              focusSignal={dmFocusSignal}
              layout="channel"
              onShowProfile={showFriendProfile}
            />
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
            <button type="button" className="dc-agent-add-entry" onClick={onStartAddAgent}>
              <Bot size={16} />
              에이전트 추가
            </button>
          )}

          {filter === "add" && (
          <section className="dc-friend-add-panel">
            <div className="min-w-0">
              <h2>친구 추가하기</h2>
              <p>사람, 구독형 AI, API, Local 세션을 친구 목록에 저장하고 DM과 방 초대 후보로 관리합니다.</p>
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

          {filter !== "add" && (
          <section className="dc-friend-section">
            <h2>{filter === "online" ? "온라인" : "모든 친구"} — {visibleFriends.length}</h2>
            {loading ? (
              <p className="dc-friend-empty">불러오는 중...</p>
            ) : visibleFriends.length ? (
              visibleFriends.map((friend) => (
                <FriendRow
                  key={friend.friend_id}
                  friend={friend}
                  onStartDm={openFriendDm}
                  onDelete={handleDeleteFriend}
                  selected={selectedFriend?.friend_id === friend.friend_id}
                  onSelect={onSelectFriend}
                />
              ))
            ) : (
              <p className="dc-friend-empty">
                {filter === "online"
                  ? "온라인 친구가 없습니다. 모두 탭에서 저장된 친구를 관리할 수 있습니다."
                  : "아직 친구가 없습니다. 친구 추가하기에서 이전 세션을 친구로 저장해 보세요."}
              </p>
            )}
          </section>
          )}

          {filter === "add" && (
          <section className="dc-friend-section">
            <h2>이전 세션 후보 — {visibleCandidates.length}</h2>
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
          )}
          </>
          )}
        </main>

        <aside className="dc-friends-activity">
          <h2>{profileFriend ? "프로필" : "현재 활동 중"}</h2>
          <FriendProfileCard
            friend={profileFriend}
            onStartDm={openFriendDm}
            onDelete={profileFriend ? handleDeleteFriend : undefined}
            processGroups={processGroups}
            onSessionActionComplete={() => {
              onSessionActionComplete?.();
              refresh();
            }}
          />
        </aside>
      </div>
    </div>
  );
}
