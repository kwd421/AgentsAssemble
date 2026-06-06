import { useEffect, useRef, useState } from "react";
import { AtSign, ChevronLeft, MessageSquare, Send, Smile, X } from "lucide-react";
import { postSideChatMessage, type SideChatEvent } from "../../api";
import type { SideChatThreadContext } from "../../lib/sideChatThreadModel";
import DiscordText from "./DiscordText";
import MentionInput from "./MentionInput";

function formatTime(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString("ko-KR", {
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return "";
  }
}

function draftKeyForThread(threadContext: SideChatThreadContext | null): string {
  return threadContext?.sourceEventId ? `thread:${threadContext.sourceEventId}` : "side-chat";
}

function SideChatMessage({ event }: { event: SideChatEvent }) {
  return (
    <article className="dc-side-message">
      <p className="flex items-baseline gap-2">
        <span className="min-w-0 truncate text-[12px] font-bold text-text-secondary preserve-words">
          {event.name || "사이드"}
        </span>
        <span className="shrink-0 text-[10px] text-text-muted">{formatTime(event.created_at)}</span>
      </p>
      <div className="text-[12px] leading-relaxed text-text-secondary preserve-words">
        <DiscordText text={event.message || ""} />
      </div>
    </article>
  );
}

export default function SideChatDock({
  meetingId,
  events,
  error,
  onPosted,
  mentionables = [],
  threadContext = null,
  onCloseThread,
  canPostMessages = true,
}: {
  meetingId: string;
  events: SideChatEvent[];
  error: Error | null;
  onPosted: (events: SideChatEvent[]) => void;
  mentionables?: string[];
  threadContext?: SideChatThreadContext | null;
  onCloseThread?: () => void;
  canPostMessages?: boolean;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [draftsByContext, setDraftsByContext] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [sendError, setSendError] = useState("");
  const draftKey = draftKeyForThread(threadContext);
  const threadSourceEventId = threadContext?.sourceEventId || "";
  const message = draftsByContext[draftKey] || "";
  const readOnlyReason = canPostMessages
    ? ""
    : "읽기 전용 초대입니다. 사이드챗도 보기만 가능합니다.";
  const composerDisabled = Boolean(readOnlyReason);
  const composerAriaLabel = threadContext ? "비공식 스레드 입력" : "비공식 사이드챗 입력";

  function setMessage(nextMessage: string) {
    if (composerDisabled) return;
    setDraftsByContext((previous) => {
      if ((previous[draftKey] || "") === nextMessage) return previous;
      return { ...previous, [draftKey]: nextMessage };
    });
  }

  useEffect(() => {
    setSendError("");
  }, [draftKey]);

  useEffect(() => {
    if (!threadSourceEventId) return undefined;
    const focusTimer = window.setTimeout(() => inputRef.current?.focus(), 0);
    return () => window.clearTimeout(focusTimer);
  }, [threadSourceEventId]);

  function insertText(text: string) {
    if (composerDisabled || busy) return;
    const input = inputRef.current;
    const start = input?.selectionStart ?? message.length;
    const end = input?.selectionEnd ?? message.length;
    const next = `${message.slice(0, start)}${text}${message.slice(end)}`;
    setMessage(next);
    window.setTimeout(() => {
      inputRef.current?.focus();
      inputRef.current?.setSelectionRange(start + text.length, start + text.length);
    }, 0);
  }

  async function handleSend() {
    const trimmed = message.trim();
    if (!trimmed || busy || composerDisabled) return;
    const previousMessage = message;
    setMessage("");
    setBusy(true);
    setSendError("");
    try {
      const payload = await postSideChatMessage({
        name: "나",
        side: "mine",
        message: trimmed,
        meetingId,
        threadSourceEventId: threadContext?.sourceEventId || "",
      });
      onPosted(payload.events?.length ? payload.events : payload.event ? [payload.event] : []);
    } catch (errorValue) {
      setMessage(previousMessage);
      setSendError(errorValue instanceof Error ? errorValue.message : "사이드챗 전송 실패");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section
      className="dc-side-chat-dock"
      aria-label="비공식 사이드챗"
      data-thread-active={threadContext ? "true" : "false"}
      data-readonly={composerDisabled ? "true" : "false"}
    >
      <header className="dc-side-chat-head">
        <span className="flex min-w-0 items-center gap-2">
          {threadContext && onCloseThread ? (
            <button
              type="button"
              className="dc-side-thread-back"
              onClick={onCloseThread}
              aria-label="사이드챗으로 돌아가기"
              title="사이드챗"
            >
              <ChevronLeft size={15} />
            </button>
          ) : (
            <MessageSquare size={15} />
          )}
          <span className="truncate preserve-words">
            {threadContext ? "스레드" : "사이드챗"}
          </span>
        </span>
        {threadContext && onCloseThread ? (
          <button
            type="button"
            className="dc-side-thread-close"
            onClick={onCloseThread}
            aria-label="스레드 닫기"
            title="닫기"
          >
            <X size={14} />
          </button>
        ) : (
        <span className="text-[10px] font-bold text-text-muted">공식 기록 제외</span>
        )}
      </header>
      {threadContext && (
        <article
          className="dc-side-thread-source"
          aria-label={`${threadContext.sourceName} 메시지에서 열린 스레드`}
        >
          <p className="truncate text-[11px] font-black text-text-secondary preserve-words">
            #{threadContext.channelLabel} · {threadContext.sourceName}
          </p>
          <div className="mt-1 line-clamp-3 text-[12px] leading-relaxed text-text-muted preserve-words">
            <DiscordText text={threadContext.sourceMessage || "메시지 본문 없음"} />
          </div>
        </article>
      )}
      <div className="dc-side-chat-feed chat-scroll">
        {events.length === 0 ? (
          <p className="dc-side-empty preserve-words">
            {composerDisabled
              ? "읽기 전용 초대에서는 사이드챗과 스레드를 보기만 할 수 있습니다."
              : threadContext && events.length === 0
              ? "이 메시지에 대한 비공식 스레드를 시작하세요."
              : "메시지에서 스레드를 열면 여기에 표시됩니다. 필요하면 비공식 메모도 남길 수 있습니다."}
          </p>
        ) : (
          events.map((event) => <SideChatMessage key={event.id} event={event} />)
        )}
      </div>
      {(error || sendError) && (
        <p className="dc-side-error preserve-words">
          {sendError || "사이드챗 연결 대기 중"}
        </p>
      )}
      {readOnlyReason && (
        <p className="dc-side-readonly preserve-words">
          {readOnlyReason}
        </p>
      )}
      <div className="dc-side-composer">
        <MentionInput
          key={draftKey}
          inputRef={inputRef}
          value={message}
          onChange={setMessage}
          maxLength={2000}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.nativeEvent.isComposing) void handleSend();
          }}
          placeholder={readOnlyReason || (threadContext ? "스레드에 답장" : "스레드 메모")}
          disabled={busy || composerDisabled}
          ariaLabel={composerAriaLabel}
          mentionables={mentionables}
        />
        <button
          type="button"
          onClick={() => insertText("@")}
          disabled={busy || composerDisabled}
          aria-label="사이드챗 멘션 삽입"
        >
          <AtSign size={15} />
        </button>
        <button
          type="button"
          onClick={() => insertText("🙂")}
          disabled={busy || composerDisabled}
          aria-label="사이드챗 이모지 삽입"
        >
          <Smile size={15} />
        </button>
        <button
          type="button"
          onClick={handleSend}
          disabled={busy || composerDisabled || !message.trim()}
          aria-label="사이드챗 보내기"
        >
          <Send size={15} />
        </button>
      </div>
    </section>
  );
}
