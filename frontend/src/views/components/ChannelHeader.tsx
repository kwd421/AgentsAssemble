import { useState, type ChangeEvent, type ReactNode } from "react";
import { ArrowLeft, Bell, ChevronRight, Pin, Search, Users, PanelRight } from "lucide-react";

type HeaderPanel = "notifications" | "pins" | "search";

export type ChannelHeaderActions = {
  notificationSummary?: string;
  lastReadSummary?: string;
  pinnedSummary?: string;
  onMarkRead?: () => void;
  onOpenSettings?: () => void;
};

/**
 * Discord-style channel header: a fixed bar at the top of the central column
 * with the channel name, an optional topic, optional right-aligned actions,
 * and the shell-owned member-list toggle.
 */
export default function ChannelHeader({
  icon,
  title,
  subtitle,
  children,
  headerActions,
  membersOpen,
  onToggleMembers,
  onOpenMobileSidebar,
  onOpenMobileInfo,
}: {
  icon: ReactNode;
  title: string;
  subtitle?: string;
  children?: ReactNode;
  headerActions?: ChannelHeaderActions;
  membersOpen?: boolean;
  onToggleMembers?: () => void;
  onOpenMobileSidebar?: () => void;
  onOpenMobileInfo?: () => void;
}) {
  const [activePanel, setActivePanel] = useState<HeaderPanel | null>(null);
  const [searchQuery, setSearchQuery] = useState("");

  function togglePanel(panel: HeaderPanel) {
    setActivePanel((current) => (current === panel ? null : panel));
  }

  function handleSearchChange(event: ChangeEvent<HTMLInputElement>) {
    const nextQuery = event.currentTarget.value;
    setSearchQuery(nextQuery);
    setActivePanel(nextQuery.trim() ? "search" : null);
  }

  const notificationSummary = headerActions?.notificationSummary || "서버 기본 알림을 사용 중입니다.";
  const lastReadSummary = headerActions?.lastReadSummary || "아직 이 채널을 읽음으로 표시하지 않았습니다.";
  const pinnedSummary = headerActions?.pinnedSummary || "아직 고정된 메시지가 없습니다.";

  return (
    <header className="dc-chat-head flex h-12 shrink-0 items-center gap-2 px-3 lg:px-4">
      {onOpenMobileSidebar && (
        <button
          type="button"
          className="dc-mobile-head-back"
          onClick={onOpenMobileSidebar}
          aria-label="채널 목록 열기"
        >
          <ArrowLeft size={25} />
        </button>
      )}
      <button
        type="button"
        className="dc-mobile-head-title"
        onClick={onOpenMobileInfo}
        disabled={!onOpenMobileInfo}
        aria-label={`${title} 채널 정보 열기`}
      >
        <span className="dc-mobile-head-channel-icon" aria-hidden>
          {icon}
        </span>
        <span className="truncate preserve-words">{title}</span>
        <ChevronRight size={16} aria-hidden />
      </button>
      <span className="dc-desktop-head-channel-icon shrink-0 text-text-muted">{icon}</span>
      <h1 className="dc-desktop-head-title shrink-0 text-[15px] font-bold text-text-primary preserve-words">
        {title}
      </h1>
      {subtitle && (
        <>
          <span className="hidden h-4 w-px bg-line sm:block" aria-hidden />
          <p className="hidden min-w-0 truncate text-[13px] text-text-muted preserve-words sm:block">
            {subtitle}
          </p>
        </>
      )}
      <div className="dc-head-actions ml-auto flex shrink-0 items-center gap-1.5">
          {children}
        <button
          type="button"
          className="dc-head-icon dc-mobile-search-trigger"
          aria-label="채널 검색"
          aria-pressed={activePanel === "search"}
          onClick={() => togglePanel("search")}
        >
          <Search size={20} />
        </button>
        <button
          type="button"
          className="dc-head-icon"
          aria-label="알림 설정"
          aria-pressed={activePanel === "notifications"}
          onClick={() => togglePanel("notifications")}
        >
          <Bell size={17} />
        </button>
        <button
          type="button"
          className="dc-head-icon"
          aria-label="고정 메시지"
          aria-pressed={activePanel === "pins"}
          onClick={() => togglePanel("pins")}
        >
          <Pin size={17} />
        </button>
        <label className="dc-head-search hidden md:flex">
          <span className="sr-only">{title} 검색</span>
          <input
            type="search"
            placeholder={`${title} 검색`}
            value={searchQuery}
            onChange={handleSearchChange}
            onFocus={() => {
              if (searchQuery.trim()) setActivePanel("search");
            }}
          />
          <Search size={14} aria-hidden />
        </label>
        {onToggleMembers && (
          <button
            type="button"
            onClick={onToggleMembers}
            aria-label="멤버 목록 토글"
            aria-pressed={membersOpen}
            className={`dc-head-icon hidden xl:grid ${
              membersOpen ? "text-text-primary" : "text-text-muted"
            }`}
          >
            {membersOpen ? <Users size={18} /> : <PanelRight size={18} />}
          </button>
        )}
        {activePanel && (
          <section className="dc-head-popover" role="status" aria-live="polite">
            {activePanel === "notifications" && (
              <>
                <p className="dc-head-popover-title">채널 알림</p>
                <p className="dc-head-popover-copy preserve-words">{notificationSummary}</p>
                <p className="dc-head-popover-copy preserve-words">{lastReadSummary}</p>
                <div className="dc-head-popover-actions">
                  {headerActions?.onMarkRead && (
                    <button
                      type="button"
                      onClick={headerActions.onMarkRead}
                    >
                      읽음으로 표시
                    </button>
                  )}
                  {headerActions?.onOpenSettings && (
                    <button
                      type="button"
                      onClick={() => {
                        setActivePanel(null);
                        headerActions.onOpenSettings?.();
                      }}
                    >
                      채널 설정
                    </button>
                  )}
                </div>
              </>
            )}
            {activePanel === "pins" && (
              <>
                <p className="dc-head-popover-title">고정 메시지</p>
                <p className="dc-head-popover-copy preserve-words">{pinnedSummary}</p>
              </>
            )}
            {activePanel === "search" && (
              <>
                <p className="dc-head-popover-title">채널 검색</p>
                <p className="dc-head-popover-copy preserve-words">
                  {searchQuery.trim()
                    ? `"${searchQuery.trim()}" 검색어를 이 채널 안에서 확인 중입니다.`
                    : "검색어를 입력하면 이 채널의 검색 상태가 표시됩니다."}
                </p>
              </>
            )}
          </section>
        )}
      </div>
    </header>
  );
}
