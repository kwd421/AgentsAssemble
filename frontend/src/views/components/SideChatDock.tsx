import { useState } from "react";
import { MessageSquare, Send } from "lucide-react";
import { postSideChatMessage, type SideChatEvent } from "../../api";
import DiscordText from "./DiscordText";

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
}: {
  meetingId: string;
  events: SideChatEvent[];
  error: Error | null;
  onPosted: (events: SideChatEvent[]) => void;
}) {
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [sendError, setSendError] = useState("");

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
          사이드챗
        </span>
        <span className="text-[10px] font-bold text-text-muted">공식 기록 제외</span>
      </header>
      <div className="dc-side-chat-feed chat-scroll">
        {events.length === 0 ? (
          <p className="dc-side-empty preserve-words">오른쪽에 붙어 있는 비공식 대화입니다.</p>
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
        <input
          value={message}
          maxLength={2000}
          onChange={(event) => setMessage(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.nativeEvent.isComposing) void handleSend();
          }}
          placeholder="사이드챗 메시지"
          aria-label="비공식 사이드챗 입력"
        />
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
