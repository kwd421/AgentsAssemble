import { useId, useState, type ReactNode } from "react";
import {
  Bot,
  Brain,
  ChevronDown,
  ChevronRight,
  CircleCheck,
  FileText,
  Globe,
  LoaderCircle,
  MessageCircle,
  MoreHorizontal,
  Search,
  Terminal,
  Wrench,
  Zap,
} from "lucide-react";

import type { LobbyEvent } from "../../api";
import type { LobbyThreadSummary } from "../../lib/sideChatThreadModel";
import type { RoomTypingIndicator } from "../../lib/roomTypingIndicators";
import DiscordText from "../components/DiscordText";
import LobbyAttachments from "../components/LobbyAttachments";
import ProviderLogo from "../components/ProviderLogo";


function timeLabel(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString("ko-KR", {
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return "--:--";
  }
}


function MessageAvatar({
  avatarImage,
  providerKind,
  show = true,
  system = false,
}: {
  avatarImage?: string;
  providerKind?: string;
  show?: boolean;
  system?: boolean;
}) {
  return (
    <span
      className={show ? `dc-message-avatar mt-0.5 ${system ? "system" : "agent"}` : ""}
      data-has-image={Boolean(show && avatarImage && !system)}
      aria-hidden="true"
    >
      {show ? (
        avatarImage && !system ? (
          <img className="dc-message-avatar-image" src={avatarImage} alt="" />
        ) : system ? (
          <Zap size={16} />
        ) : (
          <ProviderLogo
            providerKind={providerKind}
            size={40}
            fallback={<Bot size={16} />}
          />
        )
      ) : null}
    </span>
  );
}


function ThinkingDetails({
  events,
  label,
}: {
  events: LobbyEvent[];
  label: string;
}) {
  const [open, setOpen] = useState(false);
  const contentId = useId();
  const reasoningEvents = events.filter(
    (event) =>
      event.activity_kind === "reasoning" ||
      (!event.activity_kind && !event.activity_category)
  );
  const toolEvents = events.filter((event) => !reasoningEvents.includes(event));
  return (
    <>
      <button
        type="button"
        className="dc-thinking-toggle flex items-center gap-1 text-[12px] text-text-muted hover:text-text-secondary"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        aria-controls={contentId}
      >
        {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
        <span>{label}</span>
      </button>
      {open && (
        <div
          id={contentId}
          className="dc-thinking-steps mt-1 border-l border-white/10 pl-3"
          role="log"
          aria-live="polite"
          aria-relevant="additions text"
        >
          {reasoningEvents.map((event) => (
            <div
              key={event.id}
              className="dc-thinking-step flex gap-2 py-1 text-[13px] leading-relaxed text-text-muted preserve-words"
              data-activity-kind="reasoning"
            >
              <Brain size={14} className="mt-0.5 shrink-0 opacity-70" aria-hidden="true" />
              <div className="min-w-0 italic">
                <DiscordText text={event.activity_detail || event.message || ""} />
              </div>
            </div>
          ))}
          {toolEvents.map((event) => (
            <ActivityRow key={event.id} event={event} />
          ))}
        </div>
      )}
    </>
  );
}


const GENERIC_ACTIVITY_TEXT = new Set([
  "파일 읽는 중",
  "파일 확인 완료",
  "정보 검색 중",
  "정보 검색 완료",
  "명령 실행 중",
  "명령 실행 완료",
  "웹 확인 중",
  "웹 확인 완료",
  "도구 사용 중",
  "도구 사용 완료",
]);


function activityTitle(event: LobbyEvent): string {
  if (event.activity_title) return event.activity_title;
  return {
    file_read: "파일",
    search: "검색",
    command: "명령",
    web: "웹",
    tool: "도구",
  }[event.activity_category || ""] || "작업";
}


function ActivityIcon({ category }: { category?: string }) {
  const props = { size: 14, className: "shrink-0 text-text-muted", "aria-hidden": true } as const;
  if (category === "file_read") return <FileText {...props} />;
  if (category === "search") return <Search {...props} />;
  if (category === "command") return <Terminal {...props} />;
  if (category === "web") return <Globe {...props} />;
  return <Wrench {...props} />;
}


function ActivityRow({ event }: { event: LobbyEvent }) {
  const completed = event.activity_status === "completed";
  const detail =
    event.activity_detail ||
    (!GENERIC_ACTIVITY_TEXT.has(event.message || "") ? event.message : "");
  return (
    <div
      className="dc-thinking-step flex min-w-0 items-start gap-2 py-1 text-[13px] leading-relaxed"
      data-activity-kind="tool"
      data-activity-status={event.activity_status || "running"}
    >
      <span className="mt-0.5 flex shrink-0 items-center gap-1.5">
        {completed ? (
          <CircleCheck size={14} className="text-emerald-400" aria-label="완료" />
        ) : (
          <LoaderCircle
            size={14}
            className="animate-spin text-text-muted"
            aria-label="진행 중"
          />
        )}
        <ActivityIcon category={event.activity_category} />
      </span>
      <span className="min-w-0">
        <span className="font-medium text-text-secondary preserve-words">
          {activityTitle(event)}
        </span>
        {detail && (
          <span className="ml-2 text-text-muted preserve-words">
            <DiscordText text={detail} />
          </span>
        )}
      </span>
    </div>
  );
}


export function LobbyThinkingGroup({
  events,
  showHeader,
  providerKind,
}: {
  events: LobbyEvent[];
  showHeader: boolean;
  providerKind?: string;
}) {
  const header = events[0];
  const name = header?.name || "agent";
  return (
    <div
      className="dc-thinking-group grid grid-cols-[40px_minmax(0,1fr)] gap-3 px-4 py-0.5"
      data-room-event-id={header?.id}
      data-role={header?.role || undefined}
    >
      <MessageAvatar
        avatarImage={header?.avatar_image_url}
        providerKind={providerKind || header?.provider_kind}
        show={showHeader}
      />
      <div className="min-w-0">
        {showHeader && (
          <p className="flex items-baseline gap-2">
            <span className="dc-message-author truncate text-[15px] font-semibold text-text-primary preserve-words">
              {name}
            </span>
            <span className="shrink-0 text-[11px] text-text-muted">
              {timeLabel(header?.created_at || "")}
            </span>
          </p>
        )}
        <ThinkingDetails events={events} label={`💭 ${name}의 생각과 작업`} />
      </div>
    </div>
  );
}


export function LobbyTypingRow({
  indicator,
  thinkingEvents,
}: {
  indicator: RoomTypingIndicator;
  thinkingEvents: LobbyEvent[];
}) {
  return (
    <div
      className="dc-message grid grid-cols-[40px_minmax(0,1fr)] gap-3 px-4 py-1.5"
      data-role={indicator.role || undefined}
    >
      <span className="dc-message-avatar mt-0.5 agent">
        <ProviderLogo
          providerKind={indicator.providerKind}
          size={40}
          fallback={<Bot size={16} />}
        />
      </span>
      <div className="min-w-0">
        <p className="flex items-baseline gap-2">
          <span className="dc-message-author truncate text-[15px] font-semibold text-text-primary preserve-words">
            {indicator.displayName}
          </span>
        </p>
        <div
          className="flex items-center gap-2 text-[13px] text-text-muted"
          aria-live="polite"
        >
          <span className="dc-typing-dots" aria-hidden="true">
            <span></span>
            <span></span>
            <span></span>
          </span>
          <span>{indicator.activity === "compacting" ? "압축 중..." : "입력중..."}</span>
        </div>
        {thinkingEvents.length > 0 && (
          <div className="mt-1">
            <ThinkingDetails
              events={thinkingEvents}
              label={`💭 ${indicator.displayName}의 생각과 작업`}
            />
          </div>
        )}
      </div>
    </div>
  );
}

export function LobbySystemRow({ event }: { event: LobbyEvent }) {
  return (
    <div
      className="dc-system-divider px-4"
      data-room-event-id={event.id}
      role="status"
    >
      <span>
        <DiscordText text={event.message || ""} />
      </span>
    </div>
  );
}


export function LobbyMessageRow({
  event,
  providerKind,
  onOpenSideThread,
  threadSummary,
  voteCard,
  showHeader = true,
}: {
  event: LobbyEvent;
  providerKind?: string;
  onOpenSideThread?: (event: LobbyEvent) => void;
  threadSummary?: LobbyThreadSummary;
  voteCard?: ReactNode;
  showHeader?: boolean;
}) {
  const systemLike =
    event.kind === "system" || event.kind === "flow_event" || event.kind === "vote_cast";
  return (
    <div
      className={`dc-message grid grid-cols-[40px_minmax(0,1fr)] gap-3 px-4 ${
        showHeader ? "py-1.5" : "py-0.5"
      }`}
      data-room-event-id={event.id}
      data-role={event.role || undefined}
      tabIndex={0}
    >
      <MessageAvatar
        avatarImage={event.avatar_image_url}
        providerKind={providerKind || event.provider_kind}
        show={showHeader}
        system={systemLike}
      />
      <div className="dc-message-actions" aria-label="메시지 작업">
        {onOpenSideThread && (
          <button
            type="button"
            className="dc-message-action-button"
            onClick={() => onOpenSideThread(event)}
            aria-label="스레드로 열기"
            title="스레드"
          >
            <MessageCircle size={15} />
          </button>
        )}
        <button
          type="button"
          className="dc-message-action-button"
          aria-label="더 보기"
          title="더 보기"
        >
          <MoreHorizontal size={15} />
        </button>
      </div>
      <div className="min-w-0">
        {showHeader && (
          <p className="flex items-baseline gap-2">
            <span className="dc-message-author truncate text-[15px] font-semibold text-text-primary preserve-words">
              {event.name || "Room"}
            </span>
            <span className="shrink-0 text-[11px] text-text-muted">
              {timeLabel(event.created_at)}
            </span>
          </p>
        )}
        {voteCard ? (
          voteCard
        ) : (
          <div className="text-[14px] leading-relaxed text-text-secondary preserve-words">
            <DiscordText text={event.message || ""} />
          </div>
        )}
        <LobbyAttachments attachments={event.attachments} />
        {threadSummary && onOpenSideThread && (
          <button
            type="button"
            className="dc-message-thread-chip"
            onClick={() => onOpenSideThread(event)}
            aria-label={`스레드 보기, 답장 ${threadSummary.replyCount}개`}
          >
            <MessageCircle size={14} />
            <span>답장 {threadSummary.replyCount}개</span>
            <span className="dc-message-thread-last preserve-words">
              {threadSummary.lastReplyName || "사이드"} ·{" "}
              {timeLabel(threadSummary.lastReplyAt)}
            </span>
          </button>
        )}
      </div>
    </div>
  );
}
