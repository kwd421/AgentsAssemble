import { useMemo, useRef, useState, type ChangeEvent, type KeyboardEvent } from "react";
import { AtSign, Paperclip, Send, Smile, X } from "lucide-react";
import {
  postLobbyMessage,
  uploadLobbyAttachment,
  type LobbyAttachmentRef,
  type LobbyEvent,
} from "../../api";
import {
  MAX_ATTACHMENTS_MESSAGE,
  MAX_ATTACHMENTS_PER_EVENT,
  lobbySubmitFailureDraft,
  lobbySubmitSuccessDraft,
  selectLobbyAttachmentFiles,
} from "../../lib/lobbyComposerModel";

function currentLobbyName() {
  try {
    return window.localStorage.getItem("agentsassemble.name") || "나";
  } catch {
    return "나";
  }
}

export default function LobbyComposer({
  meetingId,
  onPosted,
  mentionables = [],
}: {
  meetingId: string;
  onPosted: (events: LobbyEvent[]) => void;
  mentionables?: string[];
}) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const [message, setMessage] = useState("");
  const [pendingAttachments, setPendingAttachments] = useState<LobbyAttachmentRef[]>([]);
  const [busy, setBusy] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const canSubmit = Boolean(message.trim() || pendingAttachments.length) && !busy && !uploading;
  const mentionMatch = useMemo(() => {
    const selectionStart = inputRef.current?.selectionStart ?? message.length;
    const beforeCursor = message.slice(0, selectionStart);
    const match = /(^|\s)@([^\s@#]{0,32})$/.exec(beforeCursor);
    if (!match) return null;
    return {
      start: beforeCursor.length - match[2].length - 1,
      query: match[2].toLowerCase(),
    };
  }, [message]);
  const mentionOptions = useMemo(() => {
    if (!mentionMatch) return [];
    const unique = Array.from(new Set(mentionables.filter(Boolean)));
    return unique
      .filter((name) => name.toLowerCase().includes(mentionMatch.query))
      .slice(0, 6);
  }, [mentionMatch, mentionables]);

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

  function chooseMention(name: string) {
    if (!mentionMatch) return;
    const input = inputRef.current;
    const end = input?.selectionStart ?? message.length;
    const replacement = `@${name} `;
    const next = `${message.slice(0, mentionMatch.start)}${replacement}${message.slice(end)}`;
    setMessage(next);
    window.setTimeout(() => {
      inputRef.current?.focus();
      inputRef.current?.setSelectionRange(
        mentionMatch.start + replacement.length,
        mentionMatch.start + replacement.length
      );
    }, 0);
  }

  async function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    const selected = Array.from(event.currentTarget.files || []);
    event.currentTarget.value = "";
    if (!selected.length) return;

    const { accepted: filesToUpload, error: selectionError } = selectLobbyAttachmentFiles(
      pendingAttachments.length,
      selected
    );
    if (filesToUpload.length === 0) {
      setError(selectionError || MAX_ATTACHMENTS_MESSAGE);
      return;
    }
    setError(selectionError);

    setUploading(true);
    try {
      const uploaded: LobbyAttachmentRef[] = [];
      for (const file of filesToUpload) {
        uploaded.push(await uploadLobbyAttachment(file));
      }
      setPendingAttachments((current) =>
        [...current, ...uploaded].slice(0, MAX_ATTACHMENTS_PER_EVENT)
      );
    } catch (errorValue) {
      setError(errorValue instanceof Error ? errorValue.message : "첨부 업로드 실패");
    } finally {
      setUploading(false);
    }
  }

  function removePendingAttachment(attachmentId: string) {
    if (busy || uploading) return;
    setPendingAttachments((current) =>
      current.filter((attachment) => attachment.id !== attachmentId)
    );
  }

  async function handleSubmit() {
    if (busy || uploading) return;
    const draftMessage = message;
    const draftAttachments = pendingAttachments;
    const trimmed = draftMessage.trim();
    if (!trimmed && draftAttachments.length === 0) return;

    setBusy(true);
    setError("");
    try {
      const payload = await postLobbyMessage({
        name: currentLobbyName(),
        side: "mine",
        kind: "message",
        message: trimmed,
        attachments: draftAttachments,
        meetingId,
      });
      const cleared = lobbySubmitSuccessDraft<LobbyAttachmentRef>();
      setMessage(cleared.message);
      setPendingAttachments(cleared.pendingAttachments);
      onPosted(payload.events || (payload.event ? [payload.event] : []));
    } catch (errorValue) {
      const restored = lobbySubmitFailureDraft(
        draftMessage,
        draftAttachments,
        errorValue instanceof Error ? errorValue.message : "로비 메시지 전송 실패"
      );
      setMessage(restored.message);
      setPendingAttachments(restored.pendingAttachments);
      setError(restored.error);
    } finally {
      setBusy(false);
    }
  }

  function handleKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key !== "Enter" || event.nativeEvent.isComposing) return;
    event.preventDefault();
    void handleSubmit();
  }

  return (
    <section className="dc-composer-shell">
      {error && (
        <p className="mb-2 rounded border border-danger/30 bg-danger/10 px-3 py-2 text-[12px] font-semibold text-danger preserve-words">
          {error}
        </p>
      )}

      {pendingAttachments.length > 0 && (
        <div className="mb-2 flex flex-wrap gap-2">
          {pendingAttachments.map((attachment) => (
            <span
              key={attachment.id}
              className="dc-composer-attachment inline-flex max-w-full items-center gap-2 px-3 py-1.5 text-[12px] font-bold text-text-secondary"
            >
              <span className="min-w-0 truncate preserve-words">{attachment.filename}</span>
              <button
                type="button"
                onClick={() => removePendingAttachment(attachment.id)}
                disabled={busy || uploading}
                className="grid h-5 w-5 shrink-0 place-items-center rounded border border-line/70 text-text-muted hover:border-danger/45 hover:text-danger"
                aria-label={`${attachment.filename} 첨부 제거`}
              >
                <X size={12} />
              </button>
            </span>
          ))}
        </div>
      )}

      <div className="dc-composer-bar">
        {mentionOptions.length > 0 && (
          <div className="dc-mention-popover" role="listbox" aria-label="멘션 후보">
            {mentionOptions.map((name) => (
              <button key={name} type="button" onClick={() => chooseMention(name)} role="option">
                <span className="dc-mention-avatar">@</span>
                <span>{name}</span>
              </button>
            ))}
          </div>
        )}
        <input
          ref={inputRef}
          value={message}
          onChange={(event) => setMessage(event.target.value)}
          onKeyDown={handleKeyDown}
          className="dc-composer-input"
          placeholder={uploading ? "첨부 업로드 중..." : "이 방에 메시지 남기기..."}
          disabled={busy}
        />
        <input
          ref={fileInputRef}
          type="file"
          multiple
          className="hidden"
          onChange={handleFileChange}
          aria-label="로비 첨부 선택"
        />
        <button
          type="button"
          onClick={() => fileInputRef.current?.click()}
          disabled={busy || uploading || pendingAttachments.length >= MAX_ATTACHMENTS_PER_EVENT}
          className="dc-composer-button"
          aria-label="첨부 추가"
          title={`첨부 ${pendingAttachments.length}/${MAX_ATTACHMENTS_PER_EVENT}`}
        >
          <Paperclip size={17} />
        </button>
        <button
          type="button"
          onClick={() => insertText("@")}
          disabled={busy}
          className="dc-composer-button"
          aria-label="멘션 삽입"
          title="@멘션"
        >
          <AtSign size={17} />
        </button>
        <button
          type="button"
          onClick={() => insertText("🙂")}
          disabled={busy}
          className="dc-composer-button"
          aria-label="이모지 삽입"
          title="이모지"
        >
          <Smile size={17} />
        </button>
        <button
          type="button"
          onClick={handleSubmit}
          disabled={!canSubmit}
          className="dc-composer-button send"
          aria-label="로비 메시지 보내기"
        >
          <Send size={17} />
        </button>
      </div>
    </section>
  );
}
