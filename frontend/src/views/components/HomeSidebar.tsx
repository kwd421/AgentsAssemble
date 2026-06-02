import { Bot, Cloud, Compass, Cpu, MessageCircle, Search, User, Users, Wifi } from "lucide-react";
import UserPanel from "./UserPanel";

const HOME_ITEMS = [
  { id: "friends", label: "친구", icon: Users },
  { id: "subscription_ai", label: "구독형 AI", icon: Bot },
  { id: "api", label: "API", icon: Cloud },
  { id: "local", label: "Local", icon: Cpu },
  { id: "remote", label: "Remote", icon: Wifi },
  { id: "human", label: "사람", icon: User },
] as const;

export type HomeFilter = (typeof HOME_ITEMS)[number]["id"];

export default function HomeSidebar({
  activeFilter,
  onFilterChange,
  backendStatusText,
  onlineCount,
  agentCount,
  hasBackendError,
}: {
  activeFilter: HomeFilter;
  onFilterChange: (filter: HomeFilter) => void;
  backendStatusText: string;
  onlineCount: number;
  agentCount: number;
  hasBackendError: boolean;
}) {
  return (
    <aside className="dc-sidebar dc-home-sidebar flex shrink-0 flex-col" aria-label="친구와 DM">
      <header className="dc-home-search">
        <label>
          <span className="sr-only">대화 찾기 또는 시작하기</span>
          <Search size={15} />
          <input type="search" placeholder="대화 찾기 또는 시작하기" />
        </label>
      </header>
      <nav className="min-h-0 flex-1 overflow-y-auto px-2 py-3 chat-scroll" aria-label="친구 분류">
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
            <span>다이렉트 메시지</span>
            <MessageCircle size={14} />
          </div>
          <button type="button" className="dc-dm-row">
            <Compass size={18} />
            <span>이전 세션에서 친구 추가</span>
          </button>
        </div>
      </nav>
      <footer className="dc-user-area shrink-0 px-2 py-2">
        <UserPanel
          backendStatusText={backendStatusText}
          onlineCount={onlineCount}
          agentCount={agentCount}
          hasBackendError={hasBackendError}
        />
      </footer>
    </aside>
  );
}
