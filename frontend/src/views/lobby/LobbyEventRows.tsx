import { useId, useState, type ReactNode } from "react";
import {
  Bot,
  Brain,
  ChevronDown,
  ChevronRight,
  CircleCheck,
  CircleStop,
  CircleX,
  FileText,
  Globe,
  LoaderCircle,
  Pin,
  Search,
  Terminal,
  Wrench,
  Zap,
} from "lucide-react";

import type { LobbyEvent } from "../../api";
import type { RoomTypingIndicator } from "../../lib/roomTypingIndicators";
import DiscordText, { type MentionLabels } from "../components/DiscordText";
import LobbyAttachments from "../components/LobbyAttachments";
import ProviderLogo from "../components/ProviderLogo";
import MessageMutationControls from "./MessageMutationControls";


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


function isReasoningEvent(event: LobbyEvent) {
  return (
    event.activity_kind === "reasoning" ||
    (!event.activity_kind && !event.activity_category)
  );
}


function ThinkingDetails({
  events,
  label,
  mentionLabels,
}: {
  events: LobbyEvent[];
  label: string;
  mentionLabels: MentionLabels;
}) {
  const [open, setOpen] = useState(false);
  const contentId = useId();
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
          {/* Kept in arrival order: an agent thinks, acts on that thought,
              then thinks again, and splitting the two apart hid which
              reasoning led to which tool call. */}
          {events.map((event) =>
            isReasoningEvent(event) ? (
              <div
                key={event.id}
                className="dc-thinking-step flex gap-2 py-1 text-[13px] leading-relaxed text-text-muted preserve-words"
                data-activity-kind="reasoning"
              >
                <Brain size={14} className="mt-0.5 shrink-0 opacity-70" aria-hidden="true" />
                <div className="min-w-0 italic">
                  <DiscordText
                    text={event.activity_detail || event.message || ""}
                    mentionLabels={mentionLabels}
                  />
                </div>
              </div>
            ) : (
              <ActivityRow key={event.id} event={event} mentionLabels={mentionLabels} />
            )
          )}
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


function ActivityRow({
  event,
  mentionLabels,
}: {
  event: LobbyEvent;
  mentionLabels: MentionLabels;
}) {
  const status = event.activity_status || "running";
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
        {status === "completed" ? (
          <CircleCheck size={14} className="text-emerald-400" aria-label="완료" />
        ) : status === "failed" ? (
          <CircleX size={14} className="text-red-400" aria-label="실패" />
        ) : status === "cancelled" ? (
          <CircleStop size={14} className="text-amber-400" aria-label="중단됨" />
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
            <DiscordText text={detail} mentionLabels={mentionLabels} />
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
  mentionLabels,
}: {
  events: LobbyEvent[];
  showHeader: boolean;
  providerKind?: string;
  mentionLabels: MentionLabels;
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
        <ThinkingDetails
          events={events}
          label={`💭 ${name}의 생각과 작업`}
          mentionLabels={mentionLabels}
        />
      </div>
    </div>
  );
}


export function LobbyTypingRow({
  indicator,
  thinkingEvents,
  mentionLabels,
}: {
  indicator: RoomTypingIndicator;
  thinkingEvents: LobbyEvent[];
  mentionLabels: MentionLabels;
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
              mentionLabels={mentionLabels}
            />
          </div>
        )}
      </div>
    </div>
  );
}

export function LobbySystemRow({
  event,
  mentionLabels,
}: {
  event: LobbyEvent;
  mentionLabels: MentionLabels;
}) {
  return (
    <div
      className="dc-system-divider px-4"
      data-room-event-id={event.id}
      role="status"
    >
      <span>
        <DiscordText text={event.message || ""} mentionLabels={mentionLabels} />
      </span>
    </div>
  );
}


export function LobbyMessageRow({
  event,
  providerKind,
  voteCard,
  showHeader = true,
  mentionLabels,
  roomSessionToken = "",
  pinned = false,
  canPin = false,
  onTogglePin,
  canEdit = false,
  canDelete = false,
  onEdit,
  onDelete,
}: {
  event: LobbyEvent;
  providerKind?: string;
  voteCard?: ReactNode;
  showHeader?: boolean;
  mentionLabels: MentionLabels;
  roomSessionToken?: string;
  pinned?: boolean;
  canPin?: boolean;
  onTogglePin?: () => void;
  canEdit?: boolean;
  canDelete?: boolean;
  onEdit?: (content: string) => Promise<void>;
  onDelete?: () => Promise<void>;
}) {
  const systemLike =
    event.kind === "system" ||
    event.kind === "flow_event" ||
    event.kind === "vote_cast" ||
    event.kind === "vote_withdraw" ||
    event.kind === "vote_close";
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
        {canPin && onTogglePin && (
          <button
            type="button"
            className="dc-message-action-button"
            aria-label={pinned ? "메시지 고정 해제" : "메시지 고정"}
            title={pinned ? "고정 해제" : "메시지 고정"}
            aria-pressed={pinned}
            onClick={onTogglePin}
          >
            <Pin size={14} fill={pinned ? "currentColor" : "none"} />
          </button>
        )}
        {onEdit && onDelete && (
          <MessageMutationControls
            event={event}
            canEdit={canEdit}
            canDelete={canDelete}
            onEdit={onEdit}
            onDelete={onDelete}
          />
        )}
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
        ) : event.message_deleted ? (
          <div className="text-[14px] italic leading-relaxed text-text-muted">
            삭제된 메시지입니다
          </div>
        ) : (
          <div className="text-[14px] leading-relaxed text-text-secondary preserve-words">
            <DiscordText text={event.message || ""} mentionLabels={mentionLabels} />
            {event.edited_at && <span className="ml-1 text-[10px] text-text-muted">(수정됨)</span>}
          </div>
        )}
        {!event.message_deleted && (
          <LobbyAttachments attachments={event.attachments} sessionToken={roomSessionToken} />
        )}
      </div>
    </div>
  );
}
