import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
} from "react";
import { ArrowLeft, Bell, ChevronRight, Pin, Search, Users, PanelRight } from "lucide-react";
import type { MessagePin } from "../../api/messagePins";
import "../../styles/channel-search.css";

type HeaderPanel = "notifications" | "pins" | "search";

export type ChannelHeaderActions = {
  notificationSummary?: string;
  lastReadSummary?: string;
  lastReadCursor?: string;
  latestReadCursor?: string;
  pinnedSummary?: string;
  pinnedItems?: MessagePin[];
  pinsLoading?: boolean;
  pinsError?: string;
  onSelectPin?: (pin: MessagePin) => void;
  onUnpin?: (pin: MessagePin) => void;
  onOpenPins?: () => void;
  onMarkRead?: (cursor?: string) => void;
  onOpenSettings?: () => void;
};

export type ChannelSearchItem = {
  id: string;
  author: string;
  body: string;
  meta?: string;
  exactTime?: string;
  onSelect: () => void;
};

export type ChannelSearchScope = "channel" | "all";

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
  searchItems = [],
  searchHasMore = false,
  searchLoadingMore = false,
  searchLoading = false,
  searchError = "",
  externalSearch = false,
  searchQuery: controlledSearchQuery,
  searchScope = "channel",
  onSearchQueryChange,
  onSearchScopeChange,
  onLoadMoreSearch,
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
  searchItems?: ChannelSearchItem[];
  searchHasMore?: boolean;
  searchLoadingMore?: boolean;
  searchLoading?: boolean;
  searchError?: string;
  externalSearch?: boolean;
  searchQuery?: string;
  searchScope?: ChannelSearchScope;
  onSearchQueryChange?: (query: string) => void;
  onSearchScopeChange?: (scope: ChannelSearchScope) => void;
  onLoadMoreSearch?: () => void;
}) {
  const [activePanel, setActivePanel] = useState<HeaderPanel | null>(() =>
    controlledSearchQuery?.trim() ? "search" : null
  );
  const [uncontrolledSearchQuery, setUncontrolledSearchQuery] = useState("");
  const [activeSearchIndex, setActiveSearchIndex] = useState(-1);
  const popupSearchRef = useRef<HTMLInputElement | null>(null);
  const searchQuery = controlledSearchQuery ?? uncontrolledSearchQuery;

  useEffect(() => {
    function handleWindowKeyDown(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLocaleLowerCase() === "f") {
        event.preventDefault();
        setActivePanel("search");
        window.requestAnimationFrame(() => {
          popupSearchRef.current?.focus();
          popupSearchRef.current?.select();
        });
        return;
      }
      if (event.key === "Escape" && activePanel) {
        event.preventDefault();
        setActivePanel(null);
      }
    }
    window.addEventListener("keydown", handleWindowKeyDown);
    return () => window.removeEventListener("keydown", handleWindowKeyDown);
  }, [activePanel]);

  function togglePanel(panel: HeaderPanel) {
    setActivePanel((current) => (current === panel ? null : panel));
  }

  function handleSearchChange(event: ChangeEvent<HTMLInputElement>) {
    const nextQuery = event.currentTarget.value;
    if (controlledSearchQuery === undefined) setUncontrolledSearchQuery(nextQuery);
    setActiveSearchIndex(-1);
    setActivePanel(nextQuery.trim() ? "search" : null);
    onSearchQueryChange?.(nextQuery);
  }

  function openMemberSurface() {
    if (
      onOpenMobileInfo
      && window.matchMedia("(max-width: 760px)").matches
    ) {
      onOpenMobileInfo();
      return;
    }
    onToggleMembers?.();
  }

  const notificationSummary = headerActions?.notificationSummary || "서버 기본 알림을 사용 중입니다.";
  const lastReadSummary = headerActions?.lastReadSummary || "아직 이 채널을 읽음으로 표시하지 않았습니다.";
  const pinnedSummary = headerActions?.pinnedSummary || "아직 고정된 메시지가 없습니다.";
  const searchNeedle = searchQuery.trim().toLocaleLowerCase();
  const searchMatches = useMemo(() => {
    if (!searchNeedle) return [];
    if (externalSearch) return searchItems;
    return searchItems
      .filter((item) =>
        `${item.author}\n${item.body}`.toLocaleLowerCase().includes(searchNeedle)
      )
      .slice()
      .reverse();
  }, [externalSearch, searchItems, searchNeedle]);

  function selectSearchItem(item: ChannelSearchItem) {
    item.onSelect();
    if (!externalSearch) setActivePanel(null);
  }

  function handleSearchKeyDown(event: ReactKeyboardEvent<HTMLInputElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      setActivePanel(null);
      return;
    }
    if (!searchMatches.length) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActiveSearchIndex((current) =>
        current < 0 ? 0 : (current + 1) % searchMatches.length
      );
      return;
    }
    if (event.key === "ArrowUp") {
      event.preventDefault();
      setActiveSearchIndex((current) =>
        current <= 0 ? searchMatches.length - 1 : current - 1
      );
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      selectSearchItem(searchMatches[activeSearchIndex < 0 ? 0 : activeSearchIndex]);
    }
  }

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
          onClick={() => {
            if (activePanel !== "pins") headerActions?.onOpenPins?.();
            togglePanel("pins");
          }}
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
            onKeyDown={handleSearchKeyDown}
            onFocus={() => {
              if (searchQuery.trim()) setActivePanel("search");
            }}
          />
          <Search size={14} aria-hidden />
        </label>
        {onToggleMembers && (
          <button
            type="button"
            onClick={openMemberSurface}
            aria-label="멤버 목록 토글"
            aria-pressed={membersOpen}
            className={`dc-head-icon ${
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
                      onClick={() => headerActions.onMarkRead?.(headerActions.latestReadCursor)}
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
                {headerActions?.pinsLoading ? (
                  <p className="dc-head-popover-copy preserve-words">고정 메시지를 불러오는 중입니다.</p>
                ) : headerActions?.pinsError ? (
                  <p className="dc-head-popover-copy preserve-words" role="alert">
                    {headerActions.pinsError}
                  </p>
                ) : headerActions?.pinnedItems?.length ? (
                  <div className="dc-head-search-results" role="list" aria-label="고정 메시지 목록">
                    {headerActions.pinnedItems.map((pin) => (
                      <div className="dc-pinned-message-card" role="listitem" key={pin.event_id}>
                        <button type="button" onClick={() => headerActions.onSelectPin?.(pin)}>
                          <span className="dc-head-search-result-author preserve-words">
                            {pin.author || "Room"}
                          </span>
                          <span className="dc-head-search-result-meta preserve-words">
                            {new Date(pin.created_at).toLocaleString("ko-KR")}
                          </span>
                          <span className="dc-head-search-result-body preserve-words">
                            {pin.content || pin.attachment_filenames.join(", ")}
                          </span>
                        </button>
                        {headerActions.onUnpin && (
                          <button
                            type="button"
                            className="dc-pinned-message-remove"
                            onClick={() => headerActions.onUnpin?.(pin)}
                            aria-label={`${pin.author || "Room"} 메시지 고정 해제`}
                          >
                            고정 해제
                          </button>
                        )}
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="dc-head-popover-copy preserve-words">{pinnedSummary}</p>
                )}
              </>
            )}
            {activePanel === "search" && (
              <>
                <p className="dc-head-popover-title">채널 검색</p>
                {onSearchScopeChange && (
                  <div className="dc-head-search-scope" role="group" aria-label="검색 범위">
                    <button
                      type="button"
                      aria-pressed={searchScope === "channel"}
                      onClick={() => onSearchScopeChange("channel")}
                    >
                      현재 채널
                    </button>
                    <button
                      type="button"
                      aria-pressed={searchScope === "all"}
                      onClick={() => onSearchScopeChange("all")}
                    >
                      모든 채널
                    </button>
                  </div>
                )}
                <label className="dc-head-popover-search">
                  <span className="sr-only">{title} 검색어</span>
                  <Search size={14} aria-hidden />
                  <input
                    ref={popupSearchRef}
                    type="search"
                    aria-label={`${title} 검색어`}
                    aria-activedescendant={
                      searchMatches.length
                        ? `channel-search-result-${searchMatches[activeSearchIndex < 0 ? 0 : activeSearchIndex].id}`
                        : undefined
                    }
                    placeholder="메시지 또는 작성자 검색"
                    value={searchQuery}
                    onChange={handleSearchChange}
                    onKeyDown={handleSearchKeyDown}
                    autoFocus
                  />
                </label>
                {!searchNeedle ? (
                  <p className="dc-head-popover-copy preserve-words">
                    검색어를 입력하면 {searchScope === "all" ? "읽을 수 있는 모든 채널" : "이 채널"}의 전체 메시지에서 찾습니다.
                  </p>
                ) : searchLoading && !searchMatches.length ? (
                  <p className="dc-head-popover-copy preserve-words">검색하는 중입니다.</p>
                ) : searchError ? (
                  <p className="dc-head-popover-copy preserve-words" role="alert">
                    {searchError}
                  </p>
                ) : searchMatches.length ? (
                  <>
                    <p className="dc-head-popover-copy preserve-words">
                      {searchMatches.length}개의 결과를 찾았습니다.
                    </p>
                    <div className="dc-head-search-results" role="list" aria-label="채널 검색 결과">
                      {searchMatches.map((item, index) => (
                        <button
                          key={item.id}
                          id={`channel-search-result-${item.id}`}
                          type="button"
                          data-active={index === (activeSearchIndex < 0 ? 0 : activeSearchIndex)}
                          onClick={() => selectSearchItem(item)}
                          title={item.exactTime}
                        >
                          <span className="dc-head-search-result-author preserve-words">
                            {item.author || "Room"}
                          </span>
                          {item.meta && (
                            <span className="dc-head-search-result-meta preserve-words">
                              {item.meta}
                            </span>
                          )}
                          <span className="dc-head-search-result-body preserve-words">
                            {item.body}
                          </span>
                        </button>
                      ))}
                    </div>
                  </>
                ) : (
                  <p className="dc-head-popover-copy preserve-words">
                    일치하는 메시지가 없습니다.
                  </p>
                )}
                {searchNeedle && searchHasMore && onLoadMoreSearch && (
                  <div className="dc-head-popover-actions">
                    <button
                      type="button"
                      disabled={searchLoadingMore}
                      onClick={onLoadMoreSearch}
                    >
                      {searchLoadingMore ? "검색 결과 불러오는 중..." : "검색 결과 더 보기"}
                    </button>
                  </div>
                )}
              </>
            )}
          </section>
        )}
      </div>
    </header>
  );
}
