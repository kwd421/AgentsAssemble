import { useRef, useState } from "react";
import { AtSign, MessageSquare, Send, Smile } from "lucide-react";
import { postSideChatMessage, type SideChatEvent } from "../../api";
import DiscordText from "./DiscordText";
import MentionInput from "./MentionInput";

export type SideChatThreadContext = {
  sourceEventId: string;
  sourceName: string;
  sourceMessage: string;
  channelLabel: string;
};

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

function SideChatMessage({ event }: { event: SideChatEvent }) {
  return (
    <article className="dc-side-message">
      <p className="flex items-baseline gap-2">
        <span className="min-w-0 truncate text-[12px] font-bold text-text-secondary preserve-words">
          {event.name || "사이드"}
        </span>
        <span className="shrink-0 text-[10px] text-text-muted">{formatTime(event.created_at)}</span>
      </p>
      <p className="text-[12px] leading-relaxed text-text-secondary preserve-words">
        <DiscordText text={event.message || ""} />
      </p>
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
}: {
  meetingId: string;
  events: SideChatEvent[];
  error: Error | null;
  onPosted: (events: SideChatEvent[]) => void;
  mentionables?: string[];
  threadContext?: SideChatThreadContext | null;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [sendError, setSendError] = useState("");

  function insertText(text: string) {
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
    if (!trimmed || busy) return;
    const previousMessage = message;
    setMessage("");
    setBusy(true);
    setSendError("");
    try {
      const payload = await postSideChatMessage({ name: "나", side: "mine", message: trimmed, meetingId });
      onPosted(payload.events?.length ? payload.events : payload.event ? [payload.event] : []);
    } catch (errorValue) {
      setMessage(previousMessage);
      setSendError(errorValue instanceof Error ? errorValue.message : "사이드챗 전송 실패");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="dc-side-chat-dock" aria-label="비공식 사이드챗">
      <header className="dc-side-chat-head">
        <span className="flex items-center gap-2">
          <MessageSquare size={15} />
          {threadContext ? "스레드" : "사이드챗"}
        </span>
        <span className="text-[10px] font-bold text-text-muted">공식 기록 제외</span>
      </header>
      {threadContext && (
        <article
          className="dc-side-thread-source"
          aria-label={`${threadContext.sourceName} 메시지에서 열린 스레드`}
        >
          <p className="truncate text-[11px] font-black text-text-secondary preserve-words">
            #{threadContext.channelLabel} · {threadContext.sourceName}
          </p>
          <p className="mt-1 line-clamp-3 text-[12px] leading-relaxed text-text-muted preserve-words">
            <DiscordText text={threadContext.sourceMessage || "메시지 본문 없음"} />
          </p>
        </article>
      )}
      <div className="dc-side-chat-feed chat-scroll">
        {events.length === 0 ? (
          <p className="dc-side-empty preserve-words">
            {threadContext
              ? "이 메시지에 대한 비공식 스레드를 시작하세요."
              : "오른쪽에 붙어 있는 비공식 대화입니다."}
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
      <div className="dc-side-composer">
        <MentionInput
          inputRef={inputRef}
          value={message}
          onChange={setMessage}
          maxLength={2000}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.nativeEvent.isComposing) void handleSend();
          }}
          placeholder={threadContext ? "스레드에 답장" : "사이드챗 메시지"}
          ariaLabel="비공식 사이드챗 입력"
          mentionables={mentionables}
        />
        <button
          type="button"
          onClick={() => insertText("@")}
          disabled={busy}
          aria-label="사이드챗 멘션 삽입"
        >
          <AtSign size={15} />
        </button>
        <button
          type="button"
          onClick={() => insertText("🙂")}
          disabled={busy}
          aria-label="사이드챗 이모지 삽입"
        >
          <Smile size={15} />
        </button>
        <button
          type="button"
          onClick={handleSend}
          disabled={busy || !message.trim()}
          aria-label="사이드챗 보내기"
        >
          <Send size={15} />
        </button>
      </div>
    </section>
  );
}
