import { useEffect, useMemo, useRef, useState } from "react";
import { AtSign, MessageSquare, Send, Smile } from "lucide-react";
import { postSideChatMessage, type SideChatEvent } from "../../api";
import type { Mentionable } from "../../lib/mentionComposerModel";
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

function draftKeyForContext(meetingId: string): string {
  return `${meetingId}:side-chat`;
}

function SideChatMessage({
  event,
  mentionLabels,
}: {
  event: SideChatEvent;
  mentionLabels: Readonly<Record<string, string>>;
}) {
  return (
    <article className="dc-side-message">
      <p className="flex items-baseline gap-2">
        <span className="min-w-0 truncate text-[12px] font-bold text-text-secondary preserve-words">
          {event.name || "사이드"}
        </span>
        <span className="shrink-0 text-[10px] text-text-muted">{formatTime(event.created_at)}</span>
      </p>
      <div className="text-[12px] leading-relaxed text-text-secondary preserve-words">
        <DiscordText text={event.message || ""} mentionLabels={mentionLabels} />
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
  canPostMessages = true,
  authorName = "SeiNel",
  draftsByContext,
  onDraftChange,
}: {
  meetingId: string;
  events: SideChatEvent[];
  error: Error | null;
  onPosted: (events: SideChatEvent[]) => void;
  mentionables?: Mentionable[];
  canPostMessages?: boolean;
  authorName?: string;
  draftsByContext: Record<string, string>;
  onDraftChange: (key: string, value: string) => void;
}) {
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const restoreFocusAfterSendRef = useRef(false);
  const [busy, setBusy] = useState(false);
  const [sendError, setSendError] = useState("");
  const draftKey = draftKeyForContext(meetingId);
  const message = draftsByContext[draftKey] || "";
  const readOnlyReason = canPostMessages
    ? ""
    : "읽기 전용 초대입니다. 사이드챗도 보기만 가능합니다.";
  const composerDisabled = Boolean(readOnlyReason);
  const mentionLabels = useMemo(
    () => Object.fromEntries(mentionables.map(({ token, label }) => [token, label])),
    [mentionables]
  );

  useEffect(() => {
    if (busy || !restoreFocusAfterSendRef.current) return;
    restoreFocusAfterSendRef.current = false;
    inputRef.current?.focus();
  }, [busy]);

  function setMessage(nextMessage: string) {
    if (composerDisabled) return;
    onDraftChange(draftKey, nextMessage);
  }

  useEffect(() => {
    setSendError("");
  }, [draftKey]);

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
    restoreFocusAfterSendRef.current = true;
    setBusy(true);
    setSendError("");
    try {
      const payload = await postSideChatMessage({
        name: authorName,
        side: "mine",
        message: trimmed,
        meetingId,
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
      data-readonly={composerDisabled ? "true" : "false"}
    >
      <header className="dc-side-chat-head">
        <span className="flex min-w-0 items-center gap-2">
          <MessageSquare size={15} />
          <span className="truncate preserve-words">사이드챗</span>
        </span>
        <span className="text-[10px] font-bold text-text-muted">사람 전용 · 기록 안 함</span>
      </header>
      <p className="dc-side-chat-notice preserve-words">
        에이전트에게 보이지 않으며 서버를 다시 켜면 사라집니다. 최근 24시간, 최대 200개만 유지합니다.
      </p>
      <div className="dc-side-chat-feed chat-scroll">
        {events.length === 0 ? (
          <p className="dc-side-empty preserve-words">
            {composerDisabled ? readOnlyReason : "사이드챗 메시지가 아직 없습니다."}
          </p>
        ) : (
          events.map((event) => (
            <SideChatMessage key={event.id} event={event} mentionLabels={mentionLabels} />
          ))
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
            if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault();
              void handleSend();
            }
          }}
          placeholder={
            readOnlyReason ||
            "사이드챗 메시지"
          }
          disabled={busy || composerDisabled}
          ariaLabel="비공식 사이드챗 입력"
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
