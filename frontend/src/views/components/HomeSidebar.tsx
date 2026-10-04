import { useMemo, useState } from "react";
import { Bot, Cloud, Compass, Cpu, Plus, Search, User, Users, Wifi } from "lucide-react";
import type { RoomFriend, UserProfileIdentity } from "../../api";
import type { HomeFilter } from "../../app/friendsDirectoryTypes";
import { roomFriendMatchesSearch } from "../../lib/friendSearch";
import UserPanel from "./UserPanel";

const HOME_ITEMS = [
  { id: "friends", label: "친구", icon: Users },
  { id: "subscription_ai", label: "구독형 AI", icon: Bot },
  { id: "api", label: "API", icon: Cloud },
  { id: "local", label: "Local", icon: Cpu },
  { id: "remote", label: "Remote", icon: Wifi },
  { id: "human", label: "사람", icon: User },
] as const;

export default function HomeSidebar({
  activeFilter,
  onFilterChange,
  onlineCount,
  agentCount,
  hasBackendError,
  profileIdentity,
  friends = [],
  selectedFriendId,
  onFriendSelect,
  onStartAddFriend,
  onStartAddAgent,
}: {
  activeFilter: HomeFilter;
  onFilterChange: (filter: HomeFilter) => void;
  onlineCount: number;
  agentCount: number;
  hasBackendError: boolean;
  profileIdentity?: UserProfileIdentity;
  friends?: RoomFriend[];
  selectedFriendId?: string;
  onFriendSelect?: (friend: RoomFriend) => void;
  onStartAddFriend?: (draftName?: string) => void;
  onStartAddAgent?: () => void;
}) {
  const [dmQuery, setDmQuery] = useState("");
  const cleanDmQuery = dmQuery.trim();
  const filteredDirectMessages = useMemo(() => {
    const needle = cleanDmQuery.toLowerCase();
    if (!needle) return friends.slice(0, 12);
    return friends.filter((friend) => roomFriendMatchesSearch(friend, needle));
  }, [cleanDmQuery, friends]);
  return (
    <aside className="dc-sidebar dc-home-sidebar flex shrink-0 flex-col" aria-label="친구 목록">
      <header className="dc-home-search">
        <label>
          <span className="sr-only">대화 찾기 또는 시작하기</span>
          <Search size={15} />
          <input
            type="search"
            value={dmQuery}
            onChange={(event) => setDmQuery(event.target.value)}
            placeholder="대화 찾기 또는 시작하기"
          />
        </label>
      </header>
      <nav className="min-h-0 flex-1 overflow-y-auto px-2 py-3 chat-scroll" aria-label="친구 분류">
        {onStartAddAgent && (
          <button type="button" className="dc-home-agent-add" onClick={onStartAddAgent}>
            <Bot size={18} />
            <span>에이전트 추가</span>
          </button>
        )}
        {HOME_ITEMS.map((item) => {
          const Icon = item.icon;
          return (
            <button
              key={item.id}
              type="button"
              className="dc-home-nav-item"
              data-active={activeFilter === item.id}
              onClick={() => onFilterChange(item.id)}
            >
              <Icon size={20} />
              <span>{item.label}</span>
            </button>
          );
        })}
        <div className="dc-dm-section">
          <div className="dc-dm-title">
            <span>저장된 친구</span>
            <button type="button" aria-label="친구 추가하기" onClick={() => onStartAddFriend?.()}>
              <Plus size={14} />
            </button>
          </div>
          {filteredDirectMessages.length ? (
            filteredDirectMessages.map((friend) => {
              const meta = HOME_ITEMS.find((item) => item.id === friend.participant_type);
              const Icon = meta?.icon || Compass;
              return (
                <button
                  key={friend.friend_id}
                  type="button"
                  className="dc-dm-row"
                  data-status={friend.status || "offline"}
                  data-profile-selected={selectedFriendId === friend.friend_id}
                  data-active={selectedFriendId === friend.friend_id}
                  onClick={() => onFriendSelect?.(friend)}
                  title={`${friend.display_name} · ${meta?.label || "미분류"}`}
                >
                  <span className="dc-dm-avatar">
                    <Icon size={16} />
                    <span className="dc-dm-status-dot" aria-hidden />
                  </span>
                  <span className="min-w-0">
                    <span className="block truncate preserve-words">{friend.display_name}</span>
                    <span className="block truncate text-[11px] font-semibold text-text-muted preserve-words">
                      {meta?.label || "미분류"}
                    </span>
                  </span>
                </button>
              );
            })
          ) : cleanDmQuery ? (
            <button type="button" className="dc-dm-row" onClick={() => onStartAddFriend?.(cleanDmQuery)}>
              <Compass size={18} />
              <span className="preserve-words">"{cleanDmQuery}" 친구로 추가</span>
            </button>
          ) : (
            <button type="button" className="dc-dm-row" onClick={() => onStartAddFriend?.()}>
              <Compass size={18} />
              <span>이전 세션을 친구로 저장</span>
            </button>
          )}
        </div>
      </nav>
      <footer className="dc-user-area shrink-0">
        <UserPanel
          onlineCount={onlineCount}
          agentCount={agentCount}
          hasBackendError={hasBackendError}
          profileIdentity={profileIdentity}
        />
      </footer>
    </aside>
  );
}
